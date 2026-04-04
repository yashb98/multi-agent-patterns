"""Tests for jobpulse/job_notion_sync.py — payload building only, no API calls."""

from datetime import date, datetime

import pytest
from jobpulse.job_notion_sync import (
    build_create_payload,
    build_update_payload,
    platform_display,
)
from jobpulse.models.application_models import JobListing

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_job() -> JobListing:
    return JobListing(
        job_id="abc",
        title="Data Scientist",
        company="Barclays",
        platform="linkedin",
        url="https://linkedin.com/jobs/123",
        salary_min=30000,
        salary_max=35000,
        location="London",
        remote=False,
        seniority="junior",
        required_skills=["python", "sql"],
        description_raw="...",
        ats_platform="greenhouse",
        found_at=datetime(2026, 3, 28, 7, 0, 0),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_build_create_payload(sample_job: JobListing) -> None:
    payload = build_create_payload(sample_job, "fake_db_id")
    props = payload["properties"]

    assert props["Company"]["title"][0]["text"]["content"] == "Barclays"
    assert props["Role"]["rich_text"][0]["text"]["content"] == "Data Scientist"
    assert props["Platform"]["select"]["name"] == "LinkedIn"
    assert props["Status"]["status"]["name"] == "Found"
    assert props["Seniority"]["select"]["name"] == "Junior"
    assert props["Remote"]["checkbox"] is False
    assert props["JD URL"]["url"] == "https://linkedin.com/jobs/123"


def test_build_update_payload_applied() -> None:
    payload = build_update_payload(
        status="Applied",
        ats_score=94.5,
        match_tier="auto",
        matched_projects=["Velox AI", "90 Days ML"],
        applied_date=date(2026, 3, 28),
        follow_up_date=date(2026, 4, 4),
        notes="Auto-applied. ATS: 94.5%",
    )
    props = payload["properties"]

    assert props["Status"]["status"]["name"] == "Applied"
    assert props["ATS Score"]["number"] == 94.5
    assert props["Applied Date"]["date"]["start"] == "2026-03-28"
    assert props["Follow Up Date"]["date"]["start"] == "2026-04-04"


def test_platform_display_name() -> None:
    assert platform_display("linkedin") == "LinkedIn"
    assert platform_display("indeed") == "Indeed"
    assert platform_display("totaljobs") == "TotalJobs"
    assert platform_display("glassdoor") == "Glassdoor"
    assert platform_display("reed") == "Reed"
