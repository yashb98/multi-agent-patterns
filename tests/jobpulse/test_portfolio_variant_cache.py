"""Items 6 + 7 — portfolio_variant_cache 14-day stash-drill.

Mirrors the canonical cache pattern (S4 tailored_cv_cache, S5
cover_letter_cache): stash a fresh result on first call, return the
cache on second call without firing the LLM, age past TTL → miss again.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from jobpulse.job_db import JobDB
from jobpulse.portfolio_variants import (
    _PORTFOLIO_CACHE_TTL_DAYS,
    _portfolio_cache_key,
    _portfolio_variant_cache_lookup,
    _portfolio_variant_cache_store,
)


@pytest.fixture
def tmp_jobdb(tmp_path, monkeypatch):
    """Per-test JobDB inside tmp_path so cache writes don't leak."""

    db_path = tmp_path / "applications.db"
    monkeypatch.setattr("jobpulse.job_db.DEFAULT_DB_PATH", db_path)
    return JobDB(db_path)


def test_cache_key_is_stable(tmp_jobdb):
    a = _portfolio_cache_key(
        kind="bullets", title="t", archetype="x",
        bullets=["b1", "b2"], jd_skills=["py", "sql"],
    )
    b = _portfolio_cache_key(
        kind="bullets", title="t", archetype="x",
        bullets=["b1", "b2"], jd_skills=["py", "sql"],
    )
    assert a == b
    c = _portfolio_cache_key(
        kind="bullets", title="t", archetype="x",
        bullets=["b1", "b2"], jd_skills=["py", "rust"],
    )
    assert c != a, "skill change should change the key"


def test_lookup_miss_then_store_then_hit(tmp_jobdb):
    key = "miss_hit_key"
    assert _portfolio_variant_cache_lookup("bullets", key, db=tmp_jobdb) is None
    payload = json.dumps(["bullet 1", "bullet 2", "bullet 3"])
    _portfolio_variant_cache_store("bullets", key, payload, db=tmp_jobdb)
    cached = _portfolio_variant_cache_lookup("bullets", key, db=tmp_jobdb)
    assert cached == payload


def test_ttl_expired_returns_miss(tmp_jobdb):
    key = "ttl_key"
    _portfolio_variant_cache_store("bullets", key, "[]", db=tmp_jobdb)
    # Fast-forward generated_at past the TTL.
    expired = (datetime.now() - timedelta(days=_PORTFOLIO_CACHE_TTL_DAYS + 1)).isoformat()
    conn = tmp_jobdb._connect()
    conn.execute(
        "UPDATE portfolio_variant_cache SET generated_at = ? "
        "WHERE kind = ? AND cache_key = ?",
        (expired, "bullets", key),
    )
    conn.commit()
    assert _portfolio_variant_cache_lookup("bullets", key, db=tmp_jobdb) is None


def test_kind_isolates_namespaces(tmp_jobdb):
    """A 'bullets' lookup with the same key as a stored 'entry' must miss."""

    key = "shared_key"
    _portfolio_variant_cache_store("entry", key, '{"title":"x","bullets":[]}', db=tmp_jobdb)
    assert _portfolio_variant_cache_lookup("bullets", key, db=tmp_jobdb) is None
    assert _portfolio_variant_cache_lookup("entry", key, db=tmp_jobdb) is not None


def test_test_mode_short_circuits(monkeypatch, tmp_path):
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")
    db_path = tmp_path / "applications.db"
    monkeypatch.setattr("jobpulse.job_db.DEFAULT_DB_PATH", db_path)
    # No db= passed, so the test-mode guard applies.
    _portfolio_variant_cache_store("bullets", "key", "payload")
    assert _portfolio_variant_cache_lookup("bullets", "key") is None


def test_hit_count_increments_on_lookup(tmp_jobdb):
    key = "hit_count_key"
    _portfolio_variant_cache_store("entry", key, '{"title":"x","bullets":[]}', db=tmp_jobdb)
    _portfolio_variant_cache_lookup("entry", key, db=tmp_jobdb)
    _portfolio_variant_cache_lookup("entry", key, db=tmp_jobdb)
    conn = tmp_jobdb._connect()
    row = conn.execute(
        "SELECT hit_count FROM portfolio_variant_cache "
        "WHERE kind = 'entry' AND cache_key = ?",
        (key,),
    ).fetchone()
    assert row[0] == 2
