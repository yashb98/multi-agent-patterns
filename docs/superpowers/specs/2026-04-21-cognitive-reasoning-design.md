# Cognitive Reasoning Engines — Design Spec

**Date:** 2026-04-21
**Pillar:** 2 of 6 (Autonomous Agent Infrastructure)
**Status:** Design approved, pending implementation plan
**Depends on:** Pillar 1 (Memory System Upgrade) — strategy templates stored in PROCEDURAL tier

---

## Problem Statement

The current agent system has two reasoning modes: "single LLM call" and "iterative convergence loop" (the 4 LangGraph patterns). Neither adapts to task complexity:

1. **No self-correction** — when an agent produces a bad output, it doesn't critique itself or retry with failure context. GRPO generates independent candidates but never asks "what went wrong?"
2. **No structured exploration** — agents commit to one reasoning path. For novel high-stakes tasks (new ATS platform, ambiguous email), there's no mechanism to explore multiple approaches and pick the best.
3. **No complexity-aware routing** — trivial tasks (classifying an obvious rejection email) go through the same expensive pipeline as novel tasks (first encounter with SmartRecruiters shadow DOM). Every call costs the same.
4. **No prompt self-improvement** — Persona Evolution improves one monolithic prompt per agent. Agents can't compose task-specific prompts from learned fragments or learn anti-patterns from failures.

## Solution: CognitiveEngine with 4-Level Escalation

A `CognitiveEngine` in `shared/cognitive/` that any agent calls via `engine.think(task, domain)`. It handles: classify complexity, retrieve strategy templates from Pillar 1 memory, compose prompt from learned fragments, execute at the right cognitive level, score output, store successes AND failures, update strategy templates.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Integration style | Opt-in toolkit (Approach B) | Matches existing pattern (smart_llm_call, get_evolved_prompt). Agents opt in, no big-bang migration |
| Classifier approach | Heuristic-first | Zero LLM cost for 80%+ of decisions. Matches 3-tier NLP classifier pattern |
| GRPO integration | Separate module, calls GRPO for group sampling | GRPO stays focused on group sampling. CognitiveEngine is a higher-level orchestrator |
| Strategy template storage | Pillar 1 PROCEDURAL tier | No separate DB. Full benefit of decay, promotion, graph linking, revival |
| Template scoping | Hybrid agent+domain (Option C) | Prefer own templates, fallback to cross-agent. Enables transfer learning |
| Failure storage | EPISODIC tier with failure_type payload | Reflexion stores anti-patterns. GRPO only stores successes — this fills the gap |

---

## 4-Level Graduated Escalation

System 1 is the default. System 2 requires evidence.

| Level | Name | When | Cost | Latency | LLM Calls |
|-------|------|------|------|---------|-----------|
| **L0** | Memory Recall | Strategy templates found, avg_score >= 8.0, times_used >= 3 | ~$0 | 5ms | 0 |
| **L1** | Single Shot | Templates exist but unproven, or partial memory match | ~$0.001 | 200ms | 1 |
| **L2** | Reflexion | L1 scored poorly, or novel + medium stakes | ~$0.005 | 1-2s | 2-3 + nano critiques |
| **L3** | Tree of Thought | Novel + high stakes, no memory, or L2 failed | ~$0.02-0.05 | 3-8s | 6-12 |

### Escalation Classifier Logic

```
Step 1: MEMORY CHECK (free, 2ms)
  Query Pillar 1 PROCEDURAL tier: domain + agent_name
  
  Templates found, avg_score >= 8.0, times_used >= 3?  → L0
  Templates found, lower confidence?                     → L1
  No templates?                                          → Step 2

Step 2: NOVELTY CHECK (free, 1ms)
  Query Pillar 1: ANY memory about this domain?
  
  Has episodic/semantic but no procedural?  → L1
  Zero memories?                            → Step 3

Step 3: STAKES CHECK (free, instant)
  HIGH stakes (job_application, financial, cv_generation):
    Novel + HIGH   → L3 (Tree of Thought)
  MEDIUM stakes (email_classification, calendar):
    Novel + MEDIUM → L2 (Reflexion)
  LOW stakes (briefing, trending, ranking):
    Novel + LOW    → L1 (single shot, learn from result)
```

### Post-Execution Auto-Escalation

```
L0 result scored < 6.0?       → re-run at L1
L1 result confidence < 0.7?   → escalate to L2
L2 failed after 3 retries?    → escalate to L3
L3 best branch < 5.0?         → return best + flag for human review
```

