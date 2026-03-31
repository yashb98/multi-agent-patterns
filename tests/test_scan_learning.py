"""Tests for ScanLearningEngine — verification wall learning system."""

import json
import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock


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


class TestCooldownManager:
    """Test cooldown logic with exponential backoff."""

    def test_no_cooldown_initially(self, engine):
        assert engine.can_scan_now("indeed") is True

    def test_first_block_sets_2hr_cooldown(self, engine):
        engine.start_cooldown("indeed", "cloudflare")
        assert engine.can_scan_now("indeed") is False

    def test_cooldown_expires(self, db_path: str, engine):
        """Manually insert an expired cooldown — should allow scanning."""
        past = datetime.now(timezone.utc) - timedelta(hours=3)
        expired = datetime.now(timezone.utc) - timedelta(hours=1)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO cooldowns (platform, blocked_at, cooldown_until, consecutive_blocks, last_wall_type) "
            "VALUES (?, ?, ?, 1, 'cloudflare')",
            ("indeed", past.isoformat(), expired.isoformat()),
        )
        conn.commit()
        conn.close()
        assert engine.can_scan_now("indeed") is True

    def test_second_block_doubles_cooldown(self, db_path: str, engine):
        engine.start_cooldown("indeed", "cloudflare")  # 2hr
        engine.start_cooldown("indeed", "cloudflare")  # 4hr
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT consecutive_blocks FROM cooldowns WHERE platform = 'indeed'"
        ).fetchone()
        conn.close()
        assert row[0] == 2

    def test_third_block_triggers_48hr_skip(self, db_path: str, engine):
        engine.start_cooldown("indeed", "cloudflare")
        engine.start_cooldown("indeed", "cloudflare")
        engine.start_cooldown("indeed", "cloudflare")  # 3rd → 48hr
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT consecutive_blocks, cooldown_until FROM cooldowns WHERE platform = 'indeed'"
        ).fetchone()
        conn.close()
        assert row[0] == 3
        cooldown_until = datetime.fromisoformat(row[1])
        hours_until = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 3600
        assert hours_until > 47.0

    def test_successful_scan_resets_cooldown(self, db_path: str, engine):
        engine.start_cooldown("indeed", "cloudflare")
        engine.reset_cooldown("indeed")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT * FROM cooldowns WHERE platform = 'indeed'").fetchone()
        conn.close()
        assert row is None

    def test_cooldown_per_platform_independent(self, engine):
        engine.start_cooldown("indeed", "cloudflare")
        assert engine.can_scan_now("indeed") is False
        assert engine.can_scan_now("linkedin") is True

    def test_get_cooldown_info(self, engine):
        engine.start_cooldown("indeed", "text_challenge")
        info = engine.get_cooldown_info("indeed")
        assert info is not None
        assert info["consecutive_blocks"] == 1
        assert info["last_wall_type"] == "text_challenge"

    def test_get_cooldown_info_returns_none_when_no_cooldown(self, engine):
        info = engine.get_cooldown_info("indeed")
        assert info is None

    def test_reset_cooldown_on_nonexistent_platform(self, engine):
        """reset_cooldown on a platform with no cooldown should not raise."""
        engine.reset_cooldown("indeed")  # Should not raise


