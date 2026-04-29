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
from jobpulse.job_db import JobDB
from jobpulse.job_notion_sync import find_application_page, update_application_page

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
    company = job_context.get("company", "Unknown")
    url = job_context.get("url", "")

    if not result.get("success"):
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
                success=False,
            )
            stats = result.get("agent_fill_stats", {})
            for label in stats.get("failed_labels", []):
                exp_db.record_failure_reason(
                    domain=url,
                    platform=job_context.get("ats_platform") or job_context.get("platform", "generic"),
                    failure_type="fill_failure",
                    field_label=label,
                    details=result.get("error", ""),
                )
        except Exception as exc:
            logger.warning("post_apply_hook: failure recording failed: %s", exc)
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="failure", source_loop="form_experience",
                domain=url, agent_name="form_filler",
                payload={"error": result.get("error", ""), "pages_reached": result.get("pages_filled", 0)},
                session_id=f"fe_fail_{company}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as exc:
            logger.debug("post_apply_hook: optimization signal failed: %s", exc)
        return

    notion_page_id = job_context.get("notion_page_id")
    cv_path = job_context.get("cv_path")
    cl_path = job_context.get("cover_letter_path")

    start = time.monotonic()

    # --- 0. Before-measurement for optimization engine ---
    opt_action_id = ""
    try:
        from shared.optimization import get_optimization_engine
        _engine = get_optimization_engine()
        _before = {
            "fields_filled": len(result.get("field_types", [])),
            "pages_filled": result.get("pages_filled", 0),
            "time_seconds": result.get("time_seconds", 0.0),
        }
        opt_action_id = _engine.before_learning_action(
            "post_apply", domain=url, metrics=_before,
        )
    except Exception as exc:
        logger.debug("post_apply_hook: before_learning_action failed: %s", exc)

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

    # --- 1b. Trigger cross-domain transfer learning ---
    try:
        from jobpulse.platform_transfer import PlatformTransferEngine
        transfer_engine = PlatformTransferEngine(db_path=form_exp_db_path)
        domain = FormExperienceDB.normalize_domain(url)
        transfer_engine.recompute_similarity_matrix(domain)
    except Exception as exc:
        logger.debug("post_apply_hook: transfer recomputation failed: %s", exc)

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
    if not notion_page_id:
        try:
            title = job_context.get("title", "")
            notion_page_id = find_application_page(company, title)
        except Exception as exc:
            logger.warning("post_apply_hook: Notion page search failed: %s", exc)

    if notion_page_id:
        applied_now = datetime.now(UTC)
        follow_up = date.today() + timedelta(days=7)
        cv_name = Path(cv_path).name if cv_path else None
        cl_name = Path(cl_path).name if cl_path else None

        ats_score = job_context.get("ats_score")
        match_tier = job_context.get("match_tier")
        matched_projects = job_context.get("matched_projects")
        if isinstance(matched_projects, str):
            import json as _json
            try:
                matched_projects = _json.loads(matched_projects)
            except Exception:
                matched_projects = [p.strip() for p in matched_projects.split(",") if p.strip()]

        screening = result.get("screening_questions", [])
        salary_answer = ""
        for sq in screening:
            if "salary" in sq.lower():
                salary_answer = sq.split(":", 1)[-1].strip() if ":" in sq else ""
                break
        salary = salary_answer or job_context.get("salary") or None

        seniority = job_context.get("seniority") or None
        remote = job_context.get("remote")
        ats_platform = job_context.get("ats_platform") or job_context.get("platform")
        manually_applied = job_context.get("manually_applied", True)

        notes_parts = []
        field_count = len(result.get("field_types", []))
        pages = result.get("pages_filled", 0)
        elapsed_fill = result.get("time_seconds", 0)
        if field_count:
            notes_parts.append(f"Form: {pages} page(s), {field_count} fields, {elapsed_fill:.0f}s")
        if result.get("agent_fill_stats"):
            stats = result["agent_fill_stats"]
            notes_parts.append(
                f"Agent: {stats.get('fields_filled', 0)}/{stats.get('fields_attempted', 0)} filled, "
                f"{stats.get('llm_fallback_count', 0)} LLM calls"
            )
        notes = ". ".join(notes_parts) or None

        try:
            update_application_page(
                notion_page_id,
                status="Applied",
                applied_date=date.today(),
                applied_time=applied_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                follow_up_date=follow_up,
                cv_drive_link=cv_drive_link,
                cl_drive_link=cl_drive_link,
                cv_filename=cv_name,
                cl_filename=cl_name,
                ats_score=ats_score,
                match_tier=match_tier if match_tier and match_tier != "skip" else None,
                matched_projects=matched_projects if matched_projects else None,
                notes=notes,
                ats_platform=ats_platform,
                salary=salary,
                seniority=seniority,
                remote=remote,
                manually_applied=manually_applied,
            )
        except Exception as exc:
            logger.warning("post_apply_hook: Notion update failed: %s", exc)

    job_id = (job_context.get("job_id") or "").strip()
    if job_id:
        try:
            JobDB().mark_applied(job_id)
        except Exception as exc:
            logger.warning("post_apply_hook: JobDB mark_applied failed: %s", exc)

    # --- 4. Strategy reflection + heuristic extraction ---
    if job_id:
        try:
            from jobpulse.strategy_reflector import reflect_on_application
            from jobpulse.trajectory_store import get_trajectory_store
            store = get_trajectory_store()
            strategy = reflect_on_application(store, job_id, job_context)
            import json as _json
            h_count = len(_json.loads(strategy.heuristics or "[]"))
            logger.info(
                "post_apply_hook: strategy reflection done for %s "
                "(fields=%d, pattern=%d, llm=%d, corrected=%d, heuristics=%d)",
                company,
                strategy.fields_total, strategy.fields_pattern,
                strategy.fields_llm, strategy.fields_corrected,
                h_count,
            )
        except Exception as exc:
            logger.warning("post_apply_hook: strategy reflection failed: %s", exc)

    # --- 5. Navigation self-learning — persist successful nav sequences ---
    nav_steps = result.get("navigation_steps", [])
    nav_saved = False
    if url:
        try:
            from jobpulse.navigation_learner import NavigationLearner
            learner = NavigationLearner()
            learner.save_sequence(
                domain_or_url=url,
                steps=nav_steps,
                success=True,
                platform=job_context.get("ats_platform") or job_context.get("platform", ""),
            )
            nav_saved = True
        except Exception as exc:
            logger.warning("post_apply_hook: navigation learning failed: %s", exc)

    elapsed = time.monotonic() - start
    logger.info(
        "post_apply_hook: completed for %s in %.1fs (drive_cv=%s, drive_cl=%s, notion=%s, nav=%s)",
        company,
        elapsed,
        "yes" if cv_drive_link else "no",
        "yes" if cl_drive_link else "no",
        "yes" if notion_page_id else "skip",
        "yes" if nav_saved else "skip",
    )

    # --- 6. After-measurement for optimization engine ---
    if opt_action_id:
        try:
            from shared.optimization import get_optimization_engine
            _engine = get_optimization_engine()
            _after = {
                "fields_filled": len(result.get("field_types", [])),
                "pages_filled": result.get("pages_filled", 0),
                "time_seconds": result.get("time_seconds", 0.0),
                "drive_cv_uploaded": bool(cv_drive_link),
                "drive_cl_uploaded": bool(cl_drive_link),
                "notion_updated": bool(notion_page_id),
                "nav_learned": nav_saved,
                "elapsed_seconds": round(time.monotonic() - start, 1),
            }
            _engine.after_learning_action(opt_action_id, metrics=_after)
        except Exception as exc:
            logger.debug("post_apply_hook: after_learning_action failed: %s", exc)
