# Rate Limiting & Cost Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade rate limiting and cost tracking from 8.0/10 to 9.5+/10 by fixing five gaps: LLM attribution, application audit trail, cost reporting, proactive alerts, and DB retention.

**Architecture:** Thread `agent_name` through the full LLM call stack (`get_llm()` -> `_InstrumentedLLM` -> `record_llm_usage()`), add `record_openai_usage()` for raw OpenAI SDK paths, extend `RateLimiter` with audit trail + alerts, surface cost data in briefings, and wire cleanup to the daemon tick.

**Tech Stack:** Python, SQLite, pytest, shared/cost_tracker.py, shared/agents.py, shared/streaming.py, jobpulse/rate_limiter.py

---

## File Structure

| File | Responsibility | Action |
|------|---------------|--------|
| `shared/agents.py` | `_InstrumentedLLM`, `get_llm()` factory | Modify: pass `agent_name` through all 3 return paths |
| `shared/streaming.py` | `smart_llm_call()` | Modify: resolve `agent_name` from `llm._agent_name` |
| `shared/cost_tracker.py` | Cost tracking + telemetry | Modify: add `record_openai_usage()`, `get_daily_llm_summary()`, `cleanup_old_usage()` |
| `jobpulse/utils/safe_io.py` | `safe_openai_call()` wrapper | Modify: add cost tracking after API call |
| `jobpulse/rate_limiter.py` | Quota tracking | Modify: add `application_log` table, extend `record_application()`, add alerts, add `cleanup_old()` |
| `jobpulse/applicator.py` | Application engine | Modify: pass job context to `record_application()` |
| `jobpulse/morning_briefing.py` | Daily digest | Modify: add LLM cost section |
| `jobpulse/weekly_report.py` | Weekly digest | Modify: add cost trend section |
| `jobpulse/multi_bot_listener.py` | Daemon tick | Modify: wire cleanup to optimization tick |
| `jobpulse/page_analyzer.py` | Vision page detection | Modify: add `record_openai_usage()` after call |
| `jobpulse/screening_answers.py` | Screening answers | Modify: add `agent_name` to `get_llm()` + `record_openai_usage()` to direct call |
| `jobpulse/budget_nlp.py` | Budget classification | Modify: add `record_openai_usage()` after call |
| `jobpulse/conversation.py` | Chat agent | Modify: add `record_openai_usage()` after call |
| `jobpulse/form_engine/field_mapper.py` | Form recovery | Modify: add `record_openai_usage()` after call |
| `jobpulse/persona_evolution.py` | Persona optimizer | Modify: add `agent_name` to `get_llm()` + `record_openai_usage()` to direct calls |
| `jobpulse/gmail_agent.py` | Email classifier | Modify: add `agent_name` to `get_llm()` |
| `jobpulse/portfolio_variants.py` | Portfolio bullets | Modify: add `agent_name` to `get_llm()` |
| `jobpulse/strategy_reflector.py` | Strategy reflection | Modify: add `agent_name` to `get_llm()` |
| `shared/experiential_learning.py` | GRPO variants | Modify: add `agent_name` to `get_llm()` |
| `tests/test_rate_limiter.py` | Rate limiter tests | Modify: add 5 new tests |
| `tests/shared/test_cost_tracker_ledger.py` | Cost tracker tests | Modify: add 5 new tests |
| `tests/jobpulse/test_safe_io.py` | safe_openai_call tests | Modify: add 1 new test |
| `tests/shared/test_llm_attribution.py` | Attribution tests | Create: 2 new tests |

---

### Task 1: Core LLM Attribution — `get_llm()` body

**Files:**
- Modify: `shared/agents.py:334-356`
- Test: `tests/shared/test_llm_attribution.py`

The `get_llm()` signature already has `agent_name` and `_InstrumentedLLM.__init__` already accepts it. But the 3 return paths in `get_llm()` body don't pass `agent_name` to the `_InstrumentedLLM` constructor.

- [ ] **Step 1: Write failing test**

Create `tests/shared/test_llm_attribution.py`:

```python
"""Tests for LLM agent_name attribution through the call stack."""

import os
import pytest


@pytest.fixture(autouse=True)
def _force_openai_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    # Reset provider cache
    import shared.agents as agents_mod
    agents_mod._LLM_PROVIDER = None
    agents_mod._is_local = None
    agents_mod._use_fallback_models = None
    yield
    agents_mod._LLM_PROVIDER = None
    agents_mod._is_local = None
    agents_mod._use_fallback_models = None


def test_get_llm_passes_agent_name():
    """get_llm() should thread agent_name into the returned _InstrumentedLLM."""
    from shared.agents import get_llm
    llm = get_llm(agent_name="email_classifier")
    assert hasattr(llm, "_agent_name")
    assert llm._agent_name == "email_classifier"


def test_get_llm_defaults_to_unknown():
    """get_llm() without agent_name should default to 'unknown'."""
    from shared.agents import get_llm
    llm = get_llm()
    assert llm._agent_name == "unknown"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_llm_attribution.py -v`
Expected: `test_get_llm_passes_agent_name` FAILS — `_agent_name` is `"unknown"` because `get_llm()` body doesn't pass it through.

- [ ] **Step 3: Fix `get_llm()` body — pass `agent_name` on all 3 return paths**

In `shared/agents.py`, replace the 3 `_InstrumentedLLM(...)` return statements:

