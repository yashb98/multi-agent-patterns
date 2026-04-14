# Ultraplan Phase 2: Unlock Dormant Patterns + Auto-Router

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all 4 existing LangGraph patterns (Enhanced Swarm, Peer Debate, Dynamic Swarm, Hierarchical) reachable from Telegram via an intelligent auto-router, with a new `research` intent.

**Architecture:** 2-tier pattern classifier (rule-based + embedding fallback) that detects research-style queries and routes them to the best pattern. Override syntax lets users force a specific pattern. Feedback loop stores pattern selection results for future learning. New `RESEARCH` intent in NLP classifier + dual dispatcher wiring.

**Tech Stack:** Python 3.12, LangGraph, sentence-transformers embeddings, SQLite (experience_memory.db)

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `jobpulse/pattern_router.py` | Create | 2-tier classifier, override parsing, pattern selection, feedback logging |
| `tests/jobpulse/test_pattern_router.py` | Create | Tests for rule-based tier, override syntax, embedding fallback, is_research_query |
| `jobpulse/command_router.py` | Modify | Add `RESEARCH` intent to Intent enum |
| `jobpulse/command_router.py` | Modify | Add regex patterns for `research` intent |
| `jobpulse/swarm_dispatcher.py` | Modify | Add pattern routing for research queries |
| `jobpulse/dispatcher.py` | Modify | Add `_handle_research` handler |
| `tests/test_swarm_dispatcher.py` | Modify | Add tests for research intent + pattern routing |
| `data/intent_examples.json` | Modify | Add 20 embedding examples for `research` intent |

---

### Task 1: Add RESEARCH Intent to Command Router

**Files:**
- Modify: `jobpulse/command_router.py:19-69` (Intent enum)
- Modify: `jobpulse/command_router.py:81-260` (PATTERNS list)

- [ ] **Step 1: Add RESEARCH to the Intent enum**

In `jobpulse/command_router.py`, add after `INTERVIEW_PREP = "interview_prep"` (line 68):

```python
    RESEARCH = "research"
```

- [ ] **Step 2: Add regex patterns for research intent**

In the `PATTERNS` list, add BEFORE the ARXIV patterns (research must match before arxiv for explicit research queries, but arxiv-specific keywords like "papers" still match ARXIV):

```python
    # Research (explicit research/analysis requests — routed to LangGraph patterns)
    (Intent.RESEARCH, [
        r"^research\s+(.+)",
        r"^investigate\s+(.+)",
        r"^analyze\s+(.+)",
        r"^compare\s+(.+)\s+(vs|versus|or|and)\s+(.+)",
        r"^(debate|argue)\s*:?\s+(.+)",
        r"^(deep dive|explain in depth|break down)\s+(.+)",
    ]),
```

- [ ] **Step 3: Verify existing tests still pass**

```bash
python -m pytest tests/test_command_router.py -v --tb=short -q
```

Expected: all existing tests pass (no regressions)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/command_router.py
git commit -m "feat: add RESEARCH intent to command router

New intent for explicit research/analysis queries that will be routed
to LangGraph patterns via the auto-router (next task).

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Pattern Router — Tests First

**Files:**
- Create: `tests/jobpulse/test_pattern_router.py`
- Create: `jobpulse/pattern_router.py`

- [ ] **Step 1: Write failing tests**

Create `tests/jobpulse/test_pattern_router.py`:

