"""Regression tests for the hiring-message cache (cache-llm-S3).

Per `docs/audits/cache-llm-catalog.md` §C, `_generate_hiring_message`
previously had no cache and re-invoked the LLM on every duplicate
(company, role) call. S3 adds a TTL'd SQLite cache so repeat calls hit
the cache and skip the LLM.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import jobpulse.screening_answers as sa
from jobpulse.job_db import JobDB


@pytest.fixture
def isolated_db(tmp_path: Path):
    """Point JobDB at a tmp_path SQLite file for the duration of the test."""
    db_path = tmp_path / "applications.db"
    db = JobDB(db_path=db_path)
    yield db
    db.close()


def test_cache_miss_then_hit_skips_llm(isolated_db: JobDB):
    """First call generates + caches; second call returns cache without LLM."""
    company = "Anthropic"
    role_archetype = "research_engineer"
    canned = "Test hiring message body for Anthropic research role."

    # Cache miss → cache_lookup returns None
    assert sa._hiring_message_cache_lookup(company, role_archetype, db=isolated_db) is None

    # Persist a message (simulates the post-LLM store on the generation path)
    sa._hiring_message_cache_store(company, role_archetype, canned, db=isolated_db)

    # Second lookup → hit, returns the same string
    hit = sa._hiring_message_cache_lookup(company, role_archetype, db=isolated_db)
    assert hit == canned


def test_cache_keyed_by_company_and_archetype(isolated_db: JobDB):
    """Different companies / archetypes don't bleed into each other's cache."""
    sa._hiring_message_cache_store("Anthropic", "research_engineer", "msg-A", db=isolated_db)
    sa._hiring_message_cache_store("OpenAI", "research_engineer", "msg-B", db=isolated_db)
    sa._hiring_message_cache_store("Anthropic", "data_engineer", "msg-C", db=isolated_db)

    assert sa._hiring_message_cache_lookup("Anthropic", "research_engineer", db=isolated_db) == "msg-A"
    assert sa._hiring_message_cache_lookup("OpenAI", "research_engineer", db=isolated_db) == "msg-B"
    assert sa._hiring_message_cache_lookup("Anthropic", "data_engineer", db=isolated_db) == "msg-C"
    assert sa._hiring_message_cache_lookup("OpenAI", "data_engineer", db=isolated_db) is None


def test_cache_lookup_is_case_insensitive(isolated_db: JobDB):
    """Cache should normalise company / archetype to lowercase."""
    sa._hiring_message_cache_store("Anthropic", "Research_Engineer", "msg", db=isolated_db)
    assert sa._hiring_message_cache_lookup("ANTHROPIC", "research_engineer", db=isolated_db) == "msg"
    assert sa._hiring_message_cache_lookup("anthropic", "RESEARCH_ENGINEER", db=isolated_db) == "msg"


def test_cache_ttl_expiry(isolated_db: JobDB):
    """Entries older than _HIRING_MESSAGE_CACHE_TTL_DAYS are misses."""
    sa._hiring_message_cache_store("Stripe", "ml_engineer", "stale", db=isolated_db)

    # Force the row to be older than TTL by direct UPDATE
    expired = (datetime.now() - timedelta(days=sa._HIRING_MESSAGE_CACHE_TTL_DAYS + 1)).isoformat()
    conn = isolated_db._connect()
    conn.execute(
        "UPDATE hiring_message_cache SET generated_at = ? WHERE company = 'stripe'",
        (expired,),
    )
    conn.commit()

    assert sa._hiring_message_cache_lookup("Stripe", "ml_engineer", db=isolated_db) is None


def test_cache_hit_increments_hit_count(isolated_db: JobDB):
    """Each lookup that returns a hit bumps hit_count for analytics."""
    sa._hiring_message_cache_store("Foo", "data_scientist", "msg", db=isolated_db)
    for _ in range(3):
        assert sa._hiring_message_cache_lookup("Foo", "data_scientist", db=isolated_db) == "msg"

    conn = isolated_db._connect()
    row = conn.execute(
        "SELECT hit_count FROM hiring_message_cache WHERE company = 'foo'",
    ).fetchone()
    assert row["hit_count"] == 3


def test_classify_role_archetype_groups_titles():
    """Trivial title variations collapse to the same archetype."""
    assert sa._classify_role_archetype("Senior Data Engineer") == "data_engineer"
    assert sa._classify_role_archetype("Data Engineer II") == "data_engineer"
    assert sa._classify_role_archetype("Lead Data Engineer") == "data_engineer"
    assert sa._classify_role_archetype("Research Engineer, Knowledge Team") == "research_engineer"
    assert sa._classify_role_archetype("ML Engineer") == "ml_engineer"
    assert sa._classify_role_archetype("AI Engineer") == "ml_engineer"


def test_generate_hiring_message_uses_cache_on_repeat_call(
    monkeypatch: pytest.MonkeyPatch, isolated_db: JobDB,
):
    """The full _generate_hiring_message path: first call runs LLM,
    second call hits cache and DOES NOT call LLM."""

    # Route the cache helpers at the module's tmp DB.
    # Capture the originals *before* monkeypatching so the wrappers don't recurse.
    _orig_lookup = sa._hiring_message_cache_lookup
    _orig_store = sa._hiring_message_cache_store

    def _fake_lookup(company, role_archetype, *, db=None):
        return _orig_lookup(company, role_archetype, db=isolated_db)

    def _fake_store(company, role_archetype, message, *, db=None):
        return _orig_store(company, role_archetype, message, db=isolated_db)

    monkeypatch.setattr(sa, "_hiring_message_cache_lookup", _fake_lookup)
    monkeypatch.setattr(sa, "_hiring_message_cache_store", _fake_store)

    # Mock the LLM path: get_llm + smart_llm_call return a deterministic response.
    fake_text = "x" * 200  # >50 chars passes the length gate
    call_counter = {"count": 0}

    class _FakeMsg:
        def __init__(self, content):
            self.content = content

    def _fake_smart_llm_call(llm, messages):
        call_counter["count"] += 1
        return _FakeMsg(fake_text)

    monkeypatch.setattr(
        "shared.streaming.smart_llm_call", _fake_smart_llm_call,
    )
    # Also patch the in-function imports so the right symbol is found.
    import shared.agents
    monkeypatch.setattr(shared.agents, "smart_llm_call", _fake_smart_llm_call, raising=False)
    monkeypatch.setattr(
        shared.agents, "get_llm",
        lambda *a, **kw: object(),
    )

    job_context = {"company": "Anthropic", "title": "Research Engineer, Knowledge Team"}

    msg1 = sa._generate_hiring_message(job_context)
    assert msg1 == fake_text
    assert call_counter["count"] == 1

    # Second call with same (company, role_archetype) → cache hit, NO LLM call
    msg2 = sa._generate_hiring_message(job_context)
    assert msg2 == fake_text
    assert call_counter["count"] == 1, "LLM should NOT have been called on cache hit"
