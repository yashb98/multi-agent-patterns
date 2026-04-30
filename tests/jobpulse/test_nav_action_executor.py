"""Tests for the navigation action executor."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.page_analysis.page_reasoner import PageAction
from jobpulse.navigation.action_executor import NavigationActionExecutor


def _make_action(**kwargs) -> PageAction:
    defaults = {
        "page_understanding": "test",
        "action": "fill_and_advance",
        "target_text": "",
        "reasoning": "test",
        "confidence": 0.9,
        "page_type": "signup_form",
        "field_fills": [],
        "advance_button": "",
        "overlays_to_dismiss": [],
    }
    defaults.update(kwargs)
    return PageAction(**defaults)


@pytest.fixture
def mock_page():
    page = AsyncMock()
    page.url = "https://example.com/apply"
    btn_locator = AsyncMock()
    btn_locator.count = AsyncMock(return_value=1)
    btn_locator.first = AsyncMock()
    btn_locator.first.is_visible = AsyncMock(return_value=True)
    btn_locator.first.click = AsyncMock()
    btn_locator.first.is_checked = AsyncMock(return_value=False)
    btn_locator.first.check = AsyncMock()
    btn_locator.first.fill = AsyncMock()
    btn_locator.first.select_option = AsyncMock()
    page.get_by_role = MagicMock(return_value=btn_locator)
    page.get_by_label = MagicMock(return_value=btn_locator)
    page.get_by_text = MagicMock(return_value=btn_locator)
    page.get_by_placeholder = MagicMock(return_value=btn_locator)
    page.locator = MagicMock(return_value=btn_locator)
    page.fill = AsyncMock()
    page.click = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    return page


@pytest.fixture
def executor(mock_page):
    return NavigationActionExecutor(mock_page)


class TestOverlayDismissal:
    @pytest.mark.asyncio
    async def test_dismisses_overlays_before_filling(self, executor, mock_page):
        action = _make_action(
            overlays_to_dismiss=["Agree", "Continue Working"],
            field_fills=[{"label": "Email", "value": "test@test.com", "method": "fill"}],
        )
        await executor.execute(action, profile={})
        calls = mock_page.get_by_role.call_args_list
        assert any("Agree" in str(c) for c in calls)


class TestFieldFilling:
    @pytest.mark.asyncio
    async def test_fill_resolves_profile_refs(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"}],
        )
        profile = {"email": "user@example.com"}
        await executor.execute(action, profile=profile)
        mock_page.get_by_label.assert_called()

    @pytest.mark.asyncio
    async def test_check_label_clicks_label_not_input(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "I agree with terms", "value": "true", "method": "check_label"}],
        )
        await executor.execute(action, profile={})
        mock_page.get_by_label.assert_called()

    @pytest.mark.asyncio
    async def test_skip_method_does_nothing(self, executor, mock_page):
        action = _make_action(
            field_fills=[{"label": "honeypot", "value": "", "method": "skip"}],
        )
        await executor.execute(action, profile={})
        mock_page.fill.assert_not_called()


class TestAdvanceButton:
    @pytest.mark.asyncio
    async def test_clicks_advance_button(self, executor, mock_page):
        action = _make_action(advance_button="Next")
        await executor.execute(action, profile={})
        mock_page.get_by_role.assert_called()

    @pytest.mark.asyncio
    async def test_no_advance_button_does_not_crash(self, executor, mock_page):
        action = _make_action(advance_button="")
        await executor.execute(action, profile={})


class TestClickElement:
    @pytest.mark.asyncio
    async def test_click_element_uses_target_text(self, executor, mock_page):
        action = _make_action(action="click_element", target_text="Apply Now")
        await executor.execute(action, profile={})
        calls = mock_page.get_by_role.call_args_list
        assert any("Apply Now" in str(c) for c in calls)
