"""Auto-answer common screening questions from job applications.

Pattern-based answers for frequent questions (work auth, availability, salary),
with LLM fallback for open-ended questions and SQLite caching via JobDB.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from shared.agents import get_openai_client, get_model_name, is_local_llm
from shared.pii import assert_prompt_has_wrapped_pii, wrap_pii_value

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.job_db import JobDB
from jobpulse.pipeline_hooks import with_tone_filter
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _screening_prompt_profile() -> dict[str, str]:
    from shared.profile_store import get_profile_store
    ps = get_profile_store()
    ident = ps.identity()
    visa = ps.sensitive("visa_status") or WORK_AUTH.get("visa_status", "")
    return {
        "first_name": ident.first_name or PROFILE.get("first_name", ""),
        "last_name": ident.last_name or PROFILE.get("last_name", ""),
        "education": ident.education or PROFILE.get("education", ""),
        "location": ident.location or PROFILE.get("location", ""),
        "visa_status": visa,
    }


def _screening_profile_summary(profile: dict[str, str]) -> str:
    return (
        f"Name: {wrap_pii_value('screening.first_name', profile['first_name'])} "
        f"{wrap_pii_value('screening.last_name', profile['last_name'])}. "
        f"Education: {wrap_pii_value('screening.education', profile['education'])}. "
        f"Location: {wrap_pii_value('screening.location', profile['location'])}. "
        f"Visa: {wrap_pii_value('screening.visa_status', profile['visa_status'])}."
    )

# ---------------------------------------------------------------------------
# Skill-specific experience years (used for "How many years with X?" questions)
# ---------------------------------------------------------------------------
def _get_skill_experience() -> dict[str, float]:
    from shared.profile_store import get_profile_store
    result = get_profile_store().skill_experience()
    return result if isinstance(result, dict) else {}

SKILL_EXPERIENCE: dict[str, int] = {}  # populated lazily on first use
_se_lock = __import__("threading").Lock()


def _ensure_skill_experience() -> dict[str, float]:
    global SKILL_EXPERIENCE
    if not SKILL_EXPERIENCE:
        with _se_lock:
            if not SKILL_EXPERIENCE:
                SKILL_EXPERIENCE = _get_skill_experience()
    return SKILL_EXPERIENCE

# ---------------------------------------------------------------------------
# Role-aware salary expectations
# ---------------------------------------------------------------------------
def _get_role_salary() -> dict[str, int]:
    from shared.profile_store import get_profile_store
    result = get_profile_store().role_salary()
    return result if isinstance(result, dict) else {}

ROLE_SALARY: dict[str, int] = {}  # populated lazily on first use
_rs_lock = __import__("threading").Lock()


def _ensure_role_salary() -> dict[str, int]:
    global ROLE_SALARY
    if not ROLE_SALARY:
        with _rs_lock:
            if not ROLE_SALARY:
                ROLE_SALARY = _get_role_salary()
    return ROLE_SALARY

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
    r"please.*select.*right.*work.*status|right.*work.*status": "SENSITIVE:visa_status_full",
    r"right.*work.*type|work.*type|work.*permit|work.*authorization.*type|visa.*type|type.*visa.*hold|eligibility.*based|what.*eligibility": "SENSITIVE:visa_type",
    r"authorized.*work|right to work|legally.*work|eligible.*work|unrestricted.*right": "Yes",
    r"require.*sponsor|visa.*sponsor|sponsorship|need.*sponsor": "No",
    r"visa.*status|immigration.*status|current.*visa|visa.*expire": "SENSITIVE:visa_status",
    r"british.*citizen|eu.*national|\bilr\b|indefinite.*leave|settled.*status": "No",
    r"subject.*immigration.*restrict|work.*without.*restrict": "No",

    # ===================================================================
    # SALARY & COMPENSATION (3 patterns) — current before expected
    # ===================================================================
    r"current.*salary|salary.*current|present.*salary|current.*compensation|current.*base": "CURRENT_SALARY",
    r"salary.*expect|expected.*salary|desired.*compensation|pay.*expect|minimum.*salary|salary.*range|target.*salary|compensation.*require|salary.*requirement": "ROLE_SALARY",
    r"daily.*rate|hourly.*rate|day.*rate": "150",

    # ===================================================================
    # NOTICE PERIOD & EMPLOYMENT (5 patterns)
    # ===================================================================
    r"notice.*period|when.*start|available.*start|start.*date|earliest.*start|how.*soon.*start|immediate.*start": "Immediately",
    r"currently.*employ|current.*employment|employment.*status|are.*you.*employ": "Yes",
    r"current.*job.*title|current.*role|current.*position|present.*role": "SCREENING:current_job_title",
    r"current.*employer|who.*work.*for|present.*employer|company.*work.*for": "SCREENING:current_employer",
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
    # BACKGROUND, SECURITY & LEGAL (6 patterns) — specific before general
    # ===================================================================
    r"(?:do|have).*you.*(?:criminal|unspent|civil).*conviction|(?:do|have).*you.*(?:been|ever).*convicted|have.*(?:any|an).*offence": "No",
    r"(?:anything|something).*(?:to )?disclose|(?:indicate|declare).*(?:anything|something).*disclose": "No",
    r"background.*check|dbs.*check|criminal.*record|willing.*undergo|pre.?employment.*screen": "Yes",
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
    r"gender.*identit|what.*gender|indicate.*gender|what.*your.*sex\b": "SENSITIVE:gender",
    r"sexual.*orientation|what.*orientation|indicate.*orientation": "SENSITIVE:sexual_orientation",
    r"ethnicity|ethnic.*background|racial|indicate.*ethnicity|race.*ethnic": "SENSITIVE:ethnicity",
    r"disability|disabled|long.?term.*health|equality.*act.*2010|impairment.*health|neurodivergent": "No",
    r"veteran|military": "No",
    r"religion\b|belief\b|faith\b|spiritual": "SENSITIVE:religion",
    r"marital.*status|civil.*status|relationship.*status": "SENSITIVE:marital_status",
    r"refer.*to.*you|how.*address|what.*pronoun|preferred.*pronoun|indicate.*pronoun": "SENSITIVE:pronouns",
    r"age.*(?:group|range|band|bracket)|what.*your.*age|date.*birth": "SENSITIVE:age_group",
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
        years = _ensure_skill_experience().get(skill.lower(), 2)
    return str(int(years))


def _resolve_role_salary(
    job_context: dict | None, *, input_type: str | None
) -> str:
    """Return salary expectation based on job title and input type."""
    title = ((job_context or {}).get("job_title") or "").lower()
    role_salaries = _ensure_role_salary()
    salary = role_salaries.get("default", 30000)
    best_len = 0
    for role_key, role_salary in role_salaries.items():
        if role_key != "default" and role_key in title and len(role_key) > best_len:
            salary = role_salary
            best_len = len(role_key)

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
        try:
            from shared.profile_store import get_profile_store
            ident = get_profile_store().identity()
            link = ident.github or ident.portfolio
            if link:
                return link
        except Exception:
            pass
        return PROFILE.get("github", PROFILE.get("portfolio", ""))

    if answer.startswith("SENSITIVE:"):
        key = answer[len("SENSITIVE:"):]
        try:
            from shared.profile_store import get_profile_store
            val = get_profile_store().sensitive(key)
            if val:
                return val
        except Exception:
            pass
        return "Prefer not to say"

    if answer.startswith("SCREENING:"):
        key = answer[len("SCREENING:"):]
        try:
            from shared.profile_store import get_profile_store
            val = get_profile_store().screening_default(key)
            if val:
                return val
        except Exception:
            pass
        return ""

    if answer == "CURRENT_SALARY":
        try:
            from shared.profile_store import get_profile_store
            val = get_profile_store().sensitive("current_salary")
            if val:
                return f"{int(val):,}" if input_type == "text" else val
        except Exception:
            pass
        return "22000"

    # Input-type adaptations for non-placeholder answers
    if answer == "Immediately" and input_type == "date":
        target = datetime.now() + timedelta(days=14)
        return target.strftime("%Y-%m-%d")

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
            # Matched but needs LLM (answer is None) — cache result for reuse
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            _db_tier1 = db or JobDB()
            llm_answer = _generate_answer(normalised, job_context)
            _db_tier1.cache_answer(normalised, llm_answer)
            logger.info("Generated + cached Tier 1 answer for '%s'", normalised[:60])
            return with_tone_filter(llm_answer, normalised, None)

    # --- Tier 1.5: agent rules (learned from corrections) -----------------
    try:
        from jobpulse.agent_rules import AgentRulesDB
        _rules_db = AgentRulesDB()
        _matching = _rules_db.get_rules(
            category="screening", field_label=normalised, platform=platform,
        )
        if _matching:
            _rule = _matching[0]
            _rule_val = _rules_db.apply_rule(_rule, "", None)
            if _rule_val is not None:
                logger.debug("Agent rule match for '%s' -> '%s'", normalised[:60], _rule_val[:80])
                return with_tone_filter(_rule_val, normalised, None)
    except Exception:
        pass

    # --- Tier 2: cache lookup --------------------------------------------
    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
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


def try_instant_answer(
    question: str,
    job_context: dict | None = None,
    *,
    db: JobDB | None = None,
    input_type: str | None = None,
    platform: str | None = None,
) -> str | None:
    """Pattern match + cache lookup only (no LLM). Returns ``None`` on miss."""
    if not question or not question.strip():
        return None
    normalised = question.strip()

    for pattern, answer in COMMON_ANSWERS.items():
        if re.search(pattern, normalised, re.IGNORECASE):
            if answer is not None:
                return _resolve_placeholder(
                    answer, normalised, job_context,
                    input_type=input_type, platform=platform, db=db,
                )
            return None

    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
        return cached

    return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _score_screening_answer(answer: str) -> float:
    if not answer or len(answer.strip()) < 5:
        return 2.0
    if any(kw in answer.lower() for kw in ("error", "sorry", "cannot", "i don't know")):
        return 3.0
    return 8.0


_screening_engine = None


def _get_screening_engine():
    global _screening_engine
    if _screening_engine is not None:
        return _screening_engine
    import os
    if os.getenv("COGNITIVE_ENABLED", "true").lower() == "false":
        return None
    try:
        from shared.cognitive import get_cognitive_engine
        _screening_engine = get_cognitive_engine(agent_name="screening_answers")
        return _screening_engine
    except Exception as e:
        logger.debug("Cognitive engine unavailable for screening: %s", e)
        return None


def _generate_answer(question: str, job_context: dict | None = None) -> str:
    """Use CognitiveEngine (if available) or direct LLM for screening answers."""
    context_line = ""
    if job_context:
        title = job_context.get("job_title", "the role")
        company = job_context.get("company", "the company")
        context_line = f" Context: Applying for {title} at {company}."

    prompt_profile = _screening_prompt_profile()
    profile_summary = _screening_profile_summary(prompt_profile)

    # Read optimization correction insights for this domain before generating
    correction_context = ""
    try:
        from shared.optimization import get_optimization_engine
        _opt_domain = (job_context or {}).get("company", "screening")
        engine = get_optimization_engine()
        insights = engine._bus.query(signal_type="correction", domain=_opt_domain, limit=10)
        if insights:
            corrections = [
                f"- {s.payload.get('field', '')}: use '{s.payload.get('new_value', '')}' not '{s.payload.get('old_value', '')}'"
                for s in insights if s.payload.get("field")
            ]
            if corrections:
                correction_context = " Past corrections for this domain:\n" + "\n".join(corrections[:5])
    except Exception:
        pass

    task = (
        "Answer this job application screening question concisely (1-3 sentences). "
        f"Be professional and positive. Question: {question}.{context_line} "
        f"Applicant background: {profile_summary}"
        f"{correction_context}"
    )
    assert_prompt_has_wrapped_pii(task, prompt_profile, "screening")

    engine = _get_screening_engine()
    if engine:
        try:
            result = engine.think_sync(
                task=task, domain="screening_answers", stakes="medium",
                scorer=_score_screening_answer,
            )
            engine.flush_sync()
            logger.debug("Cognitive L%d screening answer (score=%.1f, cost=$%.4f)",
                         result.level.value, result.score, result.cost)
            return result.answer.strip()
        except Exception as e:
            logger.warning("Cognitive engine failed for screening, falling back: %s", e)

    try:
        client = get_openai_client()
        response = client.chat.completions.create(
            model=get_model_name(),
            messages=[{"role": "user", "content": task}],
            max_tokens=600 if is_local_llm() else 200,
            temperature=0.4,
        )
        answer = response.choices[0].message.content.strip()
        logger.debug("LLM generated answer: %s", answer[:80])
        return answer
    except Exception as exc:
        logger.error("LLM answer generation failed for '%s': %s", question[:60], exc)
        if any(kw in question.lower() for kw in ("salary", "compensation", "pay")):
            return WORK_AUTH.get("salary_expectation", "Open to discussion")
        if any(kw in question.lower() for kw in ("notice", "start date", "availability")):
            return WORK_AUTH.get("notice_period", "Flexible")
        if any(kw in question.lower() for kw in ("visa", "sponsor", "right to work", "authoriz")):
            return WORK_AUTH.get("visa_status", "Yes, I have the right to work")
        return "Yes"
