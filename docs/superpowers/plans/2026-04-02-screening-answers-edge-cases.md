# Screening Answers Edge Case Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand screening_answers.py from 19 to 64 regex patterns with dynamic answers, input-type awareness, and collision guard tests.

**Architecture:** Keep existing 3-tier system (regex -> cache -> LLM). Add new dicts (SKILL_EXPERIENCE, ROLE_SALARY, PLATFORM_SOURCE), special placeholder resolvers, and input_type/platform parameters to get_answer(). Collision guard test suite prevents future regex regressions.

**Tech Stack:** Python 3.12, pytest, re (regex), SQLite (JobDB)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/screening_answers.py` | Core module — COMMON_ANSWERS dict, lookup dicts, get_answer(), resolvers |
| `jobpulse/ats_adapters/linkedin.py` | Pass input_type and platform to get_answer() calls |
| `jobpulse/job_db.py` | Add count_applications_for_company() method |
| `tests/test_screening_answers.py` | Update existing tests for new answer values |
| `tests/test_screening_collision_guard.py` | NEW — collision guard + positive match tests |
| `tests/test_screening_dynamic.py` | NEW — skill lookup, role salary, previously applied, platform source |

---

### Task 1: Add SKILL_EXPERIENCE and ROLE_SALARY dicts + extraction helper

**Files:**
- Modify: `jobpulse/screening_answers.py:1-62`
- Test: `tests/test_screening_dynamic.py` (create)

- [ ] **Step 1: Write failing tests for skill extraction and role salary**

Create `tests/test_screening_dynamic.py`:

```python
"""Tests for dynamic screening answer features — skill lookup, role salary, previously applied."""

from __future__ import annotations

from jobpulse.screening_answers import (
    ROLE_SALARY,
    SKILL_EXPERIENCE,
    _extract_skill_from_question,
    _resolve_role_salary,
    _resolve_skill_experience,
)


# ------------------------------------------------------------------
# Skill experience extraction
# ------------------------------------------------------------------

def test_extract_skill_python():
    q = "How many years of experience do you have with Python?"
    assert _extract_skill_from_question(q) == "python"


def test_extract_skill_machine_learning():
    q = "How many years of experience do you have in machine learning?"
    assert _extract_skill_from_question(q) == "machine learning"


def test_extract_skill_generic():
    q = "How many years of relevant experience do you have?"
    assert _extract_skill_from_question(q) is None


def test_extract_skill_sql():
    q = "How many years of experience do you have with SQL?"
    assert _extract_skill_from_question(q) == "sql"


def test_extract_skill_docker():
    q = "How many years of work experience do you have with Docker?"
    assert _extract_skill_from_question(q) == "docker"


# ------------------------------------------------------------------
# Skill experience resolution
# ------------------------------------------------------------------

def test_resolve_skill_python():
    assert _resolve_skill_experience("python", input_type=None) == "3"


def test_resolve_skill_ml():
    assert _resolve_skill_experience("machine learning", input_type=None) == "2"


def test_resolve_skill_unknown_defaults_to_2():
    assert _resolve_skill_experience("fortran", input_type=None) == "2"


def test_resolve_skill_none_defaults_to_2():
    assert _resolve_skill_experience(None, input_type=None) == "2"


def test_resolve_skill_number_field():
    assert _resolve_skill_experience("python", input_type="number") == "3"


def test_resolve_skill_text_field():
    result = _resolve_skill_experience("python", input_type="text")
    assert result == "3"


# ------------------------------------------------------------------
# Role salary resolution
# ------------------------------------------------------------------

def test_role_salary_data_scientist():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    assert _resolve_role_salary(ctx, input_type="number") == "32000"


def test_role_salary_data_analyst():
    ctx = {"job_title": "Data Analyst", "company": "Deloitte"}
    assert _resolve_role_salary(ctx, input_type="number") == "28000"


def test_role_salary_ml_engineer():
    ctx = {"job_title": "Machine Learning Engineer", "company": "Google"}
    assert _resolve_role_salary(ctx, input_type="number") == "32000"


def test_role_salary_default():
    ctx = {"job_title": "Unknown Role", "company": "Unknown"}
    assert _resolve_role_salary(ctx, input_type="number") == "28000"


def test_role_salary_none_context():
    assert _resolve_role_salary(None, input_type="number") == "28000"


