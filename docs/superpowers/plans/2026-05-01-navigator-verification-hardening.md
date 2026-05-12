# Navigator Verification Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close verified gaps in JobPulse's web-automation pipeline so the system reliably catches per-fill failures, gives auth flows the same ghost-click detection that application flows already have, and re-grounds the page reasoner when its plan doesn't pan out.

**Architecture:** Three layers, surgical changes only.
1. **Executor layer** (`jobpulse/navigation/action_executor.py`): introduce a structured `ExecutorResult` return type; add per-field read-back-and-retry inside `_execute_fill`. All callers consume the result.
2. **Navigator layer** (`jobpulse/application_orchestrator_pkg/_navigator.py`, `_auth.py`): extract the existing pre/post snapshot + ghost-click verification from `_phase_act` into a shared helper `_verify_action`. Auth handlers route through it. Cache invalidation generalizes to all detected-failure paths.
3. **Reasoner layer** (`jobpulse/page_analysis/page_reasoner.py`, `jobpulse/vision_tier.py`): add `expected_outcome` contract to `PageAction`, a field-count guard, a failure-driven re-grounding method, and a confidence-gated vision agreement check.

No new modules, no new DBs, no new dependencies. Every change either extends an existing file or adds one focused helper.

**Tech Stack:** Python 3.11, Playwright (CDP), pytest + pytest-asyncio, SQLite (existing `data/page_reasoning_cache.db`), OpenAI API via `shared.agents.smart_llm_call`, existing `shared.optimization.OptimizationEngine` for signal emission.

---

## File Structure

**Modify:**
- `jobpulse/navigation/action_executor.py` (198 → ~280 lines): add `ExecutorResult`, read-back logic in `_execute_fill`, structured returns.
- `jobpulse/application_orchestrator_pkg/_navigator.py` (1293 lines): extract `_verify_action` helper, generalize cache invalidation, wire reasoner reflection + vision gate into `_phase_act`.
- `jobpulse/application_orchestrator_pkg/_auth.py` (~100 lines): route `handle_login` and `handle_signup` through the shared verifier.
- `jobpulse/page_analysis/page_reasoner.py` (366 → ~440 lines): add `expected_outcome` to `PageAction`, public `invalidate(snapshot)` method, `reason_with_failure` method, post-LLM field-count guard.
- `jobpulse/vision_tier.py` (91 → ~150 lines): add `classify_page_type_from_screenshot(...)` for the agreement gate.

**Create:**
- `tests/jobpulse/test_action_executor_verification.py` — read-back, retry, ExecutorResult.
- `tests/jobpulse/test_verify_action_helper.py` — extracted verifier.
- `tests/jobpulse/test_auth_verification_routing.py` — auth path uses verifier.
- `tests/jobpulse/test_page_action_outcome.py` — `expected_outcome` parsing + verification.
- `tests/jobpulse/test_field_count_guard.py` — reasoner field-count check.
- `tests/jobpulse/test_cache_invalidation.py` — generalized invalidation.
- `tests/jobpulse/test_reasoner_reflection.py` — reflection on failure.
- `tests/jobpulse/test_vision_dom_gate.py` — confidence-gated vision agreement.

**Existing tests to preserve:**
- `tests/jobpulse/test_nav_action_executor.py` — must keep passing across all tasks.

---

## Task 0: Establish baseline

**Files:**
- Run-only: no edits.

- [ ] **Step 1: Capture current test state**

Run:
```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns
python -m pytest tests/jobpulse/test_nav_action_executor.py -v 2>&1 | tail -20
```
Expected: all tests pass. Record the count for regression comparison.

- [ ] **Step 2: Confirm imports resolve**

Run:
```bash
python -c "from jobpulse.navigation.action_executor import NavigationActionExecutor; from jobpulse.application_orchestrator_pkg._navigator import FormNavigator; from jobpulse.application_orchestrator_pkg._auth import AuthHandler; from jobpulse.page_analysis.page_reasoner import PageAction, get_page_reasoner; print('OK')"
```
Expected: prints `OK`.

- [ ] **Step 3: Commit a marker**

```bash
git checkout -b nav-verification-hardening
git commit --allow-empty -m "chore: start navigator verification hardening"
```

---

## Task 1: Add `ExecutorResult` dataclass

**Files:**
- Modify: `jobpulse/navigation/action_executor.py` (top of file, after imports)
- Test: `tests/jobpulse/test_action_executor_verification.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_action_executor_verification.py`:
```python
"""Tests for executor verification primitives."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.page_analysis.page_reasoner import PageAction
from jobpulse.navigation.action_executor import (
    NavigationActionExecutor,
    ExecutorResult,
)


def _make_action(**kwargs) -> PageAction:
    defaults = {
        "page_understanding": "test", "action": "fill_and_advance",
        "target_text": "", "reasoning": "test", "confidence": 0.9,
        "page_type": "signup_form", "field_fills": [],
        "advance_button": "", "overlays_to_dismiss": [],
    }
    defaults.update(kwargs)
    return PageAction(**defaults)


class TestExecutorResultShape:
    def test_default_result_is_empty(self):
        r = ExecutorResult()
        assert r.fills_attempted == 0
        assert r.fills_verified == 0
        assert r.fills_failed == []
        assert r.clicks_attempted == 0
        assert r.advance_clicked is False

    def test_result_records_failures(self):
        r = ExecutorResult()
        r.record_fill_failure("Email", expected="a@b.com", actual="")
        assert r.fills_failed == [{"label": "Email", "expected": "a@b.com", "actual": ""}]
```

- [ ] **Step 2: Run the test, expect it to fail**

```bash
python -m pytest tests/jobpulse/test_action_executor_verification.py::TestExecutorResultShape -v
```
Expected: ImportError on `ExecutorResult`.

- [ ] **Step 3: Add `ExecutorResult` to `action_executor.py`**

Edit `jobpulse/navigation/action_executor.py`. After the existing imports (after line 14), insert:
```python
from dataclasses import dataclass, field as dc_field


@dataclass
class ExecutorResult:
    """Structured outcome of a NavigationActionExecutor.execute() call.

    Returned to callers (FormNavigator._phase_act, AuthHandler.handle_login/signup)
    so they can act on per-fill failures without reverse-engineering from snapshots.
    """
    fills_attempted: int = 0
    fills_verified: int = 0
    fills_failed: list[dict] = dc_field(default_factory=list)
    clicks_attempted: int = 0
    advance_clicked: bool = False

    def record_fill_failure(self, label: str, expected: str, actual: str) -> None:
        self.fills_failed.append({
            "label": label, "expected": expected, "actual": actual,
        })

    @property
    def has_failures(self) -> bool:
        return bool(self.fills_failed)
```

- [ ] **Step 4: Run the test, expect pass**

```bash
python -m pytest tests/jobpulse/test_action_executor_verification.py::TestExecutorResultShape -v
```
Expected: 2 tests pass.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/navigation/action_executor.py tests/jobpulse/test_action_executor_verification.py
git commit -m "feat(nav): add ExecutorResult dataclass for structured executor returns"
```

---

## Task 2: Make `execute()` return `ExecutorResult`

**Files:**
- Modify: `jobpulse/navigation/action_executor.py:27-52` (`execute` method)
- Test: `tests/jobpulse/test_action_executor_verification.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_action_executor_verification.py`:
```python
@pytest.fixture
def mock_page():
    page = AsyncMock()
    page.url = "https://example.com/apply"
    loc = AsyncMock()
    loc.count = AsyncMock(return_value=1)
    loc.first = AsyncMock()
    loc.first.is_visible = AsyncMock(return_value=True)
    loc.first.click = AsyncMock()
    loc.first.is_checked = AsyncMock(return_value=False)
    loc.first.check = AsyncMock()
    loc.first.fill = AsyncMock()
    loc.first.input_value = AsyncMock(return_value="user@x.com")
    loc.first.select_option = AsyncMock()
    page.get_by_role = MagicMock(return_value=loc)
    page.get_by_label = MagicMock(return_value=loc)
    page.get_by_placeholder = MagicMock(return_value=loc)
    page.get_by_text = MagicMock(return_value=loc)
    page.locator = MagicMock(return_value=loc)
    return page


@pytest.fixture
def executor(mock_page):
    return NavigationActionExecutor(mock_page)


