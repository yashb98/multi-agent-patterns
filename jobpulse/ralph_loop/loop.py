"""Core Ralph Loop — self-healing retry wrapper for job applications.

Wraps apply_job() with a try→screenshot→diagnose→fix→retry cycle.
Learned fixes persist to SQLite so future cron runs succeed on first try.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.ralph_loop.pattern_store import PatternStore, FixPattern, compute_error_signature
from jobpulse.ralph_loop.diagnoser import (
    infer_step_from_error,
    capture_failure_context,
    diagnose_with_vision,
    heuristic_diagnosis,
)

logger = get_logger(__name__)

MAX_ITERATIONS = 5


# ---------------------------------------------------------------------------
# Overrides builder — converts fix patterns into adapter-consumable dict
# ---------------------------------------------------------------------------


def build_overrides_from_fixes(fixes: list[FixPattern]) -> dict[str, Any]:
    """Convert learned fix patterns into an overrides dict for ATS adapters.

    The overrides dict has 5 sections, one per fix type:
        selector_overrides: {original_selector: new_selector}
        wait_overrides: {step_name: timeout_ms}
        strategy_overrides: {step_name: new_strategy}
        field_remaps: {field_label: profile_key}
        interaction_mods: {selector_or_step: {modifier, wait_ms}}
    """
    overrides: dict[str, dict] = {
        "selector_overrides": {},
        "wait_overrides": {},
        "strategy_overrides": {},
        "field_remaps": {},
        "interaction_mods": {},
    }

    for fix in fixes:
        # Skip superseded fixes (they have been replaced by a better one)
        if fix.superseded_by is not None:
            logger.debug("Skipping superseded fix %s (replaced by %s)", fix.id, fix.superseded_by)
            continue

        # Skip unconfirmed test fixes (not yet validated in production)
        if fix.source == "test" and not fix.confirmed:
            logger.warning(
                "Skipping unconfirmed test fix %s for %s/%s (occurrence_count=%d)",
                fix.id, fix.platform, fix.step_name, fix.occurrence_count,
            )
            continue

        payload = fix.payload
        if fix.fix_type == "selector_override":
            orig = payload.get("original_selector", "")
            new = payload.get("new_selector", "")
            if orig and new:
                overrides["selector_overrides"][orig] = new

        elif fix.fix_type == "wait_adjustment":
            step = payload.get("step", "")
            timeout = payload.get("timeout_ms", 10000)
            if step:
                overrides["wait_overrides"][step] = timeout

        elif fix.fix_type == "strategy_switch":
            step = payload.get("step", "")
            new_strategy = payload.get("new_strategy", "")
            if step and new_strategy:
                overrides["strategy_overrides"][step] = new_strategy

        elif fix.fix_type == "field_remap":
            label = payload.get("field_label", "")
            key = payload.get("profile_key", "")
            if label and key:
                overrides["field_remaps"][label] = key

        elif fix.fix_type == "interaction_change":
            action = payload.get("action", "click")
            modifier = payload.get("modifier", "scroll_first")
            wait_ms = payload.get("wait_ms", 2000)
            step = payload.get("step", action)
            overrides["interaction_mods"][step] = {
                "modifier": modifier,
                "wait_ms": wait_ms,
            }

    return overrides


def _merge_fix_into_overrides(overrides: dict, fix: FixPattern) -> dict:
    """Apply a single new fix into an existing overrides dict."""
    payload = fix.payload
    if fix.fix_type == "selector_override":
        orig = payload.get("original_selector", "")
        new = payload.get("new_selector", "")
        if orig and new:
            overrides["selector_overrides"][orig] = new

    elif fix.fix_type == "wait_adjustment":
        step = payload.get("step", "")
        timeout = payload.get("timeout_ms", 10000)
        if step:
            overrides["wait_overrides"][step] = timeout

    elif fix.fix_type == "strategy_switch":
        step = payload.get("step", "")
        new_strategy = payload.get("new_strategy", "")
        if step and new_strategy:
            overrides["strategy_overrides"][step] = new_strategy

    elif fix.fix_type == "field_remap":
        label = payload.get("field_label", "")
        key = payload.get("profile_key", "")
        if label and key:
            overrides["field_remaps"][label] = key

    elif fix.fix_type == "interaction_change":
        action = payload.get("action", "click")
        modifier = payload.get("modifier", "scroll_first")
        wait_ms = payload.get("wait_ms", 2000)
        step = payload.get("step", action)
        overrides["interaction_mods"][step] = {
            "modifier": modifier,
            "wait_ms": wait_ms,
        }

    return overrides


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


def ralph_apply_sync(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    cl_generator: Any | None = None,
    custom_answers: dict | None = None,
    db_path: str | None = None,
) -> dict:
    """Self-healing apply loop (sync).

    1. Load known fixes → build overrides
    2. Try apply_job() with overrides
    3. On failure: diagnose (vision or heuristic), save fix, retry
    4. Max 5 iterations
    5. Returns same dict as apply_job: {success, screenshot, error, ...}
    """
    from jobpulse.applicator import apply_job, select_adapter, PROFILE, WORK_AUTH

    store = PatternStore(db_path)
    platform = (ats_platform or "generic").lower()

    # Load proactive fixes
    known_fixes = store.get_fixes_for_platform(platform)
    overrides = build_overrides_from_fixes(known_fixes)
    applied_fix_ids = [f.id for f in known_fixes]

    if known_fixes:
        logger.info(
            "Ralph Loop: loaded %d proactive fixes for %s",
            len(known_fixes), platform,
        )
        store.mark_fixes_applied(applied_fix_ids)

    already_tried_sigs: set[str] = set()
    attempt_ids: list[str] = []
    last_result: dict = {}

    for iteration in range(1, MAX_ITERATIONS + 1):
        logger.info(
            "Ralph Loop iteration %d/%d for %s: %s",
            iteration, MAX_ITERATIONS, platform, url[:80],
        )

        # First iteration: use apply_job (which handles rate limiting)
        # Subsequent iterations: call adapter directly (quota already consumed)
        if iteration == 1:
            result = apply_job(
                url=url,
                ats_platform=ats_platform,
                cv_path=cv_path,
                cover_letter_path=cover_letter_path,
                cl_generator=cl_generator,
                custom_answers=custom_answers,
                overrides=overrides,
            )
        else:
            adapter = select_adapter(ats_platform)
            merged_answers: dict = dict(WORK_AUTH)
            if custom_answers:
                merged_answers.update(custom_answers)
            try:
                result = adapter.fill_and_submit(
                    url=url,
                    cv_path=cv_path,
                    cover_letter_path=cover_letter_path,
                    profile=PROFILE,
                    custom_answers=merged_answers,
                    overrides=overrides,
                )
            except Exception as exc:
                result = {"success": False, "screenshot": None, "error": str(exc)}

        last_result = result

        if result.get("rate_limited"):
            # Rate limited on first try — don't retry
            logger.warning("Ralph Loop: rate limited, aborting")
            return result

        if result.get("success"):
            # Record success
            step = "complete"
            store.record_attempt(
                job_url=url,
                platform=platform,
                iteration=iteration,
                step_name=step,
                outcome="success",
            )
            if applied_fix_ids:
                store.mark_fixes_successful(applied_fix_ids)
            logger.info(
                "Ralph Loop: SUCCESS on iteration %d for %s", iteration, platform,
            )

            # Trigger consolidation periodically
            all_fixes = store.get_fixes_for_platform(platform)
            if len(all_fixes) >= 10:
                store.consolidate_patterns(platform)

            result["ralph_iterations"] = iteration
            return result

        # --- FAILURE PATH ---
        error_msg = result.get("error", "Unknown error")
        step_name = infer_step_from_error(error_msg, platform)
        error_sig = compute_error_signature(platform, step_name, error_msg)

        logger.warning(
            "Ralph Loop: FAILED iteration %d — step=%s error=%s",
            iteration, step_name, error_msg[:120],
        )

        # Try known fix for this specific error (if not already tried)
        if error_sig not in already_tried_sigs:
            existing_fix = store.get_fix(platform, step_name, error_sig)
            if existing_fix:
                logger.info(
                    "Ralph Loop: found existing fix %s for %s/%s — retrying",
                    existing_fix.id, platform, step_name,
                )
                overrides = _merge_fix_into_overrides(overrides, existing_fix)
                applied_fix_ids.append(existing_fix.id)
                store.mark_fixes_applied([existing_fix.id])
                already_tried_sigs.add(error_sig)

                store.record_attempt(
                    job_url=url,
                    platform=platform,
                    iteration=iteration,
                    step_name=step_name,
                    outcome="retrying_known_fix",
                    error_message=error_msg,
                    error_signature=error_sig,
                    fix_applied={"fix_id": existing_fix.id, "fix_type": existing_fix.fix_type},
                )
                attempt_ids.append(existing_fix.id)
                continue

        already_tried_sigs.add(error_sig)

        # No known fix — try to diagnose
        diagnosis: dict | None = None

        # Try vision diagnosis if we have a screenshot and page access
        screenshot_path = result.get("screenshot")
        page = result.get("_page")  # adapters can optionally pass the page object

        if page is not None:
            try:
                context = capture_failure_context(
                    page,
                    job_id=_url_to_job_id(url),
                    step_name=step_name,
                    error_message=error_msg,
                    iteration=iteration,
                )
                diagnosis = diagnose_with_vision(context, platform)
            except Exception as exc:
                logger.warning("Vision diagnosis failed: %s", exc)

        # Fallback: heuristic diagnosis
        if diagnosis is None:
            diagnosis = heuristic_diagnosis(error_msg, platform)

        if diagnosis is not None:
            fix_type = diagnosis["fix_type"]
            fix_payload = diagnosis["fix_payload"]
            confidence = diagnosis.get("confidence", 0.5)

            new_fix = store.save_fix(
                platform=platform,
                step_name=step_name,
                error_signature=error_sig,
                fix_type=fix_type,
                fix_payload=fix_payload,
                confidence=confidence,
            )

            overrides = _merge_fix_into_overrides(overrides, new_fix)
            applied_fix_ids.append(new_fix.id)
            store.mark_fixes_applied([new_fix.id])

            aid = store.record_attempt(
                job_url=url,
                platform=platform,
                iteration=iteration,
                step_name=step_name,
                outcome="diagnosed",
                error_message=error_msg,
                error_signature=error_sig,
                screenshot_path=str(screenshot_path) if screenshot_path else None,
                diagnosis=diagnosis,
                fix_applied={"fix_id": new_fix.id, "fix_type": fix_type},
            )
            attempt_ids.append(aid)

            logger.info(
                "Ralph Loop: diagnosed %s → %s (confidence=%.2f), retrying",
                step_name, fix_type, confidence,
            )
        else:
            # Can't diagnose — record and stop
            aid = store.record_attempt(
                job_url=url,
                platform=platform,
                iteration=iteration,
                step_name=step_name,
                outcome="undiagnosable",
                error_message=error_msg,
                error_signature=error_sig,
                screenshot_path=str(screenshot_path) if screenshot_path else None,
            )
            attempt_ids.append(aid)
            logger.warning(
                "Ralph Loop: cannot diagnose %s/%s — stopping", platform, step_name,
            )
            break

    # Exhausted all iterations
    store.flag_for_human_review(url, platform, attempt_ids)

    last_result["ralph_iterations"] = MAX_ITERATIONS
    last_result["ralph_exhausted"] = True
    last_result["ralph_attempts"] = store.get_attempt_history(url)
    return last_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_to_job_id(url: str) -> str:
    """Extract a short job ID from a URL for directory naming."""
    import hashlib
    return hashlib.sha256(url.encode()).hexdigest()[:12]
