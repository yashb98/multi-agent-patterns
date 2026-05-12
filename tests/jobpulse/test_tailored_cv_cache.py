"""Regression tests for the tailored-CV cache (cache-llm-S4).

Per `docs/audits/cache-llm-catalog.md` §D, `cv_tailor.tailor_all_sections`
previously fired 4 LLM calls on every JD with no cache. S4 adds a
`(role_archetype, jd_hash, profile_version)` cache so the same JD with
the same profile pulls the cached TailoredCV without invoking any LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

import jobpulse.cv_tailor as ct
from jobpulse.cv_tailor import (
    TailoredCV,
    TailoredCoverLetter,
    TailoredHeader,
    _classify_role_archetype,
    _jd_hash,
    _profile_version_hash,
    _tailored_cv_cache_lookup,
    _tailored_cv_cache_store,
    _tailored_cv_from_payload,
    _tailored_cv_to_payload,
    tailor_all_sections,
)
from jobpulse.job_db import JobDB
from shared.profile_store import ExperienceEntry


# ── Stub listing object (mirrors the JobListing fields cv_tailor reads) ──


@dataclass
class _StubListing:
    title: str
    company: str
    description_raw: str
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)


@pytest.fixture
def isolated_db(tmp_path: Path):
    db_path = tmp_path / "applications.db"
    db = JobDB(db_path=db_path)
    yield db
    db.close()


@pytest.fixture
def stub_listing() -> _StubListing:
    return _StubListing(
        title="Senior Data Engineer",
        company="Anthropic",
        description_raw="Build data pipelines for the Knowledge Team. "
                        "Python, SQL, distributed systems, ML.",
        required_skills=["python", "sql", "spark"],
        preferred_skills=["airflow", "dbt"],
    )


@pytest.fixture
def stub_experience() -> list[ExperienceEntry]:
    return [
        ExperienceEntry(
            title="Co-op Team Leader", company="Co-op",
            dates="2025-Present", bullets=["led 6 staff", "drove conversion"],
        ),
    ]


@pytest.fixture
def stub_projects() -> list[dict]:
    return [
        {"name": "JobPulse", "description": "Multi-agent system",
         "url": "https://github.com/x", "matched_skills": ["python", "ml"]},
    ]


@pytest.fixture
def fully_tailored_cv() -> TailoredCV:
    return TailoredCV(
        tagline="MSc | 2+ YOE | Data Engineer | Python",
        summary="<b>Senior Data Engineer</b> with strong Python and SQL.",
        experience=[
            ExperienceEntry(
                title="Co-op Team Leader", company="Co-op",
                dates="2025-Present", bullets=["led", "drove"],
            ),
        ],
        projects=[{"name": "JobPulse", "bullets": ["built", "shipped"]}],
        cover_letter=TailoredCoverLetter(
            intro="i.", hook="h.", closing="c.",
        ),
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def test_classify_role_archetype_collapses_variants():
    """Same archetype across casual title variants — keeps cache key stable."""
    assert _classify_role_archetype("Senior Data Engineer") == "data_engineer"
    assert _classify_role_archetype("Lead Data Engineer") == "data_engineer"
    assert _classify_role_archetype("ML Engineer") == "ml_engineer"
    assert _classify_role_archetype("AI Engineer") == "ml_engineer"
    assert _classify_role_archetype("Research Engineer, Knowledge Team") == "research_engineer"


def test_jd_hash_is_order_independent_for_skills(stub_listing):
    """Skill reordering doesn't invalidate the cache."""
    h1 = _jd_hash(stub_listing)
    flipped = _StubListing(
        title=stub_listing.title, company=stub_listing.company,
        description_raw=stub_listing.description_raw,
        required_skills=list(reversed(stub_listing.required_skills)),
        preferred_skills=list(reversed(stub_listing.preferred_skills)),
    )
    assert _jd_hash(flipped) == h1


def test_jd_hash_changes_when_description_changes(stub_listing):
    h1 = _jd_hash(stub_listing)
    stub_listing.description_raw += " Now also requires Kafka."
    assert _jd_hash(stub_listing) != h1


def test_profile_version_hash_changes_with_new_project(stub_experience, stub_projects):
    h1 = _profile_version_hash(stub_experience, stub_projects)
    stub_projects.append({"name": "DataMind", "matched_skills": ["python"]})
    assert _profile_version_hash(stub_experience, stub_projects) != h1


def test_payload_round_trip(fully_tailored_cv):
    """Serialise → deserialise → equal values (modulo dataclass identity)."""
    payload = _tailored_cv_to_payload(fully_tailored_cv)
    restored = _tailored_cv_from_payload(payload)
    assert restored.tagline == fully_tailored_cv.tagline
    assert restored.summary == fully_tailored_cv.summary
    assert restored.experience[0].title == fully_tailored_cv.experience[0].title
    assert restored.projects == fully_tailored_cv.projects
    assert restored.cover_letter.intro == fully_tailored_cv.cover_letter.intro


# ── Cache lookup / store ──────────────────────────────────────────────────


def test_cache_miss_then_hit(isolated_db, fully_tailored_cv):
    key = ("data_engineer", "abc123", "def456")
    assert _tailored_cv_cache_lookup(*key, db=isolated_db) is None

    _tailored_cv_cache_store(*key, fully_tailored_cv, db=isolated_db)

    hit = _tailored_cv_cache_lookup(*key, db=isolated_db)
    assert hit is not None
    assert hit.tagline == fully_tailored_cv.tagline
    assert hit.experience[0].title == fully_tailored_cv.experience[0].title


