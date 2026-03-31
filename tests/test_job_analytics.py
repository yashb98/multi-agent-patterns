"""Tests for jobpulse.job_analytics — TDD: tests written before implementation."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


def _seed_db(db_path: str, listings: list[dict] | None = None, applications: list[dict] | None = None) -> None:
    """Create tables and seed a test SQLite database."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS job_listings (
            job_id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, location TEXT,
            platform TEXT, salary_min REAL, salary_max REAL, description TEXT,
            remote INTEGER DEFAULT 0, seniority TEXT, ats_platform TEXT,
            easy_apply INTEGER DEFAULT 0, required_skills TEXT, preferred_skills TEXT,
            found_at TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS applications (
            job_id TEXT PRIMARY KEY, status TEXT DEFAULT 'Found', ats_score REAL DEFAULT 0,
            match_tier TEXT DEFAULT 'skip', matched_projects TEXT, cv_path TEXT,
            cover_letter_path TEXT, applied_at TEXT, notion_page_id TEXT,
            follow_up_date TEXT, custom_answers TEXT, created_at TEXT, updated_at TEXT
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    for row in listings or []:
        cur.execute(
            "INSERT INTO job_listings (job_id, title, company, url, location, platform, "
            "salary_min, salary_max, description, remote, seniority, found_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("job_id", ""),
                row.get("title", "Engineer"),
                row.get("company", "Acme"),
                row.get("url", "https://example.com"),
                row.get("location", "London"),
                row.get("platform", "linkedin"),
                row.get("salary_min"),
                row.get("salary_max"),
                row.get("description", ""),
                row.get("remote", 0),
                row.get("seniority", "mid"),
                row.get("found_at", now),
                row.get("created_at", now),
            ),
        )
    for row in applications or []:
        cur.execute(
            "INSERT INTO applications (job_id, status, ats_score, match_tier, "
            "matched_projects, cv_path, cover_letter_path, applied_at, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("job_id", ""),
                row.get("status", "Found"),
                row.get("ats_score", 0),
                row.get("match_tier", "skip"),
                row.get("matched_projects"),
                row.get("cv_path"),
                row.get("cover_letter_path"),
                row.get("applied_at"),
                row.get("created_at", now),
                row.get("updated_at", now),
            ),
        )
    con.commit()
    con.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# TestConversionFunnel
