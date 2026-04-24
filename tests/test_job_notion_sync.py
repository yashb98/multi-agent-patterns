"""Tests for jobpulse/job_notion_sync.py — payload building only, no API calls."""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from jobpulse.job_notion_sync import (
    build_create_payload,
    build_update_payload,
    delete_job_tracker_non_terminal_pages,
    platform_display,
    update_application_page,
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


@patch("jobpulse.job_notion_sync.NOTION_APPLICATIONS_DB_ID", "db-123")
@patch("jobpulse.job_notion_sync._notion_api")
def test_delete_job_tracker_non_terminal_pages_trashes_rows(mock_api) -> None:
    page_a = {"id": "p1", "object": "page"}
    mock_api.side_effect = [
        {"object": "list", "results": [page_a], "has_more": False},
        {"object": "page", "id": "p1"},
    ]
    n = delete_job_tracker_non_terminal_pages()
    assert n == 1
    assert mock_api.call_count == 2
    q = mock_api.call_args_list[0][0][2]
    assert q["filter"]["and"][0]["property"] == "Status"
    trash_body = mock_api.call_args_list[1][0][2]
    assert trash_body == {"in_trash": True}


@patch("jobpulse.job_notion_sync._notion_api")
def test_update_application_page_retries_without_unknown_property(mock_api) -> None:
    """Notion 400 'not a property' drops that field and PATCHes again."""
    mock_api.side_effect = [
        {
            "object": "error",
            "status": 400,
            "message": "Applied Time is not a property that exists.",
        },
        {"object": "page", "id": "abc"},
    ]
    ok = update_application_page(
        "page-123",
        status="Applied",
        applied_date=date(2026, 4, 23),
        applied_time="2026-04-23T12:00:00Z",
    )
    assert ok is True
    assert mock_api.call_count == 2
    second = mock_api.call_args_list[1][0][2]["properties"]
    assert "Applied Time" not in second
    assert second["Status"]["status"]["name"] == "Applied"
