from pathlib import Path
import pytest
from research_journal.verifier import check_has_repo, _RepoCache


def test_passes_when_repo_active(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: {"stars": 320, "last_commit_iso": "2026-04-25T10:00:00Z"},
    )
    ok, reason, last_commit = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is True
    assert "320 stars" in reason


def test_fails_low_stars(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: {"stars": 3, "last_commit_iso": "2026-04-25T10:00:00Z"},
    )
    ok, reason, _ = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is False


def test_cache_hit_does_not_call_github(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    cache.set("https://github.com/x/y", {"stars": 100, "last_commit_iso": "2026-04-25T10:00:00Z"})
    calls = []
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: calls.append(url) or {},
    )
    check_has_repo("https://github.com/x/y", cache=cache)
    assert calls == []  # cache hit, no API call


def test_no_url_returns_false(tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    ok, reason, _ = check_has_repo("", cache=cache)
    assert ok is False


def test_api_failure_returns_unknown(monkeypatch, tmp_path):
    cache = _RepoCache(db_path=tmp_path / "github_cache.db")
    monkeypatch.setattr(
        "research_journal.verifier._fetch_github_repo_meta",
        lambda url: (_ for _ in ()).throw(RuntimeError("rate limit")),
    )
    ok, reason, _ = check_has_repo("https://github.com/x/y", cache=cache)
    assert ok is None
