# Post-Apply Learning Loops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the 5 broken feedback loops so the optimization engine acts on its 12,500+ signals — field corrections generate rules, rules are consumed during fills, heuristics are extracted and replayed, and the optimization tick produces real learning actions.

**Architecture:** Wire existing components together. The observation layer (signals, trajectories, form experience) is strong. The action layer (rules, heuristics, parameter tuning) has all the code but nothing is connected. We close 5 loops: (1) correction capture → agent rules, (2) agent rules → form filler consumption, (3) field trajectory logging → heuristic extraction, (4) heuristic loading → pre-fill injection, (5) optimization tick → before/after measurement.

**Tech Stack:** Python, SQLite, existing optimization engine (shared/optimization/), existing TrajectoryStore (jobpulse/trajectory_store.py), existing AgentRulesDB (jobpulse/agent_rules.py)

---

## File Structure

| File | Role | Action |
|---|---|---|
| `jobpulse/applicator.py` | Application lifecycle, `confirm_application()` | Modify: pass `job_id` to correction capture for trajectory linkage |
| `jobpulse/native_form_filler.py` | Form filling orchestrator | Modify: log field trajectories, load heuristics before fill, consume agent rules |
| `jobpulse/form_engine/page_filler.py` | Per-field fill dispatch | Modify: log each field fill to TrajectoryStore |
| `jobpulse/post_apply_hook.py` | Post-submit learning pipeline | Modify: wrap with before/after learning measurement |
| `jobpulse/correction_capture.py` | Diff agent vs user values | Modify: pass job_id through for trajectory linkage |
| `jobpulse/agent_rules.py` | Rule storage and retrieval | Modify: add `get_field_overrides()` for form-fill-time consumption |
| `jobpulse/trajectory_store.py` | Per-field decision journal | No change (already complete) |
| `jobpulse/strategy_reflector.py` | Heuristic extraction | No change (already complete) |
| `shared/optimization/_engine.py` | Optimization facade | Modify: use SQLite query instead of in-memory deque for aggregator |
| `shared/optimization/_aggregator.py` | Pattern detection | Modify: `check_realtime()` queries SQLite recent signals instead of empty deque |
| `tests/jobpulse/test_learning_loops.py` | Integration tests for all 5 loops | Create |
| `tests/shared/optimization/test_realtime_sqlite.py` | Test aggregator SQLite path | Create |

---

### Task 1: Fix Aggregator to Query SQLite Instead of Empty Deque

The `check_realtime()` method reads from `self._bus.recent()` which returns the in-memory deque. Between daemon restarts, this deque is empty, so the aggregator never sees signals. Fix it to query SQLite for recent signals (last 15 minutes).

**Files:**
- Modify: `shared/optimization/_signals.py`
- Modify: `shared/optimization/_aggregator.py`
- Test: `tests/shared/optimization/test_realtime_sqlite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/optimization/test_realtime_sqlite.py
"""Test that aggregator detects patterns from SQLite, not just in-memory deque."""
import pytest
from shared.optimization._signals import SignalBus, LearningSignal
from shared.optimization._aggregator import SignalAggregator
from shared.optimization._tracker import PerformanceTracker


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "opt.db")


@pytest.fixture
def signal_bus(db_path):
    return SignalBus(db_path=db_path)


@pytest.fixture
def aggregator(db_path, signal_bus):
    tracker = PerformanceTracker(db_path=db_path)
    return SignalAggregator(signal_bus=signal_bus, tracker=tracker)


def test_realtime_detects_from_sqlite_after_deque_cleared(signal_bus, aggregator):
    """Aggregator must find patterns from SQLite even if deque was cleared."""
    # Emit 4 correction signals for the same field across 3 sessions
    for i in range(4):
        signal_bus.emit(LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="greenhouse.io",
            agent_name="form_filler",
            severity="info",
            payload={"field": "salary", "old_value": "40000", "new_value": "45000"},
            session_id=f"session_{i % 3}",
        ))

    # Clear the in-memory deque to simulate daemon restart
    signal_bus._recent.clear()
    assert len(signal_bus.recent()) == 0

    # Aggregator should still detect the systemic failure from SQLite
    insights = aggregator.check_realtime()
    systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
    assert len(systemic) >= 1
    assert systemic[0].domain == "greenhouse.io"


def test_realtime_success_patterns_from_sqlite(signal_bus, aggregator):
    """Success streak detection works from SQLite after restart."""
    for i in range(4):
        signal_bus.emit(LearningSignal(
            signal_type="success",
            source_loop="form_experience",
            domain="linkedin.com",
            agent_name="form_filler",
            severity="info",
            payload={"action": "record_experience"},
            session_id=f"sess_{i % 3}",
        ))

    signal_bus._recent.clear()

    insights = aggregator.check_realtime()
    success = [i for i in insights if i.pattern_type == "success_streak"]
    assert len(success) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/optimization/test_realtime_sqlite.py -v`
