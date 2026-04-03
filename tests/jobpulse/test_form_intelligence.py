"""Tests for FormIntelligence and FieldAnswer model."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jobpulse.ext_models import FieldAnswer
from jobpulse.form_intelligence import FormIntelligence, _generate_answer_llm


# ---------------------------------------------------------------------------
# FieldAnswer model tests
# ---------------------------------------------------------------------------


def test_field_answer_model() -> None:
    """FieldAnswer stores all supplied fields correctly."""
    fa = FieldAnswer(answer="Yes", tier=1, confidence=1.0, tier_name="pattern")
    assert fa.answer == "Yes"
    assert fa.tier == 1
    assert fa.confidence == 1.0
    assert fa.tier_name == "pattern"


def test_field_answer_defaults() -> None:
    """tier_name defaults to 'unknown' when not provided."""
    fa = FieldAnswer(answer="No", tier=4, confidence=0.7)
    assert fa.tier_name == "unknown"


def test_field_answer_empty() -> None:
    """FieldAnswer accepts an empty answer string."""
    fa = FieldAnswer(answer="", tier=1, confidence=0.0, tier_name="pattern")
    assert fa.answer == ""


# ---------------------------------------------------------------------------
# FormIntelligence.resolve() — Tier 1 pattern match
# ---------------------------------------------------------------------------


def test_resolve_pattern_match() -> None:
    """'right to work' question matches Tier 1 pattern → answer='Yes', tier=1."""
    fi = FormIntelligence()
    result = fi.resolve("Do you have the right to work in the UK?")
    assert result.tier == 1
    assert result.tier_name == "pattern"
    assert result.answer == "Yes"
    assert result.confidence == 1.0


def test_resolve_salary_placeholder() -> None:
    """Salary expectation question resolves via ROLE_SALARY placeholder, tier=1."""
    fi = FormIntelligence()
    result = fi.resolve(
        "What is your expected salary?",
        job_context={"job_title": "data scientist", "company": "ACME"},
        input_type="number",
    )
    assert result.tier == 1
    assert result.tier_name == "pattern"
    # Should return a numeric string (no currency symbol, no commas for number type)
    assert result.answer.isdigit() or result.answer.replace(",", "").replace("-", "").isdigit()


def test_resolve_llm_fallback() -> None:
    """A unique open-ended question falls through to Tier 4 (LLM)."""
    fi = FormIntelligence()
    with patch(
        "jobpulse.form_intelligence._generate_answer_llm",
        return_value="I am very passionate about this role.",
    ) as mock_llm:
        result = fi.resolve(
            "Why do you uniquely deserve this once-in-a-lifetime opportunity?",
            job_context={"job_title": "AI Engineer", "company": "DeepThought"},
        )

    mock_llm.assert_called_once()
    assert result.tier == 4
    assert result.tier_name == "llm"
    assert result.answer == "I am very passionate about this role."


def test_resolve_returns_field_answer_type() -> None:
    """resolve() always returns a FieldAnswer instance."""
    fi = FormIntelligence()
    with patch("jobpulse.form_intelligence._generate_answer_llm", return_value="Some answer"):
        result = fi.resolve("Tell me something completely unprecedented.")
    assert isinstance(result, FieldAnswer)


# ---------------------------------------------------------------------------
# FormIntelligence.resolve() — empty question guard
# ---------------------------------------------------------------------------


def test_resolve_empty_question_returns_empty() -> None:
    """Empty or whitespace-only question returns FieldAnswer with empty answer."""
    fi = FormIntelligence()
    result = fi.resolve("")
    assert result.answer == ""
    assert result.tier == 1


def test_resolve_whitespace_question_returns_empty() -> None:
    """Whitespace-only question returns FieldAnswer with empty answer."""
    fi = FormIntelligence()
    result = fi.resolve("   ")
    assert result.answer == ""


# ---------------------------------------------------------------------------
# Tier 2 — semantic cache
# ---------------------------------------------------------------------------


def test_resolve_uses_semantic_cache_on_miss() -> None:
    """When cache returns None, falls through to LLM (Tier 4)."""
    mock_cache = MagicMock()
    mock_cache.find_similar.return_value = None
    fi = FormIntelligence(semantic_cache=mock_cache)

    with patch("jobpulse.form_intelligence._generate_answer_llm", return_value="LLM answer"):
        result = fi.resolve("A completely novel bespoke question 12345?")

    assert result.tier == 4
    mock_cache.find_similar.assert_called_once()


def test_resolve_uses_semantic_cache_on_hit() -> None:
    """When cache returns a result, uses Tier 2."""
    mock_cache = MagicMock()
    mock_cache.find_similar.return_value = ("Cached answer", 0.92)
    fi = FormIntelligence(semantic_cache=mock_cache)

    result = fi.resolve("A completely novel bespoke question 12345?")

    assert result.tier == 2
    assert result.tier_name == "semantic_cache"
    assert result.answer == "Cached answer"
    assert result.confidence == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# LLM answer stored in cache
# ---------------------------------------------------------------------------


def test_llm_answer_stored_in_cache() -> None:
    """After LLM generates an answer, it is stored in the semantic cache."""
    mock_cache = MagicMock()
    mock_cache.find_similar.return_value = None
    fi = FormIntelligence(semantic_cache=mock_cache)

    with patch("jobpulse.form_intelligence._generate_answer_llm", return_value="LLM answer"):
        fi.resolve("A completely novel bespoke question 12345?")

    mock_cache.store.assert_called_once_with(
        "A completely novel bespoke question 12345?", "LLM answer"
    )


# ---------------------------------------------------------------------------
# _generate_answer_llm wrapper
# ---------------------------------------------------------------------------


def test_generate_answer_llm_wrapper_delegates() -> None:
    """_generate_answer_llm delegates to _generate_answer."""
    with patch(
        "jobpulse.form_intelligence._generate_answer", return_value="delegated"
    ) as mock_gen:
        result = _generate_answer_llm("some question", {"job_title": "Engineer"})

    mock_gen.assert_called_once_with("some question", {"job_title": "Engineer"})
    assert result == "delegated"