### Budget Guardrails

```python
@dataclass
class CognitiveBudget:
    max_l2_per_hour: int = 20
    max_l3_per_hour: int = 5
    max_cost_per_hour: float = 0.50
    cooldown_minutes: int = 5
```

When budget is exhausted, all tasks get L0/L1. The agent degrades gracefully — never stops working.

### Self-Improving Classifier

The classifier tracks its own accuracy per domain as SEMANTIC tier memories:

```python
MemoryEntry(
    tier=SEMANTIC,
    domain="cognitive_classifier",
    content="email_classification: L0 success rate 97%",
    payload={
        "target_domain": "email_classification",
        "l0_success_rate": 0.97,
        "l0_escalation_rate": 0.03,
        "sample_size": 200,
    },
)
```

Adjustments:
- L0 success rate >95% for a domain → skip Steps 2-3 (always L0)
- L1 auto-escalation rate >50% for a domain → start at L2 directly
- Domain consistently needs L3 → cache as "hard domain"

---

## Reflexion Loop (Level 2)

Sequential self-correction: try, critique what went wrong, retry with failure context.

### Interface

```python
@dataclass
class ReflexionResult:
    answer: str
    score: float
    attempts: int
    critiques: list[str]
    strategy_template: str
    cost: float

class ReflexionLoop:
    def __init__(self, memory_manager: MemoryManager, agent_name: str): ...

    async def run(
        self,
        task: str,
        domain: str,
        initial_prompt: str,
        max_attempts: int = 3,
        score_threshold: float = 7.0,
        scorer: Callable | None = None,
    ) -> ReflexionResult:
```

### The Loop

```
Attempt 1: Execute with composed prompt (from StrategyComposer)
     ↓
Score output (custom scorer or LLM self-score)
  Score >= threshold? → DONE, extract strategy template, return
     ↓
Critique (gpt-4.1-nano, ~$0.0002):
  "What specifically went wrong? Be concrete. One sentence."
     ↓
Retrieve failure memories: query Pillar 1 EPISODIC tier
  Filter: domain match + payload.failure_type exists
     ↓
Attempt 2: Execute with: original prompt + critique + failure patterns
     ↓
Score. Improved?
  Yes + >= threshold → DONE
  Yes but < threshold → one more attempt
  No improvement → DONE (return best attempt)
     ↓
Max 3 attempts. Always returns best-scoring attempt.
```

### What Gets Stored

**On success** (score >= threshold): PROCEDURAL template with the winning strategy.
**On failure** (all attempts < threshold): EPISODIC entry with failure pattern + critique.
**Always**: both success AND failure get stored. This is the key difference from GRPO.

Success template (PROCEDURAL):
```python
MemoryEntry(
    tier=PROCEDURAL, domain=domain,
    content="[extracted strategy from winning attempt]",
    score=result_score, confidence=1.0,
    payload={
        "agent_name": agent_name,
        "trigger": "[task pattern]",
        "times_used": 1, "times_succeeded": 1,
        "source": "reflexion", "attempts_needed": attempt_count,
    },
)
```

Failure pattern (EPISODIC):
```python
MemoryEntry(
    tier=EPISODIC, domain=domain,
    content="FAILURE: [what happened]. Fix: [critique suggestion]",
    score=best_score,  # low score = failure
    payload={
        "agent_name": agent_name,
        "failure_type": "[misclassification|timeout|wrong_strategy|...]",
        "critique": "[LLM critique text]",
        "source": "reflexion",
    },
)
```

### Critique Prompt

```
You are reviewing an agent's output. The task was:
{task}

The agent produced:
{output}

The score was {score}/10 (threshold: {threshold}).

What specifically went wrong? Identify the concrete mistake in one sentence.
Then suggest a specific fix in one sentence.

Format:
MISTAKE: [what went wrong]
FIX: [what to do differently]
```

Uses gpt-4.1-nano (~$0.0002/call). Critique is about identifying errors, not generating content.

---

## Tree of Thought (Level 3)

Parallel exploration of structurally different reasoning approaches. For genuinely novel, high-stakes tasks.

### When ToT Fires

All conditions must be true:
- No strategy templates in memory (complete novelty)
- Domain is HIGH stakes
- L2 Reflexion wasn't attempted or failed to reach threshold

Expected frequency: ~2-5% of tasks in week 1, <0.5% by month 2.

### Interface