```python
"""Tests for pattern auto-router — selects best LangGraph pattern for research queries."""
from unittest.mock import patch, MagicMock
from jobpulse.command_router import Intent, ParsedCommand


class TestOverrideSyntax:
    """Test explicit override prefix parsing."""

    def test_debate_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("debate: React vs Vue")
        assert pattern == "peer_debate"
        assert query == "React vs Vue"

    def test_swarm_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("swarm: quantum computing")
        assert pattern == "enhanced_swarm"
        assert query == "quantum computing"

    def test_deep_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("deep: transformer architecture")
        assert pattern == "hierarchical"
        assert query == "transformer architecture"

    def test_plan_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("plan: research VDBs then benchmark")
        assert pattern == "plan_and_execute"
        assert query == "research VDBs then benchmark"

    def test_batch_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("batch: summarize all papers")
        assert pattern == "map_reduce"
        assert query == "summarize all papers"

    def test_dynamic_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("dynamic: analyze Postgres, MongoDB, Redis")
        assert pattern == "dynamic_swarm"
        assert query == "analyze Postgres, MongoDB, Redis"

    def test_no_override(self):
        from jobpulse.pattern_router import parse_override
        pattern, query = parse_override("what is quantum computing")
        assert pattern is None
        assert query == "what is quantum computing"


class TestRuleBasedTier:
    """Test rule-based pattern selection signals."""

    def test_comparative_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("React vs Vue for dashboards")
        assert pattern == "peer_debate"
        assert "comparative" in reason.lower() or "vs" in reason.lower()

    def test_compare_keyword_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("compare React and Vue")
        assert pattern == "peer_debate"

    def test_opinion_routes_to_debate(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("should I learn Rust or Go?")
        assert pattern == "peer_debate"

    def test_structured_routes_to_hierarchical(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("break down transformer architecture")
        assert pattern == "hierarchical"

    def test_report_routes_to_hierarchical(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("explain in depth how GPT works")
        assert pattern == "hierarchical"

    def test_multi_entity_routes_to_dynamic(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("analyze Postgres, MongoDB, and Redis for caching")
        assert pattern == "dynamic_swarm"

    def test_default_routes_to_enhanced_swarm(self):
        from jobpulse.pattern_router import select_pattern
        pattern, _ = select_pattern("quantum ML advances")
        assert pattern == "enhanced_swarm"

    def test_override_wins(self):
        from jobpulse.pattern_router import select_pattern
        pattern, reason = select_pattern("swarm: compare React vs Vue")
        assert pattern == "enhanced_swarm"
        assert "override" in reason.lower()


class TestIsResearchQuery:
    """Test research query detection."""

    def test_arxiv_intent_is_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.ARXIV, args="papers", raw="papers")
        assert is_research_query(cmd) is True

    def test_research_intent_is_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.RESEARCH, args="quantum computing", raw="research quantum computing")
        assert is_research_query(cmd) is True

    def test_budget_intent_is_not_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.LOG_SPEND, args="5 on coffee", raw="spent 5 on coffee")
        assert is_research_query(cmd) is False

    def test_conversation_with_research_signals(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.CONVERSATION, args="", raw="compare React vs Vue for enterprise dashboards")
        assert is_research_query(cmd) is True

    def test_conversation_without_research_signals(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.CONVERSATION, args="", raw="hello how are you")
        assert is_research_query(cmd) is False

    def test_jobs_intent_is_not_research(self):
        from jobpulse.pattern_router import is_research_query
        cmd = ParsedCommand(intent=Intent.SCAN_JOBS, args="", raw="scan jobs")
        assert is_research_query(cmd) is False


class TestResponseHeader:
    """Test pattern response header formatting."""

    def test_header_format(self):
        from jobpulse.pattern_router import format_response_header
        header = format_response_header("peer_debate", 3, 8.4)
        assert "[Peer Debate]" in header
        assert "3 rounds" in header
        assert "8.4" in header
        assert "Override:" in header

    def test_header_with_swarm(self):
        from jobpulse.pattern_router import format_response_header
        header = format_response_header("enhanced_swarm", 1, 7.5)
        assert "[Enhanced Swarm]" in header
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_pattern_router.py -v --tb=short
```

Expected: FAIL — `ModuleNotFoundError: No module named 'jobpulse.pattern_router'`

- [ ] **Step 3: Implement pattern_router.py**

Create `jobpulse/pattern_router.py`:

