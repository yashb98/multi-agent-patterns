"""Notion sync for the Job Autopilot pipeline.

Syncs job applications to the "Job Tracker" Notion database.
Uses the same curl-based Notion API pattern as notion_agent.py.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.config import NOTION_APPLICATIONS_DB_ID
from jobpulse.notion_client import notion_api as _notion_api

if TYPE_CHECKING:
    from datetime import date

    from jobpulse.models.application_models import JobListing

logger = get_logger(__name__)


def _file_name_prefix() -> str:
    try:
        from shared.profile_store import get_profile_store
        prefix = get_profile_store().identity().file_name_prefix
        if prefix:
            return prefix
    except Exception:
        pass
    return "Yash_Bishnoi"


_NOTION_UNKNOWN_PROPERTY_RE = re.compile(
    r"^(?P<prop>.+?) is not a property that exists\.?$",
    re.MULTILINE,
)

_TERMINAL_JOB_TRACKER_STATUSES = frozenset({"Applied", "Rejected"})


def delete_job_tracker_non_terminal_pages(
    *,
    terminal_statuses: frozenset[str] | None = None,
) -> int:
    """Trash (delete) Job Tracker pages whose Status is not Applied or Rejected.

    Notion has no hard-delete in the public API: each page is moved to workspace
    trash via ``in_trash: true`` (same end result as deleting from the database
    view). ``Applied`` and ``Rejected`` rows are left untouched.

    Uses ``NOTION_APPLICATIONS_DB_ID`` and a ``Status`` property of type *status*.
    """
    terminal = terminal_statuses or _TERMINAL_JOB_TRACKER_STATUSES
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("delete_job_tracker_non_terminal_pages: NOTION_APPLICATIONS_DB_ID unset")
        return 0

    deleted = 0
    cursor: str | None = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {
                "and": [
                    {"property": "Status", "status": {"does_not_equal": "Applied"}},
                    {"property": "Status", "status": {"does_not_equal": "Rejected"}},
                ],
            },
        }
        if cursor:
            body["start_cursor"] = cursor

        result = _notion_api(
            "POST",
            f"/databases/{NOTION_APPLICATIONS_DB_ID}/query",
            body,
        )

        if result.get("object") == "error":
            logger.error(
                "delete_job_tracker_non_terminal_pages: Notion query failed: %s",
                result.get("message", result),
            )
            return deleted

        for row in result.get("results", []):
            pid = row.get("id")
            if not pid:
                continue
            patch = _notion_api("PATCH", f"/pages/{pid}", {"in_trash": True})
            if not patch.get("id"):
                patch = _notion_api("PATCH", f"/pages/{pid}", {"archived": True})
            if patch.get("id"):
                deleted += 1
            else:
                logger.warning(
                    "delete_job_tracker_non_terminal_pages: trash failed for %s: %s",
                    pid,
                    patch.get("message", patch),
                )

        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
        if not cursor:
            break

    logger.info(
        "delete_job_tracker_non_terminal_pages: trashed %d pages (kept status in %s)",
        deleted,
        ", ".join(sorted(terminal)),
    )
    return deleted


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



# _notion_api imported from jobpulse.notion_client (centralized, with retry + 401 handling)


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
        "Manually Applied": {
            "checkbox": False
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
    applied_time: str | None = None,
    follow_up_date: date | None = None,
    notes: str | None = None,
    ats_platform: str | None = None,
    cv_drive_link: str | None = None,
    cl_drive_link: str | None = None,
    recruiter_email: str | None = None,
    company: str | None = None,
    manually_applied: bool | None = None,
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
        properties["Match Tier"] = {"phone_number": tier_name}

    if matched_projects is not None:
        properties["Matched Projects"] = {
            "multi_select": [{"name": p} for p in matched_projects]
        }

    if applied_date is not None:
        properties["Applied Date"] = {"date": {"start": applied_date.isoformat()}}

    if applied_time is not None:
        properties["Applied Time"] = {
            "rich_text": [{"text": {"content": applied_time}}]
        }

    if follow_up_date is not None:
        properties["Follow Up Date"] = {"date": {"start": follow_up_date.isoformat()}}

    if notes is not None:
        properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

    if ats_platform is not None:
        ats_key = ats_platform.lower()
        ats_name = ATS_PLATFORM_NAMES.get(ats_key, ats_platform.title())
        properties["ATS Platform"] = {"select": {"name": ats_name}}

    safe_company = company.replace("/", "_").replace(" ", "_") if company else None
    _prefix = _file_name_prefix()

    if cv_drive_link is not None:
        cv_name = f"{_prefix}_{safe_company}.pdf" if safe_company else "CV.pdf"
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

    if manually_applied is not None:
        properties["Manually Applied"] = {"checkbox": manually_applied}

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

    If the Notion database schema omits a property we send (e.g. no \"Applied Time\"),
    the API returns 400; we strip that property from the payload and retry until the
    update succeeds or the error is not recoverable.
    """
    payload = build_update_payload(**kwargs)
    if not payload["properties"]:
        logger.debug("update_application_page called with no fields to update — skipping")
        return True

    properties: dict = dict(payload["properties"])
    for _ in range(16):
        result = _notion_api("PATCH", f"/pages/{page_id}", {"properties": properties})

        if result.get("id"):
            logger.info("Updated Notion page %s", page_id)
            return True

        if result.get("object") == "error" and result.get("status") == 400:
            msg = str(result.get("message", ""))
            m = _NOTION_UNKNOWN_PROPERTY_RE.search(msg)
            if m:
                bad = m.group("prop").strip()
                if bad in properties:
                    logger.warning(
                        "Notion schema has no property %r — omitting and retrying page %s",
                        bad,
                        page_id,
                    )
                    del properties[bad]
                    if not properties:
                        logger.error(
                            "Notion update for %s: no properties left after schema mismatch",
                            page_id,
                        )
                        return False
                    continue

        logger.error(
            "Failed to update Notion page %s: %s",
            page_id,
            result.get("message", "unknown error"),
        )
        return False

    logger.error("Notion update for %s: exhausted property retries", page_id)
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
    cv_filename = f"{_file_name_prefix()}_{safe_company}.pdf"
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
            try:
                from shared.profile_store import get_profile_store
                gh = get_profile_store().identity().github or "https://github.com"
                gh_base = gh.rstrip("/")
            except Exception:
                gh_base = "https://github.com"
            repo_url = f"{gh_base}/{proj}"
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