```python
@dataclass
class Branch:
    branch_id: str
    reasoning: str
    output: str
    score: float
    depth: int          # 0 = initial, 1 = extension

@dataclass
class ToTResult:
    winner: Branch
    all_branches: list[Branch]
    strategy_template: str
    pruned_count: int
    cost: float

class TreeOfThought:
    def __init__(self, memory_manager: MemoryManager, agent_name: str): ...

    async def explore(
        self,
        task: str,
        domain: str,
        context: str,
        num_branches: int = 4,
        prune_threshold: float = 5.0,
        extend_top_n: int = 2,
        scorer: Callable | None = None,
    ) -> ToTResult:
```

### The Tree

```
                      Task
           ┌──────────┼──────────┐──────────┐
        Branch A   Branch B   Branch C   Branch D
        (score 8)  (score 3)  (score 7)  (score 4)
           │                     │
        Prune B,D            Prune B,D
           │                     │
        Extend A             Extend C
        (score 8.5)          (score 9.0)
                                │
                             WINNER → extract strategy template
```

### Branch Generation

Each branch gets a structurally different reasoning instruction:

```python
BRANCH_STRATEGIES = [
    "Approach step by step from first principles.",
    "Think about what could go wrong first, then work backwards.",
    "Find the simplest possible solution. Minimum that works.",
    "What would a domain expert do? Think from their perspective.",
]
```

Branches are generated via `TrainingFreeGRPO.generate_and_rank()` — GRPO handles group sampling with temperature spread. ToT adds structured prompts and the pruning/extension layer on top.

### Pruning and Extension

1. Generate `num_branches` (default 4) initial branches
2. Score each with scorer
3. Prune branches below `prune_threshold` (default 5.0)
4. Extend top `extend_top_n` (default 2) branches one more level
   - Extension prompt: "Build on this approach: {branch.reasoning}. Refine and improve it."
5. Score extensions
6. Pick highest-scoring branch as winner

### GRPO Integration

```
ToT.explore()
  → GRPO.generate_and_rank(task, branch_prompts)    # 4 branches
  → Score each
  → Prune below threshold
  → GRPO.generate_and_rank(task, extension_prompts)  # extend top 2
  → Score extensions
  → Winner → extract strategy template → Pillar 1 memory
```

### Cost

- 4 initial branches + 2 extensions = 6 generation calls
- 6 scoring calls (gpt-4.1-nano, ~$0.0002 each)
- Total: ~$0.02-0.05 per ToT exploration
- Budget cap: max 5 per hour

---

## Strategy Templates & Prompt Composition

### Strategy Template Payload Schema

Strategy templates are `MemoryEntry` objects in Pillar 1's PROCEDURAL tier. The `payload` dict follows this contract:

```python
STRATEGY_PAYLOAD = {
    "agent_name": str,                # creator agent
    "trigger": str,                   # when to use this strategy
    "composable_fragments": list[str], # sub-strategies that can be mixed
    "times_used": int,
    "times_succeeded": int,
    "success_rate": float,            # times_succeeded / times_used
    "avg_score": float,
    "avg_latency_ms": float,
    "source": str,                    # "reflexion" | "tot" | "grpo" | "direct"
    "anti_patterns": list[str],       # what NOT to do (from linked failure memories)
}
```

### StrategyComposer

Assembles a prompt from retrieved templates + anti-patterns + task context.

```python
class StrategyComposer:
    def compose(
        self,
        task: str,
        domain: str,
        agent_name: str,
        memory_manager: MemoryManager,
        max_templates: int = 5,
        max_anti_patterns: int = 3,
    ) -> ComposedPrompt:
```

Pipeline:

```
Step 1: RETRIEVE STRATEGIES — Pillar 1 PROCEDURAL tier
  Primary query:  domain + agent_name match (own strategies)
  Fallback query: domain match only (cross-agent transfer)
  Ranked by: success_rate * 0.6 + score * 0.3 + recency * 0.1

Step 2: RETRIEVE ANTI-PATTERNS — Pillar 1 EPISODIC tier
  Filter: domain match + payload.failure_type exists
  Top 3 most recent failures

Step 3: COMPOSE — assemble sections
  [BASE PROMPT]           — agent's evolved prompt (Persona Evolution)
  [LEARNED STRATEGIES]    — top-K templates as bullet points
  [AVOID THESE MISTAKES]  — anti-patterns from failure memory
  [TASK CONTEXT]          — specific task + relevant memory context

Step 4: TOKEN BUDGET — trim to fit context window
  Strategies: max 500 tokens
  Anti-patterns: max 200 tokens
  Task context: max 1000 tokens
  Uses existing context_compression.py
```

