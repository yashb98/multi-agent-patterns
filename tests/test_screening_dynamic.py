"""Tests for dynamic screening answer features — skill lookup, role salary, previously applied."""

from __future__ import annotations

from jobpulse.screening_answers import (
    ROLE_SALARY,
    SKILL_EXPERIENCE,
    _extract_skill_from_question,
    _resolve_role_salary,
    _resolve_skill_experience,
)


# ------------------------------------------------------------------
# Skill experience extraction
# ------------------------------------------------------------------

def test_extract_skill_python():
    q = "How many years of experience do you have with Python?"
    assert _extract_skill_from_question(q) == "python"


def test_extract_skill_machine_learning():
    q = "How many years of experience do you have in machine learning?"
    assert _extract_skill_from_question(q) == "machine learning"


def test_extract_skill_generic():
    q = "How many years of relevant experience do you have?"
    assert _extract_skill_from_question(q) is None


def test_extract_skill_sql():
    q = "How many years of experience do you have with SQL?"
    assert _extract_skill_from_question(q) == "sql"


def test_extract_skill_docker():
    q = "How many years of work experience do you have with Docker?"
    assert _extract_skill_from_question(q) == "docker"


# ------------------------------------------------------------------
# Skill experience resolution
# ------------------------------------------------------------------

def test_resolve_skill_python():
    assert _resolve_skill_experience("python", input_type=None) == "3"


def test_resolve_skill_ml():
    assert _resolve_skill_experience("machine learning", input_type=None) == "2"


def test_resolve_skill_unknown_defaults_to_2():
    assert _resolve_skill_experience("fortran", input_type=None) == "2"


def test_resolve_skill_none_defaults_to_2():
    assert _resolve_skill_experience(None, input_type=None) == "2"


def test_resolve_skill_number_field():
    assert _resolve_skill_experience("python", input_type="number") == "3"


def test_resolve_skill_text_field():
    result = _resolve_skill_experience("python", input_type="text")
    assert result == "3"


# ------------------------------------------------------------------
# Role salary resolution
# ------------------------------------------------------------------

def test_role_salary_data_scientist():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    assert _resolve_role_salary(ctx, input_type="number") == "32000"


def test_role_salary_data_analyst():
    ctx = {"job_title": "Data Analyst", "company": "Deloitte"}
    assert _resolve_role_salary(ctx, input_type="number") == "28000"


def test_role_salary_ml_engineer():
    ctx = {"job_title": "Machine Learning Engineer", "company": "Google"}
    assert _resolve_role_salary(ctx, input_type="number") == "32000"


def test_role_salary_default():
    ctx = {"job_title": "Unknown Role", "company": "Unknown"}
    assert _resolve_role_salary(ctx, input_type="number") == "28000"


def test_role_salary_none_context():
    assert _resolve_role_salary(None, input_type="number") == "28000"


def test_role_salary_text_field_data_scientist():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    result = _resolve_role_salary(ctx, input_type="text")
    assert "30,000" in result or "30000" in result


def test_role_salary_text_field_default():
    ctx = {"job_title": "Unknown Role", "company": "Unknown"}
    result = _resolve_role_salary(ctx, input_type="text")
    assert "26,000" in result or "26000" in result
