# Database Wiring Tests & Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write failing tests that prove the learning chain has wiring gaps, then fix each gap so the tests pass. TDD: red → green → commit.

**Architecture:** Each task targets one specific wiring gap. Tests use `tmp_path` for all DB writes. Tests import real modules and call real functions (no mocks) — they prove the call chain fires end-to-end within a single process. Dead 0-byte database files with zero code references are deleted outright.

**Tech Stack:** pytest, SQLite (via `tmp_path`), monkeypatch for DB paths

---

## File Structure

| File | Responsibility |
|------|---------------|
| `tests/jobpulse/test_wiring_e2e.py` | End-to-end chain: `post_apply_hook` → downstream systems |
| `tests/jobpulse/test_gate4_wiring.py` | `record_gate_decision()` called from gate4 functions |
| `tests/jobpulse/test_outcome_wiring.py` | `save_outcome()` + `update_company_reliability()` wired |
| `tests/shared/optimization/test_snapshot_wiring.py` | `snapshot()` called from `optimize()` tick |
| `tests/shared/optimization/test_cognitive_wiring.py` | `record_cognitive_outcome()` called from `CognitiveEngine.think()` |
| `tests/jobpulse/test_scan_learning_wiring.py` | `ScanLearningEngine` integration with `job_scanners/` |
| `jobpulse/post_apply_hook.py` | Wire `save_outcome()` + `update_company_reliability()` |
| `jobpulse/gate4_quality.py` | Wire `record_gate_decision()` after each gate check |
| `shared/optimization/_engine.py` | Expose `snapshot()` on facade, call from `optimize()` |
| `shared/cognitive/_engine.py` | Call `record_cognitive_outcome()` after `think()` |

---

### Task 1: End-to-end learning chain test

Prove that `post_apply_hook()` triggers all downstream systems: form experience DB write, strategy reflection, optimization engine signals, navigation learning.

**Files:**
- Create: `tests/jobpulse/test_wiring_e2e.py`

- [ ] **Step 1: Write the failing test**

