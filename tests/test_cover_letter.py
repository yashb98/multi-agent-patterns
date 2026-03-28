"""Tests for the cover letter generator module.

No LLM calls — tests cover prompt construction only.
Tests written before implementation (TDD).
"""

from jobpulse.cover_letter_agent import build_cover_letter_prompt

# ---------------------------------------------------------------------------
# Test 1: build_cover_letter_prompt includes required context
# ---------------------------------------------------------------------------


def test_build_cover_letter_prompt():
    """build_cover_letter_prompt includes company, role, skills, and projects."""
    prompt = build_cover_letter_prompt(
        company="Barclays",
        role="Data Scientist",
        jd_text="We need a data scientist with Python, SQL...",
        matched_skills=["python", "sql", "pytorch"],
        matched_projects=["Velox AI", "90 Days ML"],
    )
    assert "Barclays" in prompt
    assert "Data Scientist" in prompt
    assert "python" in prompt
    assert "Velox AI" in prompt


# ---------------------------------------------------------------------------
# Test 2: word count instruction is present in prompt
# ---------------------------------------------------------------------------


def test_cover_letter_word_count_instruction():
    """build_cover_letter_prompt contains 250 and 350 word count constraints."""
    prompt = build_cover_letter_prompt(
        company="X",
        role="Y",
        jd_text="Z",
        matched_skills=["python"],
        matched_projects=["P1"],
    )
    assert "250" in prompt
    assert "350" in prompt
