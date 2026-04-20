"""Job application submission orchestrator with tier logic.

Thread-safe with mutex to prevent concurrent apply_job() calls.
Records application BEFORE submission to prevent silent limit bypass.
"""

import asyncio
import inspect
import random
import threading
import time
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters import get_adapter
from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

AGGREGATOR_DOMAINS: set[str] = {
    "bebee.com", "learn4good.com", "adzuna.co.uk", "engineeringjobs.co.uk",
    "uk.talent.com", "talent.com", "jooble.org", "neuvoo.co.uk",
    "jobrapido.com", "careerjet.co.uk", "simplyhired.co.uk",
}


def is_aggregator_url(url: str) -> bool:
    """Check if a URL belongs to a known job aggregator."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    return any(domain == agg or domain.endswith("." + agg) for agg in AGGREGATOR_DOMAINS)


# Global mutex — only one apply_job() call can run at a time
_apply_lock = threading.Lock()

# Applicant profile and work auth loaded from env vars via config
from jobpulse.config import APPLICANT_PROFILE as PROFILE, WORK_AUTH


def classify_action(ats_score: float, easy_apply: bool) -> str:
    """Classify what action to take based on ATS score and application complexity.

    Hybrid approval tiers (updated 2026-03-28):
        auto_submit              — score >= 95 AND easy apply available
        auto_submit_with_preview — score >= 95 AND NOT easy apply
        send_for_review          — 85 <= score < 95
        skip                     — score < 85
    """
    if ats_score >= 95:
        return "auto_submit" if easy_apply else "auto_submit_with_preview"
    if ats_score >= 85:
        return "send_for_review"
    return "skip"


def select_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Return the appropriate ATS adapter for the given platform name."""
    return get_adapter(ats_platform)


def _call_fill_and_submit(adapter: BaseATSAdapter, engine: str = "extension", **kwargs: Any) -> dict:
    """Call adapter.fill_and_submit(), handling the async ExtensionAdapter.

    ExtensionAdapter.fill_and_submit() is async — we dispatch the coroutine
    to the bridge's event loop (running on a background thread) so WebSocket
    calls stay on the correct loop.
    """
    result = adapter.fill_and_submit(engine=engine, **kwargs)
    if inspect.isawaitable(result):
        # Check if the adapter has a bridge with its own event loop (extension mode)
        bridge = getattr(adapter, "bridge", None)
        bridge_loop = getattr(bridge, "_loop", None) if bridge else None

        if bridge_loop and bridge_loop.is_running():
            # Dispatch to the bridge's event loop thread
            import concurrent.futures

            future = asyncio.run_coroutine_threadsafe(result, bridge_loop)
            result = future.result(timeout=300)  # 5min timeout for long applications
        else:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, result).result()
            else:
                result = asyncio.run(result)
    return result


