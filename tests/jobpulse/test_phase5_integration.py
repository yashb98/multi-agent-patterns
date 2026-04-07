"""End-to-end integration tests for Phase 5 external application engine."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from jobpulse.application_orchestrator import ApplicationOrchestrator
from jobpulse.ext_models import PageType


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.navigate = AsyncMock()
    b.fill = AsyncMock()
    b.click = AsyncMock()
    b.upload = AsyncMock()
    b.get_snapshot = AsyncMock()
    b.screenshot = AsyncMock(return_value=b"screenshot")
    b.select_option = AsyncMock()
    b.check = AsyncMock()
    # v2 form engine methods
    b.fill_radio_group = AsyncMock()
    b.fill_custom_select = AsyncMock()
    b.fill_autocomplete = AsyncMock()
    b.fill_tag_input = AsyncMock()
    b.fill_date = AsyncMock()
    b.scroll_to = AsyncMock()
    b.force_click = AsyncMock()
    b.check_consent_boxes = AsyncMock()
    b.rescan_after_fill = AsyncMock(return_value={"validation_errors": []})
    b.wait_for_apply = AsyncMock(return_value={"waited_ms": 0, "apply_diagnostics": []})
    # MV3 state persistence — return None by default (no saved progress)
    b.get_form_progress = AsyncMock(return_value=None)
    b.save_form_progress = AsyncMock(return_value=True)
    b.clear_form_progress = AsyncMock(return_value=True)
    return b


@pytest.fixture
def orchestrator(bridge, tmp_path):
    from jobpulse.account_manager import AccountManager
    from jobpulse.navigation_learner import NavigationLearner
    from jobpulse.form_engine.gotchas import GotchasDB

    orch = ApplicationOrchestrator(
        bridge=bridge,
        account_manager=AccountManager(db_path=str(tmp_path / "acc.db")),
        gmail_verifier=MagicMock(),
        navigation_learner=NavigationLearner(db_path=str(tmp_path / "nav.db")),
    )
    orch.gotchas = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    return orch


def _snapshot(buttons=None, fields=None, page_text="", verification_wall=None, has_file_inputs=False, url="https://example.com"):
    return {
        "buttons": buttons or [],
        "fields": fields or [],
        "page_text_preview": page_text,
        "verification_wall": verification_wall,
        "has_file_inputs": has_file_inputs,
        "url": url,
    }


@pytest.mark.asyncio
async def test_direct_form_to_confirmation(orchestrator, bridge):
    form = _snapshot(
        fields=[
            {"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"},
            {"type": "file", "label": "Resume", "current_value": "", "selector": "#resume"},
        ],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    # Sequence: initial → after-cookie-dismiss → nav-loop-form → fill-loop snapshots
    bridge.get_snapshot.side_effect = [form, form, form, confirm, confirm, confirm, confirm, confirm]

    result = await orchestrator.apply(
        url="https://boards.greenhouse.io/acme/jobs/123",
        platform="greenhouse",
        cv_path=Path("/tmp/cv.pdf"),
        profile={"first_name": "Yash", "last_name": "B"},
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_jd_then_form(orchestrator, bridge):
    jd = _snapshot(
        buttons=[{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
        page_text="Software Engineer position",
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    # Sequence: navigate→jd, cookie-dismiss→jd, wait_for_apply→jd(refreshed),
    # apply-click→form, cookie-dismiss→form, fill-loop→confirm...
    bridge.get_snapshot.side_effect = [jd, jd, jd, form, form, confirm, confirm, confirm, confirm]

    result = await orchestrator.apply(
        url="https://example.com/jobs/123", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    bridge.click.assert_any_call("#apply")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_captcha_wall_aborts(orchestrator, bridge):
    wall = _snapshot(verification_wall={"type": "cloudflare", "confidence": 0.9})
    bridge.get_snapshot.side_effect = [wall, wall, wall, wall]

    result = await orchestrator.apply(
        url="https://example.com/apply", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    assert result["success"] is False
    assert "CAPTCHA" in result["error"]


@pytest.mark.asyncio
async def test_sso_google_detected(orchestrator, bridge):
    login = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": "", "selector": "#email"},
            {"type": "password", "label": "Password", "current_value": "", "selector": "#pass"},
        ],
        buttons=[
            {"text": "Sign in with Google", "enabled": True, "selector": "#google-sso"},
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
        ],
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [login, login, form, form, form, confirm, confirm, confirm, confirm]

    result = await orchestrator.apply(
        url="https://careers.acme.com/apply", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    bridge.click.assert_any_call("#google-sso")
    assert result["success"] is True


@pytest.mark.asyncio
@patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "TestPass123!")
async def test_signup_verify_login_apply(orchestrator, bridge):
    signup = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": "", "selector": "#email"},
            {"type": "password", "label": "Password", "current_value": "", "selector": "#pass"},
            {"type": "password", "label": "Confirm Password", "current_value": "", "selector": "#pass2"},
        ],
        buttons=[{"text": "Create Account", "enabled": True, "selector": "#create"}],
    )
    verify_page = _snapshot(page_text="We've sent a verification email. Check your email.")
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [signup, signup, verify_page, verify_page, form, form, form, confirm, confirm, confirm]

    orchestrator.gmail.wait_for_verification.return_value = "https://example.com/verify?t=abc"

    result = await orchestrator.apply(
        url="https://careers.example.com/jobs/456", platform="generic", cv_path=Path("/tmp/cv.pdf"),
        profile={"first_name": "Yash", "last_name": "B"},
    )
    orchestrator.gmail.wait_for_verification.assert_called_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_cookie_banner_dismissed(orchestrator, bridge):
    cookie_page = _snapshot(
        buttons=[
            {"text": "Accept All Cookies", "enabled": True, "selector": "#cookies"},
            {"text": "Apply Now", "enabled": True, "selector": "#apply"},
        ],
        page_text="We use cookies. Software Engineer position.",
    )
    clean_jd = _snapshot(
        buttons=[{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
        page_text="Software Engineer position",
    )
    form = _snapshot(
        fields=[{"type": "text", "label": "First Name", "current_value": "", "selector": "#fname"}],
        buttons=[{"text": "Submit Application", "enabled": True, "selector": "#submit"}],
        has_file_inputs=True,
    )
    confirm = _snapshot(page_text="Thank you for applying!")
    bridge.get_snapshot.side_effect = [cookie_page, clean_jd, clean_jd, form, form, form, confirm, confirm, confirm]

    result = await orchestrator.apply(
        url="https://example.com/jobs", platform="generic", cv_path=Path("/tmp/cv.pdf"),
    )
    bridge.click.assert_any_call("#cookies")
    assert result["success"] is True
