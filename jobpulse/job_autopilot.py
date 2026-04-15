"""Job Autopilot Orchestrator — top-level pipeline coordinator.

Ties together all Job Autopilot pipeline tasks:
  L1: Scan platforms  (job_scanner)
  L2: Analyze JDs     (jd_analyzer)
  L3: Deduplicate     (job_deduplicator)
  L4: Match projects  (github_matcher)
  L5: Generate CV PDF (cv_templates.generate_cv — ReportLab)
  L6: Cover letter PDF(cv_templates.generate_cover_letter — ReportLab)
  L7: Score & tier    (determine_match_tier — inline)
  L8: Apply / queue   (applicator)
  L9: Notify          (telegram_bots)

External entry points (called by dispatcher.py):
  run_scan_window(platforms)   — full pipeline for one scheduled window
  approve_jobs(args)           — approve pending review jobs from Telegram
  reject_job(args)             — reject/skip a job from Telegram
  get_job_detail(args)         — full details for job number N
  update_search_config(args)   — mutate search config from Telegram
  check_follow_ups()           — daily follow-up reminder (9am cron)
  set_autopilot_paused(paused) — pause/resume autopilot
  is_paused()                  — check pause state
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.applicator import apply_job
from jobpulse.config import DATA_DIR, JOB_AUTOPILOT_ENABLED, JOB_AUTOPILOT_MAX_DAILY
from jobpulse.job_db import JobDB
from jobpulse.job_notion_sync import update_application_page
from jobpulse.job_scanner import load_search_config, save_search_config
from jobpulse.process_logger import ProcessTrail
from jobpulse.telegram_bots import send_jobs

logger = get_logger(__name__)


def determine_match_tier(ats_score: float) -> str:
    """Return 'auto' if >= 90, 'review' if >= 82, 'skip' otherwise."""
    if ats_score >= 90:
        return "auto"
    if ats_score >= 82:
        return "review"
    return "skip"


# ---------------------------------------------------------------------------
# Constants / file paths
# ---------------------------------------------------------------------------

PAUSE_FILE = DATA_DIR / "job_autopilot_paused.txt"
PENDING_REVIEW_FILE = DATA_DIR / "pending_review_jobs.json"


# ---------------------------------------------------------------------------
# Pause helpers
# ---------------------------------------------------------------------------


def is_paused() -> bool:
    """Return True if the autopilot pause file exists."""
    return PAUSE_FILE.exists()


def set_autopilot_paused(paused: bool) -> None:
    """Create (paused=True) or remove (paused=False) the pause sentinel file."""
    if paused:
        PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PAUSE_FILE.write_text(
            f"Paused at {datetime.now(UTC).isoformat()}", encoding="utf-8"
        )
        logger.info("job_autopilot: autopilot PAUSED")
    else:
        if PAUSE_FILE.exists():
            PAUSE_FILE.unlink()
        logger.info("job_autopilot: autopilot RESUMED")


# ---------------------------------------------------------------------------
# Pending review file helpers
# ---------------------------------------------------------------------------


def _load_pending() -> list[dict[str, Any]]:
    """Load pending review jobs from file. Returns [] if file missing or invalid."""
    if not PENDING_REVIEW_FILE.exists():
        return []
    try:
        return json.loads(PENDING_REVIEW_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("job_autopilot: could not load pending_review_jobs: %s", exc)
        return []


def _save_pending(jobs: list[dict[str, Any]]) -> None:
    """Persist pending review jobs to file."""
    PENDING_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEW_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _append_pending(new_jobs: list[dict[str, Any]]) -> None:
    """Atomically append jobs to the pending review file (race-safe)."""
    from jobpulse.utils.safe_io import locked_json_file

    with locked_json_file(PENDING_REVIEW_FILE, default=[]) as data:
        data.extend(new_jobs)


# ---------------------------------------------------------------------------
# Daily cap check
# ---------------------------------------------------------------------------


def _applied_today(db: JobDB) -> int:
    """Return count of applications submitted today."""
    stats = db.get_today_stats()
    return stats.get("applied", 0)


# ---------------------------------------------------------------------------
# Main scan window
# ---------------------------------------------------------------------------


_scan_lock = threading.Lock()


def run_scan_window(platforms: list[str] | None = None) -> str:
    """Execute one scan window — the full pipeline.

    Thread-safe: uses a lock to prevent concurrent pipeline runs (cron + Telegram).

    Steps:
    1. Check if enabled / paused / daily cap
    2. Scan platforms
    3. Analyze JDs → JobListing objects
    4. Deduplicate
    5. For each new job: save, Notion, match projects, tailor CV, cover letter, score
    6. Apply by tier:
       - auto (90%+): submit via applicator, update to Applied
       - review (82-89%): save to pending, send Telegram batch
       - skip (<82%): mark as Skipped
    7. Send summary to Telegram Jobs bot

    Returns:
        Human-readable summary string.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("run_scan_window: already running — skipping concurrent invocation")
        return "A scan is already in progress. Try again in a few minutes."
    try:
        return _run_scan_window_inner(platforms)
    finally:
        _scan_lock.release()


