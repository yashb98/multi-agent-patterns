"""Tests for Gate 4: JD quality, company background, CV scrutiny, LLM review."""

import json
import pytest
from unittest.mock import patch

from jobpulse.gate4_quality import check_jd_quality, check_company_background


# --- Phase A: check_jd_quality ---


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


# --- Phase B: CV Scrutiny ---


class TestCVDeterministicScrutiny:
    """Test B1: deterministic CV quality checks."""

    def test_cv_with_metrics_passes(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "PROJECTS\n"
            "• Built system processing 500+ requests/day\n"
            "• Reduced API costs by 96% from $5.63 to $0.23/month\n"
            "• Deployed to 3 environments with 99.9% uptime\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.status in ("clean", "acceptable")

    def test_conversational_text_detected(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "I worked on building a REST API.\n"
            "I was responsible for the database design.\n"
            "My role was to implement authentication.\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.conversational_count > 0

    def test_too_long_cv_error(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = "A" * 5000
        result = scrutinize_cv_deterministic(cv_text)
        assert result.has_error is True
        assert result.status == "needs_fix"

    def test_clean_professional_cv(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "TECHNICAL SKILLS\n"
            "Python FastAPI Docker AWS\n"
            "PROJECTS\n"
            "• Reduced LLM costs by 96% ($5.63 to $0.23/month) via hybrid skill extraction\n"
            "• Built 4-gate pre-screen achieving 92%+ skill match threshold\n"
            "EXPERIENCE\n"
            "• Managed team of 8, increased efficiency by 25%\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.status == "clean"
        assert result.has_error is False

    def test_informal_words_detected(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = "Built really nice stuff for the team. Just helped with various things.\n"
        result = scrutinize_cv_deterministic(cv_text)
        assert result.informal_count > 0


class TestLLMFAANGScrutiny:
    """Test B2: LLM-based FAANG recruiter review."""

    @patch("openai.OpenAI")
    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_high_score_shortlist(self, mock_llm, mock_client_cls):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = json.dumps({
            "total_score": 8, "relevance": 3, "evidence": 3,
            "presentation": 1, "standout": 1,
            "strengths": ["Strong projects"], "weaknesses": ["Minor"],
            "verdict": "shortlist",
        })
        result = scrutinize_cv_llm("cv", "SWE", "Google", ["python"], ["docker"])
        assert result.score == 8
        assert result.verdict == "shortlist"
        assert result.needs_review is False

    @patch("openai.OpenAI")
    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_low_score_flags_review(self, mock_llm, mock_client_cls):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = json.dumps({
            "total_score": 5, "relevance": 2, "evidence": 1,
            "presentation": 1, "standout": 1,
            "strengths": ["OK stack"], "weaknesses": ["No metrics", "Generic"],
            "verdict": "maybe",
        })
        result = scrutinize_cv_llm("cv", "SWE", "Meta", ["python"], [])
        assert result.score == 5
        assert result.needs_review is True

    @patch("openai.OpenAI")
    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_handles_none_response(self, mock_llm, mock_client_cls):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = None
        result = scrutinize_cv_llm("cv", "SWE", "Google", ["python"], [])
        assert result.score == 0
        assert result.needs_review is True

    @patch("openai.OpenAI")
    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_handles_invalid_json(self, mock_llm, mock_client_cls):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = "not json"
        result = scrutinize_cv_llm("cv", "SWE", "Google", ["python"], [])
        assert result.score == 0
        assert result.needs_review is True
