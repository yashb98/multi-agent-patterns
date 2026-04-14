# Ultraplan: Fix, Unlock, Extend — Full System Design

**Date**: 2026-04-14
**Status**: Draft
**Author**: Yash + Claude

## Overview

Three-phase staged rollout to bring the JobPulse multi-agent system from ~60% operational to 100%, unlock 3 dormant LangGraph patterns, and add 2 new patterns (plan-and-execute, map-reduce) with an auto-routing layer.

**Non-goals**: DSPy revival (GRPO is working, revisit in 6 months), new Telegram bots, new external integrations, UI/frontend changes.

---

## Phase 1: Fix Broken Automation

**Goal**: Get every automation back to 100% operational. No new features.

### 1.1 Crontab Path Fix

**Problem**: All 12+ cron entries point to `/Users/yashbishnoi/Downloads/multi_agent_patterns/` which no longer exists. The project lives at `/Users/yashbishnoi/projects/multi_agent_patterns/`.

**Fix**: `sed -i '' 's|Downloads/multi_agent_patterns|projects/multi_agent_patterns|g'` on the crontab. Verify each entry after replacement.

**Affected crons**:
- arXiv digest (7:57am daily)
- Briefing (8:03am daily)
- Calendar reminders (9am/12pm/3pm daily)
- Gmail check (1pm/3pm/5pm daily)
- Budget archive (Sunday 7am)
- Weekly report (Sunday 8pm)
- Health watchdog (every 10 minutes)
- Daemon restart (every 3 hours)
- Notion papers (Monday 8:33am)

### 1.2 Gmail Agent UnboundLocalError

**File**: `jobpulse/gmail_agent.py:315`
**Problem**: `messages` variable referenced before assignment when the Gmail API call fails.
**Fix**: Initialize `messages = []` before the try block.

### 1.3 Job Autopilot AttributeError

**File**: `jobpulse/job_autopilot.py:370`
**Problem**: `listing.description` should be `listing.description_raw` (Pydantic model field name).
**Fix**: Single field rename.

### 1.4 Notion Papers ImportError

**File**: `jobpulse/notion_papers_agent.py`
**Problem**: Imports deleted `fast_score` function from `arxiv_agent`.
**Fix**: Find the current scoring function that replaced `fast_score` and update the import.

### 1.5 arXiv Rate Limiting

**Problem**: 429 errors since Apr 7. Current backoff: 3 attempts at 5s/10s/15s.
**Fix**:
- Add `User-Agent` header per project rules (arXiv requires it)
- Increase backoff to 30s/60s/120s (exponential)
- Add `requests` session reuse to avoid connection churn

### 1.6 Voice Handler Import

**File**: `jobpulse/voice_handler.py`
**Problem**: `get_openai_client` import fails in daemon context.
**Fix**: Trace the correct import path. May be circular import or renamed function.

### 1.7 Missing Profile Sync Cron

**Problem**: CLAUDE.md documents a 3am profile sync cron but no crontab entry exists.
**Fix**: Add `0 3 * * * cd /Users/yashbishnoi/projects/multi_agent_patterns && /opt/homebrew/anaconda3/bin/python -m jobpulse.runner profile-sync >> logs/profile-sync.log 2>&1`

### 1.8 Daemon Restart Script

**Problem**: macOS Gatekeeper blocks execution with `Operation not permitted`.
**Fix**: `xattr -d com.apple.quarantine` on the script, or rewrite as a Python-based restart using `launchctl kickstart`.

### 1.9 Hierarchical Pattern — Wire Experiential Learning

**File**: `patterns/hierarchical.py`
**Problem**: Only pattern without experiential learning.
**Fix**: Add `ExperienceMemory` import, inject into research/writing prompts (same pattern as peer_debate.py), extract learnings at finish node.

### Phase 1 Verification

