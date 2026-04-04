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
    company: str | None = None,
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

    safe_company = company.replace("/", "_").replace(" ", "_") if company else None

    if cv_drive_link is not None:
        cv_name = f"Yash_Bishnoi_{safe_company}.pdf" if safe_company else "CV.pdf"
        properties["CV Version"] = {
            "files": [{"type": "external", "name": cv_name, "external": {"url": cv_drive_link}}]
        }

    if cl_drive_link is not None:
        cl_name = f"Cover_Letter_{safe_company}.pdf" if safe_company else "Cover_Letter.pdf"
        properties["Cover Letter"] = {
            "files": [{"type": "external", "name": cl_name, "external": {"url": cl_drive_link}}]
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


# ---------------------------------------------------------------------------
# Page content (OakNorth-style rich body)
# ---------------------------------------------------------------------------


def build_page_content(
    job: JobListing,
    ats_score: float | None = None,
    match_tier: str | None = None,
    matched_projects: list[str] | None = None,
    cv_drive_link: str | None = None,
    cl_drive_link: str | None = None,
    cv_path: str | None = None,
    cover_letter_path: str | None = None,
    gate4_notes: str | None = None,
) -> list[dict]:
    """Build Notion block children for a job page (OakNorth reference format).

    Sections: Application Details, Documents, JD Match Analysis, GitHub Repos.
    """
    safe_company = job.company.replace("/", "_").replace(" ", "_")
    cv_filename = f"Yash_Bishnoi_{safe_company}.pdf"
    cl_filename = f"Cover_Letter_{safe_company}.pdf"

    blocks: list[dict] = []

    # --- Application Details ---
    blocks.append(_heading2("Application Details"))
    blocks.append(_bulleted(f"Company: {job.company}"))
    blocks.append(_bulleted(f"Role: {job.title}"))
    blocks.append(_bulleted(f"Platform: {platform_display(job.platform)}"))
    blocks.append(_bulleted(f"Location: {job.location}"))
    if job.salary_min or job.salary_max:
        if job.salary_min and job.salary_max:
            sal = f"£{int(job.salary_min):,}-£{int(job.salary_max):,}"
        elif job.salary_min:
            sal = f"£{int(job.salary_min):,}+"
        else:
            sal = f"up to £{int(job.salary_max):,}"
        blocks.append(_bulleted(f"Salary: {sal}"))
    if ats_score is not None:
        tier_display = MATCH_TIER_NAMES.get(match_tier or "", match_tier or "N/A")
        blocks.append(_bulleted(f"ATS Score: {ats_score:.1f}% ({tier_display})"))
    if job.ats_platform:
        ats_name = ATS_PLATFORM_NAMES.get(job.ats_platform.lower(), job.ats_platform.title())
        blocks.append(_bulleted(f"ATS Platform: {ats_name}"))
    if job.url:
        blocks.append(_bulleted(f"JD URL: {job.url}"))
    blocks.append(_divider())

    # --- Documents ---
    blocks.append(_heading2("Documents"))
    cv_line = f"CV: {cv_filename}"
    if cv_drive_link:
        cv_line += f" (Drive: {cv_drive_link})"
    blocks.append(_bulleted(cv_line))
    cl_line = f"Cover Letter: {cl_filename}"
    if cl_drive_link:
        cl_line += f" (Drive: {cl_drive_link})"
    blocks.append(_bulleted(cl_line))
    if cv_path:
        blocks.append(_bulleted(f"Local CV: {cv_path}"))
    if cover_letter_path:
        blocks.append(_bulleted(f"Local CL: {cover_letter_path}"))
    blocks.append(_divider())

    # --- JD Match Analysis ---
    blocks.append(_heading2("JD Match Analysis"))
    if matched_projects:
        blocks.append(_bulleted(f"Matched Projects: {', '.join(matched_projects)}"))
    if job.required_skills:
        skills_str = ", ".join(job.required_skills[:15])
        blocks.append(_bulleted(f"Required Skills: {skills_str}"))
    if job.preferred_skills:
        pref_str = ", ".join(job.preferred_skills[:10])
        blocks.append(_bulleted(f"Preferred Skills: {pref_str}"))
    if gate4_notes:
        blocks.append(_bulleted(f"Gate 4 Notes: {gate4_notes}"))
    blocks.append(_divider())

    # --- GitHub Repos ---
    if matched_projects:
        blocks.append(_heading2("GitHub Repos"))
        for proj in matched_projects:
            repo_url = f"https://github.com/yashb98/{proj}"
            blocks.append(_bulleted(f"{proj}: {repo_url}"))

    return blocks


def set_page_content(page_id: str, blocks: list[dict]) -> bool:
    """Replace page body with the given blocks via Notion API.

    Deletes existing children first, then appends new blocks.
    """
    # Get existing children to delete
    existing = _notion_api("GET", f"/blocks/{page_id}/children?page_size=100")
    for child in existing.get("results", []):
        child_id = child.get("id")
        if child_id:
            _notion_api("DELETE", f"/blocks/{child_id}")

    # Append new blocks (max 100 per request)
    for i in range(0, len(blocks), 100):
        batch = blocks[i : i + 100]
        result = _notion_api("PATCH", f"/blocks/{page_id}/children", {"children": batch})
        if not result.get("results"):
            logger.error("Failed to set page content for %s: %s", page_id, result)
            return False

    logger.info("Set page content for Notion page %s (%d blocks)", page_id, len(blocks))
    return True


# ---------------------------------------------------------------------------
# Block builder helpers
# ---------------------------------------------------------------------------


def _heading2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _bulleted(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}