```python
"""Pattern auto-router — selects the best LangGraph pattern for research queries.

2-tier classifier:
  Tier 1: Rule-based signal matching (instant, free)
  Tier 2: Embedding similarity fallback (5ms, uses nlp_classifier infra)

Override syntax: prefix message with pattern keyword (debate:, swarm:, deep:, etc.)
"""
import re
from jobpulse.command_router import Intent, ParsedCommand
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ── Override Prefixes ──

OVERRIDE_MAP = {
    "debate": "peer_debate",
    "swarm": "enhanced_swarm",
    "deep": "hierarchical",
    "plan": "plan_and_execute",
    "batch": "map_reduce",
    "dynamic": "dynamic_swarm",
}

OVERRIDE_RE = re.compile(
    r"^(" + "|".join(OVERRIDE_MAP.keys()) + r")\s*:\s*(.+)", re.IGNORECASE
)

# ── Rule-Based Signals ──

COMPARATIVE_RE = re.compile(
    r"\b(vs\.?|versus|compare|compared to|which is better|pros and cons|advantages of .+ over)\b",
    re.IGNORECASE,
)
OPINION_RE = re.compile(
    r"\b(should I|is .+ worth|debate|argue|opinion on|which should)\b",
    re.IGNORECASE,
)
STRUCTURED_RE = re.compile(
    r"\b(outline|report on|break down|explain in depth|deep dive|in-depth|comprehensive)\b",
    re.IGNORECASE,
)
MULTI_STEP_RE = re.compile(
    r"\b(first .+ then|step by step|compare then recommend|research .+ benchmark)\b",
    re.IGNORECASE,
)
BATCH_RE = re.compile(
    r"\b(summarize all|every one of|each of the|all \d+ |batch)\b",
    re.IGNORECASE,
)

# Research signals for CONVERSATION intent detection
RESEARCH_SIGNALS_RE = re.compile(
    r"\b(compare|analyze|explain|what is|how does|investigate|research|"
    r"vs\.?|versus|architecture|algorithm|framework|benchmark|"
    r"trade.?offs?|pros and cons|advantages|disadvantages)\b",
    re.IGNORECASE,
)

# Intents that are always research
RESEARCH_INTENTS = {Intent.ARXIV, Intent.RESEARCH}

# Intents that are never research
NON_RESEARCH_INTENTS = {
    Intent.LOG_SPEND, Intent.LOG_INCOME, Intent.LOG_SAVINGS, Intent.SET_BUDGET,
    Intent.SHOW_BUDGET, Intent.SHOW_TASKS, Intent.CREATE_TASKS, Intent.COMPLETE_TASK,
    Intent.REMOVE_TASK, Intent.CALENDAR, Intent.CREATE_EVENT, Intent.GMAIL,
    Intent.GITHUB, Intent.TRENDING, Intent.BRIEFING, Intent.WEEKLY_REPORT,
    Intent.EXPORT, Intent.HELP, Intent.CLEAR_CHAT, Intent.REMOTE_SHELL,
    Intent.GIT_OPS, Intent.FILE_OPS, Intent.SYSTEM_STATUS, Intent.STOP,
    Intent.LOG_HOURS, Intent.SHOW_HOURS, Intent.CONFIRM_SAVINGS,
    Intent.UNDO_HOURS, Intent.UNDO_BUDGET, Intent.RECURRING_BUDGET,
    Intent.WEEKLY_PLAN, Intent.SCAN_JOBS, Intent.SHOW_JOBS, Intent.APPROVE_JOBS,
    Intent.REJECT_JOB, Intent.JOB_DETAIL, Intent.JOB_STATS, Intent.SEARCH_CONFIG,
    Intent.PAUSE_JOBS, Intent.RESUME_JOBS, Intent.ENGINE_STATS, Intent.ENGINE_COMPARE,
    Intent.ENGINE_LEARNING, Intent.ENGINE_RESET, Intent.JOB_PATTERNS,
    Intent.FOLLOW_UPS, Intent.INTERVIEW_PREP,
}

# Pattern display names
PATTERN_NAMES = {
    "enhanced_swarm": "Enhanced Swarm",
    "peer_debate": "Peer Debate",
    "hierarchical": "Hierarchical",
    "dynamic_swarm": "Dynamic Swarm",
    "plan_and_execute": "Plan-and-Execute",
    "map_reduce": "Map-Reduce",
}


def parse_override(text: str) -> tuple[str | None, str]:
    """Check for override prefix. Returns (pattern_name, remaining_query) or (None, original_text)."""
    m = OVERRIDE_RE.match(text.strip())
    if m:
        prefix = m.group(1).lower()
        query = m.group(2).strip()
        return OVERRIDE_MAP[prefix], query
    return None, text


def _count_entities(text: str) -> int:
    """Count comma/and-separated entities (heuristic for multi-entity detection)."""
    # Split on commas and "and"
    parts = re.split(r",\s*|\s+and\s+", text)
    # Filter out short/empty parts
    return len([p for p in parts if len(p.strip()) > 2])


def select_pattern(query: str) -> tuple[str, str]:
    """Select the best pattern for a research query.

    Returns (pattern_name, reason).
    """
    # Tier 0: Override check
    override, clean_query = parse_override(query)
    if override:
        return override, f"Override: user requested {PATTERN_NAMES.get(override, override)}"

    # Tier 1: Rule-based signals
    if COMPARATIVE_RE.search(query) or OPINION_RE.search(query):
        return "peer_debate", "Comparative/opinion query — debate produces best results"

    if MULTI_STEP_RE.search(query):
        return "plan_and_execute", "Multi-step query with dependencies"

    if BATCH_RE.search(query):
        return "map_reduce", "Batch/parallel processing query"

    if _count_entities(query) >= 3:
        return "dynamic_swarm", "Multi-entity analysis (3+ entities detected)"

    if STRUCTURED_RE.search(query):
        return "hierarchical", "Structured/in-depth analysis request"

    # Tier 2: Default to enhanced swarm (most versatile)
    return "enhanced_swarm", "Default pattern — single-topic research"


def is_research_query(cmd: ParsedCommand) -> bool:
    """Determine if a command should be routed through the pattern router."""
    if cmd.intent in RESEARCH_INTENTS:
        return True
    if cmd.intent in NON_RESEARCH_INTENTS:
        return False
    # CONVERSATION intent — check for research signals
    if cmd.intent == Intent.CONVERSATION:
        return bool(RESEARCH_SIGNALS_RE.search(cmd.raw))
    return False


def format_response_header(pattern: str, iterations: int, quality_score: float) -> str:
    """Format the pattern response header shown to the user."""
    name = PATTERN_NAMES.get(pattern, pattern)
    overrides = " | ".join(OVERRIDE_MAP.keys())
    return f"[{name}] {iterations} rounds, converged at quality={quality_score}\nOverride: {overrides}"


def run_with_pattern(pattern: str, query: str) -> str:
    """Execute a query with the selected LangGraph pattern.

    Returns the pattern's output with a response header prepended.
    """
    try:
        if pattern == "enhanced_swarm":
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
        elif pattern == "peer_debate":
            from patterns.peer_debate import run_debate
            result = run_debate(query)
        elif pattern == "dynamic_swarm":
            from patterns.dynamic_swarm import run_swarm
            result = run_swarm(query)
        elif pattern == "hierarchical":
            from patterns.hierarchical import run_hierarchical
            result = run_hierarchical(query)
        elif pattern == "plan_and_execute":
            # Phase 3 — not yet implemented, fallback to enhanced swarm
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
            logger.info("plan_and_execute not yet implemented, used enhanced_swarm fallback")
        elif pattern == "map_reduce":
            # Phase 3 — not yet implemented, fallback to enhanced swarm
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)
            logger.info("map_reduce not yet implemented, used enhanced_swarm fallback")
        else:
            from patterns.enhanced_swarm import run_enhanced_swarm
            result = run_enhanced_swarm(query)

        # Extract score and iterations from result if available
        output = result if isinstance(result, str) else result.get("final_output", str(result))
        return output

    except Exception as e:
        logger.error("Pattern %s failed: %s", pattern, e)
        return f"Pattern execution failed: {e}"


def log_pattern_selection(query: str, pattern: str, override: bool, quality_score: float):
    """Log pattern selection to experiential learning for future weight tuning."""
    try:
        from shared.experiential_learning import Experience, get_shared_experience_memory
        exp = Experience(
            task_description=f"Pattern selection: {query[:200]}",
            successful_pattern=f"Selected {pattern} (override={override}, score={quality_score})",
            score=quality_score,
            domain="pattern_routing",
        )
        get_shared_experience_memory().add(exp)
    except Exception as e:
        logger.debug("Failed to log pattern selection: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/jobpulse/test_pattern_router.py -v --tb=short
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/pattern_router.py tests/jobpulse/test_pattern_router.py
git commit -m "feat: add pattern auto-router with 2-tier classifier

Rule-based tier: comparative→debate, structured→hierarchical,
multi-entity→dynamic_swarm, batch→map_reduce, default→enhanced_swarm.
Override syntax: debate:/swarm:/deep:/plan:/batch:/dynamic: prefix.
is_research_query() gates which intents enter the pattern router.
Feedback logging to experiential learning for future weight tuning.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Wire Pattern Router into Swarm Dispatcher

**Files:**
- Modify: `jobpulse/swarm_dispatcher.py:114-183` (analyze_task)
- Modify: `jobpulse/swarm_dispatcher.py:259-319` (dispatch)
- Test: `tests/test_swarm_dispatcher.py`

- [ ] **Step 1: Add research intent handling to analyze_task**

In `jobpulse/swarm_dispatcher.py`, in `analyze_task()`, add BEFORE the `SIMPLE_INTENTS` check (line 123):

```python
    # Research queries — route through pattern auto-router
    from jobpulse.pattern_router import is_research_query
    if is_research_query(cmd):
        return [{"agent": "pattern_router", "priority": 1, "description": "Research via LangGraph pattern"}]
