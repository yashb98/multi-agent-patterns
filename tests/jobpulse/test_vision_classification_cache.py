"""Item 11 — vision_classification_cache stash-drill.

Cache key is ``(domain, content_hash)`` where ``content_hash`` is
derived from stable DOM features (URL + title + text head + field
count + button labels). Pixel-level screenshot diffs deliberately do
NOT bump the key — that was the audit's reason this cache was
deferred.

TTL is 1 hour (matches PageReasoner). Hit_count increments. Test mode
short-circuits without an explicit JobDB.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jobpulse.form_models import PageType
from jobpulse.job_db import JobDB
from jobpulse.page_analyzer import (
    _VISION_CACHE_TTL_SECONDS,
    _vision_cache_key_for,
    _vision_classification_cache_lookup,
    _vision_classification_cache_store,
)


@pytest.fixture
def tmp_jobdb(tmp_path, monkeypatch):
    db_path = tmp_path / "applications.db"
    monkeypatch.setattr("jobpulse.job_db.DEFAULT_DB_PATH", db_path)
    return JobDB(db_path)


def test_cache_key_ignores_pixel_noise():
    """Two snapshots with identical DOM but different cursor / scroll
    state must hash to the same content_hash."""

    base = {
        "url": "https://example.com/apply",
        "title": "Apply",
        "page_text": "Application form",
        "fields": [{"label": "Email"}, {"label": "Phone"}],
        "buttons": [{"label": "Submit"}, {"label": "Cancel"}],
    }
    twin = dict(base)
    # Mutate fields the cache key deliberately ignores.
    twin["scroll_y"] = 1234
    twin["cursor_x"] = 99
    d1, h1 = _vision_cache_key_for(base)
    d2, h2 = _vision_cache_key_for(twin)
    assert (d1, h1) == (d2, h2)


def test_cache_key_changes_on_field_count_change():
    a = {"url": "https://example.com/apply", "fields": [{"label": "Email"}]}
    b = {"url": "https://example.com/apply", "fields": [{"label": "Email"}, {"label": "Phone"}]}
    _, ha = _vision_cache_key_for(a)
    _, hb = _vision_cache_key_for(b)
    assert ha != hb


def test_lookup_miss_then_store_then_hit(tmp_jobdb):
    domain = "greenhouse.io"
    content = "abc123"
    assert _vision_classification_cache_lookup(domain, content, db=tmp_jobdb) is None
    _vision_classification_cache_store(
        domain, content, PageType.APPLICATION_FORM, 0.9, db=tmp_jobdb,
    )
    cached = _vision_classification_cache_lookup(domain, content, db=tmp_jobdb)
    assert cached is not None
    page_type, confidence = cached
    assert page_type == PageType.APPLICATION_FORM
    assert pytest.approx(confidence, abs=1e-9) == 0.9


def test_low_confidence_results_are_not_stored(tmp_jobdb):
    domain = "greenhouse.io"
    content = "low_conf_key"
    _vision_classification_cache_store(
        domain, content, PageType.UNKNOWN, 0.3, db=tmp_jobdb,
    )
    assert _vision_classification_cache_lookup(domain, content, db=tmp_jobdb) is None


def test_ttl_expired_returns_miss(tmp_jobdb):
    domain = "greenhouse.io"
    content = "ttl_key"
    _vision_classification_cache_store(
        domain, content, PageType.LOGIN_FORM, 0.85, db=tmp_jobdb,
    )
    expired = (datetime.now() - timedelta(seconds=_VISION_CACHE_TTL_SECONDS + 5)).isoformat()
    conn = tmp_jobdb._connect()
    conn.execute(
        "UPDATE vision_classification_cache SET generated_at = ? "
        "WHERE domain = ? AND content_hash = ?",
        (expired, domain, content),
    )
    conn.commit()
    assert _vision_classification_cache_lookup(domain, content, db=tmp_jobdb) is None


def test_test_mode_short_circuits(monkeypatch):
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")
    _vision_classification_cache_store(
        "greenhouse.io", "key", PageType.APPLICATION_FORM, 0.9,
    )
    assert _vision_classification_cache_lookup("greenhouse.io", "key") is None