```python
"""End-to-end wiring test: post_apply_hook → all downstream systems fire."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def wiring_dbs(tmp_path):
    """Create all DBs that post_apply_hook touches, return paths."""
    fe_path = str(tmp_path / "form_experience.db")
    opt_path = str(tmp_path / "optimization.db")
    nav_path = str(tmp_path / "navigation_learning.db")
    traj_path = str(tmp_path / "trajectory.db")
    app_path = str(tmp_path / "applications.db")
    return {
        "form_experience": fe_path,
        "optimization": opt_path,
        "navigation": nav_path,
        "trajectory": traj_path,
        "applications": app_path,
    }


def _make_result(success=True):
    return {
        "success": success,
        "pages_filled": 2,
        "field_types": ["text", "select", "file"],
        "screening_questions": ["Salary expectation: 35000"],
        "time_seconds": 12.5,
        "agent_fill_stats": {
            "fields_attempted": 5,
            "fields_filled": 4,
            "fields_failed": 1,
            "failed_labels": ["Cover Letter"],
            "llm_fallback_count": 1,
        },
        "navigation_steps": [
            {"action": "click", "selector": "#apply-btn"},
            {"action": "fill", "selector": "#name", "value": "test"},
        ],
    }


def _make_job_context(job_id="test_job_001"):
    return {
        "job_id": job_id,
        "company": "TestCorp",
        "title": "Data Analyst",
        "url": "https://boards.greenhouse.io/testcorp/jobs/123",
        "platform": "greenhouse",
        "ats_platform": "greenhouse",
        "notion_page_id": None,
        "cv_path": None,
        "cover_letter_path": None,
        "match_tier": "M1",
        "ats_score": 85,
        "matched_projects": ["project_a", "project_b"],
    }


def test_post_apply_hook_writes_form_experience(wiring_dbs):
    """post_apply_hook must write to form_experience DB on success."""
    from jobpulse.post_apply_hook import post_apply_hook

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.find_application_page", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page"), \
         patch("jobpulse.post_apply_hook.JobDB") as mock_jdb:
        mock_jdb.return_value.mark_applied = MagicMock()

        post_apply_hook(
            result=_make_result(),
            job_context=_make_job_context(),
            form_exp_db_path=wiring_dbs["form_experience"],
        )

    conn = sqlite3.connect(wiring_dbs["form_experience"])
    rows = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
    conn.close()
    assert rows >= 1, "post_apply_hook must write at least 1 row to form_experience"


def test_post_apply_hook_emits_optimization_signal(wiring_dbs):
    """post_apply_hook must call OptimizationEngine.before/after_learning_action."""
    from jobpulse.post_apply_hook import post_apply_hook
    from shared.optimization._engine import OptimizationEngine

    engine = OptimizationEngine(db_path=wiring_dbs["optimization"])

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.find_application_page", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page"), \
         patch("jobpulse.post_apply_hook.JobDB") as mock_jdb, \
         patch("shared.optimization.get_optimization_engine", return_value=engine), \
         patch("jobpulse.strategy_reflector.reflect_on_application", return_value=MagicMock(
             heuristics="[]", fields_total=5, fields_pattern=3,
             fields_llm=1, fields_corrected=1,
         )):
        mock_jdb.return_value.mark_applied = MagicMock()

        post_apply_hook(
            result=_make_result(),
            job_context=_make_job_context(),
            form_exp_db_path=wiring_dbs["form_experience"],
        )

    conn = sqlite3.connect(wiring_dbs["optimization"])
    conn.row_factory = sqlite3.Row
    actions = conn.execute("SELECT COUNT(*) as cnt FROM learning_actions").fetchone()["cnt"]
    conn.close()
    assert actions >= 1, "post_apply_hook must create at least 1 learning_action"


def test_post_apply_hook_records_navigation(wiring_dbs):
    """post_apply_hook must save navigation steps via NavigationLearner."""
    from jobpulse.post_apply_hook import post_apply_hook
    from jobpulse.navigation_learner import NavigationLearner

    nav_learner = NavigationLearner(db_path=wiring_dbs["navigation"])

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.find_application_page", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page"), \
         patch("jobpulse.post_apply_hook.JobDB") as mock_jdb, \
         patch("jobpulse.post_apply_hook.NavigationLearner", return_value=nav_learner), \
         patch("jobpulse.strategy_reflector.reflect_on_application", return_value=MagicMock(
             heuristics="[]", fields_total=5, fields_pattern=3,
             fields_llm=1, fields_corrected=1,
         )):
        mock_jdb.return_value.mark_applied = MagicMock()

        post_apply_hook(
            result=_make_result(),
            job_context=_make_job_context(),
            form_exp_db_path=wiring_dbs["form_experience"],
        )

    conn = sqlite3.connect(wiring_dbs["navigation"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT COUNT(*) as cnt FROM nav_sequences").fetchone()["cnt"]
    conn.close()
    assert rows >= 1, "post_apply_hook must save at least 1 nav sequence"
```

- [ ] **Step 2: Run test to verify it fails or passes (baseline)**

Run: `python -m pytest tests/jobpulse/test_wiring_e2e.py -v`
Expected: All 3 tests PASS (these systems are already wired in `post_apply_hook.py`). This establishes the baseline — the existing wiring works.

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_wiring_e2e.py
git commit -m "test(wiring): e2e tests proving post_apply_hook chain fires"
```

---

### Task 2: Wire `save_outcome()` + `update_company_reliability()` into post_apply_hook

These functions exist in `job_db.py:543-689` but have zero callers. The `application_outcomes`, `company_reliability` tables in `data/applications.db` are empty. Wire them into `post_apply_hook` so application outcomes get recorded.

**Files:**
- Modify: `tests/jobpulse/test_wiring_e2e.py` (add tests)
- Modify: `jobpulse/post_apply_hook.py:218-224` (add calls)

- [ ] **Step 1: Write the failing test**

Add to `tests/jobpulse/test_wiring_e2e.py`:

```python
def test_post_apply_hook_records_outcome(wiring_dbs):
    """post_apply_hook must call save_outcome() to record application_outcomes."""
    from jobpulse.post_apply_hook import post_apply_hook
    from jobpulse.job_db import JobDB

    jdb = JobDB(db_path=wiring_dbs["applications"])

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.find_application_page", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page"), \
         patch("jobpulse.post_apply_hook.JobDB", return_value=jdb), \
         patch("jobpulse.strategy_reflector.reflect_on_application", return_value=MagicMock(
             heuristics="[]", fields_total=5, fields_pattern=3,
             fields_llm=1, fields_corrected=1,
         )):
        post_apply_hook(
            result=_make_result(),
            job_context=_make_job_context(),
            form_exp_db_path=wiring_dbs["form_experience"],
        )

    outcome = jdb.get_outcome("test_job_001")
    assert outcome is not None, "post_apply_hook must call save_outcome()"
    assert outcome["outcome"] == "applied"
    assert outcome["stage_reached"] == "applied"


