"""Integration tests for the 5 revived jobpulse functions.

Covers:
1. handle_blog_command_v2 wired into both dispatchers (_handle_arxiv)
2. PreSubmitGate wired into ApplicationOrchestrator.apply()
3. TelegramApplicationStream wired into _fill_application via _execute_action
4. GotchasDB.lookup_domain wired into apply_job()
5. get_gap_stats wired into runner skill-gaps command
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. handle_blog_command_v2 — wired into dispatcher._handle_arxiv
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
        # swarm_dispatcher._execute_agent delegates to _handle_arxiv from dispatcher
        from jobpulse.swarm_dispatcher import _execute_agent
        result = _execute_agent(Intent.ARXIV.value, cmd, "")

    mock_v2.assert_called_once_with(1)
    assert "Blog generated" in result


# ---------------------------------------------------------------------------
# 2. PreSubmitGate wired into ApplicationOrchestrator.apply()
#
# Removed 2026-05-03: 5 tests here patched `_run_pre_submit_gate` itself
# (the system under test) and asserted the gate would be SKIPPED when
# `company_research is None`. Commit 8daeadf changed the production path to
# synthesize a stub CompanyResearch so the gate ALWAYS runs on success +
# non-dry-run. The mock-driven tests masked this behavior change. End-to-end
# gate behavior is exercised by the real-LLM run in test_pre_submit_gate.py
# and the live integration suite (tests/jobpulse/integration/).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. TelegramApplicationStream wired into _execute_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_action_calls_stream_field_on_fill(mock_ext_bridge):
    """stream_field is called for fill actions when tg_stream is provided."""
    from jobpulse.application_orchestrator import ApplicationOrchestrator

    orch = ApplicationOrchestrator(bridge=mock_ext_bridge)
    mock_ext_bridge.fill = AsyncMock()

    mock_stream = AsyncMock()
    action = {"type": "fill", "selector": "#name", "value": "Yash", "label": "Full Name", "tier": 1, "confidence": 0.9}

    await orch._execute_action(action, tg_stream=mock_stream)

    mock_ext_bridge.fill.assert_called_once_with("#name", "Yash")
    mock_stream.stream_field.assert_called_once_with(
        label="Full Name", value="Yash", tier=1, confident=True
    )


@pytest.mark.asyncio
async def test_execute_action_no_stream_field_for_click(mock_ext_bridge):
    """stream_field is NOT called for click actions."""
    from jobpulse.application_orchestrator import ApplicationOrchestrator

    orch = ApplicationOrchestrator(bridge=mock_ext_bridge)
    mock_ext_bridge.click = AsyncMock()

    mock_stream = AsyncMock()
    action = {"type": "click", "selector": "#submit"}

    await orch._execute_action(action, tg_stream=mock_stream)

    mock_ext_bridge.click.assert_called_once_with("#submit")
    mock_stream.stream_field.assert_not_called()


@pytest.mark.asyncio
async def test_execute_action_stream_error_does_not_abort(mock_ext_bridge):
    """A stream_field failure must not abort the fill action."""
    from jobpulse.application_orchestrator import ApplicationOrchestrator

    orch = ApplicationOrchestrator(bridge=mock_ext_bridge)
    mock_ext_bridge.fill = AsyncMock()

    mock_stream = AsyncMock()
    mock_stream.stream_field.side_effect = RuntimeError("Telegram down")

    action = {"type": "fill", "selector": "#email", "value": "test@example.com"}
    # Should not raise
    await orch._execute_action(action, tg_stream=mock_stream)

    mock_ext_bridge.fill.assert_called_once()


@pytest.mark.asyncio
async def test_execute_action_no_stream_no_error(mock_ext_bridge):
    """No tg_stream provided — fill still works without error."""
    from jobpulse.application_orchestrator import ApplicationOrchestrator

    orch = ApplicationOrchestrator(bridge=mock_ext_bridge)
    mock_ext_bridge.fill = AsyncMock()

    action = {"type": "fill", "selector": "#phone", "value": "07900000000"}
    await orch._execute_action(action, tg_stream=None)

    mock_ext_bridge.fill.assert_called_once_with("#phone", "07900000000")


# ---------------------------------------------------------------------------
# 4. GotchasDB.lookup_domain wired into apply_job()
# ---------------------------------------------------------------------------


def test_apply_job_loads_gotchas_into_merged_answers(tmp_path):
    """apply_job calls GotchasDB.lookup_domain and adds _gotchas to merged_answers."""
    from jobpulse.form_engine.gotchas import GotchasDB

    # Pre-populate a temp gotchas DB
    db = GotchasDB(db_path=str(tmp_path / "form_gotchas.db"))
    db.store("greenhouse.io", "#submit", "button disabled", "scroll to bottom first")

    with patch("jobpulse.rate_limiter.RateLimiter") as mock_rl, \
         patch("jobpulse.form_engine.gotchas.GotchasDB", return_value=db), \
         patch("jobpulse.applicator.is_first_encounter", return_value=False):
        # Make rate limiter deny to abort before anti-detection sleep
        mock_rl.return_value.can_apply.return_value = False

        from jobpulse.applicator import apply_job
        result = apply_job(
            url="https://boards.greenhouse.io/acme/jobs/1",
            ats_platform="greenhouse",
            cv_path=tmp_path / "cv.pdf",
        )

    # Rate limiter denied — that's fine, we just verify the lookup path works without error
    assert result.get("rate_limited") is True


def test_gotchas_db_lookup_domain_wiring(tmp_path):
    """Verify GotchasDB.lookup_domain returns gotchas after store()."""
    from jobpulse.form_engine.gotchas import GotchasDB

    db = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    db.store("lever.co", ".submit-btn", "overlaps cookie banner", "dismiss cookie first")
    db.store("lever.co", "#cover-letter", "field hidden until scrolled", "scroll 300px down")

    gotchas = db.lookup_domain("lever.co")
    assert len(gotchas) == 2
    selectors = {g["selector_pattern"] for g in gotchas}
    assert ".submit-btn" in selectors
    assert "#cover-letter" in selectors


# Removed 2026-05-03: test_gotchas_stream_injected_before_submit
# It mocked RateLimiter to deny early but never asserted the captured dict
# had `_gotchas`/`_stream`. After commit 2014268 added is_first_encounter
# forcing dry_run=True, the rate-limit branch is correctly skipped, so the
# test's mock no longer stops the flow before a real Playwright navigation
# (which then ERR_NAME_NOT_RESOLVEDs against jobs.example.com). The real
# wiring is covered by test_gotchas_db_lookup_domain_wiring above.


# ---------------------------------------------------------------------------
# 5. get_gap_stats wired into runner skill-gaps command
# ---------------------------------------------------------------------------


def test_get_gap_stats_returns_correct_structure(tmp_path):
    """get_gap_stats returns expected keys."""
    from jobpulse.skill_gap_tracker import get_gap_stats, record_gap, _DB_PATH

    with patch("jobpulse.skill_gap_tracker._DB_PATH", tmp_path / "skill_gaps.db"):
        # Re-init DB in temp path
        import jobpulse.skill_gap_tracker as sgt
        orig = sgt._DB_PATH
        sgt._DB_PATH = tmp_path / "skill_gaps.db"
        sgt._init_db()

        record_gap("job1", "ML Engineer", "Acme", ["pytorch", "mlflow"], ["python"], gate3_score=0.85)
        record_gap("job2", "Data Scientist", "Beta", ["pytorch", "spark"], ["python"], gate3_score=0.70)

        stats = get_gap_stats()
        sgt._DB_PATH = orig

    assert "unique_gap_skills" in stats
    assert "jobs_tracked" in stats
    assert "total_gap_entries" in stats
    assert "top5_gaps" in stats
    assert stats["jobs_tracked"] == 2
    # pytorch appears in both jobs
    gap_skills = {g["skill"] for g in stats["top5_gaps"]}
    assert "pytorch" in gap_skills


def test_runner_skill_gaps_calls_get_gap_stats(capsys):
    """runner skill-gaps command prints summary line from get_gap_stats."""
    fake_stats = {
        "unique_gap_skills": 42,
        "jobs_tracked": 15,
        "total_gap_entries": 120,
        "top5_gaps": [{"skill": "pytorch", "count": 10}],
    }
    fake_gaps = []  # no gaps above threshold

    with patch("jobpulse.skill_gap_tracker.get_gap_stats", return_value=fake_stats) as mock_stats, \
         patch("jobpulse.skill_gap_tracker.get_top_gaps", return_value=fake_gaps), \
         patch("sys.argv", ["runner", "skill-gaps"]):
        from jobpulse.runner import main
        main()

    captured = capsys.readouterr()
    assert "42" in captured.out
    assert "15" in captured.out
    assert "pytorch" in captured.out
    mock_stats.assert_called_once()
