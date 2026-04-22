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
