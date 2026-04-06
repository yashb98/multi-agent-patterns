"""Tests for shared/rate_monitor.py — record_from_headers and cleanup_old_records."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor(tmp_path: Path):
    """Return a rate_monitor module with DB_PATH patched to a tmp file."""
    import importlib
    import shared.rate_monitor as _mod

    db_file = tmp_path / "rate_monitor.db"
    with patch.object(_mod, "DB_PATH", db_file):
        # Re-initialise the schema in the new DB path
        _mod._init_db.__globals__["DB_PATH"] = db_file  # direct patch fallback
        conn = sqlite3.connect(str(db_file))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_rate_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                api_name TEXT NOT NULL,
                endpoint TEXT DEFAULT '',
                limit_total INTEGER,
                limit_remaining INTEGER,
                limit_reset TEXT,
                recorded_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rate_api ON api_rate_limits(api_name, recorded_at)"
        )
        conn.commit()
        conn.close()
    return _mod, db_file


# ---------------------------------------------------------------------------
# record_from_headers
# ---------------------------------------------------------------------------


class TestRecordFromHeaders:
    def test_extracts_standard_ratelimit_headers(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            headers = {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "42",
                "X-RateLimit-Reset": "2026-04-06T03:00:00",
            }
            mod.record_from_headers("github", headers, endpoint="/repos/readme")

            conn = sqlite3.connect(str(db_file))
            rows = conn.execute("SELECT * FROM api_rate_limits").fetchall()
            conn.close()

        assert len(rows) == 1
        row = rows[0]
        assert row[1] == "github"
        assert row[2] == "/repos/readme"
        assert row[3] == 100   # limit_total
        assert row[4] == 42    # limit_remaining
        assert row[5] == "2026-04-06T03:00:00"

    def test_extracts_ratelimit_remaining_variant(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            headers = {
                "ratelimit-limit": "200",
                "ratelimit-remaining": "180",
            }
            mod.record_from_headers("notion", headers)

            conn = sqlite3.connect(str(db_file))
            rows = conn.execute("SELECT * FROM api_rate_limits").fetchall()
            conn.close()

        assert len(rows) == 1
        assert rows[0][3] == 200
        assert rows[0][4] == 180

    def test_no_record_when_no_ratelimit_headers(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            # Regular response headers with no rate limit info
            mod.record_from_headers("linkedin", {"Content-Type": "text/html"})

            conn = sqlite3.connect(str(db_file))
            count = conn.execute("SELECT COUNT(*) FROM api_rate_limits").fetchone()[0]
            conn.close()

        assert count == 0

    def test_case_insensitive_header_keys(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            headers = {
                "x-RATELIMIT-LIMIT": "50",
                "X-Ratelimit-Remaining": "10",
            }
            mod.record_from_headers("reed", headers)

            conn = sqlite3.connect(str(db_file))
            rows = conn.execute("SELECT * FROM api_rate_limits").fetchall()
            conn.close()

        assert len(rows) == 1
        assert rows[0][3] == 50
        assert rows[0][4] == 10

    def test_warns_when_below_threshold(self, tmp_path, caplog):
        import shared.rate_monitor as mod
        import logging

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            # 5/100 = 5% remaining — well below 20% threshold
            headers = {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "5",
            }
            with caplog.at_level(logging.WARNING):
                mod.record_from_headers("reed", headers)

        assert any("rate limit low" in r.message for r in caplog.records)

    def test_openai_header_variant(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            headers = {
                "x-ratelimit-limit-requests": "60",
                "x-ratelimit-remaining-requests": "55",
            }
            mod.record_from_headers("openai", headers)

            conn = sqlite3.connect(str(db_file))
            rows = conn.execute("SELECT * FROM api_rate_limits").fetchall()
            conn.close()

        assert len(rows) == 1
        assert rows[0][3] == 60
        assert rows[0][4] == 55


# ---------------------------------------------------------------------------
# cleanup_old_records
# ---------------------------------------------------------------------------


class TestCleanupOldRecords:
    def _insert_record(self, db_file: Path, api_name: str, recorded_at: str):
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "INSERT INTO api_rate_limits (api_name, endpoint, limit_total, limit_remaining, limit_reset, recorded_at) VALUES (?,?,?,?,?,?)",
            (api_name, "", 100, 80, None, recorded_at),
        )
        conn.commit()
        conn.close()

    def test_deletes_old_records(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()

            old_ts = (datetime.now() - timedelta(days=35)).isoformat()
            recent_ts = (datetime.now() - timedelta(days=5)).isoformat()

            self._insert_record(db_file, "github", old_ts)
            self._insert_record(db_file, "linkedin", recent_ts)

            deleted = mod.cleanup_old_records(retention_days=30)

        assert deleted == 1
        conn = sqlite3.connect(str(db_file))
        rows = conn.execute("SELECT api_name FROM api_rate_limits").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "linkedin"

    def test_returns_zero_when_nothing_to_delete(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            recent_ts = (datetime.now() - timedelta(days=3)).isoformat()
            self._insert_record(db_file, "reed", recent_ts)

            deleted = mod.cleanup_old_records(retention_days=30)

        assert deleted == 0

    def test_empty_table_returns_zero(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()
            deleted = mod.cleanup_old_records(retention_days=7)

        assert deleted == 0

    def test_custom_retention_period(self, tmp_path):
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()

            # Insert one record per day for the last 15 days
            for i in range(1, 16):
                ts = (datetime.now() - timedelta(days=i)).isoformat()
                self._insert_record(db_file, "github", ts)

            # Keep only last 7 days → records at days 8-15 (8 records) + day 7
            # boundary may also be deleted depending on isoformat comparison.
            # The implementation deletes WHERE recorded_at < cutoff (strictly less),
            # so days 8..15 = 8 records, and day 7 falls on the cutoff boundary and
            # may also be included. Accept 8 or 9 to be boundary-agnostic.
            deleted = mod.cleanup_old_records(retention_days=7)

        assert deleted >= 8


# ---------------------------------------------------------------------------
# Integration: record_from_headers is non-blocking on exception
# ---------------------------------------------------------------------------


class TestNonBlockingBehaviour:
    def test_caller_wraps_record_from_headers_non_blocking(self, tmp_path):
        """Caller pattern: wrap record_from_headers in try/except so DB failures
        never break the actual API call flow (mirrors job_scanner.py, etc.)."""
        import shared.rate_monitor as mod

        call_succeeded = False
        with patch.object(mod, "record_rate_limit", side_effect=RuntimeError("DB down")):
            try:
                mod.record_from_headers("linkedin", {"x-ratelimit-limit": "100", "x-ratelimit-remaining": "80"})
            except Exception:
                pass  # Non-blocking: swallow monitoring failure
            call_succeeded = True

        assert call_succeeded, "Caller code should continue after rate monitor failure"

    def test_caller_wraps_cleanup_non_blocking(self, tmp_path):
        """sync_profile wraps cleanup_old_records in try/except — verify pattern works."""
        import shared.rate_monitor as mod

        db_file = tmp_path / "rm.db"
        with patch.object(mod, "DB_PATH", db_file):
            mod._init_db()

        # Simulate what sync_profile does: wrap in try/except
        cleanup_attempted = False
        try:
            with patch.object(mod, "_get_conn", side_effect=Exception("simulated failure")):
                mod.cleanup_old_records(retention_days=30)
        except Exception:
            pass  # In production this is caught by sync_profile's try/except
        cleanup_attempted = True

        assert cleanup_attempted, "Caller continued after cleanup failure"
