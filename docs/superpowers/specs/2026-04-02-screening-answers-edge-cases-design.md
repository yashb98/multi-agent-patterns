# Screening Answers Edge Case Hardening — Design Spec

> **Goal:** Expand `screening_answers.py` from 19 regex patterns to ~60, add skill-specific experience lookup, dynamic "previously applied" check, role-aware salary, platform-aware source tracking, input-type awareness, and a collision guard test suite.

> **Architecture:** Hybrid approach — keep existing 3-tier system (regex -> cache -> LLM) but expand patterns, add dynamic answer functions, input-type hints, and automated collision tests.

---

## 1. Problem Statement

The current `screening_answers.py` has 19 regex patterns covering ~5% of the 450+ question phrasings found across LinkedIn Easy Apply, Greenhouse, Lever, Indeed, Workday, and generic UK ATS platforms. This causes:

- **Unnecessary LLM calls** (~$0.002 each) for questions that have deterministic answers
- **Format mismatches** (e.g., "Immediately" in a date picker field)
- **Regex collisions** (e.g., "ethnicity" matching "city" pattern)
- **Missing categories** (education, languages, DBS, driving, travel, employment type — all unanswered)
- **Static answers** where dynamic ones are needed (experience years, previously applied, salary by role)

## 2. Components

### 2.1 Expanded COMMON_ANSWERS (~60 patterns)

The existing `COMMON_ANSWERS` dict expands from 19 to ~60 patterns across 30 categories. Patterns are grouped with specific patterns BEFORE general ones (ordering rule from mistakes.md).

**Special values:**
- `"JOB_LOCATION"` — resolved from `job_context["location"]`, fallback `"London, UK"`
- `"SKILL_EXPERIENCE"` — triggers skill-specific year lookup
- `"PREVIOUSLY_APPLIED"` — triggers JobDB query
- `"PLATFORM_SOURCE"` — returns platform name (LinkedIn, Job board, etc.)
- `"ROLE_SALARY"` — returns role-aware salary expectation
- `None` — triggers LLM generation (open-ended questions)

### 2.2 SKILL_EXPERIENCE Lookup

New dict mapping skill names to years of experience:

```python
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
    "computer vision": 2, "cv": 2,
    "reinforcement learning": 2, "rl": 2,
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
```

**Extraction logic:** When question matches experience pattern, extract skill name from the question text (e.g., "How many years of experience do you have with **Python**?" -> extract "python" -> look up -> return `3`). Default: `2` for unknown skills.

### 2.3 ROLE_SALARY Lookup

Role-aware salary expectations:

```python
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
```

**Resolution:** When salary expectation question matched, check `job_context["job_title"]` against `ROLE_SALARY` keys (case-insensitive substring match). Return matching salary as plain integer for numeric fields. For text fields, return formatted range (e.g., `"GBP27,000-GBP30,000"` for default, `"GBP30,000-GBP35,000"` for data scientist).

### 2.4 Dynamic "Previously Applied" Check

When question matches `previously.*applied|applied.*before|applied.*past`:

```python
def _check_previously_applied(question: str, job_context: dict | None, db: JobDB) -> str:
    company = (job_context or {}).get("company", "")
    if not company:
        return "No"
    count = db.count_applications_for_company(company)
    return "Yes" if count > 0 else "No"
```

### 2.5 Platform-Aware Source Tracking

`get_answer()` accepts optional `platform` parameter:

