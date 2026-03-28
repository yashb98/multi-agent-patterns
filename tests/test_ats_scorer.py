"""Tests for the deterministic ATS scorer.

Pure Python keyword matching + section detection + format checks — no LLM calls.
Tests written before implementation (TDD).
"""


from jobpulse.ats_scorer import score_ats
from jobpulse.models.application_models import ATSScore

# ---------------------------------------------------------------------------
# Test 1: Perfect score
# ---------------------------------------------------------------------------


def test_perfect_score():
    """CV with all keywords, all sections, good format >= 95."""
    jd_skills = ["python", "sql", "pytorch", "docker"]
    cv_text = """
    Education
    MSc Computer Science

    Experience
    Team Leader at Co-op

    Technical Skills
    Python, SQL, PyTorch, Docker

    Projects
    Velox AI, Cloud Sentinel
    """
    result = score_ats(jd_skills, cv_text)
    assert isinstance(result, ATSScore)
    assert result.total >= 95
    assert result.passed is True
    assert len(result.missing_keywords) == 0


# ---------------------------------------------------------------------------
# Test 2: Missing keywords
# ---------------------------------------------------------------------------


def test_missing_keywords():
    """CV missing some keywords scores lower, missing keywords listed correctly."""
    jd_skills = ["python", "sql", "pytorch", "docker", "kubernetes", "spark"]
    cv_text = """
    Education\nMSc\nExperience\nTeam Leader\nTechnical Skills\nPython, SQL\nProjects\nMy project
    """
    result = score_ats(jd_skills, cv_text)
    assert result.total < 95
    assert result.passed is False
    assert "pytorch" in result.missing_keywords
    assert "python" in result.matched_keywords


# ---------------------------------------------------------------------------
# Test 3: Synonym matching
# ---------------------------------------------------------------------------


def test_synonym_matching():
    """k8s matches kubernetes, ML matches machine learning."""
    jd_skills = ["kubernetes", "machine learning"]
    cv_text = """
    Education\nMSc\nExperience\nEngineer\nSkills\nK8s, ML, Docker\nProjects\nMy project
    """
    result = score_ats(jd_skills, cv_text)
    assert "kubernetes" in result.matched_keywords
    assert "machine learning" in result.matched_keywords


# ---------------------------------------------------------------------------
# Test 4: Section scoring
# ---------------------------------------------------------------------------


def test_section_scoring():
    """Missing sections reduce section score below maximum."""
    jd_skills = ["python"]
    cv_text = """Education\nMSc\nTechnical Skills\nPython"""  # Missing Experience and Projects
    result = score_ats(jd_skills, cv_text)
    assert result.section_score < 20


# ---------------------------------------------------------------------------
# Test 5: Empty CV
# ---------------------------------------------------------------------------


def test_empty_cv():
    """Empty CV scores 0 total and fails."""
    result = score_ats(["python"], "")
    assert result.total == 0
    assert result.passed is False


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


def test_return_type_is_ats_score():
    """score_ats always returns an ATSScore instance."""
    result = score_ats(["python"], "Python developer with experience")
    assert isinstance(result, ATSScore)


def test_score_components_sum_to_total():
    """keyword_score + section_score + format_score == total (within floating point)."""
    jd_skills = ["python", "sql"]
    cv_text = "Education\nExperience\nSkills\nPython SQL\nProjects"
    result = score_ats(jd_skills, cv_text)
    assert abs(result.keyword_score + result.section_score + result.format_score - result.total) < 1e-6


def test_keyword_score_max_70():
    """keyword_score never exceeds 70."""
    jd_skills = ["python"]
    cv_text = "Education\nExperience\nSkills\nPython SQL Docker\nProjects"
    result = score_ats(jd_skills, cv_text)
    assert result.keyword_score <= 70


def test_section_score_max_20():
    """section_score never exceeds 20."""
    jd_skills = ["python"]
    cv_text = "Education\nExperience\nSkills\nPython\nProjects"
    result = score_ats(jd_skills, cv_text)
    assert result.section_score <= 20


def test_format_score_max_10():
    """format_score never exceeds 10."""
    jd_skills = ["python"]
    cv_text = "Education\nExperience\nSkills\nPython\nProjects"
    result = score_ats(jd_skills, cv_text)
    assert result.format_score <= 10


def test_empty_jd_skills_no_match():
    """Empty JD skills list results in 0 keyword score."""
    cv_text = "Education\nExperience\nSkills\nPython SQL\nProjects"
    result = score_ats([], cv_text)
    assert result.keyword_score == 0
    assert result.matched_keywords == []
    assert result.missing_keywords == []


def test_case_insensitive_matching():
    """Keyword matching is case-insensitive."""
    jd_skills = ["Python", "SQL"]
    cv_text = "Education\nExperience\nSkills\npython sql\nProjects"
    result = score_ats(jd_skills, cv_text)
    assert len(result.matched_keywords) == 2


def test_passed_auto_derived():
    """The passed field is always derived from total >= 95, not caller-supplied."""
    # A partial CV that won't hit 95
    result = score_ats(["python", "sql", "docker"], "Python developer")
    # passed must equal total >= 95 regardless
    assert result.passed == (result.total >= 95)
