"""Tests for jobpulse/github_profile_sync.py.

All tests patch mindgraph_app.storage.DB_PATH to tmp_path so production
databases are never touched (per mistakes.md 2026-03-25 rule).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_REPOS = [
    {
        "name": "yashb98/multi-agent-patterns",
        "description": "Multi-agent orchestration system",
        "languages": ["python", "javascript"],
        "topics": ["ai", "agents"],
        "keywords": ["python", "javascript", "ai", "agents"],
        "url": "https://github.com/yashb98/multi-agent-patterns",
        "stars": 10,
    },
    {
        "name": "yashb98/DataMind",
        "description": "AI analytics platform",
        "languages": ["python", "typescript"],
        "topics": ["analytics", "kafka"],
        "keywords": ["python", "typescript", "analytics", "kafka"],
        "url": "https://github.com/yashb98/DataMind",
        "stars": 5,
    },
]


# ---------------------------------------------------------------------------
# Fixture: isolated DB
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect MindGraph DB to a temp file so production DB is never touched.

    DB_PATH is a Path object, so we patch with a Path (not a str).
    """
    db_file = tmp_path / "mindgraph_test.db"
    monkeypatch.setattr("mindgraph_app.storage.DB_PATH", db_file)
    # Re-initialize the schema against the temp DB
    import mindgraph_app.storage as storage

    storage.init_db()
    yield db_file


# ---------------------------------------------------------------------------
# Helper: fresh SkillGraphStore with no synonym file (avoids missing-file warning)
# ---------------------------------------------------------------------------


def _make_store():
    from jobpulse.skill_graph_store import SkillGraphStore

    # Pass a non-existent path so _load_synonyms returns {} silently
    return SkillGraphStore(synonyms_path="/nonexistent/skill_synonyms.json")


# ---------------------------------------------------------------------------
# Test 1: sync_repos_to_graph creates expected entities
# ---------------------------------------------------------------------------


def test_sync_repos_creates_projects_and_skills():
    """sync_repos_to_graph should create >= 2 projects and >= 4 skills."""
    from jobpulse.github_profile_sync import sync_repos_to_graph

    store = _make_store()
    sync_repos_to_graph(SAMPLE_REPOS, store)

    stats = store.get_profile_stats()
    assert stats["total_projects"] >= 2, f"Expected >= 2 projects, got {stats['total_projects']}"
    assert stats["total_skills"] >= 4, f"Expected >= 4 skills, got {stats['total_skills']}"


# ---------------------------------------------------------------------------
# Test 2: Idempotency — running twice yields the same entity count
# ---------------------------------------------------------------------------


def test_sync_repos_is_idempotent():
    """Running sync_repos_to_graph twice must not duplicate entities."""
    from jobpulse.github_profile_sync import sync_repos_to_graph

    store = _make_store()
    sync_repos_to_graph(SAMPLE_REPOS, store)
    stats_first = store.get_profile_stats()

    # Run again — entity counts must be identical
    sync_repos_to_graph(SAMPLE_REPOS, store)
    stats_second = store.get_profile_stats()

    assert stats_first["total_projects"] == stats_second["total_projects"], (
        "Project count changed on second run — not idempotent"
    )
    assert stats_first["total_skills"] == stats_second["total_skills"], (
        "Skill count changed on second run — not idempotent"
    )


# ---------------------------------------------------------------------------
# Test 3: sync_resume_skills extracts BASE_SKILLS; python must be in profile
# ---------------------------------------------------------------------------


def test_sync_resume_skills_extracts_base_skills():
    """sync_resume_skills should add skills from BASE_SKILLS; python must appear."""
    from jobpulse.github_profile_sync import sync_resume_skills

    store = _make_store()
    sync_resume_skills(store)

    profile = store.get_skill_profile()
    assert "python" in profile, f"'python' not found in skill profile: {profile}"

    # There should be a meaningful number of skills from the CV categories
    assert len(profile) >= 5, f"Expected >= 5 skills from resume, got {len(profile)}"


# ---------------------------------------------------------------------------
# Test 4: sync_profile runs end-to-end without error
# ---------------------------------------------------------------------------


def test_sync_profile_runs_without_error():
    """sync_profile should complete without raising even when GitHub API is mocked.

    fetch_and_cache_repos is imported inside sync_profile(), so we patch it at
    its source module (jobpulse.github_matcher).
    """
    from jobpulse.github_profile_sync import sync_profile

    with patch(
        "jobpulse.github_matcher.fetch_and_cache_repos",
        return_value=SAMPLE_REPOS,
    ):
        # Should not raise
        sync_profile()


def test_sync_profile_uses_mock_repos():
    """sync_profile with mocked repos should populate projects and skills."""
    from jobpulse.github_profile_sync import sync_profile

    with patch(
        "jobpulse.github_matcher.fetch_and_cache_repos",
        return_value=SAMPLE_REPOS,
    ):
        sync_profile()

    # Use a new store instance to read back from the shared (temp) DB
    store = _make_store()
    stats = store.get_profile_stats()
    assert stats["total_projects"] >= 2
    assert stats["total_skills"] >= 4


# ---------------------------------------------------------------------------
# Test 5: sync_past_applications handles missing JobDB gracefully
# ---------------------------------------------------------------------------


def test_sync_past_applications_handles_missing_db(monkeypatch: pytest.MonkeyPatch):
    """If JobDB is not importable, sync_past_applications logs info and returns cleanly."""
    import builtins
    real_import = builtins.__import__

    def _block_job_db(name, *args, **kwargs):
        if name == "jobpulse.job_db":
            raise ImportError("Simulated missing JobDB")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_job_db)

    from jobpulse.github_profile_sync import sync_past_applications

    store = _make_store()
    # Must not raise
    sync_past_applications(store)


# ---------------------------------------------------------------------------
# Test 6: sync_repos handles empty repo list gracefully
# ---------------------------------------------------------------------------


def test_sync_repos_empty_list():
    """sync_repos_to_graph with an empty list should not raise and leave DB empty."""
    from jobpulse.github_profile_sync import sync_repos_to_graph

    store = _make_store()
    sync_repos_to_graph([], store)  # Must not raise

    stats = store.get_profile_stats()
    assert stats["total_projects"] == 0
    assert stats["total_skills"] == 0


# ---------------------------------------------------------------------------
# Test 7: sync_repos correctly handles repos with no languages/topics
# ---------------------------------------------------------------------------


def test_sync_repos_handles_sparse_repo():
    """A repo with no languages and no topics should still create a PROJECT entity."""
    from jobpulse.github_profile_sync import sync_repos_to_graph

    sparse = [
        {
            "name": "yashb98/sparse-repo",
            "description": "",
            "languages": [],
            "topics": [],
            "keywords": [],
            "url": "",
            "stars": 0,
        }
    ]
    store = _make_store()
    sync_repos_to_graph(sparse, store)

    stats = store.get_profile_stats()
    assert stats["total_projects"] == 1
