"""Test claim extraction from summaries (Task 23: hallucination guard)."""

import pytest

from research_journal.summarizer import extract_claims_from_summary


def test_extracts_numeric_sentences(monkeypatch):
    """Verify extract_claims_from_summary returns sentences with numbers/benchmarks."""
    monkeypatch.setattr(
        "research_journal.summarizer._llm_extract_claims",
        lambda md: [
            "MoA improves over single-LoRA baselines by 4.2 points on MMLU.",
            "Training takes 12 GPU-hours on 8× A100.",
        ],
    )
    md = "## Results\nMoA improves... Training takes 12 GPU-hours..."
    claims = extract_claims_from_summary(md)
    assert len(claims) == 2
    assert any("MMLU" in c for c in claims)
    assert any("GPU-hours" in c for c in claims)


def test_extracts_empty_on_invalid_json(monkeypatch):
    """Return empty list if LLM returns invalid JSON."""
    monkeypatch.setattr(
        "research_journal.summarizer._llm_extract_claims",
        lambda md: [],
    )
    md = "## Results\nSome text with no numbers."
    claims = extract_claims_from_summary(md)
    assert claims == []


def test_filters_non_string_claims(monkeypatch):
    """Skip non-string items in the extracted claims list."""
    import json

    # Mock cognitive_llm_call to return JSON with mixed types
    def mock_cognitive_llm(*args, **kwargs):
        return json.dumps([
            "Valid claim with 5.2 points.",
            None,
            123,
            "Another valid claim.",
        ])

    monkeypatch.setattr(
        "shared.agents.cognitive_llm_call",
        mock_cognitive_llm,
    )
    md = "Some markdown with numbers."
    claims = extract_claims_from_summary(md)
    # Only string items should be kept
    assert len(claims) == 2
    assert all(isinstance(c, str) for c in claims)
    assert "5.2 points" in claims[0]
    assert "Another valid claim" in claims[1]