```python
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

### 2.6 Input-Type Awareness

`get_answer()` accepts optional `input_type` parameter. Specific patterns adapt:

| Question | `input_type="text"` | `input_type="number"` | `input_type="date"` |
|----------|---------------------|----------------------|---------------------|
| Notice period / start date | `Immediately` | N/A | `YYYY-MM-DD` (today + 14 days) |
| Expected salary | `GBP27,000-GBP30,000` | `28000` (plain int) | N/A |
| Current salary | `GBP22,000` | `22000` | N/A |
| Years experience | `2+ years (MSc + industry)` | `2` (plain int) | N/A |

### 2.7 Collision Guard Test Suite

New test file: `tests/test_screening_collision_guard.py`

For every pattern in `COMMON_ANSWERS`, maintain a list of "must NOT match" example questions from OTHER categories. The test iterates all patterns against all negative examples and asserts zero cross-matches.

Example:
```python
COLLISION_TESTS = {
    "location": {
        "must_match": ["What is your current location?", "Where are you based?"],
        "must_not_match": ["What is your ethnicity?", "What is your nationality?", "Which city do you work in?"],
    },
    "gender": {
        "must_match": ["What is your gender?", "Please indicate your gender"],
        "must_not_match": ["What is your sexual orientation?", "Gender pay gap policy"],
    },
    ...
}
```

This runs as part of the normal test suite and catches any future regex regressions.

## 3. Full Pattern Map (60 patterns)

### Work Authorization & Visa (6 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 1 | Right to work type | `right.*work.*type\|work.*type\|work.*permit\|work.*authorization.*type\|visa.*type\|type.*visa.*hold` | `Graduate Visa` |
| 2 | Work authorization | `authorized.*work\|right to work\|legally.*work\|eligible.*work\|unrestricted.*right` | `Yes` |
| 3 | Sponsorship | `require.*sponsor\|visa.*sponsor\|sponsorship\|need.*sponsor` | `No` |
| 4 | Visa status | `visa.*status\|immigration.*status\|current.*visa\|visa.*expire` | `Student Visa; converting to Graduate Visa from 9 May 2026 (valid 2 years)` |
| 5 | British citizen | `british.*citizen\|eu.*national\|ilr\|indefinite.*leave\|settled.*status` | `No` |
| 6 | UK work restrictions | `subject.*immigration.*restrict\|work.*without.*restrict` | `No` (has restrictions — Student Visa) |

### Salary & Compensation (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 7 | Current salary | `current.*salary\|salary.*current\|present.*salary\|current.*compensation\|current.*base` | `22000` |
| 8 | Expected salary | `salary.*expect\|expected.*salary\|desired.*compensation\|pay.*expect\|minimum.*salary\|salary.*range\|target.*salary\|compensation.*require\|salary.*requirement` | `ROLE_SALARY` — dynamic |
| 9 | Daily/hourly rate | `daily.*rate\|hourly.*rate\|day.*rate` | `150` (daily), `20` (hourly) |

### Notice Period & Employment (5 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 10 | Notice period / start date | `notice.*period\|when.*start\|available.*start\|start.*date\|earliest.*start\|how.*soon.*start\|immediate.*start` | `Immediately` / date+14d |
| 11 | Currently employed | `currently.*employ\|current.*employment\|employment.*status\|are.*you.*employ` | `Yes` |
| 12 | Current job title | `current.*job.*title\|current.*role\|current.*position\|present.*role` | `Team Leader` |
| 13 | Current employer | `current.*employer\|who.*work.*for\|present.*employer\|company.*work.*for` | `Co-op` |
| 14 | Reason for leaving | `reason.*leaving\|why.*leaving\|why.*seeking\|why.*new.*position` | `None` -> LLM |

### Location & Commute (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 15 | Current location | `current.*location\|where.*located\|your.*location\|what.*city.*live\|which.*city\|where.*you.*based\|based.*in\|residing\|country.*resid` | `JOB_LOCATION` |
| 16 | Willing to relocate | `willing.*relocate\|open.*relocation\|relocate.*within\|relocate.*to` | `Yes, within the UK` |
| 17 | Commute | `commut.*to\|commuting.*distance\|travel.*to.*office` | `Yes` |

### Remote / Hybrid / On-site (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 18 | Remote work | `willing.*remote\|work.*remote\|open.*remote\|comfortable.*remote\|fully.*remote` | `Yes` |
| 19 | On-site / office | `willing.*office\|work.*on.?site\|in.?person\|work.*in.*office\|in.*the.*office` | `Yes` |
| 20 | Hybrid | `hybrid.*work\|hybrid.*arrange\|days.*per.*week.*on.?site\|days.*in.*office\|comfortable.*hybrid` | `Yes` |

### Experience (2 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 21 | Years of experience (generic) | `years.*experience\|experience.*years\|how.*many.*years\|total.*years` | `SKILL_EXPERIENCE` — dynamic |
| 22 | Do you have experience with X | `experience.*with\|proficient.*in\|familiar.*with\|hands.?on.*experience\|worked.*with` | `SKILL_EXPERIENCE` — Yes/No + years |

### Education (4 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 23 | Highest education level | `highest.*education\|level.*education\|highest.*qualification\|completed.*education\|highest.*degree` | `Master's Degree` |
| 24 | Degree subject / type | `degree.*subject\|field.*study\|what.*degree\|degree.*type\|what.*major\|degree.*classification` | `MSc Computer Science` |
| 25 | Currently studying | `currently.*study\|currently.*enrolled\|enrolled.*education` | `No` |
| 26 | STEM / CS degree | `stem.*degree\|computer.*science.*degree\|related.*field\|relevant.*degree` | `Yes` |