def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    cl_generator: Any | None = None,  # Callable[[], Path | None]
    custom_answers: dict | None = None,
    overrides: dict | None = None,
    dry_run: bool = False,
    engine: str = "extension",
    job_context: dict | None = None,
) -> dict:
    """Submit a job application via the appropriate ATS adapter.

    Thread-safe: uses a mutex so only one application submits at a time.
    Records application BEFORE submission to prevent silent limit bypass.
    """
    from jobpulse.rate_limiter import (
        LINKEDIN_SESSION_BREAK_MINUTES,
        LINKEDIN_SESSION_CAP,
        SESSION_BREAK_MINUTES,
        RateLimiter,
    )
    from jobpulse.screening_answers import get_answer

    if is_aggregator_url(url):
        logger.warning(
            "Aggregator URL detected: %s — these require registration and may not lead to a direct application form",
            url,
        )

    platform_key = (ats_platform or "generic").lower()
    total = 0  # fallback when dry_run skips the rate limiter section

    if not dry_run:
        # Acquire mutex — prevents TOCTOU race between can_apply() and record()
        with _apply_lock:
            limiter = RateLimiter()

            # Check rate limit
            if not limiter.can_apply(platform_key):
                remaining = limiter.get_remaining()
                logger.warning("Rate limit hit for %s. Remaining: %s", platform_key, remaining)
                return {
                    "success": False,
                    "error": f"Daily limit reached for {platform_key}",
                    "rate_limited": True,
                }

            # Record BEFORE submitting — prevents silent bypass if record fails after submission
            # If submission fails later, we "waste" one quota slot. That's safer than double-submitting.
            try:
                limiter.record_application(platform_key)
            except Exception as exc:
                logger.error(
                    "Failed to record application for %s: %s — aborting to prevent untracked submission",
                    platform_key,
                    exc,
                )
                return {
                    "success": False,
                    "error": f"Rate limiter error: {exc}",
                    "rate_limited": False,
                }

            total = limiter.get_total_today()
            logger.info("Quota reserved for %s (%d/%d today)", platform_key, total, 25)

        # LinkedIn per-session cap — longer breaks to avoid ML behavioral detection
        if platform_key == "linkedin":
            linkedin_count = limiter.get_platform_count("linkedin")
            # Break BEFORE the next batch of 5 (not after)
            if linkedin_count > 0 and (linkedin_count % LINKEDIN_SESSION_CAP) == 0:
                logger.info(
                    "LinkedIn session cap (%d apps) — pausing %d minutes to avoid detection",
                    LINKEDIN_SESSION_CAP,
                    LINKEDIN_SESSION_BREAK_MINUTES,
                )
                time.sleep(LINKEDIN_SESSION_BREAK_MINUTES * 60)

        # Session break check (all platforms)
        if limiter.should_take_break():
            logger.info(
                "Session break: pausing %d minutes (every 5 applications)", SESSION_BREAK_MINUTES
            )
            time.sleep(SESSION_BREAK_MINUTES * 60)

    # Build answers
    merged_answers: dict = dict(WORK_AUTH)
    if custom_answers:
        merged_answers.update(custom_answers)

    # Extract job context from custom_answers for dynamic screening resolution
    # (falls back to the job_context parameter if not embedded in custom_answers)
    _screening_job_context = (custom_answers or {}).get("_job_context") or job_context

    for key, value in list(merged_answers.items()):
        if isinstance(value, str) and value.endswith("?"):
            answer = get_answer(
                value,
                _screening_job_context,
                input_type=None,
                platform=platform_key,
            )
            if answer:
                merged_answers[key] = answer

    # Lazy CL generation: if no cover_letter_path but we have a generator,
    # check if the adapter supports CL and generate on demand
    if cover_letter_path is None and cl_generator is not None:
        # Check if this platform typically has CL fields
        if ats_platform and ats_platform.lower() in ("greenhouse", "lever"):
            try:
                cover_letter_path = cl_generator()
                if cover_letter_path:
                    logger.info("applicator: generated cover letter on demand for %s", ats_platform)
            except Exception as exc:
                logger.warning("applicator: on-demand CL generation failed: %s", exc)

    # Infer platform from URL when ats_platform is not set
    if not ats_platform:
        if "linkedin.com" in url:
            ats_platform = "linkedin"
            platform_key = "linkedin"
        elif "indeed.com" in url:
            ats_platform = "indeed"
            platform_key = "indeed"
        elif "greenhouse.io" in url or "boards.greenhouse" in url:
            ats_platform = "greenhouse"
            platform_key = "greenhouse"
        elif "lever.co" in url or "jobs.lever" in url:
            ats_platform = "lever"
            platform_key = "lever"
        elif "myworkdayjobs.com" in url:
            ats_platform = "workday"
            platform_key = "workday"
        elif "smartrecruiters.com" in url:
            ats_platform = "smartrecruiters"
            platform_key = "smartrecruiters"

    # Load known form-filling gotchas for this ATS domain before starting
    # so the adapter can avoid known failure patterns
    try:
        from urllib.parse import urlparse
        from jobpulse.form_engine.gotchas import GotchasDB
        _parsed_domain = urlparse(url).netloc.lower().removeprefix("www.")
        if _parsed_domain:
            _gotchas_db = GotchasDB()
            _gotchas = _gotchas_db.lookup_domain(_parsed_domain)
            if _gotchas:
                logger.info("gotchas: loaded %d known gotchas for %s", len(_gotchas), _parsed_domain)
                merged_answers["_gotchas"] = _gotchas
    except Exception as _gotcha_exc:
        logger.debug("GotchasDB lookup failed: %s", _gotcha_exc)

    # Load form hints from prior applications on this domain
    try:
        from jobpulse.form_prefetch import prefetch_form_hints
        _form_hints = prefetch_form_hints(url)
        if _form_hints.known_domain:
            merged_answers["_form_hints"] = _form_hints.to_dict()
    except Exception as _prefetch_exc:
        logger.debug("form_prefetch failed: %s", _prefetch_exc)

    # Attach Telegram progress stream so the orchestrator can call stream_field()
    # per field during form filling. The stream is async but fire-and-forget from sync code.
    try:
        from jobpulse.telegram_stream import TelegramApplicationStream
        tg_stream = TelegramApplicationStream()
        merged_answers["_stream"] = tg_stream
    except Exception as _stream_exc:
        logger.debug("TelegramApplicationStream unavailable: %s", _stream_exc)

    # Submit
    adapter = select_adapter(ats_platform)
    logger.info("Applying via %s adapter to %s", adapter.name, url)

    result = _call_fill_and_submit(
        adapter,
        url=url,
        cv_path=cv_path,
        cover_letter_path=cover_letter_path,
        profile=PROFILE,
        custom_answers=merged_answers,
        overrides=overrides,
        dry_run=dry_run,
        engine=engine,
    )

    # Handle external redirect — LinkedIn detected non-Easy Apply and captured the
    # external ATS URL. Detect the ATS platform and re-apply via the correct adapter.
    if result.get("external_redirect") and result.get("external_url"):
        external_url = result["external_url"]
        logger.info("External redirect detected: %s → %s", url, external_url)

        from jobpulse.jd_analyzer import detect_ats_platform

        ext_platform = detect_ats_platform(external_url)
        ext_adapter = select_adapter(ext_platform)
        logger.info(
            "External ATS detected: %s — using %s adapter",
            ext_platform or "generic",
            ext_adapter.name,
        )

        # Lazy CL generation for external platforms that typically have CL fields
        ext_cl_path = cover_letter_path
        if ext_cl_path is None and cl_generator is not None:
            if ext_platform and ext_platform.lower() in ("greenhouse", "lever"):
                try:
                    ext_cl_path = cl_generator()
                    if ext_cl_path:
                        logger.info(
                            "applicator: generated cover letter on demand for external %s",
                            ext_platform,
                        )
                except Exception as exc:
                    logger.warning(
                        "applicator: on-demand CL generation for external failed: %s", exc
                    )

        result = _call_fill_and_submit(
            ext_adapter,
            url=external_url,
            cv_path=cv_path,
            cover_letter_path=ext_cl_path,
            profile=PROFILE,
            custom_answers=merged_answers,
            overrides=overrides,
            dry_run=dry_run,
            engine=engine,
        )
        # Tag the result so downstream knows this was an external redirect
        result["external_redirect"] = True
        result["external_url"] = external_url
        result["external_platform"] = ext_platform or "generic"

    platform_name = result.get("external_platform", adapter.name)
    if result.get("success"):
        logger.info("Application submitted via %s (%d today)", platform_name, total)
    else:
        logger.warning(
            "Application failed via %s: %s (quota already consumed)",
            platform_name,
            result.get("error"),
        )

    # Post-apply hook: record experience + Drive upload + Notion update
    if result.get("success") and not dry_run:
        ctx = job_context or {}
        try:
            from jobpulse.post_apply_hook import post_apply_hook

            post_apply_hook(
                result=result,
                job_context={
                    "job_id": ctx.get("job_id", ""),
                    "company": ctx.get("company", ""),
                    "title": ctx.get("title", ""),
                    "url": result.get("external_url", url),
                    "platform": platform_key,
                    "ats_platform": ats_platform or platform_key,
                    "notion_page_id": ctx.get("notion_page_id"),
                    "cv_path": str(cv_path),
                    "cover_letter_path": str(cover_letter_path) if cover_letter_path else None,
                    "match_tier": ctx.get("match_tier"),
                    "ats_score": ctx.get("ats_score"),
                    "matched_projects": ctx.get("matched_projects"),
                },
            )
        except Exception as exc:
            logger.warning("post_apply_hook failed: %s — application still recorded", exc)

    if not dry_run:
        # Anti-detection: random delay between submissions (20-45s with jitter)
        delay = random.uniform(20, 45)
        logger.info("Anti-detection delay: %.0fs before next application", delay)
        time.sleep(delay)

    result["rate_limited"] = False
    return result


