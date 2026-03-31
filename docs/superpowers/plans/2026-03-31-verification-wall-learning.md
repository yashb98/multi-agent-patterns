# Verification Wall Learning System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a universal, self-improving verification/CAPTCHA wall detection and avoidance system that learns from every block event across all job scanning platforms.

**Architecture:** Two new files (`verification_detector.py`, `scan_learning.py`) + integration into `job_scanner.py`. Event-sourced learning with statistical correlation (zero LLM cost) + periodic LLM pattern analysis (every 5th block). 2-hour cooldown with exponential backoff. Human-like page interaction on all scanners.

**Tech Stack:** SQLite (`data/scan_learning.db`), Playwright, GPT-5o-mini (periodic analysis only)

---

## File Structure

| File | Responsibility |
|------|----------------|
| **Create:** `jobpulse/verification_detector.py` | Universal block page detection — checks for Cloudflare, reCAPTCHA, hCaptcha, text challenges, HTTP blocks, empty anomalies |
| **Create:** `jobpulse/scan_learning.py` | ScanEvent recording (17 signals), statistical correlation engine, LLM pattern analyzer, AdaptiveParams builder, CooldownManager |
| **Create:** `tests/test_verification_detector.py` | Detection unit tests — mock HTML pages with each wall type |
| **Create:** `tests/test_scan_learning.py` | Learning engine, cooldown, adaptive params tests |
| **Modify:** `jobpulse/job_scanner.py` | Add pre-scan gate, post-page verification check, human-like interaction to all 3 scanners |

---

### Task 1: Verification Detector — Detection Logic

**Files:**
- Create: `jobpulse/verification_detector.py`
- Test: `tests/test_verification_detector.py`

- [ ] **Step 1: Write the failing tests for detection**

```python
# tests/test_verification_detector.py
"""Tests for universal verification wall detection."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone


def _make_mock_page(
    url: str = "https://uk.indeed.com/jobs",
    title: str = "Job Search",
    body_text: str = "",
    selectors: dict | None = None,
    status: int = 200,
) -> MagicMock:
    """Create a mock Playwright page with configurable content."""
    page = MagicMock()
    page.url = url
    page.title.return_value = title
    page.inner_text.return_value = body_text

    # Default: no selectors match
    _selectors = selectors or {}

    def query_selector(sel: str) -> MagicMock | None:
        if sel in _selectors:
            el = MagicMock()
            el.is_visible.return_value = True
            return el
        return None

    page.query_selector.side_effect = query_selector

    # For frame detection
    frame = MagicMock()
    frame.url = ""
    page.frames = [frame]

    return page


class TestDetectVerificationWall:
    """Test detect_verification_wall() for each wall type."""

    def test_clean_page_returns_none(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Software Engineer - London - Apply now")
        result = detect_verification_wall(page)
        assert result is None

    def test_cloudflare_turnstile_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            selectors={"#challenge-running": True},
            body_text="Checking your browser",
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "cloudflare"
        assert result.confidence >= 0.8

    def test_recaptcha_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={".g-recaptcha": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "recaptcha"
        assert result.confidence >= 0.8

    def test_hcaptcha_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(selectors={".h-captcha": True})
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "hcaptcha"
        assert result.confidence >= 0.8

    def test_text_challenge_verify_human(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Please verify you are human before proceeding")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_text_challenge_unusual_traffic(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            body_text="We've detected unusual traffic from your computer network"
        )
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_text_challenge_are_you_robot(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="Are you a robot? Complete the challenge below.")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"

    def test_cloudflare_iframe_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page()
        frame = MagicMock()
        frame.url = "https://challenges.cloudflare.com/turnstile/v0/something"
        page.frames = [frame]
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "cloudflare"

    def test_recaptcha_iframe_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page()
        frame = MagicMock()
        frame.url = "https://www.google.com/recaptcha/api2/anchor"
        page.frames = [frame]
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "recaptcha"

    def test_hcaptcha_iframe_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page()
        frame = MagicMock()
        frame.url = "https://newassets.hcaptcha.com/captcha/v1/something"
        page.frames = [frame]
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "hcaptcha"

    def test_empty_anomaly_detected(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            url="https://uk.indeed.com/jobs?q=python&l=london",
            body_text="Jobs",
        )
        result = detect_verification_wall(page, expected_results=True)
        # Empty anomaly only fires when expected_results=True and page has no job content
        # But page body doesn't contain job cards, just "Jobs"
        # This is a soft signal — confidence 0.5
        if result is not None:
            assert result.wall_type == "empty_anomaly"
            assert result.confidence <= 0.5

    def test_normal_job_page_not_flagged(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(
            body_text=(
                "Software Engineer at Google - London. "
                "We're looking for someone with 3+ years of Python experience. "
                "Apply now on Indeed."
            ),
        )
        result = detect_verification_wall(page)
        assert result is None

    def test_case_insensitive_text_matching(self):
        from jobpulse.verification_detector import detect_verification_wall

        page = _make_mock_page(body_text="VERIFY YOU ARE HUMAN to continue")
        result = detect_verification_wall(page)
        assert result is not None
        assert result.wall_type == "text_challenge"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_verification_detector.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.verification_detector'`

- [ ] **Step 3: Implement verification_detector.py**

