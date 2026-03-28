"""Tests for per-platform daily application quota tracking."""

import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from jobpulse.rate_limiter import (
    DAILY_CAPS,
    SESSION_BREAK_EVERY,
    TOTAL_DAILY_CAP,
    RateLimiter,
)


@pytest.fixture
def limiter(tmp_path):
    """RateLimiter backed by a temporary database."""
    db_path = str(tmp_path / "rate_limits.db")
    return RateLimiter(db_path=db_path)


def test_can_apply_under_limit(limiter):
    """Fresh state: every platform should be available."""
    assert limiter.can_apply("linkedin") is True
    assert limiter.can_apply("indeed") is True
    assert limiter.can_apply("reed") is True
    assert limiter.can_apply("generic") is True


def test_can_apply_over_platform_limit(limiter):
    """After exhausting a platform cap, can_apply returns False for that platform."""
    for _ in range(DAILY_CAPS["linkedin"]):
        limiter.record_application("linkedin")
    assert limiter.can_apply("linkedin") is False
    # Other platforms should still be available
    assert limiter.can_apply("indeed") is True


def test_can_apply_over_total_limit(limiter):
    """After exhausting the total daily cap, can_apply returns False for any platform."""
    # Spread across platforms to avoid hitting per-platform caps first
    platforms = list(DAILY_CAPS.keys())
    recorded = 0
    idx = 0
    while recorded < TOTAL_DAILY_CAP:
        platform = platforms[idx % len(platforms)]
        if limiter.can_apply(platform):
            limiter.record_application(platform)
            recorded += 1
        idx += 1

    assert limiter.get_total_today() == TOTAL_DAILY_CAP
    # Now every platform should be blocked by total cap
    assert limiter.can_apply("generic") is False
    assert limiter.can_apply("linkedin") is False


def test_get_remaining_shows_correct_counts(limiter):
    """Remaining counts decrease as applications are recorded."""
    remaining_before = limiter.get_remaining()
    assert remaining_before["linkedin"] == DAILY_CAPS["linkedin"]
    assert remaining_before["_total"] == TOTAL_DAILY_CAP

    limiter.record_application("linkedin")
    limiter.record_application("linkedin")

    remaining_after = limiter.get_remaining()
    assert remaining_after["linkedin"] == DAILY_CAPS["linkedin"] - 2
    assert remaining_after["_total"] == TOTAL_DAILY_CAP - 2


def test_should_take_break_every_10(limiter):
    """Break flag triggers at every SESSION_BREAK_EVERY applications."""
    for i in range(1, SESSION_BREAK_EVERY + 1):
        limiter.record_application("generic")
        if i == SESSION_BREAK_EVERY:
            assert limiter.should_take_break() is True
        else:
            assert limiter.should_take_break() is False


def test_different_days_dont_interfere(limiter):
    """Yesterday's applications should not affect today's counts."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Manually insert yesterday's records
    import sqlite3

    with sqlite3.connect(limiter.db_path) as conn:
        conn.execute(
            "INSERT INTO daily_counts (date, platform, count) VALUES (?, ?, ?)",
            (yesterday, "linkedin", 15),
        )
        conn.execute(
            "INSERT INTO session_tracker (date, total_today, last_break_at) VALUES (?, ?, ?)",
            (yesterday, 40, 0),
        )
        conn.commit()

    # Today should be completely fresh
    assert limiter.can_apply("linkedin") is True
    assert limiter.get_total_today() == 0
    assert limiter.get_remaining()["linkedin"] == DAILY_CAPS["linkedin"]


def test_unknown_platform_uses_generic_cap(limiter):
    """Platforms not in DAILY_CAPS use the generic cap."""
    for _ in range(DAILY_CAPS["generic"]):
        limiter.record_application("some_unknown_ats")
    assert limiter.can_apply("some_unknown_ats") is False


def test_reset_daily(limiter):
    """reset_daily clears today's counts."""
    limiter.record_application("linkedin")
    limiter.record_application("indeed")
    assert limiter.get_total_today() == 2

    limiter.reset_daily()
    assert limiter.get_total_today() == 0
    assert limiter.can_apply("linkedin") is True