def confirm_application(
    dry_run_result: dict,
    url: str,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    job_context: dict | None = None,
    ats_platform: str | None = None,
    agent_mapping: dict[str, str] | None = None,
    final_mapping: dict[str, str] | None = None,
) -> dict:
    """Finalize a dry-run application after manual user submission.

    Call after the user reviews a dry_run=True form and manually clicks Submit.
    Records quota usage, captures user corrections, and runs post_apply_hook.

    Args:
        agent_mapping: {field_label: value} as originally filled by the agent.
        final_mapping: {field_label: value} as approved by the user (after corrections).
            When both are provided, corrections are stored as reinforcement signals.
    """
    ctx = job_context or {}
    platform_key = (ats_platform or ctx.get("platform", "generic")).lower()

    with _apply_lock:
        try:
            from jobpulse.rate_limiter import RateLimiter
            limiter = RateLimiter()
            limiter.record_application(platform_key)
        except Exception as exc:
            logger.warning("confirm_application: rate limiter: %s", exc)

    result = dict(dry_run_result)
    result["success"] = True
    result.pop("dry_run", None)

    # Capture user corrections as reinforcement signals
    if agent_mapping and final_mapping:
        try:
            from urllib.parse import urlparse
            from jobpulse.correction_capture import CorrectionCapture

            domain = urlparse(url).netloc.lower().removeprefix("www.")
            cc = CorrectionCapture()
            correction_result = cc.record_corrections(
                domain=domain,
                platform=platform_key,
                agent_mapping=agent_mapping,
                final_mapping=final_mapping,
            )
            result["corrections"] = correction_result

            # Auto-generate agent rules from corrections (bridge to cron agents)
            if correction_result.get("corrections"):
                try:
                    from jobpulse.agent_rules import AgentRulesDB
                    rules = AgentRulesDB()
                    for c in correction_result["corrections"]:
                        rules.auto_generate_from_correction(
                            field_label=c["field"],
                            agent_value=c["agent"],
                            user_value=c["user"],
                            domain=domain,
                            platform=platform_key,
                        )
                except Exception as rules_exc:
                    logger.warning("confirm_application: agent rules generation: %s", rules_exc)
        except Exception as exc:
            logger.warning("confirm_application: correction capture: %s", exc)

    try:
        from jobpulse.post_apply_hook import post_apply_hook

        post_apply_hook(
            result=result,
            job_context={
                "job_id": ctx.get("job_id", ""),
                "company": ctx.get("company", ""),
                "title": ctx.get("title", ""),
                "url": url,
                "platform": platform_key,
                "ats_platform": ats_platform or platform_key,
                "notion_page_id": ctx.get("notion_page_id"),
                "cv_path": str(cv_path),
                "cover_letter_path": str(cover_letter_path) if cover_letter_path else None,
                "match_tier": ctx.get("match_tier"),
                "ats_score": ctx.get("ats_score"),
                "matched_projects": ctx.get("matched_projects"),
            },
        )
    except Exception as exc:
        logger.warning("confirm_application: post_apply_hook failed: %s", exc)

    return result
