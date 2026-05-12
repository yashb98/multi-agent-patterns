from datetime import datetime, timedelta, timezone

import pytest

from jobpulse.papers.ranker import _repo_activity_boost


def test_no_repo_url_zero():
    assert _repo_activity_boost(github_url="", last_commit_iso="") == 0.0


def test_recent_commit_full_boost():
    recent = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    assert _repo_activity_boost(github_url="https://github.com/x/y", last_commit_iso=recent) == 1.0


def test_old_commit_no_boost():
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    assert _repo_activity_boost(github_url="https://github.com/x/y", last_commit_iso=old) == 0.0
