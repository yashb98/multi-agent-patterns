"""Auth handlers must run pre/post verification — same as _phase_act."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobpulse.application_orchestrator_pkg._auth import AuthHandler
from jobpulse.application_orchestrator_pkg._navigator import ActionVerification


@pytest.fixture
def auth_handler():
    """Build AuthHandler with a stubbed orchestrator that exposes a navigator."""
    orch = MagicMock()
    orch.driver = AsyncMock()
    orch.driver.page = AsyncMock()
    orch.driver.page.url = "https://example.com/login"
    orch.driver.get_snapshot = AsyncMock(return_value={
        "url": "https://example.com/dashboard",
        "page_text_preview": "logged in",
        "has_dialog": False,
        "fields": [], "buttons": [],
    })
    orch._navigator = MagicMock()
    orch._navigator._verify_action = AsyncMock(return_value=ActionVerification(
        pre_url="https://example.com/login", pre_hash="a", pre_dialog=False,
        post_url="https://example.com/dashboard", post_hash="b", post_dialog=False,
        ghost_click=False,
    ))
    return AuthHandler(orch)


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
            with patch("jobpulse.applicator.PROFILE", {}):
                snap_pre = {"url": "https://example.com/login",
                            "page_text_preview": "login", "has_dialog": False,
                            "fields": [], "buttons": []}
                await auth_handler.handle_login(snap_pre, platform="generic")
        auth_handler.navigator._verify_action.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_signup_calls_verify_action(self, auth_handler):
        from jobpulse.page_analysis.page_reasoner import PageAction
        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner") as get_pr:
            get_pr.return_value.reason_sync = MagicMock(return_value=PageAction(
                page_understanding="signup", action="fill_and_advance",
                target_text="", reasoning="t", confidence=0.9,
                page_type="signup_form", field_fills=[],
                advance_button="Sign up", overlays_to_dismiss=[],
            ))
            with patch("jobpulse.applicator.PROFILE", {}):
                snap_pre = {"url": "https://example.com/signup",
                            "page_text_preview": "signup", "has_dialog": False,
                            "fields": [], "buttons": []}
                await auth_handler.handle_signup(snap_pre, platform="generic")
        auth_handler.navigator._verify_action.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_login_warns_on_ghost_click(self, auth_handler, caplog):
        from jobpulse.page_analysis.page_reasoner import PageAction
        # Re-stub _verify_action to return ghost_click=True
        auth_handler.navigator._verify_action = AsyncMock(return_value=ActionVerification(
            pre_url="https://example.com/login", pre_hash="a", pre_dialog=False,
            post_url="https://example.com/login", post_hash="a", post_dialog=False,
            ghost_click=True,
        ))
        with patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner") as get_pr:
            get_pr.return_value.reason_sync = MagicMock(return_value=PageAction(
                page_understanding="login", action="click_element",
                target_text="Sign in", reasoning="t", confidence=0.9,
                page_type="login_form", field_fills=[],
                advance_button="", overlays_to_dismiss=[],
            ))
            with patch("jobpulse.applicator.PROFILE", {}):
                snap_pre = {"url": "https://example.com/login",
                            "page_text_preview": "login", "has_dialog": False,
                            "fields": [], "buttons": []}
                import logging
                with caplog.at_level(logging.WARNING):
                    await auth_handler.handle_login(snap_pre, platform="generic")
        assert any("ghost click" in r.message.lower() for r in caplog.records)
