"""Tests for jobpulse.skill_extractor — rule-based + LLM fallback skill extraction."""

import json
import sqlite3
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


# --- Boilerplate / false positive filtering ---


class TestBoilerplateFiltering:
    def test_show_less_not_extracted(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        jd = "## Requirements\nPython developer\n\nShow less"
        result = extract_skills_rule_based(jd)
        all_skills = [s.lower() for s in result["required_skills"]]
        assert "less" not in all_skills

    def test_false_positive_skills_excluded(self, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        jd = "## Requirements\nWhat you'll do: build great software with Python"
        result = extract_skills_rule_based(jd)
        all_skills = [s.lower() for s in result["required_skills"]]
        assert "do" not in all_skills
        assert "go" not in all_skills


# --- Learning loop ---


class TestLearningLoop:
    def test_record_and_load(self, tmp_path):
        from jobpulse.skill_extractor import (
            record_extraction, _load_learned_noise, _init_learning_db,
        )

        db = str(tmp_path / "test_learning.db")
        _init_learning_db(db)
        record_extraction(["Python", "SQL", "Docker"], "Spotify", "Data Scientist", db)
        noise = _load_learned_noise(db)
        assert isinstance(noise, set)

    def test_compute_noise_not_enough_data(self, tmp_path):
        from jobpulse.skill_extractor import (
            record_extraction, compute_noise_skills, _init_learning_db,
        )

        db = str(tmp_path / "test_learning.db")
        _init_learning_db(db)
        record_extraction(["Python", "SQL"], "Spotify", "Data Scientist", db)
        record_extraction(["Python", "React"], "Google", "SWE", db)
        result = compute_noise_skills(min_companies=5, db_path=db)
        assert result == []

    def test_compute_noise_flags_ubiquitous_skill(self, tmp_path):
        from jobpulse.skill_extractor import (
            record_extraction, compute_noise_skills, _load_learned_noise, _init_learning_db,
        )

        db = str(tmp_path / "test_learning.db")
        _init_learning_db(db)
        companies = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
        for co in companies:
            record_extraction(["Python", "Communication"], co, "SWE", db)
        record_extraction(["React"], "alpha", "SWE", db)

        result = compute_noise_skills(min_companies=5, min_frequency=0.80, db_path=db)
        noise_skills = {r["skill"] for r in result}
        assert "python" in noise_skills
        assert "communication" in noise_skills
        assert "react" not in noise_skills

        loaded = _load_learned_noise(db)
        assert "python" in loaded
        assert "communication" in loaded

    def test_load_learned_noise_no_db(self, tmp_path):
        from jobpulse.skill_extractor import _load_learned_noise

        noise = _load_learned_noise(str(tmp_path / "nonexistent.db"))
        assert noise == set()

    def test_noise_skills_filtered_from_extraction(self, tmp_path, synonyms_file):
        from jobpulse.skill_extractor import extract_skills_rule_based

        db = str(tmp_path / "test_learning.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE noise_skills ("
            "  skill TEXT PRIMARY KEY, frequency REAL, "
            "  distinct_companies INTEGER, total_jds INTEGER, flagged_at TEXT)"
        )
        conn.execute(
            "INSERT INTO noise_skills VALUES ('python', 0.95, 10, 50, '2026-04-18')"
        )
        conn.commit()
        conn.close()

        with patch("jobpulse.skill_extractor._LEARNING_DB_PATH", db):
            result = extract_skills_rule_based(EXPLICIT_JD)
            req = [s.lower() for s in result["required_skills"]]
            assert "python" not in req
            assert "react" in req