### ComposedPrompt

```python
@dataclass
class ComposedPrompt:
    text: str                          # the assembled prompt
    templates_used: list[str]          # memory_ids of templates included
    anti_patterns_used: list[str]      # memory_ids of failure patterns included
    token_count: int
    source_breakdown: dict             # {"own": 3, "cross_agent": 1, "anti_patterns": 2}
```

### Template Retrieval: Hybrid Agent+Domain Scoping

```
Query: domain="greenhouse_form_fill", agent_name="job_autopilot"

  1. Own templates:   domain=greenhouse_form_fill AND agent_name=job_autopilot
     → Found 3 (preferred, highest priority)

  2. Cross-agent:     domain=greenhouse_form_fill AND agent_name != job_autopilot
     → Found 1 from native_form_filler (lower priority, but useful)

  3. Related domain:  Qdrant semantic search for similar domains
     → "lever_form_fill" template found (SIMILAR_TO edge in Neo4j)
     → Included with lowest priority

Result: 5 templates ranked by relevance, own-first
```

---

## CognitiveEngine — The Single Entry Point

### Public API

```python
class CognitiveEngine:
    def __init__(
        self,
        memory_manager: MemoryManager,
        agent_name: str,
        budget: CognitiveBudget | None = None,
    ): ...

    async def think(
        self,
        task: str,
        domain: str,
        stakes: str = "medium",
        scorer: Callable | None = None,
        force_level: ThinkLevel | None = None,
    ) -> ThinkResult:
        """The single entry point. Classify → retrieve → compose → execute → score → store."""

    def report(self) -> dict:
        """Usage stats: calls per level, cost, success rates, budget remaining."""

    async def flush(self):
        """Batch-write pending strategy templates to memory. Call at end of agent run."""
```

### ThinkResult

```python
@dataclass
class ThinkResult:
    answer: str
    score: float
    level: ThinkLevel
    cost: float
    latency_ms: float
    strategy_stored: bool
    escalated_from: ThinkLevel | None
    attempts: int
    composed_prompt: ComposedPrompt | None
```

### ThinkLevel

```python
class ThinkLevel(int, Enum):
    L0_MEMORY = 0
    L1_SINGLE = 1
    L2_REFLEXION = 2
    L3_TREE_OF_THOUGHT = 3
```

### Internal Flow

```python
async def think(self, task, domain, stakes, scorer, force_level):
    # 1. Classify
    level = force_level or self._classifier.classify(task, domain, stakes)
    
    # 2. Budget check — downgrade if over budget
    level = self._budget.clamp(level)
    
    # 3. Compose prompt from strategy templates
    composed = self._composer.compose(task, domain, self._agent_name, self._memory)
    
    # 4. Execute at classified level
    if level == L0:
        result = self._execute_l0(composed)
    elif level == L1:
        result = await self._execute_l1(task, composed, scorer)
    elif level == L2:
        result = await self._reflexion.run(task, domain, composed.text, scorer=scorer)
    elif level == L3:
        result = await self._tot.explore(task, domain, composed.text, scorer=scorer)
    
    # 5. Post-execution: auto-escalate if score too low
    if result.score < 6.0 and level < L3 and self._budget.allows(level + 1):
        return await self.think(task, domain, stakes, scorer, force_level=level + 1)
    
    # 6. Store learnings (batched, written on flush())
    self._pending_writes.append(self._extract_template(result, task, domain))
    
    return result
```

### L0 Execution (Memory Recall)

L0 has two sub-modes depending on task type:

**Deterministic tasks** (classification, routing, yes/no decisions): The template content IS the answer. Zero LLM calls.

**Generative tasks** (writing, analysis, form strategy): One LLM call with the composed prompt (templates + anti-patterns). Still cheaper than L1 because the prompt is pre-optimized — no exploration needed.

```python
def _execute_l0(self, task: str, composed: ComposedPrompt) -> ThinkResult:
    best_template = composed.templates_used[0]
    entry = self._memory.get_by_id(best_template)
    self._memory.touch(best_template)

    if entry.payload.get("deterministic"):
        # Classification/routing — template IS the answer
        return ThinkResult(answer=entry.content, score=entry.score,
                           level=ThinkLevel.L0_MEMORY, cost=0.0, ...)
    
    # Generative — one LLM call with pre-optimized prompt
    answer = await smart_llm_call(composed.text + f"\n\nTask: {task}")
    return ThinkResult(answer=answer, score=entry.score,
                       level=ThinkLevel.L0_MEMORY, cost=..., ...)
```

