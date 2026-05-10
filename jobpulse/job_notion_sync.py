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
    """ProfileStore.identity().file_name_prefix → config env → 'Resume'.
    Never hardcodes a real name in source per pii-policy.md.
    """
    try:
        from shared.profile_store import get_profile_store
        prefix = (get_profile_store().identity().file_name_prefix or "").strip()
        if prefix:
            return prefix
    except Exception:
        pass
    try:
        from jobpulse.config import APPLICANT_FIRST_NAME, APPLICANT_LAST_NAME
        prefix = f"{APPLICANT_FIRST_NAME}_{APPLICANT_LAST_NAME}".strip("_")
        if prefix:
            return prefix
    except Exception:
        pass
    logger.warning("job_notion_sync._file_name_prefix: no name in ProfileStore/config")
    return "Resume"


_NOTION_UNKNOWN_PROPERTY_RE = re.compile(
    r"^(?P<prop>.+?) is not a property that exists\.?$",
    re.MULTILINE,
)

_NOTION_TYPE_MISMATCH_RE = re.compile(
    r"body\.properties\.(?P<prop>[^.]+)\.",
)

# Notion's user-facing schema errors don't always include the body.properties
# path. Two extra shapes seen in production: "X is expected to be <type>" when
# a column was retyped manually, and 'Status option "X" does not exist' when
# the code emits a status that's not in the column's option list.
_NOTION_EXPECTED_TYPE_RE = re.compile(
    r'^(?P<prop>.+?) is expected to be \w+\.?$',
    re.MULTILINE,
)
_NOTION_BAD_STATUS_OPTION_RE = re.compile(
    r'Status option "[^"]+" does not exist',
)

_TERMINAL_JOB_TRACKER_STATUSES = frozenset({
    "Applied", "Rejected", "Withdrawn", "Expired", "Skipped", "Interviewing",
})