After all fixes:
- `crontab -l` shows all entries with correct path
- `python -c "from jobpulse.gmail_agent import check_emails"` — no ImportError
- `python -c "from jobpulse.job_autopilot import run_scan_window"` — no AttributeError
- `python -c "from jobpulse.notion_papers_agent import sync_papers"` — no ImportError
- `python -c "from jobpulse.voice_handler import transcribe_voice"` — no ImportError
- arXiv test: `python -m jobpulse.runner ralph-test` with arXiv URL returns 200
- Health watchdog runs without error for 30 minutes
- All 4 Telegram bots respond to "help"

---

## Phase 2: Unlock Dormant Patterns + Auto-Router

**Goal**: Make all 4 existing patterns reachable from Telegram via an intelligent auto-router.

### 2.1 Pattern Router

**New file**: `jobpulse/pattern_router.py`

**Architecture**: 2-tier classifier (same architecture as NLP classifier — rule-based first, embeddings fallback).

#### Rule-Based Tier (instant, free)

| Signal | Pattern | Examples |
|--------|---------|----------|
| Comparative: "vs", "compare", "which is better", "pros and cons" | Peer Debate | "React vs Vue for dashboards" |
| Controversial/opinion: "should I", "is X worth", "debate", "argue" | Peer Debate | "Should I learn Rust or Go?" |
| Multi-entity: 3+ entities listed, "analyze X, Y, and Z" | Dynamic Swarm | "Analyze Postgres, MongoDB, and Redis for caching" |
| Structured/hierarchical: "outline", "report on", "break down", "explain in depth" | Hierarchical | "Break down transformer architecture" |
| Multi-step with dependencies: "first...then", "step by step", "compare then recommend" | Plan-and-Execute | "Research 5 VDBs, benchmark each, recommend one" |
| Batch/parallel: "all", "every", "each of", list of 4+ items | Map-Reduce | "Summarize all 50 papers from this week" |
| Default / single-topic | Enhanced Swarm | "Quantum ML advances" |

#### Embedding Tier (5ms, fallback)

When no rules match with confidence, use semantic similarity against 30-50 labeled examples per pattern. Same embedding infrastructure as `nlp_classifier.py`.

#### Override Syntax

Prefix the message with a pattern keyword:
- `debate: <query>` → Peer Debate
- `swarm: <query>` → Enhanced Swarm
- `deep: <query>` → Hierarchical
- `plan: <query>` → Plan-and-Execute
- `batch: <query>` → Map-Reduce
- `dynamic: <query>` → Dynamic Swarm

Override always wins. No classification needed.

#### Response Header

Every pattern response starts with:
```
[Peer Debate] 3 rounds, converged at quality=8.4
Override: debate | swarm | deep | plan | batch
```

One line, always present. Teaches the user the system's behavior.

#### Feedback Loop

On completion, log to experience_memory.db:
```python
{
    "query_features": extracted_signals,
    "chosen_pattern": "peer_debate",
    "override": False,
    "quality_score": 8.4,
    "timestamp": "2026-04-14T..."
}
```

When an override produces a higher score than the auto-selected pattern would have, the router learns from that. After 50+ data points, the rule weights can be tuned.

### 2.2 Dispatch Integration

**File**: `jobpulse/swarm_dispatcher.py`

Changes to the dispatch function:

```python
from jobpulse.pattern_router import select_pattern, is_research_query

def dispatch(cmd):
    if is_research_query(cmd):
        pattern, reason = select_pattern(cmd.raw)
        return run_with_pattern(pattern, cmd, reason)
    # ... existing dispatch logic
```

**`is_research_query()`** returns True when:
- Intent is `arxiv` (always)
- Intent is `research` (new intent, always)
- Intent is `conversation` AND query contains research signals (compare, analyze, explain, "what is", "how does", 3+ technical terms)

Returns False for all other intents (budget, tasks, calendar, gmail, jobs, etc.). These are never routed to patterns.

**`run_with_pattern()`** calls the appropriate `run_*()` function:
- `patterns.enhanced_swarm.run_swarm()`
- `patterns.peer_debate.run_debate()`
- `patterns.dynamic_swarm.run_dynamic()`
- `patterns.hierarchical.run_hierarchical()`
- `patterns.plan_and_execute.run_plan()` (Phase 3)
- `patterns.map_reduce.run_map_reduce()` (Phase 3)