```python
# Line 335-338 (local path):
    if _is_local:
        return _InstrumentedLLM(
            _make_local_llm(temperature, model, timeout, max_tokens),
            model_hint=model,
            agent_name=agent_name,
        )

# Line 354-355 (single provider):
    if len(chain) == 1:
        return _InstrumentedLLM(chain[0], model_hint=model, agent_name=agent_name)

# Line 356 (multi-provider):
    return _InstrumentedLLM(_MultiProviderLLM(chain), model_hint=model, agent_name=agent_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/test_llm_attribution.py -v`
Expected: PASS — both tests green.

- [ ] **Step 5: Commit**

```bash
git add shared/agents.py tests/shared/test_llm_attribution.py
git commit -m "feat(cost): thread agent_name through get_llm() to _InstrumentedLLM"
```

---

### Task 2: `smart_llm_call()` Agent Name Resolution

**Files:**
- Modify: `shared/streaming.py:266-269`
- Test: `tests/shared/test_llm_attribution.py`

`smart_llm_call()` at line 269 hardcodes `agent_name="unknown"` for the streaming branch. Fix it to resolve from the LLM instance.

- [ ] **Step 1: Write failing test**

Append to `tests/shared/test_llm_attribution.py`:

```python
def test_smart_llm_call_resolves_agent_name(monkeypatch, tmp_path):
    """smart_llm_call() should pick up agent_name from _InstrumentedLLM."""
    import sqlite3
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    monkeypatch.setenv("STREAM_LLM_OUTPUT", "")  # non-streaming path

    from shared.agents import _InstrumentedLLM
    from shared.streaming import smart_llm_call
    from langchain_core.messages import HumanMessage, SystemMessage
    from shared.logging_config import set_run_id, set_trajectory_id, clear_trajectory_id

    set_run_id("test_run")
    set_trajectory_id("test_traj")

    class _FakeLLM:
        def invoke(self, messages, **kwargs):
            resp = type("R", (), {
                "content": "test response",
                "response_metadata": {
                    "model_name": "gpt-4o-mini",
                    "token_usage": {"prompt_tokens": 50, "completion_tokens": 20},
                },
            })()
            return resp

    try:
        fake = _FakeLLM()
        instrumented = _InstrumentedLLM(fake, model_hint="gpt-4o-mini", agent_name="screening_answers")
        response = smart_llm_call(instrumented, [HumanMessage(content="test")])
    finally:
        clear_trajectory_id()

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute("SELECT agent_name FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "screening_answers"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_llm_attribution.py::test_smart_llm_call_resolves_agent_name -v`
Expected: FAIL — `row[0]` is `"unknown"` because `smart_llm_call` hardcodes it.

- [ ] **Step 3: Fix `smart_llm_call()` streaming branch**

In `shared/streaming.py`, change line 269:

```python
# Before:
                agent_name="unknown",
# After:
                agent_name=getattr(llm, "_agent_name", "unknown"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/test_llm_attribution.py -v`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add shared/streaming.py tests/shared/test_llm_attribution.py
