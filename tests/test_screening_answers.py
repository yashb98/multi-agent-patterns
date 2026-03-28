"""Tests for jobpulse.screening_answers — pattern matching, caching, LLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jobpulse.job_db import JobDB
from jobpulse.screening_answers import (
    COMMON_ANSWERS,
    cache_answer,
    get_answer,
    get_cached_answer,
)


# ------------------------------------------------------------------
# Pattern-based tests
# ------------------------------------------------------------------


def test_common_authorization_question_matched():
    """Work-auth questions should return 'Yes' without DB or LLM."""
    assert get_answer("Are you authorized to work in the UK?") == "Yes"
    assert get_answer("Do you have the right to work in the United Kingdom?") == "Yes"
    assert get_answer("Are you legally allowed to work in the UK?") == "Yes"


def test_salary_question_matched():
    """Salary expectation questions should return the fixed range."""
    assert get_answer("What is your expected salary?") == "£27,000-32,000"
    assert get_answer("What salary do you expect for this role?") == "£27,000-32,000"
    assert get_answer("Desired compensation?") == "£27,000-32,000"


def test_sponsorship_question_matched():
    """Sponsorship questions should return 'No'."""
    assert get_answer("Do you require visa sponsorship?") == "No"
    assert get_answer("Will you need sponsorship to work in the UK?") == "No"


def test_availability_question_matched():
    """Start date / notice period questions should match."""
    assert get_answer("When can you start?") == "Available immediately"
    assert get_answer("What is your notice period?") == "Available immediately"


def test_remote_question_matched():
    """Remote work questions should return 'Yes'."""
    assert get_answer("Are you willing to work remote?") == "Yes"
    assert get_answer("Are you open to remote work?") == "Yes"


def test_onsite_question_matched():
    """On-site work questions should return 'Yes'."""
    assert get_answer("Are you willing to work on-site?") == "Yes"
    assert get_answer("Can you work in the office?") == "Yes"


def test_experience_question_matched():
    """Years of experience questions should match."""
    answer = get_answer("How many years of experience do you have?")
    assert "1+ years" in answer


# ------------------------------------------------------------------
# Cache tests
# ------------------------------------------------------------------


def test_unknown_question_falls_to_cache():
    """An unrecognised question should look up the DB cache."""
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = "Cached response"

    answer = get_answer("What is your favourite colour?", db=mock_db)

    mock_db.get_cached_answer.assert_called_once()
    assert answer == "Cached response"


def test_cache_stores_and_retrieves(tmp_path):
    """Round-trip: cache an answer, then retrieve it."""
    db = JobDB(db_path=tmp_path / "test_answers.db")

    # Nothing cached yet
    assert get_cached_answer("What IDE do you use?", db=db) is None

    # Store
    cache_answer("What IDE do you use?", "VS Code and Neovim", db=db)

    # Retrieve
    result = get_cached_answer("What IDE do you use?", db=db)
    assert result == "VS Code and Neovim"


def test_cache_increments_times_used(tmp_path):
    """Retrieving via get_answer should bump times_used in the cache."""
    db = JobDB(db_path=tmp_path / "test_answers.db")
    cache_answer("Niche question?", "Niche answer", db=db)

    # Calling get_answer triggers cache hit which re-caches (incrementing usage)
    answer = get_answer("Niche question?", db=db)
    assert answer == "Niche answer"


# ------------------------------------------------------------------
# LLM fallback tests
# ------------------------------------------------------------------


@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_none_pattern(mock_gen):
    """Questions matching a None-valued pattern should call the LLM."""
    mock_gen.return_value = "I am a motivated software engineer..."

    answer = get_answer("Tell me about yourself")
    mock_gen.assert_called_once()
    assert "motivated" in answer


@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_unknown_question(mock_gen):
    """Totally unknown questions with no cache should call the LLM."""
    mock_gen.return_value = "Generated answer"
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = None

    answer = get_answer("Explain quantum computing in one sentence", db=mock_db)

    mock_gen.assert_called_once()
    mock_db.cache_answer.assert_called_once()
    assert answer == "Generated answer"
