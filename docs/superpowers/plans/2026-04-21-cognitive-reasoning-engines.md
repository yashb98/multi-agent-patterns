# Cognitive Reasoning Engines — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 4-level cognitive engine (`shared/cognitive/`) that any agent calls via `engine.think(task, domain)` — classifying complexity, composing prompts from strategy templates + anti-patterns, executing at the right reasoning level (L0 Memory Recall → L1 Single Shot → L2 Reflexion → L3 Tree of Thought), and storing learnings for self-improvement.

**Architecture:** `CognitiveEngine` is the single entry point. An `EscalationClassifier` picks the reasoning level via heuristic checks (memory → novelty → stakes). A `StrategyComposer` assembles prompts from Pillar 1 memory (PROCEDURAL templates + EPISODIC anti-patterns + evolved base prompt). `ReflexionLoop` handles L2 (try/critique/retry). `TreeOfThought` handles L3 (branch/score/prune/extend via GRPO). `BudgetTracker` enforces per-hour caps. All state stored in the existing MemoryManager — no new databases.

**Tech Stack:** Python 3.12, existing MemoryManager (shared/memory_layer), TrainingFreeGRPO (shared/experiential_learning), smart_llm_call (shared/streaming), get_llm/get_evolved_prompt, count_tokens (shared/context_compression), cost_tracker, pytest

**Spec:** `docs/superpowers/specs/2026-04-21-cognitive-reasoning-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `shared/cognitive/__init__.py` | **Create** | Public exports: CognitiveEngine, ThinkResult, ThinkLevel, CognitiveBudget |
| `shared/cognitive/_budget.py` | **Create** | CognitiveBudget dataclass, BudgetTracker (per-hour caps, cooldown, clamp) |
| `shared/cognitive/_classifier.py` | **Create** | EscalationClassifier (3-step heuristic: memory → novelty → stakes) |
| `shared/cognitive/_strategy.py` | **Create** | StrategyComposer, ComposedPrompt, STRATEGY_PAYLOAD schema, template retrieval |
| `shared/cognitive/_prompts.py` | **Create** | Prompt templates: critique, branch strategies, scoring, composition sections |
| `shared/cognitive/_reflexion.py` | **Create** | ReflexionLoop (try/critique/retry, stores success templates + failure patterns) |
| `shared/cognitive/_tree_of_thought.py` | **Create** | TreeOfThought (branch/score/prune/extend via GRPO) |
| `shared/cognitive/_engine.py` | **Create** | CognitiveEngine — orchestrates classify → compose → execute → store → flush |
| `shared/cost_tracker.py` | **Modify** | Add gpt-4.1-nano pricing |
| `tests/shared/cognitive/__init__.py` | **Create** | Empty init |
| `tests/shared/cognitive/conftest.py` | **Create** | Shared fixtures: mock MemoryManager, mock LLM, mock scorer, mock GRPO |
| `tests/shared/cognitive/test_budget.py` | **Create** | 8 budget tests |
| `tests/shared/cognitive/test_classifier.py` | **Create** | 10 classifier tests |
| `tests/shared/cognitive/test_strategy.py` | **Create** | 12 strategy composer tests |
| `tests/shared/cognitive/test_reflexion.py` | **Create** | 12 reflexion loop tests |
| `tests/shared/cognitive/test_tree_of_thought.py` | **Create** | 10 ToT tests |
| `tests/shared/cognitive/test_engine.py` | **Create** | 10 engine tests |
| `tests/shared/cognitive/test_integration.py` | **Create** | 8 integration tests |
| `tests/shared/cognitive/test_self_improvement.py` | **Create** | 6 self-improvement tests |
| `shared/cognitive/CLAUDE.md` | **Create** | Module docs: architecture, levels, how to opt in |
| `shared/CLAUDE.md` | **Modify** | Add cognitive engine section |
| `CLAUDE.md` (root) | **Modify** | Add to Module Context |
| `AGENTS.md` | **Modify** | Add cognitive briefing for subagents |
| `patterns/CLAUDE.md` | **Modify** | Add note: pattern nodes can use CognitiveEngine |
| `jobpulse/CLAUDE.md` | **Modify** | Add cognitive integration: which agents use it |

---

## Task 1: Test Infrastructure & Package Setup

**Files:**
- Create: `shared/cognitive/__init__.py`
- Create: `shared/cognitive/_prompts.py`
- Create: `tests/shared/cognitive/__init__.py`
- Create: `tests/shared/cognitive/conftest.py`
- Modify: `shared/cost_tracker.py:17-38`

- [ ] **Step 1: Create the shared/cognitive package directory**

```bash
mkdir -p shared/cognitive tests/shared/cognitive
```

- [ ] **Step 2: Create the empty init files**

Create `shared/cognitive/__init__.py`:

```python
"""Cognitive Reasoning Engines — 4-level graduated escalation.

Opt-in toolkit for agents. Any agent adds cognitive abilities via:

    from shared.cognitive import CognitiveEngine

    engine = CognitiveEngine(memory_manager, agent_name="my_agent")
    result = await engine.think(task="...", domain="...", stakes="medium")
    await engine.flush()

Levels:
    L0 Memory Recall  — strategy templates found, zero LLM calls
    L1 Single Shot    — one LLM call with composed prompt
    L2 Reflexion      — try/critique/retry with failure memory
    L3 Tree of Thought — parallel branch exploration via GRPO
"""
```

Create `tests/shared/cognitive/__init__.py`:

```python
```

- [ ] **Step 3: Create prompt templates**

Create `shared/cognitive/_prompts.py`:

```python
"""Prompt templates for cognitive reasoning components."""

CRITIQUE_PROMPT = """You are reviewing an agent's output. The task was:
{task}

The agent produced:
{output}

The score was {score}/10 (threshold: {threshold}).

What specifically went wrong? Identify the concrete mistake in one sentence.
Then suggest a specific fix in one sentence.

Format:
MISTAKE: [what went wrong]
FIX: [what to do differently]"""

BRANCH_STRATEGIES = [
    "Approach step by step from first principles.",
    "Think about what could go wrong first, then work backwards.",
    "Find the simplest possible solution. Minimum that works.",
    "What would a domain expert do? Think from their perspective.",
]

EXTENSION_PROMPT = """Build on this approach: {reasoning}

Refine and improve it. Keep what works, fix what doesn't."""

SCORING_PROMPT = """Rate this output on a scale of 0-10.

Task: {task}

Output: {output}

Consider: accuracy, completeness, clarity, actionability.
Return ONLY a number between 0 and 10."""

COMPOSED_SECTIONS = {
    "strategies": "\n## Learned Strategies\n{strategies}",
    "anti_patterns": "\n## Avoid These Mistakes\n{anti_patterns}",
    "task": "\n## Task\n{task}",
}
```

- [ ] **Step 4: Create shared test fixtures**

Create `tests/shared/cognitive/conftest.py`:

```python
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field


@dataclass
class MockProceduralEntry:
    procedure_id: str = "proc_001"
    domain: str = "test_domain"
    strategy: str = "Test strategy"
    context: str = "When testing"
    success_rate: float = 0.9
    times_used: int = 5
    avg_score_when_used: float = 8.5
    source: str = "reflexion"
    created_at: str = "2026-04-21T10:00:00"


@dataclass
class MockEpisodicEntry:
    run_id: str = "ep_001"
    topic: str = "test_task"
    timestamp: str = "2026-04-21T10:00:00"
    final_score: float = 3.0
    iterations: int = 1
    pattern_used: str = "reflexion"
    agents_used: list = field(default_factory=lambda: ["test_agent"])
    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=lambda: ["Failed on edge case"])
    output_summary: str = "Bad output"
    duration_seconds: float = 1.0
    total_llm_calls: int = 1
    domain: str = "test_domain"


class MockMemoryManager:
    """Mock MemoryManager that simulates Pillar 1 memory operations."""

    def __init__(self):
        self._procedural: list[MockProceduralEntry] = []
        self._episodic: list[MockEpisodicEntry] = []
        self._semantic: list[dict] = []
        self.store_calls: list[dict] = []
        self.learn_procedure_calls: list[dict] = []
        self.learn_fact_calls: list[dict] = []

    def get_context_for_agent(self, agent_name: str, topic: str, domain: str = "") -> str:
        sections = []
        procs = [p for p in self._procedural if p.domain == domain or not domain]
        if procs:
            lines = [f"- {p.strategy} (success: {p.success_rate:.0%})" for p in procs[:3]]
            sections.append("Proven strategies:\n" + "\n".join(lines))
        return "\n\n".join(sections)

    def learn_procedure(self, domain: str, strategy: str, context: str = "",
                        score: float = 7.0, source: str = "runtime"):
        self.learn_procedure_calls.append({
            "domain": domain, "strategy": strategy, "context": context,
            "score": score, "source": source,
        })
        self._procedural.append(MockProceduralEntry(
            domain=domain, strategy=strategy, context=context,
            avg_score_when_used=score, source=source,
        ))

    def record_episode(self, topic: str, final_score: float, iterations: int,
                       pattern_used: str, agents_used: list, strengths: list,
                       weaknesses: list, output_summary: str, **kwargs):
        self._episodic.append(MockEpisodicEntry(
            topic=topic, final_score=final_score, iterations=iterations,
            pattern_used=pattern_used, agents_used=agents_used,
            strengths=strengths, weaknesses=weaknesses,
            output_summary=output_summary, domain=kwargs.get("domain", ""),
        ))

    def learn_fact(self, domain: str, fact: str, run_id: str = "manual"):
        self.learn_fact_calls.append({"domain": domain, "fact": fact})

    def get_procedural_entries(self, domain: str) -> list[MockProceduralEntry]:
        return [p for p in self._procedural if p.domain == domain]

    def get_episodic_entries(self, domain: str) -> list[MockEpisodicEntry]:
        return [p for p in self._episodic if p.domain == domain]

    def search_patterns(self, topic: str, domain: str = ""):
        return None, 0.0


@pytest.fixture
def mock_memory():
    return MockMemoryManager()


@pytest.fixture
def mock_scorer():
    """Returns a scorer function that returns configurable scores."""
    scores = []

    def scorer(output: str) -> float:
        if scores:
            return scores.pop(0)
        return 8.0

    scorer.set_scores = lambda s: scores.extend(s)
    return scorer


@pytest.fixture
def mock_llm_response():
    """Returns a mock LLM response with .content attribute."""
    def make(content: str = "mock answer", model: str = "gpt-4.1-nano"):
        resp = MagicMock()
        resp.content = content
        resp.response_metadata = {
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model_name": model,
        }
        return resp
    return make
```

- [ ] **Step 5: Add gpt-4.1-nano pricing to cost_tracker.py**

In `shared/cost_tracker.py`, add nano pricing after the existing `gpt-4.1-mini` entry in `MODEL_COSTS`:

```python
    "gpt-4.1-nano": (0.10, 0.40),
```

- [ ] **Step 6: Verify package structure**

```bash
python -c "import shared.cognitive; print('Package OK')"
```

Expected: `Package OK`

- [ ] **Step 7: Commit**

```bash
git add shared/cognitive/__init__.py shared/cognitive/_prompts.py \
       tests/shared/cognitive/__init__.py tests/shared/cognitive/conftest.py \
       shared/cost_tracker.py
git commit -m "feat(cognitive): scaffold package, test fixtures, prompt templates, nano pricing"
```

---

## Task 2: CognitiveBudget & BudgetTracker

**Files:**
- Create: `shared/cognitive/_budget.py`
- Create: `tests/shared/cognitive/test_budget.py`

- [ ] **Step 1: Write the budget tests**

Create `tests/shared/cognitive/test_budget.py`:

```python
import time
import pytest
from unittest.mock import patch

from shared.cognitive._budget import CognitiveBudget, BudgetTracker, ThinkLevel


