"""Item 8 — scan_learning LLM-analysis hit-rate measurement.

Pure observability: every ``run_llm_analysis`` call records the input
signal-set hash so we can compute ``1 - distinct/total`` per platform
over a 7-day window. The plan's decision rule:

  - hit_rate >= 0.30 over 7 days → add a cache by signal_set_hash
  - hit_rate <  0.30 → close the item with a measurement document

These tests verify the measurement scaffold itself is correct — the
cache decision is taken AFTER a week of real cron data lands.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobpulse.scan_learning import ScanLearningEngine


@pytest.fixture
def engine(tmp_path):
    return ScanLearningEngine(db_path=str(tmp_path / "scan.db"))


def test_record_call_persists(engine):
    engine._record_llm_analysis_call(
        platform="reed", signal_set_hash="abc123", event_count=20,
    )
    rates = engine.llm_analysis_hit_rate()
    assert rates == {
        "reed": {"total": 1, "distinct": 1, "hit_rate": 0.0},
    }


def test_hit_rate_with_repeats(engine):
    """5 calls, 2 distinct hashes → hit_rate = 1 - 2/5 = 0.6."""

    for h in ["a", "a", "a", "b", "b"]:
        engine._record_llm_analysis_call(
            platform="reed", signal_set_hash=h, event_count=20,
        )
    rates = engine.llm_analysis_hit_rate()
    reed = rates["reed"]
    assert reed["total"] == 5
    assert reed["distinct"] == 2
    assert pytest.approx(reed["hit_rate"], abs=1e-9) == 0.6


def test_hit_rate_separates_platforms(engine):
    engine._record_llm_analysis_call(platform="reed", signal_set_hash="x", event_count=1)
    engine._record_llm_analysis_call(platform="reed", signal_set_hash="x", event_count=1)
    engine._record_llm_analysis_call(platform="linkedin", signal_set_hash="y", event_count=1)
    rates = engine.llm_analysis_hit_rate()
    assert rates["reed"]["total"] == 2
    assert rates["reed"]["distinct"] == 1
    assert rates["linkedin"]["total"] == 1
    assert rates["linkedin"]["distinct"] == 1


def test_window_excludes_old_calls(engine):
    """Calls older than the window aren't counted."""

    import sqlite3
    engine._record_llm_analysis_call(
        platform="reed", signal_set_hash="recent", event_count=20,
    )
    old = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    conn = sqlite3.connect(engine.db_path)
    conn.execute(
        "INSERT INTO llm_analysis_calls "
        "(platform, signal_set_hash, event_count, ts) VALUES (?, ?, ?, ?)",
        ("reed", "old", 20, old),
    )
    conn.commit()
    conn.close()
    rates = engine.llm_analysis_hit_rate(days=7)
    assert rates["reed"]["total"] == 1


def test_run_llm_analysis_increments_counter(monkeypatch, engine):
    """A real ``run_llm_analysis`` call writes to the measurement table
    even if the LLM fails — instrumentation must be reliable."""

    # Seed one event so the analysis has something to classify.
    engine.record_event(
        platform="reed",
        requests_in_session=1, avg_delay=1.0, session_age_seconds=10,
        user_agent_hash="ua", was_fresh_session=True, used_vpn=False,
        simulated_mouse=False, referrer_chain="", search_query="q",
        pages_before_block=1, browser_fingerprint="fp",
        waited_for_page_load=True, page_load_time_ms=500,
        outcome="success",
    )

    # Stub the cognitive call so the test doesn't hit the network.
    monkeypatch.setattr(
        "shared.agents.cognitive_llm_call",
        lambda **kwargs: '{"pattern":"x","confidence":0.9,"recommendation":"y"}',
    )
    engine.run_llm_analysis("reed")

    rates = engine.llm_analysis_hit_rate()
    assert rates.get("reed", {}).get("total", 0) == 1
