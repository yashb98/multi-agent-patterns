"""Regression tests verifying the page-reasoner cache behaviour
(cache-llm-S6).

Per `docs/audits/cache-llm-catalog.md` §F, §2.2 #5 claimed
`page_reasoner._call_llm` had only "partial caching." Code reading at
HEAD shows comprehensive `(domain, content_hash)` caching with a 1-hour
TTL and exact + semantic-near-miss lookup paths. These tests pin that
behaviour so a future regression (e.g. someone removing the
`_get_cached` short-circuit) is caught immediately.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jobpulse.page_analysis.page_reasoner import PageAction, PageReasoner


@pytest.fixture
def reasoner(tmp_path: Path) -> PageReasoner:
    """PageReasoner pointed at a tmp_path SQLite cache."""
    return PageReasoner(db_path=str(tmp_path / "page_reasoning_cache.db"))


@pytest.fixture
def snapshot() -> dict:
    """A minimal, deterministic page snapshot for cache-key derivation."""
    return {
        "url": "https://job-boards.greenhouse.io/anthropic/jobs/4017331008",
        "page_text_preview": "Apply for Research Engineer, Knowledge Team",
        "dialog_text": "",
        "fields": [
            {"label": "First Name", "type": "text"},
            {"label": "Email", "type": "text"},
        ],
        "buttons": [{"text": "Submit Application"}],
    }


@pytest.fixture
def fake_action() -> PageAction:
    return PageAction(
        page_understanding="Application form for an engineering role",
        action="fill_form",
        target_text="",
        reasoning="DOM has form fields and a submit button",
        confidence=0.92,
        page_type="application_form",
        expected_outcome="fields_filled",
    )


# ── Cache plumbing ────────────────────────────────────────────────────────


def test_cache_key_is_domain_plus_content_hash(reasoner, snapshot):
    """The cache key starts with the domain (lowercased, no www) so
    domain-scoped semantic lookups can use a `LIKE 'domain:%'` filter."""
    key = reasoner._cache_key(
        snapshot["url"], snapshot["page_text_preview"],
        snapshot["dialog_text"], snapshot["fields"], snapshot["buttons"],
    )
    assert key.startswith("job-boards.greenhouse.io:")
    # Content portion is a 16-char hex hash
    suffix = key.split(":", 1)[1]
    assert len(suffix) == 16
    assert all(c in "0123456789abcdef" for c in suffix)


def test_cache_key_changes_when_fields_change(reasoner, snapshot):
    """Adding a field changes the content hash so a new page version
    forces a fresh LLM call."""
    k1 = reasoner._cache_key(
        snapshot["url"], snapshot["page_text_preview"], snapshot["dialog_text"],
        snapshot["fields"], snapshot["buttons"],
    )
    fields_plus = snapshot["fields"] + [{"label": "Phone", "type": "text"}]
    k2 = reasoner._cache_key(
        snapshot["url"], snapshot["page_text_preview"], snapshot["dialog_text"],
        fields_plus, snapshot["buttons"],
    )
    assert k1 != k2


def test_cache_set_and_get_round_trip(reasoner, fake_action):
    key = "domain.com:abc123"
    reasoner._set_cache(key, fake_action)
    hit = reasoner._get_cached(key)
    assert hit is not None
    assert hit.action == fake_action.action
    assert hit.page_type == fake_action.page_type
    assert hit.confidence == fake_action.confidence


def test_low_confidence_aborts_are_not_cached(reasoner):
    """An `abort` with confidence < 0.5 should NOT be cached — the LLM
    was uncertain and we want a fresh decision next visit."""
    flaky = PageAction(
        page_understanding="unclear", action="abort", target_text="",
        reasoning="unclear", confidence=0.3, page_type="unknown",
        expected_outcome="unknown",
    )
    reasoner._set_cache("domain.com:bad", flaky)
    assert reasoner._get_cached("domain.com:bad") is None


def test_cache_ttl_one_hour(reasoner, fake_action):
    """Entries older than 1 hour are misses on lookup."""
    import sqlite3
    key = "domain.com:expired"
    reasoner._set_cache(key, fake_action)
    # Force an expired created_at (3601s in the past)
    with sqlite3.connect(reasoner._db_path) as conn:
        conn.execute(
            "UPDATE reasoning_cache SET created_at = ? WHERE cache_key = ?",
            (time.time() - 3601, key),
        )
    assert reasoner._get_cached(key) is None


def test_invalidate_removes_cached_entry(reasoner, fake_action, snapshot):
    """`invalidate(snapshot)` must remove the matching cached row so
    subsequent calls don't return the known-bad cached action."""
    key = reasoner._cache_key(
        snapshot["url"], snapshot["page_text_preview"], snapshot["dialog_text"],
        snapshot["fields"], snapshot["buttons"],
    )
    reasoner._set_cache(key, fake_action)
    assert reasoner._get_cached(key) is not None

    rows_removed = reasoner.invalidate(snapshot)
    assert rows_removed == 1
    assert reasoner._get_cached(key) is None


# ── reason_sync integration ──────────────────────────────────────────────


def test_reason_sync_short_circuits_on_cache_hit(
    monkeypatch: pytest.MonkeyPatch, reasoner, snapshot, fake_action,
):
    """Pre-populate the cache, then call reason_sync — _call_llm must
    NOT fire because the cache short-circuits before the LLM."""
    key = reasoner._cache_key(
        snapshot["url"], snapshot["page_text_preview"], snapshot["dialog_text"],
        snapshot["fields"], snapshot["buttons"],
    )
    reasoner._set_cache(key, fake_action)

    llm_calls = {"count": 0}

    def _spy_llm(prompt: str) -> PageAction:
        llm_calls["count"] += 1
        return fake_action  # would never be reached on cache hit

    monkeypatch.setattr(reasoner, "_call_llm", _spy_llm)

    result = reasoner.reason_sync(snapshot)
    assert result.action == fake_action.action
    assert llm_calls["count"] == 0, "LLM must NOT be called on cache hit"


def test_reason_sync_calls_llm_on_cache_miss_then_caches(
    monkeypatch: pytest.MonkeyPatch, reasoner, snapshot, fake_action,
):
    """First call: cache miss → LLM fires → result cached.
    Second call (same snapshot): cache hit → LLM does NOT fire."""
    llm_calls = {"count": 0}

    def _spy_llm(prompt: str) -> PageAction:
        llm_calls["count"] += 1
        return fake_action

    monkeypatch.setattr(reasoner, "_call_llm", _spy_llm)

    r1 = reasoner.reason_sync(snapshot)
    assert llm_calls["count"] == 1
    assert r1.action == fake_action.action

    r2 = reasoner.reason_sync(snapshot)
    assert llm_calls["count"] == 1, "second call must hit cache, not LLM"
    assert r2.action == fake_action.action


def test_reason_sync_calls_llm_on_changed_snapshot(
    monkeypatch: pytest.MonkeyPatch, reasoner, snapshot, fake_action,
):
    """Modifying the snapshot's fields/buttons changes the cache key →
    second call is a cache miss and fires the LLM again."""
    llm_calls = {"count": 0}

    def _spy_llm(prompt: str) -> PageAction:
        llm_calls["count"] += 1
        return fake_action

    monkeypatch.setattr(reasoner, "_call_llm", _spy_llm)

    reasoner.reason_sync(snapshot)
    assert llm_calls["count"] == 1

    altered = dict(snapshot)
    altered["fields"] = snapshot["fields"] + [{"label": "Phone", "type": "text"}]
    reasoner.reason_sync(altered)
    assert llm_calls["count"] == 2, "different snapshot must miss the cache"