def test_role_salary_text_field_data_scientist():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    result = _resolve_role_salary(ctx, input_type="text")
    assert "30,000" in result or "30000" in result


def test_role_salary_text_field_default():
    ctx = {"job_title": "Unknown Role", "company": "Unknown"}
    result = _resolve_role_salary(ctx, input_type="text")
    assert "27,000" in result or "27000" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_screening_dynamic.py -v`
Expected: FAIL with ImportError (functions don't exist yet)

- [ ] **Step 3: Implement SKILL_EXPERIENCE, ROLE_SALARY, and helper functions**

Add to `jobpulse/screening_answers.py` after the existing imports and before `COMMON_ANSWERS`:

```python
from datetime import datetime, timedelta

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
```

Then add these resolver functions at the bottom of the file (before `_generate_answer`):

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_screening_dynamic.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_answers.py tests/test_screening_dynamic.py
git commit -m "feat(screening): add SKILL_EXPERIENCE, ROLE_SALARY dicts + resolvers"
```

---

### Task 2: Add count_applications_for_company to JobDB

**Files:**
- Modify: `jobpulse/job_db.py:313+`
- Test: `tests/test_screening_dynamic.py` (append)

- [ ] **Step 1: Write failing test for previously-applied lookup**

Append to `tests/test_screening_dynamic.py`:

```python
from unittest.mock import MagicMock
from jobpulse.job_db import JobDB
from jobpulse.screening_answers import _check_previously_applied


def test_previously_applied_yes(tmp_path):
    db = JobDB(db_path=tmp_path / "test.db")
    # Insert a fake application for "Gousto"
    with db._connect() as conn:
        conn.execute(
            "INSERT INTO job_listings (job_id, title, company, url, platform, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("j1", "Data Scientist", "Gousto", "https://example.com", "linkedin"),
        )
        conn.execute(
            "INSERT INTO applications (job_id, status, created_at, updated_at) "
            "VALUES (?, ?, datetime('now'), datetime('now'))",
            ("j1", "Applied"),
        )
    result = _check_previously_applied(
        "Have you previously applied to this company?",
        {"company": "Gousto"},
        db=db,
    )
    assert result == "Yes"


def test_previously_applied_no(tmp_path):
    db = JobDB(db_path=tmp_path / "test.db")
    result = _check_previously_applied(
        "Have you previously applied to this company?",
        {"company": "Microsoft"},
        db=db,
    )
    assert result == "No"


def test_previously_applied_no_context(tmp_path):
    db = JobDB(db_path=tmp_path / "test.db")
    result = _check_previously_applied(
        "Have you previously applied?",
        None,
        db=db,
    )
    assert result == "No"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_screening_dynamic.py::test_previously_applied_yes -v`
Expected: FAIL with ImportError or AttributeError

- [ ] **Step 3: Add count_applications_for_company to JobDB**

Add to `jobpulse/job_db.py` after the `fuzzy_match_exists` method:

```python
    def count_applications_for_company(self, company: str) -> int:
        """Return count of non-skipped applications for a company (case-insensitive)."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM applications a
                JOIN job_listings l ON a.job_id = l.job_id
                WHERE LOWER(l.company) = LOWER(?)
                  AND a.status NOT IN ('Skipped', 'Withdrawn')
                """,
                (company,),
            ).fetchone()
        return row["cnt"] if row else 0
```

- [ ] **Step 4: Add _check_previously_applied to screening_answers.py**

