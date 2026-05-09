"""Verification engine — composite badge of 5 checks."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from shared.external_verifiers import semantic_scholar_lookup, PEER_REVIEWED_VENUES
from shared.logging_config import get_logger

logger = get_logger(__name__)


def check_peer_reviewed(arxiv_id: str) -> tuple[Optional[bool], str]:
    """Returns (True/False/None, reason). None = S2 unavailable."""
    data = semantic_scholar_lookup(arxiv_id)
    if data is None:
        return None, "Semantic Scholar unavailable"
    if data.get("is_peer_reviewed"):
        return True, f"venue: {data.get('venue', 'unknown')}"
    venue = (data.get("venue") or "").lower()
    if any(v in venue for v in PEER_REVIEWED_VENUES):
        return True, f"venue: {data.get('venue')}"
    return False, f"venue '{data.get('venue', 'arXiv')}' not in PEER_REVIEWED_VENUES"


_CACHE_TTL_SECONDS = 24 * 3600


class _RepoCache:
    """SQLite-backed 24h cache for GitHub repo metadata."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            from jobpulse.config import DATA_DIR
            db_path = DATA_DIR / "github_cache.db"
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS repo_health "
                "(url TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at INTEGER NOT NULL)"
            )

    def get(self, url: str) -> dict | None:
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM repo_health WHERE url = ?", (url,)
            ).fetchone()
        if row is None:
            return None
        payload, fetched_at = row
        if time.time() - fetched_at > _CACHE_TTL_SECONDS:
            return None
        return json.loads(payload)

    def set(self, url: str, data: dict) -> None:
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO repo_health (url, payload, fetched_at) VALUES (?, ?, ?)",
                (url, json.dumps(data), int(time.time())),
            )


_DEFAULT_CACHE: _RepoCache | None = None


def _get_default_cache() -> _RepoCache:
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = _RepoCache()
    return _DEFAULT_CACHE


def check_has_repo(
    github_url: str, cache: _RepoCache | None = None
) -> tuple[Optional[bool], str, str]:
    """Returns (True/False/None, reason, last_commit_iso)."""
    if not github_url:
        return False, "no repo URL", ""
    cache = cache or _get_default_cache()
    cached = cache.get(github_url)
    if cached is None:
        try:
            cached = _fetch_github_repo_meta(github_url)
            cache.set(github_url, cached)
        except Exception as exc:
            logger.warning("GitHub API failed for %s: %s", github_url, exc)
            return None, f"GitHub API error: {exc}", ""
    stars = cached.get("stars", 0)
    last_commit = cached.get("last_commit_iso", "")
    if stars < 10:
        return False, f"only {stars} stars", last_commit
    return True, f"{stars} stars, last commit {last_commit[:10]}", last_commit


def _fetch_github_repo_meta(github_url: str) -> dict:
    """GET /repos/{owner}/{repo} via GitHub API (uses GITHUB_TOKEN if set)."""
    import httpx
    import os

    parts = github_url.rstrip("/").split("/")
    if "github.com" not in github_url or len(parts) < 5:
        raise ValueError(f"not a GitHub URL: {github_url}")
    owner, repo = parts[-2], parts[-1]
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
        r.raise_for_status()
        meta = r.json()
        return {
            "stars": meta.get("stargazers_count", 0),
            "last_commit_iso": meta.get("pushed_at", ""),
        }