### 2.3 New Telegram Intent: `research`

Add a dedicated `research` intent to NLP classifier for queries that are clearly research tasks but don't match existing intents (arxiv, conversation).

**Regex tier**: `^research\s+`, `^investigate\s+`, `^analyze\s+`, `^compare\s+`
**Embedding tier**: 20 examples of research-style queries.

Add to BOTH `dispatcher.py` and `swarm_dispatcher.py` per dual dispatcher invariant.

### Phase 2 Verification

- Send "compare React vs Vue" → receives `[Peer Debate]` header
- Send "break down microservices" → receives `[Hierarchical]` header
- Send "swarm: compare React vs Vue" → override works, receives `[Enhanced Swarm]` header
- Send "papers" → still routes to arXiv agent (not pattern router)
- Send "budget" → still routes to budget agent (not pattern router)
- experience_memory.db shows new pattern selection entries

---

## Phase 3: Plan-and-Execute + Map-Reduce

**Goal**: Add 2 new LangGraph patterns that handle multi-step and batch workloads.

### 3.1 Plan-and-Execute Pattern

**New file**: `patterns/plan_and_execute.py`

#### Topology

```
planner → [step_executor → evaluator → replanner?]* → synthesizer
```

5 nodes in a LangGraph StateGraph with conditional edges.

#### State

Extends the existing `AgentState` pattern:

```python
class PlanExecuteState(TypedDict):
    topic: str                          # immutable input
    plan: list[Step]                    # current plan (mutable)
    completed_steps: list[StepResult]   # executed steps with outputs
    current_step_index: int
    replan_count: int                   # max 2 replans
    research_notes: list[str]           # accumulated research
    final_output: str
    quality_score: float
    accuracy_score: float
    token_usage: list[dict]
```

```python
class Step(TypedDict):
    goal: str                  # what this step should accomplish
    expected_output: str       # what success looks like
    dependencies: list[int]    # indices of prior steps this depends on
    delegate_to: str | None    # optional: pattern to delegate to
```

#### Nodes

**planner_node**:
- Input: user query
- Output: list of 2-7 Steps
- Uses `smart_llm_call()` with a structured output prompt
- Prompt includes experiential learning context from similar past queries
- Injects examples of good plans from experience_memory.db

**step_executor_node**:
- Executes one step at a time (sequential, not parallel — steps have dependencies)
- If `delegate_to` is set, calls the appropriate pattern's `run_*()` function for that step
- If not, uses enhanced_swarm as the default executor
- Appends StepResult to `completed_steps`
- Calls `prune_state()` per existing convention

**evaluator_node**:
- After each step, checks:
  1. Is the output non-empty? (deterministic)
  2. Does it reference the expected entities? (deterministic)
  3. Did we learn something that changes the remaining plan? (LLM check, only if needed)
- Returns: `continue` (next step), `replan` (regenerate remaining steps), or `synthesize` (done early)

**replanner_node**:
- Takes completed steps + remaining plan + evaluator's reason
- Regenerates only the remaining steps (preserves completed work)
- Max 2 replans. After that, proceeds with current plan.
- Logs replan reason to experience_memory.db for future learning

**synthesizer_node**:
- Combines all step outputs into a coherent final response
- Uses `smart_llm_call()` for synthesis
- Scores the result (quality + accuracy)
- If score >= 7.0, extracts learnings for experiential learning
- Returns final output with plan summary header

#### Convergence

- Max 7 steps per plan
- Max 2 replans
- Each step has a 60-second timeout (matches existing arXiv timeout)
- Total execution timeout: 5 minutes
- Quality gate: same dual-gate as other patterns (quality >= 8.0, accuracy >= 9.5)
  - But applied at synthesis, not per-step

#### Job Autopilot Integration

Opt-in via `JOB_AUTOPILOT_ADAPTIVE=true` (default: false).

