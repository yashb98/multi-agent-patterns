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
# Skill-specific experience years (used for "How many years with X?" questions)
# ---------------------------------------------------------------------------
SKILL_EXPERIENCE: dict[str, int] = {
    # 3 years
    "python": 3, "sql": 3,
    # 2 years — ML/AI
    "machine learning": 2, "ml": 2, "deep learning": 2,
    "natural language processing": 2, "nlp": 2,
    "large language model": 2, "llm": 2, "generative ai": 2,
    "artificial intelligence": 2, "ai": 2,
    "data science": 2, "data analysis": 2, "data analytics": 2,
    "tensorflow": 2, "pytorch": 2, "scikit-learn": 2, "sklearn": 2,
    "pandas": 2, "numpy": 2, "scipy": 2,
    "computer vision": 2, "reinforcement learning": 2,
    "mlops": 2, "model deployment": 2,
    "a/b testing": 2, "ab testing": 2, "statistical analysis": 2,
    "neural network": 2, "transformer": 2,
    # 2 years — Software/DevOps
    "software engineering": 2, "software development": 2,
    "git": 2, "docker": 2, "linux": 2,
    "aws": 2, "cloud": 2, "gcp": 2, "azure": 2,
    "ci/cd": 2, "devops": 2,
    "api": 2, "rest": 2, "fastapi": 2, "flask": 2,
    # 2 years — Data Engineering
    "spark": 2, "hadoop": 2, "airflow": 2,
    "etl": 2, "data pipeline": 2, "data engineering": 2,
    "tableau": 2, "power bi": 2,
    "nosql": 2, "mongodb": 2, "redis": 2,
    "postgresql": 2, "mysql": 2,
    # 2 years — Other
    "agile": 2, "scrum": 2, "jira": 2,
    "r": 2, "matlab": 2, "java": 2, "c++": 2,
    "javascript": 2, "typescript": 2, "react": 2,
    # 3 years — Leadership
    "team management": 3, "leadership": 3, "team leader": 3,
}

# ---------------------------------------------------------------------------
# Role-aware salary expectations
# ---------------------------------------------------------------------------
ROLE_SALARY: dict[str, int] = {
    "data scientist": 32000,
    "machine learning engineer": 32000,
    "ml engineer": 32000,
    "ai engineer": 32000,
    "data analyst": 28000,
    "data engineer": 30000,
    "software engineer": 30000,
    "default": 28000,
}

# ---------------------------------------------------------------------------
# Platform-aware source tracking
# ---------------------------------------------------------------------------
PLATFORM_SOURCE: dict[str, str] = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "reed": "Reed",
    "greenhouse": "Company website",
    "lever": "Company website",
    "workday": "Company website",
    "default": "Job board",
}

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
# Dynamic resolvers
# ---------------------------------------------------------------------------


def _extract_skill_from_question(question: str) -> str | None:
    """Extract a skill/technology name from an experience question.

    Looks for patterns like "experience with X", "experience in X",
    "proficient in X", "familiar with X".
    Returns lowercase skill name or None if no skill found.
    """
    patterns = [
        r"experience (?:do you have )?(?:with|in) (.+?)[\?\.]?$",
        r"(?:proficient|familiar|experienced) (?:in|with) (.+?)[\?\.]?$",
        r"years of (.+?) experience",
        r"experience (?:with|in) (.+?)[\?\.]?$",
    ]
    normalised = question.strip().lower()
    for pat in patterns:
        m = re.search(pat, normalised)
        if m:
            skill = m.group(1).strip().rstrip("?. ")
            # Filter out generic adjectives that aren't real skills
            generic = {"relevant", "overall", "total", "professional", "work", "related"}
            if skill in generic:
                return None
            # Remove common suffixes
            for suffix in ("development", "engineering", "programming"):
                if skill.endswith(suffix) and skill != suffix:
                    trimmed = skill[: -len(suffix)].strip()
                    if trimmed in SKILL_EXPERIENCE:
                        return trimmed
            return skill if skill else None
    return None


def _resolve_skill_experience(skill: str | None, *, input_type: str | None) -> str:
    """Return years of experience for a skill as a string."""
    if skill is None:
        years = 2
    else:
        years = SKILL_EXPERIENCE.get(skill.lower(), 2)
    return str(years)


def _resolve_role_salary(
    job_context: dict | None, *, input_type: str | None
) -> str:
    """Return salary expectation based on job title and input type."""
    title = ((job_context or {}).get("job_title") or "").lower()
    salary = ROLE_SALARY["default"]
    for role_key, role_salary in ROLE_SALARY.items():
        if role_key != "default" and role_key in title:
            salary = role_salary
            break

    if input_type == "number":
        return str(salary)

    # Text field — return a range
    low = salary - 2000
    high = salary + 3000
    return f"{low:,}-{high:,}"


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