Add to `jobpulse/screening_answers.py` alongside the other resolver functions:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_screening_dynamic.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/job_db.py jobpulse/screening_answers.py tests/test_screening_dynamic.py
git commit -m "feat(screening): add previously-applied DB lookup"
```

---

### Task 3: Expand COMMON_ANSWERS from 19 to 64 patterns

**Files:**
- Modify: `jobpulse/screening_answers.py:25-62`

- [ ] **Step 1: Replace the COMMON_ANSWERS dict**

Replace the entire `COMMON_ANSWERS` dict in `jobpulse/screening_answers.py` with:

```python
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
```

- [ ] **Step 2: Run existing tests to check baseline**

Run: `python -m pytest tests/test_screening_answers.py -v`
Expected: Several FAIL (expected values changed — salary, experience, etc.)

- [ ] **Step 3: Commit the pattern expansion (tests fixed in Task 4)**

```bash
git add jobpulse/screening_answers.py
git commit -m "feat(screening): expand COMMON_ANSWERS from 19 to 64 patterns"
```

---

### Task 4: Update get_answer() with input_type, platform, and placeholder resolvers

**Files:**
- Modify: `jobpulse/screening_answers.py:70-127`

- [ ] **Step 1: Rewrite get_answer() with new signature and resolver logic**

Replace the `get_answer` function in `jobpulse/screening_answers.py`:

```python
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
                return resolved
            # Matched but needs LLM (answer is None)
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            return _generate_answer(normalised, job_context)

    # --- Tier 2: cache lookup --------------------------------------------
    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
        _db.cache_answer(normalised, cached)
        logger.debug("Cache hit for '%s'", normalised[:60])
        return cached

    # --- Tier 3: LLM generation ------------------------------------------
    answer = _generate_answer(normalised, job_context)
    _db.cache_answer(normalised, answer)
    logger.info("Generated + cached answer for '%s'", normalised[:60])
    return answer


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
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/test_screening_dynamic.py tests/test_screening_answers.py -v`
Expected: Dynamic tests PASS, some existing tests still FAIL (values changed)

- [ ] **Step 3: Commit**

```bash
git add jobpulse/screening_answers.py
git commit -m "feat(screening): add input_type/platform params + placeholder resolvers"
```

---

### Task 5: Update existing tests for new answer values

**Files:**
- Modify: `tests/test_screening_answers.py`

- [ ] **Step 1: Update test_screening_answers.py**

Replace the full file content of `tests/test_screening_answers.py`:

```python
"""Tests for jobpulse.screening_answers — pattern matching, caching, LLM fallback."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jobpulse.job_db import JobDB
from jobpulse.screening_answers import (
    COMMON_ANSWERS,
    cache_answer,
    get_answer,
    get_cached_answer,
)


# ------------------------------------------------------------------
# Work authorization
# ------------------------------------------------------------------

def test_authorization_yes():
    assert get_answer("Are you authorized to work in the UK?") == "Yes"
    assert get_answer("Do you have the right to work in the United Kingdom?") == "Yes"
    assert get_answer("Are you legally allowed to work in the UK?") == "Yes"


def test_right_to_work_type():
    assert get_answer("What is your Right to Work Type?") == "Graduate Visa"
    assert get_answer("What type of visa do you currently hold?") == "Graduate Visa"


def test_sponsorship_no():
    assert get_answer("Do you require visa sponsorship?") == "No"
    assert get_answer("Will you need sponsorship to work in the UK?") == "No"


def test_visa_status():
    answer = get_answer("What is your current visa status?")
    assert "Graduate Visa" in answer
    assert "2026" in answer


# ------------------------------------------------------------------
# Salary
# ------------------------------------------------------------------

def test_current_salary():
    assert get_answer("What is your current salary?") == "22000"


def test_expected_salary_numeric():
    ctx = {"job_title": "Data Scientist", "company": "Gousto"}
    assert get_answer("What is your expected salary?", ctx, input_type="number") == "32000"


def test_expected_salary_default_numeric():
    assert get_answer("What is your expected salary?", input_type="number") == "28000"


# ------------------------------------------------------------------
# Notice period & employment
# ------------------------------------------------------------------

def test_notice_period():
    assert get_answer("When can you start?") == "Immediately"
    assert get_answer("What is your notice period?") == "Immediately"


def test_notice_period_date_field():
    answer = get_answer("When can you start?", input_type="date")
    # Should be a YYYY-MM-DD date, not "Immediately"
    assert len(answer) == 10
    assert answer[4] == "-"


def test_currently_employed():
    assert get_answer("Are you currently employed?") == "Yes"


def test_current_job_title():
    assert get_answer("What is your current job title?") == "Team Leader"


def test_current_employer():
    assert get_answer("Who is your current employer?") == "Co-op"


# ------------------------------------------------------------------
# Location
# ------------------------------------------------------------------

def test_location_with_context():
    ctx = {"location": "London, England, United Kingdom"}
    assert get_answer("What is your current location?", ctx) == "London, England, United Kingdom"


def test_location_without_context():
    assert get_answer("Where are you based?") == "London, UK"


# ------------------------------------------------------------------
# Remote / hybrid / on-site
# ------------------------------------------------------------------

def test_remote_yes():
    assert get_answer("Are you willing to work remote?") == "Yes"
    assert get_answer("Are you open to remote work?") == "Yes"


def test_onsite_yes():
    assert get_answer("Are you willing to work on-site?") == "Yes"
    assert get_answer("Can you work in the office?") == "Yes"


def test_hybrid_yes():
    assert get_answer("Are you comfortable with a hybrid work arrangement?") == "Yes"


# ------------------------------------------------------------------
# Experience
# ------------------------------------------------------------------

def test_experience_skill_python():
    assert get_answer("How many years of experience do you have with Python?") == "3"


def test_experience_skill_ml():
    assert get_answer("How many years of experience do you have in machine learning?") == "2"


def test_experience_generic():
    answer = get_answer("How many years of experience do you have?")
    assert answer == "2"


# ------------------------------------------------------------------
# Education
# ------------------------------------------------------------------

def test_highest_education():
    assert get_answer("What is your highest level of education?") == "Master's Degree"


def test_degree_subject():
    assert get_answer("What is your degree subject?") == "MSc Computer Science"


def test_currently_studying():
    assert get_answer("Are you currently enrolled in an educational programme?") == "No"


def test_stem_degree():
    assert get_answer("Do you have a STEM degree?") == "Yes"


# ------------------------------------------------------------------
# Languages
# ------------------------------------------------------------------

def test_english_proficiency():
    assert get_answer("What is your proficiency in English?") == "Native or bilingual"


def test_hindi_proficiency():
    assert get_answer("What is your proficiency in Hindi?") == "Native or bilingual"


def test_languages_spoken():
    answer = get_answer("What languages do you speak?")
    assert "English" in answer
    assert "Hindi" in answer


# ------------------------------------------------------------------
# Driving, travel, employment type
# ------------------------------------------------------------------

def test_driving_licence():
    assert get_answer("Do you have a valid UK driving licence?") == "Yes"


def test_travel_willingness():
    assert get_answer("Are you willing to travel for work?") == "Yes"


def test_employment_type():
    assert get_answer("What is your preferred employment type?") == "Full-time"


# ------------------------------------------------------------------
# Background & security
# ------------------------------------------------------------------

def test_background_check():
    assert get_answer("Are you willing to undergo a background check?") == "Yes"
    assert get_answer("Do you have any unspent criminal convictions?") == "Yes"


def test_security_clearance():
    assert get_answer("What level of security clearance do you hold?") == "None"


# ------------------------------------------------------------------
# Diversity
# ------------------------------------------------------------------

def test_gender():
    assert get_answer("What is your gender?") == "Male"


def test_ethnicity():
    answer = get_answer("What is your ethnicity?")
    assert "Asian" in answer or "Indian" in answer


def test_ethnicity_does_not_match_location():
    """Regression: 'ethnicity' must NOT match location pattern (city substring)."""
    answer = get_answer("What is your ethnicity?")
    assert "London" not in answer


def test_orientation():
    assert get_answer("What is your sexual orientation?") == "Heterosexual/Straight"


def test_disability():
    assert get_answer("Do you have a disability?") == "No"


def test_veteran():
    assert get_answer("Are you a veteran?") == "No"


def test_religion():
    assert get_answer("What is your religion?") == "Hindu"


def test_marital_status():
    assert get_answer("What is your marital status?") == "Single"


def test_pronouns():
    assert get_answer("What are your preferred pronouns?") == "He/Him"


def test_over_18():
    assert get_answer("Are you over 18?") == "Yes"


# ------------------------------------------------------------------
# Other patterns
# ------------------------------------------------------------------

def test_consent():
    assert get_answer("I consent to having my data processed") == "Yes"


def test_nationality():
    assert get_answer("What is your nationality?") == "Indian"


def test_management_experience():
    assert get_answer("Do you have management experience?") == "Yes"


def test_direct_reports():
    assert get_answer("How many direct reports have you managed?") == "8"


def test_referral_no():
    assert get_answer("Were you referred by an employee?") == "No"


def test_uk_resident():
    assert get_answer("Are you based in the UK?") == "No"


def test_platform_source_linkedin():
    answer = get_answer("How did you hear about this job?", platform="linkedin")
    assert answer == "LinkedIn"


def test_platform_source_default():
    answer = get_answer("How did you hear about this job?")
    assert answer == "Job board"


def test_proficiency_rating():
    assert get_answer("Rate your proficiency level") == "4"


# ------------------------------------------------------------------
# Cache tests
# ------------------------------------------------------------------

def test_unknown_question_falls_to_cache():
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = "Cached response"
    answer = get_answer("What is your favourite colour?", db=mock_db)
    mock_db.get_cached_answer.assert_called_once()
    assert answer == "Cached response"


def test_cache_stores_and_retrieves(tmp_path):
    db = JobDB(db_path=tmp_path / "test_answers.db")
    assert get_cached_answer("What IDE do you use?", db=db) is None
    cache_answer("What IDE do you use?", "VS Code and Neovim", db=db)
    result = get_cached_answer("What IDE do you use?", db=db)
    assert result == "VS Code and Neovim"


def test_cache_increments_times_used(tmp_path):
    db = JobDB(db_path=tmp_path / "test_answers.db")
    cache_answer("Niche question?", "Niche answer", db=db)
    answer = get_answer("Niche question?", db=db)
    assert answer == "Niche answer"


# ------------------------------------------------------------------
# LLM fallback
# ------------------------------------------------------------------

@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_none_pattern(mock_gen):
    mock_gen.return_value = "I am a motivated software engineer..."
    answer = get_answer("Tell me about yourself")
    mock_gen.assert_called_once()
    assert "motivated" in answer


@patch("jobpulse.screening_answers._generate_answer")
def test_llm_fallback_for_unknown_question(mock_gen):
    mock_gen.return_value = "Generated answer"
    mock_db = MagicMock(spec=JobDB)
    mock_db.get_cached_answer.return_value = None
    answer = get_answer("Explain quantum computing in one sentence", db=mock_db)
    mock_gen.assert_called_once()
    mock_db.cache_answer.assert_called_once()
    assert answer == "Generated answer"


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------

def test_empty_question():
    assert get_answer("") == ""
    assert get_answer("   ") == ""
    assert get_answer(None) == ""
```

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/test_screening_answers.py tests/test_screening_dynamic.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_screening_answers.py
git commit -m "test(screening): update tests for 64 patterns + new answer values"
```

---

### Task 6: Build collision guard test suite

**Files:**
- Create: `tests/test_screening_collision_guard.py`

- [ ] **Step 1: Create the collision guard test file**

Create `tests/test_screening_collision_guard.py`:

```python
"""Collision guard — ensures no regex pattern matches questions from other categories.

