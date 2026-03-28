"""Tests for JobDB SQLite storage layer.

All tests use tmp_path so they NEVER touch the production database.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from jobpulse.job_db import JobDB
from jobpulse.models.application_models import JobListing


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_listing(
    job_id: str = "abc123",
    title: str = "Software Engineer",
    company: str = "Acme Ltd",
    platform: str = "linkedin",
    url: str = "https://linkedin.com/jobs/123",
    **kwargs,
) -> JobListing:
    """Return a minimal valid JobListing."""
    return JobListing(
        job_id=job_id,
        title=title,
        company=company,
        platform=platform,
        url=url,
        location="London",
        description_raw="We are looking for a software engineer...",
        found_at=kwargs.pop("found_at", datetime(2026, 3, 28, 9, 0, 0, tzinfo=timezone.utc)),
        **kwargs,
    )


@pytest.fixture
def db(tmp_path: Path) -> JobDB:
    """Isolated JobDB backed by a fresh tmp_path database."""
    return JobDB(db_path=tmp_path / "test_applications.db")


# ---------------------------------------------------------------------------
# 1. save_and_get_listing
# ---------------------------------------------------------------------------

def test_save_and_get_listing(db: JobDB) -> None:
    """Save a JobListing, retrieve it, and verify all fields round-trip."""
    listing = _make_listing(
        salary_min=35000.0,
        salary_max=50000.0,
        remote=True,
        seniority="mid",
        required_skills=["Python", "Django"],
        preferred_skills=["Docker"],
        ats_platform="Greenhouse",
        easy_apply=False,
    )
    db.save_listing(listing)

    row = db.get_listing("abc123")
    assert row is not None
    assert row["job_id"] == "abc123"
    assert row["title"] == "Software Engineer"
    assert row["company"] == "Acme Ltd"
    assert row["platform"] == "linkedin"
    assert row["url"] == "https://linkedin.com/jobs/123"
    assert row["salary_min"] == 35000.0
    assert row["salary_max"] == 50000.0
    assert row["location"] == "London"
    assert bool(row["remote"]) is True
    assert row["seniority"] == "mid"
    assert json.loads(row["required_skills"]) == ["Python", "Django"]
    assert json.loads(row["preferred_skills"]) == ["Docker"]
    assert row["ats_platform"] == "Greenhouse"
    assert bool(row["easy_apply"]) is False


# ---------------------------------------------------------------------------
# 2. save_listing_duplicate_is_upsert
# ---------------------------------------------------------------------------

def test_save_listing_duplicate_is_upsert(db: JobDB) -> None:
    """Saving the same listing twice keeps count at 1 (INSERT OR REPLACE)."""
    listing = _make_listing()
    db.save_listing(listing)
    db.save_listing(listing)  # duplicate
    assert db.count_listings() == 1


# ---------------------------------------------------------------------------
# 3. listing_exists
# ---------------------------------------------------------------------------

def test_listing_exists(db: JobDB) -> None:
    """listing_exists returns False before save and True after."""
    assert db.listing_exists("abc123") is False
    db.save_listing(_make_listing())
    assert db.listing_exists("abc123") is True


# ---------------------------------------------------------------------------
# 4. save_and_get_application
# ---------------------------------------------------------------------------

def test_save_and_get_application(db: JobDB) -> None:
    """Save an application with all optional fields and retrieve it."""
    db.save_listing(_make_listing())
    db.save_application(
        job_id="abc123",
        status="Applied",
        ats_score=87.5,
        match_tier="auto",
        matched_projects=["my-api", "ml-demo"],
        cv_path="/tmp/cv.pdf",
        cover_letter_path="/tmp/cl.pdf",
        applied_at="2026-03-28T10:00:00",
        notion_page_id="notion-page-42",
        follow_up_date="2026-04-07",
        custom_answers={"What is your experience?": "3 years"},
    )

    row = db.get_application("abc123")
    assert row is not None
    assert row["job_id"] == "abc123"
    assert row["status"] == "Applied"
    assert row["ats_score"] == 87.5
    assert row["match_tier"] == "auto"
    assert json.loads(row["matched_projects"]) == ["my-api", "ml-demo"]
    assert row["cv_path"] == "/tmp/cv.pdf"
    assert row["cover_letter_path"] == "/tmp/cl.pdf"
    assert row["applied_at"] == "2026-03-28T10:00:00"
    assert row["notion_page_id"] == "notion-page-42"
    assert row["follow_up_date"] == "2026-04-07"
    assert json.loads(row["custom_answers"]) == {"What is your experience?": "3 years"}


# ---------------------------------------------------------------------------
# 5. update_application_status
# ---------------------------------------------------------------------------

def test_update_application_status(db: JobDB) -> None:
    """update_status changes the status and logs an event."""
    db.save_listing(_make_listing())
    db.save_application("abc123", status="Found")

    db.update_status("abc123", "Interview")

    row = db.get_application("abc123")
    assert row["status"] == "Interview"

    events = db.get_events("abc123")
    assert len(events) >= 1
    status_events = [e for e in events if e["event_type"] == "status_change"]
    assert status_events, "Expected a status_change event"
    ev = status_events[0]
    assert ev["old_value"] == "Found"
    assert ev["new_value"] == "Interview"


# ---------------------------------------------------------------------------
# 6. log_event
# ---------------------------------------------------------------------------

def test_log_event(db: JobDB) -> None:
    """Log a custom event and verify it can be retrieved with correct fields."""
    db.save_listing(_make_listing())
    db.save_application("abc123")

    db.log_event(
        job_id="abc123",
        event_type="cv_generated",
        old_value="",
        new_value="/tmp/cv.pdf",
        details="Tailored CV generated by L5 forge",
    )

    events = db.get_events("abc123")
    assert len(events) == 1
    ev = events[0]
    assert ev["job_id"] == "abc123"
    assert ev["event_type"] == "cv_generated"
    assert ev["old_value"] == ""
    assert ev["new_value"] == "/tmp/cv.pdf"
    assert ev["details"] == "Tailored CV generated by L5 forge"
    assert ev["created_at"] is not None


# ---------------------------------------------------------------------------
# 7. get_applications_by_status
# ---------------------------------------------------------------------------

def test_get_applications_by_status(db: JobDB) -> None:
    """get_applications_by_status filters correctly and JOIN includes listing fields."""
    db.save_listing(_make_listing(job_id="job1", title="Backend Engineer"))
    db.save_listing(_make_listing(job_id="job2", title="Frontend Engineer"))
    db.save_listing(_make_listing(job_id="job3", title="DevOps Engineer"))

    db.save_application("job1", status="Applied")
    db.save_application("job2", status="Found")
    db.save_application("job3", status="Applied")

    applied = db.get_applications_by_status("Applied")
    assert len(applied) == 2
    ids = {r["job_id"] for r in applied}
    assert ids == {"job1", "job3"}

    # JOIN check — listing title should be accessible
    titles = {r["title"] for r in applied}
    assert "Backend Engineer" in titles
    assert "DevOps Engineer" in titles

    found = db.get_applications_by_status("Found")
    assert len(found) == 1
    assert found[0]["job_id"] == "job2"


# ---------------------------------------------------------------------------
# 8. get_follow_ups_due
# ---------------------------------------------------------------------------

def test_get_follow_ups_due(db: JobDB) -> None:
    """get_follow_ups_due returns applications with matching follow_up_date AND status=Applied."""
    target = date(2026, 4, 7)

    db.save_listing(_make_listing(job_id="job1"))
    db.save_listing(_make_listing(job_id="job2"))
    db.save_listing(_make_listing(job_id="job3"))

    # This one should be returned — Applied + correct follow-up date
    db.save_application("job1", status="Applied", follow_up_date="2026-04-07")
    # Wrong date — should not be returned
    db.save_application("job2", status="Applied", follow_up_date="2026-04-10")
    # Right date but wrong status — should not be returned
    db.save_application("job3", status="Found", follow_up_date="2026-04-07")

    due = db.get_follow_ups_due(target)
    assert len(due) == 1
    assert due[0]["job_id"] == "job1"


# ---------------------------------------------------------------------------
# 9. fuzzy_company_title_exists
# ---------------------------------------------------------------------------

def test_fuzzy_company_title_exists(db: JobDB) -> None:
    """
    fuzzy_match_exists returns True for same company + near-identical title,
    False for same company + very different title, False for different company.
    """
    # Save a listing and application (not Skipped/Withdrawn)
    db.save_listing(_make_listing(
        job_id="job1",
        title="Python Backend Engineer",
        company="Acme Ltd",
        found_at=datetime(2026, 3, 20, tzinfo=timezone.utc),
    ))
    db.save_application("job1", status="Found")

    # Same company, title with >80% word overlap — should match
    assert db.fuzzy_match_exists("Acme Ltd", "Python Backend Engineer") is True

    # Same company, very different title — should not match
    assert db.fuzzy_match_exists("Acme Ltd", "Marketing Director") is False

    # Different company, same title — should not match
    assert db.fuzzy_match_exists("Other Corp", "Python Backend Engineer") is False

    # Case-insensitive company match
    assert db.fuzzy_match_exists("acme ltd", "Python Backend Engineer") is True


# ---------------------------------------------------------------------------
# 10. cache_answer
# ---------------------------------------------------------------------------

def test_cache_answer(db: JobDB) -> None:
    """cache_answer stores an answer; get_cached_answer retrieves it."""
    db.cache_answer("Do you have the right to work in the UK?", "Yes")
    result = db.get_cached_answer("Do you have the right to work in the UK?")
    assert result == "Yes"


# ---------------------------------------------------------------------------
# 11. cache_answer_miss
# ---------------------------------------------------------------------------

def test_cache_answer_miss(db: JobDB) -> None:
    """get_cached_answer returns None for an unknown question."""
    result = db.get_cached_answer("What is the meaning of life?")
    assert result is None


# ---------------------------------------------------------------------------
# 12. today_stats
# ---------------------------------------------------------------------------

def test_today_stats(db: JobDB) -> None:
    """get_today_stats counts applied/found/skipped today and computes avg_ats."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

    db.save_listing(_make_listing(job_id="j1"))
    db.save_listing(_make_listing(job_id="j2"))
    db.save_listing(_make_listing(job_id="j3"))

    # Applied today
    db.save_application("j1", status="Applied", ats_score=90.0, applied_at=today_str)
    # Found today (no applied_at)
    db.save_application("j2", status="Found")
    # Skipped today
    db.save_application("j3", status="Skipped")

    stats = db.get_today_stats()
    assert stats["applied"] >= 1
    assert stats["found"] >= 1
    assert stats["skipped"] >= 1
    assert stats["avg_ats"] >= 0.0
