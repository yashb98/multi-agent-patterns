"""Job Autopilot Orchestrator — top-level pipeline coordinator.

Ties together all Job Autopilot pipeline tasks:
  L1: Scan platforms  (job_scanner)
  L2: Analyze JDs     (jd_analyzer)
  L3: Deduplicate     (job_deduplicator)
  L4: Match projects  (github_matcher)
  L5: CV PDF deferred until apply (application_materials.ensure_tailored_cv_for_job)
  L6: Cover letter lazy during form fill (NativeFormFiller + _cl_generator)
  L7: Score & tier    (determine_match_tier — inline)
  L8: Apply / queue   (applicator)
  L9: Notify          (telegram_bots)

External entry points (called by dispatcher.py):
  run_scan_window(platforms)   — full pipeline for one scheduled window
  run_linkedin_scan_with_notion_cleanup() — trash non-terminal Job Tracker Notion rows + LinkedIn scan
  approve_jobs(args)           — approve pending review jobs from Telegram
  apply_pending_job_from_cli() — live pipeline from CLI (job-apply-next, job-apply-found-today)
  reject_job(args)             — reject/skip a job from Telegram
  get_job_detail(args)         — full details for job number N
  update_search_config(args)   — mutate search config from Telegram
  check_follow_ups()           — daily follow-up reminder (9am cron)
  set_autopilot_paused(paused) — pause/resume autopilot
  is_paused()                  — check pause state
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from shared.locks import system_lock

from jobpulse.applicator import apply_job
from jobpulse.config import DATA_DIR, JOB_AUTOPILOT_ENABLED, JOB_AUTOPILOT_AUTO_SUBMIT, JOB_AUTOPILOT_MAX_DAILY
from jobpulse.cv_templates.generate_cv import build_extra_skills, get_role_profile
from jobpulse.job_db import JobDB
from jobpulse.job_notion_sync import (
    fetch_found_jobs_from_notion,
    get_notion_page_status,
    update_application_page,
)
from jobpulse.job_scanner import load_search_config, save_search_config
from jobpulse.process_logger import ProcessTrail
from jobpulse.telegram_bots import send_jobs
from shared.alerting import send_pipeline_alert

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


def _pending_jobs_dicts_from_db_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape DB join rows into the pending-review JSON entries."""

    def _sort_key(row: dict[str, Any]) -> tuple[str, str]:
        return (str(row.get("updated_at") or ""), str(row.get("created_at") or ""))

    return [
        {
            "job_id": row["job_id"],
            "title": row.get("title", ""),
            "company": row.get("company", ""),
            "platform": row.get("platform", "generic"),
            "location": row.get("location", ""),
            "ats_score": round(float(row.get("ats_score") or 0), 1),
        }
        for row in sorted(rows, key=_sort_key, reverse=True)
    ]


def _rebuild_pending_from_notion(*, found_on: date | None = None) -> list[dict[str, Any]]:
    """Fetch actionable jobs from the Notion Job Tracker (Status = 'Found').

    This is the primary source of truth for which jobs are available to apply.
    Falls back to SQLite if the Notion query fails or returns empty.
    """
    try:
        rows = fetch_found_jobs_from_notion(found_on=found_on)
    except Exception as exc:
        logger.warning("job_autopilot: Notion fetch failed, falling back to SQLite: %s", exc)
        rows = []

    if rows:
        pending = [
            {
                "notion_page_id": row["notion_page_id"],
                "title": row["title"],
                "company": row["company"],
                "platform": row["platform"],
                "location": row.get("location", ""),
                "url": row.get("url", ""),
                "ats_score": round(row.get("ats_score", 0), 1),
                "ats_platform": row.get("ats_platform"),
                "found_date": row.get("found_date", ""),
                "salary": row.get("salary", ""),
                "matched_projects": row.get("matched_projects", []),
            }
            for row in rows
        ]
        _save_pending(pending)
        logger.info("job_autopilot: rebuilt pending from Notion Job Tracker (%d jobs)", len(pending))
        return pending

    return _rebuild_pending_from_db_fallback()


