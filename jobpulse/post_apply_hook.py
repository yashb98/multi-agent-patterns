"""Unified post-apply hook — called after every successful job submission.

Handles three concerns:
1. Form experience recording (FormExperienceDB) — learn field types, pages, timing
2. Drive upload + Notion update — CV/CL links, applied date/time, follow-up, status
3. Job DB update — mark as Applied with timestamp

Called from applicator.apply_job() so both cron and manual paths get it for free.
"""
from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.drive_uploader import upload_cover_letter, upload_cv
from jobpulse.form_experience_db import FormExperienceDB
from jobpulse.job_notion_sync import update_application_page

logger = get_logger(__name__)


def post_apply_hook(
    result: dict,
    job_context: dict,
    form_exp_db_path: str | None = None,
) -> None:
    """Run all post-apply steps after a successful submission.

    Args:
        result: Return value from adapter.fill_and_submit() — must have
                success, pages_filled, field_types, screening_questions.
        job_context: Dict with keys: job_id, company, title, url, platform,
                     ats_platform, notion_page_id, cv_path, cover_letter_path,
                     match_tier, ats_score, matched_projects.
        form_exp_db_path: Override DB path (for testing with tmp_path).
    """
    if not result.get("success"):
        return

    company = job_context.get("company", "Unknown")
    url = job_context.get("url", "")
    notion_page_id = job_context.get("notion_page_id")
    cv_path = job_context.get("cv_path")
    cl_path = job_context.get("cover_letter_path")

    start = time.monotonic()

    # --- 1. Record form experience ---
    try:
        exp_db = FormExperienceDB(db_path=form_exp_db_path)
        exp_db.record(
            domain=url,
            platform=job_context.get("ats_platform") or job_context.get("platform", "generic"),
            adapter="extension",
            pages_filled=result.get("pages_filled", 0),
            field_types=result.get("field_types", []),
            screening_questions=result.get("screening_questions", []),
            time_seconds=result.get("time_seconds", 0.0),
            success=True,
        )
    except Exception as exc:
        logger.warning("post_apply_hook: form experience recording failed: %s", exc)

    # --- 2. Upload documents to Drive ---
    cv_drive_link = None
    cl_drive_link = None

    if cv_path:
        try:
            cv_drive_link = upload_cv(Path(cv_path), company)
        except Exception as exc:
            logger.warning("post_apply_hook: CV Drive upload failed: %s", exc)

    if cl_path:
        try:
            cl_drive_link = upload_cover_letter(Path(cl_path), company)
        except Exception as exc:
            logger.warning("post_apply_hook: CL Drive upload failed: %s", exc)

    # --- 3. Update Notion with all required fields ---
    if notion_page_id:
        applied_now = datetime.now(UTC)
        follow_up = date.today() + timedelta(days=7)
        try:
            update_application_page(
                notion_page_id,
                status="Applied",
                applied_date=date.today(),
                applied_time=applied_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                follow_up_date=follow_up,
                cv_drive_link=cv_drive_link,
                cl_drive_link=cl_drive_link,
                company=company,
            )
        except Exception as exc:
            logger.warning("post_apply_hook: Notion update failed: %s", exc)

    elapsed = time.monotonic() - start
    logger.info(
        "post_apply_hook: completed for %s in %.1fs (drive_cv=%s, drive_cl=%s, notion=%s)",
        company,
        elapsed,
        "yes" if cv_drive_link else "no",
        "yes" if cl_drive_link else "no",
        "yes" if notion_page_id else "skip",
    )
