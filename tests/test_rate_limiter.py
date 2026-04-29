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
    from datetime import datetime, timezone

    # Use UTC date to match RateLimiter._today() which uses UTC
    utc_today = datetime.now(timezone.utc).date()
    yesterday = (utc_today - timedelta(days=1)).isoformat()

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


# --- Task 7: Application Audit Trail ---


def test_application_log_recorded(limiter):
    """record_application stores audit trail with job details."""
    limiter.record_application(
        "greenhouse",
        job_id="gh_12345",
        company="Snowflake",
        url="https://boards.greenhouse.io/snowflake/jobs/12345",
    )

    log = limiter.get_application_log(days=1)
    assert len(log) == 1
    assert log[0]["platform"] == "greenhouse"
    assert log[0]["job_id"] == "gh_12345"
    assert log[0]["company"] == "Snowflake"
    assert log[0]["url"] == "https://boards.greenhouse.io/snowflake/jobs/12345"
    assert log[0]["recorded_at"]


def test_application_log_links_to_daily_counts(limiter):
    """application_log count matches daily_counts total."""
    limiter.record_application("greenhouse", job_id="gh_1", company="Snowflake", url="https://boards.greenhouse.io/snowflake/jobs/1")
    limiter.record_application("workday", job_id="wd_2", company="ASOS", url="https://asos.wd3.myworkdayjobs.com/careers/job/2")
    limiter.record_application("linkedin", job_id="li_3", company="Arm", url="https://www.linkedin.com/jobs/view/3")

    assert limiter.get_total_today() == 3

    log = limiter.get_application_log(days=1)
    assert len(log) == 3
    companies = {entry["company"] for entry in log}
    assert companies == {"Snowflake", "ASOS", "Arm"}


def test_application_log_backward_compatible(limiter):
    """record_application without extra args still works."""
    limiter.record_application("reed")
    assert limiter.get_total_today() == 1

    log = limiter.get_application_log(days=1)
    assert len(log) == 1
    assert log[0]["job_id"] == ""
    assert log[0]["company"] == ""


# --- Task 9: Proactive Quota Alerts ---

from unittest.mock import patch as mock_patch


def test_quota_alert_at_80_percent(tmp_path, monkeypatch):
    """Alert fires exactly once when total hits 80% of daily cap."""
    monkeypatch.setattr("jobpulse.rate_limiter.TOTAL_DAILY_CAP", 10)
    monkeypatch.setattr("jobpulse.rate_limiter.DAILY_CAPS", {"generic": 50})
    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    with mock_patch("jobpulse.rate_limiter.send_pipeline_alert") as mock_alert:
        for i in range(7):
            limiter.record_application("generic")
        assert mock_alert.call_count == 0

        limiter.record_application("generic")  # 8th = 80% of 10
        assert mock_alert.call_count == 1
        assert "80%" in mock_alert.call_args[0][0]
        assert mock_alert.call_args[1]["severity"] == "warning"
        assert mock_alert.call_args[1]["category"] == "quota"

        limiter.record_application("generic")  # 9th — no second alert
        assert mock_alert.call_count == 1


def test_platform_quota_alert(tmp_path, monkeypatch):
    """Alert fires when a single platform hits 80% of its cap."""
    monkeypatch.setattr("jobpulse.rate_limiter.DAILY_CAPS", {"linkedin": 5, "generic": 5})
    monkeypatch.setattr("jobpulse.rate_limiter.TOTAL_DAILY_CAP", 50)
    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    with mock_patch("jobpulse.rate_limiter.send_pipeline_alert") as mock_alert:
        for i in range(3):
            limiter.record_application("linkedin")
        assert mock_alert.call_count == 0

        limiter.record_application("linkedin")  # 4th = 80% of 5
        assert mock_alert.call_count == 1
        assert "linkedin" in mock_alert.call_args[0][0].lower()


# --- Task 13: DB Retention Cleanup ---


def test_cleanup_old_preserves_recent(tmp_path):
    """cleanup_old deletes old records but preserves recent ones."""
    import sqlite3
    from datetime import datetime, timezone, timedelta

    limiter = RateLimiter(db_path=str(tmp_path / "rate_limits.db"))

    utc_now = datetime.now(timezone.utc)
    old_date = (utc_now - timedelta(days=45)).strftime("%Y-%m-%d")
    recent_date = (utc_now - timedelta(days=15)).strftime("%Y-%m-%d")
    today = utc_now.strftime("%Y-%m-%d")

    with sqlite3.connect(limiter.db_path) as conn:
        for d, plat in [(old_date, "greenhouse"), (recent_date, "workday"), (today, "linkedin")]:
            conn.execute(
                "INSERT INTO daily_counts (date, platform, count) VALUES (?, ?, 1)",
                (d, plat),
            )
            conn.execute(
                "INSERT INTO application_log (date, platform, job_id, company, url, recorded_at) "
                "VALUES (?, ?, 'j1', 'TestCo', 'https://example.com', ?)",
                (d, plat, f"{d}T12:00:00Z"),
            )
        conn.commit()

    deleted = limiter.cleanup_old(retention_days=30)
    assert deleted > 0

    with sqlite3.connect(limiter.db_path) as conn:
        dc_count = conn.execute("SELECT COUNT(*) FROM daily_counts").fetchone()[0]
        al_count = conn.execute("SELECT COUNT(*) FROM application_log").fetchone()[0]

    assert dc_count == 2  # recent + today
    assert al_count == 2