def test_cache_keyed_by_all_three_fields(isolated_db, fully_tailored_cv):
    """Different (role, jd, profile) tuples don't collide."""
    _tailored_cv_cache_store("data_engineer", "jd1", "p1", fully_tailored_cv, db=isolated_db)
    cv2 = TailoredCV(
        tagline="t2", summary="s2", experience=fully_tailored_cv.experience,
        projects=fully_tailored_cv.projects, cover_letter=fully_tailored_cv.cover_letter,
    )
    _tailored_cv_cache_store("ml_engineer", "jd1", "p1", cv2, db=isolated_db)

    hit_de = _tailored_cv_cache_lookup("data_engineer", "jd1", "p1", db=isolated_db)
    hit_ml = _tailored_cv_cache_lookup("ml_engineer", "jd1", "p1", db=isolated_db)
    assert hit_de.tagline == fully_tailored_cv.tagline
    assert hit_ml.tagline == "t2"


def test_cache_ttl_expiry(isolated_db, fully_tailored_cv):
    from datetime import datetime, timedelta
    _tailored_cv_cache_store("data_engineer", "jd1", "p1", fully_tailored_cv, db=isolated_db)
    expired = (datetime.now() - timedelta(days=ct._TAILORED_CV_CACHE_TTL_DAYS + 1)).isoformat()
    conn = isolated_db._connect()
    conn.execute(
        "UPDATE tailored_cv_cache SET generated_at = ? "
        "WHERE role_archetype = 'data_engineer'",
        (expired,),
    )
    conn.commit()
    assert _tailored_cv_cache_lookup("data_engineer", "jd1", "p1", db=isolated_db) is None


# ── tailor_all_sections integration ──────────────────────────────────────


def test_tailor_all_sections_caches_and_skips_llm_on_repeat(
    monkeypatch: pytest.MonkeyPatch, isolated_db,
    stub_listing, stub_experience, stub_projects, fully_tailored_cv,
):
    """First call runs the 4 tailor_* functions; second call hits the
    cache and DOES NOT invoke any of them."""

    # Route the cache helpers at the tmp DB
    _orig_lookup = ct._tailored_cv_cache_lookup
    _orig_store = ct._tailored_cv_cache_store
    monkeypatch.setattr(
        ct, "_tailored_cv_cache_lookup",
        lambda r, j, p, *, db=None: _orig_lookup(r, j, p, db=isolated_db),
    )
    monkeypatch.setattr(
        ct, "_tailored_cv_cache_store",
        lambda r, j, p, cv, *, db=None: _orig_store(r, j, p, cv, db=isolated_db),
    )

    # Stub the 4 LLM-calling tailor_* functions to count invocations
    counters = {
        "header": 0, "experience": 0, "projects": 0, "cover_letter": 0,
    }

    def _h(*a, **kw):
        counters["header"] += 1
        return TailoredHeader(
            tagline=fully_tailored_cv.tagline, summary=fully_tailored_cv.summary,
        )

    def _e(*a, **kw):
        counters["experience"] += 1
        return fully_tailored_cv.experience

    def _p(*a, **kw):
        counters["projects"] += 1
        return fully_tailored_cv.projects

    def _c(*a, **kw):
        counters["cover_letter"] += 1
        return fully_tailored_cv.cover_letter

    monkeypatch.setattr(ct, "tailor_summary_and_tagline", _h)
    monkeypatch.setattr(ct, "tailor_experience_bullets", _e)
    monkeypatch.setattr(ct, "tailor_project_bullets", _p)
    monkeypatch.setattr(ct, "tailor_cover_letter_prose", _c)

    # First call: cache miss → all 4 stubs fire
    cv1 = tailor_all_sections(stub_listing, stub_projects, stub_experience)
    assert cv1.tagline == fully_tailored_cv.tagline
    assert counters == {"header": 1, "experience": 1, "projects": 1, "cover_letter": 1}

    # Second call: cache hit → no stubs fire
    cv2 = tailor_all_sections(stub_listing, stub_projects, stub_experience)
    assert cv2.tagline == fully_tailored_cv.tagline
    assert counters == {"header": 1, "experience": 1, "projects": 1, "cover_letter": 1}, \
        "tailor_* should NOT have been called on cache hit"


def test_partial_failure_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, isolated_db,
    stub_listing, stub_experience, stub_projects, fully_tailored_cv,
):
    """If one of the 4 tailor_* functions returns None (validation failure),
    the partial CV must NOT be persisted — otherwise the next 14 days serve
    a half-tailored CV from cache."""
    _orig_lookup = ct._tailored_cv_cache_lookup
    _orig_store = ct._tailored_cv_cache_store
    monkeypatch.setattr(
        ct, "_tailored_cv_cache_lookup",
        lambda r, j, p, *, db=None: _orig_lookup(r, j, p, db=isolated_db),
    )
    store_calls = {"count": 0}

    def _store(r, j, p, cv, *, db=None):
        store_calls["count"] += 1
        return _orig_store(r, j, p, cv, db=isolated_db)

    monkeypatch.setattr(ct, "_tailored_cv_cache_store", _store)

    # Header returns None (validation failure). Other sections succeed.
    monkeypatch.setattr(
        ct, "tailor_summary_and_tagline", lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        ct, "tailor_experience_bullets", lambda *a, **kw: fully_tailored_cv.experience,
    )
    monkeypatch.setattr(
        ct, "tailor_project_bullets", lambda *a, **kw: fully_tailored_cv.projects,
    )
    monkeypatch.setattr(
        ct, "tailor_cover_letter_prose", lambda *a, **kw: fully_tailored_cv.cover_letter,
    )

    cv = tailor_all_sections(stub_listing, stub_projects, stub_experience)
    assert cv.tagline is None  # partial result returned to caller
    assert store_calls["count"] == 0, "partial CV must not be cached"