def _run_scan_window_inner(platforms: list[str] | None = None) -> str:
    """Inner pipeline logic — called by run_scan_window() under lock.

    Delegates to stage functions in jobpulse/scan_pipeline.py:
      1. fetch_and_filter_jobs  — scan + liveness + Gate 0
      2. analyze_and_deduplicate — JD analysis + dedup
      3. prescreen_listings     — Gates 1-3 + Gate 4A
      4. generate_materials     — CV/CL + ATS + Gate 4B  (per job)
      5. route_and_apply        — auto-apply / review / skip (per job)
    """
    from jobpulse.scan_pipeline import (
        fetch_and_filter_jobs,
        analyze_and_deduplicate,
        prescreen_listings,
        generate_materials,
        route_and_apply,
    )

    trail = ProcessTrail("job_autopilot", "scan_window")
    notion_failures: list[str] = []

    # --- Step 1: gate checks ---
    if not JOB_AUTOPILOT_ENABLED:
        msg = "Job Autopilot is disabled (JOB_AUTOPILOT_ENABLED=false)."
        trail.log_step("decision", "Gate: disabled", step_output=msg, status="skipped")
        logger.info("job_autopilot: %s", msg)
        return msg

    if is_paused():
        msg = "Job Autopilot is paused. Send 'resume jobs' to restart."
        trail.log_step("decision", "Gate: paused", step_output=msg, status="skipped")
        logger.info("job_autopilot: %s", msg)
        return msg

    db = JobDB()
    already_applied = _applied_today(db)
    if already_applied >= JOB_AUTOPILOT_MAX_DAILY:
        msg = (
            f"Daily cap reached: {already_applied}/{JOB_AUTOPILOT_MAX_DAILY} applications today."
        )
        trail.log_step("decision", "Gate: daily cap", step_output=msg, status="skipped")
        logger.info("job_autopilot: %s", msg)
        return msg

    remaining_cap = JOB_AUTOPILOT_MAX_DAILY - already_applied

    # --- Stage 1: fetch and filter ---
    search_config = load_search_config()
    raw_jobs, total_found, gate0_rejected = fetch_and_filter_jobs(platforms, search_config, trail)

    # --- Stage 2: analyze JDs and deduplicate ---
    new_listings = analyze_and_deduplicate(raw_jobs, db, trail)

    # --- Stage 3: pre-screen (Gates 1-3 and Gate 4A) ---
    gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
        new_listings, db, trail,
    )

    # --- Stages 4 + 5: generate materials and route (per job) ---
    repos: list[dict] = []  # shared cache across jobs
    auto_applied = 0
    review_batch: list[dict[str, Any]] = []
    skipped = 0
    errors = 0

    for listing, screen in gate4_filtered:
        if auto_applied >= remaining_cap:
            logger.info(
                "job_autopilot: reached daily cap mid-batch, stopping at %d auto-applied",
                auto_applied,
            )
            break

        try:
            bundle = generate_materials(listing, screen, db, repos, notion_failures)
            result = route_and_apply(
                listing, bundle, db, review_batch, remaining_cap, auto_applied,
            )
            if result.action == "auto_applied":
                auto_applied += 1
            elif result.action == "skipped":
                skipped += 1
        except Exception as exc:
            logger.error(
                "job_autopilot: unhandled error processing job %s @ %s: %s",
                getattr(listing, "title", "?"),
                getattr(listing, "company", "?"),
                exc,
            )
            errors += 1

    # Persist pending review batch (append to existing, race-safe)
    if review_batch:
        _append_pending(review_batch)

    # --- Step 7: send Telegram messages ---
    hour = datetime.now().hour
    minute = datetime.now().minute
    time_str = f"{hour}:{minute:02d} {'AM' if hour < 12 else 'PM'}"

    summary_lines = [
        f"📊 Job Autopilot ({time_str} scan)",
        f"Found: {total_found} | Gate 0 filtered: {gate0_rejected}",
        f"New: {len(new_listings)} | Pre-screen: {gate_rejected} rejected, {gate_skipped} skipped",
        f"Gate 4: {gate4_blocked} blocked, {len(gate4_filtered)} passed",
        f"Processed: {len(gate4_filtered)}",
        f"Auto-applied: {auto_applied}",
        f"Ready for review: {len(review_batch)}",
        f"Skipped: {skipped} (<82% match)",
    ]
    if errors:
        summary_lines.append(f"Errors: {errors}")

    # Add skill tracker link if there are pending skills
    try:
        from jobpulse.skill_tracker_notion import get_pending_skills
        pending = get_pending_skills()
        if pending:
            tracker_db_file = Path(__file__).parent.parent / "data" / "skill_tracker_db_id.txt"
            if tracker_db_file.exists():
                db_id = tracker_db_file.read_text().strip().replace("-", "")
                tracker_url = f"https://www.notion.so/{db_id}"
                summary_lines.append(f"\n🎯 {len(pending)} skills pending your review:")
                for p in pending[:5]:
                    summary_lines.append(f"  • {p['skill']} (seen {p['times_seen']}x)")
                if len(pending) > 5:
                    summary_lines.append(f"  ... +{len(pending) - 5} more")
                summary_lines.append(f"\n📋 Review here: {tracker_url}")
    except Exception as exc:
        logger.debug("skill tracker notification failed: %s", exc)

    summary_msg = "\n".join(summary_lines)
    send_jobs(summary_msg)

    # Send review batch if any new items
    if review_batch:
        _send_review_batch(review_batch)

    trail.finalize(
        f"Scan complete: {auto_applied} auto-applied, "
        f"{len(review_batch)} for review, {skipped} skipped"
    )

    if notion_failures:
        logger.warning("job_autopilot: %d Notion sync failures this run", len(notion_failures))

    return summary_msg


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


