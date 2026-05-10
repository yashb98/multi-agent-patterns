"""Item 10 — cv_scrutiny_cache 30-day stash-drill.

Same shape as the other content caches (S4 tailored_cv, S5 cover_letter,
S3 hiring_message): keyed (cv_hash, jd_hash); TTL miss; hit_count
increments; JOBPULSE_TEST_MODE short-circuits when no JobDB is supplied.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jobpulse.gate4_quality import (
    _CV_SCRUTINY_CACHE_TTL_DAYS,
    LLMScrutinyResult,
    _cv_scrutiny_cache_lookup,
    _cv_scrutiny_cache_store,
    _cv_scrutiny_from_payload,
    _cv_scrutiny_hash,
    _cv_scrutiny_to_payload,
)
from jobpulse.job_db import JobDB


@pytest.fixture
def tmp_jobdb(tmp_path, monkeypatch):
    db_path = tmp_path / "applications.db"
    monkeypatch.setattr("jobpulse.job_db.DEFAULT_DB_PATH", db_path)
    return JobDB(db_path)


def test_payload_roundtrip():
    res = LLMScrutinyResult(
        score=8, verdict="shortlist", needs_review=False,
        strengths=["s1", "s2"], weaknesses=["w1"],
        breakdown={"relevance": 3, "evidence": 3, "presentation": 2, "standout": 0},
    )
    payload = _cv_scrutiny_to_payload(res)
    parsed = _cv_scrutiny_from_payload(payload)
    assert parsed.score == res.score
    assert parsed.verdict == res.verdict
    assert parsed.strengths == res.strengths
    assert parsed.breakdown == res.breakdown


def test_lookup_miss_then_store_then_hit(tmp_jobdb):
    cv = _cv_scrutiny_hash("cv text")
    jd = _cv_scrutiny_hash("jd text")
    assert _cv_scrutiny_cache_lookup(cv, jd, db=tmp_jobdb) is None
    payload = _cv_scrutiny_to_payload(LLMScrutinyResult(score=7))
    _cv_scrutiny_cache_store(cv, jd, payload, db=tmp_jobdb)
    cached = _cv_scrutiny_cache_lookup(cv, jd, db=tmp_jobdb)
    assert cached == payload


def test_ttl_expired_returns_miss(tmp_jobdb):
    cv = _cv_scrutiny_hash("cv")
    jd = _cv_scrutiny_hash("jd")
    _cv_scrutiny_cache_store(cv, jd, "{}", db=tmp_jobdb)
    expired = (datetime.now() - timedelta(days=_CV_SCRUTINY_CACHE_TTL_DAYS + 1)).isoformat()
    conn = tmp_jobdb._connect()
    conn.execute(
        "UPDATE cv_scrutiny_cache SET generated_at = ? WHERE cv_hash = ? AND jd_hash = ?",
        (expired, cv, jd),
    )
    conn.commit()
    assert _cv_scrutiny_cache_lookup(cv, jd, db=tmp_jobdb) is None


def test_test_mode_short_circuits(monkeypatch):
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")
    cv = _cv_scrutiny_hash("cv")
    jd = _cv_scrutiny_hash("jd")
    _cv_scrutiny_cache_store(cv, jd, "{}")
    assert _cv_scrutiny_cache_lookup(cv, jd) is None


def test_different_jd_does_not_collide(tmp_jobdb):
    cv = _cv_scrutiny_hash("cv")
    jd1 = _cv_scrutiny_hash("jd1")
    jd2 = _cv_scrutiny_hash("jd2")
    _cv_scrutiny_cache_store(cv, jd1, "p1", db=tmp_jobdb)
    assert _cv_scrutiny_cache_lookup(cv, jd1, db=tmp_jobdb) == "p1"
    assert _cv_scrutiny_cache_lookup(cv, jd2, db=tmp_jobdb) is None