def _rebuild_pending_from_db_fallback() -> list[dict[str, Any]]:
    """Fallback: rehydrate from SQLite when Notion is unavailable."""
    db = JobDB()
    rows = db.get_applications_by_status("Pending Approval") + db.get_applications_by_status("Ready")
    if not rows:
        return []

    rebuilt = _pending_jobs_dicts_from_db_rows(rows)
    _save_pending(rebuilt)
    logger.info("job_autopilot: rebuilt pending from SQLite fallback (%d jobs)", len(rebuilt))
    return rebuilt


def parse_job_apply_next_cli(argv: list[str]) -> tuple[str, date | None]:
    """Parse CLI argv for ``job-apply-next [index] [YYYY-MM-DD]``.

    Examples: ``job-apply-next``, ``job-apply-next 2``, ``job-apply-next 2026-04-23``,
    ``job-apply-next 2 2026-04-23``.
    """
    parts = argv[2:] if len(argv) > 2 else []
    idx = "1"
    found_on: date | None = None
    for p in parts:
        if len(p) >= 10 and p[4] == "-" and p[7] == "-":
            try:
                found_on = date.fromisoformat(p[:10])
            except ValueError:
                continue
        elif p.strip().isdigit():
            idx = str(int(p.strip()))
    return idx, found_on


def _load_actionable_pending() -> list[dict[str, Any]]:
    """Return pending-review entries from the local cache, rebuilding from Notion if empty.

    The file-backed queue preserves the user-visible ordering between
    ``show jobs`` and ``apply N``. When the cache is missing or empty,
    it is rebuilt from the Notion Job Tracker (Status = 'Found').
    Individual Notion status validation happens in ``approve_jobs()``
    before any application is started.
    """
    pending = _load_pending()
    if not pending:
        pending = _rebuild_pending_from_notion()
    return pending


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


def _get_event_store():
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None


_scan_lock = system_lock("jobpulse_scan_window")


def run_linkedin_scan_with_notion_cleanup() -> str:
    """Trash non-terminal Job Tracker Notion rows, then run LinkedIn-only scan."""
    from jobpulse.job_notion_sync import delete_job_tracker_non_terminal_pages

    n = delete_job_tracker_non_terminal_pages()
    logger.info(
        "job_autopilot: deleted (trashed) %d non-terminal Job Tracker Notion page(s) before scan",
        n,
    )
    return run_scan_window(["linkedin"])


