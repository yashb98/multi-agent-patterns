"""Tests for jobpulse.verification_detector — verification wall detection and human simulation."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


def _make_mock_page(
    *,
    url: str = "https://example.com/jobs",
    title: str = "Job Listing",
    body_text: str = "This is a normal job page with enough content to pass checks." * 20,
    selectors: dict[str, bool] | None = None,
    frames: list[dict[str, str]] | None = None,
) -> MagicMock:
    """Build a mock Playwright page with configurable properties."""
    page = MagicMock()
    page.url = url
    page.title.return_value = title

    # inner_text: return body_text when called with "body", otherwise empty string
    def _inner_text(sel: str = "body") -> str:
        if sel == "body":
            return body_text
        return ""

    page.inner_text = MagicMock(side_effect=_inner_text)

    # query_selector: return a mock element if selector is in the dict, else None
    _selectors = selectors or {}

    def _query_selector(sel: str) -> MagicMock | None:
        if sel in _selectors:
            return MagicMock()
        return None

    page.query_selector = MagicMock(side_effect=_query_selector)

    # frames: list of mock frame objects with .url attribute
    mock_frames: list[MagicMock] = []
    for frame_info in (frames or []):
        f = MagicMock()
        f.url = frame_info["url"]
        mock_frames.append(f)
    page.frames = mock_frames

    # mouse mock for simulate_human_interaction
    page.mouse = MagicMock()
    page.evaluate = MagicMock()

    return page


# ---------------------------------------------------------------------------
# detect_verification_wall tests
# ---------------------------------------------------------------------------

class TestDetectVerificationWall:
    """Tests for detect_verification_wall()."""

    def test_clean_page_returns_none(self) -> None:
        """1. Clean page with normal content returns None."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page()
        result = detect_verification_wall(page)
        assert result is None

    def test_cloudflare_challenge_running(self) -> None:
        """2. Cloudflare #challenge-running selector detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={"#challenge-running": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "cloudflare"
        assert result.confidence >= 0.90

    def test_cloudflare_cf_turnstile(self) -> None:
        """3. Cloudflare .cf-turnstile selector detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={".cf-turnstile": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "cloudflare"
        assert result.confidence >= 0.90

    def test_recaptcha_selector(self) -> None:
        """4. reCAPTCHA .g-recaptcha selector detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={".g-recaptcha": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "recaptcha"
        assert result.confidence >= 0.85

    def test_hcaptcha_selector(self) -> None:
        """5. hCaptcha .h-captcha selector detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={".h-captcha": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "hcaptcha"
        assert result.confidence >= 0.85

    def test_text_verify_you_are_human(self) -> None:
        """6. Text 'verify you are human' detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Please verify you are human to continue.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_text_unusual_traffic(self) -> None:
        """7. Text 'unusual traffic' detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="We detected unusual traffic from your network.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_text_are_you_a_robot(self) -> None:
        """8. Text 'are you a robot' detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Are you a robot? Complete this check.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_cloudflare_iframe(self) -> None:
        """9. Cloudflare iframe URL detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            frames=[{"url": "https://challenges.cloudflare.com/cdn-cgi/challenge"}]
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "cloudflare"
        assert result.confidence >= 0.90

    def test_recaptcha_iframe(self) -> None:
        """10. reCAPTCHA iframe URL detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            frames=[{"url": "https://www.google.com/recaptcha/api2/anchor"}]
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "recaptcha"

    def test_hcaptcha_iframe(self) -> None:
        """11. hCaptcha iframe URL detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            frames=[{"url": "https://newassets.hcaptcha.com/captcha/v1/something"}]
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "hcaptcha"

    def test_empty_anomaly_expected_results(self) -> None:
        """12. Empty anomaly when expected_results=True and body < 500 chars."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Short page.")
        result = detect_verification_wall(page, expected_results=True)
        assert result is not None
        assert result.wall_type == "empty_anomaly"
        assert result.confidence == pytest.approx(0.5, abs=0.01)

    def test_normal_job_page_not_flagged(self) -> None:
        """13. Normal job page with real content NOT flagged."""
        from jobpulse.verification_detector import detect_verification_wall

        body = (
            "Senior Software Engineer at Acme Corp. Requirements: 5+ years Python, "
            "experience with distributed systems, microservices, AWS. Benefits include "
            "health insurance, 401k matching, unlimited PTO. Apply now! " * 10
        )
        page = _make_mock_page(body_text=body)
        result = detect_verification_wall(page)
        assert result is None

    def test_case_insensitive_text_matching(self) -> None:
        """14. Case-insensitive text matching ('VERIFY YOU ARE HUMAN')."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="VERIFY YOU ARE HUMAN")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_http_block_403_forbidden(self) -> None:
        """15. HTTP block text '403 forbidden' detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="403 Forbidden - Access to this resource is denied.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "http_block"

    def test_access_denied_text(self) -> None:
        """16. 'access denied' text detected."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Access Denied. You do not have permission.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "http_block"

    def test_multiple_selectors_first_match_wins(self) -> None:
        """17. Multiple selectors present — first match wins (cloudflare before recaptcha)."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            selectors={"#challenge-running": True, ".g-recaptcha": True}
        )
        result = detect_verification_wall(page)
        assert result is not None
        # #challenge-running is checked before .g-recaptcha in _SELECTOR_PATTERNS
        assert result.wall_type == "cloudflare"

    def test_data_sitekey_attribute(self) -> None:
        """18. Page with [data-sitekey] attribute detected as recaptcha."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={"[data-sitekey]": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "recaptcha"
        assert result.confidence >= 0.75

    def test_result_has_correct_fields(self) -> None:
        """VerificationResult has all required fields."""
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            url="https://indeed.com/job/123",
            title="Job Page",
            selectors={".cf-turnstile": True},
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.page_url == "https://indeed.com/job/123"
        assert result.page_title == "Job Page"
        assert isinstance(result.detected_at, datetime)
        assert result.screenshot_path is None


# ---------------------------------------------------------------------------
# simulate_human_interaction tests
# ---------------------------------------------------------------------------

class TestSimulateHumanInteraction:
    """Tests for simulate_human_interaction()."""

    @patch("jobpulse.verification_detector.time")
    def test_calls_scroll_and_mouse(self, mock_time: MagicMock) -> None:
        """19. Calls page.evaluate (scroll) and page.mouse.move."""
        from jobpulse.verification_detector import simulate_human_interaction

        # Make time.sleep a no-op for speed
        mock_time.sleep = MagicMock()

        page = _make_mock_page()
        simulate_human_interaction(page)

        # Should have called page.evaluate at least once (for scrolling)
        assert page.evaluate.call_count >= 1
        # Should have called mouse.move at least once
        assert page.mouse.move.call_count >= 1

    @patch("jobpulse.verification_detector.time")
    def test_does_not_raise_on_evaluate_error(self, mock_time: MagicMock) -> None:
        """20. Does not raise even if page.evaluate throws."""
        from jobpulse.verification_detector import simulate_human_interaction

        mock_time.sleep = MagicMock()

        page = _make_mock_page()
        page.evaluate.side_effect = Exception("Browser crashed")
        page.mouse.move.side_effect = Exception("Mouse error")

        # Should NOT raise
        simulate_human_interaction(page)