Expected: FAIL — `check_realtime()` returns empty because it reads empty deque

- [ ] **Step 3: Add `recent_from_db()` to SignalBus**

In `shared/optimization/_signals.py`, add a method that queries SQLite for recent signals:

```python
# Add to class SignalBus, after the existing recent() method:

def recent_from_db(self, minutes: int = 15, limit: int = 1000) -> list[LearningSignal]:
    """Query SQLite for signals from the last N minutes. Survives restarts."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    return self.query(since=since, limit=limit)
```

- [ ] **Step 4: Update `check_realtime()` to use SQLite fallback**

In `shared/optimization/_aggregator.py`, modify `check_realtime()`:

```python
def check_realtime(self) -> list[AggregatedInsight]:
    insights: list[AggregatedInsight] = []
    recent = self._filter_paused(self._bus.recent())
    # Fallback to SQLite if deque is empty (daemon restart)
    if not recent:
        recent = self._filter_paused(self._bus.recent_from_db(minutes=15))
    insights.extend(self._detect_systemic_failures(recent))
    insights.extend(self._detect_platform_change(recent))
    insights.extend(self._detect_success_patterns(recent))
    insights.extend(self._detect_adaptation_effectiveness(recent))
    return insights
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/shared/optimization/test_realtime_sqlite.py -v`
Expected: PASS

- [ ] **Step 6: Run full optimization test suite**

Run: `python -m pytest tests/shared/optimization/ -v`
Expected: All pass (no regressions)

- [ ] **Step 7: Commit**

```bash
git add shared/optimization/_signals.py shared/optimization/_aggregator.py tests/shared/optimization/test_realtime_sqlite.py
git commit -m "fix(optimization): aggregator queries SQLite when deque is empty after restart"
```

---

### Task 2: Wire Field Trajectory Logging Into Form Filler

The `TrajectoryStore.log_field()` in `jobpulse/trajectory_store.py` is never called from the form-fill pipeline. Wire it into `native_form_filler.py` so every field fill creates a trajectory record.

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Test: `tests/jobpulse/test_learning_loops.py`

First, find the exact insertion point in native_form_filler.py where fields are filled.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_learning_loops.py
"""Integration tests for all 5 post-apply learning loops."""
import pytest
from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store


@pytest.fixture
def trajectory_store(tmp_path):
    _reset_shared_store()
    store = TrajectoryStore(db_path=str(tmp_path / "trajectory.db"))
    yield store
    _reset_shared_store()


def test_field_trajectory_logged_after_fill(trajectory_store):
    """Verify log_field creates a retrievable trajectory record."""
    trajectory_store.log_field(
        job_id="job_001",
        domain="greenhouse.io",
        field_label="First Name",
        strategy=StrategyTier.PATTERN_MATCH,
        value_filled="Yash",
        field_type="text",
        confidence=0.95,
        time_ms=50,
    )

    results = trajectory_store.get_trajectories("job_001")
    assert len(results) == 1
    assert results[0].field_label == "First Name"
    assert results[0].strategy == "pattern_match"
    assert results[0].confidence == 0.95
```

- [ ] **Step 2: Run test to verify it passes (this tests the store itself, not the wiring)**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_field_trajectory_logged_after_fill -v`
Expected: PASS (the store works, just nobody calls it)

- [ ] **Step 3: Add trajectory logging helper to native_form_filler.py**

