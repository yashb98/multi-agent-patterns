"""Tests for jobpulse.skill_extractor — rule-based + LLM fallback skill extraction."""

import json
from unittest.mock import patch

import pytest


# --- Fixtures ---

SAMPLE_SYNONYMS = {
    "python": ["python3", "py", "cpython"],
    "react": ["reactjs", "react.js"],
    "typescript": ["ts", "type script"],
    "postgresql": ["postgres", "psql", "pg"],
    "docker": ["docker engine", "containerization"],
    "kubernetes": ["k8s", "kube"],
    "aws": ["amazon web services", "amazon aws"],
    "rest api": ["rest apis", "restful api", "restful apis"],
    "microservices": ["micro services", "micro-services"],
    "machine learning": ["ml", "machine-learning"],
    "fastapi": ["fast api", "fast-api"],
    "django": ["django framework"],
    "ci/cd": ["ci cd", "continuous integration", "continuous delivery"],
    "agile": ["agile methodology", "scrum", "agile/scrum"],
}

EXPLICIT_JD = """## Requirements
- 3+ years Python experience
- Strong knowledge of React and TypeScript
- Experience with PostgreSQL and Docker
- Familiarity with Kubernetes and AWS
- Understanding of REST APIs and microservices

## Nice to Have
- Machine Learning experience
- FastAPI or Django
- CI/CD pipelines
- Agile/Scrum methodology"""

VAGUE_JD = (
    "We're looking for someone passionate about building great products. "
    "Strong problem-solver with attention to detail."
)


@pytest.fixture()
def synonyms_file(tmp_path):
    """Create a small test synonyms file and patch SYNONYMS_PATH to use it."""
    path = tmp_path / "skill_synonyms.json"
    path.write_text(json.dumps(SAMPLE_SYNONYMS))
    with patch("jobpulse.skill_extractor.SYNONYMS_PATH", str(path)):
        yield path


# --- detect_jd_sections ---


class TestDetectJDSections:
    def test_finds_required_and_preferred_sections(self):
        from jobpulse.skill_extractor import detect_jd_sections

        sections = detect_jd_sections(EXPLICIT_JD)
        assert "required" in sections
        assert "preferred" in sections
        assert "Python" in sections["required"]
        assert "Machine Learning" in sections["preferred"]

    def test_handles_no_sections(self):
        from jobpulse.skill_extractor import detect_jd_sections

        sections = detect_jd_sections(VAGUE_JD)
        assert "unsectioned" in sections
        assert "required" not in sections
        assert "preferred" not in sections

    def test_alternative_header_names(self):
        from jobpulse.skill_extractor import detect_jd_sections

        jd = "## Essential\nPython\n\n## Desirable\nRust"
        sections = detect_jd_sections(jd)
        assert "required" in sections
        assert "Python" in sections["required"]
        assert "preferred" in sections
        assert "Rust" in sections["preferred"]

    def test_what_youll_need_header(self):
        from jobpulse.skill_extractor import detect_jd_sections

        jd = "## What you'll need\nJava experience\n\n## Bonus\nKotlin"
        sections = detect_jd_sections(jd)
        assert "required" in sections
        assert "Java" in sections["required"]
        assert "preferred" in sections
        assert "Kotlin" in sections["preferred"]


# --- extract_skills_rule_based ---


class TestExtractSkillsRuleBased:
    def test_extracts_explicit_skills(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        result = extract_skills_rule_based(EXPLICIT_JD)
        assert result["source"] == "rule_based"
        req = [s.lower() for s in result["required_skills"]]
        assert "python" in req
        assert "react" in req
        assert "typescript" in req
        assert "postgresql" in req
        assert "docker" in req

    def test_matches_synonyms(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        jd = "## Requirements\nExperience with k8s and containerization"
        result = extract_skills_rule_based(jd)
        req = [s.lower() for s in result["required_skills"]]
        assert "kubernetes" in req
        assert "docker" in req

    def test_vague_jd_extracts_few_skills(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        result = extract_skills_rule_based(VAGUE_JD)
        total = len(result["required_skills"]) + len(result["preferred_skills"])
        assert total < 10

    def test_preferred_skills_in_nice_to_have(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        result = extract_skills_rule_based(EXPLICIT_JD)
        pref = [s.lower() for s in result["preferred_skills"]]
        assert "machine learning" in pref
        assert "fastapi" in pref

    def test_industry_detection(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        jd = "## Requirements\nFintech startup needs Python developer"
        result = extract_skills_rule_based(jd)
        assert result["industry"].lower() == "fintech"


# --- extract_skills_hybrid ---


class TestExtractSkillsHybrid:
    def test_uses_rule_based_when_enough_skills(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_hybrid

        with patch("jobpulse.skill_extractor._extract_skills_llm") as mock_llm:
            result = extract_skills_hybrid(EXPLICIT_JD)
            mock_llm.assert_not_called()
            assert result["source"] == "rule_based"

    def test_falls_back_to_llm_when_few_skills(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_hybrid

        llm_result = {
            "required_skills": ["problem solving", "communication"],
            "preferred_skills": ["leadership"],
            "industry": "general",
            "sub_context": "",
            "source": "llm_fallback",
        }
        with patch(
            "jobpulse.skill_extractor._extract_skills_llm", return_value=llm_result
        ) as mock_llm:
            result = extract_skills_hybrid(VAGUE_JD)
            mock_llm.assert_called_once_with(VAGUE_JD)
            assert result["source"] == "llm_fallback"
