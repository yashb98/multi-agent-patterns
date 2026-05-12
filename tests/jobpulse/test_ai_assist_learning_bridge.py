"""Wiring test for ai_assist_logger.finalize_session(push_to_learning=True).

Reproduces the bug found 2026-05-04: when start_session is called without
original_mapping (the typical case for emergency Claude/Kimi assists), the
session has empty original_mapping/final_mapping JSON. The bridge then calls
record_corrections({}, {}) which writes 0 rows, but the bridge function returns
len(value_fixes) regardless and silently lies about success.

This test asserts the FULL wiring chain fires after a single finalize call:
- field_corrections.db gets rows
- agent_rules.db gets rows (correction_override rules)
- Qdrant cache (screening_semantic_cache) returns the answer on lookup

Per .claude/rules/testing.md, all DBs use tmp_path — never production paths.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_dbs(tmp_path, monkeypatch):
    """Redirect every SQLite path the bridge writes to into tmp_path."""
    # Patch DATA_DIR-derived paths used by each downstream module.
    # The modules build their DB paths at import / lazy-init time from these
    # constants, so we patch the constants before the modules construct singletons.
    paths = {
        "ai_sessions": tmp_path / "ai_assist_sessions.db",
        "field_corrections": tmp_path / "field_corrections.db",
        "agent_rules": tmp_path / "agent_rules.db",
        "trajectory": tmp_path / "trajectory.db",
        "optimization": tmp_path / "optimization.db",
    }
    # AgentRulesDB constructs from `_DEFAULT_DB` — patch the module constant.
    import jobpulse.agent_rules as ar_mod
    monkeypatch.setattr(ar_mod, "_DEFAULT_DB", str(paths["agent_rules"]), raising=False)
    # CorrectionCapture also uses module-level DB constants — patch defensively.
    import jobpulse.correction_capture as cc_mod
    if hasattr(cc_mod, "_DEFAULT_DB"):
        monkeypatch.setattr(cc_mod, "_DEFAULT_DB", str(paths["field_corrections"]),
                            raising=False)
    if hasattr(cc_mod, "DB_PATH"):
        monkeypatch.setattr(cc_mod, "DB_PATH", str(paths["field_corrections"]),
                            raising=False)
    return paths


def _row_count(db_path: Path, table: str) -> int:
    if not db_path.exists():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def test_emergency_assist_without_original_mapping_propagates_to_all_dbs(
    isolated_dbs, monkeypatch
):
    """The typical Claude/Kimi assist: start_session without original_mapping,
    record_fix per field, finalize. All downstream DBs must get rows."""
    from jobpulse.ai_assist_logger import AIAssistLogger
    from jobpulse.correction_capture import CorrectionCapture
    from jobpulse.agent_rules import AgentRulesDB

    # Force fresh instances so the patched DB_PATH constants take effect
    monkeypatch.setattr(
        "jobpulse.ai_assist_logger._logger_instance", None, raising=False
    )

    ai = AIAssistLogger(db_path=isolated_dbs["ai_sessions"])

    # Stub out screening_semantic_cache + optimization engine + trajectory store
    # — those have their own wiring tests; here we focus on the SQL learning DBs.
    # The bridge calls these external systems — stub them out so the test
    # focuses on the SQL learning DBs (which have their _DEFAULT_DB patched
    # in the fixture, so init runs against tmp_path).
    with patch("jobpulse.screening_semantic_cache.get_screening_semantic_cache") as cache_factory, \
         patch("shared.optimization.get_optimization_engine") as opt_factory, \
         patch("jobpulse.trajectory_store.get_trajectory_store") as traj_factory, \
         patch("jobpulse.screening_feedback_loop.ScreeningFeedbackLoop") as fb_factory:
        cache_factory.return_value.cache = lambda *a, **kw: None
        opt_factory.return_value.emit = lambda *a, **kw: None
        traj_factory.return_value.mark_corrected = lambda *a, **kw: None
        fb_factory.return_value.learn_from_correction = lambda *a, **kw: None

        # The actual flow under test: emergency assist without original_mapping
        sess = ai.start_session(
            agent_name="claude",
            job_id="test_job_001",
            domain="job-boards.greenhouse.io",
            platform="greenhouse",
            # NB: no original_mapping passed — this is the bug-trigger
        )

        ai.record_fix(
            sess.session_id,
            field_label="Country",
            old_value="",
            new_value="United Kingdom +44",
            reasoning="React-Select fill failed at agent layer",
            fix_category="value_correction",
        )
        ai.record_fix(
            sess.session_id,
            field_label="Location (City)",
            old_value="",
            new_value="Dundee, Dundee City, United Kingdom",
            reasoning="Autocomplete picked wrong option",
            fix_category="value_correction",
        )

        result = ai.finalize_session(sess.session_id, push_to_learning=True)

    # Assertions: ALL downstream DBs should have rows after finalize
    assert result["fixes_pushed"] == 2, f"finalize reported {result['fixes_pushed']} fixes"
    assert _row_count(isolated_dbs["field_corrections"], "field_corrections") == 2, (
        "field_corrections.db is empty — bridge silently no-op'd "
        "(this is the bug being fixed)"
    )
    assert _row_count(isolated_dbs["agent_rules"], "agent_rules") == 2, (
        "agent_rules.db is empty — auto_generate_from_correction was never called "
        "from ai_assist bridge (rules only got generated inside confirm_application "
        "before this fix)"
    )


def test_assist_with_original_mapping_still_works(isolated_dbs, monkeypatch):
    """Regression guard: when caller DOES pass original_mapping, the existing
    diff-based path should still produce corrections."""
    from jobpulse.ai_assist_logger import AIAssistLogger

    ai = AIAssistLogger(db_path=isolated_dbs["ai_sessions"])

    with patch("jobpulse.screening_semantic_cache.get_screening_semantic_cache"), \
         patch("shared.optimization.get_optimization_engine"), \
         patch("jobpulse.trajectory_store.get_trajectory_store"), \
         patch("jobpulse.screening_feedback_loop.ScreeningFeedbackLoop"):

        sess = ai.start_session(
            agent_name="kimi",
            job_id="test_job_002",
            domain="boards.greenhouse.io",
            platform="greenhouse",
            original_mapping={"Country": "Wrong Value"},
        )
        ai.record_fix(
            sess.session_id,
            field_label="Country",
            old_value="Wrong Value",
            new_value="United Kingdom +44",
            fix_category="value_correction",
        )
        result = ai.finalize_session(
            sess.session_id,
            final_mapping={"Country": "United Kingdom +44"},
            push_to_learning=True,
        )

    assert result["fixes_pushed"] == 1
    assert _row_count(isolated_dbs["field_corrections"], "field_corrections") == 1
    assert _row_count(isolated_dbs["agent_rules"], "agent_rules") == 1
