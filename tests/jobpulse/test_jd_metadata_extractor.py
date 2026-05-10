"""Tests for `jobpulse.jd_metadata_extractor` — Audit 2026-05-10 / Slice S6 / TP-11.

The extractor replaces the hardcoded BeautifulSoup CSS selectors in
`scan_pipeline.process_single_url:1060-1069` (which were LinkedIn-biased and
missed every other ATS, producing `Unknown Role @ Unknown Company` for Lever,
Ashby, Greenhouse, etc.). It uses LLM extraction over `jd_text` rather than
HTML structure, making it adapter-agnostic.

Per project policy: pure-function tests only here. LLM behavior is exercised
end-to-end against real Kimi/Moonshot in `tests/jobpulse/integration/`.
"""
from __future__ import annotations

import pytest


# ── Pure helpers — caching key, validation, sanitization ──


def test_jd_hash_deterministic():
    """Same input → same hash. Hash is an audit/cache primitive."""
    from jobpulse.jd_metadata_extractor import _jd_hash

    text = "Palantir Technologies - Forward Deployed AI Engineer\nLondon, UK"
    assert _jd_hash(text) == _jd_hash(text)
    assert _jd_hash(text) != _jd_hash(text + "\nextra")


def test_jd_hash_strips_whitespace():
    """Hash should be stable across surrounding whitespace variations
    (callers may pass jd_text with or without leading/trailing newlines)."""
    from jobpulse.jd_metadata_extractor import _jd_hash

    base = "Software Engineer\nAt Acme Corp"
    assert _jd_hash(base) == _jd_hash("\n  " + base + "  \n")


def test_jd_hash_returns_short_string():
    """Hash should be a stable short string (used as cache key)."""
    from jobpulse.jd_metadata_extractor import _jd_hash

    h = _jd_hash("anything")
    assert isinstance(h, str)
    assert 8 <= len(h) <= 64  # hex digest, not the full sha256


# ── Validation: sanity-check LLM output ──


def test_validate_extraction_accepts_clean_strings():
    """Valid title + company pass through unchanged."""
    from jobpulse.jd_metadata_extractor import _validate_extraction

    out = _validate_extraction({"title": "Software Engineer", "company": "Acme"})
    assert out == {"title": "Software Engineer", "company": "Acme"}


def test_validate_extraction_strips_whitespace():
    """Surrounding whitespace is stripped (LLM occasionally pads with spaces)."""
    from jobpulse.jd_metadata_extractor import _validate_extraction

    out = _validate_extraction({"title": "  Engineer  ", "company": "\nAcme\n"})
    assert out == {"title": "Engineer", "company": "Acme"}


def test_validate_extraction_rejects_unknown_sentinels():
    """If the LLM returns "Unknown Role" or similar sentinels, treat as empty.
    The caller's fallback chain handles the empty case downstream — better
    than letting the sentinel value flow into Notion / CV path / DBs.
    """
    from jobpulse.jd_metadata_extractor import _validate_extraction

    for sentinel in ("Unknown Role", "Unknown Company", "unknown",
                     "N/A", "n/a", "Not Specified", "Not specified",
                     "TBD", "tbd"):
        out = _validate_extraction({"title": sentinel, "company": "Acme"})
        assert out["title"] == "", f"sentinel {sentinel!r} should produce empty title"


def test_validate_extraction_caps_overlong_strings():
    """Reject pathological LLM outputs that bloat the field. Defensive
    cap at 200 chars — matches `dimensions.md → B2 (input truncation)`.
    """
    from jobpulse.jd_metadata_extractor import _validate_extraction

    long_title = "A" * 500
    out = _validate_extraction({"title": long_title, "company": "Acme"})
    assert len(out["title"]) <= 200


def test_validate_extraction_handles_missing_keys():
    """Missing or non-string keys default to empty (no KeyError, no TypeError)."""
    from jobpulse.jd_metadata_extractor import _validate_extraction

    assert _validate_extraction({}) == {"title": "", "company": ""}
    assert _validate_extraction({"title": None, "company": 123}) == {
        "title": "", "company": "",
    }


# ── Cache primitives ──


def test_cache_roundtrip(monkeypatch):
    """Setting + getting through the in-memory cache round-trips."""
    from jobpulse.jd_metadata_extractor import _CACHE, _cache_get, _cache_set

    _CACHE.clear()
    _cache_set("h1", {"title": "Eng", "company": "Acme"})
    assert _cache_get("h1") == {"title": "Eng", "company": "Acme"}
    assert _cache_get("missing") is None


def test_cache_lru_eviction():
    """When the cache exceeds its max size, the oldest entry is evicted."""
    from jobpulse.jd_metadata_extractor import (
        _CACHE,
        _CACHE_MAX_ENTRIES,
        _cache_get,
        _cache_set,
    )

    _CACHE.clear()
    # Fill past max — first entry should be evicted
    for i in range(_CACHE_MAX_ENTRIES + 5):
        _cache_set(f"h{i}", {"title": str(i), "company": "x"})
    # The first 5 entries should be evicted (FIFO/LRU)
    assert _cache_get("h0") is None
    # The most-recent entry should still be present
    assert _cache_get(f"h{_CACHE_MAX_ENTRIES + 4}") is not None


# ── Empty / edge-case inputs ──


def test_extract_returns_empty_on_empty_jd():
    """Empty / whitespace-only jd_text short-circuits to empty result.
    No LLM call, no cache write."""
    from jobpulse.jd_metadata_extractor import extract_title_company

    assert extract_title_company("") == {"title": "", "company": ""}
    assert extract_title_company("   \n\n   ") == {"title": "", "company": ""}


def test_extract_uses_cache_on_repeated_call(monkeypatch):
    """Same jd_text twice → only one LLM call (cache hit on the second)."""
    from jobpulse.jd_metadata_extractor import (
        _CACHE,
        extract_title_company,
    )

    _CACHE.clear()
    call_count = {"n": 0}

    def fake_llm_call(jd_text):
        call_count["n"] += 1
        return {"title": "Eng", "company": "Acme"}

    monkeypatch.setattr(
        "jobpulse.jd_metadata_extractor._llm_extract", fake_llm_call,
    )

    jd = "Acme — Software Engineer\nLondon, UK"
    a = extract_title_company(jd)
    b = extract_title_company(jd)
    assert a == b == {"title": "Eng", "company": "Acme"}
    assert call_count["n"] == 1, "cache should suppress the second LLM call"


def test_extract_falls_through_to_empty_on_llm_failure(monkeypatch):
    """If the LLM raises, return empty result. Caller's `or 'Unknown Role'`
    fallback handles downstream. No exception leaks."""
    from jobpulse.jd_metadata_extractor import _CACHE, extract_title_company

    _CACHE.clear()

    def boom(jd_text):
        raise RuntimeError("Kimi 500")

    monkeypatch.setattr("jobpulse.jd_metadata_extractor._llm_extract", boom)

    out = extract_title_company("Acme — Software Engineer\nLondon")
    assert out == {"title": "", "company": ""}