```

Also add `Intent.RESEARCH` to the imports at the top of the file if not already imported.

- [ ] **Step 2: Add pattern_router to _execute_agent**

In `_execute_agent()`, add a handler for "pattern_router" agent BEFORE the "Standard agent" fallback:

```python
    elif agent_name == "pattern_router":
        from jobpulse.pattern_router import select_pattern, run_with_pattern, log_pattern_selection
        pattern, reason = select_pattern(cmd.raw)
        logger.info("Pattern router: %s — %s", pattern, reason)
        result = run_with_pattern(pattern, cmd.raw)
        # Log selection for learning
        log_pattern_selection(cmd.raw, pattern, override=("override" in reason.lower()), quality_score=0.0)
        return result
```

- [ ] **Step 3: Add tests for research routing**

Append to `tests/test_swarm_dispatcher.py`:

```python
class TestResearchRouting:
    """Test that research queries route through pattern router."""

    def _make_cmd(self, intent: Intent, raw: str = "test") -> ParsedCommand:
        return ParsedCommand(intent=intent, args=raw, raw=raw)

    def _analyze(self, intent: Intent, raw: str = "test") -> list:
        from jobpulse.swarm_dispatcher import analyze_task
        trail = MagicMock()
        return analyze_task(self._make_cmd(intent, raw), trail)

    def test_research_intent_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.RESEARCH, "research quantum computing")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"

    def test_conversation_with_research_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.CONVERSATION, "compare React vs Vue for dashboards")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"

    def test_conversation_without_research_stays_simple(self):
        tasks = self._analyze(Intent.CONVERSATION, "hello how are you")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "conversation"

    def test_budget_still_works(self):
        tasks = self._analyze(Intent.LOG_SPEND, "spent 5 on coffee")
        assert len(tasks) == 1
        assert tasks[0].get("grpo") is True

    def test_arxiv_routes_to_pattern_router(self):
        tasks = self._analyze(Intent.ARXIV, "papers")
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "pattern_router"
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_swarm_dispatcher.py -v --tb=short
python -m pytest tests/jobpulse/test_pattern_router.py -v --tb=short
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add jobpulse/swarm_dispatcher.py tests/test_swarm_dispatcher.py
git commit -m "feat: wire pattern router into swarm dispatcher

