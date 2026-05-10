"""Item 9 — screening_decomposition_cache 30-day stash-drill."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from jobpulse.job_db import JobDB
from jobpulse.screening_decomposer import (
    _DECOMP_CACHE_TTL_DAYS,
    _decomposition_cache_lookup,
    _decomposition_cache_store,
    _decomposition_question_hash,
)


@pytest.fixture
def tmp_jobdb(tmp_path, monkeypatch):
    db_path = tmp_path / "applications.db"
    monkeypatch.setattr("jobpulse.job_db.DEFAULT_DB_PATH", db_path)
    return JobDB(db_path)


def test_question_hash_is_stable():
    a = _decomposition_question_hash("Q1")
    b = _decomposition_question_hash("Q1")
    c = _decomposition_question_hash("Q2")
    assert a == b
    assert a != c


def test_lookup_miss_then_hit(tmp_jobdb):
    q = "Are you authorised to work in the UK and willing to relocate?"
    assert _decomposition_cache_lookup(q, db=tmp_jobdb) is None
    payload = json.dumps([
        "Are you authorised to work in the UK?",
        "Are you willing to relocate?",
    ])
    _decomposition_cache_store(q, payload, db=tmp_jobdb)
    cached = _decomposition_cache_lookup(q, db=tmp_jobdb)
    assert cached == payload


def test_ttl_expired_returns_miss(tmp_jobdb):
    q = "Q with TTL"
    _decomposition_cache_store(q, "[]", db=tmp_jobdb)
    expired = (datetime.now() - timedelta(days=_DECOMP_CACHE_TTL_DAYS + 1)).isoformat()
    conn = tmp_jobdb._connect()
    conn.execute(
        "UPDATE screening_decomposition_cache SET generated_at = ? WHERE question_hash = ?",
        (expired, _decomposition_question_hash(q)),
    )
    conn.commit()
    assert _decomposition_cache_lookup(q, db=tmp_jobdb) is None


def test_test_mode_short_circuits(monkeypatch):
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")
    _decomposition_cache_store("Q", "payload")
    assert _decomposition_cache_lookup("Q") is None
