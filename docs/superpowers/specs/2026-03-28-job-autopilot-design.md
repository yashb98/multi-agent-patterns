# Job Autopilot Pipeline — Design Spec

> Autonomous job discovery, CV tailoring, and application submission pipeline integrated into JobPulse.

**Status:** Approved
**Date:** 2026-03-28
**Author:** Yash Bishnoi + Claude

---

## 1. Problem Statement

Yash is targeting junior/graduate/intern roles (Data Scientist, ML Engineer, AI Engineer, Data Engineer) across 5 UK job platforms. Currently, job discovery is manual, CV tailoring is manual (via an Overleaf LaTeX prompt), and application tracking lives in his head + LinkedIn. This pipeline automates the entire flow: find jobs, tailor CV to 95%+ ATS, generate cover letters, apply, and track everything in Notion.

## 2. Goals

- **40+ applications/day** across 6 scan windows (7am, 10am, 1pm, 4:30pm, 7pm, 2am)
- **95%+ ATS score** on every submitted CV
- **Zero duplicate applications** — dedup against Notion tracker
- **Tiered approval** — auto-apply 90%+, review 82-89%, skip <82%
- **Full Notion tracking** — 19-column "Job Tracker" Notion database with full status lifecycle
- **Cross-agent integration** — Gmail detects responses, Calendar links interviews, MindGraph extracts company entities

## 3. Architecture

Extends JobPulse (Approach A — monolith). New modules inside `jobpulse/` and `jobpulse/ats_adapters/`. Uses existing dispatcher, Telegram multi-bot, NLP classifier, Notion sync, process trails, and experience memory.

### 3.1 Pipeline Stages

```
Scanner → JD Analyzer → Deduplicator → GitHub Matcher → CV Tailor → Cover Letter → Applicator → Notion Sync
```

### 3.2 New Files

| File | Purpose |
|------|---------|
| `jobpulse/job_scanner.py` | Scrapes 5 platforms for job listings |
| `jobpulse/jd_analyzer.py` | Parses JD into structured `JobListing` |
| `jobpulse/job_deduplicator.py` | Checks against SQLite + Notion to prevent re-applying |
| `jobpulse/github_matcher.py` | Scores GitHub repos against JD, picks top 3-4 |
| `jobpulse/cv_tailor.py` | Modifies LaTeX source using Resume Prompt, compiles PDF, scores ATS |
| `jobpulse/cover_letter_agent.py` | Generates cover letter from JD + tailored CV using template |
| `jobpulse/applicator.py` | Orchestrates submission via ATS adapters or prep packages |
| `jobpulse/job_notion_sync.py` | Syncs all application data to "Job Tracker" Notion database |
| `jobpulse/job_autopilot.py` | Top-level orchestrator — runs the full pipeline per scan window |
| `jobpulse/ats_adapters/__init__.py` | Adapter registry |
| `jobpulse/ats_adapters/base.py` | Base adapter class |
| `jobpulse/ats_adapters/linkedin.py` | LinkedIn Easy Apply adapter |
| `jobpulse/ats_adapters/indeed.py` | Indeed Quick Apply adapter |
| `jobpulse/ats_adapters/greenhouse.py` | Greenhouse form adapter |
| `jobpulse/ats_adapters/lever.py` | Lever form adapter |
| `jobpulse/ats_adapters/workday.py` | Workday multi-step wizard adapter |
| `jobpulse/ats_adapters/generic.py` | Best-effort form detection fallback |
| `jobpulse/models/application_models.py` | Pydantic models for the pipeline |
| `data/job_search_config.json` | Search titles, location, salary, exclusions |
| `data/cv_base.tex` | Base LaTeX CV source (exported from Overleaf) |
| `jobpulse/templates/Resume Prompt.md` | Resume generation prompt (already exists) |
| `jobpulse/templates/Cover letter template.md` | Cover letter format (already exists) |

### 3.3 Modified Files