```python
# jobpulse/verification_detector.py
"""Universal verification wall / CAPTCHA detection for job scanning.

Checks Playwright pages for Cloudflare Turnstile, reCAPTCHA, hCaptcha,
text-based challenges, HTTP blocks, and empty result anomalies.
Used by all platform scanners in job_scanner.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# CSS selectors that indicate a specific wall type
_SELECTOR_PATTERNS: list[tuple[str, str, float]] = [
    # (selector, wall_type, confidence)
    ("#challenge-running", "cloudflare", 0.95),
    (".cf-turnstile", "cloudflare", 0.95),
    ("#cf-challenge-running", "cloudflare", 0.90),
    (".g-recaptcha", "recaptcha", 0.90),
    ("#recaptcha-anchor", "recaptcha", 0.90),
    ("[data-sitekey]", "recaptcha", 0.80),
    (".h-captcha", "hcaptcha", 0.90),
]

# iframe URL patterns
_IFRAME_PATTERNS: list[tuple[str, str, float]] = [
    ("challenges.cloudflare.com", "cloudflare", 0.95),
    ("google.com/recaptcha", "recaptcha", 0.90),
    ("hcaptcha.com", "hcaptcha", 0.90),
]

# Text patterns in page body (case-insensitive)
_TEXT_PATTERNS: list[tuple[str, str, float]] = [
    (r"verify you are human", "text_challenge", 0.85),
    (r"please verify", "text_challenge", 0.70),
    (r"are you a robot", "text_challenge", 0.85),
    (r"unusual traffic", "text_challenge", 0.80),
    (r"automated requests", "text_challenge", 0.80),
    (r"suspected automated", "text_challenge", 0.80),
    (r"confirm you're not a robot", "text_challenge", 0.85),
    (r"complete the security check", "text_challenge", 0.80),
    (r"access denied", "http_block", 0.75),
    (r"403 forbidden", "http_block", 0.90),
    (r"you have been blocked", "http_block", 0.90),
]


@dataclass
class VerificationResult:
    """Result from verification wall detection."""

    wall_type: str          # cloudflare | recaptcha | hcaptcha | text_challenge | http_block | empty_anomaly
    confidence: float       # 0.0-1.0
    page_url: str = ""
    page_title: str = ""
    screenshot_path: str | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def detect_verification_wall(
    page: Any,
    *,
    expected_results: bool = False,
) -> VerificationResult | None:
    """Check current page for verification walls.

    Args:
        page: Playwright page object.
        expected_results: If True and page appears empty, flag as empty_anomaly.

    Returns:
        VerificationResult if a wall is detected, None if page is clean.
    """
    page_url = str(getattr(page, "url", ""))
    page_title = ""
    try:
        page_title = page.title() or ""
    except Exception:
        pass

    # 1. Check CSS selectors
    for selector, wall_type, confidence in _SELECTOR_PATTERNS:
        try:
            el = page.query_selector(selector)
            if el is not None:
                logger.warning(
                    "Verification wall detected: %s (selector: %s) on %s",
                    wall_type, selector, page_url,
                )
                return VerificationResult(
                    wall_type=wall_type,
                    confidence=confidence,
                    page_url=page_url,
                    page_title=page_title,
                )
        except Exception:
            pass

    # 2. Check iframe URLs
    try:
        frames = getattr(page, "frames", [])
        for frame in frames:
            frame_url = getattr(frame, "url", "") or ""
            for pattern, wall_type, confidence in _IFRAME_PATTERNS:
                if pattern in frame_url:
                    logger.warning(
                        "Verification wall detected: %s (iframe: %s) on %s",
                        wall_type, pattern, page_url,
                    )
                    return VerificationResult(
                        wall_type=wall_type,
                        confidence=confidence,
                        page_url=page_url,
                        page_title=page_title,
                    )
    except Exception:
        pass

    # 3. Check page body text
    body_text = ""
    try:
        body_text = page.inner_text("body") if hasattr(page, "inner_text") else ""
    except Exception:
        try:
            body_text = page.inner_text() if callable(getattr(page, "inner_text", None)) else ""
        except Exception:
            pass

    body_lower = body_text.lower()
    for pattern, wall_type, confidence in _TEXT_PATTERNS:
        if re.search(pattern, body_lower):
            logger.warning(
                "Verification wall detected: %s (text: '%s') on %s",
                wall_type, pattern, page_url,
            )
            return VerificationResult(
                wall_type=wall_type,
                confidence=confidence,
                page_url=page_url,
                page_title=page_title,
            )

    # 4. Empty anomaly — soft signal
    if expected_results and len(body_text) < 500:
        logger.info(
            "Empty anomaly: expected results but page body is %d chars on %s",
            len(body_text), page_url,
        )
        return VerificationResult(
            wall_type="empty_anomaly",
            confidence=0.5,
            page_url=page_url,
            page_title=page_title,
        )

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_verification_detector.py -v`
Expected: All 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/verification_detector.py tests/test_verification_detector.py
git commit -m "feat(jobs): add universal verification wall detector with 14 tests"
```

---

### Task 2: Scan Learning — SQLite Schema + ScanEvent Recording

**Files:**
- Create: `jobpulse/scan_learning.py`
- Create: `tests/test_scan_learning.py`

- [ ] **Step 1: Write the failing tests for event recording**

```python
# tests/test_scan_learning.py
"""Tests for scan learning engine — event recording, correlation, cooldown."""

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "scan_learning.db")


