"""Tests for ScanLearningEngine — verification wall learning system."""

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "scan_learning.db")


@pytest.fixture
def engine(db_path: str):
    from jobpulse.scan_learning import ScanLearningEngine

    return ScanLearningEngine(db_path=db_path)


def _make_event_kwargs(**overrides) -> dict:
    """Default event kwargs with overrides."""
    defaults = {
        "platform": "linkedin",
        "requests_in_session": 5,
        "avg_delay": 3.5,
        "session_age_seconds": 300.0,
        "user_agent_hash": "abc123",
        "was_fresh_session": True,
        "used_vpn": False,
        "simulated_mouse": True,
        "referrer_chain": "google->linkedin",
        "search_query": "python developer london",
        "pages_before_block": 4,
        "browser_fingerprint": "fp_xyz",
        "waited_for_page_load": True,
        "page_load_time_ms": 1200,
        "outcome": "success",
        "wall_type": None,
    }
    defaults.update(overrides)
    return defaults


class TestScanEventRecording:
    """Tests for scan event recording functionality."""

    def test_init_creates_tables(self, db_path: str, engine):
        """Verify scan_events, learned_rules, cooldowns tables exist after init."""
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = sorted(row[0] for row in cursor.fetchall())
        conn.close()
        assert "cooldowns" in tables
        assert "learned_rules" in tables
        assert "scan_events" in tables

    def test_record_success_event(self, db_path: str, engine):
        """Record a success event and verify count=1, platform and outcome correct."""
        event_id = engine.record_event(**_make_event_kwargs())
        assert event_id is not None

        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT * FROM scan_events").fetchall()
        conn.close()

        assert len(rows) == 1
        # Check by column name
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM scan_events WHERE id = ?", (event_id,)).fetchone()
        conn.close()

        assert row["platform"] == "linkedin"
        assert row["outcome"] == "success"
        assert row["id"] == event_id

    def test_record_blocked_event(self, db_path: str, engine):
        """Record a blocked event with wall_type and verify stored correctly."""
        event_id = engine.record_event(
            **_make_event_kwargs(outcome="blocked", wall_type="captcha")
        )

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM scan_events WHERE id = ?", (event_id,)).fetchone()
        conn.close()

        assert row["outcome"] == "blocked"
        assert row["wall_type"] == "captcha"

    def test_time_of_day_bucket_assigned(self, db_path: str, engine):
        """Verify time_of_day_bucket is one of morning/afternoon/evening/night."""
        event_id = engine.record_event(**_make_event_kwargs())

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM scan_events WHERE id = ?", (event_id,)).fetchone()
        conn.close()

        assert row["time_of_day_bucket"] in ("morning", "afternoon", "evening", "night")

    def test_multiple_events_recorded(self, db_path: str, engine):
        """Record 5 events and verify count=5."""
        for i in range(5):
            engine.record_event(**_make_event_kwargs(requests_in_session=i + 1))

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM scan_events").fetchone()[0]
        conn.close()

        assert count == 5

    def test_get_total_blocks_counts_correctly(self, db_path: str, engine):
        """3 blocked + 2 success = get_total_blocks returns 3."""
        for _ in range(3):
            engine.record_event(
                **_make_event_kwargs(outcome="blocked", wall_type="captcha")
            )
        for _ in range(2):
            engine.record_event(**_make_event_kwargs(outcome="success"))

        assert engine.get_total_blocks() == 3

    def test_get_total_blocks_filters_by_platform(self, db_path: str, engine):
        """Blocks on indeed vs linkedin counted separately."""
        for _ in range(3):
            engine.record_event(
                **_make_event_kwargs(
                    platform="indeed", outcome="blocked", wall_type="captcha"
                )
            )
        for _ in range(2):
            engine.record_event(
                **_make_event_kwargs(
                    platform="linkedin", outcome="blocked", wall_type="login_wall"
                )
            )
        engine.record_event(**_make_event_kwargs(platform="linkedin", outcome="success"))

        assert engine.get_total_blocks(platform="indeed") == 3
        assert engine.get_total_blocks(platform="linkedin") == 2
        assert engine.get_total_blocks() == 5