### Languages (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 27 | English proficiency | `proficiency.*english\|fluent.*english\|english.*proficiency\|level.*english` | `Native or bilingual` |
| 28 | Hindi proficiency | `proficiency.*hindi\|fluent.*hindi\|hindi.*proficiency` | `Native or bilingual` |
| 29 | Languages spoken | `languages.*speak\|what.*languages\|language.*skills\|other.*language\|do.*you.*speak` | `English (Native), Hindi (Native)` |

### Driving, Travel & Availability (4 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 30 | Driving licence | `driv.*licen[cs]e\|driver.*licen\|valid.*driv\|clean.*driv` | `Yes` |
| 31 | Travel willingness | `willing.*travel\|comfortable.*travel\|travel.*required\|percentage.*travel` | `Yes` |
| 32 | Shift / weekend / overtime | `shift.*work\|work.*weekend\|night.*shift\|on.?call\|work.*evening\|bank.*holiday\|overtime\|flexible.*hour` | `Yes` |
| 33 | Employment type | `permanent.*contract\|preferred.*employment\|full.?time\|part.?time\|employment.*type\|fixed.?term\|looking.*permanent` | `Full-time` |

### Background, Security & Legal (4 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 34 | Background / DBS check | `background.*check\|dbs.*check\|criminal.*record\|unspent.*conviction\|willing.*undergo\|pre.?employment.*screen` | `Yes` |
| 35 | Security clearance level | `security.*clearance\|hold.*clearance\|sc.*clearance\|dv.*clearance\|bpss\|level.*clearance` | `None` |
| 36 | Non-compete / conflicts | `non.?compete\|restrictive.*covenant\|conflict.*interest\|gardening.*leave` | `No` |
| 37 | UK resident | `\bbased.*in.*uk\b\|resident.*uk\|uk.*resid\|live.*in.*uk\|reside.*in.*united.*kingdom` | `No` |

### Company & Application History (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 38 | Worked for company | `currently.*work.*for\|ever.*work.*for\|employed.*by\|worked.*for\|former.*employee` | `No` |
| 39 | Previously applied | `previously.*applied\|applied.*before\|applied.*past\|applied.*position` | `PREVIOUSLY_APPLIED` — dynamic |
| 40 | How did you hear | `how.*hear.*about\|how.*find.*this\|where.*see.*vacanc\|source.*application` | `PLATFORM_SOURCE` — dynamic |

### Referral (1 pattern)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 41 | Referral | `referred.*employee\|referral.*code\|referral.*name\|employee.*refer\|were.*you.*referred` | `No` |

### Diversity & Equality Monitoring (10 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 42 | Gender | `gender.*identify\|what.*gender\|indicate.*gender\|what.*your.*sex\b` | `Male` |
| 43 | Sexual orientation | `sexual.*orientation\|what.*orientation\|indicate.*orientation` | `Heterosexual/Straight` |
| 44 | Ethnicity | `ethnicity\|ethnic.*background\|racial\|indicate.*ethnicity\|race.*ethnic` | `Asian or Asian British - Indian` |
| 45 | Disability | `disability\|disabled\|long.?term.*health\|equality.*act.*2010\|impairment.*health` | `No` |
| 46 | Veteran | `veteran\|military` | `No` |
| 47 | Religion | `religion\|belief\b\|faith\b\|spiritual` | `Hindu` (if option exists), else `Prefer not to say` |
| 48 | Marital status | `marital.*status\|civil.*status\|relationship.*status` | `Single` |
| 49 | Pronouns | `what.*pronoun\|preferred.*pronoun\|indicate.*pronoun` | `He/Him` |
| 50 | Age group | `age.*group\|what.*your.*age\|date.*birth` | `25-29` or `20-29` (dropdown), DOB for date fields |
| 51 | Over 18 | `over.*18\|are.*you.*18\|above.*18` | `Yes` |