| File | Change |
|------|--------|
| `jobpulse/command_router.py` | Add 8 new intents (SHOW_JOBS, APPROVE_JOBS, REJECT_JOB, JOB_STATS, SEARCH_CONFIG, PAUSE_JOBS, RESUME_JOBS, JOB_DETAIL) |
| `jobpulse/dispatcher.py` | Wire new intents to job autopilot functions |
| `jobpulse/config.py` | Add new env vars (TELEGRAM_JOBS_BOT_TOKEN, NOTION_APPLICATIONS_DB_ID, REED_API_KEY) |
| `jobpulse/telegram_bots.py` | Register 5th bot (Jobs bot) |
| `jobpulse/multi_bot_listener.py` | Route job intents to Jobs bot |
| `jobpulse/morning_briefing.py` | Include job pipeline summary in daily briefing |
| `jobpulse/weekly_report.py` | Include weekly application metrics |
| `jobpulse/gmail_agent.py` | Detect interview invites/rejections → update application status |
| `scripts/install_cron.py` | Add 6 scan window cron entries |
| `data/intent_examples.json` | Add training examples for 8 new intents |

## 4. Data Models

### 4.1 JobListing

```python
class JobListing(BaseModel):
    job_id: str                    # SHA-256 of URL for dedup
    title: str
    company: str
    platform: Literal["linkedin", "indeed", "reed", "totaljobs", "glassdoor"]
    url: str
    salary_min: float | None = None
    salary_max: float | None = None
    location: str
    remote: bool = False
    seniority: Literal["intern", "graduate", "junior", "mid"] | None = None
    required_skills: list[str] = []
    preferred_skills: list[str] = []
    description_raw: str
    ats_platform: str | None = None  # greenhouse, lever, workday, etc.
    found_at: datetime
    easy_apply: bool = False
```

### 4.2 ApplicationStatus

```python
class ApplicationStatus(str, Enum):
    FOUND = "Found"
    ANALYZING = "Analyzing"
    READY = "Ready"
    PENDING_APPROVAL = "Pending Approval"
    APPLIED = "Applied"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"
    WITHDRAWN = "Withdrawn"
    SKIPPED = "Skipped"
```

### 4.3 ApplicationRecord

```python
class ApplicationRecord(BaseModel):
    job: JobListing
    status: ApplicationStatus = ApplicationStatus.FOUND
    ats_score: float = 0.0
    match_tier: Literal["auto", "review", "skip"] = "skip"
    matched_projects: list[str] = []       # GitHub repo names
    cv_path: Path | None = None            # compiled tailored PDF
    cover_letter_path: Path | None = None
    applied_at: datetime | None = None
    notion_page_id: str | None = None
    follow_up_date: date | None = None
    custom_answers: dict[str, str] = {}    # question → answer cache
```

### 4.4 ATSScore

```python
class ATSScore(BaseModel):
    total: float                  # 0-100
    keyword_score: float          # 0-70 (matched/total keywords * 70)
    section_score: float          # 0-20 (required sections present)
    format_score: float           # 0-10 (parseable, no tables/images)
    missing_keywords: list[str]
    matched_keywords: list[str]
    passed: bool                  # total >= 95
```

### 4.5 SearchConfig

```python
class SearchConfig(BaseModel):
    titles: list[str]
    location: str = "United Kingdom"
    include_remote: bool = True
    salary_min: float = 27000
    salary_max: float | None = None
    exclude_companies: list[str] = []
    exclude_keywords: list[str] = ["senior", "lead", "principal", "staff", "10+ years"]
```

## 5. Stage Details

### 5.1 Job Scanner (`jobpulse/job_scanner.py`)

**5 platform scrapers:**