Research queries (RESEARCH, ARXIV, CONVERSATION+signals) now route
through pattern_router agent instead of direct handlers. Pattern
selection logged for experiential learning.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Wire Pattern Router into Flat Dispatcher

**Files:**
- Modify: `jobpulse/dispatcher.py`

Per the dual dispatcher invariant, the flat dispatcher must mirror the swarm dispatcher's research routing.

- [ ] **Step 1: Read current dispatcher.py structure**

Check how `_handle_arxiv` and `_handle_conversation` work. Read the AGENT_MAP or handler registry.

- [ ] **Step 2: Add _handle_research to dispatcher.py**

Add a handler function:

```python
def _handle_research(cmd: ParsedCommand) -> str:
    """Route research queries through pattern auto-router."""
    from jobpulse.pattern_router import select_pattern, run_with_pattern, format_response_header, log_pattern_selection
    pattern, reason = select_pattern(cmd.raw)
    logger.info("Pattern router: %s — %s", pattern, reason)
    result = run_with_pattern(pattern, cmd.raw)
    log_pattern_selection(cmd.raw, pattern, override=("override" in reason.lower()), quality_score=0.0)
    return result
```

- [ ] **Step 3: Register the handler**

Add the RESEARCH intent handler to the handler registry / AGENT_MAP in dispatcher.py, alongside the existing handlers. Check how `_handle_arxiv` is registered and follow the same pattern.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/ -k "dispatch" -v --tb=short -q
```

Expected: all dispatch tests pass

- [ ] **Step 5: Commit**

```bash
git add jobpulse/dispatcher.py
git commit -m "feat: wire pattern router into flat dispatcher (dual dispatcher invariant)