When enabled, `run_scan_window()` feeds scan results into plan-and-execute instead of the rigid pipeline:

```python
if JOB_AUTOPILOT_ADAPTIVE:
    plan = plan_from_scan_results(listings)
    # Plan might: group by company, pick best-fit, skip dupes
    return run_plan(plan)
else:
    # Existing rigid pipeline
    for listing in listings:
        screen → generate → apply
```

The planner sees all scan results at once and makes decisions like:
- "Company X has 3 roles — pick the best match, not all three"
- "This listing looks like a repost of one we applied to — skip"
- "These 2 roles are nearly identical at different companies — apply to higher-match first"

This stays behind a feature flag until proven reliable over 2+ weeks.

### 3.2 Map-Reduce Pattern

**New file**: `patterns/map_reduce.py`

#### Topology

```
splitter → parallel_map (N workers) → reducer → [reconciler]?
```

4 nodes, ~200 lines. Lightweight by design.

#### State

```python
class MapReduceState(TypedDict):
    topic: str                      # immutable input
    chunks: list[str]               # split input
    map_results: list[str]          # one per chunk
    reduced_output: str
    needs_reconciliation: bool
    final_output: str
    quality_score: float
    token_usage: list[dict]
```

#### Nodes

**splitter_node**:
- Takes input and splits by strategy:
  - `by_item`: list of papers, applications, repos → one chunk per item
  - `by_section`: long document → semantic section boundaries
  - `by_entity`: "analyze companies A, B, C" → one chunk per entity
- Strategy auto-detected from input structure, or specified by caller
- Max 20 chunks (prevents runaway parallelism)

**map_node**:
- Runs via existing `parallel_executor.py` (`parallel_grpo_candidates` or similar)
- Each worker gets: chunk + shared prompt template + topic context
- Workers are stateless — no cross-communication
- Timeout: 30 seconds per worker
- Uses `get_llm()` — respects local/cloud LLM config

**reducer_node**:
- Two modes:
  - `summarize`: concatenate map outputs + synthesize into coherent summary
  - `rank`: score each map output, return top-N with reasoning
- Mode auto-detected: if chunks are items to compare → rank. Otherwise → summarize.
- Uses `smart_llm_call()` for synthesis

**reconciler_node** (optional):
- Only runs if reducer detects contradictions across chunks
- Single pass to resolve conflicts and produce consistent output
- Skipped in 80%+ of cases

#### Use Cases

| Input | Split Strategy | Map Task | Reduce Mode |
|-------|---------------|----------|-------------|
| "Summarize 50 papers" | by_item | Summarize each paper | summarize |
| "Analyze 20 pending apps" | by_item | Score each application | rank |
| "Review all agents in jobpulse/" | by_item (files) | Assess each agent | summarize |
| "Compare 5 databases" | by_entity | Research each DB | rank |

### 3.3 Auto-Router Additions

Two new entries in `pattern_router.py`:

```python
PATTERN_RULES.extend([
    PatternRule(
        signals=["first...then", "step by step", "compare then recommend",
                 "research and then", "analyze and decide"],
        pattern="plan_and_execute",
    ),
    PatternRule(
        signals=["all", "every", "each of", "batch", "summarize all",
                 "review all", "analyze all"],
        pattern="map_reduce",
        min_entities=3,  # only trigger if 3+ items detected
    ),
])
```

### Phase 3 Verification

**Plan-and-Execute**:
- "Compare FastAPI vs Django vs Flask for webhooks" → plan with 4 steps, executes sequentially
- "Research quantum ML and recommend a paper to implement" → 2-step plan with replan capability
- Job autopilot adaptive mode: scan produces grouped recommendations instead of linear pipeline

**Map-Reduce**:
- "Summarize all papers from this week" → splits into N papers, parallel map, synthesized digest
- "Batch analyze my pending applications" → parallel scoring, ranked output

---

## Full Architecture Diagram