---

## Integration Points

### With Existing Agents (Opt-in)

```python
# Any agent — 3 lines to add cognitive abilities
from shared.cognitive import CognitiveEngine

engine = CognitiveEngine(memory_manager, agent_name="gmail_agent")
result = await engine.think(task=..., domain=..., stakes="medium")
```

### With LangGraph Patterns

Pattern nodes can use CognitiveEngine inside their execution:

```python
async def enhanced_researcher_node(state):
    engine = CognitiveEngine(memory_manager, agent_name="researcher")
    result = await engine.think(
        task=state["topic"],
        domain="research",
        stakes="medium",
    )
    return {"research_notes": result.answer}
```

### With Cron Jobs

```
Cron fires → Agent creates CognitiveEngine
  → For each sub-task: engine.think(...)
  → engine.flush()  # batch-write templates to Pillar 1
  → Cron ends, templates persisted
  → Next cron run: richer memory, faster execution
```

### With GRPO (Training-Free)

CognitiveEngine calls `TrainingFreeGRPO.generate_and_rank()` for:
- L3 branch generation (ToT uses GRPO for group sampling)
- L1 when multiple candidates are useful (optional)

GRPO stays unchanged. CognitiveEngine is a higher-level consumer.

### With Persona Evolution

The `StrategyComposer` retrieves the agent's evolved prompt via `get_evolved_prompt(agent_name)` as the base prompt. Strategy templates and anti-patterns are layered on top. Persona Evolution continues evolving the base prompt independently.

```
Final prompt = evolved_base_prompt + strategy_templates + anti_patterns + task_context
```

---

## File Structure

```
shared/cognitive/
  __init__.py              # Public: CognitiveEngine, ThinkResult, ThinkLevel, CognitiveBudget
  _classifier.py           # EscalationClassifier — 4-level, heuristic-first
  _reflexion.py            # ReflexionLoop — try/critique/retry with failure memory
  _tree_of_thought.py      # TreeOfThought — branch/score/prune/extend
  _strategy.py             # StrategyTemplate payload schema, StrategyComposer, ComposedPrompt
  _engine.py               # CognitiveEngine — orchestrates the full loop
  _prompts.py              # Prompt templates for critique, branching, scoring
  _budget.py               # CognitiveBudget, BudgetTracker
```

8 new files. All in `shared/` (correct dependency direction).

---

## Dependencies

```
shared/cognitive/
    ↓ imports from
shared/memory_layer/           # Pillar 1 — MemoryManager, MemoryQuery, MemoryEntry, MemoryTier
shared/experiential_learning/  # TrainingFreeGRPO for group sampling
shared/agents.py               # get_llm(), smart_llm_call()
shared/cost_tracker.py         # track_llm_usage()
shared/context_compression.py  # token counting, prompt trimming
shared/streaming.py            # smart_llm_call() for LLM execution
```

No new external packages required. Uses existing LLM infrastructure.

---

## Configuration

### Environment Variables

```bash
COGNITIVE_MAX_L2_PER_HOUR=20        # Reflexion budget
COGNITIVE_MAX_L3_PER_HOUR=5         # ToT budget
COGNITIVE_MAX_COST_PER_HOUR=0.50    # Hard dollar cap
COGNITIVE_COOLDOWN_MINUTES=5        # After hitting cap
COGNITIVE_SCORE_THRESHOLD=7.0       # Default success threshold
COGNITIVE_ENABLED=true              # Kill switch
```

### Stakes Registry

```python
STAKES_REGISTRY = {
    "high": [
        "job_application", "cv_generation", "cover_letter",
        "financial_transaction", "form_submission",
    ],
    "medium": [
        "email_classification", "calendar_scheduling",
        "screening_answers", "jd_analysis",
    ],
    "low": [
        "briefing_synthesis", "github_trending", "arxiv_ranking",
        "budget_categorization", "task_management",
    ],
}
```

Agents can override stakes per-call via `engine.think(stakes="high")`.

---

## Expected Performance Over Time

### Cost per task (average across all agents)

| Timeframe | L0% | L1% | L2% | L3% | Avg cost/task |
|-----------|-----|-----|-----|-----|--------------|
| Week 1 | 60% | 25% | 10% | 5% | ~$0.004 |
| Week 4 | 80% | 15% | 4% | 1% | ~$0.001 |
| Week 12 | 95% | 4% | <1% | <0.1% | ~$0.0002 |