def delete_job_tracker_non_terminal_pages(
    *,
    terminal_statuses: frozenset[str] | None = None,
    min_age_days: int | None = None,
) -> int:
    """Trash Job Tracker pages whose Status is not terminal.

    Notion has no hard-delete in the public API: each page is moved to workspace
    trash via ``in_trash: true``.  Terminal statuses (Applied, Rejected, Withdrawn,
    Expired, Skipped, Interviewing) are left untouched.

    When *min_age_days* is set, only pages whose Found Date is older than that
    many days are trashed — safe to call in regular scan windows without
    destroying freshly-created pages.
    """
    terminal = terminal_statuses or _TERMINAL_JOB_TRACKER_STATUSES
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("delete_job_tracker_non_terminal_pages: NOTION_APPLICATIONS_DB_ID unset")
        return 0

    status_filters: list[dict] = [
        {"property": "Status", "status": {"does_not_equal": s}}
        for s in sorted(terminal)
    ]

    if min_age_days is not None:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=min_age_days)).strftime("%Y-%m-%d")
        status_filters.append({"property": "Found Date", "date": {"before": cutoff}})

    deleted = 0
    cursor: str | None = None
    while True:
        body: dict = {
            "page_size": 100,
            "filter": {"and": status_filters},
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
        "delete_job_tracker_non_terminal_pages: trashed %d pages (kept status in %s%s)",
        deleted,
        ", ".join(sorted(terminal)),
        f", min_age={min_age_days}d" if min_age_days else "",
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
    "generic": "Generic",
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
    salary: str | None = None,
    seniority: str | None = None,
    remote: bool | None = None,
    cv_filename: str | None = None,
    cl_filename: str | None = None,
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
        properties["Match Tier"] = {
            "rich_text": [{"text": {"content": tier_name}}]
        }

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

    if cv_drive_link is not None:
        cv_name = cv_filename or "CV.pdf"
        properties["CV Version"] = {
            "files": [{"type": "external", "name": cv_name, "external": {"url": cv_drive_link}}]
        }

    if cl_drive_link is not None:
        cl_name = cl_filename or "Cover_Letter.pdf"
        properties["Cover Letter"] = {
            "files": [{"type": "external", "name": cl_name, "external": {"url": cl_drive_link}}]
        }

    if recruiter_email is not None:
        properties["Recruiter Email"] = {"email": recruiter_email}

    if manually_applied is not None:
        properties["Manually Applied"] = {"checkbox": manually_applied}

    if salary is not None:
        properties["Salary"] = {"rich_text": [{"text": {"content": salary}}]}

    if seniority is not None:
        properties["Seniority"] = {"select": {"name": seniority}}

    if remote is not None:
        properties["Remote"] = {"checkbox": remote}

    return {"properties": properties}


# ---------------------------------------------------------------------------
# API operations
# ---------------------------------------------------------------------------


def get_notion_page_status(page_id: str) -> str | None:
    """Read the current Status of a Job Tracker page from Notion.

    Returns the status name (e.g. "Found", "Applied") or None on failure.
    """
    if not page_id:
        return None
    result = _notion_api("GET", f"/pages/{page_id}")
    if not result or "properties" not in result:
        return None
    status_prop = result.get("properties", {}).get("Status", {})
    status_obj = status_prop.get("status")
    if isinstance(status_obj, dict):
        return status_obj.get("name")
    return None


def find_application_page(
    company: str, title: str, url: str | None = None,
) -> str | None:
    """Search the Job Tracker DB for an existing page matching this job.

    Resolution order (most-precise first):
      1. URL-based match — when *url* is provided, dedup by canonicalized URL.
         Stable across uk.linkedin.com vs www.linkedin.com host variants and
         tracking-param differences.
      2. Company + role-prefix substring — legacy fallback. Fragile on company
         name drift ('IG Group' vs 'IG group') but matches the previous
         behavior so we don't regress for jobs without URL tracking.

    Returns the Notion page ID if found, None otherwise.
    """
    if not NOTION_APPLICATIONS_DB_ID:
        return None

    # 1. URL-based dedup using the same canonicalization as job_id generation.
    #    LinkedIn 'linkedin.com:job:NNN' canonical key matches across host
    #    variants; we look up Notion pages whose stored JD URL canonicalizes
    #    to the same key.
    if url:
        try:
            from jobpulse.jd_analyzer import _canonicalize_url
            target = _canonicalize_url(url)
        except Exception:
            target = ""

        if target:
            # Notion API doesn't support custom equality on URL property in a
            # cheap way, so we do a coarser query and filter client-side. We
            # search by company first (still cheap) then check URL canonicals.
            if company:
                body: dict = {
                    "page_size": 25,
                    "filter": {"property": "Company", "title": {"equals": company}},
                }
                result = _notion_api("POST", f"/databases/{NOTION_APPLICATIONS_DB_ID}/query", body)
                for row in result.get("results", []):
                    row_url = (row.get("properties", {})
                                  .get("JD URL", {}).get("url") or "")
                    if not row_url:
                        continue
                    try:
                        from jobpulse.jd_analyzer import _canonicalize_url as _c
                        if _c(row_url) == target:
                            page_id = row.get("id")
                            logger.info(
                                "find_application_page: URL-canonical match %s for %s — %s",
                                page_id, company, title,
                            )
                            return page_id
                    except Exception:
                        continue

    if not company:
        return None

    # 2. Legacy company+role-prefix fallback (unchanged behavior)
    role_prefix = re.split(r"[,\-–—:|/]", title or "")[0].strip()[:30]
    filters: list[dict] = [{"property": "Company", "title": {"equals": company}}]
    if role_prefix:
        filters.append({"property": "Role", "rich_text": {"contains": role_prefix}})
    body: dict = {"page_size": 5, "filter": {"and": filters}}
    result = _notion_api("POST", f"/databases/{NOTION_APPLICATIONS_DB_ID}/query", body)
    rows = result.get("results", [])
    if rows:
        page_id = rows[0].get("id")
        logger.info("find_application_page: company+role match %s for %s — %s", page_id, company, title)
        return page_id
    return None


_REVERSE_PLATFORM_NAMES: dict[str, str] = {v.lower(): k for k, v in PLATFORM_NAMES.items()}
_REVERSE_ATS_PLATFORM_NAMES: dict[str, str] = {v.lower(): k for k, v in ATS_PLATFORM_NAMES.items()}


def _parse_notion_job_page(page: dict) -> dict | None:
    """Extract a standardized job dict from a Notion Job Tracker page."""
    props = page.get("properties", {})

    company_parts = props.get("Company", {}).get("title", [])
    company = company_parts[0]["text"]["content"] if company_parts else ""

    role_parts = props.get("Role", {}).get("rich_text", [])
    title = role_parts[0]["text"]["content"] if role_parts else ""

    if not company or not title:
        return None

    platform_obj = props.get("Platform", {}).get("select")
    platform_display_name = platform_obj["name"] if platform_obj else "generic"
    platform = _REVERSE_PLATFORM_NAMES.get(platform_display_name.lower(), platform_display_name.lower())

    url = props.get("JD URL", {}).get("url") or ""

    ats_score = props.get("ATS Score", {}).get("number") or 0

    loc_parts = props.get("Location", {}).get("rich_text", [])
    location = loc_parts[0]["text"]["content"] if loc_parts else ""

    ats_plat_obj = props.get("ATS Platform", {}).get("select")
    ats_platform = (
        _REVERSE_ATS_PLATFORM_NAMES.get(ats_plat_obj["name"].lower(), ats_plat_obj["name"].lower())
        if ats_plat_obj
        else None
    )

    found_date_obj = props.get("Found Date", {}).get("date")
    found_date = found_date_obj["start"] if found_date_obj else ""

    sen_obj = props.get("Seniority", {}).get("select")
    seniority = sen_obj["name"] if sen_obj else None

    remote = props.get("Remote", {}).get("checkbox", False)

    sal_parts = props.get("Salary", {}).get("rich_text", [])
    salary = sal_parts[0]["text"]["content"] if sal_parts else ""

    matched_proj = [ms["name"] for ms in props.get("Matched Projects", {}).get("multi_select", [])]

    status_obj = props.get("Status", {}).get("status")
    status = status_obj["name"] if status_obj else ""

    applied_date_obj = props.get("Applied Date", {}).get("date")
    applied_date = applied_date_obj["start"] if applied_date_obj else ""

    followup_date_obj = props.get("Follow Up Date", {}).get("date")
    follow_up_date = followup_date_obj["start"] if followup_date_obj else ""

    notes_parts = props.get("Notes", {}).get("rich_text", [])
    notes = notes_parts[0]["text"]["content"] if notes_parts else ""

    recruiter_email = props.get("Recruiter Email", {}).get("email") or ""

    manually_applied = props.get("Manually Applied", {}).get("checkbox", False)

    tier_parts = props.get("Match Tier", {}).get("rich_text", [])
    match_tier = tier_parts[0]["text"]["content"] if tier_parts else ""

    return {
        "notion_page_id": page["id"],
        "company": company,
        "title": title,
        "platform": platform,
        "url": url,
        "ats_score": float(ats_score),
        "location": location,
        "ats_platform": ats_platform,
        "found_date": found_date,
        "seniority": seniority,
        "remote": remote,
        "salary": salary,
        "matched_projects": matched_proj,
        "status": status,
        "applied_date": applied_date,
        "follow_up_date": follow_up_date,
        "notes": notes,
        "recruiter_email": recruiter_email,
        "manually_applied": manually_applied,
        "match_tier": match_tier,
    }


def fetch_found_jobs_from_notion(
    *,
    found_on: "date | None" = None,
) -> list[dict]:
    """Query the Notion Job Tracker for jobs with Status = 'Found'.

    When *found_on* is provided, results are further filtered to that Found Date.
    Returns a list of standardized job dicts sorted newest-first.
    """
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("fetch_found_jobs_from_notion: NOTION_APPLICATIONS_DB_ID unset")
        return []

    filters: list[dict] = [{"property": "Status", "status": {"equals": "Found"}}]
    if found_on is not None:
        filters.append({"property": "Found Date", "date": {"equals": found_on.isoformat()}})

    body: dict = {
        "page_size": 100,
        "filter": {"and": filters} if len(filters) > 1 else filters[0],
        "sorts": [{"property": "Found Date", "direction": "descending"}],
    }

    all_rows: list[dict] = []
    cursor: str | None = None
    while True:
        if cursor:
            body["start_cursor"] = cursor

        result = _notion_api("POST", f"/databases/{NOTION_APPLICATIONS_DB_ID}/query", body)
        if result.get("object") == "error":
            logger.error(
                "fetch_found_jobs_from_notion: Notion query failed: %s",
                result.get("message", result),
            )
            return all_rows

        for page in result.get("results", []):
            parsed = _parse_notion_job_page(page)
            if parsed:
                all_rows.append(parsed)

        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
        if not cursor:
            break

    logger.info("fetch_found_jobs_from_notion: found %d jobs (found_on=%s)", len(all_rows), found_on)
    return all_rows


def create_application_page(job: JobListing) -> str | None:
    """Create a new page in the Job Tracker Notion database.

    Checks for an existing page (same company + role) first to avoid duplicates.
    Returns the Notion page ID, or None on failure.
    """
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("NOTION_APPLICATIONS_DB_ID not set — skipping Notion sync")
        return None

    existing = find_application_page(job.company, job.title, url=getattr(job, "url", None))
    if existing:
        logger.info(
            "create_application_page: reusing existing page %s for %s @ %s",
            existing, job.title, job.company,
        )
        return existing

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
            # Notion combines multiple validation issues into ONE line joined by
            # ". ", so a single regex that anchors to end-of-line never matches
            # past the first sentence. Split into sentence-shaped chunks and
            # extract every offending property name from each chunk.
            bad_props: set[str] = set()
            chunks = re.split(r"\.\s+", msg)
            for chunk in chunks:
                # Re-add the trailing period that split() consumed, so anchored
                # regexes (matching `\.?$`) still hit.
                if not chunk.endswith("."):
                    chunk = chunk + "."
                for rx in (
                    _NOTION_UNKNOWN_PROPERTY_RE,
                    _NOTION_EXPECTED_TYPE_RE,
                    _NOTION_TYPE_MISMATCH_RE,
                ):
                    m = rx.search(chunk)
                    if m:
                        bad_props.add(m.group("prop").strip())
                        break
            if _NOTION_BAD_STATUS_OPTION_RE.search(msg) and "Status" in properties:
                bad_props.add("Status")

            stripped = [b for b in bad_props if b in properties]
            if stripped:
                logger.warning(
                    "Notion properties %s rejected (missing, wrong type, or unknown option) — "
                    "omitting and retrying page %s",
                    sorted(stripped), page_id,
                )
                for b in stripped:
                    del properties[b]
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
