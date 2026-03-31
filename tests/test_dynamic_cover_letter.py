"""Tests for recruiter email extraction from job descriptions."""

from __future__ import annotations

from jobpulse.jd_analyzer import extract_recruiter_email


def test_extracts_recruiter_email():
    """Personal recruiter email is extracted from JD text."""
    jd = "Contact john.smith@google.com for details"
    assert extract_recruiter_email(jd) == "john.smith@google.com"


def test_skips_noreply():
    """noreply addresses are discarded entirely."""
    jd = "Send applications to noreply@company.com"
    assert extract_recruiter_email(jd) is None


def test_skips_info_email():
    """info@ addresses are discarded entirely."""
    jd = "For enquiries email info@company.com"
    assert extract_recruiter_email(jd) is None


def test_prefers_recruiter_over_generic():
    """Personal recruiter email is preferred over generic HR address."""
    jd = "Apply at careers@company.com or contact sarah@company.com directly"
    assert extract_recruiter_email(jd) == "sarah@company.com"


def test_returns_generic_hr_when_no_recruiter():
    """Generic HR email is returned when no personal recruiter email exists."""
    jd = "Send your CV to careers@company.com"
    assert extract_recruiter_email(jd) == "careers@company.com"


def test_no_email_returns_none():
    """Returns None when no email address is present."""
    jd = "No contact info here"
    assert extract_recruiter_email(jd) is None


def test_multiple_recruiters_returns_first():
    """When multiple recruiter emails exist, returns the first one found."""
    jd = "Reach out to john@co.com and jane@co.com for more info"
    assert extract_recruiter_email(jd) == "john@co.com"


# ---------------------------------------------------------------------------
# Dynamic cover letter point generation tests
# ---------------------------------------------------------------------------

from unittest.mock import patch

from jobpulse.cv_templates.generate_cover_letter import (
    build_dynamic_points,
    polish_points_llm,
)


_SAMPLE_PROJECTS = [
    {
        "title": "JobPulse",
        "url": "https://github.com/yashb98/multi-agent-patterns",
        "bullets": [
            "Built 10+ autonomous agents processing 96% fewer LLM calls",
            "Implemented Python FastAPI webhook server handling 500 req/s",
            "Docker sandboxed execution with 4-gate pre-screen pipeline",
        ],
    },
    {
        "title": "LetsBuild",
        "url": "https://github.com/yashb98/LetsBuild",
        "bullets": [
            "10-layer autonomous pipeline generating production repos",
            "Claude API tool_use for 100% structured output",
            "Docker sandbox with security gates and budget controls",
        ],
    },
    {
        "title": "MindGraph",
        "url": "https://github.com/yashb98/mindgraph",
        "bullets": [
            "GraphRAG retrieval with Three.js 3D visualization",
            "Entity extraction across 14 types with LLM + rule hybrid",
        ],
    },
    {
        "title": "BudgetTracker",
        "url": "https://github.com/yashb98/budget",
        "bullets": [
            "Natural language budget tracking with 17 categories",
            "Reduced manual entry time by 85% via NLP parsing",
        ],
    },
]

_REQUIRED_SKILLS = ["Python", "FastAPI", "Docker", "Claude API", "GraphRAG"]


def test_builds_points_from_projects():
    """4 projects with overlapping skills produce 4 points with skill headers."""
    points = build_dynamic_points(_SAMPLE_PROJECTS, _REQUIRED_SKILLS)
    assert len(points) == 4
    # First project overlaps Python, FastAPI, Docker
    header0 = points[0][0].lower()
    assert "python" in header0 or "fastapi" in header0 or "docker" in header0


def test_pads_to_4_when_fewer_projects():
    """2 projects still produces exactly 4 points (padded)."""
    points = build_dynamic_points(_SAMPLE_PROJECTS[:2], _REQUIRED_SKILLS)
    assert len(points) == 4


def test_uses_metric_bullet_when_available():
    """Bullet containing '96%' is preferred as detail text."""
    points = build_dynamic_points(_SAMPLE_PROJECTS[:1], _REQUIRED_SKILLS)
    # First project's metric bullet contains "96%"
    assert "96%" in points[0][1]


def test_empty_projects_returns_defaults():
    """Empty project list returns 4 generic/default points."""
    points = build_dynamic_points([], _REQUIRED_SKILLS)
    assert len(points) == 4
    # All should be the generic padding points
    assert "Education" in points[0][0] or "Certifications" in points[0][0]


def test_polish_returns_refined_points():
    """When LLM returns valid JSON, polished points are used."""
    original = [("Python:", "detail1"), ("Docker:", "detail2"),
                ("FastAPI:", "detail3"), ("GraphRAG:", "detail4")]
    polished_json = (
        '[{"header": "Python Expertise:", "detail": "detail1 refined"}, '
        '{"header": "Docker Mastery:", "detail": "detail2 refined"}, '
        '{"header": "FastAPI Proficiency:", "detail": "detail3 refined"}, '
        '{"header": "GraphRAG Skills:", "detail": "detail4 refined"}]'
    )
    with patch(
        "jobpulse.utils.safe_io.safe_openai_call",
        return_value=polished_json,
    ) as mock_call, patch("openai.OpenAI"):
        result = polish_points_llm(original, "SWE", "Acme", ["Python"])
    assert result[0][0] == "Python Expertise:"
    assert "refined" in result[0][1]


def test_polish_falls_back_on_failure():
    """When LLM returns None, original points are returned unchanged."""
    original = [("A:", "a"), ("B:", "b"), ("C:", "c"), ("D:", "d")]
    with patch(
        "jobpulse.utils.safe_io.safe_openai_call",
        return_value=None,
    ), patch("openai.OpenAI"):
        result = polish_points_llm(original, "SWE", "Acme", ["Python"])
    assert result == original
