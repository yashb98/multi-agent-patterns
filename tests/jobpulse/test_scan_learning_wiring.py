"""Tests proving ScanLearningEngine is wired to job_scanners and optimization."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from jobpulse.scan_learning import ScanLearningEngine
from jobpulse.job_scanners import handle_block, SessionSignals
from shared.optimization._engine import OptimizationEngine


# ── Shared kwargs for record_event ──────────────────────────────────

def _event_kwargs(*, outcome: str = "success", wall_type: str | None = None) -> dict:
    return dict(
        platform="linkedin",
        requests_in_session=5,
        avg_delay=3.2,
        session_age_seconds=120.0,
        user_agent_hash="abc12345",
        was_fresh_session=True,
        used_vpn=False,
        simulated_mouse=False,
        referrer_chain="direct",
        search_query="python developer",
        pages_before_block=5,
        browser_fingerprint="fp12345",
        waited_for_page_load=True,
        page_load_time_ms=1200,
        outcome=outcome,
        wall_type=wall_type,
    )


# ── Test A: record_event creates a row in scan_events ────────────

def test_record_event_creates_scan_event(tmp_path):
    db = str(tmp_path / "scan_learning.db")
    engine = ScanLearningEngine(db_path=db)

    event_id = engine.record_event(**_event_kwargs(outcome="success"))

    assert event_id  # non-empty string

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM scan_events WHERE id = ?", (event_id,)
        ).fetchone()

    assert row is not None
    # column order: id, platform, timestamp, time_of_day_bucket, ...
    assert row[0] == event_id
    assert row[1] == "linkedin"
    # outcome is column index 17
    assert row[17] == "success"


# ── Test B: blocked event emits optimization signal ──────────────

def test_blocked_event_emits_optimization_signal(tmp_path):
    scan_db = str(tmp_path / "scan_learning.db")
    opt_db = str(tmp_path / "optimization.db")

    opt_engine = OptimizationEngine(db_path=opt_db)

    with patch("shared.optimization._engine._shared_engine", opt_engine), \
         patch("shared.optimization.get_optimization_engine", return_value=opt_engine):
        # Patch the import path used inside scan_learning.py
        with patch(
            "jobpulse.scan_learning.get_optimization_engine",
            create=True,
        ) as mock_getter:
            # Since scan_learning.py does `from shared.optimization import get_optimization_engine`
            # at call-time inside record_event, we need to patch at the source
            pass

        # Actually, scan_learning.py does a local import:
        #   from shared.optimization import get_optimization_engine
        # So we patch shared.optimization.get_optimization_engine directly
        with patch(
            "shared.optimization.get_optimization_engine",
            return_value=opt_engine,
        ):
            scan_engine = ScanLearningEngine(db_path=scan_db)
            event_id = scan_engine.record_event(
                **_event_kwargs(outcome="blocked", wall_type="cloudflare")
            )

    # Verify the signal was written to optimization.db signals table
    with sqlite3.connect(opt_db) as conn:
        rows = conn.execute(
            "SELECT signal_type, source_loop, domain, agent_name, severity, session_id "
            "FROM signals WHERE source_loop = 'scan_learning'"
        ).fetchall()

    assert len(rows) >= 1
    sig = rows[0]
    assert sig[0] == "failure"        # signal_type
    assert sig[1] == "scan_learning"  # source_loop
    assert sig[2] == "linkedin"       # domain
    assert sig[3] == "scanner"        # agent_name
    assert sig[4] == "critical"       # severity
    assert sig[5] == event_id         # session_id


# ── Test C: handle_block records cooldown ────────────────────────

def test_handle_block_records_cooldown(tmp_path):
    scan_db = str(tmp_path / "scan_learning.db")
    opt_db = str(tmp_path / "optimization.db")
    engine = ScanLearningEngine(db_path=scan_db)

    # Build a SessionSignals for the test
    signals = SessionSignals(platform="indeed", user_agent="TestAgent/1.0")
    signals.record_request()  # so requests_count > 0

    # wall needs .wall_type attribute
    wall = SimpleNamespace(wall_type="turnstile")

    # Patch optimization engine to avoid touching production DB.
    # handle_block calls record_event (which emits optimization signal)
    # and update_learned_rules (which may also emit).
    opt_engine = OptimizationEngine(db_path=opt_db)
    with patch(
        "shared.optimization.get_optimization_engine",
        return_value=opt_engine,
    ):
        handle_block(engine, "indeed", wall, signals)

    # Verify cooldown was written
    with sqlite3.connect(scan_db) as conn:
        row = conn.execute(
            "SELECT platform, consecutive_blocks, last_wall_type FROM cooldowns WHERE platform = ?",
            ("indeed",),
        ).fetchone()

    assert row is not None
    assert row[0] == "indeed"
    assert row[1] == 1                # first block
    assert row[2] == "turnstile"

    # Also verify the scan_events row exists with outcome=blocked
    with sqlite3.connect(scan_db) as conn:
        event_row = conn.execute(
            "SELECT outcome, wall_type FROM scan_events WHERE platform = 'indeed'"
        ).fetchone()

    assert event_row is not None
    assert event_row[0] == "blocked"
    assert event_row[1] == "turnstile"
