"""Tests for the CV tailor module.

No LLM calls, no xelatex needed.
Tests written before implementation (TDD).
"""

from jobpulse.cv_tailor import (
    build_cv_prompt,
    determine_match_tier,
    extract_text_from_tex,
)


# ---------------------------------------------------------------------------
# Test 1: build_cv_prompt
# ---------------------------------------------------------------------------


def test_build_cv_prompt():
    """build_cv_prompt inserts JD data into the resume prompt template."""
    jd_data = {
        "location": "London",
        "role_title": "Data Scientist",
        "years_exp": "2+",
        "industry": "FinTech",
        "sub_context": "fraud detection",
        "skills_list": ["python", "sql", "pytorch", "pandas"],
        "soft_skills": ["communication", "teamwork"],
        "extended_skills": ["NLP/LLMs"],
    }
    matched_projects = ["90-Days-ML", "Cloud-Sentinel", "Velox_AI"]
    prompt = build_cv_prompt(jd_data, matched_projects)
    assert "London" in prompt
    assert "Data Scientist" in prompt
    assert "FinTech" in prompt
    assert "python" in prompt
    assert "90-Days-ML" in prompt


# ---------------------------------------------------------------------------
# Test 2: extract_text_from_tex
# ---------------------------------------------------------------------------


def test_extract_text_from_tex():
    """extract_text_from_tex strips LaTeX commands and returns plain text."""
    tex = r"""
    \section*{Technical Skills}
    \textbf{Languages:} Python | SQL | JavaScript
    \textbf{AI/ML:} PyTorch | TensorFlow
    \section*{Education}
    MSc Computer Science, University of Dundee
    """
    text = extract_text_from_tex(tex)
    assert "Python" in text
    assert "Technical Skills" in text
    assert "Education" in text
    assert r"\textbf" not in text


# ---------------------------------------------------------------------------
# Test 3: determine_match_tier
# ---------------------------------------------------------------------------


def test_determine_match_tier():
    assert determine_match_tier(95.0) == "auto"
    assert determine_match_tier(90.0) == "auto"
    assert determine_match_tier(89.0) == "review"
    assert determine_match_tier(82.0) == "review"
    assert determine_match_tier(81.9) == "skip"
    assert determine_match_tier(50.0) == "skip"