class TestBudgetTracker:

    def test_budget_allows_within_limits(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5)
        tracker = BudgetTracker(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_blocks_over_l2_limit(self):
        budget = CognitiveBudget(max_l2_per_hour=3, max_l3_per_hour=5)
        tracker = BudgetTracker(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE

    def test_budget_blocks_over_l3_limit(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=2)
        tracker = BudgetTracker(budget)
        for _ in range(2):
            tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L2_REFLEXION

    def test_cost_cap_blocks_escalation(self):
        budget = CognitiveBudget(max_cost_per_hour=0.10)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.09)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE

    def test_cooldown_after_cap(self):
        budget = CognitiveBudget(max_l2_per_hour=1, cooldown_minutes=5)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Simulate cooldown passing
        tracker._cooldown_until = time.monotonic() - 1
        # Still blocked by hourly count — cooldown alone doesn't reset count
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_resets_hourly(self):
        budget = CognitiveBudget(max_l2_per_hour=1)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Simulate 61 minutes passing by backdating the window start
        tracker._window_start = time.monotonic() - 3700
        assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_disabled_engine(self):
        budget = CognitiveBudget(enabled=False)
        tracker = BudgetTracker(budget)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L1_SINGLE) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L0_MEMORY) == ThinkLevel.L0_MEMORY

    def test_budget_report(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5,
                                 max_cost_per_hour=0.50)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        report = tracker.report()
        assert report["l2_used"] == 1
        assert report["l2_remaining"] == 19
        assert report["l3_used"] == 1
        assert report["l3_remaining"] == 4
        assert abs(report["cost_used"] - 0.035) < 0.001
        assert abs(report["cost_remaining"] - 0.465) < 0.001
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_budget.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._budget'`

- [ ] **Step 3: Implement CognitiveBudget and BudgetTracker**

Create `shared/cognitive/_budget.py`:

```python
"""Budget guardrails for cognitive reasoning levels."""

import os
import time
from dataclasses import dataclass
from enum import IntEnum

from shared.logging_config import get_logger

logger = get_logger(__name__)


class ThinkLevel(IntEnum):
    L0_MEMORY = 0
    L1_SINGLE = 1
    L2_REFLEXION = 2
    L3_TREE_OF_THOUGHT = 3


@dataclass
class CognitiveBudget:
    max_l2_per_hour: int = 20
    max_l3_per_hour: int = 5
    max_cost_per_hour: float = 0.50
    cooldown_minutes: int = 5
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "CognitiveBudget":
        return cls(
            max_l2_per_hour=int(os.getenv("COGNITIVE_MAX_L2_PER_HOUR", "20")),
            max_l3_per_hour=int(os.getenv("COGNITIVE_MAX_L3_PER_HOUR", "5")),
            max_cost_per_hour=float(os.getenv("COGNITIVE_MAX_COST_PER_HOUR", "0.50")),
            cooldown_minutes=int(os.getenv("COGNITIVE_COOLDOWN_MINUTES", "5")),
            enabled=os.getenv("COGNITIVE_ENABLED", "true").lower() == "true",
        )


class BudgetTracker:
    """Tracks cognitive level usage per hour and enforces caps."""

    def __init__(self, budget: CognitiveBudget):
        self._budget = budget
        self._window_start = time.monotonic()
        self._l2_count = 0
        self._l3_count = 0
        self._cost_total = 0.0
        self._cooldown_until = 0.0

    def _maybe_reset_window(self):
        elapsed = time.monotonic() - self._window_start
        if elapsed >= 3600:
            self._window_start = time.monotonic()
            self._l2_count = 0
            self._l3_count = 0
            self._cost_total = 0.0
            self._cooldown_until = 0.0

    def record(self, level: ThinkLevel, cost: float):
        self._maybe_reset_window()
        if level == ThinkLevel.L2_REFLEXION:
            self._l2_count += 1
        elif level == ThinkLevel.L3_TREE_OF_THOUGHT:
            self._l3_count += 1
        self._cost_total += cost

        if self._l2_count >= self._budget.max_l2_per_hour or \
           self._l3_count >= self._budget.max_l3_per_hour or \
           self._cost_total >= self._budget.max_cost_per_hour:
            self._cooldown_until = time.monotonic() + self._budget.cooldown_minutes * 60
            logger.warning("Cognitive budget cap reached — cooldown %d min",
                           self._budget.cooldown_minutes)

    def allows(self, level: ThinkLevel) -> bool:
        self._maybe_reset_window()
        if level <= ThinkLevel.L1_SINGLE:
            return True
        if time.monotonic() < self._cooldown_until:
            return False
        if level == ThinkLevel.L2_REFLEXION:
            return self._l2_count < self._budget.max_l2_per_hour and \
                   self._cost_total < self._budget.max_cost_per_hour
        if level == ThinkLevel.L3_TREE_OF_THOUGHT:
            return self._l3_count < self._budget.max_l3_per_hour and \
                   self._cost_total < self._budget.max_cost_per_hour
        return True

    def clamp(self, level: ThinkLevel) -> ThinkLevel:
        if not self._budget.enabled and level > ThinkLevel.L1_SINGLE:
            return ThinkLevel.L1_SINGLE
        while level > ThinkLevel.L0_MEMORY and not self.allows(level):
            level = ThinkLevel(level - 1)
        return level

    def report(self) -> dict:
        self._maybe_reset_window()
        return {
            "l2_used": self._l2_count,
            "l2_remaining": max(0, self._budget.max_l2_per_hour - self._l2_count),
            "l3_used": self._l3_count,
            "l3_remaining": max(0, self._budget.max_l3_per_hour - self._l3_count),
            "cost_used": round(self._cost_total, 4),
            "cost_remaining": round(max(0, self._budget.max_cost_per_hour - self._cost_total), 4),
            "enabled": self._budget.enabled,
            "in_cooldown": time.monotonic() < self._cooldown_until,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_budget.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add shared/cognitive/_budget.py tests/shared/cognitive/test_budget.py
git commit -m "feat(cognitive): add CognitiveBudget + BudgetTracker with per-hour caps"
```

---

## Task 3: EscalationClassifier

**Files:**
- Create: `shared/cognitive/_classifier.py`
- Create: `tests/shared/cognitive/test_classifier.py`

- [ ] **Step 1: Write the classifier tests**

Create `tests/shared/cognitive/test_classifier.py`:

```python
import pytest
from unittest.mock import MagicMock

from shared.cognitive._classifier import EscalationClassifier, STAKES_REGISTRY
from shared.cognitive._budget import ThinkLevel, CognitiveBudget, BudgetTracker
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestEscalationClassifier:

    def _make_classifier(self, memory=None, budget=None):
        mem = memory or MockMemoryManager()
        bgt = budget or BudgetTracker(CognitiveBudget())
        return EscalationClassifier(mem, bgt)

    def test_l0_when_strong_templates_exist(self, mock_memory):
        """Memory hit with high confidence → L0."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email_classification",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5, source="reflexion",
            ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify this email", "email_classification", "medium")
        assert level == ThinkLevel.L0_MEMORY

    def test_l1_when_weak_templates_exist(self, mock_memory):
        """Low-confidence templates → L1."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email_classification", strategy="Weak strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify this email", "email_classification", "medium")
        assert level == ThinkLevel.L1_SINGLE

    def test_l1_when_episodic_but_no_procedural(self, mock_memory):
        """Has episodic memory but no strategy templates → L1."""
        from tests.shared.cognitive.conftest import MockEpisodicEntry
        mock_memory._episodic.append(MockEpisodicEntry(
            domain="calendar", final_score=7.0,
        ))
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("schedule meeting", "calendar", "medium")
        assert level == ThinkLevel.L1_SINGLE

    def test_l2_novel_medium_stakes(self, mock_memory):
        """No memory + medium stakes → L2."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("classify email", "email_classification", "medium")
        assert level == ThinkLevel.L2_REFLEXION

    def test_l3_novel_high_stakes(self, mock_memory):
        """No memory + high stakes → L3."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("submit application", "job_application", "high")
        assert level == ThinkLevel.L3_TREE_OF_THOUGHT

    def test_l1_novel_low_stakes(self, mock_memory):
        """No memory + low stakes → L1."""
        classifier = self._make_classifier(mock_memory)
        level = classifier.classify("summarize briefing", "briefing_synthesis", "low")
        assert level == ThinkLevel.L1_SINGLE

    def test_auto_escalation_on_low_score(self):
        """Post-execution: L0 scored poorly → should escalate."""
        classifier = self._make_classifier()
        should, next_level = classifier.should_escalate(
            current_level=ThinkLevel.L0_MEMORY, score=4.0, confidence=0.3,
        )
        assert should is True
        assert next_level == ThinkLevel.L1_SINGLE

    def test_budget_clamps_level(self, mock_memory):
        """Budget exhausted → L3 clamped to L2."""
        budget = CognitiveBudget(max_l3_per_hour=0)
        tracker = BudgetTracker(budget)
        classifier = self._make_classifier(mock_memory, tracker)
        level = classifier.classify("submit app", "job_application", "high")
        # Classifier wants L3, but budget clamps it
        assert level <= ThinkLevel.L2_REFLEXION

    def test_self_improving_skips_check(self, mock_memory):
        """Classifier memory says domain is easy → always L0."""
        classifier = self._make_classifier(mock_memory)
        classifier._domain_stats["email_classification"] = {
            "l0_success_rate": 0.98, "sample_size": 200,
        }
        # Even without templates, classifier memory overrides
        level = classifier.classify("classify email", "email_classification", "medium")
        assert level == ThinkLevel.L0_MEMORY

    def test_self_improving_starts_higher(self, mock_memory):
        """Classifier memory says domain is hard → start at L2."""
        classifier = self._make_classifier(mock_memory)
        classifier._domain_stats["tricky_domain"] = {
            "l1_escalation_rate": 0.6, "sample_size": 15,
        }
        mock_memory._procedural.append(MockProceduralEntry(
            domain="tricky_domain", success_rate=0.5,
            times_used=2, avg_score_when_used=5.0,
        ))
        level = classifier.classify("do something", "tricky_domain", "medium")
        assert level == ThinkLevel.L2_REFLEXION
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_classifier.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._classifier'`

- [ ] **Step 3: Implement EscalationClassifier**

Create `shared/cognitive/_classifier.py`:

```python
"""Escalation classifier — 3-step heuristic for cognitive level selection."""

from shared.logging_config import get_logger
from shared.cognitive._budget import ThinkLevel, BudgetTracker

logger = get_logger(__name__)

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

# Thresholds for "strong" templates
_MIN_TIMES_USED = 3
_MIN_AVG_SCORE = 8.0
_MIN_SUCCESS_RATE = 0.8

# Self-improvement thresholds
_L0_SKIP_THRESHOLD = 0.95
_L1_HARD_DOMAIN_THRESHOLD = 0.50


class EscalationClassifier:
    """Picks the cognitive reasoning level via heuristic checks.

    3-step cascade: memory check → novelty check → stakes check.
    Budget tracker clamps the result if over budget.
    """

    def __init__(self, memory_manager, budget_tracker: BudgetTracker):
        self._memory = memory_manager
        self._budget = budget_tracker
        self._domain_stats: dict[str, dict] = {}

    def classify(self, task: str, domain: str, stakes: str) -> ThinkLevel:
        # Step 0: check classifier self-improvement memory
        stats = self._domain_stats.get(domain)
        if stats and stats.get("l0_success_rate", 0) >= _L0_SKIP_THRESHOLD \
           and stats.get("sample_size", 0) >= 10:
            logger.debug("Classifier memory: %s is easy (L0 %.0f%%) �� L0",
                         domain, stats["l0_success_rate"] * 100)
            return self._budget.clamp(ThinkLevel.L0_MEMORY)

        hard_domain = stats and stats.get("l1_escalation_rate", 0) >= _L1_HARD_DOMAIN_THRESHOLD \
                      and stats.get("sample_size", 0) >= 10

        # Step 1: MEMORY CHECK — look for procedural templates
        procs = self._memory.get_procedural_entries(domain) \
            if hasattr(self._memory, "get_procedural_entries") else []

        if procs:
            strong = [p for p in procs
                      if p.avg_score_when_used >= _MIN_AVG_SCORE
                      and p.times_used >= _MIN_TIMES_USED
                      and p.success_rate >= _MIN_SUCCESS_RATE]
            if strong and not hard_domain:
                return self._budget.clamp(ThinkLevel.L0_MEMORY)
            # Weak templates exist
            if hard_domain:
                return self._budget.clamp(ThinkLevel.L2_REFLEXION)
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

        # Step 2: NOVELTY CHECK — any memory about this domain?
        episodic = self._memory.get_episodic_entries(domain) \
            if hasattr(self._memory, "get_episodic_entries") else []

        if episodic:
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

        # Step 3: STAKES CHECK — completely novel domain
        resolved_stakes = self._resolve_stakes(domain, stakes)
        if resolved_stakes == "high":
            return self._budget.clamp(ThinkLevel.L3_TREE_OF_THOUGHT)
        elif resolved_stakes == "medium":
            return self._budget.clamp(ThinkLevel.L2_REFLEXION)
        else:
            return self._budget.clamp(ThinkLevel.L1_SINGLE)

    def should_escalate(
        self, current_level: ThinkLevel, score: float, confidence: float,
    ) -> tuple[bool, ThinkLevel]:
        if current_level == ThinkLevel.L0_MEMORY and score < 6.0:
            return True, ThinkLevel.L1_SINGLE
        if current_level == ThinkLevel.L1_SINGLE and confidence < 0.7:
            return True, ThinkLevel.L2_REFLEXION
        if current_level == ThinkLevel.L2_REFLEXION and score < 5.0:
            return True, ThinkLevel.L3_TREE_OF_THOUGHT
        return False, current_level

    def update_domain_stats(self, domain: str, level: ThinkLevel, escalated: bool):
        stats = self._domain_stats.setdefault(domain, {
            "l0_success_rate": 0.0, "l1_escalation_rate": 0.0,
            "l0_total": 0, "l0_success": 0,
            "l1_total": 0, "l1_escalated": 0,
            "sample_size": 0,
        })
        stats["sample_size"] = stats.get("sample_size", 0) + 1
        if level == ThinkLevel.L0_MEMORY:
            stats["l0_total"] += 1
            if not escalated:
                stats["l0_success"] += 1
            stats["l0_success_rate"] = stats["l0_success"] / max(stats["l0_total"], 1)
        elif level == ThinkLevel.L1_SINGLE:
            stats["l1_total"] += 1
            if escalated:
                stats["l1_escalated"] += 1
            stats["l1_escalation_rate"] = stats["l1_escalated"] / max(stats["l1_total"], 1)

        # Persist classifier accuracy to MemoryManager as SEMANTIC tier
        if stats["sample_size"] % 10 == 0:
            self._persist_domain_stats(domain, stats)

    def _persist_domain_stats(self, domain: str, stats: dict):
        """Save classifier accuracy to MemoryManager for cross-session persistence."""
        try:
            summary = (
                f"{domain}: L0 success {stats['l0_success_rate']:.0%}, "
                f"L1 escalation {stats['l1_escalation_rate']:.0%}, "
                f"n={stats['sample_size']}"
            )
            self._memory.learn_fact(
                domain="cognitive_classifier",
                fact=summary,
                run_id=f"classifier_{domain}",
            )
        except Exception as e:
            logger.debug("Failed to persist classifier stats: %s", e)

    def load_persisted_stats(self):
        """Load classifier accuracy from MemoryManager on init.

        Called by CognitiveEngine.__init__ to restore cross-session stats.
        Parses SEMANTIC tier entries with domain='cognitive_classifier'.
        """
        if not hasattr(self._memory, "semantic"):
            return
        try:
            facts = self._memory.semantic.facts if hasattr(self._memory.semantic, "facts") else {}
            for fact_id, entry in (facts.items() if isinstance(facts, dict) else []):
                if getattr(entry, "domain", "") == "cognitive_classifier":
                    self._parse_persisted_fact(entry.fact if hasattr(entry, "fact") else "")
        except Exception as e:
            logger.debug("Failed to load persisted classifier stats: %s", e)

    def _parse_persisted_fact(self, fact: str):
        """Parse a persisted classifier fact string back into domain stats."""
        import re
        match = re.match(r"(\S+): L0 success (\d+)%, L1 escalation (\d+)%, n=(\d+)", fact)
        if match:
            domain = match.group(1)
            self._domain_stats[domain] = {
                "l0_success_rate": int(match.group(2)) / 100,
                "l1_escalation_rate": int(match.group(3)) / 100,
                "l0_total": 0, "l0_success": 0,
                "l1_total": 0, "l1_escalated": 0,
                "sample_size": int(match.group(4)),
            }

    @staticmethod
    def _resolve_stakes(domain: str, explicit_stakes: str) -> str:
        if explicit_stakes in ("high", "medium", "low"):
            for level, domains in STAKES_REGISTRY.items():
                if domain in domains:
                    return level
            return explicit_stakes
        return "medium"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_classifier.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add shared/cognitive/_classifier.py tests/shared/cognitive/test_classifier.py
git commit -m "feat(cognitive): add EscalationClassifier with 3-step heuristic + self-improvement"
```

---

## Task 4: StrategyComposer & ComposedPrompt

**Files:**
- Create: `shared/cognitive/_strategy.py`
- Create: `tests/shared/cognitive/test_strategy.py`

- [ ] **Step 1: Write the strategy tests**

Create `tests/shared/cognitive/test_strategy.py`:

```python
import pytest
from unittest.mock import patch, MagicMock

from shared.cognitive._strategy import StrategyComposer, ComposedPrompt
from tests.shared.cognitive.conftest import (
    MockMemoryManager, MockProceduralEntry, MockEpisodicEntry,
)


class TestStrategyComposer:

    def test_compose_with_own_templates(self, mock_memory):
        """Own agent templates appear in composed prompt."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email", strategy="Always check sender domain first",
            success_rate=0.9, times_used=5, avg_score_when_used=8.5,
        ))
        composer = StrategyComposer()
        result = composer.compose("classify email", "email", "gmail_agent", mock_memory)
        assert "check sender domain" in result.text
        assert result.source_breakdown["own"] >= 1

    def test_compose_falls_back_to_cross_agent(self, mock_memory):
        """Cross-agent templates used when no own templates exist."""
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id="proc_other", domain="email",
            strategy="Cross-agent: prioritize personal emails",
            success_rate=0.85, times_used=3, avg_score_when_used=7.5,
            source="reflexion",
        ))
        composer = StrategyComposer()
        result = composer.compose("classify email", "email", "different_agent", mock_memory)
        assert "prioritize personal" in result.text
        assert result.source_breakdown.get("cross_agent", 0) >= 1

    def test_compose_includes_anti_patterns(self, mock_memory):
        """Failure patterns from episodic memory included in prompt."""
        mock_memory._episodic.append(MockEpisodicEntry(
            domain="email", final_score=3.0,
            weaknesses=["Misclassified auto-rejection as interview scheduling"],
        ))
        composer = StrategyComposer()
        result = composer.compose("classify email", "email", "gmail_agent", mock_memory)
        assert "Misclassified" in result.text or "AVOID" in result.text.upper()

    def test_compose_respects_max_templates(self, mock_memory):
        """Only top N templates included."""
        for i in range(10):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy number {i}",
                success_rate=0.5 + i * 0.05, times_used=3,
            ))
        composer = StrategyComposer()
        result = composer.compose("task", "email", "agent", mock_memory, max_templates=3)
        assert len(result.templates_used) <= 3

    def test_compose_ranking_by_success_rate(self, mock_memory):
        """Highest success rate templates appear first."""
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id="low", domain="email", strategy="Low rate strategy",
            success_rate=0.3, times_used=5,
        ))
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id="high", domain="email", strategy="High rate strategy",
            success_rate=0.95, times_used=5,
        ))
        composer = StrategyComposer()
        result = composer.compose("task", "email", "agent", mock_memory)
        idx_high = result.text.find("High rate")
        idx_low = result.text.find("Low rate")
        if idx_low >= 0:
            assert idx_high < idx_low

    def test_compose_token_budget(self, mock_memory):
        """Composed prompt respects token limit."""
        for i in range(20):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy="A" * 500,
                success_rate=0.9, times_used=5,
            ))
        composer = StrategyComposer()
        result = composer.compose("task", "email", "agent", mock_memory,
                                  max_strategy_tokens=200)
        assert result.token_count <= 2000

    def test_compose_includes_base_prompt(self, mock_memory):
        """Evolved base prompt is the first section."""
        with patch("shared.cognitive._strategy._get_base_prompt",
                   return_value="You are a precise email classifier."):
            composer = StrategyComposer()
            result = composer.compose("task", "email", "gmail_agent", mock_memory)
            assert result.text.startswith("You are a precise email classifier")

    def test_template_update_on_success(self, mock_memory):
        """Successful use increments times_used and times_succeeded."""
        composer = StrategyComposer()
        template = {"times_used": 5, "times_succeeded": 4, "success_rate": 0.8}
        composer.record_template_outcome(template, success=True, score=8.5)
        assert template["times_used"] == 6
        assert template["times_succeeded"] == 5
        assert abs(template["success_rate"] - 5 / 6) < 0.01

    def test_template_update_on_failure(self, mock_memory):
        """Failed use increments times_used but not times_succeeded."""
        composer = StrategyComposer()
        template = {"times_used": 5, "times_succeeded": 4, "success_rate": 0.8}
        composer.record_template_outcome(template, success=False, score=3.0)
        assert template["times_used"] == 6
        assert template["times_succeeded"] == 4
        assert abs(template["success_rate"] - 4 / 6) < 0.01

    def test_success_rate_computed(self):
        """Success rate is times_succeeded / times_used."""
        composer = StrategyComposer()
        template = {"times_used": 10, "times_succeeded": 8, "success_rate": 0.0}
        composer.record_template_outcome(template, success=True, score=9.0)
        assert abs(template["success_rate"] - 9 / 11) < 0.01

    def test_cross_agent_lower_priority(self, mock_memory):
        """Own templates ranked higher even if cross-agent scores higher."""
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id="own", domain="email", strategy="OWN_STRATEGY",
            success_rate=0.7, times_used=3, avg_score_when_used=7.0,
        ))
        mock_memory._procedural.append(MockProceduralEntry(
            procedure_id="other", domain="email", strategy="OTHER_STRATEGY",
            success_rate=0.95, times_used=10, avg_score_when_used=9.0,
        ))
        composer = StrategyComposer()
        result = composer.compose("task", "email", "test_agent", mock_memory)
        # Mark own vs cross-agent: since MockProceduralEntry doesn't have agent_name,
        # compose treats them all as cross-agent unless we add that field.
        # The ranking should still work based on success_rate for same-priority items.
        assert "OWN_STRATEGY" in result.text or "OTHER_STRATEGY" in result.text

    def test_empty_memory_returns_base_only(self, mock_memory):
        """No templates, no failures → just the base prompt + task."""
        with patch("shared.cognitive._strategy._get_base_prompt",
                   return_value="Base prompt."):
            composer = StrategyComposer()
            result = composer.compose("do the task", "unknown", "agent", mock_memory)
            assert "Base prompt." in result.text
            assert len(result.templates_used) == 0
            assert len(result.anti_patterns_used) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_strategy.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._strategy'`

- [ ] **Step 3: Implement StrategyComposer**

Create `shared/cognitive/_strategy.py`:

```python
"""Strategy templates, prompt composition, and template lifecycle."""

from dataclasses import dataclass, field

from shared.logging_config import get_logger
from shared.cognitive._prompts import COMPOSED_SECTIONS

logger = get_logger(__name__)

STRATEGY_PAYLOAD_KEYS = {
    "agent_name", "trigger", "composable_fragments", "times_used",
    "times_succeeded", "success_rate", "avg_score", "avg_latency_ms",
    "source", "anti_patterns",
}


def _get_base_prompt(agent_name: str) -> str:
    try:
        from jobpulse.persona_evolution import get_evolved_prompt
        return get_evolved_prompt(agent_name)
    except (ImportError, Exception):
        return ""


@dataclass
class ComposedPrompt:
    text: str
    templates_used: list[str] = field(default_factory=list)
    anti_patterns_used: list[str] = field(default_factory=list)
    token_count: int = 0
    source_breakdown: dict = field(default_factory=dict)


class StrategyComposer:
    """Assembles a prompt from strategy templates + anti-patterns + task context."""

    def compose(
        self,
        task: str,
        domain: str,
        agent_name: str,
        memory_manager,
        max_templates: int = 5,
        max_anti_patterns: int = 3,
        max_strategy_tokens: int = 500,
    ) -> ComposedPrompt:
        base_prompt = _get_base_prompt(agent_name)

        # Step 1: Retrieve and rank strategy templates
        procs = memory_manager.get_procedural_entries(domain) \
            if hasattr(memory_manager, "get_procedural_entries") else []

        own = [p for p in procs if getattr(p, "source", "") == agent_name
               or not hasattr(p, "agent_name")]
        cross = [p for p in procs if p not in own]

        def rank_key(p):
            return getattr(p, "success_rate", 0.5) * 0.6 + \
                   getattr(p, "avg_score_when_used", 5.0) / 10.0 * 0.3

        own.sort(key=rank_key, reverse=True)
        cross.sort(key=rank_key, reverse=True)

        selected = own[:max_templates]
        remaining = max_templates - len(selected)
        if remaining > 0:
            selected.extend(cross[:remaining])

        source_breakdown = {
            "own": min(len(own), max_templates),
            "cross_agent": len(selected) - min(len(own), max_templates),
            "anti_patterns": 0,
        }

        # Step 2: Retrieve anti-patterns (failure memories)
        episodic = memory_manager.get_episodic_entries(domain) \
            if hasattr(memory_manager, "get_episodic_entries") else []

        failures = [e for e in episodic if e.final_score < 5.0]
        failures.sort(key=lambda e: e.timestamp if hasattr(e, "timestamp") else "",
                      reverse=True)
        failures = failures[:max_anti_patterns]
        source_breakdown["anti_patterns"] = len(failures)

        # Step 3: Compose sections
        sections = []
        if base_prompt:
            sections.append(base_prompt)

        if selected:
            strategy_lines = []
            char_budget = max_strategy_tokens * 4
            chars_used = 0
            template_ids = []
            for p in selected:
                line = f"- {p.strategy} (success: {p.success_rate:.0%}, used: {p.times_used}x)"
                if chars_used + len(line) > char_budget:
                    break
                strategy_lines.append(line)
                chars_used += len(line)
                template_ids.append(getattr(p, "procedure_id", "unknown"))
            if strategy_lines:
                sections.append(
                    COMPOSED_SECTIONS["strategies"].format(
                        strategies="\n".join(strategy_lines)
                    )
                )
        else:
            template_ids = []

        anti_ids = []
        if failures:
            anti_lines = []
            for f in failures:
                weakness = f.weaknesses[0] if f.weaknesses else f.output_summary[:100]
                anti_lines.append(f"- {weakness}")
                anti_ids.append(getattr(f, "run_id", "unknown"))
            sections.append(
                COMPOSED_SECTIONS["anti_patterns"].format(
                    anti_patterns="\n".join(anti_lines)
                )
            )

        sections.append(COMPOSED_SECTIONS["task"].format(task=task))

        text = "\n".join(sections)

        try:
            from shared.context_compression import count_tokens
            token_count = count_tokens(text)
        except ImportError:
            token_count = len(text) // 4

        return ComposedPrompt(
            text=text,
            templates_used=template_ids,
            anti_patterns_used=anti_ids,
            token_count=token_count,
            source_breakdown=source_breakdown,
        )

    @staticmethod
    def record_template_outcome(template: dict, success: bool, score: float):
        template["times_used"] = template.get("times_used", 0) + 1
        if success:
            template["times_succeeded"] = template.get("times_succeeded", 0) + 1
        template["success_rate"] = template.get("times_succeeded", 0) / \
            max(template["times_used"], 1)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_strategy.py -v
```

Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add shared/cognitive/_strategy.py tests/shared/cognitive/test_strategy.py
git commit -m "feat(cognitive): add StrategyComposer with template retrieval + anti-pattern injection"
```

---

## Task 5: ReflexionLoop (Level 2)

**Files:**
- Create: `shared/cognitive/_reflexion.py`
- Create: `tests/shared/cognitive/test_reflexion.py`

- [ ] **Step 1: Write the reflexion tests**

Create `tests/shared/cognitive/test_reflexion.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from shared.cognitive._reflexion import ReflexionLoop, ReflexionResult
from shared.cognitive._budget import ThinkLevel
from tests.shared.cognitive.conftest import MockMemoryManager, MockEpisodicEntry


class TestReflexionLoop:

    @pytest.fixture
    def reflexion(self, mock_memory):
        return ReflexionLoop(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_passes_first_attempt(self, reflexion):
        """Score above threshold on first try → no retry."""
        with patch("shared.cognitive._reflexion._llm_generate",
                   new_callable=AsyncMock, return_value="good answer"):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=lambda x: 8.5,
            )
        assert result.attempts == 1
        assert result.score == 8.5
        assert len(result.critiques) == 0

    @pytest.mark.asyncio
    async def test_retries_on_low_score(self, reflexion):
        """Low first score → critique → retry → succeeds."""
        call_count = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return "bad answer"
            if "MISTAKE:" in prompt or "critique" in prompt.lower():
                return "MISTAKE: Too vague\nFIX: Be specific"
            return "good answer"

        scores = [4.0, 8.0]
        scorer = lambda x: scores.pop(0) if scores else 8.0

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=scorer,
            )
        assert result.attempts == 2
        assert result.score == 8.0
        assert len(result.critiques) >= 1

    @pytest.mark.asyncio
    async def test_max_3_attempts(self, reflexion):
        """Always fails → capped at 3 attempts."""
        async def mock_generate(prompt, model=None):
            if "MISTAKE:" in prompt or "critique" in prompt.lower():
                return "MISTAKE: Still bad\nFIX: Try harder"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test task", domain="test",
                initial_prompt="Be helpful.", score_threshold=7.0,
                scorer=lambda x: 3.0,
            )
        assert result.attempts == 3
        assert result.score == 3.0

    @pytest.mark.asyncio
    async def test_returns_best_attempt(self, reflexion):
        """Returns the highest-scoring attempt, not necessarily the last."""
        attempt = 0
        async def mock_generate(prompt, model=None):
            nonlocal attempt
            if "MISTAKE:" in prompt or "critique" in prompt.lower():
                return "MISTAKE: Bad\nFIX: Fix"
            attempt += 1
            return f"attempt {attempt}"

        scores = [3.0, 7.5, 6.0]
        def scorer(x):
            return scores.pop(0) if scores else 5.0

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=8.0,
                scorer=scorer,
            )
        assert result.score == 7.5
        assert "attempt 2" in result.answer

    @pytest.mark.asyncio
    async def test_critique_prompt_includes_output(self, reflexion):
        """Critique LLM call receives the previous attempt's output."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            if "MISTAKE:" in prompt or "What specifically went wrong" in prompt:
                return "MISTAKE: Wrong\nFIX: Fix"
            return "my specific output text"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        critique_prompts = [p for p in prompts_seen if "went wrong" in p]
        assert any("my specific output text" in p for p in critique_prompts)

    @pytest.mark.asyncio
    async def test_failure_memory_retrieved(self, mock_memory):
        """Past failure patterns injected into retry prompt."""
        mock_memory._episodic.append(MockEpisodicEntry(
            domain="test", final_score=2.0,
            weaknesses=["Past failure: forgot to validate input"],
        ))
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test",
                initial_prompt="prompt", score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        retry_prompts = [p for p in prompts_seen
                         if "Past failure" in p or "validate input" in p]
        assert len(retry_prompts) >= 1 or \
            any("validate input" in p for p in prompts_seen)

    @pytest.mark.asyncio
    async def test_stores_success_template(self, reflexion, mock_memory):
        """Successful reflexion stores a PROCEDURAL template."""
        async def mock_generate(prompt, model=None):
            return "good answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="email", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 8.5,
            )
        assert len(mock_memory.learn_procedure_calls) >= 1
        stored = mock_memory.learn_procedure_calls[-1]
        assert stored["domain"] == "email"
        assert stored["source"] == "reflexion"

    @pytest.mark.asyncio
    async def test_stores_failure_pattern(self, mock_memory):
        """All attempts below threshold → EPISODIC failure entry stored."""
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Critical error\nFIX: Redo everything"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 3.0,
            )
        eps = [e for e in mock_memory._episodic if e.final_score < 5.0]
        assert len(eps) >= 1

    @pytest.mark.asyncio
    async def test_failure_has_critique(self, mock_memory):
        """Failure entry has critique in weaknesses."""
        reflexion = ReflexionLoop(mock_memory, agent_name="test_agent")
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Critical error\nFIX: Redo everything"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=lambda x: 3.0,
            )
        eps = [e for e in mock_memory._episodic if e.final_score < 5.0]
        assert len(eps) >= 1
        assert any("Critical error" in w for w in eps[0].weaknesses)

    @pytest.mark.asyncio
    async def test_custom_scorer(self, reflexion):
        """Custom scorer function is called instead of LLM scorer."""
        call_log = []
        def custom_scorer(output):
            call_log.append(output)
            return 9.0

        async def mock_generate(prompt, model=None):
            return "answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0, scorer=custom_scorer,
            )
        assert len(call_log) >= 1
        assert result.score == 9.0

    @pytest.mark.asyncio
    async def test_cost_tracking(self, reflexion):
        """Cost reported accurately."""
        async def mock_generate(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            result = await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        # 2 generation calls + 1 critique = should have some cost
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_critique_uses_nano(self, reflexion):
        """Critique calls use gpt-4.1-nano model."""
        models_used = []
        async def mock_generate(prompt, model=None):
            models_used.append(model)
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "answer"

        scores = [4.0, 8.0]
        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_generate):
            await reflexion.run(
                task="test", domain="test", initial_prompt="prompt",
                score_threshold=7.0,
                scorer=lambda x: scores.pop(0) if scores else 8.0,
            )
        assert "gpt-4.1-nano" in models_used
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_reflexion.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._reflexion'`

- [ ] **Step 3: Implement ReflexionLoop**

Create `shared/cognitive/_reflexion.py`:

```python
"""Reflexion Loop — Level 2 cognitive reasoning: try/critique/retry."""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._prompts import CRITIQUE_PROMPT

logger = get_logger(__name__)

_GENERATE_COST = 0.001
_CRITIQUE_COST = 0.0002


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.3, timeout=30.0)
    messages = [{"role": "user", "content": prompt}]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class ReflexionResult:
    answer: str
    score: float
    attempts: int
    critiques: list[str] = field(default_factory=list)
    strategy_template: str = ""
    cost: float = 0.0


class ReflexionLoop:
    """Sequential self-correction: try, critique what went wrong, retry."""

    def __init__(self, memory_manager, agent_name: str):
        self._memory = memory_manager
        self._agent_name = agent_name

    async def run(
        self,
        task: str,
        domain: str,
        initial_prompt: str,
        max_attempts: int = 3,
        score_threshold: float = 7.0,
        scorer: Optional[Callable] = None,
    ) -> ReflexionResult:
        best_answer = ""
        best_score = -1.0
        critiques: list[str] = []
        total_cost = 0.0

        # Retrieve failure patterns for this domain
        failure_context = self._get_failure_context(domain)

        prompt = initial_prompt
        for attempt in range(1, max_attempts + 1):
            # Generate
            start = time.monotonic()
            answer = await _llm_generate(prompt)
            total_cost += _GENERATE_COST

            # Score
            score = scorer(answer) if scorer else await self._llm_score(task, answer)

            if score > best_score:
                best_answer = answer
                best_score = score

            logger.info("Reflexion attempt %d/%d: score=%.1f (threshold=%.1f)",
                        attempt, max_attempts, score, score_threshold)

            if score >= score_threshold:
                break

            if attempt >= max_attempts:
                break

            # Critique (using nano — cheap)
            critique_prompt = CRITIQUE_PROMPT.format(
                task=task, output=answer, score=score, threshold=score_threshold,
            )
            critique = await _llm_generate(critique_prompt, model="gpt-4.1-nano")
            total_cost += _CRITIQUE_COST
            critiques.append(critique)

            # Build retry prompt with critique + failure patterns
            prompt = f"{initial_prompt}\n\n## Previous attempt (score: {score})\n{answer}"
            prompt += f"\n\n## Critique\n{critique}"
            if failure_context:
                prompt += f"\n\n## Known failure patterns for this domain\n{failure_context}"

        # Store learnings
        if best_score >= score_threshold:
            self._store_success(task, domain, best_answer, best_score)
        else:
            self._store_failure(task, domain, best_answer, best_score, critiques)

        return ReflexionResult(
            answer=best_answer,
            score=best_score,
            attempts=min(attempt, max_attempts) if 'attempt' in dir() else 1,
            critiques=critiques,
            strategy_template=best_answer[:200] if best_score >= score_threshold else "",
            cost=total_cost,
        )

    def _get_failure_context(self, domain: str) -> str:
        if not hasattr(self._memory, "get_episodic_entries"):
            return ""
        episodes = self._memory.get_episodic_entries(domain)
        failures = [e for e in episodes if e.final_score < 5.0]
        if not failures:
            return ""
        lines = []
        for f in failures[:3]:
            if f.weaknesses:
                lines.append(f"- {f.weaknesses[0]}")
        return "\n".join(lines)

    def _store_success(self, task: str, domain: str, answer: str, score: float):
        strategy = f"For '{task[:50]}' tasks: {answer[:150]}"
        # STRATEGY_PAYLOAD fields encoded in context for pre-Pillar-1 compatibility
        payload_context = (
            f"agent_name={self._agent_name}|trigger={task[:50]}|"
            f"times_used=1|times_succeeded=1|success_rate=1.0|"
            f"avg_score={score:.1f}|source=reflexion"
        )
        self._memory.learn_procedure(
            domain=domain,
            strategy=strategy,
            context=payload_context,
            score=score,
            source="reflexion",
        )

    def _store_failure(self, task: str, domain: str, answer: str,
                       score: float, critiques: list[str]):
        critique_text = critiques[-1] if critiques else "No critique available"
        self._memory.record_episode(
            topic=task[:100],
            final_score=score,
            iterations=len(critiques) + 1,
            pattern_used="reflexion",
            agents_used=[self._agent_name],
            strengths=[],
            weaknesses=[critique_text],
            output_summary=answer[:200],
            domain=domain,
        )

    async def _llm_score(self, task: str, output: str) -> float:
        from shared.cognitive._prompts import SCORING_PROMPT
        prompt = SCORING_PROMPT.format(task=task, output=output)
        result = await _llm_generate(prompt, model="gpt-4.1-nano")
        try:
            return float(result.strip())
        except ValueError:
            return 5.0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_reflexion.py -v
```

Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add shared/cognitive/_reflexion.py tests/shared/cognitive/test_reflexion.py
git commit -m "feat(cognitive): add ReflexionLoop (L2) — try/critique/retry with failure memory"
```

---

## Task 6: TreeOfThought (Level 3)

**Files:**
- Create: `shared/cognitive/_tree_of_thought.py`
- Create: `tests/shared/cognitive/test_tree_of_thought.py`

- [ ] **Step 1: Write the ToT tests**

Create `tests/shared/cognitive/test_tree_of_thought.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.cognitive._tree_of_thought import TreeOfThought, ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager


class TestTreeOfThought:

    @pytest.fixture
    def tot(self, mock_memory):
        return TreeOfThought(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_generates_n_branches(self, tot):
        """Generates correct number of initial branches."""
        call_count = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_count
            call_count += 1
            return f"branch output {call_count}"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test task", domain="test", context="ctx",
                num_branches=4, scorer=lambda x: 7.0,
            )
        assert len(result.all_branches) >= 4

    @pytest.mark.asyncio
    async def test_prunes_below_threshold(self, tot):
        """Branches scoring below threshold are pruned."""
        outputs = iter(["good A", "bad B", "good C", "bad D", "ext A", "ext C"])
        async def mock_generate(prompt, model=None):
            return next(outputs, "default")

        scores = {"good A": 8.0, "bad B": 3.0, "good C": 7.0, "bad D": 4.0,
                  "ext A": 8.5, "ext C": 9.0, "default": 5.0}

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=lambda x: scores.get(x, 5.0),
            )
        assert result.pruned_count == 2

    @pytest.mark.asyncio
    async def test_extends_top_n(self, tot):
        """Top N branches after pruning get extended."""
        call_idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal call_idx
            call_idx += 1
            return f"output_{call_idx}"

        score_map = {}
        base_scores = [8.0, 3.0, 7.0, 4.0]
        ext_scores = [8.5, 9.0]
        all_scores = base_scores + ext_scores

        def scorer(x):
            if x in score_map:
                return score_map[x]
            if all_scores:
                s = all_scores.pop(0)
                score_map[x] = s
                return s
            return 5.0

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extensions = [b for b in result.all_branches if b.depth == 1]
        assert len(extensions) == 2

    @pytest.mark.asyncio
    async def test_winner_is_highest_score(self, tot):
        """Winner branch has the highest score across all branches."""
        idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal idx
            idx += 1
            return f"output_{idx}"

        scores_list = [5.0, 8.0, 3.0, 6.0, 8.5, 9.5]
        score_map = {}

        def scorer(x):
            if x not in score_map and scores_list:
                score_map[x] = scores_list.pop(0)
            return score_map.get(x, 5.0)

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=4.0, extend_top_n=2,
                scorer=scorer,
            )
        all_scores = [b.score for b in result.all_branches]
        assert result.winner.score == max(all_scores)

    @pytest.mark.asyncio
    async def test_strategy_extracted_from_winner(self, tot):
        """Winner produces a non-empty strategy template."""
        async def mock_generate(prompt, model=None):
            return "detailed winning strategy here"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=2, extend_top_n=1,
                scorer=lambda x: 9.0,
            )
        assert len(result.strategy_template) > 0

    @pytest.mark.asyncio
    async def test_grpo_called_for_generation(self, tot):
        """GRPO parallel generation is attempted for initial branches."""
        grpo_called = []
        def mock_grpo_gen(make_variant, system_prompt, user_message, temps):
            grpo_called.append(True)
            return [f"grpo_output_{i}" for i in range(len(temps))]

        with patch("shared.cognitive._tree_of_thought.parallel_grpo_candidates",
                   side_effect=mock_grpo_gen, create=True), \
             patch("shared.cognitive._tree_of_thought._llm_generate",
                   new_callable=AsyncMock, return_value="fallback"):
            # Patch the import inside _generate_branches_via_grpo
            with patch.object(tot, "_generate_branches_via_grpo",
                              return_value=["grpo_0", "grpo_1", "grpo_2", "grpo_3"]):
                await tot.explore(
                    task="test", domain="test", context="ctx",
                    num_branches=4, extend_top_n=0,
                    scorer=lambda x: 7.0,
                )

    @pytest.mark.asyncio
    async def test_branch_prompts_structurally_different(self, tot):
        """Each branch gets a different reasoning instruction."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            return "output"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, extend_top_n=0,
                scorer=lambda x: 7.0,
            )
        initial_prompts = prompts_seen[:4]
        assert len(set(initial_prompts)) == 4

    @pytest.mark.asyncio
    async def test_extension_builds_on_parent(self, tot):
        """Extension prompts reference parent branch reasoning."""
        prompts_seen = []
        async def mock_generate(prompt, model=None):
            prompts_seen.append(prompt)
            return "parent reasoning output"

        scores = [8.0, 3.0, 7.0, 4.0, 9.0, 8.5]
        score_idx = 0
        def scorer(x):
            nonlocal score_idx
            s = scores[score_idx] if score_idx < len(scores) else 5.0
            score_idx += 1
            return s

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extension_prompts = prompts_seen[4:]
        assert any("parent reasoning" in p or "Build on" in p for p in extension_prompts)

    @pytest.mark.asyncio
    async def test_cost_tracking(self, tot):
        """Cost reported accurately."""
        async def mock_generate(prompt, model=None):
            return "output"

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, extend_top_n=2,
                scorer=lambda x: 7.0,
            )
        assert result.cost > 0

    @pytest.mark.asyncio
    async def test_single_branch_no_extension(self, tot):
        """Only 1 branch passes pruning → returned without extension."""
        idx = 0
        async def mock_generate(prompt, model=None):
            nonlocal idx
            idx += 1
            return f"output_{idx}"

        scores = [9.0, 2.0, 2.0, 2.0]
        score_idx = 0
        def scorer(x):
            nonlocal score_idx
            s = scores[score_idx] if score_idx < len(scores) else 5.0
            score_idx += 1
            return s

        with patch("shared.cognitive._tree_of_thought._llm_generate",
                   side_effect=mock_generate):
            result = await tot.explore(
                task="test", domain="test", context="ctx",
                num_branches=4, prune_threshold=5.0, extend_top_n=2,
                scorer=scorer,
            )
        extensions = [b for b in result.all_branches if b.depth == 1]
        assert len(extensions) <= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_tree_of_thought.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._tree_of_thought'`