### Caring & Adjustments (2 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 52 | Caring responsibilities | `caring.*responsib\|childcare\|eldercare\|dependant` | `No` |
| 53 | Reasonable adjustments | `reasonable.*adjust\|access.*require\|workplace.*adjust\|special.*accommod\|support.*application\|assistive.*tech` | `No` |

### Consent & Confirmations (1 pattern)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 54 | GDPR / consent / confirm | `consent.*data\|privacy.*policy\|gdpr\|data.*process\|retain.*future\|agree.*terms\|information.*accurate\|confirm.*read` | `Yes` |

### Nationality & Identity (2 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 55 | Nationality / citizenship | `what.*nationality\|country.*citizen\|country.*birth` | `Indian` |
| 56 | Title / salutation | `\btitle\b.*mr\|salutation\|honorific` | `Mr` |

### Team & Management (2 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 57 | Management experience | `managing.*team\|line.*management\|managed.*people\|leadership.*experience\|management.*experience` | `Yes` |
| 58 | Direct reports count | `direct.*report\|how.*many.*managed\|people.*managed\|team.*size\|largest.*team` | `8` (managed), `3` (direct reports) |

### Portfolio & Links (1 pattern)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 59 | Portfolio / GitHub / website | `portfolio.*url\|github.*url\|github.*profile\|personal.*website\|website.*url\|kaggle\|link.*to.*work` | From PROFILE dict |

### Open-Ended / LLM (3 patterns)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 60 | Why apply / motivation | `why.*apply\|why.*interest\|why.*company\|motivation\|what.*excites` | `None` -> LLM |
| 61 | About yourself | `tell.*about.*yourself\|describe.*yourself\|brief.*summary.*fit` | `None` -> LLM |
| 62 | Additional info | `anything.*else\|additional.*information\|further.*comment\|is.*there.*anything` | `None` -> LLM |

### Proficiency Ratings (1 pattern)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 63 | Self-rating scale | `rate.*proficiency\|rate.*your\|proficiency.*level\|skill.*level\|how.*qualified\|scale.*1.*5` | `4` (out of 5) |

### Certifications (1 pattern)
| # | Category | Pattern | Answer |
|---|----------|---------|--------|
| 64 | Certifications | `hold.*certification\|professional.*cert\|relevant.*cert\|aws.*cert\|certified` | `None` -> LLM |

## 4. Updated `get_answer()` Signature

```python
def get_answer(
    question: str,
    job_context: dict | None = None,
    *,
    db: JobDB | None = None,
    input_type: str | None = None,
    platform: str | None = None,
) -> str:
```

New parameters:
- `input_type` — `"text"`, `"number"`, `"date"`, `"select"`, `"radio"`, `"checkbox"`, `"textarea"`, or `None`
- `platform` — `"linkedin"`, `"greenhouse"`, `"lever"`, `"indeed"`, `"reed"`, or `None`

Resolution order unchanged: regex -> cache -> LLM. But regex matches now resolve special placeholders (`SKILL_EXPERIENCE`, `ROLE_SALARY`, `PREVIOUSLY_APPLIED`, `PLATFORM_SOURCE`, `JOB_LOCATION`) through dedicated resolver functions.

## 5. Collision Guard Test Design

File: `tests/test_screening_collision_guard.py`

```python
CATEGORY_EXAMPLES: dict[str, list[str]] = {
    "location": [
        "What is your current location?",
        "Where are you based?",
        "What city do you live in?",
    ],
    "ethnicity": [
        "What is your ethnicity?",
        "Please select your ethnic background",
        "What is your racial background?",
    ],
    "gender": [
        "What is your gender?",
        "Please indicate your gender identity",
    ],
    "orientation": [
        "What is your sexual orientation?",
    ],
    "disability": [
        "Do you have a disability?",
        "Do you consider yourself disabled?",
    ],
    "salary_current": [
        "What is your current salary?",
    ],
    "salary_expected": [
        "What are your salary expectations?",
    ],
    "authorization": [
        "Are you authorized to work in the UK?",
    ],
    "work_type": [
        "What is your Right to Work Type?",
    ],
    "sponsorship": [
        "Do you require visa sponsorship?",
    ],
    # ... all 30 categories with 3-5 examples each
}
```

