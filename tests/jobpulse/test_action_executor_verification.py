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