| Platform | Method | Auth |
|----------|--------|------|
| LinkedIn | Playwright with saved browser session | Cookies from `data/linkedin_session/` |
| Indeed | httpx — public search API (no auth for search results) | None |
| Reed | httpx — official Reed API (free key) | `REED_API_KEY` basic auth |
| TotalJobs | httpx — HTML scraping of search results | None |
| Glassdoor | Playwright with saved browser session | Cookies from `data/glassdoor_session/` |

**Scan schedule (6 windows):**

| Time | Platforms |
|------|-----------|
| 7:00 AM | All 5 |
| 10:00 AM | LinkedIn + Indeed + Reed |
| 1:00 PM | All 5 |
| 4:30 PM | LinkedIn + Indeed + Reed |
| 7:00 PM | All 5 |
| 2:00 AM | Glassdoor + TotalJobs |

**Anti-detection:**
- Randomized delays: 2-8 seconds between requests
- Rotating user agents for HTTP scrapers
- Playwright uses real browser profiles (not headless for LinkedIn/Glassdoor)
- Respect rate limits: max 50 requests/platform/scan

**Output:** List of `JobListing` objects (raw, not yet analyzed).

**Search config:** Loaded from `data/job_search_config.json`. Updatable via Telegram commands.

### 5.2 JD Analyzer (`jobpulse/jd_analyzer.py`)

Parses raw JD text into structured `JobListing` fields.

**Two-tier extraction:**
1. **Rule-based** — regex for salary (e.g., "27,000-35,000", "27K-35K"), location, seniority keywords, easy apply detection
2. **LLM extraction** — gpt-4o-mini with forced tool_use to extract: required_skills, preferred_skills, seniority, remote status, ATS platform detection

**ATS platform detection:** URL pattern matching:
- `greenhouse.io` or `boards.greenhouse.io` → Greenhouse
- `lever.co` or `jobs.lever.co` → Lever
- `myworkdayjobs.com` → Workday
- `smartrecruiters.com` → SmartRecruiters
- `icims.com` → iCIMS
- LinkedIn `/easy/apply` → Easy Apply
- Indeed quick apply button detected → Easy Apply

**Auto-populates Layer 3 of the Resume Prompt:**
```
EXTRACTED:
  LOCATION       : <from JD>
  ROLE_TITLE     : <from JD>
  YEARS_EXP      : <from JD, default "2+">
  INDUSTRY       : <inferred from company + JD>
  SUB_CONTEXT    : <from JD>
  SKILLS_LIST    : [ordered as in JD, 12-15 max]
  SOFT_SKILLS    : [from JD]
  EXTENDED_SKILLS: [matched against Resume Prompt Layer 9]
```

### 5.3 Deduplicator (`jobpulse/job_deduplicator.py`)

Prevents applying to the same job twice or the same company for the same role.

**Dedup checks (in order):**
1. **Exact URL match** — SHA-256 of URL against `applications.db`
2. **Company + Title fuzzy match** — same company + similar title (word overlap >= 0.8) within 30 days
3. **Notion cross-check** — query "Job Tracker" DB for company + role combination

**Output:** Filtered list of `JobListing` objects that are genuinely new.

### 5.4 GitHub Matcher (`jobpulse/github_matcher.py`)

Scores Yash's GitHub repos against each JD and picks the top 3-4.

**Repo data (cached daily):**
- Fetches all public repos via GitHub API
- For each repo: README content, languages, topics, description
- Extracts tech stack keywords from README (cached in `data/github_repo_cache.json`)

**Scoring per repo per JD:**
```
repo_score = (
    skill_overlap(repo_keywords, jd_required_skills) * 0.5 +
    skill_overlap(repo_keywords, jd_preferred_skills) * 0.3 +
    domain_relevance(repo_description, jd_industry) * 0.2
)
```

**Project reordering:**
- The Resume Prompt has 4 fixed projects (Velox AI, Cloud Sentinel, 90 Days ML, Deep Learning 3D)
- GitHub Matcher ranks these 4 by relevance to the JD
- Top 3-4 are included; most relevant gets maximum bullets per the prompt structure
- Order in the CV follows the prompt's bullet count rules (Project 1: 3 bullets, Project 2: 5 bullets, etc.) — the most relevant project is placed in the Project 2 slot (5 bullets) for maximum exposure