# ---------------------------------------------------------------------------
class TestConversionFunnel:
    """Funnel counts and conversion rates."""

    def test_empty_db_returns_zeros(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_db(db)

        from jobpulse.job_analytics import get_conversion_funnel

        result = get_conversion_funnel(days=7, db_path=db)
        assert result["found"] == 0
        assert result["applied"] == 0
        assert result["interview"] == 0
        assert result["offer"] == 0
        assert result["rejected"] == 0
        assert result["skipped"] == 0
        assert result["blocked"] == 0
        assert result["found_to_applied"] == 0.0
        assert result["applied_to_interview"] == 0.0

    def test_counts_by_status(self, tmp_path):
        db = str(tmp_path / "test.db")
        now = _now_iso()
        apps = (
            [{"job_id": f"found-{i}", "status": "Found", "created_at": now} for i in range(10)]
            + [{"job_id": f"applied-{i}", "status": "Applied", "created_at": now} for i in range(5)]
            + [{"job_id": f"interview-{i}", "status": "Interview", "created_at": now} for i in range(2)]
            + [{"job_id": "rejected-0", "status": "Rejected", "created_at": now}]
        )
        _seed_db(db, applications=apps)

        from jobpulse.job_analytics import get_conversion_funnel

        result = get_conversion_funnel(days=7, db_path=db)
        assert result["found"] == 10
        assert result["applied"] == 5
        assert result["interview"] == 2
        assert result["rejected"] == 1

    def test_conversion_rates(self, tmp_path):
        db = str(tmp_path / "test.db")
        now = _now_iso()
        apps = (
            [{"job_id": f"found-{i}", "status": "Found", "created_at": now} for i in range(20)]
            + [{"job_id": f"applied-{i}", "status": "Applied", "created_at": now} for i in range(10)]
            + [{"job_id": f"interview-{i}", "status": "Interview", "created_at": now} for i in range(2)]
        )
        _seed_db(db, applications=apps)

        from jobpulse.job_analytics import get_conversion_funnel

        result = get_conversion_funnel(days=7, db_path=db)
        # found_to_applied = applied / found * 100 = 10/20 * 100 = 50.0
        # But "found" only counts status == "Found".  Total inflow = found + applied + interview = 32.
        # Conversion rate: applied / (found + applied + interview + ...) or applied / total?
        # Spec says: Found→Applied rate = applied / found * 100
        # Here "found" = rows with status "Found" = 20
        assert result["found_to_applied"] == pytest.approx(10 / 20 * 100, abs=0.1)
        # applied_to_interview = interview / applied * 100 = 2 / 10 * 100 = 20.0
        assert result["applied_to_interview"] == pytest.approx(2 / 10 * 100, abs=0.1)

    def test_filters_by_days(self, tmp_path):
        db = str(tmp_path / "test.db")
        old = _days_ago_iso(30)
        recent = _days_ago_iso(2)
        apps = [
            {"job_id": "old-1", "status": "Found", "created_at": old},
            {"job_id": "old-2", "status": "Applied", "created_at": old},
            {"job_id": "new-1", "status": "Found", "created_at": recent},
            {"job_id": "new-2", "status": "Applied", "created_at": recent},
            {"job_id": "new-3", "status": "Interview", "created_at": recent},
        ]
        _seed_db(db, applications=apps)

        from jobpulse.job_analytics import get_conversion_funnel

        result = get_conversion_funnel(days=7, db_path=db)
        # Only 3 recent rows should be counted
        assert result["found"] == 1
        assert result["applied"] == 1
        assert result["interview"] == 1


# ---------------------------------------------------------------------------
# TestPlatformBreakdown
# ---------------------------------------------------------------------------
class TestPlatformBreakdown:
    """Platform-level counts via JOIN."""

    def test_counts_per_platform(self, tmp_path):
        db = str(tmp_path / "test.db")
        now = _now_iso()
        listings = [
            {"job_id": f"li-{i}", "platform": "linkedin"} for i in range(5)
        ] + [
            {"job_id": f"in-{i}", "platform": "indeed"} for i in range(3)
        ] + [
            {"job_id": f"rd-{i}", "platform": "reed"} for i in range(2)
        ]
        apps = [
            {"job_id": f"li-{i}", "status": "Found" if i < 3 else "Applied", "created_at": now}
            for i in range(5)
        ] + [
            {"job_id": f"in-{i}", "status": "Found" if i < 2 else "Applied", "created_at": now}
            for i in range(3)
        ] + [
            {"job_id": f"rd-{i}", "status": "Found", "created_at": now}
            for i in range(2)
        ]
        _seed_db(db, listings=listings, applications=apps)

        from jobpulse.job_analytics import get_platform_breakdown

        result = get_platform_breakdown(days=7, db_path=db)
        assert result["linkedin"]["found"] == 3
        assert result["linkedin"]["applied"] == 2
        assert result["indeed"]["found"] == 2
        assert result["indeed"]["applied"] == 1
        assert result["reed"]["found"] == 2
        assert result["reed"]["applied"] == 0

    def test_empty_db_returns_empty(self, tmp_path):
        db = str(tmp_path / "test.db")
        _seed_db(db)

        from jobpulse.job_analytics import get_platform_breakdown

        result = get_platform_breakdown(days=7, db_path=db)
        assert result == {}


# ---------------------------------------------------------------------------
# TestGateStats
# ---------------------------------------------------------------------------
class TestGateStats:
    """Blocked/Skipped counts from applications table."""

    def test_gate_block_counts(self, tmp_path):
        db = str(tmp_path / "test.db")
        now = _now_iso()
        apps = [
            {"job_id": "b1", "status": "Blocked", "created_at": now},
            {"job_id": "b2", "status": "Blocked", "created_at": now},
            {"job_id": "b3", "status": "Blocked", "created_at": now},
            {"job_id": "s1", "status": "Skipped", "created_at": now},
            {"job_id": "s2", "status": "Skipped", "created_at": now},
            {"job_id": "f1", "status": "Found", "created_at": now},
        ]
        _seed_db(db, applications=apps)

        from jobpulse.job_analytics import get_gate_stats

        result = get_gate_stats(days=7, db_path=db)
        assert result["blocked"] == 3
        assert result["skipped"] == 2
        assert result["total_screened"] == 5


# ---------------------------------------------------------------------------
# TestEnhancedJobStats
# ---------------------------------------------------------------------------
class TestEnhancedJobStats:
    """Formatted Telegram output string."""

    def test_formatted_output(self, tmp_path):
        db = str(tmp_path / "test.db")
        now = _now_iso()
        listings = [
            {"job_id": f"li-{i}", "platform": "linkedin"} for i in range(3)
        ]
        apps = [
            {"job_id": "li-0", "status": "Found", "created_at": now},
            {"job_id": "li-1", "status": "Applied", "created_at": now},
            {"job_id": "li-2", "status": "Interview", "created_at": now},
        ]
        _seed_db(db, listings=listings, applications=apps)

        from jobpulse.job_analytics import get_enhanced_job_stats

        output = get_enhanced_job_stats(db_path=db)
        assert "Funnel" in output
        assert "linkedin" in output.lower()
        assert "1" in output  # at least one number present