### Why it improves

Every L2/L3 success creates a strategy template that handles the same task at L0 next time. The system has a natural ratchet: expensive reasoning → cached strategy → free recall. Over time, the agent builds a library of strategies covering its entire task space.

---

## Agent-Facing Documentation Updates

| File | Changes |
|------|---------|
| **shared/CLAUDE.md** | Add cognitive engine section: module list, public API, integration pattern |
| **shared/cognitive/CLAUDE.md** | New file — module docs: architecture, levels, how to opt in, strategy template schema |
| **AGENTS.md** | Add cognitive briefing: "Use CognitiveEngine.think() for tasks that benefit from self-improvement. Call flush() at end of run." |
| **CLAUDE.md** (root) | Add to Module Context: `shared/cognitive/CLAUDE.md — 4-level cognitive engine: memory recall, single shot, reflexion, tree of thought` |
| **patterns/CLAUDE.md** | Add note: pattern nodes can use CognitiveEngine for self-improving reasoning |
| **jobpulse/CLAUDE.md** | Add cognitive integration: which agents use it, how cron runs trigger flush() |

---

## Testing Strategy

### Test Infrastructure

- All tests use mocked MemoryManager (no real Pillar 1 engines needed)
- LLM calls mocked via `unittest.mock.patch` on `smart_llm_call`
- Deterministic embeddings from Pillar 1 conftest (`_deterministic_embedding`)
- GRPO mocked for ToT tests (isolated from group sampling internals)

### Test Files

```
tests/shared/cognitive/
  conftest.py                  # Shared fixtures: mock memory, mock LLM, mock scorer
  test_classifier.py           # Escalation level selection
  test_reflexion.py            # Reflexion loop mechanics
  test_tree_of_thought.py      # ToT branching, pruning, extension
  test_strategy.py             # StrategyComposer, template retrieval, prompt assembly
  test_engine.py               # CognitiveEngine end-to-end
  test_budget.py               # Budget guardrails, degradation
  test_integration.py          # Full pipeline with mocked memory
  test_self_improvement.py     # Strategy templates improve over simulated runs
```

### Test Specifications

#### 1. Classifier Tests (test_classifier.py) — 10 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_l0_when_strong_templates_exist` | Memory hit → L0 | Insert 3 templates (score>8, used>3), classify → L0 |
| `test_l1_when_weak_templates_exist` | Low-confidence templates → L1 | Insert templates with score<8, classify → L1 |
| `test_l1_when_episodic_but_no_procedural` | Partial memory → L1 | Insert episodic memories only, classify → L1 |
| `test_l2_novel_medium_stakes` | Novel + medium → L2 | Empty memory + stakes="medium", classify → L2 |
| `test_l3_novel_high_stakes` | Novel + high → L3 | Empty memory + stakes="high", classify → L3 |
| `test_l1_novel_low_stakes` | Novel + low → L1 | Empty memory + stakes="low", classify → L1 |
| `test_auto_escalation_on_low_score` | Post-execution escalation | L0 scores 4.0 → auto-escalates to L1 |
| `test_budget_clamps_level` | Budget exhausted → downgrade | Set max_l3=0, classify novel+high → clamped to L2 |
| `test_self_improving_skips_check` | Learned L0 always works | Insert classifier memory with l0_success_rate=0.98, verify Steps 2-3 skipped |
| `test_self_improving_starts_higher` | Learned domain is hard | Insert classifier memory with l1_escalation_rate=0.6, verify starts at L2 |

#### 2. Reflexion Tests (test_reflexion.py) — 12 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_passes_first_attempt` | No retry when score high | Mock LLM scores 8.0, verify 1 attempt, no critique |
| `test_retries_on_low_score` | Critique + retry | Mock scores [4.0, 8.0], verify 2 attempts, critique generated |
| `test_max_3_attempts` | Hard cap | Mock scores [3.0, 3.0, 3.0], verify exactly 3 attempts |
| `test_returns_best_attempt` | Best not always last | Mock scores [3.0, 7.5, 6.0], verify returns attempt 2 (score 7.5) |
| `test_critique_prompt_includes_output` | Critique sees prior output | Verify critique LLM call includes previous attempt's output |
| `test_failure_memory_retrieved` | Past failures injected | Insert EPISODIC failure entry, verify it appears in retry prompt |
| `test_stores_success_template` | PROCEDURAL entry created | Run to success, verify MemoryManager.store_memory called with tier=PROCEDURAL |
| `test_stores_failure_pattern` | EPISODIC failure entry created | Run 3 attempts all < threshold, verify EPISODIC entry stored |
| `test_failure_has_critique` | Failure entry has critique payload | Verify stored EPISODIC entry.payload has failure_type and critique |
| `test_custom_scorer` | Custom scoring function | Pass custom scorer, verify it's called instead of LLM scorer |
| `test_cost_tracking` | Cost reported accurately | Verify result.cost matches expected (N generation calls + N-1 critique calls) |
| `test_critique_uses_nano` | Critique is cheap | Verify critique calls use gpt-4.1-nano model |