def run_scan_window(platforms: list[str] | None = None) -> str:
    """Execute one scan window — the full pipeline.

    Thread-safe: uses a lock to prevent concurrent pipeline runs (cron + Telegram).

    Steps:
    1. Check if enabled / paused / daily cap
    2. Scan platforms
    3. Analyze JDs → JobListing objects
    4. Deduplicate
    5. For each new job: save, Notion, match projects, ATS score (CV/CL PDFs at apply time)
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
      4. generate_materials     — ATS + Gate 4B from synthetic CV text (per job); PDFs at apply
    """
    from jobpulse.scan_pipeline import (
        fetch_and_filter_jobs,
        analyze_and_deduplicate,
        prescreen_listings,
        generate_materials,
    )
    from jobpulse.pipeline_hooks import (
        with_ghost_detection,
        with_archetype_detection,
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

    try:
        from jobpulse.browser_cleanup import reset_app_counter
        reset_app_counter()
    except Exception:
        pass

    _evt = _get_event_store()
    _stream_id = f"scan:{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M')}" if _evt else ""
    if _evt:
        _evt.emit(_stream_id, "scan.window_started", {
            "platforms": platforms or ["linkedin", "indeed", "reed"],
            "daily_cap": JOB_AUTOPILOT_MAX_DAILY,
            "already_applied": already_applied,
        })

    # --- Stage 1: fetch and filter ---
    search_config = load_search_config()
    raw_jobs, total_found, gate0_rejected = fetch_and_filter_jobs(platforms, search_config, trail)

    if _evt:
        _evt.emit(_stream_id, "scan.jobs_found", {
            "total_found": total_found,
            "gate0_rejected": gate0_rejected,
            "raw_count": len(raw_jobs),
        })

    # --- Stage 2: analyze JDs and deduplicate ---
    new_listings = analyze_and_deduplicate(raw_jobs, db, trail)

    # --- Stage 2.5: Ghost detection (F2) ---
    pre_ghost = len(new_listings)
    new_listings = with_ghost_detection(
        new_listings,
        {l.job_id: getattr(l, "description_raw", "") for l in new_listings},
    )
    ghost_blocked = pre_ghost - len(new_listings)
    if ghost_blocked:
        trail.log_step("decision", "Ghost detection", step_output=f"{ghost_blocked} blocked")

    # --- Stage 3: pre-screen (Gates 1-3 and Gate 4A) ---
    gate4_filtered, gate_rejected, gate_skipped, gate4_blocked = prescreen_listings(
        new_listings, db, trail,
    )

    # --- Stage 4: generate materials (per job) ---
    repos: list[dict] = []  # shared cache across jobs
    review_batch: list[dict[str, Any]] = []
    errors = 0

    # --- Stage 4+5: generate materials and route by tier ---
    auto_applied_count = 0
    remaining_cap = max(0, JOB_AUTOPILOT_MAX_DAILY - already_applied)

    for listing, screen in gate4_filtered:
        try:
            with_archetype_detection(listing)
            bundle = generate_materials(listing, screen, db, repos, notion_failures)

            # Draft-only mode: always queue for human review, never auto-submit
            _queue_for_review(listing, bundle.ats_score, review_batch)
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
        f"Ready for review: {len(review_batch)} (one live application at a time)",
    ]
    if errors:
        summary_lines.append(f"Errors: {errors}")
        send_pipeline_alert(
            f"Job scan encountered {errors} error(s) during processing.\n"
            f"Check logs for details.",
            severity="warning",
            category="scan",
        )

    # Add Job Tracker link
    try:
        from jobpulse.config import NOTION_APPLICATIONS_DB_ID
        if NOTION_APPLICATIONS_DB_ID:
            app_db_clean = NOTION_APPLICATIONS_DB_ID.replace("-", "")
            summary_lines.append(f"\n📎 Job Tracker: https://www.notion.so/{app_db_clean}")
    except Exception as exc:
        logger.debug("job tracker link failed: %s", exc)

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
        f"Scan complete: {len(review_batch)} for review"
    )

    if notion_failures:
        logger.warning("job_autopilot: %d Notion sync failures this run", len(notion_failures))

    if _evt:
        _evt.emit(_stream_id, "scan.window_done", {
            "total_found": total_found,
            "auto_applied": auto_applied_count,
            "review_count": len(review_batch),
            "errors": errors,
        })

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
    lines = [f"📋 {len(jobs)} job{'s' if len(jobs) != 1 else ''} ready for live review:"]
    lines.append("")

    for i, job in enumerate(jobs, start=1):
        ats_display = f"{job['ats_score']:.0f}%"
        lines.append(f"{i}. {job['title']} — {job['company']} ({job['platform']})")
        lines.append(f"   ATS: {ats_display} | {job['location']}")
        lines.append("")

    lines.append('Reply: "apply 1" to open one live application, or "reject 2" to skip one.')
    send_jobs("\n".join(lines))


# ---------------------------------------------------------------------------
# Telegram-callable approval / rejection
# ---------------------------------------------------------------------------


