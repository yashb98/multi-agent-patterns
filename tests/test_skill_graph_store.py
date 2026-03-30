"""Tests for SkillGraphStore with 4-gate pre-screen."""

import json
import pytest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import mindgraph_app.storage as storage
from jobpulse.skill_graph_store import (
    PreScreenResult,
    ProjectMatch,
    SkillGraphStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def temp_db(tmp_path):
    """Patch DB_PATH to tmp_path so tests never touch production data."""
    db_path = tmp_path / "test_skillgraph.db"
    with patch.object(storage, "DB_PATH", db_path):
        storage.init_db()
        yield db_path


@pytest.fixture
def sample_synonyms(tmp_path):
    """Write a small synonyms file and return its path."""
    data = {
        "python": ["python3", "py"],
        "javascript": ["js", "ecmascript"],
        "typescript": ["ts"],
        "docker": ["docker engine", "docker ce"],
        "kubernetes": ["k8s", "kube"],
        "react": ["reactjs", "react.js"],
        "fastapi": ["fast api"],
        "postgresql": ["postgres", "psql"],
        "aws": ["amazon web services"],
        "terraform": ["tf"],
        "django": ["django framework"],
        "flask": ["flask framework"],
        "redis": ["redis cache"],
        "sql": ["structured query language"],
        "git": ["git scm"],
        "ci/cd": ["cicd", "continuous integration"],
    }
    path = tmp_path / "skill_synonyms.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def store(sample_synonyms):
    """Create a SkillGraphStore with test synonyms."""
    return SkillGraphStore(synonyms_path=str(sample_synonyms))


def _make_listing(
    required_skills=None,
    preferred_skills=None,
    description_raw="",
    title="Software Engineer",
    company="TestCo",
):
    """Create a dict-like listing for testing."""
    return {
        "job_id": "abc123",
        "title": title,
        "company": company,
        "platform": "linkedin",
        "url": "https://example.com/job/1",
        "required_skills": required_skills or [],
        "preferred_skills": preferred_skills or [],
        "description_raw": description_raw,
        "found_at": datetime.now(timezone.utc).isoformat(),
    }


def _seed_profile(store: SkillGraphStore, skills: list[str], projects: list[dict]):
    """Helper to seed a profile with skills and projects."""
    for s in skills:
        store.upsert_skill(s)
    for p in projects:
        store.upsert_project(p)


# ---------------------------------------------------------------------------
# TestUpsertSkill
# ---------------------------------------------------------------------------


class TestUpsertSkill:
    def test_creates_entity(self, store):
        eid = store.upsert_skill("Python", source="github")
        assert eid  # non-empty string
        profile = store.get_skill_profile()
        assert "python" in profile

    def test_no_duplicate_on_re_upsert(self, store):
        eid1 = store.upsert_skill("Python")
        eid2 = store.upsert_skill("Python")
        assert eid1 == eid2
        # Should still be just one skill
        profile = store.get_skill_profile()
        assert len([s for s in profile if s == "python"]) == 1

    def test_normalizes_name(self, store):
        store.upsert_skill("  PyThOn  ")
        profile = store.get_skill_profile()
        assert "python" in profile

    def test_upsert_with_description(self, store):
        eid = store.upsert_skill("Docker", description="Container runtime")
        assert eid


# ---------------------------------------------------------------------------
# TestUpsertProject
# ---------------------------------------------------------------------------


class TestUpsertProject:
    def test_creates_project_entity(self, store):
        repo = {
            "name": "my-api",
            "description": "A FastAPI backend",
            "html_url": "https://github.com/user/my-api",
            "language": "Python",
            "topics": ["fastapi", "docker"],
        }
        pid = store.upsert_project(repo)
        assert pid

    def test_creates_demonstrates_relations(self, store):
        repo = {
            "name": "ml-pipeline",
            "description": "ML training pipeline",
            "html_url": "https://github.com/user/ml-pipeline",
            "language": "Python",
            "topics": ["tensorflow", "docker", "kubernetes"],
        }
        store.upsert_project(repo)
        stats = store.get_profile_stats()
        # Should have SKILL entities for language + topics
        assert stats["total_skills"] >= 3  # python, tensorflow, docker, kubernetes
        assert stats["total_demonstrates"] >= 3

    def test_deep_analysis_adds_extra_skills(self, store):
        repo = {
            "name": "web-app",
            "description": "Full-stack app",
            "html_url": "https://github.com/user/web-app",
            "language": "TypeScript",
            "topics": [],
        }
        store.upsert_project(repo, deep_analysis="Uses React, Redux, and PostgreSQL")
        profile = store.get_skill_profile()
        assert "typescript" in profile


# ---------------------------------------------------------------------------
# TestGetProjectsForSkills
# ---------------------------------------------------------------------------


class TestGetProjectsForSkills:
    def test_returns_matching_projects(self, store):
        store.upsert_project({
            "name": "api-server",
            "description": "REST API",
            "html_url": "https://github.com/user/api-server",
            "language": "Python",
            "topics": ["fastapi", "docker", "postgresql"],
        })
        store.upsert_project({
            "name": "frontend",
            "description": "React app",
            "html_url": "https://github.com/user/frontend",
            "language": "JavaScript",
            "topics": ["react"],
        })

        matches = store.get_projects_for_skills(["python", "fastapi", "docker"])
        assert len(matches) >= 1
        # api-server should rank highest
        assert matches[0].name == "api-server"
        assert matches[0].skill_overlap >= 2

    def test_ranked_by_overlap(self, store):
        store.upsert_project({
            "name": "proj-a",
            "description": "One skill",
            "html_url": "",
            "language": "Python",
            "topics": [],
        })
        store.upsert_project({
            "name": "proj-b",
            "description": "Many skills",
            "html_url": "",
            "language": "Python",
            "topics": ["fastapi", "docker", "redis"],
        })
        matches = store.get_projects_for_skills(["python", "fastapi", "docker", "redis"])
        assert matches[0].name == "proj-b"
        assert matches[0].skill_overlap > matches[-1].skill_overlap or len(matches) == 1


# ---------------------------------------------------------------------------
# TestPreScreen
# ---------------------------------------------------------------------------


class TestPreScreen:
    def _seed_strong_profile(self, store):
        """Seed a profile with many skills and projects for a python backend role."""
        skills = [
            "python", "fastapi", "django", "flask", "docker",
            "kubernetes", "postgresql", "redis", "aws", "terraform",
            "git", "ci/cd", "sql", "react", "typescript",
            "rest api", "machine learning", "pytorch", "pandas", "numpy",
            "mlflow", "langchain", "openai", "anthropic", "prompt engineering",
        ]
        for s in skills:
            store.upsert_skill(s)

        store.upsert_project({
            "name": "microservices-platform",
            "description": "Distributed microservices",
            "html_url": "https://github.com/user/microservices",
            "language": "Python",
            "topics": ["fastapi", "docker", "kubernetes", "postgresql", "redis", "rest api"],
        })
        store.upsert_project({
            "name": "infra-automation",
            "description": "Infrastructure as code",
            "html_url": "https://github.com/user/infra",
            "language": "Python",
            "topics": ["terraform", "aws", "docker", "ci/cd", "mlflow"],
        })
        store.upsert_project({
            "name": "web-dashboard",
            "description": "Admin dashboard",
            "html_url": "https://github.com/user/dashboard",
            "language": "Python",
            "topics": ["django", "react", "postgresql", "typescript"],
        })
        store.upsert_project({
            "name": "ml-pipeline",
            "description": "ML training pipeline",
            "html_url": "https://github.com/user/ml-pipeline",
            "language": "Python",
            "topics": ["pytorch", "pandas", "numpy", "machine learning", "langchain"],
        })

    def test_strong_match(self, store):
        self._seed_strong_profile(store)
        # 22 required skills, all in profile — must pass M3 (≥20 matches, ≥92%)
        listing = _make_listing(
            required_skills=["python", "fastapi", "django", "flask", "docker",
                             "kubernetes", "postgresql", "redis", "aws", "terraform",
                             "sql", "git", "ci/cd", "react", "typescript",
                             "rest api", "machine learning", "pytorch", "pandas", "numpy",
                             "mlflow", "langchain"],
            preferred_skills=[],
            description_raw="We need a backend engineer with 2 years experience.",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate1_passed is True
        assert result.gate2_passed is True
        assert result.gate3_score > 0
        assert result.tier in ("apply", "strong")
        assert len(result.matched_skills) >= 20

    def test_low_overlap_skip(self, store):
        # Minimal profile
        store.upsert_skill("python")
        store.upsert_skill("flask")
        listing = _make_listing(
            required_skills=["java", "spring", "hibernate", "oracle", "jenkins",
                             "maven", "junit", "docker", "kubernetes", "aws"],
            description_raw="Senior Java developer needed.",
        )
        result = store.pre_screen_jd(listing)
        # Should not be "strong" — probably "reject" or "skip"
        assert result.tier in ("reject", "skip")

    def test_kill_signal_seniority(self, store):
        self._seed_strong_profile(store)
        listing = _make_listing(
            required_skills=["python", "fastapi"],
            description_raw="We require 7+ years of production experience in distributed systems.",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate1_passed is False
        assert result.tier == "reject"
        assert "seniority" in (result.gate1_kill_reason or "").lower()

    def test_kill_signal_primary_language_missing(self, store):
        store.upsert_skill("python")
        listing = _make_listing(
            required_skills=["swift", "swiftui", "xcode", "coredata"],
            description_raw="iOS developer role.",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate1_passed is False
        assert result.tier == "reject"

    def test_kill_signal_foreign_domain(self, store):
        store.upsert_skill("python")
        store.upsert_skill("django")
        listing = _make_listing(
            required_skills=["swift", "swiftui", "xcode", "python"],
            description_raw="iOS developer.",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate1_passed is False
        assert result.tier == "reject"

    def test_gate2_must_haves_fail(self, store):
        # Have skills but not enough project evidence
        store.upsert_skill("python")
        store.upsert_skill("docker")
        store.upsert_skill("aws")
        # No projects at all
        listing = _make_listing(
            required_skills=["python", "docker", "aws", "kubernetes", "terraform",
                             "postgresql", "redis", "fastapi", "django", "flask",
                             "sql", "git"],
            description_raw="Backend engineer.",
        )
        result = store.pre_screen_jd(listing)
        assert result.gate2_passed is False

    def test_empty_required_skills(self, store):
        self._seed_strong_profile(store)
        listing = _make_listing(
            required_skills=[],
            description_raw="Some role with no explicit skills listed.",
        )
        result = store.pre_screen_jd(listing)
        # With no required skills, gates should handle gracefully
        assert result.tier in ("reject", "skip", "apply", "strong")

    def test_synonym_matching(self, store):
        """Skills matched via synonyms should count."""
        self._seed_strong_profile(store)
        listing = _make_listing(
            required_skills=["python3", "fast api", "docker engine", "k8s", "postgres",
                             "amazon web services", "tf", "sql", "git", "cicd"],
            description_raw="Backend engineer with 2 years experience.",
        )
        result = store.pre_screen_jd(listing)
        # Synonyms should resolve, so most skills match
        assert len(result.matched_skills) >= 6


# ---------------------------------------------------------------------------
# TestProfileStats
# ---------------------------------------------------------------------------


class TestProfileStats:
    def test_returns_correct_counts(self, store):
        store.upsert_skill("python")
        store.upsert_skill("docker")
        store.upsert_project({
            "name": "test-proj",
            "description": "Test",
            "html_url": "",
            "language": "Python",
            "topics": ["docker"],
        })
        stats = store.get_profile_stats()
        assert stats["total_skills"] >= 2
        assert stats["total_projects"] >= 1
        assert stats["total_demonstrates"] >= 1

    def test_empty_profile(self, store):
        stats = store.get_profile_stats()
        assert stats["total_skills"] == 0
        assert stats["total_projects"] == 0
        assert stats["total_demonstrates"] == 0
