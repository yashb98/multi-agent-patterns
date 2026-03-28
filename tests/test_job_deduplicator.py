"""Tests for jobpulse/job_deduplicator.py — TDD.

Covers:
  - Exact URL (job_id) match is filtered out
  - Fuzzy company + title: exact match filtered, low overlap not filtered
  - Different company, same title: not filtered
  - Empty input returns empty output
"""

from datetime import datetime

import pytest


@pytest.fixture()
def db(tmp_path):
    from jobpulse.job_db import JobDB

    return JobDB(tmp_path / "test_dedup.db")


@pytest.fixture()
def make_listing():
    from jobpulse.models.application_models import JobListing

    def _make(
        job_id: str = "abc",
        title: str = "Data Scientist",
        company: str = "Barclays",
        url: str = "https://example.com/1",
    ) -> JobListing:
        return JobListing(
            job_id=job_id,
            title=title,
            company=company,
            platform="linkedin",
            url=url,
            location="London",
            description_raw="...",
            found_at=datetime.now(),
        )

    return _make


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dedup_exact_url(db, make_listing):
    """Same job_id is filtered out; a listing with a different job_id passes through."""
    existing = make_listing(job_id="abc")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    incoming = [
        make_listing(job_id="abc"),
        make_listing(job_id="def", title="Software Engineer", company="HSBC", url="https://example.com/2"),
    ]

    from jobpulse.job_deduplicator import deduplicate

    result = deduplicate(incoming, db)

    assert len(result) == 1
    assert result[0].job_id == "def"


def test_dedup_fuzzy_company_title(db, make_listing):
    """Same company + exact title is filtered; low word-overlap title is not filtered."""
    existing = make_listing(job_id="abc", title="Data Scientist", company="Barclays")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    from jobpulse.job_deduplicator import deduplicate

    # "Junior Data Scientist" vs "Data Scientist"
    # Words in incoming: {"junior", "data", "scientist"}  (3 words)
    # Words in existing: {"data", "scientist"}            (2 words)
    # Intersection: {"data", "scientist"} = 2
    # Union: {"junior", "data", "scientist"} = 3
    # Overlap = 2/3 ≈ 0.67 < 0.8 → NOT filtered
    incoming = [
        make_listing(
            job_id="def",
            title="Junior Data Scientist",
            company="Barclays",
            url="https://example.com/2",
        )
    ]
    result = deduplicate(incoming, db)
    assert len(result) == 1

    # Exact same title at the same company → filtered
    incoming2 = [
        make_listing(
            job_id="ghi",
            title="Data Scientist",
            company="Barclays",
            url="https://example.com/3",
        )
    ]
    result2 = deduplicate(incoming2, db)
    assert len(result2) == 0


def test_dedup_different_company_same_title(db, make_listing):
    """Same title at a different company is NOT filtered."""
    existing = make_listing(job_id="abc", title="Data Scientist", company="Barclays")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    from jobpulse.job_deduplicator import deduplicate

    incoming = [
        make_listing(
            job_id="def",
            title="Data Scientist",
            company="HSBC",
            url="https://example.com/2",
        )
    ]
    result = deduplicate(incoming, db)
    assert len(result) == 1


def test_dedup_empty_list(db):
    """Empty input returns empty output without touching the db."""
    from jobpulse.job_deduplicator import deduplicate

    assert deduplicate([], db) == []
