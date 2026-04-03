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
    content = json.dumps({
        "score": score,
        "weaknesses": weaknesses or [],
        "suggestions": [],
    })
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


@patch("jobpulse.pre_submit_gate._get_openai_client")
def test_gate_passes_high_score(mock_client, gate, company):
    """Score >= 7 passes the gate."""
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_llm_response(8.5)
    mock_client.return_value = client

    result = gate.review(
        filled_answers={"Why us?": "I love NLP and your PyTorch stack."},
        jd_keywords=["NLP", "PyTorch", "Python"],
        company_research=company,
    )
    assert result.passed is True
    assert result.score >= 7.0


@patch("jobpulse.pre_submit_gate._get_openai_client")
def test_gate_blocks_low_score(mock_client, gate, company):
    """Score < 7 blocks the gate."""
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_llm_response(
        4.0, ["Generic answer", "Missing keywords"]
    )
    mock_client.return_value = client

    result = gate.review(
        filled_answers={"Why us?": "I want a job."},
        jd_keywords=["NLP", "PyTorch"],
        company_research=company,
    )
    assert result.passed is False
    assert result.score < 7.0
    assert len(result.weaknesses) > 0


@patch("jobpulse.pre_submit_gate._get_openai_client")
def test_gate_no_client_passes_by_default(mock_client, gate, company):
    """No OpenAI client => gate passes (fail-open)."""
    mock_client.return_value = None

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
