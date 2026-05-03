"""Tests for hybrid PageAnalyzer — DOM detection + vision fallback."""

import pytest
from unittest.mock import AsyncMock, patch
from jobpulse.form_models import PageType
from jobpulse.page_analyzer import PageAnalyzer, _dom_detect


def _snapshot(buttons=None, fields=None, page_text="", verification_wall=None, has_file_inputs=False, url="https://example.com/apply"):
    return {
        "buttons": buttons or [],
        "fields": fields or [],
        "page_text_preview": page_text,
        "verification_wall": verification_wall,
        "has_file_inputs": has_file_inputs,
        "url": url,
    }


# --- DOM detection tests ---

def test_dom_job_description_apply_now():
    s = _snapshot(buttons=[{"text": "Apply Now", "enabled": True}])
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.8


def test_dom_job_description_apply_for_this_job():
    s = _snapshot(buttons=[{"text": "Apply for this job", "enabled": True}])
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION


def test_dom_login_form():
    s = _snapshot(
        fields=[
            {"input_type": "email", "label": "Email address", "current_value": ""},
            {"input_type": "password", "label": "Password", "current_value": ""},
        ],
        buttons=[{"text": "Sign in", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.LOGIN_FORM
    assert confidence >= 0.8


def test_dom_signup_confirm_password():
    s = _snapshot(
        fields=[
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "password", "label": "Password", "current_value": ""},
            {"input_type": "password", "label": "Confirm Password", "current_value": ""},
        ],
        buttons=[{"text": "Create Account", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.SIGNUP_FORM
    assert confidence >= 0.9


def test_dom_signup_register_button():
    s = _snapshot(
        fields=[
            {"input_type": "text", "label": "Full Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "password", "label": "Password", "current_value": ""},
        ],
        buttons=[{"text": "Register", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.SIGNUP_FORM


def test_dom_email_verification():
    s = _snapshot(page_text="We've sent a verification email to your inbox. Please check your email.")
    result, confidence = _dom_detect(s)
    assert result == PageType.EMAIL_VERIFICATION
    assert confidence >= 0.8


def test_dom_application_form():
    s = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "text", "label": "Last Name", "current_value": ""},
            {"input_type": "file", "label": "Resume", "current_value": ""},
        ],
        buttons=[{"text": "Submit Application", "enabled": True}],
        has_file_inputs=True,
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.APPLICATION_FORM


def test_dom_application_form_screening():
    s = _snapshot(
        fields=[
            {"input_type": "select", "label": "Do you require sponsorship?", "current_value": "", "options": ["Yes", "No"]},
            {"input_type": "textarea", "label": "Why are you interested?", "current_value": ""},
        ],
        buttons=[{"text": "Next", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.APPLICATION_FORM


def test_dom_confirmation():
    s = _snapshot(page_text="Thank you for applying! We have received your application.")
    result, confidence = _dom_detect(s)
    assert result == PageType.CONFIRMATION
    assert confidence >= 0.9


def test_dom_verification_wall():
    s = _snapshot(verification_wall={"type": "cloudflare", "confidence": 0.9})
    result, confidence = _dom_detect(s)
    assert result == PageType.VERIFICATION_WALL
    assert confidence >= 0.9


def test_dom_easy_apply_linkedin():
    s = _snapshot(buttons=[{"text": "Easy Apply", "enabled": True}])
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.8


def test_dom_url_hint_linkedin_job_view():
    """LinkedIn job view URL detected as job_description even without apply button in DOM."""
    s = _snapshot(
        buttons=[{"text": "Save", "enabled": True}],
        url="https://www.linkedin.com/jobs/view/12345",
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.6


def test_dom_url_hint_greenhouse():
    s = _snapshot(url="https://boards.greenhouse.io/company/jobs/999")
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.6


def test_dom_url_hint_indeed():
    s = _snapshot(url="https://uk.indeed.com/viewjob?jk=abc")
    result, confidence = _dom_detect(s)
    assert result == PageType.JOB_DESCRIPTION
    assert confidence >= 0.6


def test_dom_unknown_low_confidence():
    """Generic page with no page-type signals → confidence stays low.

    The classifier returns its best guess even when no strong signal exists,
    but the confidence must be < 0.5 so callers escalate to the next tier
    (semantic reasoning / vision). This is the contract — type is best-guess,
    confidence is the trust signal.
    """
    s = _snapshot(
        page_text="Welcome to our company. Learn about our culture.",
        buttons=[{"text": "Learn More", "enabled": True}],
        url="https://example.com/about-us",
    )
    _result, confidence = _dom_detect(s)
    assert confidence < 0.5


# --- Hybrid detection tests ---

@pytest.mark.asyncio
async def test_hybrid_uses_dom_when_confident():
    """High-confidence DOM result skips vision."""
    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)
    s = _snapshot(
        page_text="Thank you for applying!",
    )
    result = await analyzer.detect(s)
    assert result == PageType.CONFIRMATION
    # Vision should NOT have been called
    bridge.screenshot.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_vision():
    """Low-confidence DOM result triggers vision fallback."""
    bridge = AsyncMock()
    bridge.screenshot = AsyncMock(return_value=b"fake_screenshot")
    analyzer = PageAnalyzer(bridge)
    s = _snapshot(
        page_text="Welcome to our company.",
        buttons=[{"text": "Learn More", "enabled": True}],
    )

    with patch("jobpulse.page_analyzer._vision_detect") as mock_vision:
        mock_vision.return_value = (PageType.JOB_DESCRIPTION, 0.85)
        result = await analyzer.detect(s)
        assert result == PageType.JOB_DESCRIPTION
        mock_vision.assert_called_once()


@pytest.mark.asyncio
async def test_hybrid_empty_screenshot_degrades_gracefully():
    """Empty screenshot from bridge doesn't crash — returns DOM result."""
    bridge = AsyncMock()
    bridge.screenshot = AsyncMock(return_value=None)
    analyzer = PageAnalyzer(bridge)
    s = _snapshot(
        page_text="Welcome to our company.",
        buttons=[{"text": "Learn More", "enabled": True}],
    )
    result = await analyzer.detect(s)
    assert result == PageType.UNKNOWN


def test_dom_detect_dialog_with_fields():
    """Modal dialog with form fields classified as APPLICATION_FORM."""
    snapshot = {
        "buttons": [{"text": "Submit application"}],
        "fields": [
            {"label": "First Name", "input_type": "text", "selector": "input[name='firstName']"},
            {"label": "Last Name", "input_type": "text", "selector": "input[name='lastName']"},
        ],
        "page_text_preview": "Apply for Data Scientist role",
        "url": "https://linkedin.com/jobs/view/123",
        "has_file_inputs": False,
        "has_dialog": True,
    }
    page_type, confidence = _dom_detect(snapshot)
    assert page_type == PageType.APPLICATION_FORM
    assert confidence >= 0.85


@pytest.mark.asyncio
async def test_stability_wait_uses_platform_aggregate():
    """PageAnalyzer uses platform aggregate for new domains (no per-domain data)."""
    from unittest.mock import MagicMock

    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)

    mock_exp = MagicMock()
    mock_exp.lookup.return_value = None
    mock_exp.get_platform_aggregate.return_value = {
        "avg_field_count": 10.0,
        "observation_count": 15,
    }
    analyzer.form_experience = mock_exp

    sparse = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
        ],
        url="https://boards.greenhouse.io/newcompany/jobs/123",
    )

    full = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "text", "label": "Last Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "tel", "label": "Phone", "current_value": ""},
            {"input_type": "file", "label": "Resume", "current_value": ""},
        ] + [{"input_type": "select", "label": f"Q{i}", "current_value": ""} for i in range(5)],
        url="https://boards.greenhouse.io/newcompany/jobs/123",
        has_file_inputs=True,
    )
    bridge.get_snapshot = AsyncMock(return_value=full)

    result = await analyzer.detect(sparse)
    assert result == PageType.APPLICATION_FORM
    mock_exp.get_platform_aggregate.assert_called()


@pytest.mark.asyncio
async def test_no_stability_wait_without_form_experience():
    """Without form experience, classify immediately (no bridge.get_snapshot)."""
    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)
    analyzer.form_experience = None

    s = _snapshot(
        fields=[{"input_type": "text", "label": "First Name", "current_value": ""}],
        url="https://unknown-ats.com/apply",
    )
    result = await analyzer.detect(s)
    bridge.get_snapshot.assert_not_called()


def test_dom_session_expired():
    s = _snapshot(page_text="Your session has expired. Please sign in again.")
    result, confidence = _dom_detect(s)
    assert result == PageType.SESSION_EXPIRED
    assert confidence >= 0.9


def test_dom_session_timed_out():
    s = _snapshot(page_text="Session timed out. Please log in to continue.")
    result, confidence = _dom_detect(s)
    assert result == PageType.SESSION_EXPIRED


@pytest.mark.asyncio
async def test_stability_wait_low_confidence_single_observation():
    """With only 1 observation, uses wider tolerance (0.3x)."""
    from unittest.mock import MagicMock

    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)

    mock_exp = MagicMock()
    mock_exp.lookup.return_value = None
    mock_exp.get_platform_aggregate.return_value = {
        "avg_field_count": 10.0,
        "observation_count": 1,  # Only 1 observation
    }
    analyzer.form_experience = mock_exp

    # 3 fields = 30% of 10 = meets 0.3 threshold, should NOT wait
    s = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "tel", "label": "Phone", "current_value": ""},
        ],
        url="https://boards.greenhouse.io/newco/jobs/1",
    )
    result = await analyzer.detect(s)
    # Should classify without waiting (3 >= 10*0.3)
    bridge.get_snapshot.assert_not_called()


def test_dom_consent_gate_privacy():
    s = _snapshot(
        page_text="Please agree to our privacy policy to continue your application.",
        buttons=[{"text": "I Accept", "enabled": True}, {"text": "Decline", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.CONSENT_GATE
    assert confidence >= 0.8


def test_dom_consent_gate_not_cookie_banner():
    """Cookie text alone should NOT trigger CONSENT_GATE."""
    s = _snapshot(
        page_text="We use cookies to improve your experience",
        buttons=[{"text": "Accept All", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result != PageType.CONSENT_GATE