### 5.5 CV Tailor (`jobpulse/cv_tailor.py`)

Generates a tailored LaTeX CV per job application.

**Flow:**
1. Load `data/cv_base.tex` (the base LaTeX template)
2. Load `jobpulse/templates/Resume Prompt.md` (the full prompt with all layers)
3. Inject JD Analyzer output into Layer 3 (EXTRACTED block)
4. Inject GitHub Matcher output into Layer 4 (skill-to-project mapping, project ordering)
5. Send to LLM (gpt-4o-mini) with the full prompt → get back complete `.tex` file
6. Save `.tex` to `data/applications/{job_id}/cv.tex`
7. Compile with `xelatex` (as specified in Resume Prompt Layer 10) → `data/applications/{job_id}/cv.pdf`
8. Extract text from PDF for ATS scoring
9. Run deterministic ATS scorer

**ATS Scoring (deterministic, no LLM):**
```python
def score_ats(jd: JobListing, cv_text: str) -> ATSScore:
    # 1. Keyword matching (0-70 points)
    #    - Exact match: full credit
    #    - Synonym match (pytorch↔torch, ml↔machine learning): 80% credit
    #    - Transferable match (pandas→data manipulation): 50% credit
    jd_keywords = jd.required_skills + jd.preferred_skills
    matched = [k for k in jd_keywords if k_in_cv(k, cv_text)]
    keyword_score = (len(matched) / len(jd_keywords)) * 70

    # 2. Section completeness (0-20 points)
    #    Required: Education, Experience, Skills, Projects (5 pts each)
    sections = detect_sections(cv_text)
    section_score = sum(5 for s in ["education", "experience", "skills", "projects"] if s in sections)

    # 3. Format score (0-10 points)
    #    No tables: +3, No images: +3, Parseable headings: +4
    format_score = check_format(cv_text)

    total = keyword_score + section_score + format_score
    return ATSScore(total=total, keyword_score=keyword_score,
                    section_score=section_score, format_score=format_score,
                    missing_keywords=[k for k in jd_keywords if k not in matched],
                    matched_keywords=matched, passed=total >= 95)
```

**If score < 95%:** LLM gets a second pass with instructions to add missing keywords naturally. Re-compiles and re-scores. Max 2 refinement passes. If still < 95% after 2 passes, application proceeds but is flagged in Notion notes.

**Synonym dictionary:** Maintained in `data/skill_synonyms.json`:
```json
{
    "pytorch": ["torch", "py torch"],
    "machine learning": ["ml", "machine-learning"],
    "tensorflow": ["tf", "tensor flow"],
    "natural language processing": ["nlp"],
    "computer vision": ["cv"],
    "kubernetes": ["k8s"],
    "continuous integration": ["ci/cd", "ci", "cd"],
    ...
}
```

### 5.6 Cover Letter Generator (`jobpulse/cover_letter_agent.py`)

Generates a cover letter following the user's template at `jobpulse/templates/Cover letter template.md`.

**Input:**
- `JobListing` (company, role, JD text)
- Tailored CV text (to ensure cover letter references same projects/skills)
- MindGraph company entities (if any prior knowledge exists about the company)