def apply_pending_job_from_cli(args: str = "1", *, found_on: date | None = None) -> str:
    """Start the live application pipeline for a queued job (CLI / runner).

    Retrieves the job queue from the Notion Job Tracker (Status = 'Found').
    When ``found_on`` is set, only jobs whose Found Date matches are shown.
    Refuses to start if another live review session is active.
    """
    pending_rows: list[dict[str, Any]] | None = None
    queue_size: int
    if found_on is not None:
        pending_rows = _rebuild_pending_from_notion(found_on=found_on)
        if not pending_rows:
            return (
                f"No jobs with status 'Found' in Notion dated "
                f"{found_on.isoformat()}. Run a scan that day first."
            )
        queue_size = len(pending_rows)
    else:
        pending = _rebuild_pending_from_notion()
        if not pending:
            return (
                "No jobs with status 'Found' in the Notion Job Tracker. "
                "Run a job scan first."
            )
        queue_size = len(pending)

    logger.info(
        "apply_pending_job_from_cli: found_on=%s queue_size=%d index=%s",
        found_on.isoformat() if found_on else "any",
        queue_size,
        (args or "1").strip(),
    )

    try:
        from jobpulse.live_review_applicator import get_active_review

        active = get_active_review()
        if active:
            return (
                "A live review is already in progress: "
                f"{active.get('title', '?')} @ {active.get('company', '?')}. "
                "Finish it in Telegram (yes/no on submit), then run:\n"
                "  python -m jobpulse.runner job-apply-next"
            )
    except Exception as exc:
        logger.debug("job_autopilot: apply_pending_job_from_cli active check: %s", exc)

    return approve_jobs(args, pending_rows=pending_rows)


def approve_jobs(args: str, *, pending_rows: list[dict[str, Any]] | None = None) -> str:
    """Approve pending review jobs.

    Args:
        args: "1" — 1-based index into the pending review list.
        pending_rows: Optional in-memory queue (e.g. filtered by listing ``found_at``).

    Returns:
        Summary message to send back to user.
    """
    if pending_rows is not None:
        pending = pending_rows
    else:
        pending = _load_actionable_pending()
    if not pending:
        return "No jobs with status 'Found' in the Notion Job Tracker. Run a scan first."

    args = args.strip().lower()
    if not args:
        return "Use: apply 1"
    if args == "all" or "," in args or " " in args:
        return "One live application at a time. Use a single index, for example: apply 1"
    try:
        idx = int(args) - 1
    except ValueError:
        return f"Could not parse job number: '{args}'. Use e.g. apply 1"
    if idx < 0 or idx >= len(pending):
        return f"Job #{args} not found. There are {len(pending)} pending jobs."

    job = pending[idx]
    notion_page_id = job.get("notion_page_id", "")

    # ── Notion status gate ─────────────────────────────────────
    if notion_page_id:
        notion_status = get_notion_page_status(notion_page_id)
        if notion_status and notion_status != "Found":
            logger.info(
                "job_autopilot: skipping %s @ %s — Notion status is '%s', not 'Found'",
                job["title"], job["company"], notion_status,
            )
            return (
                f"⏭️ Skipped: {job['title']} — {job['company']}\n"
                f"Notion status is \"{notion_status}\" (expected \"Found\").\n"
                f"Only jobs with status \"Found\" in the Notion Job Tracker are picked up."
            )
        if notion_status is None:
            logger.warning(
                "job_autopilot: could not read Notion status for %s (page %s) — proceeding with caution",
                job["title"], notion_page_id,
            )
    else:
        logger.warning(
            "job_autopilot: no notion_page_id for %s @ %s — skipping Notion gate",
            job["title"], job["company"],
        )

    # ── URL from Notion ───────────────────────────────────────
    url = job.get("url", "")
    if not url:
        return f"Job #{args} ({job['title']} — {job['company']}) is missing its JD URL in Notion."

    # ── Cross-reference SQLite for local data (CV path, job_id) ─
    db = JobDB()
    app: dict[str, Any] = {}
    job_id = job.get("job_id", "")
    if notion_page_id:
        app = db.get_application_by_notion_page_id(notion_page_id) or {}
        job_id = app.get("job_id", job_id)

    if job_id:
        try:
            from jobpulse.application_materials import ensure_tailored_cv_for_job

            ensure_tailored_cv_for_job(job_id)
            app = db.get_application(job_id) or app
        except Exception as exc:
            logger.warning("job_autopilot: ensure CV before live review failed: %s", exc)

    payload = {
        "job_id": job_id,
        "title": job["title"],
        "company": job["company"],
        "url": url,
        "platform": job.get("platform", "generic"),
        "ats_platform": job.get("ats_platform"),
        "ats_score": job.get("ats_score", 0),
        "cv_path": app.get("cv_path"),
        "cover_letter_path": app.get("cover_letter_path"),
        "custom_answers": {
            "_job_context": {
                "job_title": job.get("title", ""),
                "company": job.get("company", ""),
                "location": job.get("location", ""),
            },
        },
        "notion_page_id": notion_page_id,
        "match_tier": app.get("match_tier"),
        "matched_projects": app.get("matched_projects") or job.get("matched_projects"),
    }

    from jobpulse.live_review_applicator import start_live_review

    launch = start_live_review(payload)
    if not launch.get("started"):
        return launch.get("message", "A live review session is already active.")

    return "\n".join(
        [
            f"🧭 Starting live review for {job['title']} @ {job['company']}.",
            "",
            "I'm opening the application, filling it one job at a time, and stopping",
            "right before submit. When it is ready, review it in Chrome and reply",
            "`yes` to submit or `no` to keep it pending.",
        ]
    )