def _queue_for_review(listing: Any, ats_score: float, batch: list[dict[str, Any]]) -> None:
    """Append a listing to the review batch and update DB status."""
    db = JobDB()
    db.update_status(listing.job_id, "Pending Approval")
    batch.append(
        {
            "job_id": listing.job_id,
            "title": listing.title,
            "company": listing.company,
            "platform": listing.platform,
            "location": listing.location,
            "ats_score": round(ats_score, 1),
        }
    )


def _send_review_batch(jobs: list[dict[str, Any]]) -> None:
    """Format and send the review batch to the Jobs Telegram bot."""
    lines = [f"📋 {len(jobs)} job{'s' if len(jobs) != 1 else ''} ready for review (82-89% ATS):"]
    lines.append("")

    for i, job in enumerate(jobs, start=1):
        ats_display = f"{job['ats_score']:.0f}%"
        lines.append(f"{i}. {job['title']} — {job['company']} ({job['platform']})")
        lines.append(f"   ATS: {ats_display} | {job['location']}")
        lines.append("")

    lines.append('Reply: "apply 1,3,5" or "apply all" or "reject 2"')
    send_jobs("\n".join(lines))


# ---------------------------------------------------------------------------
# Telegram-callable approval / rejection
# ---------------------------------------------------------------------------


