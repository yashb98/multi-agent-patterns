"""Regression tests for the cover-letter polish cache (cache-llm-S5).

Per `docs/audits/cache-llm-catalog.md` §E, `polish_points_llm` previously
fired an LLM call for every cover-letter generation. S5 caches the
polished points by `(company, role_archetype, inputs_hash)` so a repeat
application to the same JD with the same input points returns the
cached refinement.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import jobpulse.cv_templates.generate_cover_letter as gcl
from jobpulse.cv_templates.generate_cover_letter import (
    _cover_letter_cache_lookup,
    _cover_letter_cache_store,
    _cover_letter_inputs_hash,
    _cover_letter_role_archetype,
    polish_points_llm,
)
from jobpulse.job_db import JobDB


@pytest.fixture
def isolated_db(tmp_path: Path):
    db_path = tmp_path / "applications.db"
    db = JobDB(db_path=db_path)
    yield db
    db.close()


@pytest.fixture
def stub_points() -> list[tuple[str, str]]:
    return [
        ("Python:", "Built JobPulse with 4,400+ tests."),
        ("ML:", "Trained transformer models on 50k examples."),
        ("Cloud:", "Deployed 5 services on AWS."),
        ("Leadership:", "Led 6-person Co-op team."),
    ]


@pytest.fixture
def required_skills() -> list[str]:
    return ["python", "ml", "aws", "kubernetes"]


# ── Helpers ────────────────────────────────────────────────────────────────


def test_role_archetype_collapses_variants():
    assert _cover_letter_role_archetype("Senior Data Engineer") == "data_engineer"
    assert _cover_letter_role_archetype("Lead Data Engineer") == "data_engineer"
    assert _cover_letter_role_archetype("ML Engineer") == "ml_engineer"
    assert _cover_letter_role_archetype("AI Engineer") == "ml_engineer"


def test_inputs_hash_is_skill_order_independent(stub_points):
    h1 = _cover_letter_inputs_hash("Engineer", "Anthropic", ["python", "ml"], stub_points)
    h2 = _cover_letter_inputs_hash("Engineer", "Anthropic", ["ml", "python"], stub_points)
    assert h1 == h2


def test_inputs_hash_changes_when_points_change(stub_points):
    h1 = _cover_letter_inputs_hash("Engineer", "Anthropic", ["python"], stub_points)
    altered = stub_points[:-1] + [("Different:", "different content")]
    h2 = _cover_letter_inputs_hash("Engineer", "Anthropic", ["python"], altered)
    assert h1 != h2


def test_inputs_hash_changes_when_company_changes(stub_points):
    h1 = _cover_letter_inputs_hash("Engineer", "Anthropic", ["python"], stub_points)
    h2 = _cover_letter_inputs_hash("Engineer", "OpenAI", ["python"], stub_points)
    assert h1 != h2


# ── Cache lookup / store ──────────────────────────────────────────────────


def test_cache_miss_then_hit(isolated_db, stub_points):
    key = ("Anthropic", "research_engineer", "abcd1234")
    assert _cover_letter_cache_lookup(*key, db=isolated_db) is None

    _cover_letter_cache_store(*key, stub_points, db=isolated_db)
    hit = _cover_letter_cache_lookup(*key, db=isolated_db)
    assert hit is not None
    assert hit == stub_points


def test_cache_lookup_round_trip_preserves_tuples(isolated_db, stub_points):
    """JSON encode/decode must yield list[tuple[str, str]], not list[list[str]]."""
    _cover_letter_cache_store("Co", "ml_engineer", "h1", stub_points, db=isolated_db)
    hit = _cover_letter_cache_lookup("Co", "ml_engineer", "h1", db=isolated_db)
    assert all(isinstance(p, tuple) for p in hit)
    assert all(len(p) == 2 for p in hit)


def test_cache_keyed_by_all_three_fields(isolated_db, stub_points):
    _cover_letter_cache_store("A", "data_engineer", "h1", stub_points, db=isolated_db)
    _cover_letter_cache_store("B", "data_engineer", "h1", stub_points, db=isolated_db)
    _cover_letter_cache_store("A", "ml_engineer", "h1", stub_points, db=isolated_db)
    _cover_letter_cache_store("A", "data_engineer", "h2", stub_points, db=isolated_db)
    # Each row distinct
    conn = isolated_db._connect()
    rows = conn.execute("SELECT COUNT(*) FROM cover_letter_cache").fetchone()
    assert rows[0] == 4


def test_cache_ttl_expiry(isolated_db, stub_points):
    _cover_letter_cache_store("Stripe", "ml_engineer", "h1", stub_points, db=isolated_db)
    expired = (datetime.now() - timedelta(days=gcl._COVER_LETTER_CACHE_TTL_DAYS + 1)).isoformat()
    conn = isolated_db._connect()
    conn.execute(
        "UPDATE cover_letter_cache SET generated_at = ? WHERE company = 'stripe'",
        (expired,),
    )
    conn.commit()
    assert _cover_letter_cache_lookup("Stripe", "ml_engineer", "h1", db=isolated_db) is None


def test_cache_hit_increments_hit_count(isolated_db, stub_points):
    _cover_letter_cache_store("Foo", "data_scientist", "h1", stub_points, db=isolated_db)
    for _ in range(3):
        _cover_letter_cache_lookup("Foo", "data_scientist", "h1", db=isolated_db)
    conn = isolated_db._connect()
    row = conn.execute(
        "SELECT hit_count FROM cover_letter_cache WHERE company = 'foo'",
    ).fetchone()
    assert row["hit_count"] == 3


# ── polish_points_llm integration ──────────────────────────────────────


def test_polish_points_llm_caches_and_skips_llm_on_repeat(
    monkeypatch: pytest.MonkeyPatch, isolated_db, stub_points, required_skills,
):
    """First call runs LLM; second call hits cache and DOES NOT run LLM."""
    _orig_lookup = gcl._cover_letter_cache_lookup
    _orig_store = gcl._cover_letter_cache_store

    lookup_count = {"count": 0}

    def _spy_lookup(c, r, h, *, db=None):
        lookup_count["count"] += 1
        return _orig_lookup(c, r, h, db=isolated_db)

    monkeypatch.setattr(gcl, "_cover_letter_cache_lookup", _spy_lookup)
    monkeypatch.setattr(
        gcl, "_cover_letter_cache_store",
        lambda c, r, h, p, *, db=None: _orig_store(c, r, h, p, db=isolated_db),
    )

    polished = [
        {"header": "Python:", "detail": "polished detail 1 "},
        {"header": "ML:", "detail": "polished detail 2 "},
        {"header": "Cloud:", "detail": "polished detail 3 "},
        {"header": "Leadership:", "detail": "polished detail 4 "},
    ]
    import json as _json

    llm_count = {"count": 0}

    def _fake_llm(*, task, domain, stakes):
        llm_count["count"] += 1
        return _json.dumps(polished)

    # cognitive_llm_call lives on shared.agents but is imported lazily inside
    # polish_points_llm, so patching shared.agents is what counts.
    import shared.agents
    monkeypatch.setattr(shared.agents, "cognitive_llm_call", _fake_llm)

    # First call: cache miss → 1 lookup attempt + 1 LLM call
    out1 = polish_points_llm(stub_points, "ML Engineer", "Anthropic", required_skills)
    assert lookup_count["count"] == 1
    assert llm_count["count"] == 1
    assert all(isinstance(p, tuple) and len(p) == 2 for p in out1)

    # Second call: cache hit → 2nd lookup fires AND returns a non-None payload,
    # so LLM count stays at 1. The lookup-count check makes the regression
    # detection symmetric: if the cache lookup were silently bypassed, this
    # would still pass, so we also assert below that the lookup actually
    # returned a hit.
    out2 = polish_points_llm(stub_points, "ML Engineer", "Anthropic", required_skills)
    assert lookup_count["count"] == 2
    assert llm_count["count"] == 1, "polish_points_llm should NOT call LLM on cache hit"
    assert out2 == out1

    # Direct cache-hit confirmation: the row exists and the lookup returns it.
    role_archetype = gcl._cover_letter_role_archetype("ML Engineer")
    inputs_hash = gcl._cover_letter_inputs_hash(
        "ML Engineer", "Anthropic", required_skills, stub_points,
    )
    direct_hit = _orig_lookup(
        "Anthropic", role_archetype, inputs_hash, db=isolated_db,
    )
    assert direct_hit is not None
    assert direct_hit == out2


def test_polish_points_llm_does_not_cache_malformed_llm_output(
    monkeypatch: pytest.MonkeyPatch, isolated_db, stub_points, required_skills,
):
    """If the LLM returns garbage, the original points are returned and
    nothing is cached — the next call must retry the LLM, not serve garbage."""
    _orig_lookup = gcl._cover_letter_cache_lookup
    _orig_store = gcl._cover_letter_cache_store
    monkeypatch.setattr(
        gcl, "_cover_letter_cache_lookup",
        lambda c, r, h, *, db=None: _orig_lookup(c, r, h, db=isolated_db),
    )
    monkeypatch.setattr(
        gcl, "_cover_letter_cache_store",
        lambda c, r, h, p, *, db=None: _orig_store(c, r, h, p, db=isolated_db),
    )

    counter = {"count": 0}

    def _bad_llm(*, task, domain, stakes):
        counter["count"] += 1
        return "this is not valid json"

    import shared.agents
    monkeypatch.setattr(shared.agents, "cognitive_llm_call", _bad_llm)

    # First call: malformed → return original points, no cache write
    out1 = polish_points_llm(stub_points, "Engineer", "Foo", required_skills)
    assert counter["count"] == 1
    assert out1 == stub_points

    # Second call: cache miss again (nothing was stored) → LLM fires again
    out2 = polish_points_llm(stub_points, "Engineer", "Foo", required_skills)
    assert counter["count"] == 2, "malformed LLM output must not be cached"
    assert out2 == stub_points


def test_polish_points_llm_does_not_cache_when_llm_returns_none(
    monkeypatch: pytest.MonkeyPatch, isolated_db, stub_points, required_skills,
):
    """LLM returning None (timeout / cognitive engine off) → unpolished
    points returned, cache untouched."""
    _orig_lookup = gcl._cover_letter_cache_lookup
    _orig_store = gcl._cover_letter_cache_store
    store_calls = {"count": 0}

    def _store_spy(c, r, h, p, *, db=None):
        store_calls["count"] += 1
        return _orig_store(c, r, h, p, db=isolated_db)

    monkeypatch.setattr(
        gcl, "_cover_letter_cache_lookup",
        lambda c, r, h, *, db=None: _orig_lookup(c, r, h, db=isolated_db),
    )
    monkeypatch.setattr(gcl, "_cover_letter_cache_store", _store_spy)

    import shared.agents
    monkeypatch.setattr(shared.agents, "cognitive_llm_call", lambda **_: None)

    out = polish_points_llm(stub_points, "Engineer", "Foo", required_skills)
    assert out == stub_points
    assert store_calls["count"] == 0