class TestStatisticalCorrelation:
    """Test risk factor identification from event history."""

    def _seed_events(self, engine, platform: str, events: list[dict]):
        for e in events:
            engine.record_event(**_make_event_kwargs(
                platform=platform,
                requests_in_session=e.get("requests", 5),
                avg_delay=e.get("delay", 4.0),
                session_age_seconds=e.get("age", 300.0),
                user_agent_hash=e.get("ua", "ua1"),
                was_fresh_session=e.get("fresh", True),
                simulated_mouse=e.get("mouse", True),
                referrer_chain=e.get("referrer", "direct"),
                search_query=e.get("query", "python"),
                pages_before_block=e.get("pages", 5),
                browser_fingerprint=e.get("fp", "fp1"),
                waited_for_page_load=e.get("waited", True),
                page_load_time_ms=e.get("load_ms", 2000),
                outcome=e["outcome"],
                wall_type=e.get("wall", None),
            ))

    def test_no_events_returns_empty_risk_factors(self, engine):
        factors = engine.compute_risk_factors("indeed")
        assert factors == []

    def test_high_block_rate_ua_becomes_risk_factor(self, engine):
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "success", "ua": "bad_ua"},
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

    def test_low_delay_becomes_risk_factor(self, engine):
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.5},
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.0},
            {"outcome": "blocked", "wall": "text_challenge", "delay": 1.8},
            {"outcome": "success", "delay": 6.0},
            {"outcome": "success", "delay": 5.0},
            {"outcome": "success", "delay": 7.0},
        ])
        factors = engine.compute_risk_factors("indeed")
        signal_names = [f["signal"] for f in factors]
        assert "avg_delay" in signal_names

    def test_minimum_sample_size_enforced(self, engine):
        """Only 2 events with same UA — below min_sample=3."""
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "rare_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "rare_ua"},
        ])
        factors = engine.compute_risk_factors("indeed")
        ua_factors = [f for f in factors if f["signal"] == "user_agent_hash" and f["bucket"] == "rare_ua"]
        assert len(ua_factors) == 0

    def test_risk_factors_stored_as_learned_rules(self, db_path: str, engine):
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

    def test_risk_factors_only_for_requested_platform(self, engine):
        """Events on linkedin should not affect indeed risk factors."""
        self._seed_events(engine, "linkedin", [
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
            {"outcome": "blocked", "wall": "cloudflare", "ua": "bad_ua"},
        ])
        factors = engine.compute_risk_factors("indeed")
        assert factors == []

    def test_no_mouse_simulation_as_risk_factor(self, engine):
        """Not simulating mouse should show as risk factor if correlated with blocks."""
        self._seed_events(engine, "indeed", [
            {"outcome": "blocked", "wall": "cloudflare", "mouse": False},
            {"outcome": "blocked", "wall": "cloudflare", "mouse": False},
            {"outcome": "blocked", "wall": "cloudflare", "mouse": False},
            {"outcome": "success", "mouse": True},
            {"outcome": "success", "mouse": True},
            {"outcome": "success", "mouse": True},
        ])
        factors = engine.compute_risk_factors("indeed")
        mouse_factors = [f for f in factors if f["signal"] == "simulated_mouse"]
        assert len(mouse_factors) > 0
        # The "0" (False) bucket should be the risky one
        no_mouse = next((f for f in mouse_factors if f["bucket"] == "0"), None)
        assert no_mouse is not None
        assert no_mouse["block_rate"] >= 0.90


class TestLLMPatternAnalyzer:
    """Test periodic LLM analysis of block patterns."""

    def _seed_blocks(self, engine, count: int):
        for i in range(count):
            engine.record_event(**_make_event_kwargs(
                platform="indeed",
                outcome="blocked",
                wall_type="cloudflare",
                requests_in_session=8 + i,
                avg_delay=1.5,
            ))

    def test_should_analyze_false_under_5_blocks(self, engine):
        self._seed_blocks(engine, 3)
        assert engine.should_run_llm_analysis() is False

    def test_should_analyze_true_at_5_blocks(self, engine):
        self._seed_blocks(engine, 5)
        assert engine.should_run_llm_analysis() is True

    def test_should_analyze_true_at_10_blocks(self, engine):
        self._seed_blocks(engine, 10)
        assert engine.should_run_llm_analysis() is True

    def test_should_analyze_false_at_6_blocks(self, engine):
        self._seed_blocks(engine, 6)
        assert engine.should_run_llm_analysis() is False

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_stores_rule(self, mock_llm, engine, db_path: str):
        mock_llm.return_value = json.dumps({
            "pattern": "Indeed blocks after 8+ requests with delay < 2s",
            "confidence": 0.85,
            "recommendation": "Increase delay to 5-8s, limit to 5 requests per session",
        })
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
    def test_llm_analysis_handles_invalid_json(self, mock_llm, engine, db_path: str):
        mock_llm.return_value = "not valid json at all"
        self._seed_blocks(engine, 5)
        engine.run_llm_analysis("indeed")  # should not raise

        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT COUNT(*) FROM learned_rules WHERE source = 'llm'"
        ).fetchone()
        conn.close()
        assert rules[0] == 0

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_handles_none_response(self, mock_llm, engine, db_path: str):
        mock_llm.return_value = None
        self._seed_blocks(engine, 5)
        engine.run_llm_analysis("indeed")

        conn = sqlite3.connect(db_path)
        rules = conn.execute(
            "SELECT COUNT(*) FROM learned_rules WHERE source = 'llm'"
        ).fetchone()
        conn.close()
        assert rules[0] == 0

    @patch("jobpulse.scan_learning.safe_openai_call")
    def test_llm_analysis_no_events_does_nothing(self, mock_llm, engine, db_path: str):
        engine.run_llm_analysis("indeed")
        mock_llm.assert_not_called()
