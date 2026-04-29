"""Tests for pre-submit application quality gate."""

from unittest.mock import patch, MagicMock

import pytest

from jobpulse.pre_submit_gate import PreSubmitGate, GateResult
from jobpulse.perplexity import CompanyResearch


@pytest.fixture
def gate():
    return PreSubmitGate()


@pytest.fixture
def company():
    return CompanyResearch(
        company="Acme AI",
        description="AI startup building NLP tools",
        tech_stack=["Python", "FastAPI", "PyTorch"],
    )


def _mock_llm_response(score: float, weaknesses: list[str] | None = None):
    import json
    return json.dumps({
        "score": score,
        "weaknesses": weaknesses or [],
        "suggestions": [],
    })


@patch("shared.agents.cognitive_llm_call")
def test_gate_passes_high_score(mock_llm, gate, company):
    """Score >= 7 passes the gate."""
    mock_llm.return_value = _mock_llm_response(8.5)

    result = gate.review(
        filled_answers={"Why us?": "I love NLP and your PyTorch stack."},
        jd_keywords=["NLP", "PyTorch", "Python"],
        company_research=company,
    )
    assert result.passed is True
    assert result.score >= 7.0


@patch("shared.agents.cognitive_llm_call")
def test_gate_blocks_low_score(mock_llm, gate, company):
    """Score < 7 blocks the gate."""
    mock_llm.return_value = _mock_llm_response(
        4.0, ["Generic answer", "Missing keywords"]
    )

    result = gate.review(
        filled_answers={"Why us?": "I want a job."},
        jd_keywords=["NLP", "PyTorch"],
        company_research=company,
    )
    assert result.passed is False
    assert result.score < 7.0
    assert len(result.weaknesses) > 0


@patch("shared.agents.cognitive_llm_call")
def test_gate_cognitive_failure_passes_by_default(mock_llm, gate, company):
    """Cognitive engine failure => gate passes (fail-open)."""
    mock_llm.return_value = None

    result = gate.review(
        filled_answers={"Why us?": "anything"},
        jd_keywords=[],
        company_research=company,
    )
    assert result.passed is True
    assert result.score == 0.0


def test_gate_result_model():
    r = GateResult(passed=True, score=8.5, weaknesses=[], suggestions=[])
    assert r.passed is True
    r2 = GateResult(passed=False, score=3.0, weaknesses=["Generic"], suggestions=["Be specific"])
    assert len(r2.weaknesses) == 1
