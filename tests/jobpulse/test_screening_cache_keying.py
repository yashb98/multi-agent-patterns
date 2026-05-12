"""Slice S1 — Cache key with profile_state_hash + jd_context_hash.

The semantic-analysis audit (TP-1) found the screening cache key is
question-text-only: the same question returns the same answer regardless
of profile state or JD context. Visa-sponsorship is the canonical
worked example — UK Graduate Visa → "No"; US JD same profile → "Yes".

These tests reproduce the bug, then ride along with the fix in the same
slice. After the fix, ScreeningSemanticCache.cache() / .lookup() accept
profile_state_hash + jd_context_hash and fold them into the cache key so
different (profile, JD) contexts produce distinct cache entries.

SQLite-only mode (qdrant_location=None) exercises the brute-force cosine
fallback path; that path also has to honour the hashes.
"""

from __future__ import annotations

import pytest

from jobpulse.screening_semantic_cache import ScreeningSemanticCache


_QUESTION = "Will you now or in the future require employment visa sponsorship?"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Fresh SQLite-only cache per test, isolated to tmp_path."""
    monkeypatch.setenv("MEMORY_QDRANT_URL", "")  # force SQLite-only
    sqlite_path = tmp_path / "screening_cache.db"
    return ScreeningSemanticCache(sqlite_path=str(sqlite_path))


def test_lookup_misses_when_jd_context_differs(cache):
    """Same question + same profile_state, but different JD context →
    cache miss. Pre-fix: hits because key is question-only.
    """
    cache.cache(
        question=_QUESTION,
        intent="sponsorship",
        answer="No",
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )

    hit_us = cache.lookup(
        _QUESTION,
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_us_nyc",
    )
    assert hit_us is None, (
        "Same question on a US JD must NOT serve the cached UK answer. "
        f"Got {hit_us!r}"
    )


def test_lookup_misses_when_profile_state_differs(cache):
    """Same question + same JD context, but different profile state →
    cache miss. Profile-state changes (visa renewal, location move,
    salary revision) MUST invalidate dependent decisions.
    """
    cache.cache(
        question=_QUESTION,
        intent="sponsorship",
        answer="No",
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )

    hit_after_visa_change = cache.lookup(
        _QUESTION,
        profile_state_hash="profile_uk_ilr",  # ILR = different visa state
        jd_context_hash="jd_uk_london",
    )
    assert hit_after_visa_change is None, (
        "Visa-state change must invalidate the cached answer. "
        f"Got {hit_after_visa_change!r}"
    )


def test_lookup_hits_when_both_hashes_match(cache):
    """Positive control: same question + same profile + same JD → hit.
    Verifies the hashing path doesn't accidentally over-segment the cache.
    """
    cache.cache(
        question=_QUESTION,
        intent="sponsorship",
        answer="No",
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )

    hit = cache.lookup(
        _QUESTION,
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )
    assert hit is not None, "Identical (profile, JD) context must hit"
    assert hit.answer == "No"


def test_distinct_entries_per_jd_context(cache):
    """Two distinct (profile, JD) caches must produce two retrievable
    entries — the worldwide multi-region requirement from D9.
    """
    cache.cache(
        question=_QUESTION,
        intent="sponsorship",
        answer="No",
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )
    cache.cache(
        question=_QUESTION,
        intent="sponsorship",
        answer="Yes",
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_us_nyc",
    )

    hit_uk = cache.lookup(
        _QUESTION,
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_uk_london",
    )
    hit_us = cache.lookup(
        _QUESTION,
        profile_state_hash="profile_uk_grad",
        jd_context_hash="jd_us_nyc",
    )

    assert hit_uk is not None and hit_uk.answer == "No"
    assert hit_us is not None and hit_us.answer == "Yes"


# ---------------------------------------------------------------------------
# Pipeline-level wiring: ScreeningPipeline computes both hashes and passes
# them through. These tests are wiring guards — they exercise the call
# signature, not behaviour the cache tests already cover.
# ---------------------------------------------------------------------------


def test_pipeline_computes_profile_state_hash():
    """ScreeningPipeline._profile_state_hash must derive from the screening-
    determining fields (visa, salary, notice, location, relocation,
    languages) — not the entire profile (otherwise unrelated changes
    like LinkedIn URL destroy hit rate)."""
    from jobpulse.screening_pipeline import ScreeningPipeline

    base = {
        "visa_status": "Graduate Visa",
        "current_salary": "20000",
        "notice_period": "1 month",
        "location": "Dundee, UK",
        "willing_to_relocate": "yes",
        "languages": ["English"],
        "linkedin": "https://linkedin.com/in/anyone",  # NOT in hash
    }
    p1 = ScreeningPipeline(profile=base)

    # Change a non-screening field — hash MUST be unchanged.
    base_unrelated = dict(base, linkedin="https://linkedin.com/in/different")
    p2 = ScreeningPipeline(profile=base_unrelated)
    assert p1._profile_state_hash == p2._profile_state_hash, (
        "Non-screening profile changes must not change profile_state_hash"
    )

    # Change a screening field (visa) — hash MUST change.
    base_visa_change = dict(base, visa_status="ILR")
    p3 = ScreeningPipeline(profile=base_visa_change)
    assert p1._profile_state_hash != p3._profile_state_hash, (
        "Visa-state change must change profile_state_hash"
    )


def test_pipeline_computes_jd_context_hash():
    """ScreeningPipeline._jd_context_hash must vary across countries /
    currencies / role levels — these are the JD axes that drive
    visa, salary, notice answers."""
    from jobpulse.screening_pipeline import ScreeningPipeline

    pipeline = ScreeningPipeline(profile={"visa_status": "Graduate Visa"})

    uk = pipeline._jd_context_hash({"country": "United Kingdom", "currency": "GBP", "role_level": "mid"})
    us = pipeline._jd_context_hash({"country": "United States", "currency": "USD", "role_level": "mid"})
    de = pipeline._jd_context_hash({"country": "Germany", "currency": "EUR", "role_level": "mid"})

    assert uk != us, "UK vs US JD context must hash differently"
    assert uk != de, "UK vs DE JD context must hash differently"
    assert us != de, "US vs DE JD context must hash differently"

    # Same context → same hash (determinism).
    uk_again = pipeline._jd_context_hash({"country": "United Kingdom", "currency": "GBP", "role_level": "mid"})
    assert uk == uk_again, "Hash must be deterministic for the same JD context"

    # Empty / None JD context → stable sentinel hash (don't crash).
    none_hash = pipeline._jd_context_hash(None)
    empty_hash = pipeline._jd_context_hash({})
    assert none_hash == empty_hash
    assert isinstance(none_hash, str) and none_hash, "Sentinel hash must be a non-empty string"
