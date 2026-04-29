"""Tests for SQLite-backed LLM usage telemetry."""

from __future__ import annotations

import sqlite3

from langchain_core.messages import HumanMessage

from shared.cost_tracker import (
    get_model_cost_snapshot_path,
    record_llm_usage,
)
from shared.logging_config import clear_trajectory_id, set_run_id, set_trajectory_id


class _FakeResponse:
    def __init__(self, content: str, model_name: str = "gpt-4.1-mini"):
        self.content = content
        self.response_metadata = {
            "model_name": model_name,
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }


def test_costs_load_from_dated_snapshot():
    snapshot = get_model_cost_snapshot_path()
    assert snapshot is not None
    assert snapshot.endswith("2026-04-22.json")


def test_record_llm_usage_persists_trajectory_context(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_cost_test")
    set_trajectory_id("traj_cost_1")
    try:
        response = _FakeResponse("hello world")
        usage = record_llm_usage(
            response,
            agent_name="researcher",
            messages=[HumanMessage(content="prompt text")],
            operation="invoke",
        )
    finally:
        clear_trajectory_id()

    assert usage["trajectory_id"] == "traj_cost_1"
    assert usage["agent"] == "researcher"

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute(
        """
        SELECT trajectory_id, run_id, agent_name, model, prompt_tokens, completion_tokens
        FROM llm_calls
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()

    assert row == (
        "traj_cost_1",
        "run_cost_test",
        "researcher",
        "gpt-4.1-mini",
        100,
        50,
    )


def test_record_openai_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_openai_test")
    set_trajectory_id("traj_openai_1")

    from shared.cost_tracker import record_openai_usage

    response = type(
        "ChatCompletion",
        (),
        {
            "usage": type(
                "Usage", (), {"prompt_tokens": 150, "completion_tokens": 45}
            )(),
            "model": "gpt-4o-mini-2024-07-18",
            "choices": [
                type(
                    "Choice",
                    (),
                    {"message": type("Msg", (), {"content": "test"})()},
                )()
            ],
        },
    )()

    try:
        result = record_openai_usage(
            response, agent_name="gate4", model_hint="gpt-4o-mini"
        )
    finally:
        clear_trajectory_id()

    assert result["agent"] == "gate4"
    assert result["prompt_tokens"] == 150
    assert result["completion_tokens"] == 45
    assert result["cost_usd"] > 0

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute(
        "SELECT agent_name, model, prompt_tokens, completion_tokens, cost_usd "
        "FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == "gate4"
    assert row[1] == "gpt-4o-mini-2024-07-18"
    assert row[2] == 150
    assert row[3] == 45
    assert row[4] > 0


def test_record_openai_usage_missing_usage(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_no_usage")
    set_trajectory_id("traj_no_usage")

    from shared.cost_tracker import record_openai_usage

    response = type(
        "ChatCompletion",
        (),
        {"usage": None, "model": "gpt-4o-mini", "choices": []},
    )()

    try:
        result = record_openai_usage(
            response, agent_name="gate4", model_hint="gpt-4o-mini"
        )
    finally:
        clear_trajectory_id()

    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["cost_usd"] == 0.0

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute(
        "SELECT prompt_tokens, completion_tokens FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == 0
    assert row[1] == 0


def test_get_daily_llm_summary(monkeypatch, tmp_path):
    """get_daily_llm_summary returns per-agent breakdown for recent calls."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    set_run_id("run_summary")
    set_trajectory_id("traj_summary")

    from shared.cost_tracker import record_openai_usage, get_daily_llm_summary

    for _ in range(2):
        resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 100, "completion_tokens": 30})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
        record_openai_usage(resp, agent_name="gate4", model_hint="gpt-4o-mini")
    for _ in range(2):
        resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 200, "completion_tokens": 60})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
        record_openai_usage(resp, agent_name="screening_answers", model_hint="gpt-4o-mini")
    resp = type("R", (), {"usage": type("U", (), {"prompt_tokens": 500, "completion_tokens": 100})(), "model": "gpt-4o-mini-2024-07-18", "choices": []})()
    record_openai_usage(resp, agent_name="page_analyzer", model_hint="gpt-4o-mini")

    clear_trajectory_id()

    summary = get_daily_llm_summary(days=1)
    assert summary["total_calls"] == 5
    assert summary["total_cost"] > 0
    assert summary["by_agent"]["gate4"]["calls"] == 2
    assert summary["by_agent"]["screening_answers"]["calls"] == 2
    assert summary["by_agent"]["page_analyzer"]["calls"] == 1


def test_get_daily_llm_summary_excludes_old(monkeypatch, tmp_path):
    """get_daily_llm_summary excludes data older than the requested window."""
    db_path = str(tmp_path / "llm_usage.db")
    monkeypatch.setenv("LLM_USAGE_DB", db_path)

    from shared.cost_tracker import get_daily_llm_summary, _usage_conn

    conn = _usage_conn()
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-01-01T00:00:00Z', 'old', 'old', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    conn.commit()

    summary = get_daily_llm_summary(days=1)
    assert summary["total_calls"] == 0
    assert summary["total_cost"] == 0.0


def test_cleanup_old_usage(monkeypatch, tmp_path):
    """cleanup_old_usage deletes rows older than retention_days."""
    db_path = str(tmp_path / "llm_usage.db")
    monkeypatch.setenv("LLM_USAGE_DB", db_path)

    from shared.cost_tracker import cleanup_old_usage, _usage_conn

    conn = _usage_conn()
    # 120 days ago
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-01-01T00:00:00Z', 't1', 'r1', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    # 60 days ago
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        "VALUES ('2026-02-28T00:00:00Z', 't2', 'r2', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    # Today
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO llm_calls (timestamp, trajectory_id, run_id, agent_name, operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd) "
        f"VALUES ('{now}', 't3', 'r3', 'gate4', 'chat', 'gpt-4o-mini', 100, 30, 130, 0.001)"
    )
    conn.commit()

    deleted = cleanup_old_usage(retention_days=90)
    assert deleted == 1  # only the 120-day-old row

    conn2 = _usage_conn()
    remaining = conn2.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
    assert remaining == 2