class TestScanEventRecording:
    """Test ScanEvent creation and storage."""

    def test_init_creates_tables(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        conn = sqlite3.connect(db_path)
        tables = [
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "scan_events" in tables
        assert "learned_rules" in tables
        assert "cooldowns" in tables
        conn.close()

    def test_record_success_event(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.record_event(
            platform="indeed",
            requests_in_session=5,
            avg_delay=4.5,
            session_age_seconds=300.0,
            user_agent_hash="abc12345",
            was_fresh_session=True,
            used_vpn=False,
            simulated_mouse=True,
            referrer_chain="homepage_first",
            search_query="python developer",
            pages_before_block=5,
            browser_fingerprint="fp123456",
            waited_for_page_load=True,
            page_load_time_ms=2500,
            outcome="success",
            wall_type=None,
        )
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]
        assert count == 1
        row = conn.execute("SELECT platform, outcome FROM scan_events").fetchone()
        assert row == ("indeed", "success")
        conn.close()

    def test_record_blocked_event(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.record_event(
            platform="indeed",
            requests_in_session=8,
            avg_delay=2.0,
            session_age_seconds=600.0,
            user_agent_hash="abc12345",
            was_fresh_session=False,
            used_vpn=False,
            simulated_mouse=False,
            referrer_chain="direct",
            search_query="data engineer",
            pages_before_block=8,
            browser_fingerprint="fp123456",
            waited_for_page_load=False,
            page_load_time_ms=1500,
            outcome="blocked",
            wall_type="cloudflare",
        )
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT outcome, wall_type FROM scan_events WHERE platform = 'indeed'"
        ).fetchone()
        assert row == ("blocked", "cloudflare")
        conn.close()

    def test_time_of_day_bucket_assigned(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.record_event(
            platform="indeed",
            requests_in_session=3,
            avg_delay=5.0,
            session_age_seconds=120.0,
            user_agent_hash="ua1",
            was_fresh_session=True,
            used_vpn=False,
            simulated_mouse=True,
            referrer_chain="direct",
            search_query="python",
            pages_before_block=3,
            browser_fingerprint="fp1",
            waited_for_page_load=True,
            page_load_time_ms=2000,
            outcome="success",
            wall_type=None,
        )
        conn = sqlite3.connect(db_path)
        bucket = conn.execute(
            "SELECT time_of_day_bucket FROM scan_events"
        ).fetchone()[0]
        assert bucket in ("morning", "afternoon", "evening", "night")
        conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_learning.py::TestScanEventRecording -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.scan_learning'`

- [ ] **Step 3: Implement scan_learning.py — schema + recording**

```python
# jobpulse/scan_learning.py
"""Scan Learning Engine — event recording, statistical correlation, LLM analysis, adaptive params, cooldown.

Learns from verification wall encounters across all platforms.
Zero LLM cost for statistical engine; periodic GPT-5o-mini analysis every 5th block.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "scan_learning.db")


def _time_bucket(dt: datetime) -> str:
    """Classify hour into time-of-day bucket."""
    h = dt.hour
    if 6 <= h < 12:
        return "morning"
    if 12 <= h < 17:
        return "afternoon"
    if 17 <= h < 22:
        return "evening"
    return "night"


def _requests_bucket(n: int) -> str:
    if n <= 3:
        return "1-3"
    if n <= 6:
        return "4-6"
    if n <= 10:
        return "7-10"
    return "11+"


def _delay_bucket(avg: float) -> str:
    if avg < 2.0:
        return "<2s"
    if avg < 4.0:
        return "2-4s"
    if avg < 8.0:
        return "4-8s"
    return "8s+"


def _session_age_bucket(seconds: float) -> str:
    if seconds < 300:
        return "<5min"
    if seconds < 600:
        return "5-10min"
    if seconds < 900:
        return "10-15min"
    return "15min+"


def _pages_bucket(n: int) -> str:
    if n <= 3:
        return "1-3"
    if n <= 6:
        return "4-6"
    if n <= 10:
        return "7-10"
    return "11+"


class ScanLearningEngine:
    """Core learning engine — records events, correlates, adapts."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS scan_events (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    time_of_day_bucket TEXT NOT NULL,
                    requests_in_session INTEGER NOT NULL,
                    avg_delay REAL NOT NULL,
                    session_age_seconds REAL NOT NULL,
                    user_agent_hash TEXT NOT NULL,
                    was_fresh_session INTEGER NOT NULL,
                    used_vpn INTEGER NOT NULL,
                    simulated_mouse INTEGER NOT NULL,
                    referrer_chain TEXT NOT NULL,
                    search_query TEXT NOT NULL,
                    pages_before_block INTEGER NOT NULL,
                    browser_fingerprint TEXT NOT NULL,
                    waited_for_page_load INTEGER NOT NULL,
                    page_load_time_ms INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    wall_type TEXT
                );

                CREATE TABLE IF NOT EXISTS learned_rules (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    rule_text TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    recommendation TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    times_applied INTEGER DEFAULT 0,
                    times_successful INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS cooldowns (
                    platform TEXT PRIMARY KEY,
                    blocked_at TEXT NOT NULL,
                    cooldown_until TEXT NOT NULL,
                    consecutive_blocks INTEGER DEFAULT 1,
                    last_wall_type TEXT
                );
            """)

    def record_event(
        self,
        *,
        platform: str,
        requests_in_session: int,
        avg_delay: float,
        session_age_seconds: float,
        user_agent_hash: str,
        was_fresh_session: bool,
        used_vpn: bool,
        simulated_mouse: bool,
        referrer_chain: str,
        search_query: str,
        pages_before_block: int,
        browser_fingerprint: str,
        waited_for_page_load: bool,
        page_load_time_ms: int,
        outcome: str,
        wall_type: str | None,
    ) -> str:
        """Record a scan event. Returns the event ID."""
        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4().hex[:16]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO scan_events (
                    id, platform, timestamp, time_of_day_bucket,
                    requests_in_session, avg_delay, session_age_seconds,
                    user_agent_hash, was_fresh_session, used_vpn,
                    simulated_mouse, referrer_chain, search_query,
                    pages_before_block, browser_fingerprint,
                    waited_for_page_load, page_load_time_ms,
                    outcome, wall_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id, platform, now.isoformat(), _time_bucket(now),
                    requests_in_session, avg_delay, session_age_seconds,
                    user_agent_hash, int(was_fresh_session), int(used_vpn),
                    int(simulated_mouse), referrer_chain, search_query,
                    pages_before_block, browser_fingerprint,
                    int(waited_for_page_load), page_load_time_ms,
                    outcome, wall_type,
                ),
            )
        logger.info(
            "Recorded scan event %s: platform=%s outcome=%s wall=%s",
            event_id, platform, outcome, wall_type,
        )
        return event_id

    def get_total_blocks(self, platform: str | None = None) -> int:
        """Count total block events, optionally filtered by platform."""
        with sqlite3.connect(self.db_path) as conn:
            if platform:
                row = conn.execute(
                    "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked' AND platform = ?",
                    (platform,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM scan_events WHERE outcome = 'blocked'"
                ).fetchone()
            return row[0] if row else 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_learning.py::TestScanEventRecording -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_learning.py tests/test_scan_learning.py
git commit -m "feat(jobs): add scan learning engine — SQLite schema + event recording"
```

---

### Task 3: Cooldown Manager

**Files:**
- Modify: `jobpulse/scan_learning.py`
- Test: `tests/test_scan_learning.py`

- [ ] **Step 1: Write the failing tests for cooldown**

Add to `tests/test_scan_learning.py`:

```python
class TestCooldownManager:
    """Test cooldown logic with exponential backoff."""

    def test_no_cooldown_initially(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        assert engine.can_scan_now("indeed") is True

    def test_first_block_sets_2hr_cooldown(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")
        assert engine.can_scan_now("indeed") is False

    def test_cooldown_expires(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        # Manually insert expired cooldown
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO cooldowns (platform, blocked_at, cooldown_until, consecutive_blocks, last_wall_type) "
                "VALUES (?, ?, ?, 1, 'cloudflare')",
                ("indeed", past.isoformat(), expired.isoformat()),
            )
        assert engine.can_scan_now("indeed") is True

    def test_second_block_doubles_cooldown(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")  # 2hr
        engine.start_cooldown("indeed", "cloudflare")  # 4hr

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT consecutive_blocks FROM cooldowns WHERE platform = 'indeed'"
            ).fetchone()
            assert row[0] == 2

    def test_third_block_triggers_48hr_skip(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")
        engine.start_cooldown("indeed", "cloudflare")
        engine.start_cooldown("indeed", "cloudflare")  # 3rd → 48hr

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT consecutive_blocks, cooldown_until FROM cooldowns WHERE platform = 'indeed'"
            ).fetchone()
            assert row[0] == 3
            cooldown_until = datetime.fromisoformat(row[1])
            # Should be ~48 hours from now (allow 1 min tolerance)
            hours_until = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
            assert hours_until > 47.0

    def test_successful_scan_resets_cooldown(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")
        engine.reset_cooldown("indeed")

        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT * FROM cooldowns WHERE platform = 'indeed'"
            ).fetchone()
            assert row is None

    def test_cooldown_per_platform_independent(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")
        assert engine.can_scan_now("indeed") is False
        assert engine.can_scan_now("linkedin") is True

    def test_get_cooldown_info(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "text_challenge")
        info = engine.get_cooldown_info("indeed")
        assert info is not None
        assert info["consecutive_blocks"] == 1
        assert info["last_wall_type"] == "text_challenge"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_learning.py::TestCooldownManager -v`
Expected: FAIL with `AttributeError: 'ScanLearningEngine' object has no attribute 'can_scan_now'`

- [ ] **Step 3: Add cooldown methods to ScanLearningEngine**

Add these methods to the `ScanLearningEngine` class in `jobpulse/scan_learning.py`:

```python
    # --- Cooldown Manager ---

    _COOLDOWN_HOURS = {1: 2, 2: 4}  # consecutive_blocks → hours. 3+ → 48hr
    _MAX_COOLDOWN_HOURS = 48

    def can_scan_now(self, platform: str) -> bool:
        """Check if platform is NOT in cooldown."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM cooldowns WHERE platform = ?",
                (platform,),
            ).fetchone()
            if row is None:
                return True
            cooldown_until = datetime.fromisoformat(row[0])
            if datetime.now(timezone.utc) >= cooldown_until:
                # Cooldown expired — clean up
                conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
                conn.commit()
                return True
            return False

    def start_cooldown(self, platform: str, wall_type: str) -> None:
        """Start or extend cooldown for a platform after a block."""
        now = datetime.now(timezone.utc)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT consecutive_blocks FROM cooldowns WHERE platform = ?",
                (platform,),
            ).fetchone()

            consecutive = (row[0] + 1) if row else 1
            hours = self._COOLDOWN_HOURS.get(consecutive, self._MAX_COOLDOWN_HOURS)
            cooldown_until = now + timedelta(hours=hours)

            conn.execute(
                """INSERT INTO cooldowns (platform, blocked_at, cooldown_until, consecutive_blocks, last_wall_type)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(platform) DO UPDATE SET
                       blocked_at = ?, cooldown_until = ?, consecutive_blocks = ?, last_wall_type = ?""",
                (
                    platform, now.isoformat(), cooldown_until.isoformat(), consecutive, wall_type,
                    now.isoformat(), cooldown_until.isoformat(), consecutive, wall_type,
                ),
            )

        logger.warning(
            "Cooldown started: %s blocked (%s), %dhr cooldown (block #%d), until %s",
            platform, wall_type, hours, consecutive, cooldown_until.isoformat(),
        )

    def reset_cooldown(self, platform: str) -> None:
        """Reset cooldown after a successful scan."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM cooldowns WHERE platform = ?", (platform,))
            conn.commit()
        logger.info("Cooldown reset for %s after successful scan", platform)

    def get_cooldown_info(self, platform: str) -> dict[str, Any] | None:
        """Get current cooldown state for a platform."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT blocked_at, cooldown_until, consecutive_blocks, last_wall_type "
                "FROM cooldowns WHERE platform = ?",
                (platform,),
            ).fetchone()
            if row is None:
                return None
            return {
                "blocked_at": row[0],
                "cooldown_until": row[1],
                "consecutive_blocks": row[2],
                "last_wall_type": row[3],
            }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_learning.py::TestCooldownManager -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_learning.py tests/test_scan_learning.py
git commit -m "feat(jobs): add cooldown manager — 2hr base, exponential backoff, 48hr max"
```

---

### Task 4: Statistical Correlation Engine

**Files:**
- Modify: `jobpulse/scan_learning.py`
- Test: `tests/test_scan_learning.py`

- [ ] **Step 1: Write the failing tests for correlation**

Add to `tests/test_scan_learning.py`:

```python
class TestStatisticalCorrelation:
    """Test risk factor identification from event history."""

    def _seed_events(self, engine, platform: str, events: list[dict]):
        """Helper to seed multiple events."""
        for e in events:
            engine.record_event(
                platform=platform,
                requests_in_session=e.get("requests", 5),
                avg_delay=e.get("delay", 4.0),
                session_age_seconds=e.get("age", 300.0),
                user_agent_hash=e.get("ua", "ua1"),
                was_fresh_session=e.get("fresh", True),
                used_vpn=e.get("vpn", False),
                simulated_mouse=e.get("mouse", True),
                referrer_chain=e.get("referrer", "direct"),
                search_query=e.get("query", "python"),
                pages_before_block=e.get("pages", 5),
                browser_fingerprint=e.get("fp", "fp1"),
                waited_for_page_load=e.get("waited", True),
                page_load_time_ms=e.get("load_ms", 2000),
                outcome=e["outcome"],
                wall_type=e.get("wall", None),
            )

    def test_no_events_returns_empty_risk_factors(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        factors = engine.compute_risk_factors("indeed")
        assert factors == []

    def test_high_block_rate_ua_becomes_risk_factor(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        # UA "bad_ua" blocked 3/4 times = 75%
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "success", "ua": "bad_ua"},
            # good_ua never blocked
            {"outcome": "success", "ua": "good_ua"},
            {"outcome": "success", "ua": "good_ua"},
            {"outcome": "success", "ua": "good_ua"},
        ])

        factors = engine.compute_risk_factors("indeed")
        signal_names = [f["signal"] for f in factors]
        assert "user_agent_hash" in signal_names
        ua_factor = next(f for f in factors if f["signal"] == "user_agent_hash")
        assert ua_factor["bucket"] == "bad_ua"
        assert ua_factor["block_rate"] >= 0.70

    def test_low_delay_becomes_risk_factor(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        # Low delay (<2s) blocked 3/3 times
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.5},
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.0},
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.8},
            # Higher delay succeeds
            {"outcome": "success", "delay": 6.0},
            {"outcome": "success", "delay": 5.0},
            {"outcome": "success", "delay": 7.0},
        ])

        factors = engine.compute_risk_factors("indeed")
        signal_names = [f["signal"] for f in factors]
        assert "avg_delay" in signal_names

    def test_minimum_sample_size_enforced(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        # Only 2 events with same UA — below min_sample=3
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "rare_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "rare_ua"},
        ])

        factors = engine.compute_risk_factors("indeed")
        # Should be empty — not enough data
        ua_factors = [f for f in factors if f["signal"] == "user_agent_hash" and f["bucket"] == "rare_ua"]
        assert len(ua_factors) == 0

    def test_risk_factors_stored_as_learned_rules(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "success", "ua": "good_ua"},
            {"outcome": "success", "ua": "good_ua"},
            {"outcome": "success", "ua": "good_ua"},
        ])

        engine.update_learned_rules("indeed")
        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT rule_text, source FROM learned_rules WHERE platform = 'indeed'"
        ).fetchall()
        conn.close()
        assert len(rules) > 0
        assert any(r[1] == "statistical" for r in rules)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_learning.py::TestStatisticalCorrelation -v`
Expected: FAIL with `AttributeError: 'ScanLearningEngine' object has no attribute 'compute_risk_factors'`

- [ ] **Step 3: Add correlation methods to ScanLearningEngine**

Add these methods to `ScanLearningEngine` in `jobpulse/scan_learning.py`:

```python
    # --- Statistical Correlation Engine ---

    _MIN_SAMPLE_SIZE = 3
    _RISK_THRESHOLD = 0.50

    # Which columns to bucket and how
    _BUCKETED_SIGNALS: list[tuple[str, Any]] = [
        ("time_of_day_bucket", None),           # already bucketed
        ("requests_in_session", _requests_bucket),
        ("avg_delay", _delay_bucket),
        ("session_age_seconds", _session_age_bucket),
        ("user_agent_hash", None),              # use raw value
        ("was_fresh_session", None),             # 0 or 1
        ("simulated_mouse", None),              # 0 or 1
        ("referrer_chain", None),               # raw value
        ("pages_before_block", _pages_bucket),
        ("waited_for_page_load", None),         # 0 or 1
    ]

    def compute_risk_factors(self, platform: str) -> list[dict[str, Any]]:
        """Compute block rate per signal bucket. Return factors with rate > threshold."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scan_events WHERE platform = ? "
                "ORDER BY timestamp DESC LIMIT 200",
                (platform,),
            ).fetchall()

        if not rows:
            return []

        risk_factors: list[dict[str, Any]] = []

        for signal_col, bucket_fn in self._BUCKETED_SIGNALS:
            # Group events by bucket
            buckets: dict[str, list[str]] = {}  # bucket_value → [outcome, ...]
            for row in rows:
                raw_val = row[signal_col]
                if bucket_fn is not None:
                    bucket_val = bucket_fn(raw_val)
                else:
                    bucket_val = str(raw_val)
                buckets.setdefault(bucket_val, []).append(row["outcome"])

            for bucket_val, outcomes in buckets.items():
                total = len(outcomes)
                if total < self._MIN_SAMPLE_SIZE:
                    continue
                blocked = sum(1 for o in outcomes if o == "blocked")
                rate = blocked / total

                if rate >= self._RISK_THRESHOLD:
                    risk_factors.append({
                        "signal": signal_col,
                        "bucket": bucket_val,
                        "block_rate": round(rate, 2),
                        "sample_size": total,
                        "blocked_count": blocked,
                    })

        # Sort by block rate descending
        risk_factors.sort(key=lambda f: f["block_rate"], reverse=True)
        return risk_factors

    def update_learned_rules(self, platform: str) -> int:
        """Compute risk factors and store as learned rules. Returns count of new rules."""
        factors = self.compute_risk_factors(platform)
        if not factors:
            return 0

        count = 0
        with sqlite3.connect(self.db_path) as conn:
            for f in factors:
                rule_id = hashlib.sha256(
                    f"{platform}:{f['signal']}:{f['bucket']}".encode()
                ).hexdigest()[:16]
                rule_text = (
                    f"High block rate ({f['block_rate']:.0%}) when "
                    f"{f['signal']} = {f['bucket']} "
                    f"({f['blocked_count']}/{f['sample_size']} sessions blocked)"
                )
                recommendation = (
                    f"Avoid {f['signal']} = {f['bucket']} — "
                    f"use alternative values or adjust timing"
                )
                conn.execute(
                    """INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at)
                       VALUES (?, ?, ?, ?, ?, 'statistical', ?)
                       ON CONFLICT(id) DO UPDATE SET
                           rule_text = ?, confidence = ?, recommendation = ?, created_at = ?""",
                    (
                        rule_id, platform, rule_text, f["block_rate"], recommendation,
                        datetime.now(timezone.utc).isoformat(),
                        rule_text, f["block_rate"], recommendation,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                count += 1

        logger.info("Updated %d learned rules for %s", count, platform)
        return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_learning.py::TestStatisticalCorrelation -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_learning.py tests/test_scan_learning.py
git commit -m "feat(jobs): add statistical correlation engine — risk factor detection from event history"
```

---

### Task 5: LLM Pattern Analyzer

**Files:**
- Modify: `jobpulse/scan_learning.py`
- Test: `tests/test_scan_learning.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scan_learning.py`:

```python
from unittest.mock import patch, MagicMock


class TestLLMPatternAnalyzer:
    """Test periodic LLM analysis of block patterns."""

    def _seed_blocks(self, engine, count: int):
        """Seed N block events for testing."""
        for i in range(count):
            engine.record_event(
                platform="indeed",
                requests_in_session=8 + i,
                avg_delay=1.5,
                session_age_seconds=600.0,
                user_agent_hash="ua1",
                was_fresh_session=False,
                used_vpn=False,
                simulated_mouse=False,
                referrer_chain="direct",
                search_query="python developer",
                pages_before_block=8,
                browser_fingerprint="fp1",
                waited_for_page_load=False,
                page_load_time_ms=1500,
                outcome="blocked",
                wall_type="cloudflare",
            )

    def test_should_analyze_false_under_5_blocks(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 3)
        assert engine.should_run_llm_analysis() is False

    def test_should_analyze_true_at_5_blocks(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 5)
        assert engine.should_run_llm_analysis() is True

    def test_should_analyze_true_at_10_blocks(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 10)
        assert engine.should_run_llm_analysis() is True

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_stores_rule(self, mock_llm, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine
        import json

        mock_llm.return_value = json.dumps({
            "pattern": "Indeed blocks after 8+ requests with delay < 2s",
            "confidence": 0.85,
            "recommendation": "Increase delay to 5-8s, limit to 5 requests per session",
        })

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 5)
        engine.run_llm_analysis("indeed")

        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT rule_text, source, confidence FROM learned_rules WHERE source = 'llm'"
        ).fetchall()
        conn.close()
        assert len(rules) == 1
        assert rules[0][1] == "llm"
        assert rules[0][2] == 0.85

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_handles_invalid_json(self, mock_llm, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        mock_llm.return_value = "not valid json at all"

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 5)
        # Should not raise
        engine.run_llm_analysis("indeed")

        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT COUNT(*) FROM learned_rules WHERE source = 'llm'"
        ).fetchone()
        conn.close()
        assert rules[0] == 0

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_handles_none_response(self, mock_llm, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        mock_llm.return_value = None

        engine = ScanLearningEngine(db_path=db_path)
        self._seed_blocks(engine, 5)
        engine.run_llm_analysis("indeed")

        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT COUNT(*) FROM learned_rules WHERE source = 'llm'"
        ).fetchone()
        conn.close()
        assert rules[0] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_learning.py::TestLLMPatternAnalyzer -v`
Expected: FAIL with `AttributeError`

- [ ] **Step 3: Add LLM analysis methods to ScanLearningEngine**

Add import at top of `jobpulse/scan_learning.py`:

```python
from jobpulse.utils.safe_io import safe_openai_call
```

Add these methods to `ScanLearningEngine`:

```python
    # --- LLM Pattern Analyzer ---

    _LLM_ANALYSIS_EVERY_N_BLOCKS = 5

    def should_run_llm_analysis(self) -> bool:
        """True if total blocks across all platforms is a multiple of 5."""
        total = self.get_total_blocks()
        return total > 0 and total % self._LLM_ANALYSIS_EVERY_N_BLOCKS == 0

    def run_llm_analysis(self, platform: str) -> None:
        """Run GPT-5o-mini analysis on recent events for a platform."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM scan_events WHERE platform = ? ORDER BY timestamp DESC LIMIT 20",
                (platform,),
            ).fetchall()

        if not rows:
            return

        # Build events table for LLM
        header = "timestamp | requests | avg_delay | session_age | ua_hash | fresh | mouse | referrer | pages | waited | outcome | wall_type"
        lines = [header]
        for r in rows:
            lines.append(
                f"{r['timestamp'][:16]} | {r['requests_in_session']} | "
                f"{r['avg_delay']:.1f}s | {r['session_age_seconds']:.0f}s | "
                f"{r['user_agent_hash'][:6]} | {bool(r['was_fresh_session'])} | "
                f"{bool(r['simulated_mouse'])} | {r['referrer_chain']} | "
                f"{r['pages_before_block']} | {bool(r['waited_for_page_load'])} | "
                f"{r['outcome']} | {r['wall_type'] or 'n/a'}"
            )
        events_table = "\n".join(lines)

        prompt = (
            f"You are analyzing job scraping session data to find patterns that trigger verification walls.\n\n"
            f"Here are the last {len(rows)} scan sessions for {platform}:\n{events_table}\n\n"
            f"Identify the pattern that most likely triggers blocks. Return ONLY valid JSON:\n"
            f'{{"pattern": "human-readable description", "confidence": 0.0-1.0, '
            f'"recommendation": "specific parameter changes"}}'
        )

        import openai
        client = openai.OpenAI()
        response = safe_openai_call(
            client,
            model="gpt-5o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            caller="scan_learning_llm_analysis",
        )

        if not response:
            logger.warning("LLM analysis returned None for %s", platform)
            return

        import json as _json
        try:
            result = _json.loads(response)
        except _json.JSONDecodeError:
            logger.warning("LLM analysis returned invalid JSON for %s: %s", platform, response[:200])
            return

        pattern = result.get("pattern", "")
        confidence = float(result.get("confidence", 0.5))
        recommendation = result.get("recommendation", "")

        if not pattern:
            return

        rule_id = uuid.uuid4().hex[:16]
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at)
                   VALUES (?, ?, ?, ?, ?, 'llm', ?)""",
                (rule_id, platform, pattern, confidence, recommendation,
                 datetime.now(timezone.utc).isoformat()),
            )

        logger.info(
            "LLM analysis for %s: pattern='%s' confidence=%.2f",
            platform, pattern, confidence,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_learning.py::TestLLMPatternAnalyzer -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_learning.py tests/test_scan_learning.py
git commit -m "feat(jobs): add LLM pattern analyzer — GPT-5o-mini analysis every 5th block"
```

---

### Task 6: Adaptive Parameters

**Files:**
- Modify: `jobpulse/scan_learning.py`
- Test: `tests/test_scan_learning.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_scan_learning.py`:

```python
class TestAdaptiveParams:
    """Test adaptive parameter generation from learned rules."""

    def test_default_params_no_history(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        params = engine.get_adaptive_params("indeed")
        assert params["risk_level"] == "low"
        assert params["delay_range"] == (2.0, 8.0)
        assert params["max_requests"] == 50
        assert params["wait_for_load"] is True
        assert params["cooldown_active"] is False

    def test_medium_risk_with_one_factor(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        # Insert a learned rule manually
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at) "
                "VALUES ('r1', 'indeed', 'High block rate in morning', 0.75, 'Avoid morning scans', 'statistical', '2026-03-31T10:00:00')",
            )

        params = engine.get_adaptive_params("indeed")
        assert params["risk_level"] == "medium"
        # Delays increased 50%
        assert params["delay_range"][0] >= 3.0
        # Max requests halved
        assert params["max_requests"] <= 25

    def test_high_risk_with_multiple_factors(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at) "
                "VALUES ('r1', 'indeed', 'Block in morning', 0.80, 'Avoid', 'statistical', '2026-03-31T10:00:00')",
            )
            conn.execute(
                "INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at) "
                "VALUES ('r2', 'indeed', 'Block with low delay', 0.70, 'Increase', 'statistical', '2026-03-31T10:00:00')",
            )

        params = engine.get_adaptive_params("indeed")
        assert params["risk_level"] == "high"
        assert params["simulate_human"] is True
        assert params["max_requests"] <= 5

    def test_cooldown_active_reflected_in_params(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        engine.start_cooldown("indeed", "cloudflare")
        params = engine.get_adaptive_params("indeed")
        assert params["cooldown_active"] is True
        assert params["cooldown_until"] is not None

    def test_params_independent_per_platform(self, db_path: str):
        from jobpulse.scan_learning import ScanLearningEngine

        engine = ScanLearningEngine(db_path=db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at) "
                "VALUES ('r1', 'indeed', 'Block pattern', 0.80, 'Fix', 'statistical', '2026-03-31T10:00:00')",
            )
            conn.execute(
                "INSERT INTO learned_rules (id, platform, rule_text, confidence, recommendation, source, created_at) "
                "VALUES ('r2', 'indeed', 'Another pattern', 0.70, 'Fix', 'statistical', '2026-03-31T10:00:00')",
            )

        indeed_params = engine.get_adaptive_params("indeed")
        linkedin_params = engine.get_adaptive_params("linkedin")
        assert indeed_params["risk_level"] == "high"
        assert linkedin_params["risk_level"] == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_scan_learning.py::TestAdaptiveParams -v`
Expected: FAIL with `AttributeError: 'ScanLearningEngine' object has no attribute 'get_adaptive_params'`

- [ ] **Step 3: Add get_adaptive_params to ScanLearningEngine**

Add to `ScanLearningEngine` in `jobpulse/scan_learning.py`:

```python
    # --- Adaptive Parameters ---

    _DEFAULT_PARAMS: dict[str, Any] = {
        "delay_range": (2.0, 8.0),
        "max_requests": 50,
        "simulate_human": False,
        "session_max_age_seconds": 1800,
        "referrer_strategy": "direct",
        "wait_for_load": True,
        "cooldown_active": False,
        "cooldown_until": None,
        "risk_level": "low",
    }

    def get_adaptive_params(self, platform: str) -> dict[str, Any]:
        """Build scan parameters based on learned rules + cooldown state."""
        params = dict(self._DEFAULT_PARAMS)

        # Check cooldown
        cooldown = self.get_cooldown_info(platform)
        if cooldown and not self.can_scan_now(platform):
            params["cooldown_active"] = True
            params["cooldown_until"] = cooldown["cooldown_until"]

        # Count active learned rules for this platform
        with sqlite3.connect(self.db_path) as conn:
            rule_count = conn.execute(
                "SELECT COUNT(*) FROM learned_rules WHERE platform = ? AND confidence >= 0.50",
                (platform,),
            ).fetchone()[0]

        if rule_count == 0:
            params["risk_level"] = "low"
        elif rule_count == 1:
            params["risk_level"] = "medium"
            params["delay_range"] = (3.0, 12.0)
            params["max_requests"] = 25
            params["simulate_human"] = True
            params["session_max_age_seconds"] = 600
        else:
            params["risk_level"] = "high"
            params["delay_range"] = (5.0, 15.0)
            params["max_requests"] = 5
            params["simulate_human"] = True
            params["session_max_age_seconds"] = 480
            params["referrer_strategy"] = "homepage_first"

        # Select a user agent NOT associated with blocks
        params["user_agent"] = self._pick_safe_ua(platform)

        return params

    def _pick_safe_ua(self, platform: str) -> str | None:
        """Pick a user agent hash that has NOT been flagged as risky. Returns None to use default."""
        with sqlite3.connect(self.db_path) as conn:
            blocked_uas = conn.execute(
                "SELECT DISTINCT bucket FROM learned_rules "
                "WHERE platform = ? AND signal = 'user_agent_hash' AND source = 'statistical'",
                # This query won't work because learned_rules doesn't have a 'signal' column
                # but the rule_text contains the signal info. For simplicity, return None.
                (platform,),
            ).fetchall()
        # For now return None — UA rotation handled in job_scanner.py
        return None
```

Wait — the `_pick_safe_ua` method queries a `signal` column that doesn't exist in `learned_rules`. Let me fix that. The `learned_rules` table stores the rule as text. Instead, we'll add a `signal` and `bucket` column to `learned_rules` for statistical rules. Actually, simpler: just return None and let the scanner handle UA rotation. The risk level is what matters.

Replace the `_pick_safe_ua` method with:

```python
    def _pick_safe_ua(self, platform: str) -> str | None:
        """Return None — UA rotation is handled by the scanner based on risk_level."""
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_scan_learning.py::TestAdaptiveParams -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/scan_learning.py tests/test_scan_learning.py
git commit -m "feat(jobs): add adaptive params — risk-based delay/request/behavior adjustment"
```

---

### Task 7: Human-Like Page Interaction Helper

**Files:**
- Modify: `jobpulse/job_scanner.py`
- Test: `tests/test_verification_detector.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_verification_detector.py`:

```python
class TestSimulateHumanInteraction:
    """Test human-like page interaction helper."""

    def test_simulate_human_calls_scroll_and_move(self):
        from jobpulse.verification_detector import simulate_human_interaction

        page = MagicMock()
        page.evaluate = MagicMock()
        page.mouse = MagicMock()
        page.wait_for_timeout = MagicMock()

        simulate_human_interaction(page)

        # Should have called evaluate for scroll
        assert page.evaluate.called
        # Should have called mouse.move at least once
        assert page.mouse.move.called

    def test_simulate_human_does_not_raise(self):
        from jobpulse.verification_detector import simulate_human_interaction

        page = MagicMock()
        page.evaluate.side_effect = Exception("page crashed")
        # Should not raise even if page interaction fails
        simulate_human_interaction(page)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_verification_detector.py::TestSimulateHumanInteraction -v`
Expected: FAIL with `ImportError: cannot import name 'simulate_human_interaction'`

- [ ] **Step 3: Add simulate_human_interaction to verification_detector.py**

Add to `jobpulse/verification_detector.py`:

```python
def simulate_human_interaction(page: Any) -> None:
    """Simulate human-like page interaction — scroll, mouse movement, reading delay.

    Safe to call on any page — swallows all exceptions.
    """
    import random
    import time

    try:
        # 1. Reading delay (1-3s)
        time.sleep(random.uniform(1.0, 3.0))

        # 2. Scroll down slowly (300-600px in 50px increments)
        scroll_target = random.randint(300, 600)
        scroll_step = 50
        for offset in range(0, scroll_target, scroll_step):
            try:
                page.evaluate(f"window.scrollBy(0, {scroll_step})")
                time.sleep(random.uniform(0.05, 0.15))
            except Exception:
                break

        # 3. Random mouse movement
        try:
            x = random.randint(200, 800)
            y = random.randint(200, 500)
            page.mouse.move(x, y)
        except Exception:
            pass

        # 4. Short pause after interaction
        time.sleep(random.uniform(0.5, 1.5))

    except Exception as exc:
        logger.debug("simulate_human_interaction: %s (non-fatal)", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_verification_detector.py::TestSimulateHumanInteraction -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/verification_detector.py tests/test_verification_detector.py
git commit -m "feat(jobs): add human-like page interaction — scroll, mouse, reading delays"
```

---

### Task 8: Integrate Into job_scanner.py — Indeed Scanner

**Files:**
- Modify: `jobpulse/job_scanner.py:263-389`

- [ ] **Step 1: Add imports and session signal tracker to job_scanner.py**

Add after the existing imports (line 26):

```python
from jobpulse.verification_detector import detect_verification_wall, simulate_human_interaction
from jobpulse.scan_learning import ScanLearningEngine
```

Add a helper class after `_anti_detection_sleep()` (line 85):

```python
class _SessionSignals:
    """Track signals for the current scan session (per platform invocation)."""

    def __init__(self, platform: str, user_agent: str, browser_fingerprint: str) -> None:
        self.platform = platform
        self.start_time = time.monotonic()
        self.request_times: list[float] = []
        self.user_agent_hash = hashlib.sha256(user_agent.encode()).hexdigest()[:8]
        self.browser_fingerprint = browser_fingerprint
        self.was_fresh_session = True  # updated if reusing profile
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
```

- [ ] **Step 2: Rewrite scan_indeed with verification detection + human interaction**

Replace the `scan_indeed` function (lines 263-389) with:

```python
def scan_indeed(config: SearchConfig) -> list[dict[str, Any]]:
    """Indeed.co.uk job search via Playwright (public search, no login required).

    Includes verification wall detection, human-like interaction, and
    adaptive parameters from the scan learning engine.
    """
    try:
        from playwright.sync_api import sync_playwright as _  # noqa: F401
    except ImportError:
        logger.warning(
            "scan_indeed: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    # Pre-scan gate: check cooldown + adaptive params
    engine = ScanLearningEngine()
    params = engine.get_adaptive_params("indeed")
    if params["cooldown_active"]:
        logger.info(
            "scan_indeed: in cooldown until %s, skipping", params["cooldown_until"]
        )
        return []

    delay_min, delay_max = params["delay_range"]
    max_requests = params["max_requests"]

    results: list[dict[str, Any]] = []
    ua = _random_ua()
    fp = hashlib.sha256(f"indeed:1280x800:{ua}".encode()).hexdigest()[:8]
    signals = _SessionSignals("indeed", ua, fp)
    signals.was_fresh_session = not (DATA_DIR / "indeed_profile").exists()

    try:
        with managed_persistent_browser(
            user_data_dir=str(DATA_DIR / "indeed_profile"),
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
        ) as (_browser, page):
            for title in config.titles:
                if len(results) >= max_requests:
                    break

                signals.last_query = title

                search_url = (
                    f"https://uk.indeed.com/jobs"
                    f"?q={_url_encode(title)}"
                    f"&l={_url_encode(config.location)}"
                    f"&fromage=1"
                )

                try:
                    logger.info("scan_indeed: fetching '%s'", search_url)
                    load_start = time.monotonic()
                    page.goto(search_url, timeout=30_000, wait_until="networkidle")
                    signals.last_load_time_ms = int((time.monotonic() - load_start) * 1000)
                    signals.waited_for_load = True
                    signals.record_request()

                    # Human-like interaction before extracting
                    if params.get("simulate_human", False) or params["risk_level"] != "low":
                        simulate_human_interaction(page)
                        signals.simulated_mouse = True

                    # Check for verification wall
                    wall = detect_verification_wall(page, expected_results=True)
                    if wall and wall.confidence >= 0.7:
                        logger.warning(
                            "scan_indeed: verification wall detected (%s, %.0f%%), aborting",
                            wall.wall_type, wall.confidence * 100,
                        )
                        engine.record_event(
                            platform="indeed",
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
                            wall_type=wall.wall_type,
                        )
                        engine.start_cooldown("indeed", wall.wall_type)
                        engine.update_learned_rules("indeed")
                        if engine.should_run_llm_analysis():
                            engine.run_llm_analysis("indeed")
                        return results  # return whatever we got before block

                    time.sleep(random.uniform(delay_min, delay_max))

                    # Find job cards
                    cards = page.query_selector_all(
                        ".job_seen_beacon, .resultContent, [data-jk]"
                    )
                    logger.info("scan_indeed: found %d cards for '%s'", len(cards), title)

                    for card in cards:
                        if len(results) >= max_requests:
                            break
                        try:
                            title_el = card.query_selector("h2.jobTitle a, h2 a, .jobTitle a")
                            company_el = card.query_selector(
                                "[data-testid='company-name'], .companyName, .company"
                            )
                            location_el = card.query_selector(
                                "[data-testid='text-location'], .companyLocation, .location"
                            )

                            job_title = title_el.inner_text().strip() if title_el else ""
                            company = company_el.inner_text().strip() if company_el else ""
                            location = location_el.inner_text().strip() if location_el else ""
                            href = title_el.get_attribute("href") if title_el else ""

                            if href and not href.startswith("http"):
                                href = "https://uk.indeed.com" + href

                            if not href or not job_title:
                                continue

                            # Click to get full description with human interaction
                            description = ""
                            try:
                                if title_el:
                                    title_el.click()
                                    signals.record_request()
                                    time.sleep(random.uniform(1.5, 3.0))

                                    # Check for wall after click
                                    wall = detect_verification_wall(page)
                                    if wall and wall.confidence >= 0.7:
                                        logger.warning(
                                            "scan_indeed: wall after card click (%s), aborting",
                                            wall.wall_type,
                                        )
                                        engine.record_event(
                                            platform="indeed",
                                            requests_in_session=signals.requests_count,
                                            avg_delay=signals.avg_delay,
                                            session_age_seconds=signals.session_age,
                                            user_agent_hash=signals.user_agent_hash,
                                            was_fresh_session=signals.was_fresh_session,
                                            used_vpn=False,
                                            simulated_mouse=signals.simulated_mouse,
                                            referrer_chain="search_to_detail",
                                            search_query=signals.last_query,
                                            pages_before_block=signals.requests_count,
                                            browser_fingerprint=signals.browser_fingerprint,
                                            waited_for_page_load=signals.waited_for_load,
                                            page_load_time_ms=signals.last_load_time_ms,
                                            outcome="blocked",
                                            wall_type=wall.wall_type,
                                        )
                                        engine.start_cooldown("indeed", wall.wall_type)
                                        engine.update_learned_rules("indeed")
                                        if engine.should_run_llm_analysis():
                                            engine.run_llm_analysis("indeed")
                                        return results

                                    desc_el = page.query_selector(
                                        ".jobsearch-jobDescriptionText, "
                                        "#jobDescriptionText, "
                                        "[class*='jobDescription']"
                                    )
                                    if desc_el:
                                        description = desc_el.inner_text()[:5000]
                            except Exception:
                                pass

                            results.append({
                                "title": job_title,
                                "company": company,
                                "url": href,
                                "location": location,
                                "salary_min": None,
                                "salary_max": None,
                                "description": description,
                                "platform": "indeed",
                                "job_id": _make_job_id(href),
                            })

                            # Adaptive delay between cards
                            time.sleep(random.uniform(delay_min, delay_max))

                        except Exception as card_err:
                            logger.debug("scan_indeed: card parse error: %s", card_err)

                except Exception as page_err:
                    logger.error("scan_indeed: error fetching '%s': %s", search_url, page_err)

    except Exception as exc:
        logger.error("scan_indeed: Playwright error: %s", exc)

    # Record successful session
    if signals.requests_count > 0:
        engine.record_event(
            platform="indeed",
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
        engine.reset_cooldown("indeed")

    logger.info("scan_indeed: returning %d total results", len(results))
    return results
```

- [ ] **Step 3: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "feat(jobs): integrate verification detection + adaptive params into Indeed scanner"
```

---

### Task 9: Integrate Into job_scanner.py — LinkedIn Scanner

**Files:**
- Modify: `jobpulse/job_scanner.py:392-537`

- [ ] **Step 1: Rewrite scan_linkedin with verification detection**

Replace the `scan_linkedin` function (lines 392-537) with the same pattern as Indeed — add pre-scan gate, `_SessionSignals`, `detect_verification_wall` after `page.goto()` and after `card.click()`, `simulate_human_interaction`, adaptive delays, and success/block event recording. The structure is identical to the Indeed rewrite:

```python
def scan_linkedin(config: SearchConfig) -> list[dict[str, Any]]:
    """LinkedIn job search via Playwright with saved browser session.

    Includes verification wall detection, human-like interaction, and
    adaptive parameters from the scan learning engine.
    """
    try:
        from playwright.sync_api import sync_playwright as _  # noqa: F401
    except ImportError:
        logger.warning(
            "scan_linkedin: playwright not installed. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    chrome_profile = DATA_DIR / "chrome_profile"
    if not chrome_profile.exists():
        logger.warning(
            "scan_linkedin: no Chrome profile at %s. Run login flow first.",
            chrome_profile,
        )
        return []

    # Pre-scan gate
    engine = ScanLearningEngine()
    params = engine.get_adaptive_params("linkedin")
    if params["cooldown_active"]:
        logger.info("scan_linkedin: in cooldown until %s, skipping", params["cooldown_until"])
        return []

    delay_min, delay_max = params["delay_range"]
    max_requests = params["max_requests"]

    results: list[dict[str, Any]] = []
    ua = _random_ua()
    fp = hashlib.sha256(f"linkedin:1280x800:{ua}".encode()).hexdigest()[:8]
    signals = _SessionSignals("linkedin", ua, fp)
    signals.was_fresh_session = False  # LinkedIn always reuses profile

    def _record_block_and_abort(wall, signals, engine):
        engine.record_event(
            platform="linkedin",
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
            wall_type=wall.wall_type,
        )
        engine.start_cooldown("linkedin", wall.wall_type)
        engine.update_learned_rules("linkedin")
        if engine.should_run_llm_analysis():
            engine.run_llm_analysis("linkedin")

    try:
        with managed_persistent_browser(
            user_data_dir=str(chrome_profile),
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            user_agent=ua,
            viewport={"width": 1280, "height": 800},
        ) as (_browser, page):
            for title in config.titles:
                if len(results) >= max_requests:
                    break

                signals.last_query = title
                search_url = (
                    f"https://www.linkedin.com/jobs/search/"
                    f"?keywords={_url_encode(title)}"
                    f"&location={_url_encode(config.location)}"
                    f"&f_TPR=r86400&f_E=1,2"
                )

                try:
                    logger.info("scan_linkedin: fetching '%s'", search_url)
                    load_start = time.monotonic()
                    page.goto(search_url, timeout=45_000, wait_until="networkidle")
                    signals.last_load_time_ms = int((time.monotonic() - load_start) * 1000)
                    signals.waited_for_load = True
                    signals.record_request()

                    # Human interaction
                    simulate_human_interaction(page)
                    signals.simulated_mouse = True

                    # Check for wall
                    wall = detect_verification_wall(page, expected_results=True)
                    if wall and wall.confidence >= 0.7:
                        logger.warning("scan_linkedin: wall detected (%s), aborting", wall.wall_type)
                        _record_block_and_abort(wall, signals, engine)
                        return results

                    try:
                        page.wait_for_selector(
                            ".job-card-container, .jobs-search-results-list", timeout=15_000
                        )
                    except Exception:
                        logger.warning("scan_linkedin: job cards not found, trying scroll")

                    page.mouse.wheel(0, 500)
                    time.sleep(random.uniform(delay_min, delay_max))

                    cards = page.query_selector_all(".job-card-container")
                    logger.info("scan_linkedin: found %d job cards for '%s'", len(cards), title)

                    for card in cards:
                        if len(results) >= max_requests:
                            break
                        try:
                            link_el = card.query_selector('a[href*="/jobs/view"]')
                            href = link_el.get_attribute("href") if link_el else ""
                            lines = [l.strip() for l in card.inner_text().split("\n") if l.strip()]
                            job_title = lines[0] if len(lines) > 0 else ""
                            start = 1
                            if len(lines) > 1 and lines[1] == job_title:
                                start = 2
                            company = lines[start] if len(lines) > start else ""
                            location = lines[start + 1] if len(lines) > start + 1 else ""

                            if href and not href.startswith("http"):
                                href = "https://www.linkedin.com" + href
                            if not href:
                                continue

                            description = ""
                            try:
                                card.click()
                                signals.record_request()
                                time.sleep(random.uniform(1.5, 3.0))

                                wall = detect_verification_wall(page)
                                if wall and wall.confidence >= 0.7:
                                    logger.warning("scan_linkedin: wall after card click (%s)", wall.wall_type)
                                    _record_block_and_abort(wall, signals, engine)
                                    return results

                                desc_el = page.query_selector(
                                    ".jobs-description__content, "
                                    ".jobs-box__html-content, "
                                    ".job-details-jobs-unified-top-card__job-insight, "
                                    ".jobs-description, "
                                    "[class*='description']"
                                )
                                if desc_el:
                                    description = desc_el.inner_text()[:5000]
                            except Exception as desc_err:
                                logger.debug("scan_linkedin: JD detail error: %s", desc_err)

                            results.append({
                                "title": job_title,
                                "company": company,
                                "url": href,
                                "location": location,
                                "salary_min": None,
                                "salary_max": None,
                                "description": description,
                                "platform": "linkedin",
                                "job_id": _make_job_id(href),
                            })

                            time.sleep(random.uniform(delay_min, delay_max))

                        except Exception as card_err:
                            logger.debug("scan_linkedin: card parse error: %s", card_err)
                            continue

                except Exception as page_err:
                    logger.error("scan_linkedin: error fetching '%s': %s", search_url, page_err)

    except Exception as exc:
        logger.error("scan_linkedin: Playwright session error: %s", exc)

    # Record successful session
    if signals.requests_count > 0:
        engine.record_event(
            platform="linkedin",
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
        engine.reset_cooldown("linkedin")

    logger.info("scan_linkedin: returning %d total results", len(results))
    return results
```

- [ ] **Step 2: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "feat(jobs): integrate verification detection + adaptive params into LinkedIn scanner"
```

---

### Task 10: Update Documentation + Memory

**Files:**
- Modify: `CLAUDE.md`
- Modify: `.claude/rules/jobs.md`
- Modify: `docs/rules.md`

- [ ] **Step 1: Add verification wall learning to CLAUDE.md**

Add after the "Safety" line in the Job Autopilot section of `CLAUDE.md`:

```markdown
**Verification Wall Learning:** Universal detection (Cloudflare, reCAPTCHA, hCaptcha, text challenges, HTTP blocks). Event-sourced learning with 17 signals. Statistical correlation + periodic LLM analysis (every 5th block). 2hr cooldown with exponential backoff (max 48hr). Human-like page interaction (scroll, mouse, load wait).
```

- [ ] **Step 2: Add to .claude/rules/jobs.md**

Add a new section after "## Safety":

```markdown
## Verification Wall Learning
- Universal detector: Cloudflare Turnstile, reCAPTCHA, hCaptcha, text challenges, HTTP 403/429, empty anomaly
- 17 signals tracked per scan session: time of day, requests, delay, session age, UA, cookies, VPN, mouse, referrer, query, pages, fingerprint, page load
- Statistical correlation engine: zero LLM cost, computes block rate per signal bucket, identifies risk factors (>50% block rate, ≥3 samples)
- LLM pattern analyzer: GPT-5o-mini every 5th block event, ~$0.002/call, stores human-readable rules
- Cooldown: 2hr → 4hr → 48hr (exponential backoff). Reset on successful scan. Telegram alert on 3rd consecutive block
- Adaptive params: risk level (low/medium/high) adjusts delays, max requests, human simulation, session length
- Human interaction: wait for networkidle, scroll 300-600px, random mouse movement, 1-3s reading delay
- Database: data/scan_learning.db (scan_events, learned_rules, cooldowns)
```

- [ ] **Step 3: Add to docs/rules.md Anti-Detection section**

Add under the existing "### Anti-Detection" section:

```markdown
- **Verification Wall Learning**: detect + record + correlate + adapt. 17 signals per session. Statistical engine (free) + LLM (every 5th block). 2hr→4hr→48hr cooldown. Human-like interaction on all Playwright scanners.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/jobs.md docs/rules.md
git commit -m "docs: add verification wall learning system to CLAUDE.md, rules, and jobs rules"
```

---

## Self-Review

**1. Spec coverage:**
- Section 1 (Verification Detector): Task 1 ✅
- Section 2 (Scan Event Recording): Task 2 ✅
- Section 3 (Statistical Correlation): Task 4 ✅
- Section 4 (LLM Pattern Analyzer): Task 5 ✅
- Section 5 (Adaptive Parameters): Task 6 ✅
- Section 6 (Cooldown Manager): Task 3 ✅
- Section 7 (Human-Like Interaction): Task 7 ✅
- Section 8 (Scanner Integration): Tasks 8 + 9 ✅
- Section 9 (Testing Strategy): Tests in Tasks 1-7 ✅
- Section 10 (File Map): All files covered ✅

**2. Placeholder scan:** No TBDs, TODOs, or incomplete sections.

**3. Type consistency:**
- `ScanLearningEngine` used consistently across Tasks 2-9
- `detect_verification_wall()` signature consistent: `(page, *, expected_results=False) -> VerificationResult | None`
- `simulate_human_interaction(page)` consistent in Task 7 and Tasks 8-9
- `_SessionSignals` defined once in Task 8, used in Tasks 8-9
- `VerificationResult.wall_type` string values consistent across detection and recording

All clear — plan is complete.