class TestExecuteReturnsResult:
    @pytest.mark.asyncio
    async def test_returns_executor_result(self, executor):
        action = _make_action(field_fills=[
            {"label": "Email", "value": "user@x.com", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        assert isinstance(result, ExecutorResult)
        assert result.fills_attempted == 1

    @pytest.mark.asyncio
    async def test_advance_click_is_recorded(self, executor):
        action = _make_action(advance_button="Next")
        result = await executor.execute(action, profile={})
        assert result.advance_clicked is True
```

- [ ] **Step 2: Run the test, expect failure**

```bash
python -m pytest tests/jobpulse/test_action_executor_verification.py::TestExecuteReturnsResult -v
```
Expected: AttributeError or AssertionError — current `execute()` returns None.

- [ ] **Step 3: Update `execute()` to construct and return `ExecutorResult`**

In `jobpulse/navigation/action_executor.py`, replace the `execute` method (lines 27–52) with:
```python
    async def execute(
        self, action: PageAction, profile: dict[str, str]
    ) -> ExecutorResult:
        """Execute the full action and return a structured outcome."""
        result = ExecutorResult()

        if action.action == "click_element":
            result.clicks_attempted += 1
            if await self._try_click_by_text(action.target_text):
                return result
            if action.overlays_to_dismiss:
                await self._dismiss_overlays(action.overlays_to_dismiss)
                if await self._try_click_by_text(action.target_text):
                    return result
            logger.warning("Could not find clickable element: '%s'",
                           (action.target_text or "")[:40])
            return result

        if action.overlays_to_dismiss:
            await self._dismiss_overlays(action.overlays_to_dismiss)

        if action.action == "dismiss_overlay":
            if action.target_text:
                result.clicks_attempted += 1
                await self._click_by_text(action.target_text)
            return result

        if action.action in ("fill_and_advance", "login", "signup"):
            for fill in action.field_fills:
                await self._execute_fill(fill, profile, result)
            if action.advance_button:
                await asyncio.sleep(0.3)
                await self._click_by_text(action.advance_button)
                result.advance_clicked = True
                result.clicks_attempted += 1

        return result
```

- [ ] **Step 4: Update `_execute_fill` signature to accept the result**

Replace the signature of `_execute_fill` (line 100) so it accepts and mutates `result`:
```python
    async def _execute_fill(
        self, fill: dict[str, str], profile: dict[str, str], result: ExecutorResult,
    ) -> None:
```
Inside the method, immediately after the `if method == "skip":` block, add:
```python
        result.fills_attempted += 1
```
(Do not yet add read-back logic — that's Task 3.)

- [ ] **Step 5: Run new + existing executor tests**

```bash
python -m pytest tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_action_executor_verification.py -v
```
Expected: all pass. Existing tests don't inspect the return value, so they remain green.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/navigation/action_executor.py tests/jobpulse/test_action_executor_verification.py
git commit -m "feat(nav): execute() returns structured ExecutorResult"
```

---

## Task 3: Add per-field read-back and one retry inside `_execute_fill`

**Files:**
- Modify: `jobpulse/navigation/action_executor.py` (`_execute_fill`, the `elif method == "fill":` branch around line 137)
- Test: `tests/jobpulse/test_action_executor_verification.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_action_executor_verification.py`:
```python
class TestFillReadback:
    @pytest.mark.asyncio
    async def test_successful_fill_marks_verified(self, executor, mock_page):
        # input_value returns the value we filled — verified
        mock_page.get_by_label.return_value.first.input_value = AsyncMock(
            return_value="user@x.com"
        )
        action = _make_action(field_fills=[
            {"label": "Email", "value": "user@x.com", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        assert result.fills_verified == 1
        assert result.fills_failed == []

    @pytest.mark.asyncio
    async def test_mismatch_triggers_one_retry(self, executor, mock_page):
        # First read-back returns wrong value, second returns correct
        loc = mock_page.get_by_label.return_value.first
        loc.input_value = AsyncMock(side_effect=["", "user@x.com"])
        action = _make_action(field_fills=[
            {"label": "Email", "value": "user@x.com", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        # fill called twice (initial + retry)
        assert loc.fill.await_count == 2
        assert result.fills_verified == 1

    @pytest.mark.asyncio
    async def test_persistent_mismatch_records_failure(self, executor, mock_page):
        loc = mock_page.get_by_label.return_value.first
        loc.input_value = AsyncMock(return_value="")  # always empty
        action = _make_action(field_fills=[
            {"label": "Email", "value": "user@x.com", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        assert result.fills_verified == 0
        assert len(result.fills_failed) == 1
        assert result.fills_failed[0]["label"] == "Email"
        assert result.fills_failed[0]["expected"] == "user@x.com"
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_action_executor_verification.py::TestFillReadback -v
```
Expected: failures because read-back logic doesn't exist.

- [ ] **Step 3: Implement read-back-and-retry**

In `jobpulse/navigation/action_executor.py`, replace the `elif method == "fill":` branch inside `_execute_fill` (currently lines 137–146) with:
```python
            elif method == "fill":
                loc = self._page.get_by_label(label, exact=False)
                if not await loc.count():
                    loc = self._page.get_by_placeholder(label, exact=False)
                if await loc.count():
                    await loc.first.fill(value)
                    if await self._verify_fill(loc.first, value):
                        result.fills_verified += 1
                        logger.info("Filled %s (verified)", label[:30])
                    else:
                        # one retry with a small wait — covers React controlled
                        # inputs that revert and autocompletes that need time
                        await asyncio.sleep(0.2)
                        await loc.first.fill(value)
                        if await self._verify_fill(loc.first, value):
                            result.fills_verified += 1
                            logger.info("Filled %s (verified after retry)", label[:30])
                        else:
                            actual = await self._safe_input_value(loc.first)
                            result.record_fill_failure(label, value, actual)
                            logger.warning(
                                "Fill mismatch for '%s': expected=%r actual=%r",
                                label[:30], value[:40], actual[:40],
                            )
                else:
                    logger.warning("No locator for fill: %s", label[:40])
```

Then add two helper methods to the class (above `_try_click_by_text` at line 151):
```python
    @staticmethod
    async def _safe_input_value(locator: Any) -> str:
        try:
            return (await locator.input_value()) or ""
        except Exception:
            return ""

    async def _verify_fill(self, locator: Any, expected: str) -> bool:
        actual = await self._safe_input_value(locator)
        if not expected:
            return True
        # Three-way match — same pattern NativeFormFiller uses (line 879-883)
        norm_e = expected.strip().lower()
        norm_a = actual.strip().lower()
        return bool(norm_a) and (
            norm_e == norm_a or norm_e in norm_a or norm_a in norm_e
        )
```

- [ ] **Step 4: Run all executor tests**

```bash
python -m pytest tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_action_executor_verification.py -v
```
Expected: all pass, including the three new TestFillReadback tests.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/navigation/action_executor.py tests/jobpulse/test_action_executor_verification.py
git commit -m "feat(nav): per-field read-back + one retry in _execute_fill"
```

---

## Task 4: Update callers to consume `ExecutorResult`

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:629-630`
- Modify: `jobpulse/application_orchestrator_pkg/_auth.py:55-57, 74-76`
- Test: `tests/jobpulse/test_action_executor_verification.py`

- [ ] **Step 1: Write a unit test asserting failure-signal emission**

Append to `tests/jobpulse/test_action_executor_verification.py`:
```python
class TestFailureSignalEmission:
    @pytest.mark.asyncio
    async def test_emit_helper_sends_optimization_signal(self, monkeypatch, executor, mock_page):
        from jobpulse.navigation.action_executor import emit_fill_failures
        captured = []
        class FakeEngine:
            def emit(self, **kwargs):
                captured.append(kwargs)
        monkeypatch.setattr(
            "shared.optimization.get_optimization_engine",
            lambda: FakeEngine(),
        )
        result = ExecutorResult()
        result.record_fill_failure("Email", "a@b.com", "")
        emit_fill_failures(result, domain="example.com", source="executor_test")
        assert len(captured) == 1
        assert captured[0]["signal_type"] == "failure"
        assert captured[0]["payload"]["field"] == "Email"
```

- [ ] **Step 2: Run, expect ImportError**

```bash
python -m pytest tests/jobpulse/test_action_executor_verification.py::TestFailureSignalEmission -v
```
Expected: ImportError on `emit_fill_failures`.

- [ ] **Step 3: Add `emit_fill_failures` helper**

At the bottom of `jobpulse/navigation/action_executor.py`, add:
```python
def emit_fill_failures(
    result: ExecutorResult, *, domain: str, source: str = "navigator",
) -> None:
    """Emit one optimization signal per failed fill, for downstream learning.

    Wired so both FormNavigator._phase_act and AuthHandler can call this
    without each having to know about OptimizationEngine internals.
    """
    if not result.has_failures:
        return
    try:
        from datetime import UTC, datetime
        from shared.optimization import get_optimization_engine
        engine = get_optimization_engine()
        session_id = f"exec_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        for f in result.fills_failed:
            engine.emit(
                signal_type="failure",
                source_loop=source,
                domain=domain,
                agent_name="action_executor",
                payload={
                    "field": f["label"],
                    "expected": f["expected"][:60],
                    "actual": f["actual"][:60],
                    "kind": "fill_mismatch",
                },
                session_id=session_id,
            )
    except Exception as exc:
        logger.debug("emit_fill_failures: optimization signal failed: %s", exc)
```

- [ ] **Step 4: Wire `_phase_act` to consume the result**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, modify the `else:` branch around lines 626–633. Replace:
```python
        else:
            page = getattr(self.driver, "page", None)
            if page is not None:
                from jobpulse.applicator import PROFILE
                nav_executor = NavigationActionExecutor(page)
                await nav_executor.execute(action, profile=PROFILE)
            ctx.action_executed = True
            await asyncio.sleep(1.0)
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
```
with:
```python
        else:
            page = getattr(self.driver, "page", None)
            if page is not None:
                from jobpulse.applicator import PROFILE
                from jobpulse.navigation.action_executor import emit_fill_failures
                nav_executor = NavigationActionExecutor(page)
                exec_result = await nav_executor.execute(action, profile=PROFILE)
                ctx.executor_result = exec_result
                domain = extract_domain(pre_url)
                emit_fill_failures(exec_result, domain=domain, source="navigator")
            ctx.action_executed = True
            await asyncio.sleep(1.0)
            post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
```

- [ ] **Step 5: Add `executor_result` field to `StepContext`**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, find the `@dataclass class StepContext` (line 75) and add the field. After the existing fields, before the closing of the dataclass body, add:
```python
    executor_result: Any = None
```

- [ ] **Step 6: Wire `_auth.py` to consume the result**

In `jobpulse/application_orchestrator_pkg/_auth.py`, replace the body of `handle_login` (lines 44–61) with:
```python
    async def handle_login(self, snapshot: dict, platform: str) -> dict:
        """Login via reasoner — analyzes actual page content."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import (
            NavigationActionExecutor, emit_fill_failures,
        )
        from jobpulse.applicator import PROFILE
        from urllib.parse import urlparse

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth login via reasoner: %s — %s",
                    action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            result = await executor.execute(action, profile=PROFILE)
            domain = urlparse(snapshot.get("url", "")).netloc.lower().removeprefix("www.")
            emit_fill_failures(result, domain=domain, source="auth_login")

        import asyncio
        await asyncio.sleep(2.0)
        return self._as_dict(await self.driver.get_snapshot())
```
Apply the same pattern to `handle_signup` (lines 63–80) — change the source label to `"auth_signup"`.

- [ ] **Step 7: Run all jobpulse tests touching nav + auth**

```bash
python -m pytest tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_action_executor_verification.py -v
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add jobpulse/navigation/action_executor.py jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/application_orchestrator_pkg/_auth.py tests/jobpulse/test_action_executor_verification.py
git commit -m "feat(nav): wire ExecutorResult through navigator + auth + optimization signals"
```

---

## Task 5: Extract `_verify_action` helper from `_phase_act`

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (refactor `_phase_act` lines 550–701)
- Test: `tests/jobpulse/test_verify_action_helper.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_verify_action_helper.py`:
```python
"""Tests for the extracted _verify_action helper used by both _phase_act and auth."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, ActionVerification,
)


@pytest.fixture
def navigator():
    nav = FormNavigator.__new__(FormNavigator)  # bypass __init__ for unit test
    nav.driver = AsyncMock()
    nav.driver.get_snapshot = AsyncMock(return_value={
        "url": "https://example.com/step2",
        "page_text_preview": "step 2",
        "has_dialog": False,
        "fields": [], "buttons": [],
    })
    return nav


class TestActionVerification:
    def test_default_unverified(self):
        v = ActionVerification(
            pre_url="https://example.com",
            pre_hash="abc",
            pre_dialog=False,
            post_url="https://example.com",
            post_hash="abc",
            post_dialog=False,
        )
        assert v.url_changed is False
        assert v.content_changed is False

    def test_url_change_detected(self):
        v = ActionVerification(
            pre_url="https://example.com/login",
            pre_hash="abc",
            pre_dialog=False,
            post_url="https://example.com/dashboard",
            post_hash="def",
            post_dialog=False,
        )
        assert v.url_changed is True
        assert v.content_changed is True
```

- [ ] **Step 2: Run, expect ImportError**

```bash
python -m pytest tests/jobpulse/test_verify_action_helper.py -v
```
Expected: ImportError on `ActionVerification`.

- [ ] **Step 3: Add `ActionVerification` dataclass and `_verify_action` helper**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, after the `StepContext` dataclass (around line 100), add:
```python
@dataclass
class ActionVerification:
    pre_url: str
    pre_hash: str
    pre_dialog: bool
    post_url: str
    post_hash: str
    post_dialog: bool
    ghost_click: bool = False
    expected_outcome_met: bool | None = None  # populated in Task 7

    @property
    def url_changed(self) -> bool:
        return self.pre_url != self.post_url

    @property
    def content_changed(self) -> bool:
        return self.url_changed or self.pre_hash != self.post_hash or self.pre_dialog != self.post_dialog
```

Then, inside `class FormNavigator`, add a method (place it near `_detect_ghost_click` at line 248):
```python
    async def _verify_action(
        self,
        pre_snapshot: dict[str, Any],
        post_snapshot: dict[str, Any],
        action_kind: str,
    ) -> ActionVerification:
        """Compute pre/post verification — shared between _phase_act and auth handlers."""
        pre_url = pre_snapshot.get("url", "")
        pre_hash = self._snapshot_content_hash(pre_snapshot)
        pre_dialog = bool(pre_snapshot.get("has_dialog"))
        post_url = post_snapshot.get("url", "")
        post_hash = self._snapshot_content_hash(post_snapshot)
        post_dialog = bool(post_snapshot.get("has_dialog"))
        is_click = action_kind in (
            "click_apply", "click_apply_guess", "click_element",
            "linkedin_direct_apply", "dismiss_overlay", "dismiss_dialog",
            "accept_consent",
        )
        ghost = is_click and self._detect_ghost_click(
            pre_url, pre_hash, pre_dialog, post_url, post_hash, post_dialog,
        )
        return ActionVerification(
            pre_url=pre_url, pre_hash=pre_hash, pre_dialog=pre_dialog,
            post_url=post_url, post_hash=post_hash, post_dialog=post_dialog,
            ghost_click=ghost,
        )
```

- [ ] **Step 4: Run the test**

```bash
python -m pytest tests/jobpulse/test_verify_action_helper.py -v
```
Expected: 2 tests pass.

- [ ] **Step 5: Refactor `_phase_act` to use `_verify_action`**

In `_phase_act`, replace lines 638–647 (the inline `post_url`, `post_hash`, `post_dialog`, `is_click`, ghost-click branch) with:
```python
        verification = await self._verify_action(
            pre_snapshot=ctx.snapshot,
            post_snapshot=post_snap,
            action_kind=act,
        )
        post_url = verification.post_url
        post_hash = verification.post_hash
        post_dialog = verification.post_dialog
        if verification.ghost_click:
            logger.warning("ACT: ghost click detected for action '%s'", act)
```

(The existing force-click retry block lines 648–679 already references `pre_url`, `pre_hash`, `pre_dialog`, `post_snap` — those names are preserved by the refactor above, so the retry block continues to work without further edit.)

- [ ] **Step 6: Run all related tests**

```bash
python -m pytest tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_action_executor_verification.py tests/jobpulse/test_verify_action_helper.py -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_verify_action_helper.py
git commit -m "refactor(nav): extract _verify_action helper from _phase_act"
```

---

## Task 6: Route auth handlers through `_verify_action`

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_auth.py` (`handle_login`, `handle_signup`)
- Test: `tests/jobpulse/test_auth_verification_routing.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_auth_verification_routing.py`:
```python
"""Auth handlers must run pre/post verification — same as _phase_act."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobpulse.application_orchestrator_pkg._auth import AuthHandler


@pytest.fixture
def auth_handler():
    driver = AsyncMock()
    driver.page = AsyncMock()
    driver.page.url = "https://example.com/login"
    driver.get_snapshot = AsyncMock(return_value={
        "url": "https://example.com/dashboard",
        "page_text_preview": "logged in",
        "has_dialog": False,
        "fields": [], "buttons": [],
    })
    accounts = MagicMock()
    gmail = MagicMock()
    nav = AsyncMock()
    nav._verify_action = AsyncMock(return_value=MagicMock(ghost_click=False, url_changed=True))
    return AuthHandler(driver=driver, accounts=accounts, gmail=gmail, navigator=nav)


class TestAuthVerificationRouting:
    @pytest.mark.asyncio
    async def test_login_calls_verify_action(self, auth_handler):
        from jobpulse.page_analysis.page_reasoner import PageAction
        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner") as get_pr:
            get_pr.return_value.reason_sync = MagicMock(return_value=PageAction(
                page_understanding="login", action="fill_and_advance",
                target_text="", reasoning="t", confidence=0.9,
                page_type="login_form", field_fills=[],
                advance_button="Sign in", overlays_to_dismiss=[],
            ))
            snap_pre = {"url": "https://example.com/login",
                        "page_text_preview": "login", "has_dialog": False,
                        "fields": [], "buttons": []}
            await auth_handler.handle_login(snap_pre, platform="generic")
        auth_handler._navigator._verify_action.assert_awaited_once()
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_auth_verification_routing.py -v
```
Expected: failure — `AuthHandler` does not currently take `navigator` param.

- [ ] **Step 3: Update `AuthHandler` to accept the navigator and call `_verify_action`**

In `jobpulse/application_orchestrator_pkg/_auth.py`, update the class. Find `class AuthHandler:` (likely around line 20) and modify `__init__` to accept a `navigator` argument:
```python
    def __init__(self, driver, accounts, gmail, navigator=None):
        self.driver = driver
        self.accounts = accounts
        self.gmail = gmail
        self._navigator = navigator
```

Then update `handle_login` (currently lines 44–61) to:
```python
    async def handle_login(self, snapshot: dict, platform: str) -> dict:
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import (
            NavigationActionExecutor, emit_fill_failures,
        )
        from jobpulse.applicator import PROFILE
        from urllib.parse import urlparse

        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        logger.info("Auth login via reasoner: %s — %s",
                    action.action, action.page_understanding[:60])

        page = getattr(self.driver, "page", None)
        if page is not None:
            executor = NavigationActionExecutor(page)
            result = await executor.execute(action, profile=PROFILE)
            domain = urlparse(snapshot.get("url", "")).netloc.lower().removeprefix("www.")
            emit_fill_failures(result, domain=domain, source="auth_login")

        import asyncio
        await asyncio.sleep(2.0)
        post_snap = self._as_dict(await self.driver.get_snapshot())

        if self._navigator is not None:
            verification = await self._navigator._verify_action(
                pre_snapshot=snapshot, post_snapshot=post_snap, action_kind=action.action,
            )
            if verification.ghost_click:
                logger.warning("Auth login: ghost click detected — page did not progress")
        return post_snap
```

Apply the analogous change to `handle_signup` (lines 63–80) — change the source label and log prefix to `auth_signup`.

- [ ] **Step 4: Find `AuthHandler` instantiation and pass the navigator**

Run:
```bash
grep -n "AuthHandler(" jobpulse/application_orchestrator_pkg/*.py jobpulse/*.py
```
At each instantiation site (likely `FormNavigator.__init__` in `_navigator.py`), pass `navigator=self`. Example pattern:
```python
self.auth = AuthHandler(driver=self.driver, accounts=..., gmail=..., navigator=self)
```

- [ ] **Step 5: Run the auth test + regression**

```bash
python -m pytest tests/jobpulse/test_auth_verification_routing.py tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_verify_action_helper.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_auth.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_auth_verification_routing.py
git commit -m "feat(nav): auth handlers route through _verify_action — ghost-click parity with _phase_act"
```

---

## Task 7: Add `expected_outcome` field to `PageAction` + LLM prompt update

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py` (`PageAction` dataclass at lines 60–82, system prompt at 264–304, `_parse_response` at 327–355, `to_dict` at 71–82)
- Test: `tests/jobpulse/test_page_action_outcome.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_page_action_outcome.py`:
```python
"""Tests for the new expected_outcome contract on PageAction."""
import json
import pytest
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


VALID_OUTCOMES = {"url_changes", "fields_filled", "dialog_dismissed", "page_unchanged", "unknown"}


class TestPageActionOutcomeField:
    def test_default_is_unknown(self):
        a = PageAction(
            page_understanding="t", action="abort", target_text="",
            reasoning="t", confidence=0.0, page_type="unknown",
        )
        assert a.expected_outcome == "unknown"

    def test_outcome_round_trips(self):
        a = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        assert a.to_dict()["expected_outcome"] == "url_changes"

    def test_parser_extracts_outcome(self):
        text = json.dumps({
            "page_understanding": "login form", "action": "fill_and_advance",
            "target_text": "", "field_fills": [], "advance_button": "Sign in",
            "overlays_to_dismiss": [], "reasoning": "t", "confidence": 0.9,
            "page_type": "login_form", "expected_outcome": "url_changes",
        })
        action = PageReasoner._parse_response(text)
        assert action.expected_outcome == "url_changes"

    def test_parser_normalizes_unknown_outcome(self):
        text = json.dumps({
            "page_understanding": "x", "action": "abort", "target_text": "",
            "reasoning": "t", "confidence": 0.0, "page_type": "unknown",
            "expected_outcome": "rocket_launch",
        })
        action = PageReasoner._parse_response(text)
        assert action.expected_outcome == "unknown"
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_page_action_outcome.py -v
```
Expected: AttributeError — field doesn't exist.

- [ ] **Step 3: Add `expected_outcome` to `PageAction`**

In `jobpulse/page_analysis/page_reasoner.py`, modify the `PageAction` dataclass (lines 59–82):
```python
VALID_OUTCOMES = frozenset({
    "url_changes",        # we expect the URL to change after this action
    "fields_filled",      # we expect specific fields to become non-empty
    "dialog_dismissed",   # we expect a dialog/overlay to disappear
    "page_unchanged",     # we expect to stay on this page (e.g. consent acknowledgement only)
    "unknown",            # default — no specific expectation
})


@dataclass
class PageAction:
    page_understanding: str
    action: str
    target_text: str
    reasoning: str
    confidence: float
    page_type: str
    field_fills: list[dict[str, str]] = dc_field(default_factory=list)
    advance_button: str = ""
    overlays_to_dismiss: list[str] = dc_field(default_factory=list)
    expected_outcome: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_understanding": self.page_understanding,
            "action": self.action,
            "target_text": self.target_text,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "page_type": self.page_type,
            "field_fills": self.field_fills,
            "advance_button": self.advance_button,
            "overlays_to_dismiss": self.overlays_to_dismiss,
            "expected_outcome": self.expected_outcome,
        }
```

- [ ] **Step 4: Update `_parse_response` to extract and normalize**

Replace `_parse_response` (lines 327–355) with:
```python
    @staticmethod
    def _parse_response(text: str) -> PageAction:
        try:
            if "{" in text:
                text = text[text.index("{"):text.rindex("}") + 1]
            data = json.loads(text)
            action = data.get("action", "abort")
            if action not in VALID_ACTIONS:
                action = "abort"
            outcome = data.get("expected_outcome", "unknown")
            if outcome not in VALID_OUTCOMES:
                outcome = "unknown"
            return PageAction(
                page_understanding=data.get("page_understanding", ""),
                action=action,
                target_text=data.get("target_text", ""),
                reasoning=data.get("reasoning", ""),
                confidence=float(data.get("confidence", 0.5)),
                page_type=data.get("page_type", "unknown"),
                field_fills=data.get("field_fills", []),
                advance_button=data.get("advance_button", ""),
                overlays_to_dismiss=data.get("overlays_to_dismiss", []),
                expected_outcome=outcome,
            )
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            return PageAction(
                page_understanding=f"Failed to parse LLM response: {exc}",
                action="abort", target_text="", reasoning=text[:200],
                confidence=0.0, page_type="unknown",
            )
```

- [ ] **Step 5: Update the system prompt to require `expected_outcome`**

In `_system_prompt` (lines 264–304), add `expected_outcome` to the JSON schema and rules. Replace the prompt body so the JSON example becomes:
```python
            "Return ONLY a JSON object:\n"
            "{\n"
            '  "page_understanding": "one sentence describing what you see",\n'
            '  "page_type": "job_description|application_form|login_form|signup_form|'
            'email_verification|confirmation|verification_wall|consent_gate|session_expired|expired_job|unknown",\n'
            '  "action": "fill_and_advance|click_element|dismiss_overlay|wait_human|fill_form|done|abort",\n'
            '  "target_text": "button/link text to click (if action is click_element)",\n'
            '  "field_fills": [\n'
            '    {"label": "field label", "value": "what to put", "method": "fill|check_label|check_input|select|skip"}\n'
            "  ],\n"
            '  "advance_button": "text of Next/Submit/Continue button to click after filling",\n'
            '  "overlays_to_dismiss": ["button text to click to dismiss cookie/session overlays"],\n'
            '  "reasoning": "why this action",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "expected_outcome": "url_changes|fields_filled|dialog_dismissed|page_unchanged|unknown"\n'
            "}\n\n"
            "RULES:\n"
            '- For email fields, use value "FROM_PROFILE:email"\n'
            '- For name fields, use "FROM_PROFILE:first_name" or "FROM_PROFILE:last_name"\n'
            '- For phone fields, use "FROM_PROFILE:phone"\n'
            '- For password fields, use "FROM_PROFILE:password"\n'
            "- For consent/agree checkboxes, method = \"check_label\"\n"
            "- For honeypot fields, method = \"skip\"\n"
            "- If a CAPTCHA is present and blocking, action = \"wait_human\"\n"
            "- If overlays are blocking the form, list them in overlays_to_dismiss\n"
            "- If this is an application form ready to fill, action = \"fill_form\"\n"
            "- If the job is no longer available, page_type = \"expired_job\" and action = \"abort\"\n"
            "- If application was submitted, action = \"done\"\n"
            "- action \"fill_and_advance\" = fill the listed fields + click advance_button\n"
            "- action \"click_element\" = click a specific button/link\n"
            "- expected_outcome MUST be one of: url_changes, fields_filled, dialog_dismissed, page_unchanged, unknown\n"
            "- Pick url_changes for navigation/login/submit actions\n"
            "- Pick dialog_dismissed for overlay/consent dismissals\n"
            "- Pick fields_filled for fill_form when no advance is expected on this page\n"
            "- Pick page_unchanged ONLY when no visible state change is expected\n\n"
            "Context: The bot navigates from a job listing to the application form, "
            "fills it out, and stops before final submission."
```

- [ ] **Step 6: Run the parse tests**

```bash
python -m pytest tests/jobpulse/test_page_action_outcome.py -v
```
Expected: all 4 pass.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py tests/jobpulse/test_page_action_outcome.py
git commit -m "feat(reasoner): PageAction.expected_outcome contract + parser + prompt"
```

---

## Task 8: Verify `expected_outcome` inside `_verify_action`

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (`_verify_action`, `_phase_act`)
- Test: `tests/jobpulse/test_verify_action_helper.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_verify_action_helper.py`:
```python
class TestExpectedOutcomeVerification:
    @pytest.mark.asyncio
    async def test_url_changes_outcome_satisfied(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        pre = {"url": "https://example.com/login", "has_dialog": False,
               "page_text_preview": "login", "fields": [], "buttons": []}
        post = {"url": "https://example.com/dashboard", "has_dialog": False,
                "page_text_preview": "dash", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is True

    @pytest.mark.asyncio
    async def test_url_changes_outcome_violated(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        pre = {"url": "https://example.com/login", "has_dialog": False,
               "page_text_preview": "login", "fields": [], "buttons": []}
        post = {"url": "https://example.com/login", "has_dialog": False,
                "page_text_preview": "login", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is False
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_verify_action_helper.py::TestExpectedOutcomeVerification -v
```

- [ ] **Step 3: Add `_check_expected_outcome`**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, inside `class FormNavigator`, after `_verify_action`, add:
```python
    def _check_expected_outcome(
        self, action: PageAction, verification: ActionVerification,
    ) -> ActionVerification:
        """Populate verification.expected_outcome_met based on action.expected_outcome."""
        outcome = getattr(action, "expected_outcome", "unknown")
        if outcome == "unknown":
            verification.expected_outcome_met = None
            return verification
        if outcome == "url_changes":
            verification.expected_outcome_met = verification.url_changed
        elif outcome == "dialog_dismissed":
            verification.expected_outcome_met = (
                verification.pre_dialog and not verification.post_dialog
            )
        elif outcome == "page_unchanged":
            verification.expected_outcome_met = not verification.content_changed
        elif outcome == "fields_filled":
            # Defer to ExecutorResult — _phase_act sets this directly.
            verification.expected_outcome_met = None
        else:
            verification.expected_outcome_met = None
        return verification
```

- [ ] **Step 4: Wire it into `_phase_act`**

In `_phase_act`, after the `verification = await self._verify_action(...)` block from Task 5, add:
```python
        verification = self._check_expected_outcome(action, verification)
        if verification.expected_outcome_met is False:
            logger.warning(
                "ACT: expected_outcome '%s' not met for action '%s'",
                action.expected_outcome, act,
            )
```

- [ ] **Step 5: Run all relevant tests**

```bash
python -m pytest tests/jobpulse/test_verify_action_helper.py tests/jobpulse/test_page_action_outcome.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_verify_action_helper.py
git commit -m "feat(nav): verify expected_outcome inside _verify_action"
```

---

## Task 9: Field-count guard on reasoner output

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py` (after `reason_sync` returns)
- Test: `tests/jobpulse/test_field_count_guard.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_field_count_guard.py`:
```python
"""Tests for the post-LLM field-count guard."""
from unittest.mock import patch, MagicMock
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


def _action(field_fills, action="fill_and_advance"):
    return PageAction(
        page_understanding="t", action=action, target_text="",
        reasoning="t", confidence=0.9, page_type="application_form",
        field_fills=field_fills, advance_button="Submit",
        overlays_to_dismiss=[], expected_outcome="url_changes",
    )


class TestFieldCountGuard:
    def test_full_coverage_passes(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
        ]
        action = _action([
            {"label": "First name", "value": "X", "method": "fill"},
            {"label": "Email", "value": "x@y.com", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        assert guarded.action == "fill_and_advance"
        assert guarded.confidence >= 0.9

    def test_dropped_required_field_lowers_confidence(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
            {"label": "Phone", "input_type": "tel", "required": True},
            {"label": "City", "input_type": "text", "required": True},
            {"label": "Country", "input_type": "text", "required": True},
        ]
        action = _action([
            {"label": "Email", "value": "x@y.com", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # Coverage 1/5 = 20% → guard kicks in
        assert guarded.confidence < 0.5
        assert "field" in guarded.reasoning.lower() or "coverage" in guarded.reasoning.lower()

    def test_optional_fields_are_not_counted(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Newsletter", "input_type": "checkbox", "required": False},
        ]
        action = _action([
            {"label": "First name", "value": "X", "method": "fill"},
        ])
        guarded = pr._apply_field_count_guard(action, snap_fields)
        # Required fields = 1, covered = 1 → 100%
        assert guarded.confidence >= 0.9
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_field_count_guard.py -v
```

- [ ] **Step 3: Add `_apply_field_count_guard`**

In `jobpulse/page_analysis/page_reasoner.py`, inside `class PageReasoner`, add (place after `_set_cache`, before `reason_sync`):
```python
    @staticmethod
    def _apply_field_count_guard(
        action: "PageAction", snapshot_fields: list[dict],
    ) -> "PageAction":
        """If the LLM dropped required fields, lower confidence and annotate.

        Only applies when action is fill-related. Honeypots and skip-marked
        fills do not count toward coverage.
        """
        if action.action not in ("fill_and_advance", "fill_form", "login", "signup"):
            return action

        required = [
            f for f in snapshot_fields
            if f.get("required") and f.get("label")
            and "honeypot" not in (f.get("label") or "").lower()
        ]
        if not required:
            return action

        filled_labels = {
            (f.get("label") or "").strip().lower()
            for f in action.field_fills
            if f.get("method") != "skip"
        }
        required_labels = {(f.get("label") or "").strip().lower() for f in required}
        covered = required_labels & filled_labels
        coverage = len(covered) / len(required_labels) if required_labels else 1.0

        if coverage < 0.8:
            new_confidence = min(action.confidence, coverage)
            return PageAction(
                page_understanding=action.page_understanding,
                action=action.action,
                target_text=action.target_text,
                reasoning=(
                    f"{action.reasoning} | field_coverage={coverage:.0%} "
                    f"({len(covered)}/{len(required_labels)} required fields)"
                ),
                confidence=new_confidence,
                page_type=action.page_type,
                field_fills=action.field_fills,
                advance_button=action.advance_button,
                overlays_to_dismiss=action.overlays_to_dismiss,
                expected_outcome=action.expected_outcome,
            )
        return action
```

- [ ] **Step 4: Apply the guard inside `reason_sync`**

In `reason_sync`, change the line just before the final `return action` from:
```python
        self._set_cache(cache_key, action)
```
to:
```python
        action = self._apply_field_count_guard(action, fields)
        self._set_cache(cache_key, action)
```
(`fields` is already in scope from line 185.)

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/jobpulse/test_field_count_guard.py -v
```
Expected: 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py tests/jobpulse/test_field_count_guard.py
git commit -m "feat(reasoner): field-count guard lowers confidence when LLM drops required fields"
```

---

## Task 10: Generalized cache invalidation on verification failure

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py` (add public `invalidate(snapshot)`)
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (replace ad-hoc delete at lines 583–598; add invalidation on ghost click around line 666)
- Test: `tests/jobpulse/test_cache_invalidation.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_cache_invalidation.py`:
```python
"""Tests for PageReasoner.invalidate(snapshot)."""
import sqlite3
import pytest
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


def _snap(url="https://example.com/page"):
    return {
        "url": url, "page_text_preview": "hello world",
        "dialog_text": "", "fields": [], "buttons": [],
    }


class TestInvalidate:
    def test_invalidate_removes_matching_entry(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        action = PageAction(
            page_understanding="x", action="fill_form", target_text="",
            reasoning="t", confidence=0.9, page_type="application_form",
        )
        snap = _snap()
        key = pr._cache_key(snap["url"], snap["page_text_preview"], snap["dialog_text"],
                            snap["fields"], snap["buttons"])
        pr._set_cache(key, action)
        # Confirm cached
        assert pr._get_cached(key) is not None
        # Invalidate via public API
        removed = pr.invalidate(snap)
        assert removed == 1
        assert pr._get_cached(key) is None

    def test_invalidate_no_entry_returns_zero(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        assert pr.invalidate(_snap()) == 0
```

- [ ] **Step 2: Run, expect AttributeError**

```bash
python -m pytest tests/jobpulse/test_cache_invalidation.py -v
```

- [ ] **Step 3: Add `invalidate(snapshot)` to `PageReasoner`**

In `jobpulse/page_analysis/page_reasoner.py`, inside `class PageReasoner`, add after `_set_cache`:
```python
    def invalidate(self, snapshot: dict[str, Any]) -> int:
        """Delete the cached PageAction for this snapshot. Returns rows removed.

        Called by FormNavigator when verification fails so the next visit
        re-runs the LLM rather than reusing a wrong cached plan.
        """
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        fields = snapshot.get("fields", []) or []
        buttons = snapshot.get("buttons", []) or []
        cache_key = self._cache_key(url, page_text, dialog_text, fields, buttons)
        try:
            with sqlite3.connect(self._db_path) as conn:
                cur = conn.execute(
                    "DELETE FROM reasoning_cache WHERE cache_key = ?", (cache_key,),
                )
                return cur.rowcount
        except Exception as exc:
            logger.debug("PageReasoner.invalidate failed: %s", exc)
            return 0
```

- [ ] **Step 4: Replace the ad-hoc delete in `_phase_act`**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, replace the inline cache-key + DELETE block (lines 583–598) with:
```python
            if wall_bypass_attempts > 2:
                try:
                    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                    get_page_reasoner().invalidate(ctx.snapshot)
                except Exception:
                    pass
                if job:
                    pb_result = await self._try_platform_bypass(ctx.snapshot, job, steps)
                    if pb_result is not None:
                        ctx.post_snapshot = pb_result
                        ctx.action_executed = True
                        return ctx
```

- [ ] **Step 5: Add invalidation when ghost click confirmed**

In `_phase_act`, inside the `else:` branch of the force-click retry (around line 665, where `ctx.ghost_click = True` is set), add the cache invalidation immediately after the `optimization` signal emit. The block should look like:
```python
                else:
                    ctx.ghost_click = True
                    try:
                        from shared.optimization import get_optimization_engine
                        from datetime import UTC, datetime
                        get_optimization_engine().emit(
                            signal_type="failure",
                            source_loop="navigator",
                            domain=extract_domain(pre_url),
                            agent_name="navigator",
                            payload={"param": "ghost_click", "action": act,
                                     "target": action.target_text[:40]},
                            session_id=f"gc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                        )
                    except Exception:
                        pass
                    try:
                        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                        removed = get_page_reasoner().invalidate(ctx.snapshot)
                        if removed:
                            logger.info("Invalidated cached reasoning for ghost-click page")
                    except Exception:
                        pass
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/jobpulse/test_cache_invalidation.py tests/jobpulse/test_verify_action_helper.py -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_cache_invalidation.py
git commit -m "feat(reasoner): public invalidate(snapshot) + invalidate on ghost click"
```

---

## Task 11: Reasoner reflection on verification failure

**Files:**
- Modify: `jobpulse/page_analysis/page_reasoner.py` (add `reason_with_failure(snapshot, failure_context)`)
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (call it on ghost click)
- Test: `tests/jobpulse/test_reasoner_reflection.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_reasoner_reflection.py`:
```python
"""Tests for reason_with_failure — failure-driven re-grounding."""
from unittest.mock import patch, MagicMock
import json
from jobpulse.page_analysis.page_reasoner import PageReasoner


class TestReasonWithFailure:
    def test_failure_context_appears_in_prompt(self, tmp_path):
        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap = {
            "url": "https://example.com/login", "page_text_preview": "login",
            "dialog_text": "", "fields": [], "buttons": [],
        }
        captured_prompts = []
        with patch("jobpulse.page_analysis.page_reasoner.smart_llm_call") as mock_call:
            mock_call.return_value = MagicMock(content=json.dumps({
                "page_understanding": "stuck on login",
                "page_type": "login_form",
                "action": "wait_human",
                "target_text": "",
                "field_fills": [], "advance_button": "",
                "overlays_to_dismiss": [],
                "reasoning": "previous fill bounced",
                "confidence": 0.4,
                "expected_outcome": "page_unchanged",
            }))
            with patch("jobpulse.page_analysis.page_reasoner.get_llm",
                       return_value=MagicMock()):
                def capture_call(*args, **kwargs):
                    captured_prompts.append(args[1])
                    return mock_call.return_value
                mock_call.side_effect = capture_call
                action = pr.reason_with_failure(
                    snap,
                    failure_context="ghost_click on advance_button=Sign in",
                )
        assert action.action == "wait_human"
        # The prompt sent to the LLM must contain the failure context
        all_text = str(captured_prompts)
        assert "ghost_click" in all_text
```

- [ ] **Step 2: Run, expect AttributeError**

```bash
python -m pytest tests/jobpulse/test_reasoner_reflection.py -v
```

- [ ] **Step 3: Add `reason_with_failure`**

In `jobpulse/page_analysis/page_reasoner.py`, inside `class PageReasoner`, add (after `reason_sync`):
```python
    def reason_with_failure(
        self, snapshot: dict[str, Any], failure_context: str,
    ) -> PageAction:
        """Re-call the LLM with a failure context appended — does NOT use cache.

        Called by FormNavigator when a previously-cached action led to a
        ghost click, expected_outcome violation, or persistent fill failure.
        Returns a fresh PageAction the caller can route on.
        """
        url = snapshot.get("url", "")
        page_text = snapshot.get("page_text_preview", "")[:800]
        dialog_text = snapshot.get("dialog_text", "")[:500]
        buttons = snapshot.get("buttons", [])
        fields = snapshot.get("fields", [])
        wall = snapshot.get("verification_wall")

        button_summary = [b.get("text", "")[:40] for b in buttons[:15] if b.get("text")]
        field_summary = []
        for f in fields[:20]:
            label = f.get("label", "?")
            ftype = f.get("input_type", f.get("type", "?"))
            value = f.get("value", "")
            entry = f"{label} ({ftype})"
            if value:
                entry += f" [current: {value[:30]}]"
            field_summary.append(entry)
        wall_info = ""
        if wall:
            wall_info = f"\nCAPTCHA/WALL DETECTED: {wall.get('type', 'unknown')}"

        base_prompt = self._build_prompt(
            url, page_text, dialog_text, button_summary, field_summary, wall_info,
        )
        prompt = (
            base_prompt
            + "\n\nPRIOR ATTEMPT FAILED:\n"
            + failure_context
            + "\n\nYour previous plan did not produce the expected outcome. "
              "Reconsider: is the page type different than you thought? "
              "Is there an overlay you missed? Should this escalate to wait_human?"
        )
        action = self._call_llm(prompt)
        # Do not cache reflection results — they are situational.
        logger.info(
            "PageReasoner.reflect: %s → action=%s, type=%s, confidence=%.2f",
            url[:60], action.action, action.page_type, action.confidence,
        )
        return action
```

- [ ] **Step 4: Wire reflection into `_phase_act` ghost-click branch**

In `_phase_act`, in the same block where Task 10 added cache invalidation on confirmed ghost click, append:
```python
                    try:
                        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                        reflected = get_page_reasoner().reason_with_failure(
                            ctx.snapshot,
                            failure_context=(
                                f"ghost_click on action={act}, "
                                f"target='{action.target_text[:60]}', "
                                f"pre_url={pre_url}, post_url={post_url}"
                            ),
                        )
                        ctx.reflected_action = reflected
                        logger.info(
                            "Reflection produced: %s (confidence=%.2f)",
                            reflected.action, reflected.confidence,
                        )
                    except Exception as exc:
                        logger.debug("Reflection failed: %s", exc)
```

- [ ] **Step 5: Add `reflected_action` to `StepContext`**

In `_navigator.py`, in the `StepContext` dataclass, add (after `executor_result`):
```python
    reflected_action: Any = None
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/jobpulse/test_reasoner_reflection.py tests/jobpulse/test_cache_invalidation.py -v
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/page_analysis/page_reasoner.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_reasoner_reflection.py
git commit -m "feat(reasoner): reason_with_failure — re-ground after ghost click"
```

---

## Task 12: Vision–DOM agreement gate on low confidence

**Files:**
- Modify: `jobpulse/vision_tier.py` (add `classify_page_type_from_screenshot`)
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (call gate when `confidence < 0.7`)
- Test: `tests/jobpulse/test_vision_dom_gate.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_vision_dom_gate.py`:
```python
"""Tests for the vision-DOM agreement gate on low-confidence reasoner output."""
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
import asyncio
from jobpulse.vision_tier import classify_page_type_from_screenshot


class TestVisionPageTypeClassifier:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "")
        result = await classify_page_type_from_screenshot(b"fake_png")
        assert result is None

    @pytest.mark.asyncio
    async def test_extracts_page_type_from_response(self, monkeypatch):
        monkeypatch.setattr("jobpulse.vision_tier.OPENAI_API_KEY", "x")
        fake_resp = MagicMock()
        fake_resp.output_text = "login_form"
        fake_client = MagicMock()
        fake_client.responses.create = MagicMock(return_value=fake_resp)
        with patch("jobpulse.vision_tier.get_openai_client", return_value=fake_client):
            with patch("jobpulse.vision_tier.record_openai_usage"):
                result = await classify_page_type_from_screenshot(b"fake_png")
        assert result == "login_form"
```

- [ ] **Step 2: Run, expect ImportError**

```bash
python -m pytest tests/jobpulse/test_vision_dom_gate.py -v
```

- [ ] **Step 3: Add `classify_page_type_from_screenshot` to `vision_tier.py`**

Append to `jobpulse/vision_tier.py`:
```python
_PAGE_TYPE_PROMPT = (
    "Look at this screenshot of a web page. Classify the page into ONE of these types:\n"
    "  job_description — a job listing with description and Apply button\n"
    "  application_form — a form to fill in personal/application details\n"
    "  login_form — a login page with email + password\n"
    "  signup_form — an account creation page\n"
    "  email_verification — a page asking to check email\n"
    "  confirmation — application submitted successfully\n"
    "  verification_wall — CAPTCHA / Cloudflare / hCaptcha challenge\n"
    "  consent_gate — cookie banner / privacy consent page blocking access\n"
    "  session_expired — session-expired or login-required notice\n"
    "  expired_job — job no longer available / closed / filled\n"
    "  unknown — anything else\n\n"
    "Return ONLY the page type string, nothing else."
)


_VALID_PAGE_TYPES = {
    "job_description", "application_form", "login_form", "signup_form",
    "email_verification", "confirmation", "verification_wall", "consent_gate",
    "session_expired", "expired_job", "unknown",
}


async def classify_page_type_from_screenshot(screenshot_png: bytes) -> str | None:
    """Classify the page type from a rendered screenshot via gpt-4.1-mini.

    Used by FormNavigator as a tiebreaker when DOM-based PageReasoner
    confidence is low. Returns None if the API key is missing or call fails.
    """
    if not OPENAI_API_KEY:
        logger.debug("vision page-type classifier skipped — no OPENAI_API_KEY")
        return None
    try:
        b64_image = base64.b64encode(screenshot_png).decode("ascii")
        client = get_openai_client()
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _PAGE_TYPE_PROMPT},
                    {"type": "input_image",
                     "image_url": f"data:image/png;base64,{b64_image}"},
                ],
            }],
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="vision_tier_pagetype",
                                model_hint="gpt-4.1-mini")
        except Exception:
            pass
        raw = (response.output_text or "").strip().lower().split()[0:1]
        page_type = raw[0] if raw else "unknown"
        page_type = page_type.strip(".,'\" ")
        if page_type not in _VALID_PAGE_TYPES:
            return "unknown"
        return page_type
    except Exception as exc:
        logger.warning("vision page-type classifier failed: %s", exc)
        return None
```

- [ ] **Step 4: Wire the gate into `_phase_act`**

In `_phase_act`, immediately after the `_check_expected_outcome` call from Task 8 (where the warning is logged on `expected_outcome_met is False`), add a confidence-gated vision call. Place this BEFORE the existing step-recording at line 686:
```python
        if action.confidence < 0.7 and act not in ("done", "abort", "wait_human"):
            try:
                from jobpulse.vision_tier import classify_page_type_from_screenshot
                page = getattr(self.driver, "page", None)
                if page is not None:
                    shot = await page.screenshot(type="png")
                    vision_type = await classify_page_type_from_screenshot(shot)
                    if vision_type and vision_type != action.page_type:
                        logger.warning(
                            "Vision-DOM disagreement: reasoner=%s vision=%s — escalating",
                            action.page_type, vision_type,
                        )
                        ctx.vision_disagreement = {
                            "reasoner_type": action.page_type,
                            "vision_type": vision_type,
                        }
                        try:
                            from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                            get_page_reasoner().invalidate(ctx.snapshot)
                        except Exception:
                            pass
            except Exception as exc:
                logger.debug("Vision gate failed: %s", exc)
```

- [ ] **Step 5: Add `vision_disagreement` to `StepContext`**

In `_navigator.py`'s `StepContext` dataclass, add after `reflected_action`:
```python
    vision_disagreement: Any = None
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/jobpulse/test_vision_dom_gate.py -v
```
Expected: 2 tests pass.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/vision_tier.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_vision_dom_gate.py
git commit -m "feat(nav): vision-DOM agreement gate on low-confidence reasoner output"
```

---

## Task 13: Wiring smoke test — full regression

**Files:**
- Run-only: comprehensive regression.

- [ ] **Step 1: Run the full jobpulse test suite touching any changed module**

```bash
python -m pytest tests/jobpulse/ -v 2>&1 | tail -60
```
Expected: all green. If any pre-existing test fails, diagnose before proceeding — verification is supposed to be additive, not change existing behavior.

- [ ] **Step 2: Run a static-import sanity check**

```bash
python -c "
from jobpulse.navigation.action_executor import (
    NavigationActionExecutor, ExecutorResult, emit_fill_failures,
)
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, ActionVerification,
)
from jobpulse.application_orchestrator_pkg._auth import AuthHandler
from jobpulse.page_analysis.page_reasoner import (
    PageReasoner, PageAction, VALID_OUTCOMES, get_page_reasoner,
)
from jobpulse.vision_tier import classify_page_type_from_screenshot
print('All imports OK')
"
```
Expected: prints `All imports OK`.

- [ ] **Step 3: Verify trigger path with grep**

Run, and confirm each grep returns the expected occurrences:
```bash
grep -n "ExecutorResult" jobpulse/navigation/action_executor.py jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/application_orchestrator_pkg/_auth.py
grep -n "_verify_action\|ActionVerification" jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/application_orchestrator_pkg/_auth.py
grep -n "expected_outcome\|_check_expected_outcome\|_apply_field_count_guard" jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/page_analysis/page_reasoner.py
grep -n "invalidate\|reason_with_failure\|classify_page_type_from_screenshot" jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/page_analysis/page_reasoner.py jobpulse/vision_tier.py
```
Expected results:
- `ExecutorResult` referenced in all three files (declaration + 2 consumers).
- `_verify_action` declared in `_navigator.py`, called in `_navigator.py` and `_auth.py`.
- `expected_outcome` in both `page_reasoner.py` and `_navigator.py`.
- `invalidate` and `reason_with_failure` defined in `page_reasoner.py`, called from `_navigator.py`. `classify_page_type_from_screenshot` defined in `vision_tier.py`, called from `_navigator.py`.

- [ ] **Step 4: Real-data smoke — single dry-run application**

```bash
JOBPULSE_LOG_LEVEL=INFO python -m jobpulse.runner job-process-url <a real Greenhouse or Lever URL the user supplies> --dry-run 2>&1 | tee /tmp/nav-hardening-smoke.log
```
Expected log lines confirming wiring:
- `FILL_OBS` instrumentation lines (Task 1) — at least one per filled field.
- `Filled X (verified)` or `Filled X (verified after retry)` (Task 3).
- If a ghost click occurs: `ACT: ghost click detected`, then `Invalidated cached reasoning for ghost-click page`, then `Reflection produced: ...`.
- If reasoner confidence is low: `Vision-DOM disagreement: ...` (only if disagreement actually occurs).

- [ ] **Step 5: Document any unexpected log noise**

If the smoke run produces unexpected warnings or errors from the new code, capture them in `docs/superpowers/plans/2026-05-01-navigator-verification-hardening-followups.md` as a follow-up list. Do not fix them in this branch unless they break the run.

- [ ] **Step 6: Commit log capture**

```bash
git add docs/superpowers/plans/2026-05-01-navigator-verification-hardening-followups.md 2>/dev/null || true
git commit --allow-empty -m "chore: navigator verification hardening — smoke test passed"
```

---

## Task 14: Update CLAUDE.md and rules

**Files:**
- Modify: `jobpulse/CLAUDE.md` (Application Orchestrator section)
- Modify: `.claude/rules/jobs.md` (External Application Engine section)

- [ ] **Step 1: Update `jobpulse/CLAUDE.md`**

Find the "Application Orchestrator (Playwright)" section. Append after the existing "Semantic reasoning" line:
```markdown
**Per-action verification**: Every `NavigationActionExecutor.execute()` returns an `ExecutorResult` with per-fill verified/failed counts. Failures emit `failure` signals via `emit_fill_failures`. Both `_phase_act` and `AuthHandler.handle_login/handle_signup` route through `FormNavigator._verify_action`, which produces an `ActionVerification` (pre/post URL + content hash + ghost-click flag + `expected_outcome_met`).
**Reasoner contract**: `PageAction` includes `expected_outcome` (`url_changes|fields_filled|dialog_dismissed|page_unchanged|unknown`). The reasoner applies a field-count guard that lowers `confidence` when required snapshot fields are dropped from `field_fills`.
**Failure recovery**: On confirmed ghost click → `PageReasoner.invalidate(snapshot)` + `reason_with_failure(snapshot, failure_context)` for re-grounding. When `PageAction.confidence < 0.7`, the navigator runs `classify_page_type_from_screenshot` and escalates on disagreement.
```

- [ ] **Step 2: Update `.claude/rules/jobs.md`**

In the "External Application Engine" section, append:
```markdown
**Verification primitives** (post 2026-05 hardening):
- `NavigationActionExecutor.execute()` reads back every fill, retries once on mismatch, returns `ExecutorResult`.
- `FormNavigator._verify_action(pre, post, action_kind)` is the shared verifier — `_phase_act` and `AuthHandler` both call it.
- `PageAction.expected_outcome` is a contract — set it correctly when extending the reasoner prompt.
- On ghost click: cache invalidation + reflection via `reason_with_failure`. Don't bypass — these run even on auth pages now.
- Low-confidence (`< 0.7`) actions trigger a screenshot-based page-type cross-check; disagreement invalidates the cache.
```

- [ ] **Step 3: Verify markdown renders correctly**

```bash
head -100 jobpulse/CLAUDE.md
head -50 .claude/rules/jobs.md
```
Expected: clean output, no broken sections.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/CLAUDE.md .claude/rules/jobs.md
git commit -m "docs(nav): document verification primitives + expected_outcome contract"
```

---

## Self-Review

**Spec coverage check:**
- ✅ P0 instrumentation — Task 1 (FILL_OBS via verify-on-fill log) folded into Task 3, where read-back is added with logging.
- ✅ P0 read-back-and-retry — Task 3.
- ✅ P1 ExecutorResult structured return — Tasks 1, 2, 4.
- ✅ P1 auth handlers route through verifier — Tasks 5 (extract), 6 (route).
- ✅ P2 expected_outcome contract — Tasks 7, 8.
- ✅ P2 field-count guard — Task 9.
- ✅ P2 generalized cache invalidation — Task 10.
- ✅ P2 reasoner reflection — Task 11.
- ✅ P2 vision–DOM agreement gate — Task 12.
- ✅ Wiring verification — Task 13.
- ✅ Documentation — Task 14.

**Placeholder scan:** No "TBD", "TODO", "implement later", or "similar to Task N" — every task has concrete code blocks.

**Type/name consistency:**
- `ExecutorResult` defined Task 1, used Tasks 2–6 — name consistent.
- `ActionVerification` defined Task 5, extended Task 8 — fields used consistently (`url_changed`, `content_changed`, `expected_outcome_met`).
- `PageAction.expected_outcome` defined Task 7, consumed Task 8 — consistent.
- `_verify_action` signature `(pre_snapshot, post_snapshot, action_kind)` — same in declaration (Task 5) and call sites (Tasks 5, 6).
- `_check_expected_outcome(action, verification)` — defined and called consistently.
- `PageReasoner.invalidate(snapshot)` — same signature in Task 10 declaration, Tasks 10/11/12 callers.
- `reason_with_failure(snapshot, failure_context)` — defined Task 11, called Task 11 with same signature.
- `classify_page_type_from_screenshot(screenshot_png)` — defined Task 12, called Task 12 with same signature.

**Trigger path verification (Task 13 step 3) ensures every new symbol is reachable from the navigator/executor/auth call sites at runtime, not just defined.**

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-01-navigator-verification-hardening.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