git commit -m "feat(cost): smart_llm_call resolves agent_name from _InstrumentedLLM"
```

---

### Task 3: `record_openai_usage()` Helper

**Files:**
- Modify: `shared/cost_tracker.py` (add function after `record_llm_usage`)
- Test: `tests/shared/test_cost_tracker_ledger.py`

New function for raw OpenAI SDK responses (used by `safe_openai_call()` and 6 direct call sites).

- [ ] **Step 1: Write failing test**

Append to `tests/shared/test_cost_tracker_ledger.py`:

```python
def test_record_openai_usage(monkeypatch, tmp_path):
    """record_openai_usage persists raw OpenAI SDK response to llm_calls."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_openai_test")
    set_trajectory_id("traj_openai_1")

    from shared.cost_tracker import record_openai_usage

    # Simulate a real gpt-4o-mini response
    response = type("ChatCompletion", (), {
        "usage": type("Usage", (), {
            "prompt_tokens": 150,
            "completion_tokens": 45,
        })(),
        "model": "gpt-4o-mini-2024-07-18",
        "choices": [type("Choice", (), {
            "message": type("Msg", (), {"content": "test output"})(),
        })()],
    })()

    try:
        result = record_openai_usage(response, agent_name="gate4", model_hint="gpt-4o-mini")
    finally:
        clear_trajectory_id()

    assert result["agent"] == "gate4"
    assert result["prompt_tokens"] == 150
    assert result["completion_tokens"] == 45
    assert result["cost_usd"] > 0

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute(
        "SELECT agent_name, model, prompt_tokens, completion_tokens, cost_usd "
        "FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == "gate4"
    assert row[1] == "gpt-4o-mini-2024-07-18"
    assert row[2] == 150
    assert row[3] == 45
    assert row[4] > 0


def test_record_openai_usage_missing_usage(monkeypatch, tmp_path):
    """record_openai_usage handles responses with usage=None (API timeout edge case)."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_no_usage")
    set_trajectory_id("traj_no_usage")

    from shared.cost_tracker import record_openai_usage

    response = type("ChatCompletion", (), {
        "usage": None,
        "model": "gpt-4o-mini",
        "choices": [],
    })()

    try:
        result = record_openai_usage(response, agent_name="gate4", model_hint="gpt-4o-mini")
    finally:
        clear_trajectory_id()

    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["cost_usd"] == 0.0

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute("SELECT prompt_tokens, completion_tokens FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py::test_record_openai_usage -v`
Expected: FAIL — `ImportError: cannot import name 'record_openai_usage'`

- [ ] **Step 3: Implement `record_openai_usage()`**

Add to `shared/cost_tracker.py` after the `record_llm_usage` function (after line 285):

```python
def record_openai_usage(
    response,
    *,
    agent_name: str = "unknown",
    model_hint: str | None = None,
    operation: str = "chat",
) -> dict:
    """Persist a raw OpenAI SDK ChatCompletion response to SQLite.

    Use for client.chat.completions.create() calls that bypass _InstrumentedLLM.
    """
    usage = getattr(response, "usage", None)
    if usage is not None:
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    else:
        prompt_tokens = 0
        completion_tokens = 0

    model = getattr(response, "model", None) or model_hint or "gpt-4o-mini"
    cost = estimate_cost(model, prompt_tokens, completion_tokens)

    result = {
        "agent": agent_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": model,
        "cost_usd": cost,
        "trajectory_id": get_trajectory_id(),
        "run_id": get_run_id(),
        "operation": operation,
    }
    _record_usage_row(
        agent_name=agent_name,
        operation=operation,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost,
    )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py -v -k "openai_usage"`
Expected: PASS — both new tests green.

- [ ] **Step 5: Commit**

```bash
git add shared/cost_tracker.py tests/shared/test_cost_tracker_ledger.py
git commit -m "feat(cost): add record_openai_usage() for raw OpenAI SDK responses"
```

---

### Task 4: Wire `safe_openai_call()` to Cost Tracking

**Files:**
- Modify: `jobpulse/utils/safe_io.py:37-57`
- Test: `tests/jobpulse/test_safe_io.py`

`safe_openai_call()` has 7 callers and zero cost tracking. Add a `record_openai_usage()` call after the API response.

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_safe_io.py`:

```python
def test_safe_openai_call_records_cost(monkeypatch, tmp_path):
    """safe_openai_call should record cost after successful API call."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))

    from shared.logging_config import set_run_id, set_trajectory_id, clear_trajectory_id
    set_run_id("run_safe_cost")
    set_trajectory_id("traj_safe_cost")

    from jobpulse.utils.safe_io import safe_openai_call

    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 100
    mock_usage.completion_tokens = 30

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "LLM response text"
    mock_response.usage = mock_usage
    mock_response.model = "gpt-4o-mini-2024-07-18"

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    try:
        result = safe_openai_call(
            mock_client,
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "test"}],
            caller="gate4_scrutiny",
        )
    finally:
        clear_trajectory_id()

    assert result == "LLM response text"

    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute("SELECT agent_name, prompt_tokens, completion_tokens FROM llm_calls ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "gate4_scrutiny"
    assert row[1] == 100
    assert row[2] == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_safe_io.py::test_safe_openai_call_records_cost -v`
Expected: FAIL — no row in `llm_calls` (cost tracking not wired).

- [ ] **Step 3: Add cost tracking to `safe_openai_call()`**

In `jobpulse/utils/safe_io.py`, after the `response = client.chat.completions.create(...)` call (line 47) and before the `if not response.choices:` check, add cost recording:

```python
        response = client.chat.completions.create(**call_kwargs)

        # Record cost (best-effort — never block the caller)
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(
                response,
                agent_name=caller or "safe_openai_call",
                model_hint=model,
            )
        except Exception:
            pass

        if not response.choices:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_safe_io.py -v`
Expected: PASS — all tests green including new one.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/utils/safe_io.py tests/jobpulse/test_safe_io.py
git commit -m "feat(cost): wire safe_openai_call() to record_openai_usage()"
```

---

### Task 5: Wire 6 Direct `client.chat.completions.create()` Call Sites

**Files:**
- Modify: `jobpulse/page_analyzer.py`, `jobpulse/screening_answers.py`, `jobpulse/budget_nlp.py`, `jobpulse/conversation.py`, `jobpulse/form_engine/field_mapper.py`, `jobpulse/persona_evolution.py`

Each site needs a 5-line wrapper after the `client.chat.completions.create()` call.

- [ ] **Step 1: Add to `jobpulse/page_analyzer.py`**

Find the `client.chat.completions.create(` call (around line 43). After the response is captured, before it's used, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="page_analyzer", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 2: Add to `jobpulse/screening_answers.py`**

Find the direct `client.chat.completions.create(` call (around line 808, in `_generate_answer`). After the response, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="screening_answers", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 3: Add to `jobpulse/budget_nlp.py`**

Find the call around line 84 (`classify_transaction`). After the response, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="budget_nlp", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 4: Add to `jobpulse/conversation.py`**

Find the call around line 99 (`chat`). After the response, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="conversation", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 5: Add to `jobpulse/form_engine/field_mapper.py`**

Find the call around line 735 (`review_form`). After the response, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="field_mapper", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 6: Add to `jobpulse/persona_evolution.py`**

Three call sites: lines ~103, ~192, ~204. After each response, add:

```python
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="persona_evolution", model_hint=model)
        except Exception:
            pass
```

- [ ] **Step 7: Verify no regressions**

Run: `python -m pytest tests/ -v -k "page_analyzer or screening or budget_nlp or conversation or field_mapper or persona" --timeout=30`
Expected: PASS (or no tests exist for these files — that's OK, the wrapper is try/except guarded).

- [ ] **Step 8: Commit**

```bash
git add jobpulse/page_analyzer.py jobpulse/screening_answers.py jobpulse/budget_nlp.py jobpulse/conversation.py jobpulse/form_engine/field_mapper.py jobpulse/persona_evolution.py
git commit -m "feat(cost): add record_openai_usage() to 6 direct client.chat call sites"
```

---

### Task 6: Update All `get_llm()` Callers with `agent_name`

**Files:**
- Modify: `jobpulse/gmail_agent.py:139`, `jobpulse/portfolio_variants.py:291,356`, `jobpulse/screening_answers.py:397`, `jobpulse/strategy_reflector.py:186`, `jobpulse/persona_evolution.py:177`, `shared/agents.py:409,475,541`, `shared/experiential_learning.py:340`

Each call needs a `agent_name=` keyword argument added.

- [ ] **Step 1: Update `shared/agents.py` pattern nodes**

At line 409 (`researcher_node`), change:
```python
    llm = get_llm(temperature=0.3)
```
to:
```python
    llm = get_llm(temperature=0.3, agent_name="researcher")
```

At line 475 (`writer_node`), change:
```python
    llm = get_llm(temperature=0.7)
```
to:
```python
    llm = get_llm(temperature=0.7, agent_name="writer")
```

At line 541 (`reviewer_node`), change:
```python
    llm = get_llm(
        model="gpt-5-mini",
        temperature=0.2,
        timeout=30.0,
    )
```
to:
```python
    llm = get_llm(
        model="gpt-5-mini",
        temperature=0.2,
        timeout=30.0,
        agent_name="reviewer",
    )
```

- [ ] **Step 2: Update `jobpulse/gmail_agent.py:139`**

Change: `get_llm(...)` to include `agent_name="email_classifier"`.

- [ ] **Step 3: Update `jobpulse/portfolio_variants.py:291,356`**

Both calls: add `agent_name="portfolio_variants"`.

- [ ] **Step 4: Update `jobpulse/screening_answers.py:397`**

Add `agent_name="screening_answers"`.

- [ ] **Step 5: Update `jobpulse/strategy_reflector.py:186`**

Add `agent_name="strategy_reflector"`.

- [ ] **Step 6: Update `jobpulse/persona_evolution.py:177`**

Add `agent_name="persona_evolution"`.

- [ ] **Step 7: Update `shared/experiential_learning.py:340`**

Add `agent_name="grpo_variant"`.

- [ ] **Step 8: Run test suite to verify no regressions**

Run: `python -m pytest tests/shared/test_llm_attribution.py tests/shared/test_cost_tracker_ledger.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add shared/agents.py shared/experiential_learning.py jobpulse/gmail_agent.py jobpulse/portfolio_variants.py jobpulse/screening_answers.py jobpulse/strategy_reflector.py jobpulse/persona_evolution.py
git commit -m "feat(cost): add agent_name to all get_llm() call sites"
```

---

### Task 7: Application Audit Trail — `application_log` Table

**Files:**
- Modify: `jobpulse/rate_limiter.py`
- Test: `tests/test_rate_limiter.py`

Add `application_log` table, extend `record_application()`, add `get_application_log()`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_rate_limiter.py`:

```python
def test_application_log_recorded(limiter):
    """record_application stores audit trail with job details."""
    limiter.record_application(
        "greenhouse",
        job_id="gh_12345",
        company="Snowflake",
        url="https://boards.greenhouse.io/snowflake/jobs/12345",
    )

    log = limiter.get_application_log(days=1)
    assert len(log) == 1
    assert log[0]["platform"] == "greenhouse"
    assert log[0]["job_id"] == "gh_12345"
    assert log[0]["company"] == "Snowflake"
    assert log[0]["url"] == "https://boards.greenhouse.io/snowflake/jobs/12345"
    assert log[0]["recorded_at"]  # non-empty timestamp


def test_application_log_links_to_daily_counts(limiter):
    """application_log count matches daily_counts total for the same date."""
    limiter.record_application("greenhouse", job_id="gh_1", company="Snowflake", url="https://boards.greenhouse.io/snowflake/jobs/1")
    limiter.record_application("workday", job_id="wd_2", company="ASOS", url="https://asos.wd3.myworkdayjobs.com/careers/job/2")
    limiter.record_application("linkedin", job_id="li_3", company="Arm", url="https://www.linkedin.com/jobs/view/3")

    assert limiter.get_total_today() == 3

    log = limiter.get_application_log(days=1)
    assert len(log) == 3
    companies = {entry["company"] for entry in log}
    assert companies == {"Snowflake", "ASOS", "Arm"}


def test_application_log_backward_compatible(limiter):
    """record_application without extra args still works (backward-compatible)."""
    limiter.record_application("reed")
    assert limiter.get_total_today() == 1

    log = limiter.get_application_log(days=1)
    assert len(log) == 1
    assert log[0]["job_id"] == ""
    assert log[0]["company"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rate_limiter.py::test_application_log_recorded -v`
Expected: FAIL — `record_application()` doesn't accept `job_id`, `company`, `url`.

- [ ] **Step 3: Implement application_log table and extended record_application**

In `jobpulse/rate_limiter.py`:

1. In `_init_db()`, after the `session_tracker` CREATE TABLE (line 64), add:

```python
            conn.execute(
                """CREATE TABLE IF NOT EXISTS application_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    company TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_applog_date ON application_log(date)"
            )