```
Telegram message
  │
  ├─ NLP classify (41+ intents, 3-tier: regex → embeddings → LLM)
  │
  ├─ swarm_dispatcher.dispatch()
  │    │
  │    ├─ is_research_query()?
  │    │    │
  │    │    YES → pattern_router.select()
  │    │    │      │
  │    │    │      ├─ Override prefix? → use specified pattern
  │    │    │      │
  │    │    │      └─ Auto-classify:
  │    │    │           ├─ Comparative/controversial → Peer Debate
  │    │    │           ├─ Multi-entity parallel     → Dynamic Swarm
  │    │    │           ├─ Structured/in-depth       → Hierarchical
  │    │    │           ├─ Multi-step + dependencies  → Plan-and-Execute
  │    │    │           ├─ Batch/parallel (3+ items)  → Map-Reduce
  │    │    │           └─ Default                    → Enhanced Swarm
  │    │    │
  │    │    └─ run_with_pattern() → response + "[Pattern] header"
  │    │
  │    └─ NO → direct agent (budget, gmail, calendar, tasks, jobs, etc.)
  │
  └─ experiential_learning.store()
       └─ pattern selection feedback → router improves over time
```

## Shared Infrastructure (No Changes Needed)

These existing modules serve all 6 patterns without modification:

- `shared/agents.py` — `get_llm()`, `smart_llm_call()`, `risk_aware_reviewer_node`
- `shared/experiential_learning.py` — `ExperienceMemory`, `TrainingFreeGRPO`
- `shared/parallel_executor.py` — parallel worker pool
- `shared/streaming.py` — streaming output support
- `shared/fact_checker.py` — claim verification
- `shared/code_graph.py` — AST-based risk analysis

## New Files Summary

| File | Phase | Lines (est.) | Purpose |
|------|-------|-------------|---------|
| `jobpulse/pattern_router.py` | 2 | ~200 | Auto-router + override + feedback |
| `patterns/plan_and_execute.py` | 3 | ~350 | 5-node plan-execute-replan graph |
| `patterns/map_reduce.py` | 3 | ~200 | 4-node split-map-reduce graph |

## Modified Files Summary

| File | Phase | Change |
|------|-------|--------|
| crontab | 1 | Fix all paths |
| `jobpulse/gmail_agent.py` | 1 | Initialize `messages = []` |
| `jobpulse/job_autopilot.py` | 1 | `description` → `description_raw` |
| `jobpulse/notion_papers_agent.py` | 1 | Fix `fast_score` import |
| `jobpulse/arxiv_agent.py` | 1 | Add User-Agent header, increase backoff |
| `jobpulse/voice_handler.py` | 1 | Fix import path |
| `patterns/hierarchical.py` | 1 | Wire experiential learning |
| `jobpulse/swarm_dispatcher.py` | 2 | Add pattern router call |
| `jobpulse/dispatcher.py` | 2 | Add pattern router call (flat path) |
| `shared/nlp_classifier.py` | 2 | Add `research` intent examples |
| `jobpulse/job_autopilot.py` | 3 | Opt-in adaptive mode |

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Auto-router misclassifies | Override syntax always available; feedback loop self-corrects |
| Plan-and-execute infinite replans | Hard cap at 2 replans, 5-minute total timeout |
| Map-reduce parallelism overload | Max 20 chunks, reuses existing parallel_executor pool |
| Job autopilot adaptive breaks applications | Feature-flagged (default off), 2-week proving period |
| Pattern router adds latency | Rule-based tier is instant; embedding tier is 5ms; no LLM needed |

## Testing Strategy

- All new patterns: unit tests with mocked LLM (no real API calls)
- Pattern router: test all signal→pattern mappings + override syntax
- Integration: end-to-end test with a real query through each pattern
- Job autopilot adaptive: test with fixture scan results, verify plan output
- All tests use `tmp_path` for databases per project rules
- Tests mirror source: `tests/patterns/test_plan_and_execute.py`, `tests/patterns/test_map_reduce.py`, `tests/jobpulse/test_pattern_router.py`
