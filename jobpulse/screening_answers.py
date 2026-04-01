"""Auto-answer common screening questions from job applications.

Pattern-based answers for frequent questions (work auth, availability, salary),
with LLM fallback for open-ended questions and SQLite caching via JobDB.
"""

from __future__ import annotations

import re

from openai import OpenAI

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.config import OPENAI_API_KEY
from jobpulse.job_db import JobDB
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Pattern-based answers for frequent screening questions.
# Value = str  -> return directly
# Value = None -> needs LLM generation (open-ended / personalised)
# ---------------------------------------------------------------------------
COMMON_ANSWERS: dict[str, str | None] = {
    # Work authorization — SPECIFIC patterns FIRST, then general
    r"right.*work.*type|work.*type|work.*permit|work.*authorization.*type": (
        "Graduate Visa"
    ),
    r"authorized.*work.*uk|right to work|legally.*work": "Yes",
    r"require.*sponsor|visa.*sponsor|sponsorship|need.*sponsor": "No",
    r"visa.*status|immigration": (
        "Student Visa; converting to Graduate Visa from 9 May 2026 (valid 2 years)"
    ),
    # Company / employment history
    r"currently.*work.*for|ever.*work.*for|employed.*by|worked.*for": "No",
    r"current.*salary|salary.*current|present.*salary": "25000",
    # Location — uses JOB_LOCATION placeholder, resolved dynamically in get_answer()
    r"current.*location|where.*located|your.*location|what.*city.*live|which.*city": "JOB_LOCATION",
    r"where.*you.*based|based.*in|residing": "JOB_LOCATION",
    # Availability
    r"notice.*period|start.*date|available.*start|when.*start": "Immediately",
    r"willing.*relocate|open.*relocation": "Yes, within the UK",
    # Salary — numeric value for numeric fields, text for text fields
    r"salary.*expect|expected.*salary|desired.*compensation|pay.*expect": "30000",
    # Experience
    r"years.*experience|experience.*years": (
        "1+ years (MSc Computer Science + industry experience)"
    ),
    # Remote / on-site
    r"willing.*remote|work.*remote|open.*remote": "Yes",
    r"willing.*office|work.*on.?site|in.?person|work.*in.*office|in.*the.*office": "Yes",
    # Equality / diversity — SPECIFIC multi-word patterns before single-word
    r"gender.*identify|what.*gender|indicate.*gender": "Male",
    r"sexual.*orientation|what.*orientation|indicate.*orientation": "Heterosexual/Straight",
    r"ethnicity|ethnic.*background|racial|indicate.*ethnicity": "Prefer not to say",
    r"disability|disabled": "No",
    r"veteran|military": "No",
    # Cover letter / Why — needs LLM
    r"why.*apply|why.*interest|why.*company|motivation": None,
    r"tell.*about.*yourself|describe.*yourself": None,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_answer(
    question: str,
    job_context: dict | None = None,
    *,
    db: JobDB | None = None,
) -> str:
    """Return an answer for a screening question.

    Resolution order:
    1. Regex match against ``COMMON_ANSWERS`` (instant, free).
    2. If the match maps to ``None`` -> LLM generation required.
    3. If no regex match -> look up ``ats_answer_cache`` in applications.db.
    4. If cached -> return it (and increment ``times_used``).
    5. Otherwise -> generate via LLM, cache, and return.

    Args:
        question: The screening question text.
        job_context: Optional dict with ``job_title`` and ``company`` keys.
        db: Optional ``JobDB`` instance (created on demand if not supplied).

    Returns:
        The answer string.
    """
    if not question or not question.strip():
        return ""

    normalised = question.strip()

    # --- Tier 1: pattern match -------------------------------------------
    for pattern, answer in COMMON_ANSWERS.items():
        if re.search(pattern, normalised, re.IGNORECASE):
            if answer is not None:
                # Resolve JOB_LOCATION placeholder from job_context
                if answer == "JOB_LOCATION":
                    location = (job_context or {}).get("location", "London, UK")
                    logger.debug("Pattern match for '%s' -> location '%s'", normalised[:60], location)
                    return location
                logger.debug("Pattern match for '%s' -> '%s'", normalised[:60], answer)
                return answer
            # Matched but needs LLM (answer is None)
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            return _generate_answer(normalised, job_context)

    # --- Tier 2: cache lookup --------------------------------------------
    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
        # Increment usage counter by re-caching with the same text
        _db.cache_answer(normalised, cached)
        logger.debug("Cache hit for '%s'", normalised[:60])
        return cached

    # --- Tier 3: LLM generation ------------------------------------------
    answer = _generate_answer(normalised, job_context)
    _db.cache_answer(normalised, answer)
    logger.info("Generated + cached answer for '%s'", normalised[:60])
    return answer


def cache_answer(question: str, answer: str, *, db: JobDB | None = None) -> None:
    """Store an answer in the ``ats_answer_cache`` table."""
    _db = db or JobDB()
    _db.cache_answer(question, answer)
    logger.debug("Cached answer for '%s'", question.strip()[:60])


def get_cached_answer(question: str, *, db: JobDB | None = None) -> str | None:
    """Look up a cached answer by question hash. Returns ``None`` on miss."""
    _db = db or JobDB()
    return _db.get_cached_answer(question)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _generate_answer(question: str, job_context: dict | None = None) -> str:
    """Call OpenAI to generate a concise screening-question answer."""
    context_line = ""
    if job_context:
        title = job_context.get("job_title", "the role")
        company = job_context.get("company", "the company")
        context_line = f" Context: Applying for {title} at {company}."

    profile_summary = (
        f"Name: {PROFILE['first_name']} {PROFILE['last_name']}. "
        f"Education: {PROFILE['education']}. "
        f"Location: {PROFILE['location']}. "
        f"Visa: {WORK_AUTH['visa_status']}."
    )

    prompt = (
        "Answer this job application screening question concisely (1-3 sentences). "
        f"Be professional and positive. Question: {question}.{context_line} "
        f"Applicant background: {profile_summary}"
    )

    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.4,
        )
        answer = response.choices[0].message.content.strip()
        logger.debug("LLM generated answer: %s", answer[:80])
        return answer
    except Exception as exc:
        logger.error("LLM answer generation failed: %s", exc)
        # Fallback: return a safe generic response
        return "Please refer to my CV for details."
