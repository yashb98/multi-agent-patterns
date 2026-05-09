"""Auto-answer common screening questions from job applications.

Pattern-based answers for frequent questions (work auth, availability, salary),
with LLM fallback for open-ended questions and SQLite caching via JobDB.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.agents import get_openai_client, get_model_name, is_local_llm
from shared.pii import assert_prompt_has_wrapped_pii, wrap_pii_value

from jobpulse.applicator import PROFILE, WORK_AUTH
from jobpulse.job_db import JobDB
from jobpulse.pipeline_hooks import with_tone_filter
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Strategy tier tracking — thread-local so concurrent form fills don't collide
_strategy_local = threading.local()


@dataclass
class AnswerResult:
    """Answer + metadata about how it was resolved."""
    answer: str
    strategy: str  # StrategyTier value
    confidence: float  # 0.0 - 1.0


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
    r"right.*work.*type|work.{0,20}type.{0,10}(?:visa|permit|authorization)|work.*permit|work.*authorization.*type|visa.*type|type.*visa.*hold|eligibility.*based|what.*eligibility": "SENSITIVE:visa_type",
    r"(?:any|are there).*restriction.*(?:right.*work|work.*uk)|restriction.*(?:your|on).*right": "No",
    r"authorized.*work|right to work|legally.*work|eligible.*work|unrestricted.*right": "Yes",
    r"require.*sponsor|visa.*sponsor|sponsorship|need.*sponsor": "No",
    r"visa.*status|immigration.*status|current.*visa|visa.*expire": "SENSITIVE:visa_status",
    r"british.*citizen|eu.*national|\bilr\b|indefinite.*leave|settled.*status": "No",
    r"subject.*immigration.*restrict|work.*without.*restrict": "No",

    # ===================================================================
    # SALARY & COMPENSATION (3 patterns) — current before expected
    # ===================================================================
    # `current.*base` must be salary-anchored — bare `base` collides with
    # location queries like "Are you currently based in the UK?". Audit S4
    # B-1: live repro returned the user's salary value (PII leak) for that
    # question because of unbounded `.*` between "current" and "base".
    r"current.*salary|salary.*current|present.*salary|current.*compensation|current.*base\s*(?:pay|salary|compensation|comp|rate|wage)": "CURRENT_SALARY",
    r"salary.*expect|expected.*salary|desired.*compensation|pay.*expect|minimum.*salary|salary.*range|target.*salary|compensation.*require|salary.*requirement": "ROLE_SALARY",
    r"daily.*rate|hourly.*rate|day.*rate": "150",

    # ===================================================================
    # NOTICE PERIOD & EMPLOYMENT (5 patterns)
    # ===================================================================
    r"notice.*period|when.*start|available.*start|start.*date|earliest.*start|how.*soon.*start|immediate.*start": "SCREENING:notice_period",
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
    # EXPERIENCE (4 patterns)
    # ===================================================================
    r"years.*experience|experience.*years|how.*many.*years|total.*years.*experience": "SKILL_EXPERIENCE",
    r"experience.*with|proficient.*in|familiar.*with|worked.*with": "SKILL_EXPERIENCE",
    r"have.*you.*worked.*in.*(?:data science|machine learning|analy|ai |ml |engineer)|worked.*(?:data science|ml|ai).*role": "Yes",
    r"happy.*(?:hands.?on|engineering|building|iterating)|significant.*part.*(?:hands.?on|engineering|building)": "Yes",

    # ===================================================================
    # EDUCATION (4 patterns) — specific before general
    # ===================================================================
    r"highest.*education|level.*education|highest.*qualification|completed.*education|highest.*degree": "SENSITIVE:highest_qualification",
    r"degree.*subject|field.*study|what.*degree|degree.*type|what.*major|degree.*classification": "SENSITIVE:degree_subject",
    r"currently.*study|currently.*enrolled|enrolled.*education": "No",
    r"stem.*degree|computer.*science.*degree|related.*field|relevant.*degree": "Yes",

    # ===================================================================
    # LANGUAGES (3 patterns) — specific before general
    # ===================================================================
    r"proficiency.*english|fluent.*english|english.*proficiency|level.*english": "SENSITIVE:second_language_proficiency",
    r"proficiency.*hindi|fluent.*hindi|hindi.*proficiency": "SENSITIVE:second_language_proficiency",
    r"languages.*speak|what.*languages|language.*skills|other.*language|do.*you.*speak": "SENSITIVE:languages_summary",

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
    # Removed: `based.*in.*uk|resident.*uk|...` — audit S4 B-1. The pattern
    # over-matched and answered "No" to plain location/residency questions
    # ("Are you a UK resident?", "Do you live in the UK?") even though the
    # user IS based in the UK. The legitimate "permanent resident / settled
    # status" cases are already covered by L127 (british.*citizen|...|
    # settled.*status). Plain location → V2 intent classifier handles it.

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
    r"what.*nationality|country.*citizen|country.*birth": "SENSITIVE:nationality",
    r"\btitle\b.*mr|salutation|honorific": "SENSITIVE:title",

    # ===================================================================
    # TEAM & MANAGEMENT (2 patterns) — specific before general
    # ===================================================================
    r"direct.*report|how.*many.*managed|people.*managed|team.*size|largest.*team": "SENSITIVE:largest_team_managed",
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
    # HIRING MESSAGE / INTEREST (1 pattern) — dynamic per company
    # ===================================================================
    r"company.*know.*interest|interest.*working.*there|message.*hiring.*team|message.*recruiter": "HIRING_MESSAGE",

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


def lookup_user_salary(job_title: str) -> int:
    """Look up the user's salary expectation for a job title.

    Two-pass match against the role_salary DB:
      1. Substring match — pick the longest role key contained in the title.
      2. Token-overlap fallback — when no substring match (e.g. title
         "Data Analytics" doesn't substring-match the role key "data
         analyst" because analytics≠analyst), use shared-token Jaccard
         similarity ≥0.5 to find the closest role.

    Live regression on Revolut welovealfa.com 2026-05-05: the title
    "Software Engineer (Data)" had no exact substring match and the
    agent fell back to LLM, which then read the JD's listed range
    £85,500-£118,000 from the page and used those as the user's salary
    expectation. The token fallback now matches on "software engineer"
    or "engineer" → £35-38k from role_salary.
    """
    title = (job_title or "").lower()
    role_salaries = _ensure_role_salary()
    default_salary = role_salaries.get("default", 30000)
    if not title:
        return default_salary
    salary = default_salary
    best_len = 0
    for role_key, role_salary in role_salaries.items():
        if role_key == "default":
            continue
        if role_key in title and len(role_key) > best_len:
            salary = role_salary
            best_len = len(role_key)
    if best_len > 0:
        return salary
    # Token fallback
    import re as _re
    title_tokens = {t for t in _re.findall(r"[a-z]{3,}", title)}
    if not title_tokens:
        return default_salary
    best_score = 0.0
    for role_key, role_salary in role_salaries.items():
        if role_key == "default":
            continue
        role_tokens = {t for t in _re.findall(r"[a-z]{3,}", role_key)}
        if not role_tokens:
            continue
        overlap = len(title_tokens & role_tokens)
        union = len(title_tokens | role_tokens)
        score = overlap / union if union else 0.0
        # Boost for distinctive role tokens (analyst/scientist/engineer)
        if overlap and role_tokens & {"analyst", "scientist", "engineer", "developer", "designer"}:
            score += 0.1
        if score > best_score and score >= 0.25:
            best_score = score
            salary = role_salary
    return salary


def _resolve_role_salary(
    job_context: dict | None, *, input_type: str | None
) -> str:
    """Return salary expectation based on job title and input type."""
    title = ((job_context or {}).get("job_title") or "")
    salary = lookup_user_salary(title)

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


_HIRING_MESSAGE_CACHE_TTL_DAYS = 30
_HIRING_MESSAGE_CACHE_LOCK = threading.Lock()


def _hiring_message_cache_init(db: JobDB) -> None:
    """Lazily create the hiring-message cache table inside applications.db.

    Keyed by ``(company_lower, role_archetype_lower)``; rows older than
    ``_HIRING_MESSAGE_CACHE_TTL_DAYS`` are treated as misses on lookup.
    """
    conn = db._connect()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hiring_message_cache ("
        "company TEXT NOT NULL, role_archetype TEXT NOT NULL, "
        "message TEXT NOT NULL, generated_at TEXT NOT NULL, "
        "hit_count INTEGER NOT NULL DEFAULT 0, "
        "PRIMARY KEY (company, role_archetype))"
    )
    conn.commit()


def _hiring_message_cache_lookup(
    company: str, role_archetype: str, *, db: JobDB | None = None,
) -> str | None:
    """Return cached message or None on miss / TTL expiry.

    Under ``JOBPULSE_TEST_MODE=1`` (set by ``tests/conftest.py``), a default
    ``db=None`` short-circuits to None so unrelated tests don't pick up
    cache entries from prior runs of the same suite. Tests that exercise
    cache behaviour pass an explicit ``db=`` kwarg from their tmp_path.
    Mirrors the same guard in ``cv_tailor._tailored_cv_cache_lookup``.
    """
    if not company or not role_archetype:
        return None
    import os as _os
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return None
    key = (company.lower().strip(), role_archetype.lower().strip())
    db = db or JobDB()
    with _HIRING_MESSAGE_CACHE_LOCK:
        _hiring_message_cache_init(db)
        conn = db._connect()
        row = conn.execute(
            "SELECT message, generated_at FROM hiring_message_cache "
            "WHERE company = ? AND role_archetype = ?", key,
        ).fetchone()
        if not row:
            return None
        try:
            generated = datetime.fromisoformat(row["generated_at"])
            age = datetime.now() - generated
            if age.days > _HIRING_MESSAGE_CACHE_TTL_DAYS:
                return None
        except (ValueError, TypeError):
            return None
        conn.execute(
            "UPDATE hiring_message_cache SET hit_count = hit_count + 1 "
            "WHERE company = ? AND role_archetype = ?", key,
        )
        conn.commit()
        return row["message"]


def _hiring_message_cache_store(
    company: str, role_archetype: str, message: str, *, db: JobDB | None = None,
) -> None:
    """Persist a freshly-generated hiring message.

    Under ``JOBPULSE_TEST_MODE=1`` with default ``db=None``, the store is
    a no-op — same rationale as the lookup guard above.
    """
    if not company or not role_archetype or not message:
        return
    import os as _os
    if db is None and _os.environ.get("JOBPULSE_TEST_MODE") == "1":
        return
    key = (company.lower().strip(), role_archetype.lower().strip())
    db = db or JobDB()
    with _HIRING_MESSAGE_CACHE_LOCK:
        _hiring_message_cache_init(db)
        conn = db._connect()
        conn.execute(
            "INSERT OR REPLACE INTO hiring_message_cache "
            "(company, role_archetype, message, generated_at, hit_count) "
            "VALUES (?, ?, ?, ?, 0)",
            (*key, message, datetime.now().isoformat()),
        )
        conn.commit()


def _classify_role_archetype(role_title: str) -> str:
    """Coarse role archetype for cache keying — keeps the (company, role)
    cache from being polluted by trivial title variations like
    'Senior X' vs 'X' vs 'X II'. Falls back to lowercased role title.
    """
    if not role_title:
        return "generic"
    t = role_title.lower().strip()
    if "data analyst" in t or "analytics" in t and "engineer" not in t:
        return "data_analyst"
    if "data engineer" in t:
        return "data_engineer"
    if "data scientist" in t:
        return "data_scientist"
    if "machine learning" in t or "ml engineer" in t or "ai engineer" in t:
        return "ml_engineer"
    if "research engineer" in t or "research scientist" in t:
        return "research_engineer"
    if "backend" in t or "back-end" in t:
        return "backend_engineer"
    if "frontend" in t or "front-end" in t:
        return "frontend_engineer"
    if "full stack" in t or "fullstack" in t or "full-stack" in t:
        return "fullstack_engineer"
    if "software engineer" in t or "developer" in t:
        return "software_engineer"
    return t.split()[0] if t else "generic"


def _generate_hiring_message(job_context: dict | None) -> str:
    """Generate a tailored hiring message using LLM with the user's core projects."""
    ctx = job_context or {}
    company = ctx.get("company", "the company")
    role = ctx.get("title", "this role")

    # Cache lookup: per-(company, role_archetype). Same company + role
    # returns the cached message without firing an LLM call. TTL keeps
    # messages from going stale across job-description rewrites.
    role_archetype = ctx.get("archetype") or _classify_role_archetype(role)
    cached = _hiring_message_cache_lookup(company, role_archetype)
    if cached:
        logger.info(
            "hiring_message_cache: hit on (%s, %s) — skipping LLM",
            company[:40], role_archetype[:30],
        )
        return cached

    # Build the project highlights from cv_projects in user_profile.db so the
    # narrative reflects the user's actual portfolio rather than a hardcoded
    # snapshot for one specific applicant. Falls back to a generic skill-shape
    # narrative when the DB is empty (e.g. fresh install).
    _CORE_PROJECTS = ""
    try:
        from shared.profile_store import get_profile_store
        store = get_profile_store()
        bullets: list[str] = []
        for proj in (store.cv_projects() or [])[:4]:
            title = (proj.get("title") or "").strip()
            url = (proj.get("url") or "").strip()
            proj_bullets = proj.get("bullets") or []
            first_bullet = (proj_bullets[0] if proj_bullets else "").strip()
            if title and first_bullet:
                # Strip HTML tags from bullet for plaintext narrative
                import re as _re
                clean = _re.sub(r"<[^>]+>", "", first_bullet)
                url_part = f" ({url})" if url else ""
                bullets.append(f"- {title}{url_part}: {clean}")
        _CORE_PROJECTS = "\n".join(bullets)
    except Exception:
        pass

    if not _CORE_PROJECTS:
        # Generic skill-shape fallback (no PII) — still useful for prompt
        # context when the project DB hasn't been populated.
        _CORE_PROJECTS = (
            "- Production engineering with measurable outcomes (A/B tests, "
            "conversion funnels, error handling, cost tracking).\n"
            "- Pragmatic GenAI: rule-based first, embeddings second, LLM only "
            "when needed — saves 70-85% of API costs vs. LLM-first designs.\n"
            "- Builds safely: structured error handling, prompt-injection "
            "defence, dry-run-first workflows."
        )

    prompt = (
        f"Write a short message (150-200 words, plain text, NO greeting/sign-off/subject) "
        f"for a job application form text box at {company} for the role: {role}.\n\n"
        f"The candidate's core projects and strengths:\n{_CORE_PROJECTS}\n\n"
        f"Rules:\n"
        f"- First person, conversational but confident. Not a formal letter.\n"
        f"- Open with ONE sentence about the most relevant project for THIS company/role.\n"
        f"- Then 2-3 sentences showing concrete alignment with the role.\n"
        f"- Close with one forward-looking sentence about the company's mission.\n"
        f"- No bullet points, no headers, no greetings, no sign-offs, no placeholders.\n"
        f"- Do NOT use em-dashes. Use commas or periods instead.\n"
        f"- Make a recruiter want to schedule an interview after reading this."
    )

    try:
        from shared.agents import get_llm, smart_llm_call
        from langchain_core.messages import HumanMessage
        llm = get_llm(temperature=0.7, agent_name="screening_answers")
        result = smart_llm_call(llm, [HumanMessage(content=prompt)])
        text = result.content if hasattr(result, "content") else str(result)
        if text and len(text.strip()) > 50:
            cleaned = text.strip()
            _hiring_message_cache_store(company, role_archetype, cleaned)
            return cleaned
    except Exception as exc:
        logger.debug("Hiring message LLM generation failed: %s", exc)

    return (
        f"I have spent the past year building a production multi-agent AI system that "
        f"orchestrates 10+ autonomous agents using LangGraph, with a 3-engine memory layer "
        f"and self-improving reinforcement learning. I evaluate GenAI vs simpler approaches "
        f"daily, saving 70-85% of API costs through a rule-based-first architecture. "
        f"I would love to bring this hands-on GenAI engineering experience to {company}."
    )


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
        resolved = ""
        try:
            from shared.profile_store import get_profile_store
            val = get_profile_store().screening_default(key)
            if val:
                resolved = val
        except Exception:
            pass
        # Date adaptation: notice_period returns strings like "Immediately"
        # or "1 month" — convert to YYYY-MM-DD when the form wants a date.
        if input_type == "date" and resolved:
            today = datetime.now()
            lower = resolved.strip().lower()
            if lower in ("immediately", "asap", "now"):
                target = today + timedelta(days=14)
            elif "week" in lower:
                # "2 weeks" / "1 week"
                weeks = next((int(s) for s in lower.split() if s.isdigit()), 2)
                target = today + timedelta(weeks=weeks)
            elif "month" in lower:
                months = next((int(s) for s in lower.split() if s.isdigit()), 1)
                target = today + timedelta(days=months * 30)
            else:
                target = today + timedelta(days=14)
            return target.strftime("%Y-%m-%d")
        return resolved

    if answer == "HIRING_MESSAGE":
        return _generate_hiring_message(job_context)

    if answer == "CURRENT_SALARY":
        try:
            from shared.profile_store import get_profile_store
            val = get_profile_store().sensitive("current_salary")
            if val:
                return f"{int(val):,}" if input_type == "text" else val
        except Exception:
            pass
        # No hardcoded fallback — current_salary is PII and must be set in DB.
        # Return empty so the caller treats this as a screening miss and either
        # skips the field, prompts the user, or escalates to LLM with options.
        logger.warning(
            "screening_answers: CURRENT_SALARY placeholder requested but "
            "sensitive_fields.current_salary is empty — set it via "
            "ProfileStore.set_sensitive('current_salary', ...) or skip the field"
        )
        return ""

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

    # --- Tier 1: V2 pipeline (semantic cache + intent classifier + LLM) ---
    # Primary path. Embedding similarity over 175 prototype questions across
    # 31 intents survives paraphrase/typo/multilingual drift. Regex tiers
    # below remain as safety nets for cases the embeddings haven't learned.
    v2_answer = try_screening_v2(normalised, job_context)
    if v2_answer:
        logger.debug("Screening V2 answer for '%s' -> '%s'", normalised[:60], v2_answer[:80])
        _strategy_local.last = AnswerResult(v2_answer, "screening_v2", 0.75)
        return with_tone_filter(v2_answer, normalised, None)

    # --- Tier 2: agent rules (learned from user corrections) -------------
    # User-stored regex patterns (not hardcoded) — these are learned signals,
    # genuinely dynamic, retained as-is.
    try:
        from jobpulse.agent_rules import AgentRulesDB
        import re as _re
        _rules_db = AgentRulesDB()
        _all_rules = _rules_db.get_active_rules("correction_override")
        for _rule in _all_rules:
            if _rule.get("category") == "screening" and _re.search(_rule.get("pattern", ""), normalised):
                _rule_val = _rule.get("value", "")
                if _rule_val:
                    logger.debug("Agent rule match for '%s' -> '%s'", normalised[:60], _rule_val[:80])
                    _strategy_local.last = AnswerResult(_rule_val, "agent_rule", 0.85)
                    return with_tone_filter(_rule_val, normalised, None)
    except Exception:
        pass

    # --- Tier 3: COMMON_ANSWERS regex fallback (legacy heuristic) --------
    # Only fires when the V2 embedding pipeline has nothing useful. Regex
    # patterns are brittle on paraphrase but provide a cheap last-resort
    # for known-shape questions (yes/no, work auth, salary). Each match
    # logs that the dynamic path missed — those should be added as
    # prototype questions in screening_intent.py to retire the regex
    # over time.
    for pattern, answer in COMMON_ANSWERS.items():
        if re.search(pattern, normalised, re.IGNORECASE):
            if answer is not None:
                resolved = _resolve_placeholder(
                    answer, normalised, job_context,
                    input_type=input_type, platform=platform, db=db,
                )
                logger.info(
                    "screening_answers: regex fallback hit for '%s' (V2 missed) "
                    "— consider adding to screening_intent prototypes",
                    normalised[:60],
                )
                _strategy_local.last = AnswerResult(resolved, "regex_fallback", 0.7)
                return with_tone_filter(resolved, normalised, None)
            llm_answer = _generate_answer(normalised, job_context)
            logger.info(
                "screening_answers: regex-triggered LLM fallback for '%s'",
                normalised[:60],
            )
            _strategy_local.last = AnswerResult(llm_answer, "llm_tier3", 0.6)
            return with_tone_filter(llm_answer, normalised, None)

    # --- Tier 4: LLM generation → cache in V2 ----------------------------
    answer = _generate_answer(normalised, job_context)
    try:
        from jobpulse.screening_semantic_cache import get_screening_semantic_cache
        get_screening_semantic_cache().cache(
            question=normalised, intent="unknown", answer=answer, confidence=0.55,
        )
    except Exception:
        pass
    logger.info("Generated + cached (V2) answer for '%s'", normalised[:60])
    _strategy_local.last = AnswerResult(answer, "llm_tier4", 0.6)
    return with_tone_filter(answer, normalised, None)


def get_last_strategy() -> AnswerResult | None:
    """Return the strategy tier used for the last get_answer() call (thread-local)."""
    return getattr(_strategy_local, "last", None)


def get_answer_with_strategy(
    question: str,
    job_context: dict | None = None,
    **kwargs,
) -> AnswerResult:
    """Like get_answer() but returns AnswerResult with strategy metadata."""
    answer = get_answer(question, job_context, **kwargs)
    result = getattr(_strategy_local, "last", None)
    if result is not None:
        return result
    return AnswerResult(answer=answer, strategy="default_fallback", confidence=0.3)


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
# Screening V2 Pipeline Integration
# ---------------------------------------------------------------------------

# Module-level singleton for the V2 pipeline (lazy init)
_v2_pipeline = None


def _get_v2_pipeline():
    """Lazy-init the ScreeningPipeline with applicant profile + work auth."""
    global _v2_pipeline
    if _v2_pipeline is not None:
        return _v2_pipeline
    try:
        from jobpulse.screening_pipeline import ScreeningPipeline
        merged = dict(PROFILE)
        merged["visa_status"] = str(WORK_AUTH.get("visa_status", ""))
        merged["visa_sponsorship_required"] = "No" if not WORK_AUTH.get("requires_sponsorship") else "Yes"
        merged["right_to_work"] = "Yes" if WORK_AUTH.get("right_to_work_uk") else "No"
        merged["notice_period"] = str(WORK_AUTH.get("notice_period", ""))
        merged["salary_expectation"] = str(WORK_AUTH.get("salary_expectation", ""))
        _v2_pipeline = ScreeningPipeline(profile=merged)
        logger.debug("Screening V2 pipeline initialised")
        return _v2_pipeline
    except Exception as exc:
        logger.debug("Screening V2 pipeline unavailable: %s", exc)
        return None


def try_screening_v2(
    question: str,
    job_context: dict | None = None,
    *,
    field: dict | None = None,
    min_confidence: float = 0.55,
) -> str | None:
    """Try the V2 screening pipeline (semantic cache → intent → regex → rules → LLM).

    Returns the answer string if confidence >= min_confidence, else None.
    This is a non-blocking, best-effort call — failures are swallowed silently.

    Skipped in test mode to preserve deterministic test behaviour for the
    legacy resolution tiers.
    """
    import os
    if os.getenv("JOBPULSE_TEST_MODE") == "1":
        return None

    if not question or not question.strip():
        return None

    pipeline = _get_v2_pipeline()
    if pipeline is None:
        return None

    try:
        result = pipeline.answer(question.strip(), field=field, job_context=job_context)
        answer = result.get("answer", "")
        confidence = result.get("confidence", 0.0)
        source = result.get("source", "unknown")

        if answer and confidence >= min_confidence:
            logger.debug(
                "Screening V2 (%s, conf=%.2f) for '%s' -> '%s'",
                source, confidence, question[:60], answer[:80],
            )
            return answer

        logger.debug(
            "Screening V2 (%s) confidence too low (%.2f < %.2f) for '%s'",
            source, confidence, min_confidence, question[:60],
        )
        return None
    except Exception as exc:
        logger.debug("Screening V2 failed for '%s': %s", question[:60], exc)
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
        model = get_model_name()
        from shared.agents import _token_limit_kwargs
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": task}],
            temperature=0.4,
            **_token_limit_kwargs(model, 600 if is_local_llm() else 200),
        )
        try:
            from shared.cost_tracker import record_openai_usage
            record_openai_usage(response, agent_name="screening_answers", model_hint=get_model_name())
        except Exception:
            pass

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