def approve_jobs(args: str) -> str:
    """Approve pending review jobs.

    Args:
        args: "1,3,5" or "all" — 1-based indices into the pending review list.

    Returns:
        Summary message to send back to user.
    """
    pending = _load_pending()
    if not pending:
        return "No jobs pending review. Run a scan first."

    # Parse engine override: "approve 3 pw" or "approve 3 ext"
    parts = args.strip().split()
    engine_override = "extension"
    if len(parts) >= 2 and parts[-1].lower() in ("pw", "playwright"):
        engine_override = "playwright"
        args = " ".join(parts[:-1])
    elif len(parts) >= 2 and parts[-1].lower() in ("ext", "extension"):
        engine_override = "extension"
        args = " ".join(parts[:-1])

    # Parse args
    args = args.strip().lower()
    if args == "all":
        indices = list(range(len(pending)))
    else:
        indices = []
        for part in args.replace(" ", "").split(","):
            try:
                n = int(part) - 1  # convert 1-based to 0-based
                if 0 <= n < len(pending):
                    indices.append(n)
            except ValueError:
                pass

    if not indices:
        return "Could not parse job numbers. Use: apply 1,3,5 or apply all"

    db = JobDB()
    applied_titles: list[str] = []
    failed_titles: list[str] = []

    for idx in indices:
        job = pending[idx]
        job_id = job["job_id"]

        # Retrieve stored application data
        app = db.get_application(job_id)
        if not app:
            logger.warning("job_autopilot: approve_jobs — no application record for %s", job_id)
            failed_titles.append(f"{job['title']} @ {job['company']}")
            continue

        cv_path_str: str | None = app.get("cv_path")
        cover_letter_path_str: str | None = app.get("cover_letter_path")
        listing_row = db.get_listing(job_id)

        cv_path = Path(cv_path_str) if cv_path_str else None
        cover_letter_path = Path(cover_letter_path_str) if cover_letter_path_str else None

        if cv_path is None or not cv_path.exists():
            logger.warning(
                "job_autopilot: approve_jobs — no CV for %s, using placeholder", job_id[:8]
            )

        try:
            ats_platform = listing_row.get("ats_platform") if listing_row else None
            listing_url = listing_row.get("url", "") if listing_row else job_id

            result = apply_job(
                url=listing_url,
                ats_platform=ats_platform,
                cv_path=cv_path or Path("/dev/null"),
                cover_letter_path=cover_letter_path,
                custom_answers=None,
                engine=engine_override,
            )

            applied_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
            follow_up = (date.today() + timedelta(days=7)).isoformat()

            db.save_application(
                job_id=job_id,
                status="Applied",
                ats_score=app.get("ats_score", 0),
                match_tier=app.get("match_tier", "review"),
                matched_projects=json.loads(app.get("matched_projects") or "[]"),
                cv_path=cv_path_str,
                cover_letter_path=cover_letter_path_str,
                applied_at=applied_at,
                notion_page_id=app.get("notion_page_id"),
                follow_up_date=follow_up,
            )

            notion_page_id = app.get("notion_page_id")
            if notion_page_id:
                try:
                    update_application_page(
                        notion_page_id,
                        status="Applied",
                        applied_date=date.today(),
                        follow_up_date=date.today() + timedelta(days=7),
                    )
                except Exception as exc:
                    logger.warning(
                        "job_autopilot: approve_jobs Notion update failed: %s", exc
                    )

            applied_titles.append(f"{job['title']} @ {job['company']}")
            logger.info(
                "job_autopilot: APPROVED + APPLIED %s @ %s (success=%s)",
                job["title"],
                job["company"],
                result.get("success"),
            )
        except Exception as exc:
            logger.error(
                "job_autopilot: approve_jobs failed for %s: %s", job_id[:8], exc
            )
            failed_titles.append(f"{job['title']} @ {job['company']}")

    # Remove approved jobs from pending list
    approved_set = set(indices)
    remaining = [j for i, j in enumerate(pending) if i not in approved_set]
    _save_pending(remaining)

    lines: list[str] = []
    if applied_titles:
        lines.append(f"✅ Applied to {len(applied_titles)} job(s):")
        for t in applied_titles:
            lines.append(f"  • {t}")
    if failed_titles:
        lines.append(f"❌ Failed to apply to {len(failed_titles)} job(s):")
        for t in failed_titles:
            lines.append(f"  • {t}")
    if remaining:
        lines.append(f"\n{len(remaining)} job(s) still pending review.")

    return "\n".join(lines) if lines else "No matching jobs found."


