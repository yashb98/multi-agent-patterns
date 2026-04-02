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
# Work authorization
# ------------------------------------------------------------------

def test_authorization_yes():
    assert get_answer("Are you authorized to work in the UK?") == "Yes"
    assert get_answer("Do you have the right to work in the United Kingdom?") == "Yes"
    assert get_answer("Are you legally allowed to work in the UK?") == "Yes"


def test_right_to_work_type():
    assert get_answer("What is your Right to Work Type?") == "Graduate Visa"
    assert get_answer("What type of visa do you currently hold?") == "Graduate Visa"


def test_sponsorship_no():
    assert get_answer("Do you require visa sponsorship?") == "No"
    assert get_answer("Will you need sponsorship to work in the UK?") == "No"


def test_visa_status():
    answer = get_answer("What is your current visa status?")
    assert "Graduate Visa" in answer
    assert "2026" in answer


# ------------------------------------------------------------------
# Salary
# ------------------------------------------------------------------

def test_current_salary():
    assert get_answer("What is your current salary?") == "22000"


def test_expected_salary_numeric():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    assert get_answer("What is your expected salary?", ctx, input_type="number") == "32000"


def test_expected_salary_default_numeric():
    assert get_answer("What is your expected salary?", input_type="number") == "28000"


# ------------------------------------------------------------------
# Notice period & employment
# ------------------------------------------------------------------

def test_notice_period():
    assert get_answer("When can you start?") == "Immediately"
    assert get_answer("What is your notice period?") == "Immediately"


def test_notice_period_date_field():
    answer = get_answer("When can you start?", input_type="date")
    # Should be a YYYY-MM-DD date, not "Immediately"
    assert len(answer) == 10
    assert answer[4] == "-"


def test_currently_employed():
    assert get_answer("Are you currently employed?") == "Yes"


def test_current_job_title():
    assert get_answer("What is your current job title?") == "Team Leader"


def test_current_employer():
    assert get_answer("Who is your current employer?") == "Co-op"


# ------------------------------------------------------------------
# Location
# ------------------------------------------------------------------

def test_location_with_context():
    ctx = {"location": "London, England, United Kingdom"}
    assert get_answer("What is your current location?", ctx) == "London, England, United Kingdom"


def test_location_without_context():
    assert get_answer("Where are you based?") == "London, UK"


# ------------------------------------------------------------------
# Remote / hybrid / on-site
# ------------------------------------------------------------------

def test_remote_yes():
    assert get_answer("Are you willing to work remote?") == "Yes"
    assert get_answer("Are you open to remote work?") == "Yes"


def test_onsite_yes():
    assert get_answer("Are you willing to work on-site?") == "Yes"
    assert get_answer("Can you work in the office?") == "Yes"


def test_hybrid_yes():
    assert get_answer("Are you comfortable with a hybrid work arrangement?") == "Yes"


# ------------------------------------------------------------------
# Experience
# ------------------------------------------------------------------

def test_experience_skill_python():
    assert get_answer("How many years of experience do you have with Python?") == "3"


def test_experience_skill_ml():
    assert get_answer("How many years of experience do you have in machine learning?") == "2"


def test_experience_generic():
    answer = get_answer("How many years of experience do you have?")
    assert answer == "2"


# ------------------------------------------------------------------
# Education
# ------------------------------------------------------------------

def test_highest_education():
    assert get_answer("What is your highest level of education?") == "Master's Degree"


def test_degree_subject():
    assert get_answer("What is your degree subject?") == "MSc Computer Science"


def test_currently_studying():
    assert get_answer("Are you currently enrolled in an educational programme?") == "No"


def test_stem_degree():
    assert get_answer("Do you have a STEM degree?") == "Yes"


# ------------------------------------------------------------------
# Languages
# ------------------------------------------------------------------

def test_english_proficiency():
    assert get_answer("What is your proficiency in English?") == "Native or bilingual"


def test_hindi_proficiency():
    assert get_answer("What is your proficiency in Hindi?") == "Native or bilingual"


def test_languages_spoken():
    answer = get_answer("What languages do you speak?")
    assert "English" in answer
    assert "Hindi" in answer


# ------------------------------------------------------------------
# Driving, travel, employment type
# ------------------------------------------------------------------

