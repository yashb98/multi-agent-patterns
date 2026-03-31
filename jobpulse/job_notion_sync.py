"""Notion sync for the Job Autopilot pipeline.

Syncs job applications to the "Job Tracker" Notion database.
Uses the same curl-based Notion API pattern as notion_agent.py.
"""

from __future__ import annotations

import json
import subprocess
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.config import NOTION_API_KEY, NOTION_APPLICATIONS_DB_ID

if TYPE_CHECKING:
    from datetime import date

    from jobpulse.models.application_models import JobListing

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Display name maps
# ---------------------------------------------------------------------------

PLATFORM_NAMES: dict[str, str] = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "reed": "Reed",
    "totaljobs": "TotalJobs",
    "glassdoor": "Glassdoor",
}

SENIORITY_NAMES: dict[str, str] = {
    "intern": "Intern",
    "graduate": "Graduate",
    "junior": "Junior",
    "mid": "Mid",
}

ATS_PLATFORM_NAMES: dict[str, str] = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "workday": "Workday",
    "smartrecruiters": "SmartRecruiters",
    "icims": "iCIMS",
}

MATCH_TIER_NAMES: dict[str, str] = {
    "auto": "Auto-apply",
    "review": "Review",
    "skip": "Skipped",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def platform_display(platform: str) -> str:
    """Return display name for a platform key. E.g., 'linkedin' -> 'LinkedIn'."""
    return PLATFORM_NAMES.get(platform, platform.title())


def _notion_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Call Notion API via curl (same pattern as notion_agent.py)."""
    cmd = [
        "curl", "-s", "-X", method,
        f"https://api.notion.com/v1{endpoint}",
        "-H", f"Authorization: Bearer {NOTION_API_KEY}",
        "-H", "Content-Type: application/json",
        "-H", "Notion-Version: 2022-06-28",
    ]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        logger.error("Notion API error %s %s: %s", method, endpoint, e)
        return {}


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def build_create_payload(job: JobListing, db_id: str) -> dict:
    """Build Notion create-page payload for a new job application row.

    Columns:
      Company (title), Role (rich_text), Platform (select), Status=Found (select),
      Salary (rich_text), Location (rich_text), Remote (checkbox), Found Date (date),
      JD URL (url), Seniority (select, if set), ATS Platform (select, if set)
    """
    # Salary display string
    if job.salary_min is not None and job.salary_max is not None:
        salary_str = f"\xa3{int(job.salary_min):,}-\xa3{int(job.salary_max):,}"
    elif job.salary_min is not None:
        salary_str = f"£{int(job.salary_min):,}+"
    elif job.salary_max is not None:
        salary_str = f"up to £{int(job.salary_max):,}"
    else:
        salary_str = ""

    properties: dict = {
        "Company": {
            "title": [{"text": {"content": job.company}}]
        },
        "Role": {
            "rich_text": [{"text": {"content": job.title}}]
        },
        "Platform": {
            "select": {"name": platform_display(job.platform)}
        },
        "Status": {
            "status": {"name": "Found"}
        },
        "Location": {
            "rich_text": [{"text": {"content": job.location}}]
        },
        "Remote": {
            "checkbox": job.remote
        },
        "Found Date": {
            "date": {"start": job.found_at.strftime("%Y-%m-%d")}
        },
        "JD URL": {
            "url": job.url
        },
    }

    if salary_str:
        properties["Salary"] = {
            "rich_text": [{"text": {"content": salary_str}}]
        }

    if job.seniority is not None:
        properties["Seniority"] = {
            "select": {"name": SENIORITY_NAMES.get(job.seniority, job.seniority.title())}
        }

    if job.ats_platform is not None:
        ats_key = job.ats_platform.lower()
        ats_name = ATS_PLATFORM_NAMES.get(ats_key, job.ats_platform.title())
        properties["ATS Platform"] = {
            "select": {"name": ats_name}
        }

    if job.recruiter_email is not None:
        properties["Recruiter Email"] = {"email": job.recruiter_email}

    return {
        "parent": {"database_id": db_id},
        "properties": properties,
    }


def build_update_payload(
    status: str | None = None,
    ats_score: float | None = None,
    match_tier: str | None = None,
    matched_projects: list[str] | None = None,
    applied_date: date | None = None,
    follow_up_date: date | None = None,
    notes: str | None = None,
    ats_platform: str | None = None,
    cv_drive_link: str | None = None,
    cl_drive_link: str | None = None,
    recruiter_email: str | None = None,
) -> dict:
    """Build Notion update-page payload with only the provided (non-None) fields.

    match_tier display names: auto->Auto-apply, review->Review, skip->Skipped
    matched_projects -> multi_select
    dates -> {date: {start: ISO}}
    """
    properties: dict = {}

    if status is not None:
        properties["Status"] = {"status": {"name": status}}

    if ats_score is not None:
        properties["ATS Score"] = {"number": ats_score}

    if match_tier is not None:
        tier_name = MATCH_TIER_NAMES.get(match_tier, match_tier.title())
        properties["Match Tier"] = {"rich_text": [{"text": {"content": tier_name}}]}

    if matched_projects is not None:
        properties["Matched Projects"] = {
            "multi_select": [{"name": p} for p in matched_projects]
        }

    if applied_date is not None:
        properties["Applied Date"] = {"date": {"start": applied_date.isoformat()}}

    if follow_up_date is not None:
        properties["Follow Up Date"] = {"date": {"start": follow_up_date.isoformat()}}

    if notes is not None:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    if ats_platform is not None:
        ats_key = ats_platform.lower()
        ats_name = ATS_PLATFORM_NAMES.get(ats_key, ats_platform.title())
        properties["ATS Platform"] = {"select": {"name": ats_name}}

    if cv_drive_link is not None:
        properties["CV Version"] = {
            "files": [{"type": "external", "name": "CV.pdf", "external": {"url": cv_drive_link}}]
        }

    if cl_drive_link is not None:
        properties["Cover Letter"] = {
            "files": [{"type": "external", "name": "CoverLetter.pdf", "external": {"url": cl_drive_link}}]
        }

    if recruiter_email is not None:
        properties["Recruiter Email"] = {"email": recruiter_email}

    return {"properties": properties}


# ---------------------------------------------------------------------------
# API operations
# ---------------------------------------------------------------------------


def create_application_page(job: JobListing) -> str | None:
    """Create a new page in the Job Tracker Notion database.

    Returns the Notion page ID, or None on failure.
    """
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("NOTION_APPLICATIONS_DB_ID not set — skipping Notion sync")
        return None

    payload = build_create_payload(job, NOTION_APPLICATIONS_DB_ID)
    result = _notion_api("POST", "/pages", payload)

    page_id: str | None = result.get("id")
    if page_id:
        logger.info("Created Notion page %s for job %s at %s", page_id, job.job_id, job.company)
    else:
        logger.error(
            "Failed to create Notion page for job %s: %s",
            job.job_id,
            result.get("message", "unknown error"),
        )
    return page_id


def update_application_page(page_id: str, **kwargs) -> bool:
    """Update an existing Notion page for a job application.

    Accepts the same keyword arguments as build_update_payload().
    Returns True on success, False on failure.
    """
    payload = build_update_payload(**kwargs)
    if not payload["properties"]:
        logger.debug("update_application_page called with no fields to update — skipping")
        return True

    result = _notion_api("PATCH", f"/pages/{page_id}", payload)

    if result.get("id"):
        logger.info("Updated Notion page %s", page_id)
        return True

    logger.error(
        "Failed to update Notion page %s: %s",
        page_id,
        result.get("message", "unknown error"),
    )
    return False
