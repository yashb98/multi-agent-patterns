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

    def test_has_failures_reflects_fill_failures(self):
        r = ExecutorResult()
        assert r.has_failures is False
        r.record_fill_failure("Name", expected="Alice", actual="")
        assert r.has_failures is True


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
        assert result.clicks_attempted == 1


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

    @pytest.mark.asyncio
    async def test_short_value_no_substring_false_positive(self, executor, mock_page):
        # Short numeric fills must use exact match — '1' should NOT verify against '10'
        loc = mock_page.get_by_label.return_value.first
        loc.input_value = AsyncMock(return_value="10")
        action = _make_action(field_fills=[
            {"label": "Years", "value": "1", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        # First read-back returns "10" (mismatch under length guard);
        # retry also returns "10" → recorded as failure
        assert result.fills_verified == 0
        assert len(result.fills_failed) == 1
        assert result.fills_failed[0]["label"] == "Years"
        assert result.fills_failed[0]["expected"] == "1"
        assert result.fills_failed[0]["actual"] == "10"

    @pytest.mark.asyncio
    async def test_retry_exception_records_failure(self, executor, mock_page):
        # First fill mismatches → retry → retry's fill() raises
        loc = mock_page.get_by_label.return_value.first
        loc.input_value = AsyncMock(return_value="")  # mismatch
        loc.fill = AsyncMock(side_effect=[None, RuntimeError("element detached")])
        action = _make_action(field_fills=[
            {"label": "Email", "value": "user@x.com", "method": "fill"}
        ])
        result = await executor.execute(action, profile={})
        assert result.fills_verified == 0
        assert len(result.fills_failed) == 1
        assert result.fills_failed[0]["label"] == "Email"


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
