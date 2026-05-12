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


# ---------------------------------------------------------------------------
# check_liveness_batch wiring — exercises the kwargs contract end-to-end.
# Regression for S9 audit B-1: kwargs were 'status'/'final_url'/'body_text'/
# 'apply_controls' (none of which classify_liveness accepts), so every batch
# raised TypeError and was silently dropped by scan_pipeline's blanket except.
# ---------------------------------------------------------------------------


class _StubResp:
    def __init__(self, status_code: int, url: str, text: str) -> None:
        self.status_code = status_code
        self.url = url
        self.text = text


class _StubClient:
    def __init__(self, *_, **__) -> None:
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, url):
        self.calls.append(url)
        # Two URLs in the test: an expired 404, and a live posting.
        if "expired" in url:
            return _StubResp(404, url, "x" * 400)
        return _StubResp(200, url, "x" * 400 + " Apply now ")


def test_check_liveness_batch_passes_correct_kwargs(monkeypatch):
    """End-to-end: real check_liveness_batch through real classify_liveness."""
    import httpx
    from jobpulse import job_scanner

    monkeypatch.setattr(httpx, "Client", _StubClient)
    monkeypatch.setattr(job_scanner.httpx, "Client", _StubClient)

    listings = [
        {"url": "https://example.com/jobs/expired-1"},
        {"url": "https://example.com/jobs/live-1"},
    ]
    alive, expired = job_scanner.check_liveness_batch(listings)

    assert len(expired) == 1
    assert expired[0]["url"] == "https://example.com/jobs/expired-1"
    assert "404" in expired[0]["liveness"]
    assert len(alive) == 1
    assert alive[0]["url"] == "https://example.com/jobs/live-1"
