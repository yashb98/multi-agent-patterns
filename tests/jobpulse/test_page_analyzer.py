"""Tests for hybrid PageAnalyzer — DOM detection + vision fallback."""

import pytest
from unittest.mock import AsyncMock, patch
from jobpulse.ext_models import PageType
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
            {"type": "email", "label": "Email address", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
        ],
        buttons=[{"text": "Sign in", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.LOGIN_FORM
    assert confidence >= 0.8


def test_dom_signup_confirm_password():
    s = _snapshot(
        fields=[
            {"type": "email", "label": "Email", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
            {"type": "password", "label": "Confirm Password", "current_value": ""},
        ],
        buttons=[{"text": "Create Account", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.SIGNUP_FORM
    assert confidence >= 0.9


def test_dom_signup_register_button():
    s = _snapshot(
        fields=[
            {"type": "text", "label": "Full Name", "current_value": ""},
            {"type": "email", "label": "Email", "current_value": ""},
            {"type": "password", "label": "Password", "current_value": ""},
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
            {"type": "text", "label": "First Name", "current_value": ""},
            {"type": "text", "label": "Last Name", "current_value": ""},
            {"type": "file", "label": "Resume", "current_value": ""},
        ],
        buttons=[{"text": "Submit Application", "enabled": True}],
        has_file_inputs=True,
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.APPLICATION_FORM


def test_dom_application_form_screening():
    s = _snapshot(
        fields=[
            {"type": "select", "label": "Do you require sponsorship?", "current_value": "", "options": ["Yes", "No"]},
            {"type": "textarea", "label": "Why are you interested?", "current_value": ""},
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
    s = _snapshot(
        page_text="Welcome to our company. Learn about our culture.",
        buttons=[{"text": "Learn More", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.UNKNOWN
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