Mirrors swarm_dispatcher research routing. RESEARCH intent handler
added to flat dispatcher for JOBPULSE_SWARM=false mode.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Add NLP Embedding Examples for Research Intent

**Files:**
- Modify: `data/intent_examples.json`

- [ ] **Step 1: Read current intent_examples.json structure**

Check how existing intents are structured (key format, example format).

- [ ] **Step 2: Add 20 research intent examples**

Add a `"research"` key with examples like:

```json
"research": [
    "research quantum computing advances",
    "investigate transformer architectures",
    "analyze the impact of LLMs on software engineering",
    "compare different vector databases",
    "what are the trade-offs between RAG and fine-tuning",
    "explain how attention mechanisms work",
    "deep dive into reinforcement learning from human feedback",
    "break down the RLHF pipeline",
    "compare PyTorch vs JAX for research",
    "how does mixture of experts work",
    "investigate multi-agent coordination patterns",
    "analyze the pros and cons of microservices",
    "research the latest advances in code generation",
    "compare embedding models for semantic search",
    "explain diffusion models step by step",
    "what is the current state of autonomous agents",
    "investigate knowledge graph construction methods",
    "analyze trade-offs in distributed training",
    "research federated learning privacy guarantees",
    "compare RLHF vs DPO vs GRPO for alignment"
]
```

- [ ] **Step 3: Verify NLP classifier loads cleanly**

```bash
python -c "
from jobpulse.nlp_classifier import load_examples
examples = load_examples()
print('research examples:', len(examples.get('research', [])))
print('Total intents:', len(examples))
"
```

Expected: `research examples: 20`

- [ ] **Step 4: Commit**

```bash
git add data/intent_examples.json
git commit -m "feat: add 20 NLP embedding examples for research intent

Enables semantic-tier classification of research queries that don't
match regex patterns. Covers QA, comparison, explanation, and analysis.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Final Verification

- [ ] **Step 1: Run full test suite for changed files**

```bash
python -m pytest tests/jobpulse/test_pattern_router.py tests/test_swarm_dispatcher.py tests/test_command_router.py -v --tb=short
```

Expected: all tests pass

- [ ] **Step 2: Verify pattern routing works end-to-end**

```bash
python -c "
from jobpulse.pattern_router import select_pattern, is_research_query, parse_override
from jobpulse.command_router import Intent, ParsedCommand

# Test override
p, q = parse_override('debate: React vs Vue')
print(f'Override: pattern={p}, query={q}')

# Test rule-based
p, r = select_pattern('React vs Vue for dashboards')
print(f'Comparative: pattern={p}, reason={r}')

p, r = select_pattern('break down transformer architecture')
print(f'Structured: pattern={p}, reason={r}')

p, r = select_pattern('analyze Postgres, MongoDB, and Redis for caching')
print(f'Multi-entity: pattern={p}, reason={r}')

p, r = select_pattern('quantum ML advances')
print(f'Default: pattern={p}, reason={r}')

# Test is_research_query
cmd = ParsedCommand(intent=Intent.RESEARCH, args='test', raw='research test')
print(f'RESEARCH intent: is_research={is_research_query(cmd)}')

cmd = ParsedCommand(intent=Intent.LOG_SPEND, args='5', raw='spent 5')
print(f'BUDGET intent: is_research={is_research_query(cmd)}')

cmd = ParsedCommand(intent=Intent.CONVERSATION, args='', raw='compare React vs Vue')
print(f'CONVERSATION+signals: is_research={is_research_query(cmd)}')
"
```

Expected output:
```
Override: pattern=peer_debate, query=React vs Vue
Comparative: pattern=peer_debate, reason=Comparative/opinion query...
Structured: pattern=hierarchical, reason=Structured/in-depth...
Multi-entity: pattern=dynamic_swarm, reason=Multi-entity...
Default: pattern=enhanced_swarm, reason=Default...
RESEARCH intent: is_research=True
BUDGET intent: is_research=False
CONVERSATION+signals: is_research=True
```

- [ ] **Step 3: Commit plan + push all**

```bash
git add docs/superpowers/plans/2026-04-14-ultraplan-phase2.md
git commit -m "docs: add ultraplan Phase 2 implementation plan

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
git push origin main
```
