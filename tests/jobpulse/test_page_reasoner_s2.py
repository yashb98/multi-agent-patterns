"""Tests for `jobpulse.page_analysis.page_reasoner` — Audit 2026-05-10 / Slice S2 / TP-3.

Closes the load-bearing-Fix-D regression. PageReasoner's first-pass JSON
parse failed twice in every Graphcore live run this session, triggering
the strict-JSON retry safety net that confidence=0.3 + field_count_guard
papered over. Per orchestration-agents.md, "Use response_format=
{'type':'json_object'} when expecting JSON from OpenAI" and "Never rely
on markdown stripping to extract JSON from responses". This slice:

1. Binds `response_format={"type":"json_object"}` to the LLM so Moonshot
   returns parseable JSON without prose wrappers.
2. Emits a `failure` signal to OptimizationEngine when the parse-cleanup
   path fires, so engagement-rate becomes observable in
   data/optimization.db.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def reasoner(tmp_path: Path):
    from jobpulse.page_analysis.page_reasoner import PageReasoner
    return PageReasoner(db_path=str(tmp_path / "cache.db"))


@pytest.fixture
def snapshot() -> dict:
    return {
        "url": "https://example.com/jobs/123",
        "page_text": "Senior Engineer\nApply",
        "buttons": ["Apply"],
        "fields": [],
        "wall_info": "",
    }


# ── 1. response_format binding ──


def test_call_llm_binds_response_format_json_object(reasoner, snapshot):
    """`_call_llm` must call `.bind(response_format={'type':'json_object'})`
    on the LLM before invoking it. This is the structural fix that prevents
    Kimi from emitting prose+JSON or markdown-fenced JSON."""
    from jobpulse.page_analysis.page_reasoner import PageAction

    bound_llm = MagicMock()
    bound_llm.invoke = MagicMock(return_value=MagicMock(
        content='{"page_understanding":"x","page_type":"application_form",'
                '"action":"fill_form","target_text":"","reasoning":"r",'
                '"confidence":0.8,"field_fills":[],"advance_button":"",'
                '"overlays_to_dismiss":[],"expected_outcome":"fields_filled"}'
    ))
    base_llm = MagicMock()
    base_llm.bind = MagicMock(return_value=bound_llm)

    bind_calls: list[dict] = []

    def capture_bind(**kwargs):
        bind_calls.append(kwargs)
        return bound_llm

    base_llm.bind = capture_bind

    with patch("jobpulse.page_analysis.page_reasoner.get_llm", return_value=base_llm), \
         patch("jobpulse.page_analysis.page_reasoner.smart_llm_call",
               side_effect=lambda llm, msgs: llm.invoke(msgs)):
        action = reasoner._call_llm("test prompt")

    # bind() must have been called with response_format
    assert bind_calls, "get_llm result was not bound — response_format never set"
    assert any(
        "response_format" in c and c["response_format"] == {"type": "json_object"}
        for c in bind_calls
    ), f"response_format not bound to JSON object: {bind_calls}"
    assert action.action == "fill_form"
    assert action.confidence == 0.8


# ── 2. Failure signal emission ──


def test_emits_failure_signal_when_parse_cleanup_engages(reasoner):
    """When the first parse fails (triggering the strict-JSON retry path),
    PageReasoner must emit a `failure` signal to OptimizationEngine so the
    cleanup-retry engagement-rate is observable."""
    from jobpulse.page_analysis.page_reasoner import PageAction

    # Mock LLM that returns malformed JSON on first call, valid on retry
    mock_llm = MagicMock()
    mock_llm.bind = MagicMock(return_value=mock_llm)

    call_count = {"n": 0}

    def smart_call(llm, msgs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Truly unparseable — no JSON object, neither first parse nor
            # the trailing-comma/comment cleanup will recover this.
            return MagicMock(content="not json — this is prose without any object")
        return MagicMock(content='{"page_understanding":"ok","page_type":"unknown",'
                                 '"action":"fill_form","target_text":"","reasoning":"",'
                                 '"confidence":0.7,"field_fills":[],"advance_button":"",'
                                 '"overlays_to_dismiss":[],"expected_outcome":"fields_filled"}')

    emit_calls: list[dict] = []

    class _StubEngine:
        def emit(self, signal_type, source_loop, domain, agent_name="",
                 payload=None, session_id="", severity="info"):
            emit_calls.append({
                "signal_type": signal_type,
                "source_loop": source_loop,
                "domain": domain,
                "severity": severity,
                "payload": payload or {},
            })

    with patch("jobpulse.page_analysis.page_reasoner.get_llm", return_value=mock_llm), \
         patch("jobpulse.page_analysis.page_reasoner.smart_llm_call", side_effect=smart_call), \
         patch("jobpulse.page_analysis.page_reasoner.get_optimization_engine",
               return_value=_StubEngine()):
        action = reasoner._call_llm("test prompt")

    # Two LLM calls (initial + strict-JSON retry)
    assert call_count["n"] == 2

    # At least one failure signal emitted
    failure_signals = [s for s in emit_calls if s["signal_type"] == "failure"]
    assert failure_signals, f"No failure signal emitted; calls={emit_calls}"

    # Signal must be tagged with page_reasoner source_loop
    sig = failure_signals[0]
    assert sig["source_loop"] == "page_reasoner"
    assert "parse_failure" in sig["payload"].get("reason", "")


def test_no_failure_signal_on_clean_first_parse(reasoner):
    """A successful first-parse must NOT emit a failure signal — that
    would inflate the engagement-rate metric and obscure real Kimi
    malformations."""
    mock_llm = MagicMock()
    mock_llm.bind = MagicMock(return_value=mock_llm)

    valid_json = ('{"page_understanding":"x","page_type":"application_form",'
                  '"action":"fill_form","target_text":"","reasoning":"r",'
                  '"confidence":0.9,"field_fills":[],"advance_button":"",'
                  '"overlays_to_dismiss":[],"expected_outcome":"fields_filled"}')

    emit_calls: list[dict] = []

    class _StubEngine:
        def emit(self, signal_type, **kw):
            emit_calls.append({"signal_type": signal_type, **kw})

    with patch("jobpulse.page_analysis.page_reasoner.get_llm", return_value=mock_llm), \
         patch("jobpulse.page_analysis.page_reasoner.smart_llm_call",
               return_value=MagicMock(content=valid_json)), \
         patch("jobpulse.page_analysis.page_reasoner.get_optimization_engine",
               return_value=_StubEngine()):
        action = reasoner._call_llm("test prompt")

    failure_signals = [s for s in emit_calls if s["signal_type"] == "failure"]
    assert not failure_signals, f"Failure signal emitted on clean parse: {failure_signals}"
    assert action.action == "fill_form"
    assert action.confidence == 0.9


# ── 3. Optimization-engine unavailability is handled gracefully ──


def test_emit_signal_does_not_raise_when_engine_unavailable(reasoner):
    """If `get_optimization_engine` is unavailable or its `emit` raises,
    PageReasoner must continue; observability is desirable but not
    load-bearing."""
    mock_llm = MagicMock()
    mock_llm.bind = MagicMock(return_value=mock_llm)

    def smart_call_fail_then_succeed(llm, msgs):
        if not getattr(smart_call_fail_then_succeed, "called", False):
            smart_call_fail_then_succeed.called = True
            return MagicMock(content="not json at all")
        return MagicMock(content='{"page_understanding":"x","page_type":"unknown",'
                                 '"action":"abort","target_text":"","reasoning":"",'
                                 '"confidence":0.0,"field_fills":[],"advance_button":"",'
                                 '"overlays_to_dismiss":[],"expected_outcome":"unknown"}')

    class _BoomEngine:
        def emit(self, *args, **kwargs):
            raise RuntimeError("optimization DB unreachable")

    with patch("jobpulse.page_analysis.page_reasoner.get_llm", return_value=mock_llm), \
         patch("jobpulse.page_analysis.page_reasoner.smart_llm_call",
               side_effect=smart_call_fail_then_succeed), \
         patch("jobpulse.page_analysis.page_reasoner.get_optimization_engine",
               return_value=_BoomEngine()):
        # Should not raise even though engine.emit raises
        action = reasoner._call_llm("test prompt")
        assert action is not None  # graceful continuation