```

2. Update `record_application()` signature (line 108):

```python
    def record_application(
        self, platform: str,
        job_id: str = "", company: str = "", url: str = "",
    ) -> None:
```

3. Inside `record_application()`, after the `INSERT INTO daily_counts` and `INSERT INTO session_tracker` block (still inside `with atomic_sqlite`), add:

```python
            conn.execute(
                """INSERT INTO application_log (date, platform, job_id, company, url, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (today, platform, job_id, company, url, self._now_iso()),
            )
```

4. Add helper methods:

```python
    def _now_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_application_log(self, days: int = 7) -> list[dict]:
        """Return application audit trail for the last N days."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT date, platform, job_id, company, url, recorded_at "
                "FROM application_log WHERE date >= ? ORDER BY id DESC",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rate_limiter.py -v`
Expected: PASS — all existing + 3 new tests green.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat(rate-limit): add application_log audit trail table"
```

---

### Task 8: Update `applicator.py` Callers to Pass Job Context

**Files:**
- Modify: `jobpulse/applicator.py:265,467`

Both `apply_job()` and `confirm_application()` call `limiter.record_application(platform_key)` — update them to pass `job_id`, `company`, `url` from `job_context`.

- [ ] **Step 1: Update `apply_job()` call (line ~265)**

Change:
```python
                limiter.record_application(platform_key)
```
to:
```python
                ctx = job_context or {}
                limiter.record_application(
                    platform_key,
                    job_id=ctx.get("job_id", ""),
                    company=ctx.get("company", ""),
                    url=url,
                )
```

- [ ] **Step 2: Update `confirm_application()` call (line ~467)**

Change:
```python
            limiter.record_application(platform_key)
```
to:
```python
            limiter.record_application(
                platform_key,
                job_id=ctx.get("job_id", ""),
                company=ctx.get("company", ""),
                url=url,
            )
```

- [ ] **Step 3: Verify no regressions**

Run: `python -m pytest tests/ -v -k "applicator or rate_limiter" --timeout=30`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/applicator.py
git commit -m "feat(rate-limit): pass job_id/company/url to record_application()"
```

---

### Task 9: Proactive Quota Alerts

**Files:**
- Modify: `jobpulse/rate_limiter.py` (in `record_application()`)
- Test: `tests/test_rate_limiter.py`

Fire `send_pipeline_alert()` when total or per-platform count hits 80% of cap.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_rate_limiter.py`:

```python
from unittest.mock import patch as mock_patch, call


def test_quota_alert_at_80_percent(tmp_path, monkeypatch):
    """Alert fires exactly once when total hits 80% of daily cap."""
    monkeypatch.setattr("jobpulse.rate_limiter.TOTAL_DAILY_CAP", 10)
    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    with mock_patch("jobpulse.rate_limiter.send_pipeline_alert") as mock_alert:
        for i in range(7):
            limiter.record_application("generic")
        assert mock_alert.call_count == 0

        limiter.record_application("generic")  # 8th = 80% of 10
        assert mock_alert.call_count == 1
        assert "80%" in mock_alert.call_args[0][0]
        assert mock_alert.call_args[1]["severity"] == "warning"
        assert mock_alert.call_args[1]["category"] == "quota"

        limiter.record_application("generic")  # 9th — no second alert
        assert mock_alert.call_count == 1


def test_platform_quota_alert(tmp_path, monkeypatch):
    """Alert fires when a single platform hits 80% of its cap."""
    monkeypatch.setattr("jobpulse.rate_limiter.DAILY_CAPS", {"linkedin": 5, "generic": 5})
    monkeypatch.setattr("jobpulse.rate_limiter.TOTAL_DAILY_CAP", 50)
    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    with mock_patch("jobpulse.rate_limiter.send_pipeline_alert") as mock_alert:
        for i in range(3):
            limiter.record_application("linkedin")
        assert mock_alert.call_count == 0

        limiter.record_application("linkedin")  # 4th = 80% of 5
        assert mock_alert.call_count == 1
        assert "linkedin" in mock_alert.call_args[0][0].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_rate_limiter.py::test_quota_alert_at_80_percent -v`
Expected: FAIL — `send_pipeline_alert` not imported in `rate_limiter.py`.

- [ ] **Step 3: Add alert logic to `record_application()`**

At the top of `jobpulse/rate_limiter.py`, add import:

```python
from shared.alerting import send_pipeline_alert
```

At the end of `record_application()`, after the `logger.info(...)` line (line 130), add:

```python
        # Proactive quota alerts at 80% threshold
        threshold = int(TOTAL_DAILY_CAP * 0.8)
        if total == threshold:
            send_pipeline_alert(
                f"Daily quota at {total}/{TOTAL_DAILY_CAP} (80%). "
                f"{TOTAL_DAILY_CAP - total} slots remaining.",
                severity="warning",
                category="quota",
            )

        cap = DAILY_CAPS.get(platform, DAILY_CAPS["generic"])
        platform_count = self._get_platform_count(platform)
        platform_threshold = int(cap * 0.8)
        if platform_threshold > 0 and platform_count == platform_threshold:
            send_pipeline_alert(
                f"{platform.title()} quota at {platform_count}/{cap} (80%). "
                f"{cap - platform_count} slots remaining.",
                severity="warning",
                category="quota",
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_rate_limiter.py -v`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat(rate-limit): proactive quota alerts at 80% threshold"
```

---

### Task 10: `get_daily_llm_summary()` Query Function

**Files:**
- Modify: `shared/cost_tracker.py`
- Test: `tests/shared/test_cost_tracker_ledger.py`

New query function that returns per-agent cost breakdown for the last N days.

- [ ] **Step 1: Write failing tests**

Append to `tests/shared/test_cost_tracker_ledger.py`:

```python
def test_get_daily_llm_summary(monkeypatch, tmp_path):
    """get_daily_llm_summary returns per-agent breakdown for recent calls."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_summary")
    set_trajectory_id("traj_summary")

    from shared.cost_tracker import record_openai_usage, get_daily_llm_summary

    # Insert 5 realistic calls
    for _ in range(2):
        resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 100, "completion_tokens": 30})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
        record_openai_usage(resp, agent_name="gate4", model_hint="gpt-4o-mini")
    for _ in range(2):
        resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 200, "completion_tokens": 60})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
        record_openai_usage(resp, agent_name="screening_answers", model_hint="gpt-4o-mini")
    resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 500, "completion_tokens": 100})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
    record_openai_usage(resp, agent_name="page_analyzer", model_hint="gpt-4o-mini")

    clear_trajectory_id()

    summary = get_daily_llm_summary(days=1)
    assert summary["total_calls"] == 5
    assert summary["total_cost"] > 0
    assert summary["by_agent"]["gate4"]["calls"] == 2
    assert summary["by_agent"]["screening_answers"]["calls"] == 2
    assert summary["by_agent"]["page_analyzer"]["calls"] == 1
    assert "gate4" in summary["by_agent"]


def test_get_daily_llm_summary_excludes_old(monkeypatch, tmp_path):
    """get_daily_llm_summary excludes data older than the requested window."""
    db_path = str(tmp_path / "llm_usage.db")
    monkeypatch.setenv("LLM_USAGE_DB", db_path)

    from shared.cost_tracker import get_daily_llm_summary, _usage_conn

    conn = _usage_conn()
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-01-01T00:00:00Z', 'old', 'old', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    conn.commit()
    conn.close()

    summary = get_daily_llm_summary(days=1)
    assert summary["total_calls"] == 0
    assert summary["total_cost"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py::test_get_daily_llm_summary -v`
Expected: FAIL — `ImportError: cannot import name 'get_daily_llm_summary'`

- [ ] **Step 3: Implement `get_daily_llm_summary()`**

Add to `shared/cost_tracker.py` after `record_openai_usage()`:

```python
def get_daily_llm_summary(days: int = 1) -> dict:
    """Per-agent cost breakdown for the last N days.

    Returns:
        {
            "total_cost": float,
            "total_calls": int,
            "by_agent": {name: {"calls": int, "cost": float}},
            "by_day": {date_str: float},
        }
    """
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = _usage_conn()
    rows = conn.execute(
        "SELECT agent_name, cost_usd, timestamp FROM llm_calls WHERE timestamp >= ?",
        (cutoff,),
    ).fetchall()
    conn.close()

    total_cost = 0.0
    by_agent: dict[str, dict] = {}
    by_day: dict[str, float] = {}

    for agent_name, cost, ts in rows:
        total_cost += cost
        if agent_name not in by_agent:
            by_agent[agent_name] = {"calls": 0, "cost": 0.0}
        by_agent[agent_name]["calls"] += 1
        by_agent[agent_name]["cost"] += cost

        day = ts[:10]
        by_day[day] = by_day.get(day, 0.0) + cost

    return {
        "total_cost": total_cost,
        "total_calls": len(rows),
        "by_agent": by_agent,
        "by_day": by_day,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py -v -k "daily_llm_summary"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared/cost_tracker.py tests/shared/test_cost_tracker_ledger.py
git commit -m "feat(cost): add get_daily_llm_summary() query function"
```

---

### Task 11: Cost Section in Morning Briefing

**Files:**
- Modify: `jobpulse/morning_briefing.py:146-231`

Add a new section 11 (LLM cost) after the job stats section, before the message assembly.

- [ ] **Step 1: Add cost section after section 10 (job stats block)**

After the job stats `except` block (around line 177), before the `# ── Build Message ──` line (line 179), add:

```python
    # ── Section 11: LLM Cost ──
    section_cost = ""
    with trail.step("api_call", "Collect LLM cost data") as s:
        try:
            from shared.cost_tracker import get_daily_llm_summary
            cost_summary = get_daily_llm_summary(days=1)
            if cost_summary["total_calls"] > 0:
                top_agents = sorted(
                    cost_summary["by_agent"].items(),
                    key=lambda x: x[1]["cost"],
                    reverse=True,
                )[:5]
                agent_parts = ", ".join(
                    f"{name}: ${data['cost']:.3f}" for name, data in top_agents
                )
                section_cost = (
                    f"LLM COST:\n"
                    f"  Total: ${cost_summary['total_cost']:.3f} ({cost_summary['total_calls']} calls)\n"
                    f"  Top agents: {agent_parts}"
                )
            s["output"] = f"${cost_summary['total_cost']:.3f}, {cost_summary['total_calls']} calls"
        except Exception as e:
            logger.debug("LLM cost section skipped: %s", e)
            s["output"] = f"Cost error: {e}"
```

- [ ] **Step 2: Add cost section to the message template**

In the message template string, after the `section_jobs` block (around line 230), add:

```python
{f"""
━━━━━━━━━━━━━━━━━━━━

💰 {section_cost}""" if section_cost else ""}
```

- [ ] **Step 3: Verify no regressions**

Run: `python -m pytest tests/ -v -k "briefing" --timeout=30`
Expected: PASS (or no briefing tests exist — that's OK, the section is guarded by try/except).

- [ ] **Step 4: Commit**

```bash
git add jobpulse/morning_briefing.py
git commit -m "feat(cost): add LLM cost section to morning briefing"
```

---

### Task 12: Cost Trend in Weekly Report

**Files:**
- Modify: `jobpulse/weekly_report.py:100-128`

Add a new section 6 (LLM cost trend) after the job applications section.

- [ ] **Step 1: Add cost trend section after section 5 (jobs)**

After the jobs `except` block (around line 126), before the `# Build message` line (line 128), add:

```python
    # 6. LLM cost trend
    try:
        from shared.cost_tracker import get_daily_llm_summary
        cost_data = get_daily_llm_summary(days=7)
        if cost_data["total_calls"] > 0:
            avg_daily = cost_data["total_cost"] / max(len(cost_data["by_day"]), 1)
            top_agents = sorted(
                cost_data["by_agent"].items(),
                key=lambda x: x[1]["cost"],
                reverse=True,
            )[:3]
            agent_lines = "\n".join(
                f"  {name}: ${data['cost']:.3f} ({data['calls']} calls)"
                for name, data in top_agents
            )
            sections["cost"] = (
                f"  Total: ${cost_data['total_cost']:.3f} ({cost_data['total_calls']} calls)\n"
                f"  Daily avg: ${avg_daily:.3f}\n"
                f"  Top agents:\n{agent_lines}"
            )
        else:
            sections["cost"] = "  No LLM calls recorded"
    except Exception as e:
        logger.debug("Weekly report cost: %s", e)
        sections["cost"] = "  Data unavailable"
```

- [ ] **Step 2: Add cost section to the report template**

In the report string builder (around line 155), after the JOB APPLICATIONS block, add:

```python
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"\U0001f4b0 LLM COST:\n"
        f"{sections['cost']}\n"
```

- [ ] **Step 3: Verify no regressions**

Run: `python -m pytest tests/ -v -k "weekly" --timeout=30`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/weekly_report.py
git commit -m "feat(cost): add LLM cost trend to weekly report"
```

---

### Task 13: DB Retention Cleanup Functions

**Files:**
- Modify: `shared/cost_tracker.py`, `jobpulse/rate_limiter.py`
- Test: `tests/shared/test_cost_tracker_ledger.py`, `tests/test_rate_limiter.py`

Add `cleanup_old_usage()` to cost_tracker and `cleanup_old()` to RateLimiter.

- [ ] **Step 1: Write failing tests**

Append to `tests/shared/test_cost_tracker_ledger.py`:

```python
def test_cleanup_old_usage(monkeypatch, tmp_path):
    """cleanup_old_usage deletes rows older than retention_days."""
    db_path = str(tmp_path / "llm_usage.db")
    monkeypatch.setenv("LLM_USAGE_DB", db_path)

    from shared.cost_tracker import cleanup_old_usage, _usage_conn

    conn = _usage_conn()
    # 120 days ago
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-01-01T00:00:00Z', 't1', 'r1', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    # 60 days ago
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-02-28T00:00:00Z', 't2', 'r2', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    # Today
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        f"VALUES ('{now}', 't3', 'r3', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    conn.commit()
    conn.close()

    deleted = cleanup_old_usage(retention_days=90)
    assert deleted == 1  # only the 120-day-old row

    conn = _usage_conn()
    remaining = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    conn.close()
    assert remaining == 2
```

Append to `tests/test_rate_limiter.py`:

```python
def test_cleanup_old_preserves_recent(tmp_path):
    """cleanup_old deletes old records but preserves recent ones."""
    import sqlite3
    from datetime import datetime, timezone, timedelta

    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    utc_now = datetime.now(timezone.utc)
    old_date = (utc_now - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_date = (utc_now - timedelta(days=15)).strftime("%Y-%m-%d")
    today = utc_now.strftime("%Y-%m-%d")

    with sqlite3.connect(limiter.db_path) as conn:
        for d, plat in [(old_date, "greenhouse"), (recent_date, "workday"), (today, "linkedin")]:
            conn.execute(
                "INSERT INTO daily_counts (date, platform, count) VALUES (?, ?, 1)",
                (d, plat),
            )
            conn.execute(
                "INSERT INTO application_log (date, platform, job_id, company, url, recorded_at) "
                "VALUES (?, ?, 'j1', 'TestCo', 'https://example.com', ?)",
                (d, plat, f"{d}T12:00:00Z"),
            )
        conn.commit()

    deleted = limiter.cleanup_old(retention_days=30)
    assert deleted > 0

    with sqlite3.connect(limiter.db_path) as conn:
        dc_count = conn.execute("SELECT COUNT(*) FROM daily_counts").fetchone()[0]
        al_count = conn.execute("SELECT COUNT(*) FROM application_log").fetchone()[0]

    assert dc_count == 2  # recent + today
    assert al_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py::test_cleanup_old_usage tests/test_rate_limiter.py::test_cleanup_old_preserves_recent -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement `cleanup_old_usage()` in `shared/cost_tracker.py`**

Add after `get_daily_llm_summary()`:

```python
def cleanup_old_usage(retention_days: int = 90) -> int:
    """Delete llm_calls rows older than retention_days. Returns count deleted."""
    from datetime import datetime, timezone, timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _LEDGER_LOCK:
        conn = _usage_conn()
        cursor = conn.execute("DELETE FROM llm_calls WHERE timestamp < ?", (cutoff,))
        deleted = cursor.rowcount
        conn.commit()
    if deleted:
        logger.info("Cleaned up %d old llm_calls rows (retention=%d days)", deleted, retention_days)
    return deleted
```

- [ ] **Step 4: Implement `cleanup_old()` in `jobpulse/rate_limiter.py`**

Add to `RateLimiter` class:

```python
    def cleanup_old(self, retention_days: int = 30) -> int:
        """Delete daily_counts and application_log rows older than retention_days."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%d")
        total_deleted = 0
        with sqlite3.connect(self.db_path) as conn:
            c1 = conn.execute("DELETE FROM daily_counts WHERE date < ?", (cutoff,))
            total_deleted += c1.rowcount
            c2 = conn.execute("DELETE FROM application_log WHERE date < ?", (cutoff,))
            total_deleted += c2.rowcount
            conn.execute("DELETE FROM session_tracker WHERE date < ?", (cutoff,))
            conn.commit()
        if total_deleted:
            logger.info("Cleaned up %d old rate limiter rows (retention=%d days)", total_deleted, retention_days)
        return total_deleted
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_cost_tracker_ledger.py::test_cleanup_old_usage tests/test_rate_limiter.py::test_cleanup_old_preserves_recent -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shared/cost_tracker.py jobpulse/rate_limiter.py tests/shared/test_cost_tracker_ledger.py tests/test_rate_limiter.py
git commit -m "feat(cost): add cleanup_old_usage() and RateLimiter.cleanup_old()"
```

---

### Task 14: Wire Cleanup to Daemon Optimization Tick

**Files:**
- Modify: `jobpulse/multi_bot_listener.py:61-76`

Add cleanup calls inside the forgetting sweep block (runs every 4th tick = ~1 hour).

- [ ] **Step 1: Add cleanup to the forgetting sweep block**

In `_optimization_tick()`, after the forgetting sweep `except` block (after line 76), still inside the `if sweep_counter >= 4:` block, add:

```python
                # DB retention cleanup (same cadence as forgetting sweep)
                try:
                    from jobpulse.rate_limiter import RateLimiter
                    rl_deleted = RateLimiter().cleanup_old(retention_days=30)
                    if rl_deleted:
                        _log(f"rate limiter cleanup: {rl_deleted} old rows deleted")
                except Exception as e:
                    _log(f"rate limiter cleanup error: {e}")

                try:
                    from shared.cost_tracker import cleanup_old_usage
                    usage_deleted = cleanup_old_usage(retention_days=90)
                    if usage_deleted:
                        _log(f"llm usage cleanup: {usage_deleted} old rows deleted")
                except Exception as e:
                    _log(f"llm usage cleanup error: {e}")