#### 3. Tree of Thought Tests (test_tree_of_thought.py) — 10 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_generates_n_branches` | Correct branch count | num_branches=4, verify 4 initial branches created |
| `test_prunes_below_threshold` | Pruning works | 4 branches, scores [8, 3, 7, 4], threshold=5, verify 2 pruned |
| `test_extends_top_n` | Extension works | After pruning, top 2 extended, verify 2 extension branches |
| `test_winner_is_highest_score` | Correct selection | Verify result.winner has highest score across all branches |
| `test_strategy_extracted_from_winner` | Template created | Verify result.strategy_template is non-empty string |
| `test_grpo_called_for_generation` | GRPO integration | Mock GRPO, verify generate_and_rank called for initial + extension |
| `test_branch_prompts_structurally_different` | Not just temperature variation | Verify each branch gets a different reasoning instruction |
| `test_extension_builds_on_parent` | Extensions reference parent | Verify extension prompts include parent branch reasoning |
| `test_cost_tracking` | Cost accurate | Verify result.cost = generation_calls + scoring_calls |
| `test_single_branch_no_extension` | Edge case | Only 1 branch passes pruning, verify it's returned without extension |

#### 4. Strategy Tests (test_strategy.py) — 12 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_compose_with_own_templates` | Own agent templates preferred | Insert templates for agent_name, verify they appear in composed prompt |
| `test_compose_falls_back_to_cross_agent` | Cross-agent transfer | No own templates, insert other agent's templates, verify they appear |
| `test_compose_includes_anti_patterns` | Failure patterns included | Insert EPISODIC failures, verify "AVOID" section in composed prompt |
| `test_compose_respects_max_templates` | Limit honored | Insert 10 templates, max_templates=3, verify only 3 in prompt |
| `test_compose_ranking_by_success_rate` | Best templates first | Insert templates with varying success rates, verify highest-rate first |
| `test_compose_token_budget` | Token trimming | Insert huge templates, verify composed prompt respects token limit |
| `test_compose_includes_base_prompt` | Persona Evolution base | Mock get_evolved_prompt, verify it's the first section |
| `test_template_update_on_success` | times_used incremented | Use a template, report success, verify times_used and times_succeeded both incremented |
| `test_template_update_on_failure` | times_used incremented only | Use a template, report failure, verify times_used incremented but times_succeeded unchanged |
| `test_success_rate_computed` | Rate calculation | Template with 8 successes out of 10 uses → success_rate=0.8 |
| `test_cross_agent_lower_priority` | Own templates ranked higher | Insert own (score 7) + cross-agent (score 9), verify own appears first |
| `test_empty_memory_returns_base_only` | Graceful empty state | No templates, no failures, verify composed prompt is just the base prompt |

#### 5. Engine Tests (test_engine.py) — 10 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_think_l0_no_llm_call` | L0 is free | Strong templates in memory, verify zero LLM calls made |
| `test_think_l1_single_call` | L1 makes one call | Partial templates, verify exactly 1 LLM call |
| `test_think_l2_calls_reflexion` | L2 delegates to ReflexionLoop | Novel + medium stakes, verify ReflexionLoop.run called |
| `test_think_l3_calls_tot` | L3 delegates to TreeOfThought | Novel + high stakes, verify TreeOfThought.explore called |
| `test_auto_escalation_l0_to_l1` | Escalation chain | L0 scores 4.0, verify re-called at L1 |
| `test_auto_escalation_l1_to_l2` | Escalation chain | L1 confidence 0.5, verify re-called at L2 |
| `test_force_level_overrides_classifier` | Manual override | force_level=L3, verify classifier not called |
| `test_flush_writes_to_memory` | Batch write | Think 5 times, flush, verify 5 store_memory calls |
| `test_report_tracks_levels` | Usage reporting | Think at various levels, verify report() counts match |
| `test_think_returns_result` | ThinkResult structure | Verify all fields populated: answer, score, level, cost, latency_ms |

