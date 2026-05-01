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

    STANDARD_CLOSE = {"Not now", "No thanks", "Dismiss", "Close", "Got it", "Maybe later", "Skip"}

    def _make_locator(matches: bool):
        loc = AsyncMock()
        loc.count = AsyncMock(return_value=1 if matches else 0)
        loc.first = AsyncMock()
        loc.first.is_visible = AsyncMock(return_value=matches)
        loc.first.click = AsyncMock()
        loc.first.is_checked = AsyncMock(return_value=False)
        loc.first.check = AsyncMock()
        loc.first.fill = AsyncMock()
        loc.first.select_option = AsyncMock()
        return loc

    matching_locator = _make_locator(matches=True)
    empty_locator = _make_locator(matches=False)

    def get_by_role(role, *, name=None, exact=False):
        # Return empty locator for standard close-button names so
        # _dismiss_overlays falls through to LLM-suggested overlay texts.
        if name in STANDARD_CLOSE:
            return empty_locator
        return matching_locator

    def get_by_locator(selector):
        # The aria-label close/dismiss locator path uses .first directly on the
        # locator, so we return empty_locator to prevent early exit there too.
        if "aria-label" in str(selector):
            return empty_locator
        return matching_locator

    page.get_by_role = MagicMock(side_effect=get_by_role)
    page.get_by_label = MagicMock(return_value=matching_locator)
    page.get_by_text = MagicMock(return_value=matching_locator)
    page.get_by_placeholder = MagicMock(return_value=matching_locator)
    page.locator = MagicMock(side_effect=get_by_locator)
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
