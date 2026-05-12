"""Job scanner modules — one per platform.

Shared utilities used by all platform scanners live here.
"""

from __future__ import annotations

import hashlib
import random
import time
from typing import Any

from shared.logging_config import get_logger

from jobpulse.scan_learning import ScanLearningEngine

logger = get_logger(__name__)

MAX_REQUESTS_PER_PLATFORM = 50

_USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
]


def make_job_id(url: str) -> str:
    """SHA-256 of the normalised URL — used as the deduplication key."""
    if not url:
        import uuid
        logger.warning("make_job_id: received empty URL, generating random ID")
        return f"unknown-{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()[:16]


def random_ua() -> str:
    return random.choice(_USER_AGENTS)


def anti_detection_sleep() -> None:
    """Sleep 2-8 seconds between requests to avoid rate-limiting."""
    time.sleep(random.uniform(2.0, 8.0))


def to_float(value: Any) -> float | None:
    """Coerce a JSON value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def url_encode(text: str) -> str:
    """Percent-encode a string for use in a URL query parameter."""
    import urllib.parse

    return urllib.parse.quote(text, safe="")


class SessionSignals:
    """Track signals for the current scan session."""

    def __init__(self, platform: str, user_agent: str) -> None:
        self.platform = platform
        self.start_time = time.monotonic()
        self.request_times: list[float] = []
        self.user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:8]
        self.browser_fingerprint = hashlib.sha256(
            f"{platform}:1280x800:{user_agent}".encode()
        ).hexdigest()[:8]
        self.was_fresh_session = True
        self.simulated_mouse = False
        self.referrer_chain = "direct"
        self.last_query = ""
        self.waited_for_load = True
        self.last_load_time_ms = 0

    def record_request(self) -> None:
        self.request_times.append(time.monotonic())

    @property
    def requests_count(self) -> int:
        return len(self.request_times)

    @property
    def avg_delay(self) -> float:
        if len(self.request_times) < 2:
            return 0.0
        deltas = [
            self.request_times[i] - self.request_times[i - 1]
            for i in range(1, len(self.request_times))
        ]
        return sum(deltas) / len(deltas)

    @property
    def session_age(self) -> float:
        return time.monotonic() - self.start_time


def handle_block(engine: ScanLearningEngine, platform: str, wall: Any, signals: SessionSignals) -> None:
    """Record block event, start cooldown, update rules, optionally run LLM analysis.

    ``wall`` accepts either a ``VerificationWall`` (LinkedIn cognitive path)
    or a plain string like ``"http_429"`` (httpx scanners). Pre-fix this only
    worked for the wall-object shape, leaving httpx-based scanners with no
    way to log blocks (pipeline-bugs M-9.D).
    """
    wall_type = wall.wall_type if hasattr(wall, "wall_type") else str(wall)
    engine.record_event(
        platform=platform,
        requests_in_session=signals.requests_count,
        avg_delay=signals.avg_delay,
        session_age_seconds=signals.session_age,
        user_agent_hash=signals.user_agent_hash,
        was_fresh_session=signals.was_fresh_session,
        used_vpn=False,
        simulated_mouse=signals.simulated_mouse,
        referrer_chain=signals.referrer_chain,
        search_query=signals.last_query,
        pages_before_block=signals.requests_count,
        browser_fingerprint=signals.browser_fingerprint,
        waited_for_page_load=signals.waited_for_load,
        page_load_time_ms=signals.last_load_time_ms,
        outcome="blocked",
        wall_type=wall_type,
    )
    engine.start_cooldown(platform, wall_type)
    engine.update_learned_rules(platform)
    if engine.should_run_llm_analysis():
        engine.run_llm_analysis(platform)


def record_success(engine: ScanLearningEngine, platform: str, signals: SessionSignals) -> None:
    """Record a successful scan session."""
    if signals.requests_count > 0:
        engine.record_event(
            platform=platform,
            requests_in_session=signals.requests_count,
            avg_delay=signals.avg_delay,
            session_age_seconds=signals.session_age,
            user_agent_hash=signals.user_agent_hash,
            was_fresh_session=signals.was_fresh_session,
            used_vpn=False,
            simulated_mouse=signals.simulated_mouse,
            referrer_chain=signals.referrer_chain,
            search_query=signals.last_query,
            pages_before_block=signals.requests_count,
            browser_fingerprint=signals.browser_fingerprint,
            waited_for_page_load=signals.waited_for_load,
            page_load_time_ms=signals.last_load_time_ms,
            outcome="success",
            wall_type=None,
        )
        engine.reset_cooldown(platform)