**Test logic:**
1. For each category, verify all `must_match` examples hit the correct pattern
2. For each category, verify NO example from any OTHER category matches this pattern
3. Verify pattern ordering — specific patterns must match before general ones
4. Verify no two patterns can both match the same input string

## 6. Files Changed

| File | Change |
|------|--------|
| `jobpulse/screening_answers.py` | Expand COMMON_ANSWERS (19 -> 64), add SKILL_EXPERIENCE dict, add ROLE_SALARY dict, add PLATFORM_SOURCE dict, update `get_answer()` signature with `input_type` and `platform` params, add resolver functions for special placeholders, add `_extract_skill_from_question()`, add `_check_previously_applied()` |
| `jobpulse/ats_adapters/linkedin.py` | Pass `input_type` and `platform="linkedin"` to `get_answer()` calls |
| `jobpulse/ats_adapters/greenhouse.py` | Wire up `get_answer()` for custom screening questions (currently unused) |
| `jobpulse/ats_adapters/lever.py` | Wire up `get_answer()` for custom screening questions (currently unused) |
| `tests/test_screening_answers.py` | Update existing tests for new answers (salary, experience, etc.) |
| `tests/test_screening_collision_guard.py` | NEW — collision guard test suite (~200 test cases) |
| `tests/test_screening_dynamic.py` | NEW — tests for skill lookup, role salary, previously applied, platform source |
| `.claude/mistakes.md` | Already updated with 6 LinkedIn mistakes |

## 7. Edge Cases Handled

### Regex Collision Prevention
- `"ethnicity"` no longer matches location pattern (fixed: `what.*city.*live|which.*city`)
- `"sexual orientation"` won't match gender pattern (`what.*your.*sex\b` uses word boundary)
- `"current salary"` won't match expected salary (specific `current.*salary` ordered before `salary.*expect`)
- `"Right to Work Type"` matches before "Right to Work" (specific-first ordering)
- Collision guard test suite catches future regressions

### Input-Type Adaptation
- Date fields get `YYYY-MM-DD` (today + 14 days) instead of "Immediately"
- Numeric fields get plain integers (no currency symbols, commas, ranges)
- Text fields get human-readable formatted answers
- Select/dropdown matching uses substring + fuzzy logic in the adapter

### Dynamic Answers
- Experience years vary by skill (Python: 3, ML: 2, unknown: 2)
- Salary varies by role (Data Scientist: 32000, Data Analyst: 28000)
- "Previously applied" checks actual DB records
- "How did you hear" returns platform name

### Negation Handling
- "Do you NOT require sponsorship?" — pattern `require.*sponsor` still matches, answer `No` is correct (we don't require it)
- "Are you eligible to work WITHOUT restriction?" — separate pattern returns `No` (Student Visa has restrictions)

### Compound Questions
- "Are you authorized to work in the UK and willing to relocate?" — matches authorization pattern first (most important part), returns `Yes`
- These are rare and imperfect — LLM fallback handles truly compound questions

### Missing/Empty Context
- `job_context` is `None` -> salary defaults to 28000, location defaults to "London, UK"
- `platform` is `None` -> "How did you hear" defaults to "Job board"
- `input_type` is `None` -> returns the default text answer

## 8. What This Does NOT Cover

- **Greenhouse/Lever/Indeed adapter wiring** — these adapters don't call `get_answer()` today. Wiring them is a separate task (noted for future).
- **Multi-checkbox "select all that apply"** — these are handled by the adapter's checkbox logic, not screening_answers.
- **File upload questions** — CV/CL uploads handled by adapter, not screening_answers.
- **Reference details** — name, phone, email for references are too personal for auto-fill. LLM fallback returns generic response.
- **NI number** — too sensitive for auto-fill. LLM fallback.
- **Emergency contact** — typically post-offer. LLM fallback.

## 9. Success Criteria

1. All 64 patterns pass their `must_match` examples
2. Zero cross-category collisions in collision guard tests
3. All existing tests in `test_screening_answers.py` pass (updated for new answers)
4. LinkedIn dry run fills all fields correctly (regression test)
5. LLM fallback calls reduced by ~80% (from ~20/application to ~4)
