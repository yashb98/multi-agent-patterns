"""Auto-answer common screening questions from job applications.

Pattern-based answers for frequent questions (work auth, availability, salary),
with LLM fallback for open-ended questions and SQLite caching via JobDB.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from shared.agents import get_openai_client, get_model_name, is_local_llm

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.job_db import JobDB
from jobpulse.pipeline_hooks import with_tone_filter
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
    # ===================================================================
    # WORK AUTHORIZATION & VISA (6 patterns) — specific before general
    # ===================================================================
    r"right.*work.*type|work.*type|work.*permit|work.*authorization.*type|visa.*type|type.*visa.*hold": "Graduate Visa",
    r"authorized.*work|right to work|legally.*work|eligible.*work|unrestricted.*right": "Yes",
    r"require.*sponsor|visa.*sponsor|sponsorship|need.*sponsor": "No",
    r"visa.*status|immigration.*status|current.*visa|visa.*expire": (
        "Student Visa; converting to Graduate Visa from 9 May 2026 (valid 2 years)"
    ),
    r"british.*citizen|eu.*national|\bilr\b|indefinite.*leave|settled.*status": "No",
    r"subject.*immigration.*restrict|work.*without.*restrict": "No",

    # ===================================================================
    # SALARY & COMPENSATION (3 patterns) — current before expected
    # ===================================================================
    r"current.*salary|salary.*current|present.*salary|current.*compensation|current.*base": "22000",
    r"salary.*expect|expected.*salary|desired.*compensation|pay.*expect|minimum.*salary|salary.*range|target.*salary|compensation.*require|salary.*requirement": "ROLE_SALARY",
    r"daily.*rate|hourly.*rate|day.*rate": "150",

    # ===================================================================
    # NOTICE PERIOD & EMPLOYMENT (5 patterns)
    # ===================================================================
    r"notice.*period|when.*start|available.*start|start.*date|earliest.*start|how.*soon.*start|immediate.*start": "Immediately",
    r"currently.*employ|current.*employment|employment.*status|are.*you.*employ": "Yes",
    r"current.*job.*title|current.*role|current.*position|present.*role": "Team Leader",
    r"current.*employer|who.*work.*for|present.*employer|company.*work.*for": "Co-op",
    r"reason.*leaving|why.*leaving|why.*seeking|why.*new.*position": None,

    # ===================================================================
    # LOCATION & COMMUTE (3 patterns)
    # ===================================================================
    r"current.*location|where.*located|your.*location|what.*city.*live|which.*city|where.*you.*based|based.*in(?!.*uk)|residing|country.*resid": "JOB_LOCATION",
    r"willing.*relocate|open.*relocation|relocate.*within|relocate.*to": "Yes, within the UK",
    r"commut.*to|commuting.*distance|travel.*to.*office": "Yes",

    # ===================================================================
    # REMOTE / HYBRID / ON-SITE (3 patterns)
    # ===================================================================
    r"willing.*remote|work.*remote|open.*remote|comfortable.*remote|fully.*remote": "Yes",
    r"willing.*office|work.*on.?site|in.?person|work.*in.*office|in.*the.*office": "Yes",
    r"hybrid.*work|hybrid.*arrange|days.*per.*week.*on.?site|days.*in.*office|comfortable.*hybrid": "Yes",

    # ===================================================================
    # EXPERIENCE (2 patterns)
    # ===================================================================
    r"years.*experience|experience.*years|how.*many.*years|total.*years.*experience": "SKILL_EXPERIENCE",
    r"experience.*with|proficient.*in|familiar.*with|hands.?on.*experience|worked.*with": "SKILL_EXPERIENCE",

    # ===================================================================
    # EDUCATION (4 patterns) — specific before general
    # ===================================================================
    r"highest.*education|level.*education|highest.*qualification|completed.*education|highest.*degree": "Master's Degree",
    r"degree.*subject|field.*study|what.*degree|degree.*type|what.*major|degree.*classification": "MSc Computer Science",
    r"currently.*study|currently.*enrolled|enrolled.*education": "No",
    r"stem.*degree|computer.*science.*degree|related.*field|relevant.*degree": "Yes",

    # ===================================================================
    # LANGUAGES (3 patterns) — specific before general
    # ===================================================================
    r"proficiency.*english|fluent.*english|english.*proficiency|level.*english": "Native or bilingual",
    r"proficiency.*hindi|fluent.*hindi|hindi.*proficiency": "Native or bilingual",
    r"languages.*speak|what.*languages|language.*skills|other.*language|do.*you.*speak": "English (Native), Hindi (Native)",

    # ===================================================================
    # DRIVING, TRAVEL & AVAILABILITY (4 patterns)
    # ===================================================================
    r"driv.*licen[cs]e|driver.*licen|valid.*driv|clean.*driv": "Yes",
    r"willing.*travel|comfortable.*travel|travel.*required|percentage.*travel": "Yes",
    r"shift.*work|work.*weekend|night.*shift|on.?call|work.*evening|bank.*holiday|overtime|flexible.*hour": "Yes",
    r"permanent.*contract|preferred.*employment|full.?time|part.?time|employment.*type|fixed.?term|looking.*permanent": "Full-time",

    # ===================================================================
    # BACKGROUND, SECURITY & LEGAL (4 patterns)
    # ===================================================================
    r"background.*check|dbs.*check|criminal.*record|unspent.*conviction|willing.*undergo|pre.?employment.*screen": "Yes",
    r"security.*clearance|hold.*clearance|sc.*clearance|dv.*clearance|bpss|level.*clearance": "None",
    r"non.?compete|restrictive.*covenant|conflict.*interest|gardening.*leave": "No",
    r"based.*in.*uk|resident.*uk|uk.*resid|live.*in.*uk|reside.*in.*united.*kingdom": "No",

    # ===================================================================
    # COMPANY & APPLICATION HISTORY (3 patterns)
    # ===================================================================
    r"currently.*work.*for|ever.*work.*for|employed.*by|worked.*for|former.*employee": "No",
    r"previously.*applied|applied.*before|applied.*past|applied.*position": "PREVIOUSLY_APPLIED",
    r"how.*hear.*about|how.*find.*this|where.*see.*vacanc|source.*application": "PLATFORM_SOURCE",

    # ===================================================================
    # REFERRAL (1 pattern)
    # ===================================================================
    r"referred.*employee|referral.*code|referral.*name|employee.*refer|were.*you.*referred": "No",

    # ===================================================================
    # DIVERSITY & EQUALITY MONITORING (10 patterns) — specific before general
    # ===================================================================
    r"gender.*identify|what.*gender|indicate.*gender|what.*your.*sex\b": "Male",
    r"sexual.*orientation|what.*orientation|indicate.*orientation": "Heterosexual/Straight",
    r"ethnicity|ethnic.*background|racial|indicate.*ethnicity|race.*ethnic": "Asian or Asian British - Indian",
    r"disability|disabled|long.?term.*health|equality.*act.*2010|impairment.*health": "No",
    r"veteran|military": "No",
    r"religion\b|belief\b|faith\b|spiritual": "Hindu",
    r"marital.*status|civil.*status|relationship.*status": "Single",
    r"what.*pronoun|preferred.*pronoun|indicate.*pronoun": "He/Him",
    r"age.*group|what.*your.*age|date.*birth": "25-29",
    r"over.*18|are.*you.*18|above.*18": "Yes",

    # ===================================================================
    # CARING & ADJUSTMENTS (2 patterns)
    # ===================================================================
    r"caring.*responsib|childcare|eldercare|dependant": "No",
    r"reasonable.*adjust|access.*require|workplace.*adjust|special.*accommod|support.*application|assistive.*tech": "No",

    # ===================================================================
    # CONSENT & CONFIRMATIONS (1 pattern)
    # ===================================================================
    r"consent.*data|privacy.*policy|gdpr|data.*process|retain.*future|agree.*terms|information.*accurate|confirm.*read": "Yes",

    # ===================================================================
    # NATIONALITY & IDENTITY (2 patterns)
    # ===================================================================
    r"what.*nationality|country.*citizen|country.*birth": "Indian",
    r"\btitle\b.*mr|salutation|honorific": "Mr",

    # ===================================================================
    # TEAM & MANAGEMENT (2 patterns) — specific before general
    # ===================================================================
    r"direct.*report|how.*many.*managed|people.*managed|team.*size|largest.*team": "8",
    r"managing.*team|line.*management|managed.*people|leadership.*experience|management.*experience": "Yes",

    # ===================================================================
    # PORTFOLIO & LINKS (1 pattern)
    # ===================================================================
    r"portfolio.*url|github.*url|github.*profile|personal.*website|website.*url|kaggle|link.*to.*work": "PROFILE_LINK",

    # ===================================================================
    # PROFICIENCY RATINGS (1 pattern)
    # ===================================================================
    r"rate.*proficiency|rate.*your|proficiency.*level|skill.*level|how.*qualified|scale.*1.*5": "4",

    # ===================================================================
    # CERTIFICATIONS (1 pattern) — LLM
    # ===================================================================
    r"hold.*certification|professional.*cert|relevant.*cert|aws.*cert|certified": None,

    # ===================================================================
    # OPEN-ENDED / LLM (3 patterns)
    # ===================================================================
    r"why.*apply|why.*interest|why.*company|motivation|what.*excites": None,
    r"tell.*about.*yourself|describe.*yourself|brief.*summary.*fit": None,
    r"anything.*else|additional.*information|further.*comment|is.*there.*anything": None,
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


def _check_previously_applied(
    question: str,
    job_context: dict | None,
    *,
    db: JobDB | None = None,
) -> str:
    """Check if we've previously applied to this company."""
    company = (job_context or {}).get("company", "")
    if not company:
        return "No"
    _db = db or JobDB()
    count = _db.count_applications_for_company(company)
    return "Yes" if count > 0 else "No"


