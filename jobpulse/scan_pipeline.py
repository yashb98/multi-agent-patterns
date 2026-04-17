"""Scan pipeline stages — extracted from _run_scan_window_inner.

Each function handles one stage of the job scan pipeline and is independently
testable. The stages are:

  1. fetch_and_filter_jobs   — scan platforms + liveness check + Gate 0 title filter
  2. analyze_and_deduplicate — analyze JDs + cross-platform deduplication
  3. prescreen_listings      — Gates 1-3 (SkillGraphStore) + Gate 4A (quality/blocklist)
  4. generate_materials      — match projects + CV PDF + ATS score + Gate 4B CV scrutiny
  5. route_and_apply         — classify action → auto-apply / queue for review / skip

Called by _run_scan_window_inner() in job_autopilot.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

# Module-level imports so each name can be patched in tests.
from jobpulse.applicator import apply_job, classify_action
from jobpulse.company_blocklist import BlocklistCache, detect_spam_company, flag_company_in_notion
from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
from jobpulse.cv_templates.generate_cv import (
    BASE_SKILLS,
    EDUCATION,
    EXPERIENCE,
    build_extra_skills,
    generate_cv_pdf,
    get_role_profile,
)
from jobpulse.drive_uploader import upload_cover_letter, upload_cv
from jobpulse.gate4_quality import (
    check_company_background,
    check_jd_quality,
    scrutinize_cv_deterministic,
    scrutinize_cv_llm,
)
from jobpulse.github_matcher import fetch_and_cache_repos, pick_top_projects
from jobpulse.jd_analyzer import analyze_jd
from jobpulse.job_deduplicator import deduplicate
from jobpulse.project_portfolio import get_best_projects_for_jd
from jobpulse.skill_graph_store import SkillGraphStore
from jobpulse.job_notion_sync import (
    build_page_content,
    create_application_page,
    set_page_content,
    update_application_page,
)
from jobpulse.job_scanner import check_liveness_batch, scan_platforms
from jobpulse.recruiter_screen import gate0_title_relevance
from jobpulse.ats_scorer import score_ats
from jobpulse.pipeline_hooks import (
    enhanced_generate_materials,
    with_ghost_detection,
    with_archetype_detection,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tier / routing helpers (mirrored from job_autopilot to avoid circular import)
# ---------------------------------------------------------------------------


def determine_match_tier(ats_score: float) -> str:
    """Return 'auto' if >= 90, 'review' if >= 82, 'skip' otherwise."""
    if ats_score >= 90:
        return "auto"
    if ats_score >= 82:
        return "review"
    return "skip"


def _queue_for_review(listing: Any, ats_score: float, batch: list[dict]) -> None:
    """Mark listing as pending approval and append to review batch."""
    from jobpulse.job_db import JobDB

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


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class MaterialsBundle:
    """Output of generate_materials() — everything needed to route and apply."""

    cv_path: Path | None = None
    cv_text: str = ""
    cover_letter_path: str | None = None
    ats_score: float = 0.0
    matched_project_names: list[str] = field(default_factory=list)
    matched_projects: list[dict] = field(default_factory=list)
    cv_drive_link: str | None = None
    cl_drive_link: str | None = None
    gate4b_notes: str = ""
    notion_page_id: str | None = None
    notion_status: str = "Ready"


@dataclass
class RouteResult:
    """Outcome of route_and_apply() for one job."""

    action: str  # "auto_applied" | "queued_for_review" | "skipped" | "error"
    job_id: str
    title: str
    company: str


# ---------------------------------------------------------------------------
# Stage 1: fetch and filter
# ---------------------------------------------------------------------------


def fetch_and_filter_jobs(
    platforms: list[str] | None,
    search_config: Any,
    trail: Any,
) -> tuple[list[dict], int, int]:
    """Scan platforms, check liveness, and apply Gate 0 title filter.

    Args:
        platforms: Platform names to scan, or None for all configured platforms.
        search_config: Loaded search config object (from load_search_config()).
        trail: ProcessTrail instance for audit logging.

    Returns:
        Tuple of (filtered_raw_jobs, total_found, gate0_rejected_count).
    """
    # Scan platforms
    trail.log_step("api_call", "Scan platforms", step_input=str(platforms))
    try:
        raw_jobs = scan_platforms(platforms)
    except Exception as exc:
        logger.error("scan_pipeline: scan_platforms failed: %s", exc)
        raw_jobs = []

    total_found = len(raw_jobs)
    trail.log_step(
        "api_call", "Platforms scanned",
        step_output=f"{total_found} raw jobs found",
    )

    # Liveness check — filter expired postings
    try:
        alive_listings, expired_listings = check_liveness_batch(raw_jobs)
        if expired_listings:
            logger.info("scan_pipeline: liveness filtered %d expired postings", len(expired_listings))
        trail.log_step(
            "api_call", "Liveness check",
            step_output=f"{len(alive_listings)} alive, {len(expired_listings)} expired",
        )
        raw_jobs = alive_listings
    except Exception as exc:
        logger.warning("scan_pipeline: liveness check failed: %s — continuing with all jobs", exc)

    # Gate 0 — title relevance filter
    titles = (
        search_config.titles
        if hasattr(search_config, "titles")
        else search_config.get("titles", [])
    )
    exclude_keywords = (
        search_config.exclude_keywords
        if hasattr(search_config, "exclude_keywords")
        else search_config.get("exclude_keywords", [
            "senior", "lead", "principal", "staff", "director", "manager",
            "10+ years", "8+ years", "7+ years", "5+ years",
        ])
    )
    gate0_config = {"titles": titles, "exclude_keywords": exclude_keywords}

    filtered_jobs = []
    gate0_rejected = 0
    for raw in raw_jobs:
        title = raw.get("title", "")
        jd_snippet = raw.get("description", "")[:500]
        if gate0_title_relevance(title, jd_snippet, gate0_config):
            filtered_jobs.append(raw)
        else:
            gate0_rejected += 1

    trail.log_step(
        "decision", "Gate 0: Title filter",
        step_output=f"{len(filtered_jobs)} passed, {gate0_rejected} rejected",
    )

    return filtered_jobs, total_found, gate0_rejected


# ---------------------------------------------------------------------------
# Stage 2: analyze JDs and deduplicate
# ---------------------------------------------------------------------------


def analyze_and_deduplicate(
    raw_jobs: list[dict],
    db: Any,
    trail: Any,
) -> list[Any]:
    """Analyze JDs with jd_analyzer and deduplicate against the DB.

    Args:
        raw_jobs: Raw job dicts from scan/filter stage.
        db: JobDB instance.
        trail: ProcessTrail instance.

    Returns:
        List of new (not-yet-seen) JobListing objects.
    """
    trail.log_step("llm_call", "Analyze JDs", step_input=f"{len(raw_jobs)} raw jobs")
    listings = []
    for raw in raw_jobs:
        try:
            listing = analyze_jd(
                url=raw.get("url", ""),
                title=raw.get("title", ""),
                company=raw.get("company", ""),
                platform=raw.get("platform", "reed"),
                jd_text=raw.get("description", ""),
                apply_url=raw.get("apply_url", raw.get("url", "")),
            )
            listings.append(listing)
        except Exception as exc:
            logger.warning(
                "scan_pipeline: analyze_jd failed for %r @ %r: %s",
                raw.get("title"),
                raw.get("company"),
                exc,
            )

    trail.log_step("llm_call", "JDs analyzed", step_output=f"{len(listings)} listings")

    new_listings = deduplicate(listings, db)
    trail.log_step(
        "decision", "Deduplicated",
        step_output=f"{len(new_listings)} new (filtered {len(listings) - len(new_listings)})",
    )

    return new_listings


# ---------------------------------------------------------------------------
# Stage 3: pre-screen (Gates 1-3 and Gate 4A)
# ---------------------------------------------------------------------------


def prescreen_listings(
    new_listings: list[Any],
    db: Any,
    trail: Any,
) -> tuple[list[tuple], int, int, int]:
    """Apply Gates 1-3 (SkillGraphStore) and Gate 4A (quality/blocklist) filters.

    Args:
        new_listings: Deduplicated JobListing objects.
        db: JobDB instance.
        trail: ProcessTrail instance.

    Returns:
        Tuple of (gate4_filtered, gate_rejected, gate_skipped, gate4_blocked).
        gate4_filtered is a list of (listing, screen_or_None) tuples ready for material generation.
    """
    # --- Gates 1-3: SkillGraphStore pre-screen ---
    try:
        store = SkillGraphStore()
    except Exception as exc:
        logger.warning("scan_pipeline: SkillGraphStore init failed: %s — skipping pre-screen", exc)
        store = None

    screened_listings: list[tuple] = []
    gate_rejected = 0
    gate_skipped = 0

    for listing in new_listings:
        if store is None:
            screened_listings.append((listing, None))
            continue

        screen = store.pre_screen_jd(listing)

        # Record skill gaps for ALL tiers
        try:
            from jobpulse.skill_gap_tracker import record_gap
            record_gap(
                job_id=listing.job_id,
                title=listing.title,
                company=listing.company,
                missing_skills=screen.missing_skills,
                matched_skills=screen.matched_skills,
                gate3_score=screen.gate3_score,
            )
        except Exception as exc:
            logger.debug("scan_pipeline: skill_gap_tracker.record_gap failed: %s", exc)

        # Sync missing skills to Notion Skill Tracker
        try:
            from jobpulse.skill_tracker_notion import sync_skills_to_notion
            if screen and screen.missing_skills:
                sync_skills_to_notion(screen.missing_skills, listing.company)
        except Exception as exc:
            logger.debug("scan_pipeline: skill_tracker_notion sync failed: %s", exc)

        if screen.tier == "reject":
            gate_rejected += 1
            logger.info(
                "scan_pipeline: REJECTED %s @ %s — %s",
                listing.title, listing.company, screen.gate1_kill_reason,
            )
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Rejected", match_tier="reject")
            continue

        if screen.tier == "skip":
            gate_skipped += 1
            reason = screen.gate2_fail_reason or f"Score {screen.gate3_score}/100"
            logger.info(
                "scan_pipeline: SKIPPED %s @ %s — %s",
                listing.title, listing.company, reason,
            )
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Skipped", match_tier="skip")
            continue

        screened_listings.append((listing, screen))

    trail.log_step(
        "decision", "Gates 1-3 pre-screen",
        step_output=f"{len(screened_listings)} pass, {gate_rejected} rejected, {gate_skipped} skipped",
    )

    # --- Gate 4 Phase A: Pre-generation quality check ---
    blocklist = BlocklistCache()
    try:
        blocklist.refresh()
    except Exception as exc:
        logger.warning("scan_pipeline: blocklist refresh failed: %s", exc)

    gate4_filtered: list[tuple] = []
    gate4_blocked = 0

    for listing, screen in screened_listings:
        # A2: Company blocklist check
        if blocklist.is_blocked(listing.company):
            gate4_blocked += 1
            logger.info("scan_pipeline: Gate 4 BLOCKED (blocklist) %s @ %s", listing.title, listing.company)
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Blocked", match_tier="skip")
            continue

        # A2: Spam detection
        if not blocklist.is_approved(listing.company) and not blocklist.is_known(listing.company):
            spam = detect_spam_company(listing.company)
            if spam.is_spam:
                gate4_blocked += 1
                logger.info(
                    "scan_pipeline: Gate 4 BLOCKED (spam) %s @ %s — %s",
                    listing.title, listing.company, spam.reason,
                )
                try:
                    flag_company_in_notion(listing.company, spam.reason, listing.platform)
                except Exception as e:
                    logger.warning("scan_pipeline: flag_company_in_notion failed for %s: %s", listing.company, e)
                db.save_listing(listing)
                db.save_application(job_id=listing.job_id, status="Blocked", match_tier="skip")
                continue

        # A1: JD quality check
        jd_quality = check_jd_quality(
            listing.description_raw or "",
            listing.required_skills + listing.preferred_skills,
        )
        if not jd_quality.passed:
            gate4_blocked += 1
            logger.info(
                "scan_pipeline: Gate 4 BLOCKED (JD quality) %s @ %s — %s",
                listing.title, listing.company, jd_quality.reason,
            )
            db.save_listing(listing)
            db.save_application(job_id=listing.job_id, status="Skipped", match_tier="skip")
            continue

        # A3: Company background (soft flags)
        try:
            past_apps = db.get_applications_by_company(listing.company)
        except (AttributeError, Exception):
            past_apps = []
        bg = check_company_background(listing.company, past_apps)
        if bg.previously_applied:
            logger.info("scan_pipeline: Gate 4 NOTE — %s", bg.note)
        if bg.is_generic:
            logger.info("scan_pipeline: Gate 4 NOTE — generic company name: %s", listing.company)

        gate4_filtered.append((listing, screen))

    trail.log_step(
        "decision", "Gate 4 Phase A",
        step_output=f"{len(gate4_filtered)} pass, {gate4_blocked} blocked",
    )

    return gate4_filtered, gate_rejected, gate_skipped, gate4_blocked


# ---------------------------------------------------------------------------
# Stage 4: generate materials (CV, CL, ATS score, Gate 4B)
# ---------------------------------------------------------------------------


def generate_materials(
    listing: Any,
    screen: Any,
    db: Any,
    repos: list[dict],
    notion_failures: list[str],
) -> MaterialsBundle:
    """Generate CV, cover letter, ATS score, and run Gate 4B scrutiny for one job.

    Args:
        listing: JobListing object.
        screen: PreScreenResult or None from SkillGraphStore.
        db: JobDB instance.
        repos: Cached list of GitHub repos (may be mutated if empty on first call).
        notion_failures: Mutable list — Notion errors are appended here.

    Returns:
        MaterialsBundle with paths, scores, Notion page ID, and gate4b notes.
    """
    from jobpulse.config import DATA_DIR

    bundle = MaterialsBundle()

    # Save listing and create initial DB record
    db.save_listing(listing)
    db.save_application(job_id=listing.job_id, status="Analyzing")

    # Create Notion page
    notion_page_id: str | None = None
    try:
        notion_page_id = create_application_page(listing)
    except Exception as exc:
        logger.warning("scan_pipeline: Notion create failed for %s: %s", listing.job_id[:8], exc)
        notion_failures.append(f"{listing.title}: {exc}")
    if notion_page_id:
        db.save_application(job_id=listing.job_id, status="Analyzing", notion_page_id=notion_page_id)
    bundle.notion_page_id = notion_page_id

    # Match GitHub projects
    if screen and screen.best_projects:
        matched_project_names = [p.name for p in screen.best_projects[:4]]
    else:
        if not repos:
            try:
                fetched = fetch_and_cache_repos()
                repos.extend(fetched)
            except Exception as exc:
                logger.warning("scan_pipeline: fetch_and_cache_repos fallback: %s", exc)
        matched_repos = pick_top_projects(
            repos,
            jd_required=listing.required_skills,
            jd_preferred=listing.preferred_skills,
            top_n=4,
        )
        matched_project_names = [r.get("name", "") for r in matched_repos]
    bundle.matched_project_names = matched_project_names

    # Generate CV PDF
    cv_path = None
    cv_text = ""
    ats_score = 0.0
    matched_projects: list[dict] = []
    try:
        extra_skills = build_extra_skills(listing.required_skills, listing.preferred_skills)

        # Pre-generation: sync Notion Skill Tracker
        try:
            from jobpulse.skill_tracker_notion import sync_verified_to_profile
            sync_verified_to_profile()
        except Exception:
            pass  # Non-blocking

        # Dynamic project selection from MindGraph
        matched_projects = get_best_projects_for_jd(
            listing.required_skills, listing.preferred_skills,
        )

        role_profile = get_role_profile(listing.title)
        cv_path = generate_cv_pdf(
            company=listing.company,
            location=listing.location or "United Kingdom",
            tagline=role_profile.get("tagline"),
            summary=role_profile.get("summary"),
            projects=matched_projects,
            extra_skills=extra_skills if extra_skills else None,
            output_dir=str(DATA_DIR / "applications" / listing.job_id),
        )

        # ATS scoring
        cv_parts = [
            "PROFESSIONAL SUMMARY Software Engineer Python AI ML",
            "TECHNICAL SKILLS " + " ".join(BASE_SKILLS.values()),
        ]
        if extra_skills:
            cv_parts.append(" ".join(extra_skills.values()))
        cv_parts.append("PROJECTS " + " ".join(
            p["title"] + " " + " ".join(p["bullets"])
            for p in matched_projects
        ))
        cv_parts.append("EXPERIENCE " + " ".join(
            e["title"] + " " + " ".join(e["bullets"])
            for e in EXPERIENCE
        ))
        cv_parts.append("EDUCATION " + " ".join(
            e["degree"] + " " + e["institution"]
            for e in EDUCATION
        ))
        cv_text = " ".join(cv_parts)
        jd_skills = listing.required_skills + listing.preferred_skills
        ats_score_obj = score_ats(jd_skills, cv_text)
        ats_score = ats_score_obj.total
    except Exception as exc:
        logger.warning("scan_pipeline: generate_cv_pdf failed for %s: %s", listing.job_id[:8], exc)

    bundle.cv_path = cv_path
    bundle.cv_text = cv_text
    bundle.ats_score = ats_score
    bundle.matched_projects = matched_projects

    # Cover letter: generate upfront alongside CV
    cl_path = None
    cl_drive_link_val = None
    if cv_path:
        try:
            cl_path = generate_cover_letter_pdf(
                company=listing.company,
                role=listing.title,
                location=listing.location or "United Kingdom",
                matched_projects=matched_projects,
                required_skills=listing.required_skills + listing.preferred_skills,
                output_dir=str(DATA_DIR / "applications" / listing.job_id),
            )
            if cl_path:
                try:
                    cl_drive_link_val = upload_cover_letter(cl_path, listing.company)
                except Exception as e:
                    logger.warning("scan_pipeline: CL upload failed for %s: %s", listing.company, e)
        except Exception as exc:
            logger.warning("scan_pipeline: generate_cover_letter_pdf failed for %s: %s", listing.job_id[:8], exc)

    bundle.cover_letter_path = str(cl_path) if cl_path else None
    bundle.cl_drive_link = cl_drive_link_val

    # Gate 4 Phase B: CV quality scrutiny
    gate4b_notes = ""
    if cv_path and cv_text:
        b1_result = scrutinize_cv_deterministic(cv_text)
        if b1_result.warnings:
            gate4b_notes = "B1: " + "; ".join(b1_result.warnings)
            logger.info("scan_pipeline: Gate 4B warnings for %s: %s", listing.company, gate4b_notes)

        if b1_result.status in ("clean", "acceptable"):
            try:
                b2_result = scrutinize_cv_llm(
                    cv_text, listing.title, listing.company,
                    listing.required_skills, listing.preferred_skills,
                )
                if b2_result.needs_review:
                    weakness_str = "; ".join(b2_result.weaknesses[:3])
                    gate4b_notes += f" | B2: {b2_result.score}/10 — {weakness_str}"
                    logger.info(
                        "scan_pipeline: Gate 4B LLM score %d/10 for %s — %s",
                        b2_result.score, listing.company, weakness_str,
                    )
            except Exception as exc:
                logger.warning("scan_pipeline: Gate 4B LLM failed: %s", exc)

    bundle.gate4b_notes = gate4b_notes
    bundle.notion_status = "Needs Review" if (gate4b_notes and "B2:" in gate4b_notes) else "Ready"

    # Upload CV to Drive
    cv_drive_link = None
    if cv_path:
        try:
            cv_drive_link = upload_cv(cv_path, listing.company)
        except Exception as exc:
            logger.warning("scan_pipeline: Drive CV upload failed: %s", exc)
    bundle.cv_drive_link = cv_drive_link

    # Update DB with full analysis results
    tier = determine_match_tier(ats_score)
    db.save_application(
        job_id=listing.job_id,
        status=bundle.notion_status,
        ats_score=ats_score,
        match_tier=tier,
        matched_projects=matched_project_names,
        cv_path=str(cv_path) if cv_path else None,
        cover_letter_path=bundle.cover_letter_path,
        notion_page_id=notion_page_id,
    )

    # Update Notion with score, tier, and links
    if notion_page_id:
        try:
            update_application_page(
                notion_page_id,
                status=bundle.notion_status,
                ats_score=ats_score,
                match_tier=tier,
                matched_projects=matched_project_names,
                cv_drive_link=cv_drive_link,
                cl_drive_link=cl_drive_link_val,
                notes=gate4b_notes if gate4b_notes else None,
                company=listing.company,
            )
            page_blocks = build_page_content(
                job=listing,
                ats_score=ats_score,
                match_tier=tier,
                matched_projects=matched_project_names,
                cv_drive_link=cv_drive_link,
                cl_drive_link=cl_drive_link_val,
                cv_path=str(cv_path) if cv_path else None,
                cover_letter_path=bundle.cover_letter_path,
                gate4_notes=gate4b_notes,
            )
            set_page_content(notion_page_id, page_blocks)
        except Exception as exc:
            logger.warning(
                "scan_pipeline: Notion update failed for %s: %s",
                listing.job_id[:8], exc,
            )
            notion_failures.append(f"{listing.title}: {exc}")

    return bundle


# ---------------------------------------------------------------------------
# Stage 5: route and apply
# ---------------------------------------------------------------------------


def route_and_apply(
    listing: Any,
    bundle: MaterialsBundle,
    db: Any,
    review_batch: list[dict],
    remaining_cap: int,
    auto_applied: int,
) -> RouteResult:
    """Route one job to auto-apply, review queue, or skip based on ATS score.

    Args:
        listing: JobListing object.
        bundle: MaterialsBundle from generate_materials().
        db: JobDB instance.
        review_batch: Mutable list — review jobs appended here.
        remaining_cap: How many more auto-applications are allowed today.
        auto_applied: How many have already been auto-applied this run.

    Returns:
        RouteResult indicating what action was taken.
    """
    ats_score = bundle.ats_score
    tier = determine_match_tier(ats_score)
    notion_page_id = bundle.notion_page_id

    action = classify_action(ats_score, listing.easy_apply)

    if action in ("auto_submit", "auto_submit_with_preview"):
        if auto_applied >= remaining_cap:
            logger.info("scan_pipeline: daily cap reached — routing %s to review", listing.job_id[:8])
            _queue_for_review(listing, ats_score, review_batch)
            return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)

        if bundle.cv_path is None:
            logger.warning("scan_pipeline: no CV for auto-apply %s — routing to review", listing.job_id[:8])
            _queue_for_review(listing, ats_score, review_batch)
            return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)

        try:
            result = apply_job(
                url=listing.url,
                ats_platform=listing.ats_platform,
                cv_path=bundle.cv_path,
                cover_letter_path=bundle.cover_letter_path,
                cl_generator=None,
                custom_answers=None,
            )
            if result.get("success"):
                applied_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
                follow_up = (date.today() + timedelta(days=7)).isoformat()
                db.save_application(
                    job_id=listing.job_id,
                    status="Applied",
                    ats_score=ats_score,
                    match_tier=tier,
                    matched_projects=bundle.matched_project_names,
                    cv_path=str(bundle.cv_path),
                    cover_letter_path=str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
                    applied_at=applied_at,
                    notion_page_id=notion_page_id,
                    follow_up_date=follow_up,
                )
                if notion_page_id:
                    try:
                        update_application_page(
                            notion_page_id,
                            status="Applied",
                            applied_date=date.today(),
                            follow_up_date=date.today() + timedelta(days=7),
                        )
                    except Exception as exc:
                        logger.warning("scan_pipeline: Notion applied update failed: %s", exc)
                logger.info(
                    "scan_pipeline: AUTO-APPLIED %s @ %s (ATS %.1f%%)",
                    listing.title, listing.company, ats_score,
                )
                return RouteResult("auto_applied", listing.job_id, listing.title, listing.company)
            else:
                logger.warning(
                    "scan_pipeline: auto-apply failed for %s: %s",
                    listing.job_id[:8], result.get("error"),
                )
                _queue_for_review(listing, ats_score, review_batch)
                return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)
        except Exception as exc:
            logger.error("scan_pipeline: apply_job exception for %s: %s", listing.job_id[:8], exc)
            _queue_for_review(listing, ats_score, review_batch)
            return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)

    elif action == "send_for_review":
        _queue_for_review(listing, ats_score, review_batch)
        return RouteResult("queued_for_review", listing.job_id, listing.title, listing.company)

    else:
        # Skip
        db.update_status(listing.job_id, "Skipped")
        if notion_page_id:
            try:
                update_application_page(notion_page_id, status="Skipped")
            except Exception as exc:
                logger.warning("scan_pipeline: Notion skip update failed: %s", exc)
        logger.debug(
            "scan_pipeline: SKIPPED %s @ %s (ATS %.1f%%)",
            listing.title, listing.company, ats_score,
        )
        return RouteResult("skipped", listing.job_id, listing.title, listing.company)