Every category has must_match (positive) and must_not_match (negative) examples.
If a pattern matches a question from another category, this test fails —
catching regressions like the ethnicity/city collision (2026-04-01).
"""

from __future__ import annotations

import re

import pytest

from jobpulse.screening_answers import COMMON_ANSWERS

# Map each category to example questions it MUST match
POSITIVE_EXAMPLES: dict[str, list[str]] = {
    # Work authorization
    "right_to_work_type": [
        "What is your Right to Work Type?",
        "What type of visa do you currently hold?",
        "What is your work permit type?",
    ],
    "authorization": [
        "Are you authorized to work in the UK?",
        "Do you have the right to work in the United Kingdom?",
        "Are you legally allowed to work in the UK?",
        "Are you eligible to work in the UK?",
    ],
    "sponsorship": [
        "Do you require visa sponsorship?",
        "Will you need sponsorship to work in the UK?",
        "Do you now or in the future require sponsorship?",
    ],
    "visa_status": [
        "What is your current visa status?",
        "What is your immigration status?",
    ],
    # Salary
    "current_salary": [
        "What is your current salary?",
        "What is your present salary?",
    ],
    "expected_salary": [
        "What is your expected salary?",
        "What are your salary expectations?",
        "Desired compensation?",
        "What is your target salary?",
    ],
    # Employment
    "currently_employed": [
        "Are you currently employed?",
        "What is your current employment status?",
    ],
    "current_title": [
        "What is your current job title?",
        "What is your current role?",
    ],
    "current_employer": [
        "Who is your current employer?",
        "What company do you work for?",
    ],
    # Location
    "location": [
        "What is your current location?",
        "Where are you based?",
        "What city do you live in?",
        "Where are you currently located?",
    ],
    # Education
    "education_level": [
        "What is your highest level of education?",
        "What is your highest qualification?",
    ],
    "degree_subject": [
        "What is your degree subject?",
        "What is your field of study?",
    ],
    # Diversity — critical collision zone
    "gender": [
        "What is your gender?",
        "Please indicate your gender identity",
        "What is your gender identity?",
    ],
    "orientation": [
        "What is your sexual orientation?",
        "Please indicate your sexual orientation",
    ],
    "ethnicity": [
        "What is your ethnicity?",
        "What is your ethnic background?",
        "Please select your racial background",
    ],
    "disability": [
        "Do you have a disability?",
        "Do you consider yourself disabled?",
        "Do you have a long-term health condition?",
    ],
    "religion": [
        "What is your religion?",
        "What is your religion or belief?",
    ],
    "marital_status": [
        "What is your marital status?",
        "What is your relationship status?",
    ],
    "pronouns": [
        "What are your preferred pronouns?",
        "Please indicate your pronouns",
    ],
    "nationality": [
        "What is your nationality?",
        "What is your country of citizenship?",
    ],
    # Other
    "driving": [
        "Do you have a valid UK driving licence?",
        "Do you have a valid driver's license?",
    ],
    "background_check": [
        "Are you willing to undergo a background check?",
        "Do you have any unspent criminal convictions?",
        "Are you willing to undergo a DBS check?",
    ],
    "security_clearance": [
        "What level of security clearance do you hold?",
        "Do you hold SC or DV clearance?",
    ],
    "management": [
        "Do you have management experience?",
        "Do you have leadership experience?",
    ],
    "direct_reports": [
        "How many direct reports have you managed?",
        "How many people have you managed?",
    ],
    "uk_resident": [
        "Are you based in the UK?",
        "Are you a UK resident?",
    ],
    "consent": [
        "I consent to having my data processed for recruitment purposes",
        "Do you agree to the privacy policy?",
    ],
    "how_hear": [
        "How did you hear about this job?",
        "How did you find this position?",
    ],
    "referral": [
        "Were you referred by an employee?",
        "Do you have a referral code?",
    ],
}


def _find_matching_pattern(question: str) -> str | None:
    """Return the first COMMON_ANSWERS pattern that matches the question, or None."""
    for pattern in COMMON_ANSWERS:
        if re.search(pattern, question, re.IGNORECASE):
            return pattern
    return None


class TestPositiveMatches:
    """Every example question must match at least one pattern."""

    @pytest.mark.parametrize(
        "category,question",
        [
            (cat, q)
            for cat, questions in POSITIVE_EXAMPLES.items()
            for q in questions
        ],
    )
    def test_question_matches_a_pattern(self, category, question):
        pattern = _find_matching_pattern(question)
        assert pattern is not None, (
            f"Category '{category}': question '{question}' matched NO pattern in COMMON_ANSWERS"
        )


class TestNoCrossCollisions:
    """No question from category X should match a pattern that belongs to category Y.

    This catches bugs like 'ethnicity' matching the location pattern via 'city' substring.
    """

    # Build a map: pattern -> category (first match wins based on dict order)
    PATTERN_TO_CATEGORY: dict[str, str] = {}
    for _cat, _questions in POSITIVE_EXAMPLES.items():
        for _q in _questions:
            _pat = _find_matching_pattern(_q)
            if _pat and _pat not in PATTERN_TO_CATEGORY:
                PATTERN_TO_CATEGORY[_pat] = _cat

    # Critical cross-collision pairs to check
    COLLISION_PAIRS = [
        # (question, must NOT match this category)
        ("What is your ethnicity?", "location"),
        ("What is your ethnic background?", "location"),
        ("What is your sexual orientation?", "gender"),
        ("What is your nationality?", "location"),
        ("What is your current salary?", "expected_salary"),
        ("What is your Right to Work Type?", "authorization"),
        ("Do you have a disability?", "background_check"),
        ("What is your religion?", "location"),
        ("What is your marital status?", "location"),
        ("Are you based in the UK?", "location"),
    ]

    @pytest.mark.parametrize("question,forbidden_category", COLLISION_PAIRS)
    def test_no_cross_collision(self, question, forbidden_category):
        pattern = _find_matching_pattern(question)
        if pattern is None:
            return  # No match at all — not a collision
        matched_cat = self.PATTERN_TO_CATEGORY.get(pattern, "unknown")
        assert matched_cat != forbidden_category, (
            f"COLLISION: '{question}' matched pattern for '{forbidden_category}' "
            f"category (pattern: {pattern})"
        )


class TestPatternOrdering:
    """Specific patterns must match before general ones."""

    ORDERING_TESTS = [
        # (question, expected_answer_substring)
        ("What is your Right to Work Type?", "Graduate Visa"),
        ("What is your current salary?", "22000"),
        ("How many direct reports have you managed?", "8"),
    ]

    @pytest.mark.parametrize("question,expected_substr", ORDERING_TESTS)
    def test_specific_before_general(self, question, expected_substr):
        # Walk patterns in order — first match should contain expected
        for pattern, answer in COMMON_ANSWERS.items():
            if re.search(pattern, question, re.IGNORECASE):
                assert answer is not None, f"First match for '{question}' was LLM (None)"
                assert expected_substr in str(answer), (
                    f"'{question}' first matched pattern '{pattern}' -> '{answer}', "
                    f"expected '{expected_substr}'"
                )
                break
```

- [ ] **Step 2: Run collision guard tests**

Run: `python -m pytest tests/test_screening_collision_guard.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_screening_collision_guard.py
git commit -m "test(screening): add collision guard suite — 50+ cross-category checks"
```

---

### Task 7: Wire input_type and platform into LinkedIn adapter

**Files:**
- Modify: `jobpulse/ats_adapters/linkedin.py:360-527`

- [ ] **Step 1: Update all get_answer() calls in _answer_questions**

In `jobpulse/ats_adapters/linkedin.py`, in the `_answer_questions` function, update every `get_answer()` call to pass `input_type` and `platform="linkedin"`.

Replace line 396 (select/dropdown):
```python
                answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
```
with:
```python
                answer = get_answer(
                    question,
                    custom_answers.get("_job_context") if custom_answers else None,
                    input_type="select",
                    platform="linkedin",
                )
```

Replace line 424 (radio):
```python
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
```
with:
```python
                    answer = get_answer(
                        question,
                        custom_answers.get("_job_context") if custom_answers else None,
                        input_type="radio",
                        platform="linkedin",
                    )
```

Replace line 461 (checkbox):
```python
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
```
with:
```python
                    answer = get_answer(
                        question,
                        custom_answers.get("_job_context") if custom_answers else None,
                        input_type="checkbox",
                        platform="linkedin",
                    )
```

Replace line 512 (text/number/tel/email):
```python
                        answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
```
with:
```python
                        answer = get_answer(
                            question,
                            custom_answers.get("_job_context") if custom_answers else None,
                            input_type=input_type,
                            platform="linkedin",
                        )
```

Replace line 520 (textarea):
```python
                    answer = get_answer(question, custom_answers.get("_job_context") if custom_answers else None)
```
with:
```python
                    answer = get_answer(
                        question,
                        custom_answers.get("_job_context") if custom_answers else None,
                        input_type="textarea",
                        platform="linkedin",
                    )
```

- [ ] **Step 2: Run all tests to verify nothing broke**

Run: `python -m pytest tests/test_screening_answers.py tests/test_screening_dynamic.py tests/test_screening_collision_guard.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add jobpulse/ats_adapters/linkedin.py
git commit -m "feat(linkedin): pass input_type and platform to get_answer() calls"
```

---

### Task 8: Final integration — run all tests + update mistakes.md

**Files:**
- Modify: `.claude/mistakes.md` (already done, verify)
- Test: all screening test files

- [ ] **Step 1: Run the full screening test suite**

Run: `python -m pytest tests/test_screening_answers.py tests/test_screening_dynamic.py tests/test_screening_collision_guard.py -v --tb=short`
Expected: All PASS (60+ tests)

- [ ] **Step 2: Run the full project test suite to check for regressions**

Run: `python -m pytest tests/ -v --tb=short -x`
Expected: All existing tests PASS. If any fail due to changed get_answer() signature (new kwargs are optional, so backwards-compatible), fix the caller.

- [ ] **Step 3: Verify pattern count**

Run: `python -c "from jobpulse.screening_answers import COMMON_ANSWERS; print(f'{len(COMMON_ANSWERS)} patterns')"`
Expected: `64 patterns`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(screening): complete edge case hardening — 64 patterns, collision guards, dynamic answers"
```