# ---------------------------------------------------------------------------
# Placeholder resolver
# ---------------------------------------------------------------------------


def _resolve_placeholder(
    answer: str,
    question: str,
    job_context: dict | None,
    *,
    input_type: str | None = None,
    platform: str | None = None,
    db: JobDB | None = None,
) -> str:
    """Resolve special placeholder values in COMMON_ANSWERS."""
    if answer == "JOB_LOCATION":
        return (job_context or {}).get("location", "London, UK")

    if answer == "SKILL_EXPERIENCE":
        skill = _extract_skill_from_question(question)
        return _resolve_skill_experience(skill, input_type=input_type)

    if answer == "ROLE_SALARY":
        return _resolve_role_salary(job_context, input_type=input_type)

    if answer == "PREVIOUSLY_APPLIED":
        return _check_previously_applied(question, job_context, db=db)

    if answer == "PLATFORM_SOURCE":
        return PLATFORM_SOURCE.get(platform or "", PLATFORM_SOURCE["default"])

    if answer == "PROFILE_LINK":
        return PROFILE.get("github", PROFILE.get("portfolio", ""))

    # Input-type adaptations for non-placeholder answers
    if answer == "Immediately" and input_type == "date":
        target = datetime.now() + timedelta(days=14)
        return target.strftime("%Y-%m-%d")

    if answer == "22000" and input_type == "text":
        return "22,000"

    return answer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_answer(
    question: str,
    job_context: dict | None = None,
    *,
    db: JobDB | None = None,
    input_type: str | None = None,
    platform: str | None = None,
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
        job_context: Optional dict with ``job_title``, ``company``, ``location`` keys.
        db: Optional ``JobDB`` instance (created on demand if not supplied).
        input_type: HTML input type (``text``, ``number``, ``date``, ``select``, etc.).
        platform: ATS platform name (``linkedin``, ``greenhouse``, etc.).

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
                resolved = _resolve_placeholder(
                    answer, normalised, job_context,
                    input_type=input_type, platform=platform, db=db,
                )
                logger.debug("Pattern match for '%s' -> '%s'", normalised[:60], resolved[:80])
                return with_tone_filter(resolved, normalised, None)
            # Matched but needs LLM (answer is None)
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            return with_tone_filter(_generate_answer(normalised, job_context), normalised, None)

    # --- Tier 2: cache lookup --------------------------------------------
    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
        _db.cache_answer(normalised, cached)
        logger.debug("Cache hit for '%s'", normalised[:60])
        return with_tone_filter(cached, normalised, None)

    # --- Tier 3: LLM generation ------------------------------------------
    answer = _generate_answer(normalised, job_context)
    _db.cache_answer(normalised, answer)
    logger.info("Generated + cached answer for '%s'", normalised[:60])
    return with_tone_filter(answer, normalised, None)


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
        client = get_openai_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600 if is_local_llm() else 200,
            temperature=0.4,
        )
        answer = response.choices[0].message.content.strip()
        logger.debug("LLM generated answer: %s", answer[:80])
        return answer
    except Exception as exc:
        logger.error("LLM answer generation failed: %s", exc)
        # Fallback: return a safe generic response
        return "Please refer to my CV for details."