def reject_job(args: str) -> str:
    """Reject/skip a pending review job.

    Args:
        args: "2" — 1-based index of the job in the pending review list.

    Returns:
        Confirmation message.
    """
    pending = _load_pending()
    if not pending:
        return "No jobs pending review."

    args = args.strip()
    try:
        idx = int(args) - 1  # 1-based → 0-based
    except ValueError:
        return f"Could not parse job number: '{args}'. Use e.g. reject 2"

    if idx < 0 or idx >= len(pending):
        return f"Job #{args} not found. There are {len(pending)} pending jobs."

    job = pending[idx]
    job_id = job["job_id"]

    db = JobDB()
    db.update_status(job_id, "Skipped")

    # Update Notion if we have a page ID
    app = db.get_application(job_id)
    if app and app.get("notion_page_id"):
        try:
            update_application_page(app["notion_page_id"], status="Skipped")
        except Exception as exc:
            logger.warning("job_autopilot: reject Notion update failed: %s", exc)

    # Remove from pending list
    remaining = [j for i, j in enumerate(pending) if i != idx]
    _save_pending(remaining)

    logger.info(
        "job_autopilot: REJECTED job %s @ %s", job["title"], job["company"]
    )
    msg = f"❌ Skipped: {job['title']} — {job['company']}"
    if remaining:
        msg += f"\n{len(remaining)} job(s) still pending."
    return msg


