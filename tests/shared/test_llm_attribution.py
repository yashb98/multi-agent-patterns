"""Tests for LLM agent_name attribution through the call stack."""

from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _force_openai_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    import shared.agents as agents_mod

    agents_mod._LLM_PROVIDER = None
    agents_mod._is_local = None
    agents_mod._use_fallback_models = None
    yield
    agents_mod._LLM_PROVIDER = None
    agents_mod._is_local = None
    agents_mod._use_fallback_models = None


def test_get_llm_passes_agent_name():
    from shared.agents import get_llm

    llm = get_llm(agent_name="email_classifier")
    assert hasattr(llm, "_agent_name")
    assert llm._agent_name == "email_classifier"


def test_get_llm_defaults_to_unknown():
    from shared.agents import get_llm

    llm = get_llm()
    assert llm._agent_name == "unknown"


def test_smart_llm_call_streaming_resolves_agent_name(monkeypatch, tmp_path):
    """smart_llm_call() streaming branch should pick up agent_name from _InstrumentedLLM."""
    monkeypatch.setenv("LLM_USAGE_DB", str(tmp_path / "llm_usage.db"))
    monkeypatch.setenv("STREAM_LLM_OUTPUT", "1")

    from shared.agents import _InstrumentedLLM
    from shared.logging_config import clear_trajectory_id, set_run_id, set_trajectory_id
    from shared.streaming import smart_llm_call

    set_run_id("test_run")
    set_trajectory_id("test_traj")

    class _FakeChunk:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        model_name = "gpt-4o-mini"

        def stream(self, messages, **kwargs):
            yield _FakeChunk("test ")
            yield _FakeChunk("response")

        def invoke(self, messages, **kwargs):
            return type("R", (), {"content": "test response", "response_metadata": {}})()

    try:
        fake = _FakeLLM()
        instrumented = _InstrumentedLLM(
            fake, model_hint="gpt-4o-mini", agent_name="screening_answers"
        )

        from langchain_core.messages import HumanMessage

        response = smart_llm_call(instrumented, [HumanMessage(content="test")])
    finally:
        clear_trajectory_id()

    conn = sqlite3.connect(str(tmp_path / "llm_usage.db"))
    row = conn.execute(
        "SELECT agent_name FROM llm_calls ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "screening_answers"