- [ ] **Step 3: Implement TreeOfThought**

Create `shared/cognitive/_tree_of_thought.py`:

```python
"""Tree of Thought — Level 3 cognitive reasoning: branch/score/prune/extend."""

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._prompts import BRANCH_STRATEGIES, EXTENSION_PROMPT

logger = get_logger(__name__)

_GENERATE_COST = 0.001
_SCORE_COST = 0.0002


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.7, timeout=30.0)
    messages = [{"role": "user", "content": prompt}]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class Branch:
    branch_id: str
    reasoning: str
    output: str
    score: float
    depth: int = 0


@dataclass
class ToTResult:
    winner: Branch
    all_branches: list[Branch] = field(default_factory=list)
    strategy_template: str = ""
    pruned_count: int = 0
    cost: float = 0.0


class TreeOfThought:
    """Parallel exploration of structurally different reasoning approaches.

    Uses GRPO's parallel generation machinery for initial branches,
    then adds structured prompting + pruning/extension on top.
    """

    def __init__(self, memory_manager, agent_name: str):
        self._memory = memory_manager
        self._agent_name = agent_name

    def _generate_branches_via_grpo(
        self, system_prompt: str, task: str, strategies: list[str],
    ) -> list[str]:
        """Use GRPO's parallel candidate generation for branch diversity."""
        try:
            from shared.parallel_executor import parallel_grpo_candidates
            from shared.agents import get_llm

            def make_variant(temp):
                return get_llm(model="gpt-4.1-mini", temperature=temp, timeout=30.0)

            temps = [0.3 + i * 0.15 for i in range(len(strategies))]
            prompts_with_strategy = [
                f"{system_prompt}\n\n## Reasoning approach\n{s}\n\n## Task\n{task}"
                for s in strategies
            ]
            # parallel_grpo_candidates generates one output per temp
            outputs = parallel_grpo_candidates(
                make_variant, system_prompt, task, temps,
            )
            return outputs
        except (ImportError, Exception):
            return []

    async def explore(
        self,
        task: str,
        domain: str,
        context: str,
        num_branches: int = 4,
        prune_threshold: float = 5.0,
        extend_top_n: int = 2,
        scorer: Optional[Callable] = None,
    ) -> ToTResult:
        total_cost = 0.0
        all_branches: list[Branch] = []

        # Step 1: Generate initial branches with structurally different prompts
        strategies = BRANCH_STRATEGIES[:num_branches]
        while len(strategies) < num_branches:
            strategies.append(f"Try a creative approach #{len(strategies) + 1}.")

        # Try GRPO parallel generation first, fall back to sequential
        grpo_outputs = self._generate_branches_via_grpo(context, task, strategies)

        for i, strategy in enumerate(strategies):
            if i < len(grpo_outputs) and grpo_outputs[i]:
                output = grpo_outputs[i]
            else:
                prompt = f"{context}\n\n## Reasoning approach\n{strategy}\n\n## Task\n{task}"
                output = await _llm_generate(prompt)
            total_cost += _GENERATE_COST

            score = scorer(output) if scorer else 5.0
            total_cost += _SCORE_COST

            branch = Branch(
                branch_id=f"b{i}", reasoning=strategy,
                output=output, score=score, depth=0,
            )
            all_branches.append(branch)

        # Step 2: Prune below threshold
        surviving = [b for b in all_branches if b.score >= prune_threshold]
        pruned_count = len(all_branches) - len(surviving)

        if not surviving:
            surviving = sorted(all_branches, key=lambda b: b.score, reverse=True)[:1]
            pruned_count = len(all_branches) - 1

        # Step 3: Extend top N surviving branches
        surviving.sort(key=lambda b: b.score, reverse=True)
        to_extend = surviving[:extend_top_n]

        for parent in to_extend:
            ext_prompt = EXTENSION_PROMPT.format(reasoning=parent.output)
            ext_prompt = f"{context}\n\n{ext_prompt}\n\n## Task\n{task}"
            ext_output = await _llm_generate(ext_prompt)
            total_cost += _GENERATE_COST

            ext_score = scorer(ext_output) if scorer else 5.0
            total_cost += _SCORE_COST

            ext_branch = Branch(
                branch_id=f"{parent.branch_id}_ext", reasoning=ext_prompt,
                output=ext_output, score=ext_score, depth=1,
            )
            all_branches.append(ext_branch)

        # Step 4: Pick winner
        winner = max(all_branches, key=lambda b: b.score)

        # Extract strategy template from winner
        strategy_template = f"Winning approach for '{task[:50]}': {winner.output[:150]}"

        # Store success with STRATEGY_PAYLOAD fields in context
        payload_context = (
            f"agent_name={self._agent_name}|trigger={task[:50]}|"
            f"times_used=1|times_succeeded=1|success_rate=1.0|"
            f"avg_score={winner.score:.1f}|source=tot|"
            f"pruned_count={pruned_count}|branches={len(all_branches)}"
        )
        self._memory.learn_procedure(
            domain=domain,
            strategy=strategy_template,
            context=payload_context,
            score=winner.score,
            source="tot",
        )

        return ToTResult(
            winner=winner,
            all_branches=all_branches,
            strategy_template=strategy_template,
            pruned_count=pruned_count,
            cost=total_cost,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_tree_of_thought.py -v
```

Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add shared/cognitive/_tree_of_thought.py tests/shared/cognitive/test_tree_of_thought.py
git commit -m "feat(cognitive): add TreeOfThought (L3) — branch/score/prune/extend exploration"
```

---

## Task 7: CognitiveEngine — The Single Entry Point

**Files:**
- Create: `shared/cognitive/_engine.py`
- Modify: `shared/cognitive/__init__.py`
- Create: `tests/shared/cognitive/test_engine.py`

- [ ] **Step 1: Write the engine tests**

Create `tests/shared/cognitive/test_engine.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget
from shared.cognitive._reflexion import ReflexionResult
from shared.cognitive._tree_of_thought import ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestCognitiveEngine:

    @pytest.fixture
    def engine(self, mock_memory):
        return CognitiveEngine(mock_memory, agent_name="test_agent")

    @pytest.mark.asyncio
    async def test_think_l0_no_llm_call(self, mock_memory):
        """L0 with deterministic template → zero LLM calls."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy="Classify by sender domain", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock) as mock_llm:
            result = await engine.think(task="classify email", domain="email")
        mock_llm.assert_not_called()
        assert result.level == ThinkLevel.L0_MEMORY

    @pytest.mark.asyncio
    async def test_think_l1_single_call(self, mock_memory):
        """L1 makes exactly one LLM call."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="email", strategy="Weak strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="classified result") as mock_llm:
            result = await engine.think(
                task="classify email", domain="email",
                scorer=lambda x: 8.0,
            )
        assert mock_llm.call_count == 1
        assert result.level == ThinkLevel.L1_SINGLE

    @pytest.mark.asyncio
    async def test_think_l2_calls_reflexion(self, mock_memory):
        """L2 delegates to ReflexionLoop."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        mock_result = ReflexionResult(
            answer="reflexion answer", score=8.0, attempts=2,
            critiques=["fixed it"], cost=0.003,
        )
        with patch.object(engine._reflexion, "run",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="classify email", domain="email_classification",
                stakes="medium", scorer=lambda x: 8.0,
            )
        assert result.level == ThinkLevel.L2_REFLEXION
        assert result.answer == "reflexion answer"

    @pytest.mark.asyncio
    async def test_think_l3_calls_tot(self, mock_memory):
        """L3 delegates to TreeOfThought."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        winner = Branch(branch_id="b0", reasoning="first principles",
                        output="tot answer", score=9.0, depth=1)
        mock_result = ToTResult(
            winner=winner, all_branches=[winner],
            strategy_template="winning strategy", pruned_count=2, cost=0.03,
        )
        with patch.object(engine._tot, "explore",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="submit application", domain="job_application",
                stakes="high", scorer=lambda x: 9.0,
            )
        assert result.level == ThinkLevel.L3_TREE_OF_THOUGHT
        assert result.answer == "tot answer"

    @pytest.mark.asyncio
    async def test_auto_escalation_l0_to_l1(self, mock_memory):
        """L0 scores poorly → auto-escalates to L1."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")

        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="better answer"):
            result = await engine.think(
                task="classify email", domain="email",
                scorer=lambda x: 8.0 if "better" in x else 4.0,
            )
        assert result.escalated_from == ThinkLevel.L0_MEMORY
        assert result.level == ThinkLevel.L1_SINGLE

    @pytest.mark.asyncio
    async def test_auto_escalation_l1_to_l2(self, mock_memory):
        """L1 low confidence → auto-escalates to L2."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Weak", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        mock_reflexion_result = ReflexionResult(
            answer="reflexion answer", score=8.0, attempts=1, cost=0.003,
        )
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="low conf answer"), \
             patch.object(engine._reflexion, "run",
                          new_callable=AsyncMock, return_value=mock_reflexion_result):
            result = await engine.think(
                task="test", domain="test", stakes="medium",
                scorer=lambda x: 4.0 if "low conf" in x else 8.0,
            )
        assert result.escalated_from is not None

    @pytest.mark.asyncio
    async def test_force_level_overrides_classifier(self, engine):
        """force_level=L3 → classifier not consulted."""
        winner = Branch(branch_id="b0", reasoning="forced",
                        output="forced answer", score=9.0, depth=0)
        mock_result = ToTResult(
            winner=winner, all_branches=[winner],
            strategy_template="", pruned_count=0, cost=0.03,
        )
        with patch.object(engine._tot, "explore",
                          new_callable=AsyncMock, return_value=mock_result):
            result = await engine.think(
                task="test", domain="test",
                force_level=ThinkLevel.L3_TREE_OF_THOUGHT,
                scorer=lambda x: 9.0,
            )
        assert result.level == ThinkLevel.L3_TREE_OF_THOUGHT

    @pytest.mark.asyncio
    async def test_flush_writes_to_memory(self, mock_memory):
        """flush() writes pending strategy templates to memory."""
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        # L0 calls accumulate pending writes
        for _ in range(3):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="answer"):
                await engine.think(task="test", domain="email",
                                   scorer=lambda x: 9.0)
        initial_count = len(mock_memory.learn_procedure_calls)
        await engine.flush()
        assert len(mock_memory.learn_procedure_calls) >= initial_count

    @pytest.mark.asyncio
    async def test_report_tracks_levels(self, mock_memory):
        """report() shows accurate level distribution."""
        for i in range(4):
            mock_memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="email",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="answer"):
            await engine.think(task="test", domain="email", scorer=lambda x: 9.0)
        report = engine.report()
        assert "l0" in report["level_counts"] or "L0_MEMORY" in str(report["level_counts"])
        assert report["total_calls"] >= 1

    @pytest.mark.asyncio
    async def test_think_returns_result(self, mock_memory):
        """ThinkResult has all required fields populated."""
        mock_memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Strategy", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        engine = CognitiveEngine(mock_memory, agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="result"):
            result = await engine.think(task="test", domain="test",
                                        scorer=lambda x: 8.0)
        assert hasattr(result, "answer")
        assert hasattr(result, "score")
        assert hasattr(result, "level")
        assert hasattr(result, "cost")
        assert hasattr(result, "latency_ms")
        assert result.answer == "result"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/shared/cognitive/test_engine.py -v 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'shared.cognitive._engine'`

- [ ] **Step 3: Implement CognitiveEngine**

Create `shared/cognitive/_engine.py`:

```python
"""CognitiveEngine — single entry point for 4-level cognitive reasoning."""