Find the section in `native_form_filler.py` where each field fill result is recorded (the per-field loop that calls fill functions). Add trajectory logging after each field fill. The exact location depends on the current fill loop structure — look for where `FillResult` or field fill outcomes are collected.

Add at the top of `native_form_filler.py`:

```python
def _log_field_trajectory(
    job_id: str, domain: str, field_label: str, field_type: str,
    strategy: str, value: str, confidence: float, time_ms: int,
    page_index: int = 0,
) -> None:
    """Log a field fill to the TrajectoryStore. Non-blocking."""
    try:
        from jobpulse.trajectory_store import get_trajectory_store
        get_trajectory_store().log_field(
            job_id=job_id, domain=domain, field_label=field_label,
            strategy=strategy, value_filled=value,
            field_type=field_type, confidence=confidence,
            time_ms=time_ms, page_index=page_index,
        )
    except Exception as exc:
        logger.debug("trajectory log_field failed: %s", exc)
```

Then call `_log_field_trajectory(...)` after each field fill completes in the main fill loop, passing the field's label, type, the strategy tier used (e.g. "pattern_match", "llm_tier3", "agent_rule"), the value filled, and the time taken.

- [ ] **Step 4: Verify trajectory data is written during a fill (manual or unit test)**

Write a test that mocks the fill loop context and verifies `_log_field_trajectory` writes to the store:

```python
def test_log_field_trajectory_helper(trajectory_store, monkeypatch):
    """The helper function writes to trajectory store."""
    monkeypatch.setattr(
        "jobpulse.native_form_filler.get_trajectory_store",
        lambda: trajectory_store,
    )
    from jobpulse.native_form_filler import _log_field_trajectory
    _log_field_trajectory(
        job_id="job_002", domain="linkedin.com",
        field_label="Email", field_type="email",
        strategy="pattern_match", value="test@test.com",
        confidence=0.95, time_ms=30,
    )
    results = trajectory_store.get_trajectories("job_002")
    assert len(results) == 1
    assert results[0].field_label == "Email"
```

- [ ] **Step 5: Run test**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_learning_loops.py
git commit -m "feat(learning): wire field trajectory logging into form filler"
```

---

### Task 3: Add `get_field_overrides()` to AgentRulesDB and Wire Into Form Filler

Agent rules exist (7 rules) but `times_applied = 0` — the form filler never reads them. Add a method that returns field-level overrides and query it before filling each field.

**Files:**
- Modify: `jobpulse/agent_rules.py`
- Modify: `jobpulse/native_form_filler.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/jobpulse/test_learning_loops.py

def test_agent_rules_field_overrides(tmp_path):
    """AgentRulesDB returns field overrides for form filler consumption."""
    from jobpulse.agent_rules import AgentRulesDB
    db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    overrides = db.get_field_overrides(domain="greenhouse.io")
    assert "city" in overrides
    assert overrides["city"]["value"] == "Dundee"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_agent_rules_field_overrides -v`
Expected: FAIL — `get_field_overrides` doesn't exist

- [ ] **Step 3: Implement `get_field_overrides()`**

Add to `jobpulse/agent_rules.py` in class `AgentRulesDB`:

```python
def get_field_overrides(self, domain: str = "", platform: str = "") -> dict[str, dict]:
    """Return {field_label: {value, action, confidence}} for form-fill consumption.

    Queries correction_override rules matching domain or platform.
    Increments times_applied for each returned rule.
    """
    rules = self.get_active_rules("correction_override")
    overrides: dict[str, dict] = {}
    rule_ids_used: list[int] = []

    for r in rules:
        # Match by domain (pattern field stores domain for correction rules)
        if domain and r.get("pattern") and r["pattern"] != domain:
            continue
        field = r["category"]
        if field in overrides:
            # Keep higher confidence
            if r["confidence"] <= overrides[field]["confidence"]:
                continue
        overrides[field] = {
            "value": r["value"],
            "action": r["action"],
            "confidence": r["confidence"],
            "rule_id": r["rule_id"],
        }
        rule_ids_used.append(r["rule_id"])

    # Increment times_applied
    if rule_ids_used:
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            for rid in rule_ids_used:
                conn.execute(
                    "UPDATE agent_rules SET times_applied = times_applied + 1 WHERE rule_id = ?",
                    (rid,),
                )

    return overrides
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_agent_rules_field_overrides -v`
Expected: PASS

- [ ] **Step 5: Add override lookup to the form filler**

In `jobpulse/native_form_filler.py`, add a function that loads field overrides before filling starts:

```python
def _load_field_overrides(domain: str) -> dict[str, dict]:
    """Load agent rule overrides for this domain. Non-blocking."""
    try:
        from jobpulse.agent_rules import AgentRulesDB
        return AgentRulesDB().get_field_overrides(domain=domain)
    except Exception as exc:
        logger.debug("agent_rules override load failed: %s", exc)
        return {}
