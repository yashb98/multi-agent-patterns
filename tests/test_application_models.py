"""Tests for Job Autopilot Pydantic models.

Tests follow TDD pattern — written before implementation.
All tests are pure model validation; no DB or API calls required.
"""

from datetime import datetime, date, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobpulse.models.application_models import (
    ApplicationRecord,
    ApplicationStatus,
    ATSScore,
    JobListing,
    SearchConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_job_listing() -> dict:
    """Minimum valid payload for a JobListing."""
    return {
        "job_id": "abc123",
        "title": "Software Engineer",
        "company": "Acme Ltd",
        "platform": "linkedin",
        "url": "https://linkedin.com/jobs/123",
        "location": "London, UK",
        "description_raw": "We are looking for a software engineer...",
        "found_at": datetime(2026, 3, 28, 9, 0, 0, tzinfo=timezone.utc),
    }


# ---------------------------------------------------------------------------
# 1. test_job_listing_minimal
# ---------------------------------------------------------------------------

def test_job_listing_minimal():
    """Only required fields supplied; verify all defaults are correct."""
    data = _minimal_job_listing()
    job = JobListing(**data)

    assert job.job_id == "abc123"
    assert job.title == "Software Engineer"
    assert job.company == "Acme Ltd"
    assert job.platform == "linkedin"
    assert job.url == "https://linkedin.com/jobs/123"
    assert job.location == "London, UK"
    assert job.description_raw == "We are looking for a software engineer..."

    # Defaults
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.remote is False
    assert job.seniority is None
    assert job.required_skills == []
    assert job.preferred_skills == []
    assert job.ats_platform is None
    assert job.easy_apply is False


# ---------------------------------------------------------------------------
# 2. test_job_listing_full
# ---------------------------------------------------------------------------

def test_job_listing_full():
    """All fields populated; verify correct storage."""
    data = _minimal_job_listing()
    data.update(
        {
            "salary_min": 40000.0,
            "salary_max": 60000.0,
            "remote": True,
            "seniority": "mid",
            "required_skills": ["Python", "FastAPI"],
            "preferred_skills": ["Docker"],
            "ats_platform": "Workday",
            "easy_apply": True,
        }
    )
    job = JobListing(**data)

    assert job.salary_min == 40000.0
    assert job.salary_max == 60000.0
    assert job.remote is True
    assert job.seniority == "mid"
    assert job.required_skills == ["Python", "FastAPI"]
    assert job.preferred_skills == ["Docker"]
    assert job.ats_platform == "Workday"
    assert job.easy_apply is True


# ---------------------------------------------------------------------------
# 3. test_job_listing_invalid_platform
# ---------------------------------------------------------------------------

def test_job_listing_invalid_platform():
    """'monster' is not a valid platform — must raise ValidationError."""
    data = _minimal_job_listing()
    data["platform"] = "monster"
    with pytest.raises(ValidationError):
        JobListing(**data)


# ---------------------------------------------------------------------------
# 4. test_application_status_enum
# ---------------------------------------------------------------------------

def test_application_status_enum():
    """Verify key ApplicationStatus enum values exist and have correct string values."""
    assert ApplicationStatus.FOUND == "Found"
    assert ApplicationStatus.APPLIED == "Applied"
    assert ApplicationStatus.INTERVIEW == "Interview"
    assert ApplicationStatus.OFFER == "Offer"
    assert ApplicationStatus.REJECTED == "Rejected"
    assert ApplicationStatus.WITHDRAWN == "Withdrawn"
    assert ApplicationStatus.SKIPPED == "Skipped"
    assert ApplicationStatus.PENDING_APPROVAL == "Pending Approval"
    assert ApplicationStatus.ANALYZING == "Analyzing"
    assert ApplicationStatus.READY == "Ready"


# ---------------------------------------------------------------------------
# 5. test_application_record_defaults
# ---------------------------------------------------------------------------

def test_application_record_defaults():
    """ApplicationRecord created with just a job; verify all defaults."""
    job = JobListing(**_minimal_job_listing())
    record = ApplicationRecord(job=job)

    assert record.status == ApplicationStatus.FOUND
    assert record.ats_score == 0.0
    assert record.match_tier == "skip"
    assert record.matched_projects == []
    assert record.cv_path is None
    assert record.cover_letter_path is None
    assert record.applied_at is None
    assert record.notion_page_id is None
    assert record.follow_up_date is None
    assert record.custom_answers == {}


# ---------------------------------------------------------------------------
# 6. test_ats_score_pass
# ---------------------------------------------------------------------------

def test_ats_score_pass():
    """ATSScore with total >= 95 should have passed=True."""
    score = ATSScore(
        total=96.5,
        keyword_score=65.0,
        section_score=20.0,
        format_score=10.0,
        missing_keywords=[],
        matched_keywords=["Python", "FastAPI"],
    )
    assert score.passed is True
    assert score.total == 96.5


# ---------------------------------------------------------------------------
# 7. test_ats_score_fail
# ---------------------------------------------------------------------------

def test_ats_score_fail():
    """ATSScore with total < 95 should have passed=False."""
    score = ATSScore(
        total=80.0,
        keyword_score=55.0,
        section_score=15.0,
        format_score=10.0,
        missing_keywords=["Docker", "Kubernetes"],
        matched_keywords=["Python"],
    )
    assert score.passed is False
    assert score.missing_keywords == ["Docker", "Kubernetes"]


# ---------------------------------------------------------------------------
# 8. test_search_config_defaults
# ---------------------------------------------------------------------------

def test_search_config_defaults():
    """SearchConfig with only required fields; verify all defaults."""
    config = SearchConfig(titles=["Software Engineer", "Backend Developer"])

    assert config.titles == ["Software Engineer", "Backend Developer"]
    assert config.location == "United Kingdom"
    assert config.include_remote is True
    assert config.salary_min == 27000
    assert config.salary_max is None
    assert config.exclude_companies == []
    # Default excluded keywords should include seniority filters
    assert "senior" in config.exclude_keywords
    assert "lead" in config.exclude_keywords
    assert "principal" in config.exclude_keywords
    assert "staff" in config.exclude_keywords
    assert "director" in config.exclude_keywords


# ---------------------------------------------------------------------------
# 9. test_search_config_custom
# ---------------------------------------------------------------------------

def test_search_config_custom():
    """SearchConfig with custom exclusions overrides defaults."""
    config = SearchConfig(
        titles=["ML Engineer"],
        location="Manchester",
        include_remote=False,
        salary_min=35000.0,
        salary_max=55000.0,
        exclude_companies=["Bad Corp", "Skip Me Ltd"],
        exclude_keywords=["manager", "head of"],
    )

    assert config.location == "Manchester"
    assert config.include_remote is False
    assert config.salary_min == 35000.0
    assert config.salary_max == 55000.0
    assert config.exclude_companies == ["Bad Corp", "Skip Me Ltd"]
    assert config.exclude_keywords == ["manager", "head of"]
    # Custom list should NOT include the defaults
    assert "senior" not in config.exclude_keywords