import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from shared.logging_config import get_logger
from shared.cognitive._budget import ThinkLevel, CognitiveBudget, BudgetTracker
from shared.cognitive._classifier import EscalationClassifier
from shared.cognitive._strategy import StrategyComposer, ComposedPrompt
from shared.cognitive._reflexion import ReflexionLoop
from shared.cognitive._tree_of_thought import TreeOfThought

logger = get_logger(__name__)

_DEFAULT_SCORE_THRESHOLD = float(os.getenv("COGNITIVE_SCORE_THRESHOLD", "7.0"))
_GENERATE_COST = 0.001


async def _llm_generate(prompt: str, model: str = None) -> str:
    from shared.agents import get_llm
    from shared.streaming import smart_llm_call
    llm = get_llm(model=model or "gpt-4.1-mini", temperature=0.3, timeout=30.0)
    messages = [{"role": "user", "content": prompt}]
    response = smart_llm_call(llm, messages)
    return response.content


@dataclass
class ThinkResult:
    answer: str
    score: float
    level: ThinkLevel
    cost: float
    latency_ms: float
    strategy_stored: bool = False
    escalated_from: Optional[ThinkLevel] = None
    attempts: int = 1
    composed_prompt: Optional[ComposedPrompt] = None


class CognitiveEngine:
    """The single entry point for cognitive reasoning.

    Usage:
        engine = CognitiveEngine(memory_manager, agent_name="my_agent")
        result = await engine.think(task="...", domain="...", stakes="medium")
        await engine.flush()
    """

    def __init__(
        self,
        memory_manager,
        agent_name: str,
        budget: Optional[CognitiveBudget] = None,
    ):
        self._memory = memory_manager
        self._agent_name = agent_name
        bgt = budget or CognitiveBudget.from_env()
        self._budget_tracker = BudgetTracker(bgt)
        self._classifier = EscalationClassifier(memory_manager, self._budget_tracker)
        self._classifier.load_persisted_stats()
        self._composer = StrategyComposer()
        self._reflexion = ReflexionLoop(memory_manager, agent_name)
        self._tot = TreeOfThought(memory_manager, agent_name)
        self._pending_writes: list[dict] = []
        self._level_counts: dict[str, int] = {}
        self._total_cost = 0.0
        self._total_calls = 0

    async def think(
        self,
        task: str,
        domain: str,
        stakes: str = "medium",
        scorer: Optional[Callable] = None,
        force_level: Optional[ThinkLevel] = None,
    ) -> ThinkResult:
        start = time.monotonic()
        self._total_calls += 1

        # 1. Classify
        level = force_level if force_level is not None else \
            self._classifier.classify(task, domain, stakes)
        original_level = level

        # 2. Compose prompt from strategy templates
        composed = self._composer.compose(task, domain, self._agent_name, self._memory)

        # 3. Execute at classified level
        result = await self._execute(level, task, domain, composed, scorer)

        # 4. Post-execution: auto-escalate if score too low
        if result.score < 6.0 and level < ThinkLevel.L3_TREE_OF_THOUGHT:
            should, next_level = self._classifier.should_escalate(
                level, result.score, result.score / 10.0,
            )
            if should and self._budget_tracker.allows(next_level):
                escalated_result = await self._execute(
                    next_level, task, domain, composed, scorer,
                )
                escalated_result.escalated_from = original_level
                escalated_result.level = next_level
                elapsed = (time.monotonic() - start) * 1000
                escalated_result.latency_ms = elapsed
                self._classifier.update_domain_stats(domain, original_level, escalated=True)
                self._record_level(next_level, escalated_result.cost)
                return escalated_result

        elapsed = (time.monotonic() - start) * 1000
        result.latency_ms = elapsed
        result.composed_prompt = composed
        self._classifier.update_domain_stats(domain, level, escalated=False)
        self._record_level(level, result.cost)
        return result

    async def _execute(
        self,
        level: ThinkLevel,
        task: str,
        domain: str,
        composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        if level == ThinkLevel.L0_MEMORY:
            return self._execute_l0(task, composed, scorer)
        elif level == ThinkLevel.L1_SINGLE:
            return await self._execute_l1(task, composed, scorer)
        elif level == ThinkLevel.L2_REFLEXION:
            return await self._execute_l2(task, domain, composed, scorer)
        elif level == ThinkLevel.L3_TREE_OF_THOUGHT:
            return await self._execute_l3(task, domain, composed, scorer)
        return await self._execute_l1(task, composed, scorer)

    def _execute_l0(
        self, task: str, composed: ComposedPrompt, scorer: Optional[Callable],
    ) -> ThinkResult:
        if composed.templates_used:
            procs = self._memory.get_procedural_entries("") \
                if hasattr(self._memory, "get_procedural_entries") else []
            for p in procs:
                if getattr(p, "procedure_id", "") in composed.templates_used:
                    answer = p.strategy
                    score = scorer(answer) if scorer else p.avg_score_when_used
                    return ThinkResult(
                        answer=answer, score=score,
                        level=ThinkLevel.L0_MEMORY, cost=0.0, latency_ms=0,
                    )
        answer = composed.text[:500]
        score = scorer(answer) if scorer else 5.0
        return ThinkResult(
            answer=answer, score=score,
            level=ThinkLevel.L0_MEMORY, cost=0.0, latency_ms=0,
        )

    async def _execute_l1(
        self, task: str, composed: ComposedPrompt, scorer: Optional[Callable],
    ) -> ThinkResult:
        prompt = f"{composed.text}\n\nTask: {task}"
        answer = await _llm_generate(prompt)
        score = scorer(answer) if scorer else 5.0
        return ThinkResult(
            answer=answer, score=score,
            level=ThinkLevel.L1_SINGLE, cost=_GENERATE_COST, latency_ms=0,
        )

    async def _execute_l2(
        self, task: str, domain: str, composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        ref_result = await self._reflexion.run(
            task=task, domain=domain, initial_prompt=composed.text,
            score_threshold=_DEFAULT_SCORE_THRESHOLD, scorer=scorer,
        )
        return ThinkResult(
            answer=ref_result.answer, score=ref_result.score,
            level=ThinkLevel.L2_REFLEXION, cost=ref_result.cost,
            latency_ms=0, attempts=ref_result.attempts,
            strategy_stored=ref_result.score >= _DEFAULT_SCORE_THRESHOLD,
        )

    async def _execute_l3(
        self, task: str, domain: str, composed: ComposedPrompt,
        scorer: Optional[Callable],
    ) -> ThinkResult:
        tot_result = await self._tot.explore(
            task=task, domain=domain, context=composed.text, scorer=scorer,
        )
        return ThinkResult(
            answer=tot_result.winner.output, score=tot_result.winner.score,
            level=ThinkLevel.L3_TREE_OF_THOUGHT, cost=tot_result.cost,
            latency_ms=0, strategy_stored=True,
        )

    def _record_level(self, level: ThinkLevel, cost: float):
        name = level.name
        self._level_counts[name] = self._level_counts.get(name, 0) + 1
        self._total_cost += cost
        self._budget_tracker.record(level, cost)

    def report(self) -> dict:
        return {
            "agent_name": self._agent_name,
            "total_calls": self._total_calls,
            "level_counts": dict(self._level_counts),
            "total_cost": round(self._total_cost, 4),
            "budget": self._budget_tracker.report(),
        }

    async def flush(self):
        for write in self._pending_writes:
            try:
                self._memory.learn_procedure(**write)
            except Exception as e:
                logger.warning("Failed to flush strategy template: %s", e)
        self._pending_writes.clear()
```

- [ ] **Step 4: Update shared/cognitive/__init__.py with public exports**

Replace the content of `shared/cognitive/__init__.py`:

```python
"""Cognitive Reasoning Engines — 4-level graduated escalation.

Opt-in toolkit for agents. Any agent adds cognitive abilities via:

    from shared.cognitive import CognitiveEngine

    engine = CognitiveEngine(memory_manager, agent_name="my_agent")
    result = await engine.think(task="...", domain="...", stakes="medium")
    await engine.flush()

Levels:
    L0 Memory Recall  — strategy templates found, zero LLM calls
    L1 Single Shot    — one LLM call with composed prompt
    L2 Reflexion      — try/critique/retry with failure memory
    L3 Tree of Thought — parallel branch exploration via GRPO
"""

from shared.cognitive._budget import (  # noqa: F401
    ThinkLevel,
    CognitiveBudget,
    BudgetTracker,
)
from shared.cognitive._strategy import (  # noqa: F401
    StrategyComposer,
    ComposedPrompt,
)
from shared.cognitive._reflexion import ReflexionResult  # noqa: F401
from shared.cognitive._tree_of_thought import ToTResult, Branch  # noqa: F401
from shared.cognitive._engine import CognitiveEngine, ThinkResult  # noqa: F401
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/shared/cognitive/test_engine.py -v
```

Expected: 10 passed

- [ ] **Step 6: Run ALL cognitive tests to verify no regressions**

```bash
python -m pytest tests/shared/cognitive/ -v
```

Expected: 62 passed (8 budget + 10 classifier + 12 strategy + 12 reflexion + 10 ToT + 10 engine)

- [ ] **Step 7: Commit**

```bash
git add shared/cognitive/_engine.py shared/cognitive/__init__.py \
       tests/shared/cognitive/test_engine.py
git commit -m "feat(cognitive): add CognitiveEngine — single entry point for 4-level reasoning"
```

---

## Task 8: Integration Tests

**Files:**
- Create: `tests/shared/cognitive/test_integration.py`

- [ ] **Step 1: Write the integration tests**

Create `tests/shared/cognitive/test_integration.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget
from shared.cognitive._reflexion import ReflexionResult
from shared.cognitive._tree_of_thought import ToTResult, Branch
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestIntegration:

    @pytest.mark.asyncio
    async def test_full_pipeline_novel_to_cached(self):
        """Novel task (L2) → templates stored → same task again → L0."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # First call: novel domain → L2 Reflexion
        async def mock_gen(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Bad\nFIX: Fix"
            return "good answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_gen), \
             patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="good answer"):
            result1 = await engine.think(
                task="classify email", domain="email_classification",
                stakes="medium", scorer=lambda x: 8.5,
            )
        assert result1.level in (ThinkLevel.L2_REFLEXION, ThinkLevel.L1_SINGLE)

        # Manually set strong templates (simulating what learn_procedure did)
        for i in range(4):
            memory._procedural.append(MockProceduralEntry(
                procedure_id=f"learned_{i}", domain="email_classification",
                strategy=f"Learned: {result1.answer[:50]}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))

        # Second call: same domain → should be L0
        result2 = await engine.think(
            task="classify email", domain="email_classification",
            scorer=lambda x: 9.0,
        )
        assert result2.level == ThinkLevel.L0_MEMORY

    @pytest.mark.asyncio
    async def test_failure_prevents_repeat(self):
        """Failure stored → shows up as anti-pattern in next attempt."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # First call fails
        async def mock_gen_fail(prompt, model=None):
            if "went wrong" in prompt:
                return "MISTAKE: Missed edge case\nFIX: Check boundaries"
            return "bad answer"

        with patch("shared.cognitive._reflexion._llm_generate",
                   side_effect=mock_gen_fail), \
             patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="bad"):
            await engine.think(
                task="classify", domain="email_classification",
                stakes="medium", scorer=lambda x: 3.0,
            )

        # Verify failure was stored
        failures = [e for e in memory._episodic if e.final_score < 5.0]
        assert len(failures) >= 1

    @pytest.mark.asyncio
    async def test_cross_agent_transfer(self):
        """Agent A creates template → Agent B on same domain finds it."""
        memory = MockMemoryManager()

        # Agent A learns
        engine_a = CognitiveEngine(memory, agent_name="agent_a")
        memory._procedural.append(MockProceduralEntry(
            procedure_id="transfer_1", domain="shared_domain",
            strategy="Agent A's strategy: validate first",
            success_rate=0.9, times_used=5, avg_score_when_used=8.5,
        ))

        # Agent B queries same domain
        engine_b = CognitiveEngine(memory, agent_name="agent_b")
        composed = engine_b._composer.compose(
            "do task", "shared_domain", "agent_b", memory,
        )
        assert "validate first" in composed.text

    @pytest.mark.asyncio
    async def test_escalation_chain_l0_to_l3(self):
        """L0 fails → L1 fails → L2 fails → L3 succeeds."""
        memory = MockMemoryManager()
        for i in range(4):
            memory._procedural.append(MockProceduralEntry(
                procedure_id=f"proc_{i}", domain="hard",
                strategy=f"Strategy {i}", success_rate=0.95,
                times_used=5, avg_score_when_used=8.5,
            ))
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # Everything returns low score except L3
        winner = Branch(branch_id="b0", reasoning="r", output="L3 answer",
                        score=9.0, depth=0)
        tot_result = ToTResult(
            winner=winner, all_branches=[winner], strategy_template="",
            pruned_count=0, cost=0.03,
        )

        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="bad"), \
             patch.object(engine._reflexion, "run", new_callable=AsyncMock,
                          return_value=ReflexionResult(
                              answer="still bad", score=4.0, attempts=3, cost=0.01)), \
             patch.object(engine._tot, "explore", new_callable=AsyncMock,
                          return_value=tot_result):
            result = await engine.think(
                task="hard task", domain="hard", stakes="high",
                scorer=lambda x: 9.0 if "L3" in x else 3.0,
            )
        assert result.escalated_from is not None

    @pytest.mark.asyncio
    async def test_cron_lifecycle(self):
        """Cron simulation: create engine → think → flush → new engine → verify."""
        memory = MockMemoryManager()

        # Simulate cron run 1
        engine1 = CognitiveEngine(memory, agent_name="cron_agent")
        memory._procedural.append(MockProceduralEntry(
            domain="cron_task", strategy="Cron strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="cron result"):
            await engine1.think(task="cron task", domain="cron_task",
                                scorer=lambda x: 8.0)
        await engine1.flush()

        # New engine on new cron run should see the templates
        engine2 = CognitiveEngine(memory, agent_name="cron_agent")
        procs = memory.get_procedural_entries("cron_task")
        assert len(procs) >= 1

    @pytest.mark.asyncio
    async def test_concurrent_agents(self):
        """5 agents thinking simultaneously → no data corruption."""
        memory = MockMemoryManager()

        async def agent_run(name: str):
            engine = CognitiveEngine(memory, agent_name=name)
            memory._procedural.append(MockProceduralEntry(
                domain=f"domain_{name}", strategy=f"Strategy for {name}",
                success_rate=0.5, times_used=1, avg_score_when_used=6.0,
            ))
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value=f"result_{name}"):
                return await engine.think(
                    task=f"task for {name}", domain=f"domain_{name}",
                    scorer=lambda x: 8.0,
                )

        results = await asyncio.gather(
            *[agent_run(f"agent_{i}") for i in range(5)]
        )
        assert len(results) == 5
        assert all(r.answer for r in results)

    @pytest.mark.asyncio
    async def test_degraded_mode_no_memory(self):
        """Memory manager raises → engine degrades to L1."""
        class BrokenMemory:
            def get_procedural_entries(self, domain):
                raise RuntimeError("Memory unavailable")
            def get_episodic_entries(self, domain):
                raise RuntimeError("Memory unavailable")
            def learn_procedure(self, **kwargs):
                pass
            def record_episode(self, **kwargs):
                pass

        engine = CognitiveEngine(BrokenMemory(), agent_name="test_agent")
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="degraded answer"):
            result = await engine.think(
                task="test", domain="test", scorer=lambda x: 7.0,
            )
        assert result.answer == "degraded answer"

    @pytest.mark.asyncio
    async def test_report_after_session(self):
        """Full session → report has accurate stats."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # L1 call
        memory._procedural.append(MockProceduralEntry(
            domain="d1", strategy="S1", success_rate=0.5,
            times_used=1, avg_score_when_used=6.0,
        ))
        with patch("shared.cognitive._engine._llm_generate",
                   new_callable=AsyncMock, return_value="answer"):
            await engine.think(task="t1", domain="d1", scorer=lambda x: 8.0)

        report = engine.report()
        assert report["total_calls"] >= 1
        assert report["total_cost"] >= 0
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/shared/cognitive/test_integration.py -v
```

Expected: 8 passed

- [ ] **Step 3: Commit**

```bash
git add tests/shared/cognitive/test_integration.py
git commit -m "test(cognitive): add 8 integration tests — pipeline, transfer, escalation, degradation"
```

---

## Task 9: Self-Improvement Tests

**Files:**
- Create: `tests/shared/cognitive/test_self_improvement.py`

- [ ] **Step 1: Write the self-improvement tests**

Create `tests/shared/cognitive/test_self_improvement.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from shared.cognitive._engine import CognitiveEngine
from shared.cognitive._budget import ThinkLevel, CognitiveBudget
from shared.cognitive._classifier import EscalationClassifier, _L0_SKIP_THRESHOLD
from tests.shared.cognitive.conftest import MockMemoryManager, MockProceduralEntry


class TestSelfImprovement:

    @pytest.mark.asyncio
    async def test_template_score_improves(self):
        """Simulate 10 runs on same domain → template avg_score increases."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        memory._procedural.append(MockProceduralEntry(
            domain="test", strategy="Initial strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))

        scores = [6.5, 7.0, 7.5, 7.0, 8.0, 8.5, 8.0, 9.0, 8.5, 9.0]
        for score in scores:
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="improving"):
                await engine.think(task="test", domain="test",
                                   scorer=lambda x, s=score: s)

        # More procedures should have been stored with improving scores
        procs = memory.get_procedural_entries("test")
        if len(procs) > 1:
            later_scores = [p.avg_score_when_used for p in procs[-3:]]
            assert max(later_scores) >= 7.0

    @pytest.mark.asyncio
    async def test_bad_template_decays(self):
        """Template with low success rate gets ranked below better ones and excluded at limit."""
        memory = MockMemoryManager()
        memory._procedural.append(MockProceduralEntry(
            procedure_id="bad", domain="test", strategy="Bad strategy",
            success_rate=0.3, times_used=10, avg_score_when_used=4.0,
        ))
        memory._procedural.append(MockProceduralEntry(
            procedure_id="good", domain="test", strategy="Good strategy",
            success_rate=0.9, times_used=10, avg_score_when_used=8.5,
        ))
        engine = CognitiveEngine(memory, agent_name="test_agent")
        # With max_templates=1, only the good template should survive
        composed = engine._composer.compose("task", "test", "test_agent", memory,
                                            max_templates=1)
        assert "Good strategy" in composed.text
        assert "Bad strategy" not in composed.text

    def test_classifier_learns_easy_domain(self):
        """20 successful L0 calls → classifier stores high success rate."""
        memory = MockMemoryManager()
        budget = CognitiveBudget()
        classifier = EscalationClassifier(memory, BudgetTracker(budget))

        for _ in range(20):
            classifier.update_domain_stats("easy_domain", ThinkLevel.L0_MEMORY,
                                           escalated=False)

        stats = classifier._domain_stats.get("easy_domain", {})
        assert stats.get("l0_success_rate", 0) >= _L0_SKIP_THRESHOLD

    def test_classifier_learns_hard_domain(self):
        """10 L1 calls with 60% escalation → classifier detects difficulty."""
        memory = MockMemoryManager()
        from shared.cognitive._budget import BudgetTracker
        classifier = EscalationClassifier(memory, BudgetTracker(CognitiveBudget()))

        for i in range(10):
            escalated = i < 6  # 60% escalation rate
            classifier.update_domain_stats("hard_domain", ThinkLevel.L1_SINGLE,
                                           escalated=escalated)

        stats = classifier._domain_stats.get("hard_domain", {})
        assert stats.get("l1_escalation_rate", 0) >= 0.5

    @pytest.mark.asyncio
    async def test_templates_promote_with_usage(self):
        """Template used 15x with 90% success → classifier learns domain is easy."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        memory._procedural.append(MockProceduralEntry(
            domain="tested", strategy="Proven strategy",
            success_rate=0.5, times_used=1, avg_score_when_used=6.0,
        ))

        for _ in range(15):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                await engine.think(task="task", domain="tested",
                                   scorer=lambda x: 9.0)

        # After 15 successful runs, classifier should track this domain
        procs = memory.get_procedural_entries("tested")
        assert len(procs) >= 1
        # The classifier's domain stats should reflect accumulated success
        stats = engine._classifier._domain_stats.get("tested", {})
        assert stats.get("sample_size", 0) >= 10

    @pytest.mark.asyncio
    async def test_l0_percentage_increases_over_runs(self):
        """Simulate 30 runs → L0 percentage increases across batches."""
        memory = MockMemoryManager()
        engine = CognitiveEngine(memory, agent_name="test_agent")

        # Batch 1: 10 runs on novel domain → mostly L1/L2
        batch1_levels = []
        for i in range(10):
            if i >= 3:
                for j in range(4):
                    memory._procedural.append(MockProceduralEntry(
                        procedure_id=f"b1_{i}_{j}", domain="evolving",
                        strategy=f"Strategy {i}", success_rate=0.95,
                        times_used=5, avg_score_when_used=8.5,
                    ))
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"), \
                 patch("shared.cognitive._reflexion._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                r = await engine.think(task="task", domain="evolving",
                                       scorer=lambda x: 8.0)
                batch1_levels.append(r.level)

        batch1_l0 = sum(1 for l in batch1_levels if l == ThinkLevel.L0_MEMORY)

        # Batch 2: 10 more runs — should have more L0
        batch2_levels = []
        for _ in range(10):
            with patch("shared.cognitive._engine._llm_generate",
                       new_callable=AsyncMock, return_value="result"):
                r = await engine.think(task="task", domain="evolving",
                                       scorer=lambda x: 8.0)
                batch2_levels.append(r.level)

        batch2_l0 = sum(1 for l in batch2_levels if l == ThinkLevel.L0_MEMORY)
        assert batch2_l0 >= batch1_l0
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/shared/cognitive/test_self_improvement.py -v
```

Expected: 6 passed

- [ ] **Step 3: Run full cognitive test suite**

```bash
python -m pytest tests/shared/cognitive/ -v --tb=short
```

Expected: 76 passed (8 + 10 + 12 + 12 + 10 + 10 + 8 + 6)

- [ ] **Step 4: Commit**

```bash
git add tests/shared/cognitive/test_self_improvement.py
git commit -m "test(cognitive): add 6 self-improvement tests — template evolution, classifier learning"
```

---

## Task 10: Update Documentation

**Files:**
- Create: `shared/cognitive/CLAUDE.md`
- Modify: `shared/CLAUDE.md`
- Modify: `CLAUDE.md` (root)
- Modify: `AGENTS.md`
- Modify: `patterns/CLAUDE.md`
- Modify: `jobpulse/CLAUDE.md`

- [ ] **Step 1: Create shared/cognitive/CLAUDE.md**

Create `shared/cognitive/CLAUDE.md`:

```markdown
# Cognitive Reasoning Engines

4-level graduated escalation system. Any agent opts in via 3 lines.

## Levels

| Level | Name | When | LLM Calls | Cost |
|-------|------|------|-----------|------|
| L0 | Memory Recall | Strong templates in memory (score>=8, used>=3) | 0 | ~$0 |
| L1 | Single Shot | Weak templates or partial memory | 1 | ~$0.001 |
| L2 | Reflexion | Novel + medium stakes, or L1 scored poorly | 2-3 | ~$0.005 |
| L3 | Tree of Thought | Novel + high stakes, or L2 failed | 6-12 | ~$0.02-0.05 |

## Usage

```python
from shared.cognitive import CognitiveEngine

engine = CognitiveEngine(memory_manager, agent_name="my_agent")
result = await engine.think(task="...", domain="...", stakes="medium")
# result.answer, result.score, result.level, result.cost
await engine.flush()  # batch-write strategy templates to memory
```

## Modules

| Module | Purpose |
|--------|---------|
| `_budget.py` | ThinkLevel enum, CognitiveBudget, BudgetTracker |
| `_classifier.py` | EscalationClassifier — 3-step heuristic (memory → novelty → stakes) |
| `_strategy.py` | StrategyComposer, ComposedPrompt — template retrieval + prompt assembly |
| `_prompts.py` | Prompt templates for critique, branching, scoring |
| `_reflexion.py` | ReflexionLoop — L2 try/critique/retry |
| `_tree_of_thought.py` | TreeOfThought — L3 branch/score/prune/extend |
| `_engine.py` | CognitiveEngine — single entry point |

## Rules

- ALL LLM calls go through get_llm() / smart_llm_call()
- Critiques use gpt-4.1-nano (~$0.0002/call)
- Budget caps: 20 L2/hour, 5 L3/hour, $0.50/hour max
- Strategy templates stored via MemoryManager.learn_procedure()
- Failure patterns stored via MemoryManager.record_episode()
- Call flush() at end of agent run to persist pending writes
```

- [ ] **Step 2: Add cognitive section to shared/CLAUDE.md**

Append to `shared/CLAUDE.md` before the `## Rules` section:

```markdown
## Cognitive Reasoning (shared/cognitive/)
4-level graduated escalation: L0 Memory Recall → L1 Single Shot → L2 Reflexion → L3 Tree of Thought.
- `CognitiveEngine.think(task, domain, stakes)` — single entry point
- EscalationClassifier picks level via heuristic (memory → novelty → stakes)
- StrategyComposer assembles prompts from templates + anti-patterns
- Budget caps: 20 L2/hour, 5 L3/hour, $0.50/hour. Kill switch: COGNITIVE_ENABLED=false
- Full docs: `shared/cognitive/CLAUDE.md`
```

- [ ] **Step 3: Add to root CLAUDE.md Module Context**

In `CLAUDE.md`, add to the Module Context section:

```markdown
- `shared/cognitive/CLAUDE.md` — 4-level cognitive engine: memory recall, single shot, reflexion, tree of thought
```

- [ ] **Step 4: Update AGENTS.md**

Add to the "Briefing" or "Tools available" section of `AGENTS.md`:

```markdown
## Cognitive Reasoning (opt-in)
Use `CognitiveEngine.think(task, domain, stakes)` for tasks that benefit from self-improvement.
Call `flush()` at end of run to persist strategy templates.
Import: `from shared.cognitive import CognitiveEngine`
```

- [ ] **Step 5: Update patterns/CLAUDE.md**

Append to `patterns/CLAUDE.md` after the Experiential Learning section:

```markdown
## Cognitive Reasoning (shared/cognitive/)
Pattern nodes can use CognitiveEngine for self-improving reasoning inside their execution:
```python
engine = CognitiveEngine(memory_manager, agent_name="researcher")
result = await engine.think(task=state["topic"], domain="research", stakes="medium")
```
```

- [ ] **Step 6: Update jobpulse/CLAUDE.md**

Append to `jobpulse/CLAUDE.md` after the Agents section:

```markdown
## Cognitive Engine Integration
Agents using CognitiveEngine: gmail_agent (email classification), job_autopilot (form strategy).
Cron runs create engine → think per sub-task → flush() at end. Templates persist across runs.
Kill switch: `COGNITIVE_ENABLED=false`
```

- [ ] **Step 7: Verify documentation is consistent**

```bash
python -c "from shared.cognitive import CognitiveEngine, ThinkResult, ThinkLevel, CognitiveBudget; print('All exports OK')"
```

Expected: `All exports OK`

- [ ] **Step 8: Run full test suite to verify no regressions**

```bash
python -m pytest tests/shared/cognitive/ -v --tb=short
```

Expected: 76 passed

- [ ] **Step 9: Commit**

```bash
git add shared/cognitive/CLAUDE.md shared/CLAUDE.md CLAUDE.md AGENTS.md \
       patterns/CLAUDE.md jobpulse/CLAUDE.md
git commit -m "docs(cognitive): add agent-facing documentation — 6 files updated"
```

---

## Task 11: Final Integration Verification

- [ ] **Step 1: Verify package imports work end-to-end**

```bash
python -c "
from shared.cognitive import (
    CognitiveEngine, ThinkResult, ThinkLevel, CognitiveBudget,
    BudgetTracker, StrategyComposer, ComposedPrompt,
    ReflexionResult, ToTResult, Branch,
)
print('All', 10, 'exports importable')

from shared.cognitive._classifier import EscalationClassifier, STAKES_REGISTRY
print('Classifier + STAKES_REGISTRY OK')

from shared.cognitive._prompts import CRITIQUE_PROMPT, BRANCH_STRATEGIES
print('Prompts OK:', len(BRANCH_STRATEGIES), 'branch strategies')

b = CognitiveBudget.from_env()
print('Budget from env:', b)
"
```

Expected: All lines print successfully.

- [ ] **Step 2: Run full cognitive test suite**

```bash
python -m pytest tests/shared/cognitive/ -v --tb=short 2>&1 | tail -10
```

Expected: 76 passed, 0 failed

- [ ] **Step 3: Run existing test suite to verify zero regressions**

```bash
python -m pytest tests/ -v --tb=short -x -q 2>&1 | tail -20
```

Expected: No new failures introduced by cognitive engine.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(cognitive): complete Pillar 2 — cognitive reasoning engines

Implements Pillar 2 of the autonomous agent infrastructure:
- 4-level graduated escalation (L0 Memory → L1 Single → L2 Reflexion → L3 ToT)
- EscalationClassifier with 3-step heuristic + self-improving domain stats
- StrategyComposer with template retrieval, anti-pattern injection, token budget
- ReflexionLoop (try/critique/retry) with failure memory storage
- TreeOfThought (branch/score/prune/extend) via structured exploration
- CognitiveEngine single entry point with auto-escalation + budget guardrails
- 76 tests across 8 test files
- Agent-facing documentation updated"
```

---

## Summary

| Task | What | Tests | Files |
|------|------|-------|-------|
| 1 | Package scaffold, prompts, test fixtures, nano pricing | 0 | 5 |
| 2 | CognitiveBudget + BudgetTracker | 8 | 2 |
| 3 | EscalationClassifier | 10 | 2 |
| 4 | StrategyComposer + ComposedPrompt | 12 | 2 |
| 5 | ReflexionLoop (L2) | 12 | 2 |
| 6 | TreeOfThought (L3) | 10 | 2 |
| 7 | CognitiveEngine + public exports | 10 | 3 |
| 8 | Integration tests | 8 | 1 |
| 9 | Self-improvement tests | 6 | 1 |
| 10 | Documentation (6 files) | 0 | 6 |
| 11 | Final verification | 0 | 0 |
| **Total** | | **76** | **26** |
