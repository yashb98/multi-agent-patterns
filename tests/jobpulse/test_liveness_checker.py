# tests/jobpulse/test_liveness_checker.py

import pytest

from jobpulse.liveness_checker import classify_liveness, LivenessResult

# Enough body text to pass the 300-char threshold in non-short-body cases.
_LONG_BODY = "x" * 400


def test_active_with_apply_button():
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/123",
        body=_LONG_BODY,
        apply_control_text="Apply now",
    )
    assert result.status == "active"


def test_expired_404():
    result = classify_liveness(
        status_code=404,
        url="https://example.com/jobs/123",
        body=_LONG_BODY,
    )
    assert result.status == "expired"
    assert "404" in result.reason


def test_expired_410():
    result = classify_liveness(
        status_code=410,
        url="https://example.com/jobs/123",
        body=_LONG_BODY,
    )
    assert result.status == "expired"
    assert "410" in result.reason


def test_expired_greenhouse_error_redirect():
    result = classify_liveness(
        status_code=200,
        url="https://boards.greenhouse.io/company/jobs/999?error=true",
        body=_LONG_BODY,
    )
    assert result.status == "expired"
    assert "Greenhouse" in result.reason


def test_expired_no_longer_available():
    body = _LONG_BODY + " This job is no longer available. "
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/123",
        body=body,
    )
    assert result.status == "expired"
    assert "no longer available" in result.reason.lower()


def test_expired_position_filled():
    body = _LONG_BODY + " Position has been filled. "
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/123",
        body=body,
    )
    assert result.status == "expired"
    assert "filled" in result.reason.lower()


def test_expired_short_body():
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/123",
        body="Short page.",
    )
    assert result.status == "expired"
    assert "short" in result.reason.lower()


def test_uncertain_no_apply_button():
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/123",
        body=_LONG_BODY,
        apply_control_text="",
    )
    assert result.status == "uncertain"


def test_expired_listing_page_redirect():
    body = _LONG_BODY + " 142 jobs found matching your search. "
    result = classify_liveness(
        status_code=200,
        url="https://example.com/jobs/search",
        body=body,
        apply_control_text="Apply",  # apply text present but listing check runs first
    )
    assert result.status == "expired"
    assert "Listing page" in result.reason


def test_expired_german():
    body = _LONG_BODY + " Diese Stelle ist nicht mehr besetzt. "
    result = classify_liveness(
        status_code=200,
        url="https://example.de/jobs/456",
        body=body,
    )
    assert result.status == "expired"


def test_expired_french():
    body = _LONG_BODY + " Offre expirée. "
    result = classify_liveness(
        status_code=200,
        url="https://example.fr/jobs/789",
        body=body,
    )
    assert result.status == "expired"