**Template structure (from user's format):**
1. Greeting + catchy hook (2 lines — why interested / biggest achievement)
2. "I have read the job description and feel that I'm a great fit due to the following reasons:"
3. 4 numbered points — each maps a top JD skill/duty to user's experience with numbers
4. Closing paragraph — years of experience + industry + passion + fit

**LLM prompt:**
- Model: gpt-4o-mini
- Input: JD + tailored CV + cover letter template + MindGraph context
- Constraint: 250-350 words, match template structure exactly, use numbers/metrics, reference matched projects by name

**Output:** Saved as `data/applications/{job_id}/cover_letter.tex` → compiled to PDF alongside CV. Also saved as plain text for form paste.

### 5.7 Applicator (`jobpulse/applicator.py`)

Orchestrates the actual submission.

**Tier logic:**

| ATS Score | Easy Apply? | Action |
|-----------|-------------|--------|
| >= 90% | Yes | Auto-submit → notify Telegram after |
| >= 90% | No (complex form) | Pre-fill via ATS adapter → screenshot to Telegram → auto-submit after 15 min unless rejected |
| 82-89% | Yes or No | Send to Telegram review batch → wait for explicit approval |
| < 82% | Any | Skip silently → log in Notion as "Skipped" with reason |

**ATS Adapters (`jobpulse/ats_adapters/`):**

Base class:
```python
class BaseATSAdapter:
    async def detect(self, url: str) -> bool:
        """Returns True if this adapter handles this URL."""

    async def fill_form(self, page: Page, app: ApplicationRecord) -> Screenshot:
        """Fill all form fields, return screenshot before submit."""

    async def submit(self, page: Page) -> bool:
        """Click submit. Returns True on success."""
```

6 adapters:

| Adapter | Detection Pattern | Form Strategy |
|---------|-------------------|---------------|
| `linkedin.py` | `linkedin.com/jobs` + Easy Apply button | Click Easy Apply → upload CV → fill fields → submit |
| `indeed.py` | `indeed.co.uk` + Quick Apply | Upload CV → fill fields → submit |
| `greenhouse.py` | `greenhouse.io` in URL or iframe | Fill name/email/phone → upload CV + CL → answer custom Qs |
| `lever.py` | `lever.co` in URL | Fill name/email → upload CV + CL → answer custom Qs |
| `workday.py` | `myworkdayjobs.com` in URL | Multi-step wizard: personal → education → experience → upload → review → submit |
| `generic.py` | Fallback | Detect input fields by label text, best-effort fill |

**Custom question handling:**
- Common questions have hardcoded answers:
  - "Do you require sponsorship?" → "No"
  - "Visa status" → "Student Visa, converting to Graduate Visa from 9 May 2026 (valid 2 years)"
  - "Right to work in UK?" → "Yes"
  - "Notice period?" → "Available immediately"
  - "Salary expectation?" → extracted from search config (27K-32K range)
- Novel questions → LLM generates answer from JD + CV context → cached in `ats_answer_cache` table for reuse across identical questions

**Profile data** for form filling (extracted from Resume Prompt Layer 1):
```json
{
    "name": "Yash B",
    "email": "bishnoiyash274@gmail.com",
    "phone": "07909445288",
    "linkedin": "https://linkedin.com/in/yash-bishnoi-2ab36a1a5",
    "github": "https://github.com/yashb98",
    "portfolio": "https://yashbishnoi.io",
    "education": "MSc Computer Science, University of Dundee (Jan 2025 - Jan 2026)",
    "location": "Dundee, UK"
}
```

### 5.8 Notion Sync (`jobpulse/job_notion_sync.py`)

Syncs every application to the "Job Tracker" Notion database.

**Env var:** `NOTION_APPLICATIONS_DB_ID`

**Sync points:**
| Event | Notion Action |
|-------|---------------|
| Job found | Create row: Company, Role, Platform, Status=Found, Found Date, JD URL, Seniority |
| Analysis complete | Update: ATS Score, Match Tier, Matched Projects, ATS Platform, Salary, Location, Remote, Notes |
| CV + Cover Letter ready | Update: Status=Ready, upload CV Version + Cover Letter files |
| Sent for approval | Update: Status=Pending Approval |
| Applied | Update: Status=Applied, Applied Date, Follow Up Date (applied + 7 days) |
| Skipped | Update: Status=Skipped, Notes (reason) |
| Interview detected (Gmail) | Update: Status=Interview |
| Rejection detected (Gmail) | Update: Status=Rejected |

**Follow-up reminders:** Daily check at 9am — any application with Follow Up Date = today and Status = Applied → send Telegram reminder: "7 days since you applied to {role} at {company}. No response yet. Mark as follow-up or move on?"

### 5.9 Job Autopilot Orchestrator (`jobpulse/job_autopilot.py`)

Top-level function called by cron at each scan window.

```python
async def run_scan_window(platforms: list[str]):
    """Execute one scan window."""
    # 1. Scan
    listings = await scan_platforms(platforms)

    # 2. Analyze
    analyzed = [await analyze_jd(listing) for listing in listings]

    # 3. Dedup
    new_jobs = await deduplicate(analyzed)

    # 4. For each new job: match → tailor → cover letter → score → tier
    applications = []
    for job in new_jobs:
        matched = await match_github_projects(job)
        cv_path, ats = await tailor_cv(job, matched)
        cl_path = await generate_cover_letter(job, cv_path)
        app = build_application(job, matched, cv_path, cl_path, ats)
        await sync_to_notion(app)
        applications.append(app)

    # 5. Apply by tier
    auto_apps = [a for a in applications if a.match_tier == "auto"]
    review_apps = [a for a in applications if a.match_tier == "review"]

    for app in auto_apps:
        await apply_job(app)  # auto-submit

    if review_apps:
        await send_review_batch(review_apps)  # Telegram batch

    # 6. Summary to Telegram
    await send_scan_summary(len(listings), len(new_jobs), len(auto_apps), len(review_apps))
```

## 6. Telegram Integration

### 6.1 New Bot

**5th Telegram bot:** Jobs bot
- Token: `TELEGRAM_JOBS_BOT_TOKEN`
- Chat ID: `TELEGRAM_JOBS_CHAT_ID`
- Falls back to main bot if not configured

### 6.2 New Intents

| Intent | Patterns | Action |
|--------|----------|--------|
| `SHOW_JOBS` | "jobs", "show jobs", "new jobs", "what's available" | Today's found jobs grouped by tier |
| `APPROVE_JOBS` | "apply 3,5,7", "approve 1-5", "apply all" | Approve specific jobs from review batch |
| `REJECT_JOB` | "reject 4", "skip 2", "pass on 6" | Skip a specific job |
| `JOB_STATS` | "job stats", "application stats", "how many applied" | This week's pipeline metrics |
| `SEARCH_CONFIG` | "search: add title X", "search: exclude company X", "search: remove title Y" | Modify search parameters |
| `PAUSE_JOBS` | "pause jobs", "stop applying" | Pause the autopilot |
| `RESUME_JOBS` | "resume jobs", "start applying" | Resume autopilot |
| `JOB_DETAIL` | "job 5", "details 3" | Full details for a specific job from the batch |

### 6.3 Telegram Message Formats

**Scan summary:**
```
Job Autopilot (7:00 AM scan)
Found: 23 new jobs
Auto-applied: 8 (avg ATS: 94.2%)
Ready for review: 12
Skipped: 3 (<82% match)
```

**Review batch:**
```
12 jobs ready for review (82-89% ATS):

1. Data Scientist — Barclays (Indeed)
   ATS: 87% | London | 30-35K | Greenhouse
   Skills: Python, SQL, Tableau, ML

2. ML Engineer Intern — Revolut (LinkedIn)
   ATS: 84% | Remote | 28-32K | Lever
   Skills: Python, PyTorch, Docker, AWS

...

Reply: "apply 1,2,5" or "apply all" or "reject 3"
```

**Daily summary (7pm):**
```
Job Autopilot Daily Summary
Applied today: 34 (22 auto, 12 approved)
Awaiting review: 3
Skipped: 8
Avg ATS: 93.2%
Top platform: LinkedIn (15 applied)
Follow-ups due tomorrow: 2
```

## 7. SQLite Storage (`data/applications.db`)

```sql
CREATE TABLE job_listings (
    job_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    salary_min REAL,
    salary_max REAL,
    location TEXT,
    remote BOOLEAN DEFAULT FALSE,
    seniority TEXT,
    required_skills TEXT,       -- JSON array
    preferred_skills TEXT,      -- JSON array
    description_raw TEXT,
    ats_platform TEXT,
    easy_apply BOOLEAN DEFAULT FALSE,
    found_at TEXT NOT NULL
);

CREATE TABLE applications (
    job_id TEXT PRIMARY KEY REFERENCES job_listings(job_id),
    status TEXT NOT NULL DEFAULT 'Found',
    ats_score REAL DEFAULT 0,
    match_tier TEXT DEFAULT 'skip',
    matched_projects TEXT,      -- JSON array
    cv_path TEXT,
    cover_letter_path TEXT,
    applied_at TEXT,
    notion_page_id TEXT,
    follow_up_date TEXT,
    custom_answers TEXT,        -- JSON dict
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE application_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT REFERENCES applications(job_id),
    event_type TEXT NOT NULL,   -- status_change, cv_generated, applied, error
    old_value TEXT,
    new_value TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE search_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,         -- JSON
    updated_at TEXT NOT NULL
);

CREATE TABLE ats_answer_cache (
    question_hash TEXT PRIMARY KEY,  -- SHA-256 of normalized question text
    question_text TEXT NOT NULL,
    answer TEXT NOT NULL,
    times_used INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);
```

## 8. Cross-Agent Integration

### 8.1 Gmail Agent Enhancement

Add classification categories to detect application responses:
- **INTERVIEW_INVITE** — patterns: "interview", "schedule a call", "next steps", "meet the team"
- **REJECTION** — patterns: "unfortunately", "other candidates", "not moving forward", "position filled"
- **APPLICATION_CONFIRMATION** — patterns: "received your application", "application submitted"

When detected, query `applications.db` by company name (fuzzy match) → update status → sync to Notion.

### 8.2 MindGraph Integration

After JD Analyzer parses a job, auto-extract:
- Company entity (name, industry, location)
- Role entity (title, seniority, skills)
- Relation: APPLIED_TO between user and company

This builds a knowledge graph of the job market — over time, surfaces patterns like "3 fintech companies you're tracking all use Python + Spark."

### 8.3 Morning Briefing Integration

Add a "Jobs" section to the morning briefing:
```
Jobs: Applied to 34 yesterday (avg ATS 93%). 2 follow-ups due today.
3 new interviews this week. Top matching: ML Engineer at DeepMind (ATS 97%).
```

### 8.4 Weekly Report Integration

Add application metrics to the weekly report:
- Total applications this week
- Applications per platform
- Average ATS score
- Interview conversion rate
- Status breakdown (applied/interview/rejected/pending)

### 8.5 Process Trails

Every pipeline run gets a full process trail:
```
ProcessTrail("job_autopilot", "7am_scan")
  → step("scan", "LinkedIn: found 12 listings")      → 8.2s
  → step("scan", "Indeed: found 8 listings")          → 3.1s
  → step("scan", "Reed: found 6 listings")            → 2.4s
  → step("analyze", "Parsed 26 JDs")                  → 12.5s
  → step("dedup", "18 new, 8 duplicates")             → 0.3s
  → step("match", "GitHub matched 18 jobs")           → 4.2s
  → step("tailor", "Generated 18 CVs")                → 45.0s
  → step("score", "Avg ATS: 93.2%, 10 auto, 6 review, 2 skip") → 2.1s
  → step("apply", "Auto-applied to 10 jobs")          → 120.0s
  → step("notify", "Sent review batch of 6 to Telegram") → 0.5s
  → finalize("Scan complete: 10 applied, 6 pending review, 2 skipped")
```

## 9. Configuration

### 9.1 New Environment Variables

```env
# Jobs Bot
TELEGRAM_JOBS_BOT_TOKEN=<token>
TELEGRAM_JOBS_CHAT_ID=<chat_id>

# Notion Applications DB
NOTION_APPLICATIONS_DB_ID=<database_id>

# Reed API
REED_API_KEY=<key>

# Job Autopilot
JOB_AUTOPILOT_ENABLED=true     # master switch
JOB_AUTOPILOT_AUTO_SUBMIT=true # false = approval required for everything
JOB_AUTOPILOT_MAX_DAILY=60     # safety cap
```

### 9.2 Search Config (`data/job_search_config.json`)

```json
{
    "titles": [
        "Data Scientist",
        "ML Engineer",
        "AI Engineer",
        "Data Engineer",
        "Machine Learning Engineer",
        "Graduate Data Scientist",
        "Junior AI Engineer",
        "Graduate ML Engineer",
        "Data Science Intern",
        "Machine Learning Intern"
    ],
    "location": "United Kingdom",
    "include_remote": true,
    "salary_min": 27000,
    "salary_max": null,
    "exclude_companies": [],
    "exclude_keywords": ["senior", "lead", "principal", "staff", "10+ years", "8+ years", "director"]
}
```

### 9.3 Work Authorization (hardcoded in applicator)

```python
WORK_AUTH = {
    "requires_sponsorship": False,
    "visa_status": "Student Visa (converting to Graduate Visa from 9 May 2026, valid 2 years)",
    "right_to_work_uk": True,
    "notice_period": "Available immediately",
    "salary_expectation": "27,000 - 32,000",
}
```

## 10. Dependencies

New pip dependencies:
```
playwright>=1.40.0        # browser automation for job sites + ATS forms
                          # xelatex must be installed (texlive-xetex or mactex)
pymupdf>=1.24.0           # PDF text extraction for ATS scoring
```

System dependency:
```bash
playwright install chromium   # one-time browser install
```

## 11. Testing Strategy

| Component | Test Type | What to Verify |
|-----------|-----------|----------------|
| JD Analyzer | Unit | Correct extraction from sample JDs (salary, skills, seniority) |
| Deduplicator | Unit | Exact URL dedup, fuzzy company+title dedup, no false positives |
| GitHub Matcher | Unit | Correct scoring and ranking against sample JDs |
| ATS Scorer | Unit | Score calculation accuracy, synonym matching, section detection |
| CV Tailor | Integration | Full flow: JD → tailored .tex → compiled PDF → ATS score >= 95% |
| Cover Letter | Unit | Template adherence, word count, references correct projects |
| Notion Sync | Unit | Correct column mapping, status transitions |
| Applicator | Unit | Tier classification, adapter selection |
| ATS Adapters | Integration | Form detection and fill on test pages (Greenhouse demo, etc.) |
| Autopilot | Integration | Full pipeline run with mocked scrapers |

## 12. Risks and Mitigations

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| LinkedIn blocks scraping | High | Use real browser profile, slow requests, fall back to other platforms |
| ATS adapters break on layout changes | Medium | Generic fallback adapter + prep package, adapter health monitoring |
| LaTeX compilation fails | Low | Validate .tex before compile, cache last working version |
| Rate limiting on job sites | Medium | Respect rate limits, randomized delays, distribute across 6 windows |
| Duplicate applications despite dedup | Low | Triple check (URL + fuzzy + Notion), log all dedup decisions |
| Over-applying (spam perception) | Low | Daily cap (60), per-company limit (1 per 7 days), quality gate |

## 13. Future Enhancements (Out of Scope)

- Interview prep agent (generates company-specific prep notes)
- Application follow-up automation (email templates after 7 days)
- Salary negotiation data (aggregate market data for leverage)
- A/B testing CV variations (track which CV style gets more interviews)
- Persona evolution for cover letters (learn which tone gets responses)