def test_post_apply_hook_updates_company_reliability(wiring_dbs):
    """post_apply_hook must call update_company_reliability()."""
    from jobpulse.post_apply_hook import post_apply_hook
    from jobpulse.job_db import JobDB

    jdb = JobDB(db_path=wiring_dbs["applications"])

    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.find_application_page", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page"), \
         patch("jobpulse.post_apply_hook.JobDB", return_value=jdb), \
         patch("jobpulse.strategy_reflector.reflect_on_application", return_value=MagicMock(
             heuristics="[]", fields_total=5, fields_pattern=3,
             fields_llm=1, fields_corrected=1,
         )):
        post_apply_hook(
            result=_make_result(),
            job_context=_make_job_context(),
            form_exp_db_path=wiring_dbs["form_experience"],
        )

    conn = sqlite3.connect(wiring_dbs["applications"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM company_reliability WHERE company = 'TestCorp'"
    ).fetchall()
    conn.close()
    assert len(rows) >= 1, "post_apply_hook must call update_company_reliability()"
    assert rows[0]["total_applied"] >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_wiring_e2e.py::test_post_apply_hook_records_outcome tests/jobpulse/test_wiring_e2e.py::test_post_apply_hook_updates_company_reliability -v`
Expected: FAIL — `post_apply_hook` doesn't call `save_outcome()` or `update_company_reliability()` today.

- [ ] **Step 3: Wire save_outcome() and update_company_reliability() into post_apply_hook**

In `jobpulse/post_apply_hook.py`, after the `mark_applied()` block (around line 224), add:

```python
    # --- 3b. Record application outcome + company reliability ---
    if job_id:
        try:
            jdb = JobDB()
            jdb.save_outcome(
                job_id=job_id,
                outcome="applied",
                stage_reached="applied",
            )
            jdb.update_company_reliability(
                company=company,
                outcome="applied",
            )
        except Exception as exc:
            logger.warning("post_apply_hook: outcome/reliability recording failed: %s", exc)
```

Note: The `JobDB` instance is already imported at the top of the file. We need to reuse the existing instance or create a new one. Since `mark_applied` uses `JobDB()` directly, follow the same pattern.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_wiring_e2e.py -v`
Expected: All tests PASS including the 2 new ones.

- [ ] **Step 5: Commit**

```bash
git add tests/jobpulse/test_wiring_e2e.py jobpulse/post_apply_hook.py
git commit -m "feat(wiring): wire save_outcome + update_company_reliability into post_apply_hook"
```

---

### Task 3: Wire `record_gate_decision()` into gate4_quality

`record_gate_decision()` exists in `job_db.py:611-621` but has zero callers. The `gate_effectiveness` table in `data/applications.db` is empty. Each gate check in `gate4_quality.py` should record its decision.

**Files:**
- Create: `tests/jobpulse/test_gate4_wiring.py`
- Modify: `jobpulse/gate4_quality.py` (add calls after each gate function)

- [ ] **Step 1: Write the failing test**

```python
"""Tests proving gate4 records decisions to gate_effectiveness table."""
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from jobpulse.gate4_quality import check_jd_quality, check_company_background


@pytest.fixture
def gate_db(tmp_path):
    """Return path to tmp applications DB."""
    return str(tmp_path / "applications.db")


def test_check_jd_quality_records_gate_decision(gate_db):
    """check_jd_quality must call record_gate_decision with its result."""
    from jobpulse.job_db import JobDB
    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        result = check_jd_quality(
            jd_text="A" * 300,
            extracted_skills=["Python", "SQL", "Pandas", "NumPy", "Scikit-learn"],
        )

    effectiveness = jdb.get_gate_effectiveness("jd_quality")
    assert len(effectiveness) >= 1, "check_jd_quality must record a gate decision"
    assert effectiveness[0]["decision"] == "pass"


def test_check_jd_quality_records_fail(gate_db):
    """check_jd_quality must record 'fail' decision for short JDs."""
    from jobpulse.job_db import JobDB
    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        result = check_jd_quality(jd_text="Short JD", extracted_skills=["Python"])

    effectiveness = jdb.get_gate_effectiveness("jd_quality")
    assert len(effectiveness) >= 1
    assert effectiveness[0]["decision"] == "fail"


def test_check_company_background_records_gate_decision(gate_db):
    """check_company_background must record its decision."""
    from jobpulse.job_db import JobDB
    jdb = JobDB(db_path=gate_db)

    with patch("jobpulse.gate4_quality.JobDB", return_value=jdb):
        result = check_company_background("Acme Corp", [])

    effectiveness = jdb.get_gate_effectiveness("company_background")
    assert len(effectiveness) >= 1, "check_company_background must record a gate decision"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_gate4_wiring.py -v`
Expected: FAIL — `check_jd_quality` and `check_company_background` don't call `record_gate_decision()` today.

- [ ] **Step 3: Wire record_gate_decision() into gate4 functions**

In `jobpulse/gate4_quality.py`, modify `check_jd_quality()` to record after computing the result. Add before `return result` at lines 73, 83, 99, and 103:

```python
def check_jd_quality(jd_text: str, extracted_skills: list[str]) -> JDQualityResult:
    # ... existing code ...

    # Record gate decision for effectiveness tracking
    try:
        from jobpulse.job_db import JobDB
        decision = "pass" if result.passed else "fail"
        JobDB().record_gate_decision("jd_quality", decision, result.reason)
    except Exception:
        pass

    return result
```

Similarly for `check_company_background()` — add before the final `return`:

```python
def check_company_background(company, past_applications):
    # ... existing code ...

    # Record gate decision
    try:
        from jobpulse.job_db import JobDB
        decision = "generic" if is_generic else ("reapply" if previously_applied else "pass")
        JobDB().record_gate_decision("company_background", decision, note)
    except Exception:
        pass

    return CompanyBackgroundResult(...)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_gate4_wiring.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/jobpulse/test_gate4_wiring.py jobpulse/gate4_quality.py
git commit -m "feat(wiring): wire record_gate_decision into gate4 quality checks"
```

---

### Task 4: Expose `snapshot()` on OptimizationEngine facade and call from `optimize()`

`_tracker.snapshot()` exists at `shared/optimization/_tracker.py:108` but is only called from `PersonaEvolver`. The `performance_snapshots` table has zero production rows. The `optimize()` tick should snapshot key metrics each cycle.

**Files:**
- Create: `tests/shared/optimization/test_snapshot_wiring.py`
- Modify: `shared/optimization/_engine.py` (expose snapshot, call from optimize)

- [ ] **Step 1: Write the failing test**

```python
"""Tests proving optimize() calls snapshot() to populate performance_snapshots."""
import sqlite3

import pytest

from shared.optimization._engine import OptimizationEngine


@pytest.fixture
def opt_engine(tmp_path):
    db_path = str(tmp_path / "optimization.db")
    return OptimizationEngine(db_path=db_path)


def test_optimize_creates_snapshot(opt_engine):
    """optimize() must create at least one performance_snapshot per cycle."""
    # Emit some signals so there's data to snapshot
    opt_engine.emit("success", "form_experience", "greenhouse.io",
                    agent_name="form_filler", payload={"fields": 5})
    opt_engine.emit("correction", "correction_capture", "greenhouse.io",
                    agent_name="form_filler", payload={"field": "salary"})

    opt_engine.optimize()

    conn = sqlite3.connect(opt_engine._db_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM performance_snapshots"
    ).fetchone()["cnt"]
    conn.close()
    assert count >= 1, "optimize() must call snapshot() to record performance_snapshots"


def test_snapshot_exposed_on_facade(opt_engine):
    """OptimizationEngine must expose snapshot() as a public method."""
    snap = opt_engine.snapshot("test_loop", "test_domain", {"metric_a": 1.0})
    assert snap is not None
    assert snap.loop_name == "test_loop"
    assert snap.domain == "test_domain"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/optimization/test_snapshot_wiring.py -v`
Expected: FAIL — `optimize()` doesn't call `snapshot()`, and `snapshot()` on the facade is a no-op stub (line 531 returns None).

- [ ] **Step 3: Expose snapshot() and call from optimize()**

In `shared/optimization/_engine.py`:

1. Replace the no-op `snapshot` stub with a real delegation:

```python
def snapshot(self, loop_name: str, domain: str, metrics: dict):
    if not self._enabled:
        return None
    return self._tracker.snapshot(loop_name, domain, metrics)
```

2. In `optimize()`, after computing insights and before returning, add a snapshot of the cycle metrics:

```python
def optimize(self) -> dict:
    if not self._enabled:
        return {"insights": [], "actions": []}

    insights = self._aggregator.check_realtime()
    insights.extend(self._aggregator.check_regressions())
    insights.extend(self._aggregator.sweep())

    trajectory_insights = self._mine_trajectory_insights()
    insights.extend(trajectory_insights)

    all_actions: list[PolicyAction] = []
    for insight in insights:
        actions = self._policy.decide(insight)
        all_actions.extend(actions)

    self._action_counts["insights"] += len(insights)

    executed = self._execute_actions(all_actions)

    # Snapshot cycle metrics for performance_snapshots table
    try:
        cycle_metrics = {
            "insights_found": len(insights),
            "actions_executed": len(executed),
            "total_actions": len(all_actions),
        }
        self._tracker.snapshot("optimization_cycle", "global", cycle_metrics)
    except Exception as exc:
        logger.debug("optimize: snapshot failed: %s", exc)

    self.flush_sync()

    return {
        "insights": [...],  # existing code unchanged
        "actions": [...],   # existing code unchanged
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/optimization/test_snapshot_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/shared/optimization/test_snapshot_wiring.py shared/optimization/_engine.py
git commit -m "feat(wiring): expose snapshot() on OptimizationEngine, call from optimize()"
```

---

### Task 5: Wire `record_cognitive_outcome()` into CognitiveEngine.think()

`record_cognitive_outcome()` exists on `OptimizationEngine` (line 157) and `PerformanceTracker` (line 299), but only tests call it. `CognitiveEngine.think()` should call it after every execution to populate the `cognitive_outcomes` table.

**Files:**
- Create: `tests/shared/optimization/test_cognitive_wiring.py`
- Modify: `shared/cognitive/_engine.py` (call record_cognitive_outcome after think)

- [ ] **Step 1: Write the failing test**

```python
"""Tests proving CognitiveEngine.think() records cognitive outcomes."""
import asyncio
import sqlite3
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from shared.optimization._engine import OptimizationEngine


@pytest.fixture
def opt_engine(tmp_path):
    return OptimizationEngine(db_path=str(tmp_path / "optimization.db"))


@pytest.fixture
def mock_memory():
    mm = MagicMock()
    mm.get_procedural_entries.return_value = []
    mm.recall.return_value = []
    mm.search.return_value = []
    return mm


def test_think_records_cognitive_outcome(opt_engine, mock_memory, tmp_path):
    """CognitiveEngine.think() must call record_cognitive_outcome after execution."""
    from shared.cognitive._engine import CognitiveEngine, ThinkLevel

    engine = CognitiveEngine(memory_manager=mock_memory, agent_name="test_agent")

    with patch("shared.cognitive._engine._llm_generate", new_callable=AsyncMock,
               return_value="Test answer"), \
         patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        result = asyncio.run(engine.think(
            task="Test question",
            domain="test_domain",
            stakes="low",
            force_level=ThinkLevel.L1_SINGLE,
        ))

    conn = sqlite3.connect(opt_engine._db_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM cognitive_outcomes"
    ).fetchone()["cnt"]
    conn.close()
    assert count >= 1, "think() must record a cognitive outcome via OptimizationEngine"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/optimization/test_cognitive_wiring.py -v`
Expected: FAIL — `CognitiveEngine.think()` doesn't call `record_cognitive_outcome()`.

- [ ] **Step 3: Wire record_cognitive_outcome into CognitiveEngine.think()**

In `shared/cognitive/_engine.py`, after the level tracking at line 157 (the `self._record_level(level, result.cost)` call), add:

```python
        # Record cognitive outcome for optimization tracking
        try:
            from shared.optimization import get_optimization_engine
            success = result.score is not None and result.score >= 6.0
            get_optimization_engine().record_cognitive_outcome(
                domain=domain,
                agent_name=self._agent_name,
                level=level.value,
                success=success,
                escalated=result.escalated_from is not None,
            )
        except Exception:
            pass
```

This goes in two places: the normal return path (line ~158) and the escalation return path (line ~151).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/optimization/test_cognitive_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/shared/optimization/test_cognitive_wiring.py shared/cognitive/_engine.py
git commit -m "feat(wiring): CognitiveEngine.think() records cognitive outcomes"
```

---

### Task 6: Verify scan_learning wiring with job_scanners

`ScanLearningEngine` is already imported and used in `jobpulse/job_scanners/__init__.py` (line 15), `job_scanners/linkedin.py` (line 24), and `job_scanners/reed.py` (line 23). The `scan_events` table has data (scan_learning.db is 11MB). But `learned_rules` and `cooldowns` tables need verification.

**Files:**
- Create: `tests/jobpulse/test_scan_learning_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests proving ScanLearningEngine integration with job_scanners."""
import sqlite3

import pytest

from jobpulse.scan_learning import ScanLearningEngine


@pytest.fixture
def scan_engine(tmp_path):
    return ScanLearningEngine(db_path=str(tmp_path / "scan_learning.db"))


def test_record_event_creates_scan_event(scan_engine):
    """record_event must write to scan_events table."""
    event_id = scan_engine.record_event(
        platform="greenhouse",
        requests_in_session=5,
        avg_delay=3.0,
        session_age_seconds=120.0,
        user_agent_hash="abc123",
        was_fresh_session=True,
        used_vpn=False,
        simulated_mouse=True,
        referrer_chain="google.com",
        search_query="data analyst jobs",
        pages_before_block=3,
        browser_fingerprint="fp_001",
        waited_for_page_load=True,
        page_load_time_ms=1200,
        outcome="success",
    )
    assert event_id is not None

    conn = sqlite3.connect(scan_engine.db_path)
    count = conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]
    conn.close()
    assert count == 1


def test_blocked_event_emits_optimization_signal(scan_engine, tmp_path):
    """A blocked scan event must emit a failure signal to OptimizationEngine."""
    from shared.optimization._engine import OptimizationEngine
    from unittest.mock import patch

    opt_engine = OptimizationEngine(db_path=str(tmp_path / "optimization.db"))

    with patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        scan_engine.record_event(
            platform="linkedin",
            requests_in_session=10,
            avg_delay=1.5,
            session_age_seconds=300.0,
            user_agent_hash="def456",
            was_fresh_session=False,
            used_vpn=False,
            simulated_mouse=False,
            referrer_chain="direct",
            search_query="",
            pages_before_block=8,
            browser_fingerprint="fp_002",
            waited_for_page_load=True,
            page_load_time_ms=2000,
            outcome="blocked",
            wall_type="reCAPTCHA",
        )

    conn = sqlite3.connect(str(tmp_path / "optimization.db"))
    conn.row_factory = sqlite3.Row
    signals = conn.execute(
        "SELECT * FROM signals WHERE signal_type = 'failure' AND source_loop = 'scan_learning'"
    ).fetchall()
    conn.close()
    assert len(signals) >= 1, "blocked scan must emit failure signal to OptimizationEngine"


def test_handle_block_records_cooldown(scan_engine):
    """handle_block from job_scanners/__init__.py must write to cooldowns table."""
    from jobpulse.job_scanners import handle_block, SessionSignals
    from unittest.mock import MagicMock

    wall = MagicMock()
    wall.wall_type = "Turnstile"

    signals = SessionSignals(
        requests_in_session=5,
        avg_delay=2.0,
        session_age_seconds=180.0,
        user_agent_hash="hash1",
        was_fresh_session=True,
        used_vpn=False,
        simulated_mouse=True,
        pages_before_block=3,
        browser_fingerprint="fp",
        page_load_time_ms=1000,
    )

    handle_block(scan_engine, "indeed", wall, signals)

    conn = sqlite3.connect(scan_engine.db_path)
    conn.row_factory = sqlite3.Row
    cooldowns = conn.execute(
        "SELECT * FROM cooldowns WHERE platform = 'indeed'"
    ).fetchall()
    conn.close()
    assert len(cooldowns) >= 1, "handle_block must write to cooldowns table"
```

- [ ] **Step 2: Run test to verify it passes or identify gaps**

Run: `python -m pytest tests/jobpulse/test_scan_learning_wiring.py -v`
Expected: Tests 1-2 should PASS (record_event and signal emission already work). Test 3 (handle_block → cooldowns) verifies the wiring exists. If `SessionSignals` doesn't exist as a public class, adapt the import.

- [ ] **Step 3: Fix any failures**

If `handle_block` or `SessionSignals` don't match the actual API, read `jobpulse/job_scanners/__init__.py` and adapt the test to the real function signatures.

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_scan_learning_wiring.py
git commit -m "test(wiring): scan_learning integration with job_scanners and optimization"
```

---

### Task 7: Clean up dead 0-byte databases

13 databases in `data/` are 0 bytes with no tables and zero or near-zero code references. Remove the files and update `.gitignore` / documentation.

**Dead databases (0 bytes, no tables, no meaningful code refs):**
- `data/code_graph.db` — 0 references (code_intelligence.db replaced it)
- `data/gate_effectiveness.db` — 0 references (table lives in applications.db)
- `data/job_applications.db` — 0 references (applications.db is the real one)
- `data/job_autopilot.db` — 0 references
- `data/job_listings.db` — 0 references (table in applications.db)
- `data/job_tracker.db` — 0 references
- `data/jobs.db` — 0 references
- `data/screening_cache.db` — 0 references (screening_semantic_cache.db replaced it)
- `data/screening_cache_v2.db` — 0 references
- `data/strategy_reflector.db` — 0 references (strategy reflection writes to trajectory.db)

**Keep (have code references despite 0 bytes):**
- `data/company_reliability.db` — 1 reference (but table lives in applications.db, so the file is dead)
- `data/profile.db` — 1 reference (may be lazily initialized)
- `data/project_selection_outcomes.db` — 3 references (active code, just no data yet)

**Files:**
- Delete: 11 dead DB files
- Modify: `.gitignore` if these are tracked

- [ ] **Step 1: Verify each file is truly dead**

```bash
# Double-check zero references for each candidate
for db in code_graph gate_effectiveness job_applications job_autopilot job_listings job_tracker jobs screening_cache screening_cache_v2 strategy_reflector company_reliability; do
  echo "=== $db.db ==="
  grep -rl "$db" jobpulse/ shared/ tests/ scripts/ 2>/dev/null | head -5
done
```

- [ ] **Step 2: Check if files are tracked in git**

```bash
git ls-files data/*.db
```

- [ ] **Step 3: Delete dead databases**

```bash
rm -f data/code_graph.db data/gate_effectiveness.db data/job_applications.db \
      data/job_autopilot.db data/job_listings.db data/job_tracker.db data/jobs.db \
      data/screening_cache.db data/screening_cache_v2.db data/strategy_reflector.db \
      data/company_reliability.db
```

- [ ] **Step 4: If git-tracked, stage the deletion**

```bash
git rm --cached data/code_graph.db data/gate_effectiveness.db data/job_applications.db \
      data/job_autopilot.db data/job_listings.db data/job_tracker.db data/jobs.db \
      data/screening_cache.db data/screening_cache_v2.db data/strategy_reflector.db \
      data/company_reliability.db 2>/dev/null || true
```

- [ ] **Step 5: Run full test suite to verify no breakage**

Run: `python -m pytest tests/ -x -q --timeout=30`
Expected: No test references these dead databases.

- [ ] **Step 6: Commit**

```bash
git add -A data/
git commit -m "chore: remove 11 dead 0-byte databases with zero code references"
```

---

### Task 8: Update CLAUDE.md database wiring status

Update the documentation to reflect the new wiring state after all fixes.

**Files:**
- Modify: `CLAUDE.md` (update Database Wiring Status section)

- [ ] **Step 1: Update the database counts in CLAUDE.md**

Change the "Database Wiring Status" section to reflect:
- 22 active → now includes `application_outcomes`, `company_reliability`, `gate_effectiveness`, `performance_snapshots`, `cognitive_outcomes` (previously empty tables now wired)
- 21 wired but empty → reduced (5 tables now wired)
- 13 dead → now 2 (profile.db and project_selection_outcomes.db kept; 11 deleted)

- [ ] **Step 2: Run all new tests together**

```bash
python -m pytest tests/jobpulse/test_wiring_e2e.py tests/jobpulse/test_gate4_wiring.py tests/shared/optimization/test_snapshot_wiring.py tests/shared/optimization/test_cognitive_wiring.py tests/jobpulse/test_scan_learning_wiring.py -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update database wiring status after fixes"
```
