"""Job application submission orchestrator with tier logic."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters import get_adapter
from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

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

    Args:
        ats_score: Match score 0-100 from the ATS scorer.
        easy_apply: True if the platform supports one-click / Easy Apply flow.

    Returns:
        Action string: 'auto_submit' | 'auto_submit_with_preview' | 'send_for_review' | 'skip'
    """
    if ats_score >= 95:
        return "auto_submit" if easy_apply else "auto_submit_with_preview"
    if ats_score >= 85:
        return "send_for_review"
    return "skip"


def select_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Return the appropriate ATS adapter for the given platform name.

    Falls back to the generic adapter when platform is None or unrecognised.
    """
    return get_adapter(ats_platform)


def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    custom_answers: dict | None = None,
) -> dict:
    """Submit a job application via the appropriate ATS adapter.

    Checks rate limits before submission, records application after success,
    and enforces session breaks every 10 applications.

    Args:
        url: Direct application URL.
        ats_platform: Platform key (e.g. 'greenhouse', 'lever'). None → generic.
        cv_path: Path to the tailored CV PDF/DOCX.
        cover_letter_path: Optional path to the cover letter.
        custom_answers: Caller-supplied field answers (overrides WORK_AUTH if key clashes).

    Returns:
        dict with keys: success (bool), screenshot (Path|None), error (str|None),
        rate_limited (bool), break_taken (bool)
    """
    import asyncio
    from jobpulse.rate_limiter import RateLimiter
    from jobpulse.screening_answers import get_answer

    platform_key = (ats_platform or "generic").lower()
    limiter = RateLimiter()

    # Check rate limit before attempting
    if not limiter.can_apply(platform_key):
        remaining = limiter.get_remaining()
        logger.warning("Rate limit hit for %s. Remaining: %s", platform_key, remaining)
        return {"success": False, "error": f"Daily limit reached for {platform_key}", "rate_limited": True}

    # Session break check
    if limiter.should_take_break():
        import time
        logger.info("Session break: pausing 5 minutes (every 10 applications)")
        time.sleep(5 * 60)

    merged_answers: dict = dict(WORK_AUTH)
    if custom_answers:
        merged_answers.update(custom_answers)

    # Auto-answer any screening questions in custom_answers that are questions
    for key, value in list(merged_answers.items()):
        if isinstance(value, str) and value.endswith("?"):
            # This is a question, not an answer — auto-fill it
            answer = get_answer(value, {"title": "", "company": ""})
            if answer:
                merged_answers[key] = answer

    adapter = select_adapter(ats_platform)
    logger.info("Applying via %s adapter to %s", adapter.name, url)

    result = adapter.fill_and_submit(
        url=url,
        cv_path=cv_path,
        cover_letter_path=cover_letter_path,
        profile=PROFILE,
        custom_answers=merged_answers,
    )

    if result.get("success"):
        limiter.record_application(platform_key)
        logger.info("Application submitted via %s (%d/%d today)",
                    adapter.name, limiter.get_total_today(), 40)
    else:
        logger.warning("Application failed via %s: %s", adapter.name, result.get("error"))

    result["rate_limited"] = False
    return result