def test_driving_licence():
    assert get_answer("Do you have a valid UK driving licence?") == "Yes"


def test_travel_willingness():
    assert get_answer("Are you willing to travel for work?") == "Yes"


def test_employment_type():
    assert get_answer("What is your preferred employment type?") == "Full-time"


# ------------------------------------------------------------------
# Background & security
# ------------------------------------------------------------------

def test_background_check():
    assert get_answer("Are you willing to undergo a background check?") == "Yes"
    assert get_answer("Do you have any unspent criminal convictions?") == "Yes"


def test_security_clearance():
    assert get_answer("What level of security clearance do you hold?") == "None"


# ------------------------------------------------------------------
# Diversity
# ------------------------------------------------------------------

def test_gender():
    assert get_answer("What is your gender?") == "Male"


def test_ethnicity():
    answer = get_answer("What is your ethnicity?")
    assert "Asian" in answer or "Indian" in answer


def test_ethnicity_does_not_match_location():
    """Regression: 'ethnicity' must NOT match location pattern (city substring)."""
    answer = get_answer("What is your ethnicity?")
    assert "London" not in answer


def test_orientation():
    assert get_answer("What is your sexual orientation?") == "Heterosexual/Straight"


def test_disability():
    assert get_answer("Do you have a disability?") == "No"


def test_veteran():
    assert get_answer("Are you a veteran?") == "No"


def test_religion():
    assert get_answer("What is your religion?") == "Hindu"


def test_marital_status():
    assert get_answer("What is your marital status?") == "Single"


def test_pronouns():
    assert get_answer("What are your preferred pronouns?") == "He/Him"


def test_over_18():
    assert get_answer("Are you over 18?") == "Yes"


# ------------------------------------------------------------------
# Other patterns
# ------------------------------------------------------------------

def test_consent():
    assert get_answer("I consent to having my data processed") == "Yes"


def test_nationality():
    assert get_answer("What is your nationality?") == "Indian"


def test_management_experience():
    assert get_answer("Do you have management experience?") == "Yes"


def test_direct_reports():
    assert get_answer("How many direct reports have you managed?") == "8"


def test_referral_no():
    assert get_answer("Were you referred by an employee?") == "No"


def test_uk_resident():
    assert get_answer("Are you based in the UK?") == "No"


def test_platform_source_linkedin():
    answer = get_answer("How did you hear about this job?", platform="linkedin")
    assert answer == "LinkedIn"


def test_platform_source_default():
    answer = get_answer("How did you hear about this job?")
    assert answer == "Job board"


def test_proficiency_rating():
    assert get_answer("Rate your proficiency level") == "4"


# ------------------------------------------------------------------
# Cache tests
# ------------------------------------------------------------------

def test_unknown_question_falls_to_cache():
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = "Cached response"
    answer = get_answer("What is your favourite colour?", db=mock_db)
    mock_db.get_cached_answer.assert_called_once()
    assert answer == "Cached response"


def test_cache_stores_and_retrieves(tmp_path):
    db = JobDB(db_path=tmp_path / "test_answers.db")
    assert get_cached_answer("What IDE do you use?", db=db) is None
    cache_answer("What IDE do you use?", "VS Code and Neovim", db=db)
    result = get_cached_answer("What IDE do you use?", db=db)
    assert result == "VS Code and Neovim"


def test_cache_increments_times_used(tmp_path):
    db = JobDB(db_path=tmp_path / "test_answers.db")
    cache_answer("Niche question?", "Niche answer", db=db)
    answer = get_answer("Niche question?", db=db)
    assert answer == "Niche answer"


# ------------------------------------------------------------------
# LLM fallback
# ------------------------------------------------------------------

@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_none_pattern(mock_gen):
    mock_gen.return_value = "I am a motivated software engineer..."
    answer = get_answer("Tell me about yourself")
    mock_gen.assert_called_once()
    assert "motivated" in answer


@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_unknown_question(mock_gen):
    mock_gen.return_value = "Generated answer"
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = None
    answer = get_answer("Explain quantum computing in one sentence", db=mock_db)
    mock_gen.assert_called_once()
    mock_db.cache_answer.assert_called_once()
    assert answer == "Generated answer"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

def test_empty_question():
    assert get_answer("") == ""
    assert get_answer("   ") == ""