def get_job_detail(args: str) -> str:
    """Return full details for a pending review job.

    Args:
        args: "3" — 1-based index of the job in the pending review list.

    Returns:
        Formatted string with title, company, platform, location, salary, ATS score,
        matched projects, and URL.
    """
    pending = _load_pending()
    if not pending:
        return "No jobs pending review."

    args = args.strip()
    try:
        idx = int(args) - 1  # 1-based → 0-based
    except ValueError:
        return f"Could not parse job number: '{args}'."

    if idx < 0 or idx >= len(pending):
        return f"Job #{args} not found. There are {len(pending)} pending jobs."

    job = pending[idx]
    job_id = job["job_id"]

    db = JobDB()
    listing = db.get_listing(job_id)
    app = db.get_application(job_id)

    lines: list[str] = [f"💼 Job #{args}: {job['title']}"]
    lines.append(f"Company:  {job['company']}")
    lines.append(f"Platform: {job['platform']}")
    lines.append(f"Location: {job.get('location', 'N/A')}")
    lines.append(f"ATS Score: {job.get('ats_score', 0):.1f}%")

    if listing:
        salary_min = listing.get("salary_min")
        salary_max = listing.get("salary_max")
        if salary_min is not None and salary_max is not None:
            lines.append(f"Salary:   £{int(salary_min):,} – £{int(salary_max):,}")
        elif salary_min is not None:
            lines.append(f"Salary:   £{int(salary_min):,}+")
        lines.append(f"URL:      {listing.get('url', 'N/A')}")

    if app:
        matched_raw = app.get("matched_projects") or "[]"
        try:
            projects: list[str] = json.loads(matched_raw)
        except (json.JSONDecodeError, TypeError):
            projects = []
        if projects:
            lines.append(f"Matched:  {', '.join(projects[:3])}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Search config management
# ---------------------------------------------------------------------------


def update_search_config(args: str) -> str:
    """Update job search configuration from a Telegram command.

    Supported commands:
        "add title NLP Engineer"
        "remove title NLP Engineer"
        "exclude company Workday"

    Returns:
        Confirmation message.
    """
    args = args.strip()
    config = load_search_config()

    # Parse "add title X"
    if args.lower().startswith("add title "):
        title = args[len("add title "):].strip()
        if title and title not in config.titles:
            config.titles.append(title)
            save_search_config(config)
            return f"✅ Added title: '{title}'\nSearching: {', '.join(config.titles)}"
        elif title in config.titles:
            return f"'{title}' is already in your search titles."
        return "Please specify a title to add."

    # Parse "remove title X"
    if args.lower().startswith("remove title "):
        title = args[len("remove title "):].strip()
        if title in config.titles:
            config.titles.remove(title)
            save_search_config(config)
            return f"✅ Removed title: '{title}'\nSearching: {', '.join(config.titles) or '(none)'}"
        return f"'{title}' not found in your search titles."

    # Parse "exclude company X"
    if args.lower().startswith("exclude company "):
        company = args[len("exclude company "):].strip()
        if company and company not in config.exclude_companies:
            config.exclude_companies.append(company)
            save_search_config(config)
            return f"✅ Added '{company}' to excluded companies."
        elif company in config.exclude_companies:
            return f"'{company}' is already excluded."
        return "Please specify a company to exclude."

    # Unknown command
    return (
        "Unknown search config command. Supported:\n"
        "  search: add title <title>\n"
        "  search: remove title <title>\n"
        "  search: exclude company <name>"
    )


# ---------------------------------------------------------------------------
# Follow-up checker (9am cron)
# ---------------------------------------------------------------------------


def check_follow_ups() -> str:
    """Check for applications due for follow-up today.

    Uses followup_tracker for urgency-based prioritisation when available,
    falls back to DB-based follow_up_date query.

    Returns:
        Summary string.
    """
    # Try followup_tracker for richer urgency-based report
    try:
        from jobpulse.followup_tracker import compute_urgency, get_followup_count, format_followup_report, FollowUpEntry
        db = JobDB()
        applied = db.get_applications_by_status("Applied")
        entries = []
        for app in applied:
            applied_date_str = app.get("applied_at") or app.get("created_at", "")
            if not applied_date_str:
                continue
            applied_date = date.fromisoformat(applied_date_str[:10])
            job_id = app.get("job_id", "")
            count = get_followup_count(job_id)
            urgency = compute_urgency("applied", applied_date, count)
            if urgency in ("overdue", "urgent"):
                entries.append(FollowUpEntry(
                    job_id=job_id,
                    company=app.get("company", "N/A"),
                    role=app.get("title", "N/A"),
                    status="applied",
                    urgency=urgency,
                    next_followup_date=None,
                    days_until_next=(date.today() - applied_date).days,
                    followup_count=count,
                ))
        if entries:
            msg = format_followup_report(entries)
            send_jobs(msg)
            logger.info("job_autopilot: check_follow_ups — %d urgent/overdue entries", len(entries))
            return msg
    except Exception as exc:
        logger.debug("job_autopilot: followup_tracker failed, falling back: %s", exc)

    # Fallback: DB-based follow_up_date query
    db = JobDB()
    today = date.today()
    due = db.get_follow_ups_due(today)

    if not due:
        msg = f"No follow-ups due today ({today.isoformat()})."
        logger.info("job_autopilot: check_follow_ups — %s", msg)
        return msg

    lines = [f"📬 {len(due)} follow-up(s) due today ({today.strftime('%d %b')}):\n"]
    for i, app in enumerate(due, start=1):
        lines.append(f"{i}. {app.get('title', 'N/A')} — {app.get('company', 'N/A')}")
        lines.append(f"   Applied: {app.get('applied_at', 'N/A')[:10]}")
        lines.append(f"   URL: {app.get('url', 'N/A')}")
        lines.append("")

    lines.append("Send a follow-up email to keep your application visible!")

    msg = "\n".join(lines)
    send_jobs(msg)
    logger.info("job_autopilot: check_follow_ups — sent reminder for %d application(s)", len(due))
    return msg
