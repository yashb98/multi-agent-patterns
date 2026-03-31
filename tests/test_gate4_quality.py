"""Tests for Gate 4 Phase A: JD quality and company background checks."""

import pytest

from jobpulse.gate4_quality import (
    JDQualityResult,
    CompanyBackgroundResult,
    check_jd_quality,
    check_company_background,
)


# --- check_jd_quality ---


def test_short_jd_blocked():
    """JD with <200 chars is blocked as too short."""
    result = check_jd_quality("Short JD text", ["python", "react", "sql", "aws", "docker"])
    assert not result.passed
    assert "too short" in result.reason.lower()


def test_few_skills_blocked():
    """JD with <5 extracted skills is blocked as vague."""
    jd = "x" * 250  # Long enough text
    result = check_jd_quality(jd, ["python", "sql", "react", "aws"])
    assert not result.passed
    assert "vague" in result.reason.lower() or "skills" in result.reason.lower()
    assert result.skill_count == 4


def test_boilerplate_jd_blocked():
    """JD with 3+ boilerplate phrases AND <8 skills is blocked."""
    jd = (
        "We are looking for passionate individuals to join our dynamic team "
        "in a fast-paced environment. Competitive salary offered. "
        "Must know Python, SQL, React, Docker, and AWS. Apply now!"
    )
    # Pad to pass length check
    jd += " " * max(0, 201 - len(jd))
    skills = ["python", "sql", "react", "docker", "aws"]
    result = check_jd_quality(jd, skills)
    assert not result.passed
    assert "boilerplate" in result.reason.lower()
    assert result.boilerplate_count >= 3


def test_good_jd_passes():
    """JD with >200 chars and 9 skills passes."""
    jd = (
        "We are hiring a backend engineer to design and build scalable "
        "microservices for our fintech platform. You will work with event-driven "
        "architecture and deploy to Kubernetes clusters. Strong focus on testing "
        "and observability. Experience with distributed systems required."
    )
    skills = ["python", "fastapi", "kubernetes", "docker", "postgresql", "redis", "kafka", "grafana", "pytest"]
    result = check_jd_quality(jd, skills)
    assert result.passed
    assert result.skill_count == 9


def test_boilerplate_with_enough_skills_passes():
    """JD with boilerplate phrases but >=8 skills still passes."""
    jd = (
        "Join our dynamic team in a fast-paced environment with competitive salary. "
        "We need a senior engineer proficient in multiple technologies to build "
        "our next-generation platform with microservices and event-driven architecture."
    )
    jd += " " * max(0, 201 - len(jd))
    skills = ["python", "react", "docker", "kubernetes", "postgresql", "redis", "kafka", "terraform"]
    result = check_jd_quality(jd, skills)
    assert result.passed
    assert result.boilerplate_count >= 3
    assert result.skill_count == 8


# --- check_company_background ---


def test_generic_company_name():
    """Generic company names like 'Tech Solutions Ltd' are flagged."""
    result = check_company_background("Tech Solutions Ltd", [])
    assert result.is_generic


def test_real_company_name():
    """Real company names like 'Revolut' are not flagged as generic."""
    result = check_company_background("Revolut", [])
    assert not result.is_generic


def test_previously_applied():
    """Past application detected and date included in note."""
    past = [{"company": "Revolut", "date": "2026-03-15", "role": "Backend Engineer"}]
    result = check_company_background("Revolut", past)
    assert result.previously_applied
    assert "2026-03-15" in result.note


def test_no_past_application():
    """No past application returns previously_applied=False."""
    past = [{"company": "Monzo", "date": "2026-03-10", "role": "SRE"}]
    result = check_company_background("Revolut", past)
    assert not result.previously_applied