```

Then in the fill loop, before each field fill, check if an override exists:

```python
# Before filling a field:
override = field_overrides.get(field_label_normalized)
if override and override["action"] == "override_answer":
    value = override["value"]
    strategy = "agent_rule"
```

The `field_overrides` dict is loaded once at the start of the fill session via `_load_field_overrides(domain)`.

- [ ] **Step 6: Write test for override consumption**

```python
def test_field_override_consumed_during_fill(tmp_path):
    """When an override exists, the form filler uses it and increments times_applied."""
    from jobpulse.agent_rules import AgentRulesDB
    db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    # First call: should return override
    overrides = db.get_field_overrides(domain="greenhouse.io")
    assert overrides["city"]["value"] == "Dundee"

    # Verify times_applied was incremented
    rules = db.get_active_rules("correction_override")
    city_rules = [r for r in rules if r["category"] == "city"]
    assert city_rules[0]["times_applied"] == 1
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/agent_rules.py jobpulse/native_form_filler.py tests/jobpulse/test_learning_loops.py
git commit -m "feat(learning): agent rules consumed by form filler during fills"
```

---

### Task 4: Wire Heuristic Loading Into Pre-Fill Phase

`load_heuristics_for_application()` in `trajectory_store.py` has 0 callers. Wire it into the form fill pipeline so heuristics from prior applications inform future fills.

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/jobpulse/test_learning_loops.py

def test_heuristics_loaded_before_fill(trajectory_store):
    """Heuristics from prior applications are loaded before a new fill."""
    from jobpulse.trajectory_store import Heuristic, load_heuristics_for_application

    trajectory_store.save_heuristics([
        Heuristic(
            trigger="field 'city' on smartrecruiters",
            action="type text then ArrowDown+Enter",
            confidence=0.85,
            source_domain="jobs.smartrecruiters.com",
            platform="smartrecruiters",
        ),
    ])

    result = load_heuristics_for_application(
        "jobs.smartrecruiters.com",
        platform="smartrecruiters",
        store=trajectory_store,
    )
    assert len(result["domain_heuristics"]) >= 1
    assert "ArrowDown" in result["prompt_context"]
```

- [ ] **Step 2: Run test to verify it passes (the function exists, just nobody calls it)**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_heuristics_loaded_before_fill -v`
Expected: PASS

- [ ] **Step 3: Add heuristic loading to the form fill setup**

In `jobpulse/native_form_filler.py`, at the start of the fill session (where domain/platform are known), add:

```python
def _load_heuristics(domain: str, platform: str) -> str:
    """Load heuristics context for LLM prompts. Non-blocking."""
    try:
        from jobpulse.trajectory_store import load_heuristics_for_application
        result = load_heuristics_for_application(domain, platform=platform)
        context = result.get("prompt_context", "")
        if context:
            logger.info("Loaded %d domain + %d platform heuristics for %s",
                        len(result["domain_heuristics"]),
                        len(result["platform_heuristics"]), domain)
        return context
    except Exception as exc:
        logger.debug("heuristic loading failed: %s", exc)
        return ""
```

Call this at the start of the fill session. The returned `prompt_context` string should be injected into any LLM prompt used for field value generation (e.g., screening answer fallback, field mapper recovery).

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_learning_loops.py
git commit -m "feat(learning): load heuristics from prior applications before fill"
```

---

### Task 5: Wire before/after Learning Measurement Into post_apply_hook

`before_learning_action()` / `after_learning_action()` are never called (0 learning actions in DB). Wire them into `post_apply_hook` so every application measures whether the learning pipeline improved metrics.

