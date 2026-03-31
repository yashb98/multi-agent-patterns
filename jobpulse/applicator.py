"""Job application submission orchestrator with tier logic.

Thread-safe with mutex to prevent concurrent apply_job() calls.
Records application BEFORE submission to prevent silent limit bypass.
"""

import random
import threading
import time
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger

from jobpulse.ats_adapters import get_adapter
from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

# Global mutex — only one apply_job() call can run at a time
_apply_lock = threading.Lock()

# Work authorisation facts — injected into every application
WORK_AUTH: dict[str, object] = {
    "requires_sponsorship": False,
    "visa_status": "Student Visa (converting to Graduate Visa from 9 May 2026, valid 2 years)",
    "right_to_work_uk": True,
    "notice_period": "Available immediately",
    "salary_expectation": "27,000 - 32,000",
}

# Applicant profile — used to pre-fill standard form fields
PROFILE: dict[str, str] = {
    "first_name": "Yash",
    "last_name": "B",
    "email": "bishnoiyash274@gmail.com",
    "phone": "07909445288",
    "linkedin": "https://linkedin.com/in/yash-bishnoi-2ab36a1a5",
    "github": "https://github.com/yashb98",
    "portfolio": "https://yashbishnoi.io",
    "education": "MSc Computer Science, University of Dundee (Jan 2025 - Jan 2026)",
    "location": "Dundee, UK",
}


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


def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    cl_generator: Any | None = None,  # Callable[[], Path | None]
    custom_answers: dict | None = None,
    overrides: dict | None = None,
) -> dict:
    """Submit a job application via the appropriate ATS adapter.

    Thread-safe: uses a mutex so only one application submits at a time.
    Records application BEFORE submission to prevent silent limit bypass.
    """
    from jobpulse.rate_limiter import (
        RateLimiter, LINKEDIN_SESSION_CAP, LINKEDIN_SESSION_BREAK_MINUTES,
        SESSION_BREAK_MINUTES,
    )
    from jobpulse.screening_answers import get_answer

    platform_key = (ats_platform or "generic").lower()

    # Acquire mutex — prevents TOCTOU race between can_apply() and record()
    with _apply_lock:
        limiter = RateLimiter()

        # Check rate limit
        if not limiter.can_apply(platform_key):
            remaining = limiter.get_remaining()
            logger.warning("Rate limit hit for %s. Remaining: %s", platform_key, remaining)
            return {"success": False, "error": f"Daily limit reached for {platform_key}", "rate_limited": True}

        # Record BEFORE submitting — prevents silent bypass if record fails after submission
        # If submission fails later, we "waste" one quota slot. That's safer than double-submitting.
        try:
            limiter.record_application(platform_key)
        except Exception as exc:
            logger.error("Failed to record application for %s: %s — aborting to prevent untracked submission", platform_key, exc)
            return {"success": False, "error": f"Rate limiter error: {exc}", "rate_limited": False}

        total = limiter.get_total_today()
        logger.info("Quota reserved for %s (%d/%d today)", platform_key, total, 25)

    # LinkedIn per-session cap — longer breaks to avoid ML behavioral detection
    if platform_key == "linkedin":
        linkedin_count = limiter.get_platform_count("linkedin")
        # Break BEFORE the next batch of 5 (not after)
        if linkedin_count > 0 and (linkedin_count % LINKEDIN_SESSION_CAP) == 0:
            logger.info(
                "LinkedIn session cap (%d apps) — pausing %d minutes to avoid detection",
                LINKEDIN_SESSION_CAP, LINKEDIN_SESSION_BREAK_MINUTES,
            )
            time.sleep(LINKEDIN_SESSION_BREAK_MINUTES * 60)

    # Session break check (all platforms)
    if limiter.should_take_break():
        logger.info("Session break: pausing %d minutes (every 5 applications)", SESSION_BREAK_MINUTES)
        time.sleep(SESSION_BREAK_MINUTES * 60)

    # Build answers
    merged_answers: dict = dict(WORK_AUTH)
    if custom_answers:
        merged_answers.update(custom_answers)

    for key, value in list(merged_answers.items()):
        if isinstance(value, str) and value.endswith("?"):
            answer = get_answer(value, {"title": "", "company": ""})
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

    # Submit
    adapter = select_adapter(ats_platform)
    logger.info("Applying via %s adapter to %s", adapter.name, url)

    result = adapter.fill_and_submit(
        url=url,
        cv_path=cv_path,
        cover_letter_path=cover_letter_path,
        profile=PROFILE,
        custom_answers=merged_answers,
        overrides=overrides,
    )

    if result.get("success"):
        logger.info("Application submitted via %s (%d today)", adapter.name, total)
    else:
        logger.warning("Application failed via %s: %s (quota already consumed)", adapter.name, result.get("error"))

    # Anti-detection: random delay between submissions (20-45s with jitter)
    delay = random.uniform(20, 45)
    logger.info("Anti-detection delay: %.0fs before next application", delay)
    time.sleep(delay)

    result["rate_limited"] = False
    return result