#### 6. Budget Tests (test_budget.py) — 8 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_budget_allows_within_limits` | Normal operation | 3 L2 calls with max=20, all allowed |
| `test_budget_blocks_over_limit` | L2 cap | 21st L2 call blocked, clamped to L1 |
| `test_budget_blocks_l3_over_limit` | L3 cap | 6th L3 call with max=5, clamped to L2 |
| `test_cost_cap_blocks_escalation` | Dollar limit | Accumulate $0.49, next L3 ($0.03) blocked because total would exceed $0.50 |
| `test_cooldown_after_cap` | Cooldown period | Hit cap, verify L2/L3 blocked for cooldown_minutes |
| `test_budget_resets_hourly` | Hourly window | Hit cap, advance clock 61 minutes, verify budget reset |
| `test_disabled_engine` | Kill switch | COGNITIVE_ENABLED=false, verify think() returns L1 always |
| `test_budget_report` | Stats accurate | Verify report() shows correct remaining budget |

#### 7. Integration Tests (test_integration.py) — 8 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_full_pipeline_novel_to_cached` | Learning loop | Think (novel, L2) → templates stored → think again (same domain) → L0 |
| `test_failure_prevents_repeat` | Anti-pattern learning | Think → fail → failure stored → think again → failure pattern in prompt |
| `test_cross_agent_transfer` | Knowledge sharing | Agent A creates template → Agent B queries same domain → finds it |
| `test_escalation_chain_l0_to_l3` | Full escalation | Mock L0,L1,L2 all fail → reaches L3 → succeeds |
| `test_cron_lifecycle` | Cron simulation | Create engine → think 5x → flush → new engine → verify templates loaded |
| `test_concurrent_agents` | Thread safety | 5 agents thinking simultaneously → no data corruption |
| `test_degraded_mode_no_memory` | Pillar 1 down | Memory manager raises, verify engine degrades to L1 (single LLM call) |
| `test_report_after_session` | End-to-end stats | Run varied session, verify report has accurate level distribution and costs |

#### 8. Self-Improvement Tests (test_self_improvement.py) — 6 tests

| Test | Verifies | Assert |
|------|----------|--------|
| `test_template_score_improves` | RL loop works | Simulate 10 runs on same domain, verify template avg_score increases |
| `test_bad_template_decays` | Bad strategies forgotten | Template with 30% success rate → verify Pillar 1 decay drops it |
| `test_classifier_learns_easy_domain` | Classifier self-improvement | 20 successful L0 calls → verify classifier memory stores high success rate |
| `test_classifier_learns_hard_domain` | Classifier detects difficulty | 10 L1 calls with 60% escalation → verify classifier starts at L2 |
| `test_templates_promote_stm_to_ltm` | Lifecycle promotion | Template used 15x with 90% success → verify lifecycle=LTM |
| `test_l0_percentage_increases_over_runs` | System gets faster | Simulate 50 diverse runs, verify L0 percentage increases across batches |

### Test Counts

| File | Tests | Type |
|------|-------|------|
| test_classifier.py | 10 | Unit |
| test_reflexion.py | 12 | Unit |
| test_tree_of_thought.py | 10 | Unit |
| test_strategy.py | 12 | Unit |
| test_engine.py | 10 | Unit + Integration |
| test_budget.py | 8 | Unit |
| test_integration.py | 8 | Integration |
| test_self_improvement.py | 6 | Integration |
| **Total** | **76** | |

---

## Success Criteria

1. `CognitiveEngine.think()` is the single entry point — agents don't need to know about levels, templates, or internals
2. L0 makes zero LLM calls — pure memory recall, <10ms latency
3. Strategy templates are stored in Pillar 1 PROCEDURAL tier with the defined payload schema
4. Failure patterns are stored in Pillar 1 EPISODIC tier — Reflexion learns from mistakes, not just successes
5. Budget guardrails prevent runaway costs — hard caps on L2/L3 per hour and total dollars
6. System self-improves: L0 percentage measurably increases over repeated runs on the same domains
7. Cross-agent template transfer works: Agent A's strategy helps Agent B on the same domain
8. Graceful degradation: if Pillar 1 memory is unavailable, engine falls back to L1 (single LLM call)
9. All existing tests pass without modification — zero regressions
10. Agent-facing documentation updated — a new subagent naturally uses CognitiveEngine after reading AGENTS.md