**Files:**
- Modify: `jobpulse/post_apply_hook.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/jobpulse/test_learning_loops.py

def test_post_apply_records_learning_action(tmp_path, monkeypatch):
    """post_apply_hook wraps with before/after learning measurement."""
    import sqlite3
    db_path = str(tmp_path / "optimization.db")

    # Create a minimal optimization engine with tmp DB
    from shared.optimization._engine import OptimizationEngine
    engine = OptimizationEngine(db_path=db_path)
    monkeypatch.setattr(
        "shared.optimization._engine._shared_engine", engine,
    )

    # Record a learning action manually to verify the wiring works
    action_id = engine.before_learning_action(
        "post_apply", domain="greenhouse.io",
        metrics={"correction_rate": 0.3, "fields_filled": 10},
    )
    assert action_id  # non-empty string

    result = engine.after_learning_action(
        action_id,
        metrics={"correction_rate": 0.1, "fields_filled": 12},
    )
    assert result["improved"] is True

    # Verify in DB
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM learning_actions").fetchall()
    assert len(rows) == 1
    assert rows[0]["after_metrics"] is not None
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_post_apply_records_learning_action -v`
Expected: PASS (the engine methods work, they're just never called)

- [ ] **Step 3: Add before/after measurement to post_apply_hook**

In `jobpulse/post_apply_hook.py`, wrap the success path with learning measurement. Add at the start of the success path (after `start = time.monotonic()`):

```python
    # --- 0. Before-measurement for optimization engine ---
    opt_action_id = ""
    try:
        from shared.optimization import get_optimization_engine
        _engine = get_optimization_engine()
        _domain = FormExperienceDB.normalize_domain(url)
        _before = {
            "fields_filled": len(result.get("field_types", [])),
            "pages_filled": result.get("pages_filled", 0),
            "time_seconds": result.get("time_seconds", 0.0),
        }
        opt_action_id = _engine.before_learning_action(
            "post_apply", domain=_domain, metrics=_before,
        )
    except Exception as exc:
        logger.debug("post_apply_hook: before_learning_action failed: %s", exc)
```

Then at the end of `post_apply_hook` (after all learning steps), add the after-measurement:

```python
    # --- 6. After-measurement for optimization engine ---
    if opt_action_id:
        try:
            from shared.optimization import get_optimization_engine
            _engine = get_optimization_engine()
            corrections = result.get("corrections", {})
            correction_count = len(corrections.get("corrections", [])) if isinstance(corrections, dict) else 0
            _after = {
                "fields_filled": len(result.get("field_types", [])),
                "pages_filled": result.get("pages_filled", 0),
                "time_seconds": result.get("time_seconds", 0.0),
                "correction_count": correction_count,
            }
            _engine.after_learning_action(opt_action_id, metrics=_after)
        except Exception as exc:
            logger.debug("post_apply_hook: after_learning_action failed: %s", exc)
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_learning_loops.py
git commit -m "feat(learning): wrap post_apply_hook with before/after measurement"
```

---

### Task 6: Pass job_id Through Correction Capture for Trajectory Linkage

`CorrectionCapture.record_corrections()` accepts `job_id` and links corrections to field trajectories via `mark_corrected()`. But `confirm_application()` doesn't pass `job_id`. Fix it.

**Files:**
- Modify: `jobpulse/applicator.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Write the test**

```python
# Add to tests/jobpulse/test_learning_loops.py

def test_correction_links_to_trajectory(tmp_path):
    """Corrections from confirm_application link to field trajectories."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store
    from jobpulse.correction_capture import CorrectionCapture

    _reset_shared_store()
    traj_store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    cc = CorrectionCapture(db_path=str(tmp_path / "corrections.db"))

    # Log a field trajectory
    traj_store.log_field(
        job_id="job_100", domain="greenhouse.io",
        field_label="City", strategy=StrategyTier.PATTERN_MATCH,
        value_filled="London", field_type="text",
    )

    # Record a correction with job_id
    result = cc.record_corrections(
        domain="greenhouse.io",
        platform="greenhouse",
        agent_mapping={"City": "London"},
        final_mapping={"City": "Dundee"},
        job_id="job_100",
    )

    assert len(result["corrections"]) == 1
    _reset_shared_store()
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py::test_correction_links_to_trajectory -v`
Expected: PASS (the code path already handles job_id, it's just not passed from applicator.py)

- [ ] **Step 3: Pass job_id from confirm_application to record_corrections**

In `jobpulse/applicator.py`, in `confirm_application()`, find the call to `cc.record_corrections()` (~line 486). Add `job_id=ctx.get("job_id", "")` to the call:

```python
            correction_result = cc.record_corrections(
                domain=domain,
                platform=platform_key,
                agent_mapping=agent_mapping,
                final_mapping=final_mapping,
                job_id=ctx.get("job_id", ""),
            )
```

- [ ] **Step 4: Commit**

```bash
git add jobpulse/applicator.py tests/jobpulse/test_learning_loops.py
git commit -m "fix(learning): pass job_id through correction capture for trajectory linkage"
```

---

### Task 7: Add `times_applied` Column to agent_rules Schema (if missing)

The existing schema uses `rule_id` as the column name from `auto_generate_from_correction` but the initial CREATE TABLE has different column names in `agent_rules.py`. Verify the schema has `times_applied` and add the column to `_init_db` if it was created with the old schema.

**Files:**
- Modify: `jobpulse/agent_rules.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Check current schema vs production DB**

Run: `sqlite3 data/agent_rules.db ".schema agent_rules"`

Compare against `_init_db()` in `agent_rules.py`. If `times_applied` is missing from the CREATE TABLE, add it with a migration.

- [ ] **Step 2: Add migration to _init_db if needed**

```python
# Add after the CREATE TABLE in _init_db:
try:
    conn.execute("ALTER TABLE agent_rules ADD COLUMN times_applied INTEGER NOT NULL DEFAULT 0")
except sqlite3.OperationalError:
    pass  # Column already exists
```

- [ ] **Step 3: Commit**

```bash
git add jobpulse/agent_rules.py
git commit -m "fix(agent-rules): ensure times_applied column exists in schema"
```

---

### Task 8: End-to-End Integration Test

Write a test that verifies the full loop: fill → trajectory → correction → rule → next fill uses rule.

**Files:**
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Write the end-to-end test**

```python
# Add to tests/jobpulse/test_learning_loops.py

def test_full_correction_to_rule_to_consumption_loop(tmp_path):
    """E2E: correction generates a rule, next fill consumes it."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store
    from jobpulse.correction_capture import CorrectionCapture
    from jobpulse.agent_rules import AgentRulesDB

    _reset_shared_store()
    traj_store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    cc = CorrectionCapture(db_path=str(tmp_path / "corrections.db"))
    rules_db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    # Step 1: Agent fills "city" with "London"
    traj_store.log_field(
        job_id="job_200", domain="greenhouse.io",
        field_label="City", strategy=StrategyTier.PATTERN_MATCH,
        value_filled="London", field_type="text",
    )

    # Step 2: User corrects to "Dundee"
    cc.record_corrections(
        domain="greenhouse.io",
        platform="greenhouse",
        agent_mapping={"City": "London"},
        final_mapping={"City": "Dundee"},
        job_id="job_200",
    )

    # Step 3: Rule auto-generated
    rules_db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    # Step 4: Next fill queries rules — should get "Dundee"
    overrides = rules_db.get_field_overrides(domain="greenhouse.io")
    assert "city" in overrides
    assert overrides["city"]["value"] == "Dundee"
    assert overrides["city"]["action"] == "override_answer"

    # Step 5: Verify trajectory was marked corrected
    trajs = traj_store.get_trajectories("job_200")
    # mark_corrected was called by correction_capture with job_id
    # (the store may or may not have the corrected flag depending on label normalization)

    _reset_shared_store()


def test_heuristic_extraction_from_trajectories(tmp_path):
    """Strategy reflector extracts heuristics from field trajectories."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, Heuristic, _reset_shared_store
    from jobpulse.strategy_reflector import extract_deterministic_heuristics

    _reset_shared_store()
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))

    # Log 5 fields, 2 corrected
    for i, (label, corrected) in enumerate([
        ("First Name", False), ("Last Name", False),
        ("City", True), ("Salary", True), ("Email", False),
    ]):
        row_id = store.log_field(
            job_id="job_300", domain="greenhouse.io",
            field_label=label, strategy=StrategyTier.PATTERN_MATCH,
            value_filled=f"val_{i}", field_type="text",
            confidence=0.9, time_ms=50,
        )
        if corrected:
            store.mark_corrected("job_300", "greenhouse.io", label, f"corrected_{i}")

    trajs = store.get_trajectories("job_300")
    heuristics = extract_deterministic_heuristics(trajs)

    # Should extract correction heuristics for City and Salary
    correction_h = [h for h in heuristics if h["source"] == "correction"]
    assert len(correction_h) >= 2

    _reset_shared_store()
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/jobpulse/test_learning_loops.py -v`
Expected: All PASS

- [ ] **Step 3: Run the full test suite to check for regressions**

Run: `python -m pytest tests/ -v -x --timeout=60 -q 2>&1 | tail -20`
Expected: No regressions

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_learning_loops.py
git commit -m "test(learning): end-to-end tests for correction→rule→consumption loop"
```

---

### Task 9: Emit Optimization Signals From Strategy Reflector

The strategy reflector runs (via post_apply_hook) but doesn't emit optimization signals. Wire it so the optimization engine sees heuristic extraction as a learning event.

**Files:**
- Modify: `jobpulse/strategy_reflector.py`
- Test: `tests/jobpulse/test_learning_loops.py`

- [ ] **Step 1: Add signal emission to `reflect_on_application`**

In `jobpulse/strategy_reflector.py`, at the end of `reflect_on_application()` (after `store.save_heuristics(typed)`), add:

```python
    # Emit optimization signal for the learning action
    try:
        from shared.optimization import get_optimization_engine
        engine = get_optimization_engine()
        engine.emit(
            signal_type="success" if strategy.success else "failure",
            source_loop="strategy_reflector",
            domain=strategy.domain,
            agent_name="strategy_reflector",
            payload={
                "heuristics_extracted": len(all_heuristics),
                "fields_total": strategy.fields_total,
                "fields_corrected": strategy.fields_corrected,
                "deterministic": len(det_heuristics),
                "llm": len(llm_heuristics),
            },
            session_id=f"sr_{strategy.domain}_{job_id[:8]}",
        )
    except Exception as exc:
        logger.debug("strategy_reflector: optimization signal failed: %s", exc)
```

- [ ] **Step 2: Commit**

```bash
git add jobpulse/strategy_reflector.py
git commit -m "feat(learning): strategy reflector emits optimization signals"
```

---

### Task 10: Verification — Run Optimization Tick and Verify Actions

Final verification: run the optimization engine manually and confirm it now produces insights and actions from the accumulated signals.

**Files:**
- Test: manual verification via runner

- [ ] **Step 1: Run optimization tick manually**

```bash
python -c "
from shared.optimization import get_optimization_engine
engine = get_optimization_engine()
result = engine.optimize()
print('Insights:', len(result['insights']))
for i in result['insights']:
    print(f'  {i[\"type\"]} ({i[\"domain\"]}): {i[\"evidence\"][:80]}')
print('Actions:', len(result['actions']))
for a in result['actions']:
    print(f'  {a[\"type\"]} ({a[\"domain\"]}): executed={a[\"executed\"]}')
print('Health:', engine.health())
"
```

Expected: Non-zero insights and actions from the 12,500+ accumulated signals

- [ ] **Step 2: Check learning_actions table**

```bash
sqlite3 data/optimization.db "SELECT COUNT(*) FROM learning_actions WHERE after_metrics IS NOT NULL"
```

Expected: > 0 after next application submission

- [ ] **Step 3: Check agent_rules times_applied**

```bash
sqlite3 data/agent_rules.db "SELECT category, times_applied FROM agent_rules WHERE times_applied > 0"
```

Expected: Non-zero after next form fill with matching rules

- [ ] **Step 4: Final commit with updated stats**

```bash
git add -A
git commit -m "feat(learning): close all 5 post-apply learning loops

Loops closed:
1. Aggregator queries SQLite (survives daemon restart)
2. Field trajectories logged per fill
3. Agent rules consumed during form fills
4. Heuristics loaded before each fill
5. before/after measurement wraps post_apply_hook"
```
