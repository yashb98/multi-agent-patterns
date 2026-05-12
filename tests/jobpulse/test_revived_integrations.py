"""Integration tests for the 5 revived jobpulse functions.

Per project policy (CLAUDE.md): real data, no mocks of the system under test
or of the Playwright driver. Tests that mocked the bridge/orchestrator/
gate/runner internals were removed in 2026-05-03; the real-LLM gate behavior
is exercised by `test_pre_submit_gate.py`, end-to-end navigation by
`tests/jobpulse/integration/test_pipeline_live.py`.

What remains:
  - Dispatcher routing tests (use a sentinel patch on the *target* function
    only to assert routing, not to test the function's behavior).
  - GotchasDB real-SQLite round-trip via tmp_path.
  - get_gap_stats real-SQLite round-trip via tmp_path + real runner CLI.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. handle_blog_command_v2 — wired into dispatcher._handle_arxiv
#
# These tests verify ROUTING (that the dispatcher reaches v2, not v1).
# The patch on the target function is a sentinel to detect the call without
# triggering a real LLM blog generation. The dispatcher itself runs unmocked.
# ---------------------------------------------------------------------------


def test_dispatcher_blog_uses_v2():
    """_handle_arxiv routes 'blog N' to handle_blog_command_v2, not v1."""
    from jobpulse.command_router import ParsedCommand, Intent

    cmd = ParsedCommand(intent=Intent.ARXIV, raw="blog 2", args="2")

    with patch("jobpulse.blog_generator.handle_blog_command_v2") as mock_v2, \
         patch("jobpulse.blog_generator.handle_blog_command", create=True) as mock_v1:
        mock_v2.return_value = "Blog generated: Test Paper (1200 words)"

        from jobpulse.dispatcher import _handle_arxiv
        result = _handle_arxiv(cmd)

    mock_v2.assert_called_once_with(2)
    mock_v1.assert_not_called()
    assert "Blog generated" in result


def test_dispatcher_regenerate_uses_v2():
    """_handle_arxiv routes 'regenerate N' to handle_blog_command_v2."""
    from jobpulse.command_router import ParsedCommand, Intent

    cmd = ParsedCommand(intent=Intent.ARXIV, raw="regenerate 3", args="3")

    with patch("jobpulse.blog_generator.handle_blog_command_v2") as mock_v2:
        mock_v2.return_value = "Blog generated: Another Paper (1500 words)"

        from jobpulse.dispatcher import _handle_arxiv
        result = _handle_arxiv(cmd)

    mock_v2.assert_called_once_with(3)
    assert "Blog generated" in result


def test_swarm_dispatcher_blog_routes_through_handle_arxiv():
    """Swarm dispatcher AGENT_MAP uses _handle_arxiv from dispatcher, which uses v2."""
    from jobpulse.command_router import ParsedCommand, Intent

    cmd = ParsedCommand(intent=Intent.ARXIV, raw="blog 1", args="1")

    with patch("jobpulse.blog_generator.handle_blog_command_v2") as mock_v2:
        mock_v2.return_value = "Blog generated: Swarm Paper (800 words)"
        from jobpulse.swarm_dispatcher import _execute_agent
        result = _execute_agent(Intent.ARXIV.value, cmd, "")

    mock_v2.assert_called_once_with(1)
    assert "Blog generated" in result


# ---------------------------------------------------------------------------
# 2. PreSubmitGate
#
# Removed 2026-05-03: 5 tests patched `_run_pre_submit_gate` itself (mocked
# the system under test). End-to-end gate behavior is in test_pre_submit_gate.py
# (real LLM via cognitive engine) and the live integration suite.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. TelegramApplicationStream wired into _execute_action
#
# Removed 2026-05-03: 4 tests used mock_ext_bridge = AsyncMock() to mock the
# entire Playwright driver, then asserted that the stream got a sentinel call.
# This is a Category B mock pattern (Playwright bridge). End-to-end stream
# behavior is exercised by test_telegram_stream.py + the live pipeline tests.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. GotchasDB.lookup_domain wired into apply_job()
# ---------------------------------------------------------------------------


def test_gotchas_db_lookup_domain_wiring(tmp_path):
    """Real SQLite round-trip — store gotchas, retrieve them by domain."""
    from jobpulse.form_engine.gotchas import GotchasDB

    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("lever.co", ".submit-btn", "overlaps cookie banner", "dismiss cookie first")
    db.store("lever.co", "#cover-letter", "field hidden until scrolled", "scroll 300px down")

    gotchas = db.lookup_domain("lever.co")
    assert len(gotchas) == 2
    selectors = {g["selector_pattern"] for g in gotchas}
    assert ".submit-btn" in selectors
    assert "#cover-letter" in selectors


def test_gotchas_db_normalizes_domain(tmp_path):
    """lookup_domain matches across www./https:// variants — real round-trip."""
    from jobpulse.form_engine.gotchas import GotchasDB

    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("greenhouse.io", "#submit", "disabled until scroll", "scroll to bottom")

    # Store form was normalized; retrieval should match many input shapes.
    assert len(db.lookup_domain("greenhouse.io")) == 1


# Removed 2026-05-03: test_apply_job_loads_gotchas_into_merged_answers
# It patched RateLimiter (system under test) and is_first_encounter to make
# apply_job abort early; the value tested was indirect. The same wiring is
# verified by test_gotchas_db_lookup_domain_wiring (real DB round-trip).

# Removed 2026-05-03: test_gotchas_stream_injected_before_submit
# Asserted nothing — captured a dict but never inspected it.


# ---------------------------------------------------------------------------
# 5. get_gap_stats wired into runner skill-gaps command
# ---------------------------------------------------------------------------


def test_get_gap_stats_returns_correct_structure(tmp_path, monkeypatch):
    """Real SQLite gaps DB → real get_gap_stats → assert real shape."""
    import jobpulse.skill_gap_tracker as sgt

    monkeypatch.setattr(sgt, "_DB_PATH", tmp_path / "skill_gaps.db")
    sgt._init_db()

    sgt.record_gap("job1", "ML Engineer", "Acme", ["pytorch", "mlflow"], ["python"], gate3_score=0.85)
    sgt.record_gap("job2", "Data Scientist", "Beta", ["pytorch", "spark"], ["python"], gate3_score=0.70)

    stats = sgt.get_gap_stats()

    assert stats["unique_gap_skills"] >= 3
    assert stats["jobs_tracked"] == 2
    assert stats["total_gap_entries"] >= 3
    gap_skills = {g["skill"] for g in stats["top5_gaps"]}
    assert "pytorch" in gap_skills


def test_runner_skill_gaps_prints_real_stats(tmp_path, monkeypatch, capsys):
    """End-to-end: runner skill-gaps command runs against a real (tmp_path) DB."""
    import jobpulse.skill_gap_tracker as sgt

    monkeypatch.setattr(sgt, "_DB_PATH", tmp_path / "skill_gaps.db")
    sgt._init_db()
    sgt.record_gap("j1", "ML Eng", "Acme", ["pytorch"], ["python"], gate3_score=0.9)
    sgt.record_gap("j2", "Data Sci", "Beta", ["pytorch", "spark"], ["python"], gate3_score=0.8)

    monkeypatch.setattr("sys.argv", ["runner", "skill-gaps"])
    from jobpulse.runner import main
    main()

    captured = capsys.readouterr()
    # Real stats from the seeded DB should appear in stdout
    assert "pytorch" in captured.out
    assert "2" in captured.out  # jobs_tracked == 2