```

- [ ] **Step 2: Verify no regressions**

Run: `python -m pytest tests/ -v -k "multi_bot" --timeout=30`
Expected: PASS (or no tests — daemon code is hard to unit test, wiring is verified by inspection).

- [ ] **Step 3: Commit**

```bash
git add jobpulse/multi_bot_listener.py
git commit -m "feat(cost): wire DB retention cleanup to daemon optimization tick"
```

---

### Task 15: Re-export `record_openai_usage` from `shared/agents.py`

**Files:**
- Modify: `shared/agents.py:52-58`

The existing re-exports from `shared/cost_tracker` don't include the new function. Add it so callers can import from `shared/agents`.

- [ ] **Step 1: Add re-export**

In `shared/agents.py`, update the `from shared.cost_tracker import` block (lines 52-58):

```python
from shared.cost_tracker import (  # noqa: F401
    MODEL_COSTS,
    estimate_cost,
    record_llm_usage,
    record_openai_usage,
    track_llm_usage,
    compute_cost_summary,
)
```

- [ ] **Step 2: Run full test suite to verify nothing broke**

Run: `python -m pytest tests/shared/ tests/test_rate_limiter.py tests/jobpulse/test_safe_io.py -v --timeout=60`
Expected: PASS — all tests green.

- [ ] **Step 3: Commit**

```bash
git add shared/agents.py
git commit -m "chore: re-export record_openai_usage from shared/agents"
```

---

### Task 16: Final Integration Verification

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/ -v --timeout=120 -x`
Expected: PASS — zero failures.

