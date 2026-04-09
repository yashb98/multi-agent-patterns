"""Verification wall detection for job platform scrapers.

Detects Cloudflare Turnstile, reCAPTCHA, hCaptcha, text challenges, and HTTP blocks.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class VerificationResult:
    """Result of a verification wall detection check."""

    wall_type: str
    confidence: float
    page_url: str
    page_title: str
    screenshot_path: str | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Detection patterns (ordered — first match wins)
# ---------------------------------------------------------------------------

_SELECTOR_PATTERNS: list[tuple[str, str, float]] = [
    ("#challenge-running", "cloudflare", 0.95),
    (".cf-turnstile", "cloudflare", 0.95),
    ("#cf-challenge-running", "cloudflare", 0.90),
    (".g-recaptcha", "recaptcha", 0.90),
    ("#recaptcha-anchor", "recaptcha", 0.90),
    ("[data-sitekey]", "recaptcha", 0.80),
    (".h-captcha", "hcaptcha", 0.90),
]

_IFRAME_PATTERNS: list[tuple[str, str, float]] = [
    ("challenges.cloudflare.com", "cloudflare", 0.95),
    ("google.com/recaptcha", "recaptcha", 0.90),
    ("hcaptcha.com", "hcaptcha", 0.90),
]

_TEXT_PATTERNS: list[tuple[str, str, float]] = [
    ("verify you are human", "text_challenge", 0.85),
    ("please verify", "text_challenge", 0.70),
    ("are you a robot", "text_challenge", 0.85),
    ("unusual traffic", "text_challenge", 0.80),
    ("automated requests", "text_challenge", 0.80),
    ("suspected automated", "text_challenge", 0.80),
    ("confirm you're not a robot", "text_challenge", 0.85),
    ("complete the security check", "text_challenge", 0.80),
    ("access denied", "http_block", 0.75),
    ("403 forbidden", "http_block", 0.90),
    ("you have been blocked", "http_block", 0.90),
]


def detect_verification_wall(
    page: Any, *, expected_results: bool = False
) -> VerificationResult | None:
    """Detect verification walls on a Playwright page.

    Args:
        page: Playwright page object.
        expected_results: If True, flag suspiciously empty pages as anomalies.

    Returns:
        VerificationResult if a wall is detected, None otherwise.
    """
    # Safely get page metadata
    try:
        page_url = page.url
    except Exception:
        page_url = "unknown"

    try:
        page_title = page.title()
    except Exception:
        page_title = "unknown"

    def _result(wall_type: str, confidence: float) -> VerificationResult:
        logger.warning(
            "Verification wall detected: %s (confidence=%.2f) on %s",
            wall_type,
            confidence,
            page_url,
        )
        return VerificationResult(
            wall_type=wall_type,
            confidence=confidence,
            page_url=page_url,
            page_title=page_title,
        )

    # 1. Check CSS selectors
    for selector, wall_type, confidence in _SELECTOR_PATTERNS:
        try:
            if page.query_selector(selector) is not None:
                return _result(wall_type, confidence)
        except Exception as e:
            logger.debug("Selector check failed for %s: %s", selector, e)

    # 2. Check iframe URLs
    try:
        for frame in page.frames:
            frame_url = getattr(frame, "url", "")
            for url_substr, wall_type, confidence in _IFRAME_PATTERNS:
                if url_substr in frame_url:
                    return _result(wall_type, confidence)
    except Exception as e:
        logger.debug("Iframe check failed: %s", e)

    # 3. Get body text
    body_text = ""
    try:
        body_text = page.inner_text("body")
    except Exception:
        try:
            body_text = page.inner_text()
        except Exception as e:
            logger.debug("Body text extraction failed: %s", e)

    # 4. Check text patterns (case-insensitive)
    body_lower = body_text.lower()
    for pattern, wall_type, confidence in _TEXT_PATTERNS:
        if re.search(re.escape(pattern), body_lower):
            return _result(wall_type, confidence)

    # 5. Empty anomaly check
    if expected_results and len(body_text) < 500:
        return _result("empty_anomaly", 0.5)

    return None


def simulate_human_interaction(page: Any) -> None:
    """Simulate human-like page interaction to avoid bot detection.

    Scrolls, moves mouse, and adds random delays. Never raises exceptions.
    """
    try:
        # Reading delay
        time.sleep(random.uniform(1.0, 3.0))

        # Scroll down in small increments
        scroll_distance = random.randint(300, 600)
        for offset in range(0, scroll_distance, 50):
            try:
                page.evaluate(f"window.scrollBy(0, 50)")
                time.sleep(random.uniform(0.05, 0.15))
            except Exception:
                break

        # Random mouse movement
        try:
            page.mouse.move(
                random.randint(200, 800),
                random.randint(200, 500),
            )
        except Exception as e:
            logger.debug("Mouse movement failed: %s", e)

        # Final pause
        time.sleep(random.uniform(0.5, 1.5))

    except Exception:
        # Never raise — this is best-effort
        logger.debug("simulate_human_interaction failed silently")