def reject_job(args: str) -> str:
    """Reject/skip a pending review job.

    Updates Notion (primary) and SQLite (secondary) to 'Skipped'.

    Args:
        args: "2" — 1-based index of the job in the pending review list.

    Returns:
        Confirmation message.
    """
    pending = _load_actionable_pending()
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
    notion_page_id = job.get("notion_page_id", "")

    # Update Notion first (primary source of truth)
    if notion_page_id:
        try:
            update_application_page(notion_page_id, status="Skipped")
        except Exception as exc:
            logger.warning("job_autopilot: reject Notion update failed: %s", exc)

    # Update SQLite if cross-reference exists (secondary)
    db = JobDB()
    job_id = job.get("job_id", "")
    if not job_id and notion_page_id:
        app = db.get_application_by_notion_page_id(notion_page_id)
        if app:
            job_id = app["job_id"]
    if job_id:
        db.update_status(job_id, "Skipped")

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


def show_pending_jobs() -> str:
    """Return the pending-review list from Notion Job Tracker, aligned with ``apply N`` numbering.

    Always refreshes from Notion to show the latest state.
    """
    pending = _rebuild_pending_from_notion()

    active_review: dict[str, Any] | None = None
    try:
        from jobpulse.live_review_applicator import get_active_review

        active_review = get_active_review()
    except Exception as exc:
        logger.debug("job_autopilot: active review lookup failed: %s", exc)

    if not pending and not active_review:
        return "No jobs with status 'Found' in the Notion Job Tracker. Try 'job stats' for today's numbers."

    lines: list[str] = []
    if active_review:
        lines.extend(
            [
                "🟡 Currently reviewing:",
                f"{active_review['title']} — {active_review['company']} ({active_review['platform']})",
                "Reply `yes` to submit or `no` to keep it pending.",
                "",
            ]
        )

    if pending:
        lines.append(f"📋 {len(pending)} jobs found in Notion Job Tracker:\n")
        for i, job in enumerate(pending[:15], 1):
            lines.append(f"{i}. {job['title']} — {job['company']} ({job['platform']})")
            lines.append(f"   ATS: {job.get('ats_score', 0)}% | {job.get('location', 'UK')}")
        lines.append("")
        lines.append('Reply: "apply 1" to open one live application, or "reject 2" to skip one.')

    return "\n".join(lines).strip()


def get_job_detail(args: str) -> str:
    """Return full details for a pending review job.

    All data sourced from the Notion Job Tracker (cached in pending list).

    Args:
        args: "3" — 1-based index of the job in the pending review list.

    Returns:
        Formatted string with title, company, platform, location, salary, ATS score,
        matched projects, and URL.
    """
    pending = _load_actionable_pending()
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

    lines: list[str] = [f"💼 Job #{args}: {job['title']}"]
    lines.append(f"Company:  {job['company']}")
    lines.append(f"Platform: {job['platform']}")
    lines.append(f"Location: {job.get('location', 'N/A')}")
    lines.append(f"ATS Score: {job.get('ats_score', 0):.1f}%")

    salary = job.get("salary", "")
    if salary:
        lines.append(f"Salary:   {salary}")

    url = job.get("url", "")
    if url:
        lines.append(f"URL:      {url}")

    matched_projects = job.get("matched_projects", [])
    if matched_projects:
        lines.append(f"Matched:  {', '.join(matched_projects[:3])}")

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