- [ ] **Step 2: Verify attribution coverage by inspecting key paths**

```bash
# Verify all get_llm() calls have agent_name (should return 0 matches without agent_name):
grep -rn "get_llm(" shared/agents.py jobpulse/gmail_agent.py jobpulse/portfolio_variants.py jobpulse/screening_answers.py jobpulse/strategy_reflector.py jobpulse/persona_evolution.py shared/experiential_learning.py | grep -v "agent_name"
```

Expected: Only the `def get_llm(` signature line matches — all call sites have `agent_name=`.

- [ ] **Step 3: Verify safe_openai_call has cost tracking**

```bash
grep -n "record_openai_usage" jobpulse/utils/safe_io.py
```

Expected: At least 1 match.

- [ ] **Step 4: Verify all 6 direct call sites have cost tracking**

```bash
grep -rn "record_openai_usage" jobpulse/page_analyzer.py jobpulse/screening_answers.py jobpulse/budget_nlp.py jobpulse/conversation.py jobpulse/form_engine/field_mapper.py jobpulse/persona_evolution.py
```

Expected: 8 matches (6 files, persona_evolution has 3 calls).

- [ ] **Step 5: Commit all work with final message**

```bash
git add -A
git commit -m "feat: rate limiting & cost observability upgrade — 5 gaps closed"
```
