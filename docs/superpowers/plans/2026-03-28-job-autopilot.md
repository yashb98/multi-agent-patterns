# Job Autopilot Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous job discovery, CV tailoring, and application submission pipeline inside JobPulse.

**Architecture:** 7-stage pipeline (Scanner → Analyzer → Deduper → GitHub Matcher → CV Tailor + Cover Letter → Applicator → Notion Sync) integrated into existing JobPulse dispatcher, Telegram multi-bot, and cron infrastructure. New modules in `jobpulse/`, adapters in `jobpulse/ats_adapters/`, models in `jobpulse/models/`.

**Tech Stack:** Python 3.12, httpx (scraping), Playwright (browser automation), OpenAI gpt-4o-mini (JD analysis, CV generation, cover letters), xelatex (PDF compilation), SQLite (local storage), Notion API (tracking), Telegram Bot API (approvals).

**Spec:** `docs/superpowers/specs/2026-03-28-job-autopilot-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `jobpulse/models/application_models.py` | Pydantic models: JobListing, ApplicationRecord, ATSScore, SearchConfig, ApplicationStatus |
| `jobpulse/job_db.py` | SQLite storage layer for applications.db (listings, applications, events, answer cache) |
| `jobpulse/jd_analyzer.py` | Parse raw JD text into structured JobListing fields (rule-based + LLM) |
| `jobpulse/job_deduplicator.py` | Prevent re-applying: URL hash, fuzzy company+title, Notion cross-check |
| `jobpulse/github_matcher.py` | Score GitHub repos against JD, pick top 3-4 projects |
| `jobpulse/ats_scorer.py` | Deterministic ATS keyword/section/format scoring (no LLM) |
| `jobpulse/cv_tailor.py` | Generate tailored LaTeX CV per job, compile to PDF, score |
| `jobpulse/cover_letter_agent.py` | Generate cover letter from JD + CV using user's template |
| `jobpulse/job_notion_sync.py` | Sync applications to "Job Tracker" Notion database |
| `jobpulse/job_scanner.py` | Scrape 5 job platforms for listings |
| `jobpulse/applicator.py` | Orchestrate submission: tier logic + adapter dispatch |
| `jobpulse/ats_adapters/__init__.py` | Adapter registry |
| `jobpulse/ats_adapters/base.py` | BaseATSAdapter abstract class |
| `jobpulse/ats_adapters/linkedin.py` | LinkedIn Easy Apply adapter |
| `jobpulse/ats_adapters/indeed.py` | Indeed Quick Apply adapter |
| `jobpulse/ats_adapters/greenhouse.py` | Greenhouse form adapter |
| `jobpulse/ats_adapters/lever.py` | Lever form adapter |
| `jobpulse/ats_adapters/workday.py` | Workday wizard adapter |
| `jobpulse/ats_adapters/generic.py` | Fallback form fill adapter |
| `jobpulse/job_autopilot.py` | Top-level orchestrator — runs full pipeline per scan window |
| `data/job_search_config.json` | Search titles, location, salary, exclusions |
| `data/skill_synonyms.json` | Keyword synonyms for ATS scoring |
| `tests/test_application_models.py` | Model validation tests |
| `tests/test_job_db.py` | Storage layer tests |
| `tests/test_jd_analyzer.py` | JD parsing tests |
| `tests/test_job_deduplicator.py` | Dedup logic tests |
| `tests/test_github_matcher.py` | Repo scoring tests |
| `tests/test_ats_scorer.py` | ATS scoring tests |
| `tests/test_cv_tailor.py` | CV generation integration tests |
| `tests/test_cover_letter.py` | Cover letter tests |
| `tests/test_job_notion_sync.py` | Notion sync tests |
| `tests/test_applicator.py` | Applicator tier logic tests |
| `tests/test_job_autopilot.py` | Full pipeline integration test |

### Modified Files

| File | Change |
|------|--------|
| `jobpulse/config.py` | Add 5 new env vars |
| `jobpulse/command_router.py` | Add 8 new Intent enum values + regex patterns |
| `jobpulse/dispatcher.py` | Wire 8 new intent handlers |
| `jobpulse/telegram_bots.py` | Add Jobs bot (5th bot), JOBS_INTENTS set, send_jobs(), HELP_JOBS |
| `jobpulse/multi_bot_listener.py` | Import JOBS_INTENTS, add processing estimates for job intents |
| `jobpulse/morning_briefing.py` | Add job pipeline summary section |
| `jobpulse/weekly_report.py` | Add weekly application metrics |
| `scripts/install_cron.py` | Add 6 scan window cron entries |
| `data/intent_examples.json` | Add ~40 training examples for 8 new intents |

---

## Task 1: Pydantic Models

**Files:**
- Create: `jobpulse/models/__init__.py`
- Create: `jobpulse/models/application_models.py`
- Test: `tests/test_application_models.py`

- [ ] **Step 1: Create models directory**

```bash
mkdir -p jobpulse/models
```

- [ ] **Step 2: Write failing tests for all models**

```python
# tests/test_application_models.py
"""Tests for job application pipeline Pydantic models."""

import pytest
from datetime import datetime, date


def test_job_listing_minimal():
    """JobListing with only required fields."""
    from jobpulse.models.application_models import JobListing

    job = JobListing(
        job_id="abc123",
        title="Data Scientist",
        company="Barclays",
        platform="linkedin",
        url="https://linkedin.com/jobs/123",
        location="London",
        description_raw="We need a data scientist...",
        found_at=datetime.now(),
    )
    assert job.company == "Barclays"
    assert job.remote is False
    assert job.easy_apply is False
    assert job.required_skills == []


def test_job_listing_full():
    """JobListing with all fields populated."""
    from jobpulse.models.application_models import JobListing

    job = JobListing(
        job_id="def456",
        title="ML Engineer Intern",
        company="Revolut",
        platform="indeed",
        url="https://indeed.co.uk/jobs/456",
        salary_min=28000,
        salary_max=32000,
        location="Remote",
        remote=True,
        seniority="intern",
        required_skills=["python", "pytorch", "sql"],
        preferred_skills=["docker", "aws"],
        description_raw="Full JD text here...",
        ats_platform="greenhouse",
        found_at=datetime.now(),
        easy_apply=False,
    )
    assert job.salary_min == 28000
    assert job.seniority == "intern"
    assert len(job.required_skills) == 3


def test_job_listing_invalid_platform():
    """JobListing rejects invalid platform."""
    from jobpulse.models.application_models import JobListing
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        JobListing(
            job_id="x", title="x", company="x", platform="monster",
            url="x", location="x", description_raw="x", found_at=datetime.now(),
        )


def test_application_status_enum():
    """ApplicationStatus has all required values."""
    from jobpulse.models.application_models import ApplicationStatus

    assert ApplicationStatus.FOUND == "Found"
    assert ApplicationStatus.APPLIED == "Applied"
    assert ApplicationStatus.INTERVIEW == "Interview"
    assert ApplicationStatus.SKIPPED == "Skipped"


def test_application_record_defaults():
    """ApplicationRecord initialises with correct defaults."""
    from jobpulse.models.application_models import ApplicationRecord, JobListing, ApplicationStatus

    job = JobListing(
        job_id="abc", title="DE", company="X", platform="reed",
        url="https://reed.co.uk/1", location="UK", description_raw="...",
        found_at=datetime.now(),
    )
    app = ApplicationRecord(job=job)
    assert app.status == ApplicationStatus.FOUND
    assert app.ats_score == 0.0
    assert app.match_tier == "skip"
    assert app.matched_projects == []
    assert app.cv_path is None
    assert app.cover_letter_path is None


def test_ats_score_pass():
    """ATSScore with total >= 95 passes."""
    from jobpulse.models.application_models import ATSScore

    score = ATSScore(
        total=96.5, keyword_score=66.5, section_score=20.0, format_score=10.0,
        missing_keywords=["spark"], matched_keywords=["python", "sql", "pytorch"],
        passed=True,
    )
    assert score.passed is True
    assert score.total == 96.5


def test_ats_score_fail():
    """ATSScore with total < 95 fails."""
    from jobpulse.models.application_models import ATSScore

    score = ATSScore(
        total=78.0, keyword_score=48.0, section_score=20.0, format_score=10.0,
        missing_keywords=["docker", "aws", "spark"], matched_keywords=["python"],
        passed=False,
    )
    assert score.passed is False


def test_search_config_defaults():
    """SearchConfig has correct defaults."""
    from jobpulse.models.application_models import SearchConfig

    config = SearchConfig(titles=["Data Scientist"])
    assert config.location == "United Kingdom"
    assert config.include_remote is True
    assert config.salary_min == 27000
    assert config.salary_max is None
    assert config.exclude_companies == []


def test_search_config_custom():
    """SearchConfig with custom exclusions."""
    from jobpulse.models.application_models import SearchConfig

    config = SearchConfig(
        titles=["ML Engineer", "AI Engineer"],
        exclude_companies=["Palantir"],
        exclude_keywords=["senior", "lead"],
    )
    assert "Palantir" in config.exclude_companies
    assert len(config.titles) == 2
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_application_models.py -v
```

Expected: ModuleNotFoundError — `jobpulse.models.application_models` does not exist.

- [ ] **Step 4: Implement models**

```python
# jobpulse/models/__init__.py
"""Pydantic models for the job application pipeline."""
```

```python
# jobpulse/models/application_models.py
"""Pydantic models for the Job Autopilot pipeline."""

from datetime import datetime, date
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


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


class JobListing(BaseModel):
    job_id: str = Field(description="SHA-256 hash of URL for dedup")
    title: str = Field(description="Job title from listing")
    company: str = Field(description="Company name")
    platform: Literal["linkedin", "indeed", "reed", "totaljobs", "glassdoor"] = Field(
        description="Source platform"
    )
    url: str = Field(description="Original job listing URL")
    salary_min: float | None = Field(default=None, description="Minimum salary in GBP")
    salary_max: float | None = Field(default=None, description="Maximum salary in GBP")
    location: str = Field(description="Job location")
    remote: bool = Field(default=False, description="Whether remote work is available")
    seniority: Literal["intern", "graduate", "junior", "mid"] | None = Field(
        default=None, description="Seniority level"
    )
    required_skills: list[str] = Field(default_factory=list, description="Required skills from JD")
    preferred_skills: list[str] = Field(
        default_factory=list, description="Nice-to-have skills from JD"
    )
    description_raw: str = Field(description="Full JD text")
    ats_platform: str | None = Field(
        default=None, description="Detected ATS platform (greenhouse, lever, workday, etc.)"
    )
    found_at: datetime = Field(description="When the listing was discovered")
    easy_apply: bool = Field(default=False, description="Whether Easy Apply / Quick Apply is available")


class ATSScore(BaseModel):
    total: float = Field(description="Overall ATS score 0-100")
    keyword_score: float = Field(description="Keyword match score 0-70")
    section_score: float = Field(description="Section completeness score 0-20")
    format_score: float = Field(description="Format score 0-10")
    missing_keywords: list[str] = Field(default_factory=list, description="Keywords not found in CV")
    matched_keywords: list[str] = Field(default_factory=list, description="Keywords matched in CV")
    passed: bool = Field(description="True if total >= 95")


class ApplicationRecord(BaseModel):
    job: JobListing
    status: ApplicationStatus = ApplicationStatus.FOUND
    ats_score: float = 0.0
    match_tier: Literal["auto", "review", "skip"] = "skip"
    matched_projects: list[str] = Field(default_factory=list, description="GitHub repo names")
    cv_path: Path | None = None
    cover_letter_path: Path | None = None
    applied_at: datetime | None = None
    notion_page_id: str | None = None
    follow_up_date: date | None = None
    custom_answers: dict[str, str] = Field(default_factory=dict)


class SearchConfig(BaseModel):
    titles: list[str] = Field(description="Job titles to search for")
    location: str = "United Kingdom"
    include_remote: bool = True
    salary_min: float = 27000
    salary_max: float | None = None
    exclude_companies: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(
        default_factory=lambda: ["senior", "lead", "principal", "staff", "10+ years", "8+ years", "director"]
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_application_models.py -v
```

Expected: All 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/models/__init__.py jobpulse/models/application_models.py tests/test_application_models.py
git commit -m "feat(jobs): Task 1 — Pydantic models for Job Autopilot pipeline"
```

---

## Task 2: SQLite Storage Layer

**Files:**
- Create: `jobpulse/job_db.py`
- Test: `tests/test_job_db.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_job_db.py
"""Tests for job application SQLite storage layer."""

import pytest
from datetime import datetime, date
from pathlib import Path


@pytest.fixture
def db(tmp_path):
    """Create a temporary job database."""
    from jobpulse.job_db import JobDB
    return JobDB(tmp_path / "test_applications.db")


@pytest.fixture
def sample_listing():
    from jobpulse.models.application_models import JobListing
    return JobListing(
        job_id="sha256_abc123",
        title="Data Scientist",
        company="Barclays",
        platform="linkedin",
        url="https://linkedin.com/jobs/123",
        salary_min=30000,
        salary_max=35000,
        location="London",
        remote=False,
        seniority="junior",
        required_skills=["python", "sql", "tableau"],
        preferred_skills=["pytorch"],
        description_raw="We need a data scientist with...",
        ats_platform="greenhouse",
        found_at=datetime(2026, 3, 28, 7, 0, 0),
        easy_apply=False,
    )


def test_save_and_get_listing(db, sample_listing):
    db.save_listing(sample_listing)
    result = db.get_listing("sha256_abc123")
    assert result is not None
    assert result["company"] == "Barclays"
    assert result["platform"] == "linkedin"


def test_save_listing_duplicate_is_upsert(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_listing(sample_listing)
    count = db.count_listings()
    assert count == 1


def test_listing_exists(db, sample_listing):
    assert db.listing_exists("sha256_abc123") is False
    db.save_listing(sample_listing)
    assert db.listing_exists("sha256_abc123") is True


def test_save_and_get_application(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(
        job_id="sha256_abc123", status="Applied", ats_score=94.5,
        match_tier="auto", matched_projects=["Velox AI", "Cloud Sentinel"],
        cv_path="/data/applications/abc/cv.pdf",
        cover_letter_path="/data/applications/abc/cl.pdf",
        applied_at=datetime(2026, 3, 28, 8, 0, 0),
        notion_page_id="notion_123",
        follow_up_date=date(2026, 4, 4),
    )
    app = db.get_application("sha256_abc123")
    assert app is not None
    assert app["status"] == "Applied"
    assert app["ats_score"] == 94.5


def test_update_application_status(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(job_id="sha256_abc123", status="Found", ats_score=0)
    db.update_status("sha256_abc123", "Applied")
    app = db.get_application("sha256_abc123")
    assert app["status"] == "Applied"


def test_log_event(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(job_id="sha256_abc123", status="Found", ats_score=0)
    db.log_event("sha256_abc123", "status_change", "Found", "Applied", "Auto-applied")
    events = db.get_events("sha256_abc123")
    assert len(events) == 1
    assert events[0]["event_type"] == "status_change"


def test_get_applications_by_status(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(job_id="sha256_abc123", status="Applied", ats_score=94.5)
    results = db.get_applications_by_status("Applied")
    assert len(results) == 1
    assert results[0]["job_id"] == "sha256_abc123"


def test_get_follow_ups_due(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(
        job_id="sha256_abc123", status="Applied", ats_score=94.5,
        follow_up_date=date(2026, 3, 28),
    )
    due = db.get_follow_ups_due(date(2026, 3, 28))
    assert len(due) == 1


def test_fuzzy_company_title_exists(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(job_id="sha256_abc123", status="Applied", ats_score=94.5)
    assert db.fuzzy_match_exists("Barclays", "Data Scientist") is True
    assert db.fuzzy_match_exists("Barclays", "Senior Data Scientist") is True
    assert db.fuzzy_match_exists("Barclays", "Frontend Developer") is False
    assert db.fuzzy_match_exists("HSBC", "Data Scientist") is False


def test_cache_answer(db):
    db.cache_answer("Do you require sponsorship?", "No")
    answer = db.get_cached_answer("Do you require sponsorship?")
    assert answer == "No"


def test_cache_answer_miss(db):
    answer = db.get_cached_answer("Unknown question")
    assert answer is None


def test_today_stats(db, sample_listing):
    db.save_listing(sample_listing)
    db.save_application(
        job_id="sha256_abc123", status="Applied", ats_score=94.5,
        applied_at=datetime.now(),
    )
    stats = db.get_today_stats()
    assert stats["applied"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_job_db.py -v
```

Expected: ModuleNotFoundError — `jobpulse.job_db` does not exist.

- [ ] **Step 3: Implement JobDB**

```python
# jobpulse/job_db.py
"""SQLite storage layer for the Job Autopilot pipeline."""

import json
import hashlib
import sqlite3
from datetime import datetime, date
from pathlib import Path

from jobpulse.config import DATA_DIR
from jobpulse.models.application_models import JobListing
from shared.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH = DATA_DIR / "applications.db"


class JobDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS job_listings (
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
                required_skills TEXT,
                preferred_skills TEXT,
                description_raw TEXT,
                ats_platform TEXT,
                easy_apply BOOLEAN DEFAULT FALSE,
                found_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
                job_id TEXT PRIMARY KEY REFERENCES job_listings(job_id),
                status TEXT NOT NULL DEFAULT 'Found',
                ats_score REAL DEFAULT 0,
                match_tier TEXT DEFAULT 'skip',
                matched_projects TEXT,
                cv_path TEXT,
                cover_letter_path TEXT,
                applied_at TEXT,
                notion_page_id TEXT,
                follow_up_date TEXT,
                custom_answers TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS application_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT REFERENCES applications(job_id),
                event_type TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ats_answer_cache (
                question_hash TEXT PRIMARY KEY,
                question_text TEXT NOT NULL,
                answer TEXT NOT NULL,
                times_used INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()

    def save_listing(self, listing: JobListing):
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO job_listings
               (job_id, title, company, platform, url, salary_min, salary_max,
                location, remote, seniority, required_skills, preferred_skills,
                description_raw, ats_platform, easy_apply, found_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (listing.job_id, listing.title, listing.company, listing.platform,
             listing.url, listing.salary_min, listing.salary_max,
             listing.location, listing.remote, listing.seniority,
             json.dumps(listing.required_skills), json.dumps(listing.preferred_skills),
             listing.description_raw, listing.ats_platform, listing.easy_apply,
             listing.found_at.isoformat()),
        )
        conn.commit()
        conn.close()

    def get_listing(self, job_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM job_listings WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def listing_exists(self, job_id: str) -> bool:
        conn = self._conn()
        row = conn.execute("SELECT 1 FROM job_listings WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        return row is not None

    def count_listings(self) -> int:
        conn = self._conn()
        row = conn.execute("SELECT COUNT(*) as c FROM job_listings").fetchone()
        conn.close()
        return row["c"]

    def save_application(self, job_id: str, status: str = "Found", ats_score: float = 0,
                         match_tier: str = "skip", matched_projects: list[str] | None = None,
                         cv_path: str | None = None, cover_letter_path: str | None = None,
                         applied_at: datetime | None = None, notion_page_id: str | None = None,
                         follow_up_date: date | None = None, custom_answers: dict | None = None):
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute(
            """INSERT OR REPLACE INTO applications
               (job_id, status, ats_score, match_tier, matched_projects,
                cv_path, cover_letter_path, applied_at, notion_page_id,
                follow_up_date, custom_answers, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, status, ats_score, match_tier,
             json.dumps(matched_projects or []),
             cv_path, cover_letter_path,
             applied_at.isoformat() if applied_at else None,
             notion_page_id,
             follow_up_date.isoformat() if follow_up_date else None,
             json.dumps(custom_answers or {}),
             now, now),
        )
        conn.commit()
        conn.close()

    def get_application(self, job_id: str) -> dict | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_status(self, job_id: str, new_status: str):
        conn = self._conn()
        old = conn.execute("SELECT status FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        old_status = old["status"] if old else None
        conn.execute(
            "UPDATE applications SET status = ?, updated_at = ? WHERE job_id = ?",
            (new_status, datetime.now().isoformat(), job_id),
        )
        conn.commit()
        conn.close()
        if old_status:
            self.log_event(job_id, "status_change", old_status, new_status)

    def log_event(self, job_id: str, event_type: str, old_value: str = "",
                  new_value: str = "", details: str = ""):
        conn = self._conn()
        conn.execute(
            "INSERT INTO application_events (job_id, event_type, old_value, new_value, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, event_type, old_value, new_value, details, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_events(self, job_id: str) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM application_events WHERE job_id = ? ORDER BY created_at", (job_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_applications_by_status(self, status: str) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            """SELECT a.*, l.title, l.company, l.platform, l.url, l.location, l.salary_min, l.salary_max
               FROM applications a JOIN job_listings l ON a.job_id = l.job_id
               WHERE a.status = ? ORDER BY a.updated_at DESC""",
            (status,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_follow_ups_due(self, target_date: date) -> list[dict]:
        conn = self._conn()
        rows = conn.execute(
            """SELECT a.*, l.title, l.company, l.platform, l.url
               FROM applications a JOIN job_listings l ON a.job_id = l.job_id
               WHERE a.follow_up_date = ? AND a.status = 'Applied'""",
            (target_date.isoformat(),),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def fuzzy_match_exists(self, company: str, title: str) -> bool:
        """Check if a similar application exists (same company, similar title, within 30 days)."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT l.title FROM applications a
               JOIN job_listings l ON a.job_id = l.job_id
               WHERE LOWER(l.company) = LOWER(?)
               AND a.status NOT IN ('Skipped', 'Withdrawn')
               AND a.created_at >= date('now', '-30 days')""",
            (company,),
        ).fetchall()
        conn.close()

        if not rows:
            return False

        title_words = set(title.lower().split())
        for row in rows:
            existing_words = set(row["title"].lower().split())
            if len(title_words) == 0 or len(existing_words) == 0:
                continue
            overlap = len(title_words & existing_words) / max(len(title_words), len(existing_words))
            if overlap >= 0.8:
                return True
        return False

    def cache_answer(self, question: str, answer: str):
        q_hash = hashlib.sha256(question.lower().strip().encode()).hexdigest()
        conn = self._conn()
        conn.execute(
            """INSERT INTO ats_answer_cache (question_hash, question_text, answer, times_used, created_at)
               VALUES (?, ?, ?, 1, ?)
               ON CONFLICT(question_hash) DO UPDATE SET
               times_used = times_used + 1""",
            (q_hash, question, answer, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()

    def get_cached_answer(self, question: str) -> str | None:
        q_hash = hashlib.sha256(question.lower().strip().encode()).hexdigest()
        conn = self._conn()
        row = conn.execute(
            "SELECT answer FROM ats_answer_cache WHERE question_hash = ?", (q_hash,)
        ).fetchone()
        conn.close()
        return row["answer"] if row else None

    def get_today_stats(self) -> dict:
        today = date.today().isoformat()
        conn = self._conn()
        applied = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE applied_at LIKE ?", (f"{today}%",)
        ).fetchone()["c"]
        found = conn.execute(
            "SELECT COUNT(*) as c FROM job_listings WHERE found_at LIKE ?", (f"{today}%",)
        ).fetchone()["c"]
        skipped = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE status = 'Skipped' AND created_at LIKE ?",
            (f"{today}%",),
        ).fetchone()["c"]
        avg_ats = conn.execute(
            "SELECT AVG(ats_score) as avg FROM applications WHERE applied_at LIKE ? AND ats_score > 0",
            (f"{today}%",),
        ).fetchone()["avg"]
        conn.close()
        return {
            "applied": applied,
            "found": found,
            "skipped": skipped,
            "avg_ats": round(avg_ats, 1) if avg_ats else 0,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_job_db.py -v
```

Expected: All 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_db.py tests/test_job_db.py
git commit -m "feat(jobs): Task 2 — SQLite storage layer for applications"
```

---

## Task 3: Config + Search Config + Skill Synonyms

**Files:**
- Modify: `jobpulse/config.py`
- Create: `data/job_search_config.json`
- Create: `data/skill_synonyms.json`

- [ ] **Step 1: Add new env vars to config.py**

Add these lines after the existing Telegram config block (after line 30 in `jobpulse/config.py`):

```python
# Jobs bot
TELEGRAM_JOBS_BOT_TOKEN = os.getenv("TELEGRAM_JOBS_BOT_TOKEN", "")
TELEGRAM_JOBS_CHAT_ID = os.getenv("TELEGRAM_JOBS_CHAT_ID", TELEGRAM_CHAT_ID)

# Notion Applications DB
NOTION_APPLICATIONS_DB_ID = os.getenv("NOTION_APPLICATIONS_DB_ID", "")

# Reed API
REED_API_KEY = os.getenv("REED_API_KEY", "")

# Job Autopilot
JOB_AUTOPILOT_ENABLED = os.getenv("JOB_AUTOPILOT_ENABLED", "true").lower() in ("true", "1", "yes")
JOB_AUTOPILOT_AUTO_SUBMIT = os.getenv("JOB_AUTOPILOT_AUTO_SUBMIT", "true").lower() in ("true", "1", "yes")
JOB_AUTOPILOT_MAX_DAILY = int(os.getenv("JOB_AUTOPILOT_MAX_DAILY", "60"))
```

- [ ] **Step 2: Create search config**

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

Save to `data/job_search_config.json`.

- [ ] **Step 3: Create skill synonyms**

```json
{
    "python": ["python3", "py"],
    "pytorch": ["torch", "py torch"],
    "tensorflow": ["tf", "tensor flow", "tensor-flow"],
    "machine learning": ["ml", "machine-learning"],
    "deep learning": ["dl", "deep-learning"],
    "natural language processing": ["nlp"],
    "computer vision": ["cv", "image recognition"],
    "kubernetes": ["k8s"],
    "docker": ["containerization", "containerisation", "containers"],
    "continuous integration": ["ci/cd", "ci", "cd", "cicd"],
    "amazon web services": ["aws"],
    "google cloud platform": ["gcp", "google cloud"],
    "microsoft azure": ["azure"],
    "structured query language": ["sql"],
    "postgresql": ["postgres"],
    "mongodb": ["mongo"],
    "scikit-learn": ["sklearn", "scikit learn"],
    "pandas": ["data manipulation", "dataframes"],
    "numpy": ["numerical computing"],
    "fastapi": ["fast api"],
    "react": ["reactjs", "react.js"],
    "javascript": ["js"],
    "typescript": ["ts"],
    "data visualization": ["data visualisation", "dataviz"],
    "power bi": ["powerbi"],
    "exploratory data analysis": ["eda"],
    "extract transform load": ["etl"],
    "rest api": ["restful", "rest apis", "api development"],
    "large language models": ["llms", "llm"],
    "retrieval augmented generation": ["rag"],
    "model context protocol": ["mcp"],
    "langchain": ["lang chain"],
    "hugging face": ["huggingface", "hf"],
    "mlflow": ["ml flow"],
    "mlops": ["ml ops", "ml operations"],
    "agile": ["scrum", "kanban"],
    "git": ["github", "version control"]
}
```

Save to `data/skill_synonyms.json`.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/config.py data/job_search_config.json data/skill_synonyms.json
git commit -m "feat(jobs): Task 3 — config, search config, skill synonyms"
```

---

## Task 4: JD Analyzer

**Files:**
- Create: `jobpulse/jd_analyzer.py`
- Test: `tests/test_jd_analyzer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_jd_analyzer.py
"""Tests for JD analyzer — parses raw job descriptions into structured data."""

import pytest
from datetime import datetime


SAMPLE_JD = """
Data Scientist - Barclays

Location: London, UK (Hybrid)
Salary: £30,000 - £35,000

About the role:
We are looking for a Junior Data Scientist to join our analytics team.
You will work with large datasets to derive insights and build ML models.

Requirements:
- Python (essential)
- SQL and database management
- Machine Learning (scikit-learn, PyTorch)
- Data visualization (Tableau or Power BI)
- Strong communication skills

Nice to have:
- Docker and Kubernetes experience
- Cloud platforms (AWS or GCP)
- Experience with NLP

Apply via our Greenhouse portal.
"""

SAMPLE_JD_NO_SALARY = """
ML Engineer Intern - Revolut
Remote, UK

We're hiring an ML Engineer Intern. You'll build production ML pipelines.

Must have:
- Python, PyTorch
- Docker
- Git

Preferred:
- AWS experience
- FastAPI
"""


def test_extract_salary_range():
    from jobpulse.jd_analyzer import extract_salary
    assert extract_salary("£30,000 - £35,000") == (30000, 35000)
    assert extract_salary("30K-35K") == (30000, 35000)
    assert extract_salary("£28k - £32k per annum") == (28000, 32000)
    assert extract_salary("Competitive salary") == (None, None)
    assert extract_salary("$50,000-$60,000") == (50000, 60000)


def test_extract_location():
    from jobpulse.jd_analyzer import extract_location
    assert extract_location("Location: London, UK (Hybrid)") == "London, UK"
    assert extract_location("Remote, UK") == "Remote, UK"


def test_detect_remote():
    from jobpulse.jd_analyzer import detect_remote
    assert detect_remote("Remote, UK") is True
    assert detect_remote("Hybrid working from London") is True
    assert detect_remote("Office-based in Manchester") is False


def test_detect_seniority():
    from jobpulse.jd_analyzer import detect_seniority
    assert detect_seniority("Junior Data Scientist") == "junior"
    assert detect_seniority("Graduate ML Engineer") == "graduate"
    assert detect_seniority("ML Engineer Intern") == "intern"
    assert detect_seniority("Data Scientist") is None


def test_detect_ats_platform():
    from jobpulse.jd_analyzer import detect_ats_platform
    assert detect_ats_platform("https://boards.greenhouse.io/barclays/123") == "greenhouse"
    assert detect_ats_platform("https://jobs.lever.co/revolut/456") == "lever"
    assert detect_ats_platform("https://barclays.wd3.myworkdayjobs.com/en-US/jobs/1") == "workday"
    assert detect_ats_platform("https://linkedin.com/jobs/123") is None


def test_detect_easy_apply():
    from jobpulse.jd_analyzer import detect_easy_apply
    assert detect_easy_apply("https://linkedin.com/jobs/123", "Easy Apply available") is True
    assert detect_easy_apply("https://indeed.co.uk/jobs/456", "Quick Apply") is True
    assert detect_easy_apply("https://greenhouse.io/jobs/789", "Apply now") is False


def test_generate_job_id():
    from jobpulse.jd_analyzer import generate_job_id
    id1 = generate_job_id("https://linkedin.com/jobs/123")
    id2 = generate_job_id("https://linkedin.com/jobs/123")
    id3 = generate_job_id("https://linkedin.com/jobs/456")
    assert id1 == id2  # same URL = same ID
    assert id1 != id3  # different URL = different ID
    assert len(id1) == 64  # SHA-256 hex
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jd_analyzer.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement rule-based extraction functions**

```python
# jobpulse/jd_analyzer.py
"""JD Analyzer — parses raw job descriptions into structured JobListing fields.

Two-tier extraction:
  1. Rule-based: salary, location, seniority, ATS platform, easy apply
  2. LLM (gpt-4o-mini): required_skills, preferred_skills, industry, sub-context
"""

import re
import hashlib
import json
from datetime import datetime
from pathlib import Path

from jobpulse.config import OPENAI_API_KEY, DATA_DIR
from jobpulse.models.application_models import JobListing
from shared.logging_config import get_logger

logger = get_logger(__name__)


def generate_job_id(url: str) -> str:
    """SHA-256 hash of URL for deduplication."""
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


def extract_salary(text: str) -> tuple[float | None, float | None]:
    """Extract salary range from JD text. Returns (min, max) in raw numbers."""
    # £30,000 - £35,000 or $50,000-$60,000
    m = re.search(r'[£$€]\s*([\d,]+)\s*[-–to]+\s*[£$€]?\s*([\d,]+)', text)
    if m:
        lo = float(m.group(1).replace(",", ""))
        hi = float(m.group(2).replace(",", ""))
        return lo, hi

    # 30K-35K or 30k - 35k
    m = re.search(r'(\d+)\s*[kK]\s*[-–to]+\s*(\d+)\s*[kK]', text)
    if m:
        return float(m.group(1)) * 1000, float(m.group(2)) * 1000

    return None, None


def extract_location(text: str) -> str:
    """Extract location from JD text."""
    m = re.search(r'(?:Location|Based|Office)\s*:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if m:
        loc = m.group(1).strip()
        # Strip parenthetical notes like (Hybrid)
        loc = re.sub(r'\s*\(.*?\)', '', loc).strip()
        return loc

    # First line that looks like a location (City, Country)
    for line in text.split('\n'):
        line = line.strip()
        if re.match(r'^(Remote|Hybrid|London|Manchester|Edinburgh|Birmingham|Leeds|Bristol|Glasgow|Cambridge|Oxford|Cardiff|Belfast|Dundee|UK|United Kingdom)', line, re.IGNORECASE):
            return line.split('.')[0].strip()

    return "United Kingdom"


def detect_remote(text: str) -> bool:
    """Detect if the job offers remote or hybrid work."""
    text_lower = text.lower()
    return bool(re.search(r'\b(remote|hybrid|work from home|wfh|flexible.?location)\b', text_lower))


def detect_seniority(text: str) -> str | None:
    """Detect seniority level from title or JD."""
    text_lower = text.lower()
    if re.search(r'\b(intern|internship|placement)\b', text_lower):
        return "intern"
    if re.search(r'\b(graduate|grad scheme|grad programme|entry.?level)\b', text_lower):
        return "graduate"
    if re.search(r'\b(junior|jr\.?|associate)\b', text_lower):
        return "junior"
    if re.search(r'\b(mid.?level|mid.?senior|intermediate)\b', text_lower):
        return "mid"
    return None


def detect_ats_platform(url: str) -> str | None:
    """Detect ATS platform from application URL."""
    url_lower = url.lower()
    if "greenhouse.io" in url_lower or "boards.greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "myworkdayjobs.com" in url_lower or "workday" in url_lower:
        return "workday"
    if "smartrecruiters.com" in url_lower:
        return "smartrecruiters"
    if "icims.com" in url_lower:
        return "icims"
    return None


def detect_easy_apply(url: str, text: str) -> bool:
    """Detect if Easy Apply / Quick Apply is available."""
    text_lower = text.lower()
    url_lower = url.lower()
    if "linkedin.com" in url_lower and "easy apply" in text_lower:
        return True
    if "indeed" in url_lower and "quick apply" in text_lower:
        return True
    return False


def extract_skills_llm(jd_text: str) -> dict:
    """Use LLM to extract structured skill data from JD.

    Returns dict with: required_skills, preferred_skills, industry, sub_context
    """
    if not OPENAI_API_KEY:
        logger.warning("No OPENAI_API_KEY — skipping LLM skill extraction")
        return {"required_skills": [], "preferred_skills": [], "industry": "", "sub_context": ""}

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": f"""Extract structured data from this job description.

Return a JSON object with exactly these keys:
- "required_skills": list of required/essential skills (lowercase, max 15)
- "preferred_skills": list of nice-to-have skills (lowercase, max 10)
- "industry": the industry (e.g. "FinTech", "HealthTech", "SaaS", "Banking")
- "sub_context": specific technical context (e.g. "fraud detection", "NLP pipelines")

Job Description:
{jd_text[:3000]}

Return ONLY valid JSON, no markdown."""}],
        max_tokens=500,
        temperature=0,
    )

    try:
        content = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse LLM skill extraction: %s", e)
        return {"required_skills": [], "preferred_skills": [], "industry": "", "sub_context": ""}


def analyze_jd(url: str, title: str, company: str, platform: str,
               jd_text: str, apply_url: str = "") -> JobListing:
    """Full JD analysis: rule-based + LLM extraction → JobListing."""
    job_id = generate_job_id(url)
    salary_min, salary_max = extract_salary(jd_text)
    location = extract_location(jd_text)
    remote = detect_remote(jd_text)
    seniority = detect_seniority(f"{title} {jd_text[:500]}")
    ats_platform = detect_ats_platform(apply_url or url)
    easy_apply = detect_easy_apply(url, jd_text)

    # LLM extraction for skills
    skills_data = extract_skills_llm(jd_text)

    return JobListing(
        job_id=job_id,
        title=title,
        company=company,
        platform=platform,
        url=url,
        salary_min=salary_min,
        salary_max=salary_max,
        location=location,
        remote=remote,
        seniority=seniority,
        required_skills=skills_data.get("required_skills", []),
        preferred_skills=skills_data.get("preferred_skills", []),
        description_raw=jd_text,
        ats_platform=ats_platform,
        found_at=datetime.now(),
        easy_apply=easy_apply,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jd_analyzer.py -v
```

Expected: All 7 tests PASS (rule-based functions only — no LLM call in these tests).

- [ ] **Step 5: Commit**

```bash
git add jobpulse/jd_analyzer.py tests/test_jd_analyzer.py
git commit -m "feat(jobs): Task 4 — JD analyzer with rule-based + LLM extraction"
```

---

## Task 5: Deduplicator

**Files:**
- Create: `jobpulse/job_deduplicator.py`
- Test: `tests/test_job_deduplicator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_job_deduplicator.py
"""Tests for job deduplication logic."""

import pytest
from datetime import datetime


@pytest.fixture
def db(tmp_path):
    from jobpulse.job_db import JobDB
    return JobDB(tmp_path / "test_dedup.db")


@pytest.fixture
def make_listing():
    from jobpulse.models.application_models import JobListing
    def _make(job_id="abc", title="Data Scientist", company="Barclays",
              url="https://example.com/1"):
        return JobListing(
            job_id=job_id, title=title, company=company, platform="linkedin",
            url=url, location="London", description_raw="...", found_at=datetime.now(),
        )
    return _make


def test_dedup_exact_url(db, make_listing):
    """Same job_id (URL hash) is filtered out."""
    from jobpulse.job_deduplicator import deduplicate
    existing = make_listing(job_id="abc")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    incoming = [make_listing(job_id="abc"), make_listing(job_id="def", url="https://example.com/2")]
    result = deduplicate(incoming, db)
    assert len(result) == 1
    assert result[0].job_id == "def"


def test_dedup_fuzzy_company_title(db, make_listing):
    """Same company + similar title within 30 days is filtered."""
    from jobpulse.job_deduplicator import deduplicate
    existing = make_listing(job_id="abc", title="Data Scientist", company="Barclays")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    # Similar title at same company
    incoming = [make_listing(job_id="def", title="Junior Data Scientist", company="Barclays",
                             url="https://example.com/2")]
    result = deduplicate(incoming, db)
    # "Data Scientist" vs "Junior Data Scientist" — overlap is 2/3 = 0.67 < 0.8, so NOT filtered
    assert len(result) == 1

    # Exact same title at same company
    incoming2 = [make_listing(job_id="ghi", title="Data Scientist", company="Barclays",
                              url="https://example.com/3")]
    result2 = deduplicate(incoming2, db)
    assert len(result2) == 0  # filtered


def test_dedup_different_company_same_title(db, make_listing):
    """Same title at different company is NOT filtered."""
    from jobpulse.job_deduplicator import deduplicate
    existing = make_listing(job_id="abc", title="Data Scientist", company="Barclays")
    db.save_listing(existing)
    db.save_application(job_id="abc", status="Applied", ats_score=90)

    incoming = [make_listing(job_id="def", title="Data Scientist", company="HSBC",
                             url="https://example.com/2")]
    result = deduplicate(incoming, db)
    assert len(result) == 1


def test_dedup_empty_list(db):
    """Empty input returns empty output."""
    from jobpulse.job_deduplicator import deduplicate
    assert deduplicate([], db) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_job_deduplicator.py -v
```

- [ ] **Step 3: Implement deduplicator**

```python
# jobpulse/job_deduplicator.py
"""Job Deduplicator — prevents applying to the same job twice."""

from jobpulse.job_db import JobDB
from jobpulse.models.application_models import JobListing
from shared.logging_config import get_logger

logger = get_logger(__name__)


def deduplicate(listings: list[JobListing], db: JobDB) -> list[JobListing]:
    """Filter out listings that match existing applications.

    Checks:
      1. Exact URL match (job_id = SHA-256 of URL)
      2. Same company + similar title (word overlap >= 0.8) within 30 days
    """
    if not listings:
        return []

    new = []
    for listing in listings:
        # Check 1: exact URL
        if db.listing_exists(listing.job_id):
            logger.debug("Dedup: exact URL match for %s at %s", listing.title, listing.company)
            continue

        # Check 2: fuzzy company + title
        if db.fuzzy_match_exists(listing.company, listing.title):
            logger.debug("Dedup: fuzzy match for %s at %s", listing.title, listing.company)
            continue

        new.append(listing)

    filtered = len(listings) - len(new)
    if filtered:
        logger.info("Dedup: %d/%d filtered, %d new", filtered, len(listings), len(new))
    return new
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_job_deduplicator.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_deduplicator.py tests/test_job_deduplicator.py
git commit -m "feat(jobs): Task 5 — job deduplicator with URL hash + fuzzy matching"
```

---

## Task 6: GitHub Matcher

**Files:**
- Create: `jobpulse/github_matcher.py`
- Test: `tests/test_github_matcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_github_matcher.py
"""Tests for GitHub repo matching against JD requirements."""

import pytest


MOCK_REPOS = [
    {
        "name": "Velox_AI",
        "description": "Enterprise AI Voice Agent Platform",
        "languages": ["python", "javascript"],
        "topics": ["ai", "voice", "fastapi", "docker", "gcp"],
        "keywords": ["python", "fastapi", "docker", "gcp", "langchain", "websocket",
                     "ai", "voice", "real-time", "kubernetes"],
    },
    {
        "name": "Cloud-Sentinel",
        "description": "AI Powered Cloud Security Platform",
        "languages": ["python", "typescript"],
        "topics": ["security", "rag", "react", "fastapi", "docker"],
        "keywords": ["python", "react", "fastapi", "docker", "redis", "pinecone",
                     "rag", "embeddings", "security", "mcp", "typescript"],
    },
    {
        "name": "90-Days-ML",
        "description": "90 Days Machine Learning journey",
        "languages": ["python"],
        "topics": ["machine-learning", "pytorch", "tensorflow", "scikit-learn"],
        "keywords": ["python", "pytorch", "tensorflow", "scikit-learn", "pandas",
                     "numpy", "matplotlib", "machine learning", "deep learning",
                     "eda", "mlflow", "mlops"],
    },
    {
        "name": "3D-Face-Reconstruction",
        "description": "Deep Learning for Facial 3D Reconstructions",
        "languages": ["python"],
        "topics": ["deep-learning", "pytorch", "computer-vision"],
        "keywords": ["python", "pytorch", "computer vision", "deep learning",
                     "3d reconstruction", "ssim", "cnn"],
    },
]


def test_score_repo_data_science():
    """ML-heavy JD should rank 90-Days-ML highest."""
    from jobpulse.github_matcher import score_repo

    jd_required = ["python", "sql", "machine learning", "scikit-learn", "pandas"]
    jd_preferred = ["pytorch", "tensorflow", "mlflow"]

    scores = {}
    for repo in MOCK_REPOS:
        scores[repo["name"]] = score_repo(repo, jd_required, jd_preferred)

    assert scores["90-Days-ML"] > scores["Velox_AI"]
    assert scores["90-Days-ML"] > scores["Cloud-Sentinel"]


def test_score_repo_cloud_engineering():
    """Cloud/infra JD should rank Velox AI or Cloud Sentinel highest."""
    from jobpulse.github_matcher import score_repo

    jd_required = ["python", "docker", "kubernetes", "fastapi", "gcp"]
    jd_preferred = ["redis", "ci/cd"]

    scores = {}
    for repo in MOCK_REPOS:
        scores[repo["name"]] = score_repo(repo, jd_required, jd_preferred)

    assert scores["Velox_AI"] > scores["90-Days-ML"]
    assert scores["Cloud-Sentinel"] > scores["90-Days-ML"]


def test_pick_top_projects():
    """pick_top_projects returns 3-4 repos sorted by score."""
    from jobpulse.github_matcher import pick_top_projects

    jd_required = ["python", "pytorch", "deep learning"]
    jd_preferred = ["computer vision"]

    top = pick_top_projects(MOCK_REPOS, jd_required, jd_preferred, top_n=3)
    assert len(top) == 3
    assert top[0]["name"] in ("90-Days-ML", "3D-Face-Reconstruction")


def test_pick_top_projects_limit_4():
    """Can request top 4."""
    from jobpulse.github_matcher import pick_top_projects

    jd_required = ["python"]
    jd_preferred = []

    top = pick_top_projects(MOCK_REPOS, jd_required, jd_preferred, top_n=4)
    assert len(top) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_github_matcher.py -v
```

- [ ] **Step 3: Implement GitHub Matcher**

```python
# jobpulse/github_matcher.py
"""GitHub Matcher — scores repos against JD requirements, picks top 3-4 projects.

Uses cached repo data (refreshed daily via GitHub API).
"""

import json
from pathlib import Path

from jobpulse.config import DATA_DIR, GITHUB_USERNAME, GITHUB_TOKEN
from shared.logging_config import get_logger

logger = get_logger(__name__)

REPO_CACHE_PATH = DATA_DIR / "github_repo_cache.json"

# Yash's 4 fixed portfolio projects (from Resume Prompt)
PORTFOLIO_PROJECTS = {
    "Velox_AI": "Velox AI - Enterprise AI Voice Agent Platform",
    "Cloud-Sentinel": "Cloud Sentinel - AI Powered Cloud Security Platform",
    "90-Days-ML": "90 Days Machine learning",
    "3D-Face-Reconstruction": "Deep Learning for Facial 3D Reconstructions",
}


def load_skill_synonyms() -> dict[str, list[str]]:
    """Load skill synonym mapping."""
    synonyms_path = DATA_DIR / "skill_synonyms.json"
    if synonyms_path.exists():
        return json.loads(synonyms_path.read_text())
    return {}


def _normalize(skill: str) -> str:
    return skill.lower().strip().replace("-", " ").replace("_", " ")


def _skill_match(skill: str, keywords: list[str], synonyms: dict[str, list[str]]) -> bool:
    """Check if a skill matches any keyword, including synonyms."""
    norm_skill = _normalize(skill)
    norm_keywords = [_normalize(k) for k in keywords]

    # Direct match
    if norm_skill in norm_keywords:
        return True

    # Check if any synonym of the skill appears in keywords
    for canonical, syns in synonyms.items():
        all_forms = [_normalize(canonical)] + [_normalize(s) for s in syns]
        if norm_skill in all_forms:
            # Skill is a form of this canonical — check if any form is in keywords
            if any(kw in all_forms for kw in norm_keywords):
                return True

    return False


def score_repo(repo: dict, jd_required: list[str], jd_preferred: list[str]) -> float:
    """Score a single repo against JD skills.

    Score = required_match * 0.5 + preferred_match * 0.3 + keyword_density * 0.2
    """
    synonyms = load_skill_synonyms()
    repo_keywords = repo.get("keywords", [])

    if not jd_required and not jd_preferred:
        return 0.0

    # Required skill overlap
    req_matches = sum(1 for s in jd_required if _skill_match(s, repo_keywords, synonyms))
    req_score = (req_matches / len(jd_required)) if jd_required else 0

    # Preferred skill overlap
    pref_matches = sum(1 for s in jd_preferred if _skill_match(s, repo_keywords, synonyms))
    pref_score = (pref_matches / len(jd_preferred)) if jd_preferred else 0

    # Keyword density (how many repo keywords are relevant at all)
    all_jd = jd_required + jd_preferred
    density_matches = sum(1 for k in repo_keywords if _skill_match(k, all_jd, synonyms))
    density_score = (density_matches / len(repo_keywords)) if repo_keywords else 0

    return req_score * 0.5 + pref_score * 0.3 + density_score * 0.2


def pick_top_projects(repos: list[dict], jd_required: list[str],
                      jd_preferred: list[str], top_n: int = 4) -> list[dict]:
    """Score all repos and return top N sorted by relevance."""
    scored = [(repo, score_repo(repo, jd_required, jd_preferred)) for repo in repos]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [repo for repo, _ in scored[:top_n]]


def fetch_and_cache_repos() -> list[dict]:
    """Fetch repo data from GitHub API and cache locally. Called once per day."""
    import httpx

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    try:
        resp = httpx.get(
            f"https://api.github.com/users/{GITHUB_USERNAME}/repos?per_page=100&sort=updated",
            headers=headers, timeout=15,
        )
        resp.raise_for_status()
        raw_repos = resp.json()
    except Exception as e:
        logger.error("Failed to fetch GitHub repos: %s", e)
        # Fall back to cache
        if REPO_CACHE_PATH.exists():
            return json.loads(REPO_CACHE_PATH.read_text())
        return []

    repos = []
    for r in raw_repos:
        name = r.get("name", "")
        description = r.get("description", "") or ""
        languages = list((r.get("language") or "").lower().split()) if r.get("language") else []
        topics = r.get("topics", [])

        # Build keyword list from topics + languages + description words
        keywords = [_normalize(t) for t in topics]
        keywords.extend([_normalize(l) for l in languages])

        # Extract tech keywords from description
        for word in description.lower().split():
            word = word.strip(".,()-/")
            if len(word) > 2:
                keywords.append(word)

        repos.append({
            "name": name,
            "description": description,
            "languages": languages,
            "topics": topics,
            "keywords": list(set(keywords)),
            "url": r.get("html_url", ""),
        })

    REPO_CACHE_PATH.write_text(json.dumps(repos, indent=2))
    logger.info("Cached %d GitHub repos", len(repos))
    return repos
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_github_matcher.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/github_matcher.py tests/test_github_matcher.py
git commit -m "feat(jobs): Task 6 — GitHub matcher with synonym-aware scoring"
```

---

## Task 7: ATS Scorer (Deterministic)

**Files:**
- Create: `jobpulse/ats_scorer.py`
- Test: `tests/test_ats_scorer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ats_scorer.py
"""Tests for deterministic ATS scoring — no LLM involved."""

import pytest


def test_perfect_score():
    """CV with all keywords, all sections, good format = 100."""
    from jobpulse.ats_scorer import score_ats

    jd_skills = ["python", "sql", "pytorch", "docker"]
    cv_text = """
    Education
    MSc Computer Science
    Experience
    Team Leader at Co-op
    Technical Skills
    Python, SQL, PyTorch, Docker
    Projects
    Velox AI, Cloud Sentinel
    """
    result = score_ats(jd_skills, cv_text)
    assert result.total >= 95
    assert result.passed is True
    assert len(result.missing_keywords) == 0


def test_missing_keywords():
    """CV missing some JD keywords scores lower."""
    from jobpulse.ats_scorer import score_ats

    jd_skills = ["python", "sql", "pytorch", "docker", "kubernetes", "spark"]
    cv_text = """
    Education
    MSc Computer Science
    Experience
    Team Leader
    Technical Skills
    Python, SQL
    Projects
    My project
    """
    result = score_ats(jd_skills, cv_text)
    assert result.total < 95
    assert result.passed is False
    assert "pytorch" in result.missing_keywords
    assert "python" in result.matched_keywords


def test_synonym_matching():
    """Synonyms (k8s → kubernetes) should count as matches."""
    from jobpulse.ats_scorer import score_ats

    jd_skills = ["kubernetes", "machine learning"]
    cv_text = """
    Education
    MSc Computer Science
    Experience
    Engineer
    Skills
    K8s, ML, Docker
    Projects
    My project
    """
    result = score_ats(jd_skills, cv_text)
    assert "kubernetes" in result.matched_keywords
    assert "machine learning" in result.matched_keywords


def test_section_scoring():
    """Missing sections reduce the score."""
    from jobpulse.ats_scorer import score_ats

    jd_skills = ["python"]
    # Missing Experience and Projects sections
    cv_text = """
    Education
    MSc Computer Science
    Technical Skills
    Python
    """
    result = score_ats(jd_skills, cv_text)
    assert result.section_score < 20  # missing 2 sections


def test_empty_cv():
    """Empty CV scores 0."""
    from jobpulse.ats_scorer import score_ats

    result = score_ats(["python"], "")
    assert result.total == 0
    assert result.passed is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ats_scorer.py -v
```

- [ ] **Step 3: Implement ATS scorer**

```python
# jobpulse/ats_scorer.py
"""Deterministic ATS Scorer — keyword matching + section detection + format checks.

No LLM calls. Pure Python scoring against JD skills and CV text.
"""

import re
import json
from pathlib import Path

from jobpulse.config import DATA_DIR
from jobpulse.models.application_models import ATSScore
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _load_synonyms() -> dict[str, list[str]]:
    path = DATA_DIR / "skill_synonyms.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _normalize(text: str) -> str:
    return text.lower().strip().replace("-", " ").replace("_", " ")


def _keyword_in_text(keyword: str, cv_text: str, synonyms: dict) -> bool:
    """Check if keyword (or any synonym) appears in CV text."""
    norm_cv = _normalize(cv_text)
    norm_kw = _normalize(keyword)

    # Direct match
    if norm_kw in norm_cv:
        return True

    # Check all synonym forms
    for canonical, syns in synonyms.items():
        all_forms = [_normalize(canonical)] + [_normalize(s) for s in syns]
        if norm_kw in all_forms:
            # This keyword is a form of this canonical — check all forms against CV
            for form in all_forms:
                if form in norm_cv:
                    return True

    return False


def _detect_sections(cv_text: str) -> set[str]:
    """Detect which standard CV sections are present."""
    text_lower = cv_text.lower()
    found = set()
    if re.search(r'\b(education|academic|degree|university|msc|bsc)\b', text_lower):
        found.add("education")
    if re.search(r'\b(experience|employment|work history|professional experience)\b', text_lower):
        found.add("experience")
    if re.search(r'\b(skills|technical skills|core skills|competencies)\b', text_lower):
        found.add("skills")
    if re.search(r'\b(projects|portfolio|personal projects)\b', text_lower):
        found.add("projects")
    return found


def score_ats(jd_skills: list[str], cv_text: str) -> ATSScore:
    """Score a CV against JD skills. Returns ATSScore with breakdown.

    Scoring:
      - Keyword match: 0-70 (matched/total * 70)
      - Section completeness: 0-20 (5 per required section)
      - Format: 0-10 (parseable, no binary content)
    """
    if not cv_text.strip():
        return ATSScore(
            total=0, keyword_score=0, section_score=0, format_score=0,
            missing_keywords=jd_skills, matched_keywords=[], passed=False,
        )

    synonyms = _load_synonyms()

    # 1. Keyword matching (0-70)
    matched = []
    missing = []
    for skill in jd_skills:
        if _keyword_in_text(skill, cv_text, synonyms):
            matched.append(skill)
        else:
            missing.append(skill)

    keyword_score = (len(matched) / len(jd_skills) * 70) if jd_skills else 70

    # 2. Section completeness (0-20)
    sections = _detect_sections(cv_text)
    required = {"education", "experience", "skills", "projects"}
    section_score = sum(5 for s in required if s in sections)

    # 3. Format score (0-10)
    format_score = 10  # Start with full marks
    # Check for binary/table content (bad for ATS)
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', cv_text):
        format_score -= 3
    # Check for parseable headings
    if not re.search(r'\n[A-Z]', cv_text):
        format_score -= 4

    total = round(keyword_score + section_score + format_score, 1)

    return ATSScore(
        total=total,
        keyword_score=round(keyword_score, 1),
        section_score=section_score,
        format_score=format_score,
        missing_keywords=missing,
        matched_keywords=matched,
        passed=total >= 95,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ats_scorer.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ats_scorer.py tests/test_ats_scorer.py
git commit -m "feat(jobs): Task 7 — deterministic ATS scorer with synonym matching"
```

---

## Task 8: CV Tailor

**Files:**
- Create: `jobpulse/cv_tailor.py`
- Test: `tests/test_cv_tailor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cv_tailor.py
"""Tests for CV tailoring pipeline."""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_build_cv_prompt():
    """build_cv_prompt inserts JD data into the resume prompt template."""
    from jobpulse.cv_tailor import build_cv_prompt

    jd_data = {
        "location": "London",
        "role_title": "Data Scientist",
        "years_exp": "2+",
        "industry": "FinTech",
        "sub_context": "fraud detection",
        "skills_list": ["python", "sql", "pytorch", "pandas"],
        "soft_skills": ["communication", "teamwork"],
        "extended_skills": ["NLP/LLMs"],
    }
    matched_projects = ["90-Days-ML", "Cloud-Sentinel", "Velox_AI"]

    prompt = build_cv_prompt(jd_data, matched_projects)
    assert "London" in prompt
    assert "Data Scientist" in prompt
    assert "FinTech" in prompt
    assert "python" in prompt
    assert "90-Days-ML" in prompt


def test_extract_text_from_tex():
    """extract_text_from_tex strips LaTeX commands and returns plain text."""
    from jobpulse.cv_tailor import extract_text_from_tex

    tex = r"""
    \section*{Technical Skills}
    \textbf{Languages:} Python | SQL | JavaScript
    \textbf{AI/ML:} PyTorch | TensorFlow
    \section*{Education}
    MSc Computer Science, University of Dundee
    """
    text = extract_text_from_tex(tex)
    assert "Python" in text
    assert "Technical Skills" in text
    assert "Education" in text
    assert "\\textbf" not in text


def test_determine_match_tier():
    """Tier classification based on ATS score."""
    from jobpulse.cv_tailor import determine_match_tier

    assert determine_match_tier(95.0) == "auto"
    assert determine_match_tier(90.0) == "auto"
    assert determine_match_tier(89.0) == "review"
    assert determine_match_tier(82.0) == "review"
    assert determine_match_tier(81.9) == "skip"
    assert determine_match_tier(50.0) == "skip"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cv_tailor.py -v
```

- [ ] **Step 3: Implement CV tailor**

```python
# jobpulse/cv_tailor.py
"""CV Tailor — generates job-specific LaTeX CVs using the Resume Prompt.

Flow:
  1. Load base Resume Prompt template
  2. Inject JD-specific data (Layer 3: EXTRACTED block)
  3. Inject matched project info (Layer 4: skill routing)
  4. Send to LLM → get complete .tex file
  5. Compile with xelatex → PDF
  6. Extract text → ATS score
  7. If < 95%: refine and re-score (max 2 passes)
"""

import re
import subprocess
import shutil
from pathlib import Path

from jobpulse.config import DATA_DIR, OPENAI_API_KEY
from jobpulse.models.application_models import JobListing, ATSScore
from jobpulse.ats_scorer import score_ats
from shared.logging_config import get_logger

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
RESUME_PROMPT_PATH = TEMPLATES_DIR / "Resume Prompt.md"
CV_BASE_PATH = DATA_DIR / "cv_base.tex"
APPLICATIONS_DIR = DATA_DIR / "applications"


def build_cv_prompt(jd_data: dict, matched_projects: list[str]) -> str:
    """Build the full LLM prompt by injecting JD data into the Resume Prompt template."""
    template = RESUME_PROMPT_PATH.read_text(encoding="utf-8")

    # Build the EXTRACTED block for Layer 3
    extracted_block = f"""
EXTRACTED:
  LOCATION       : {jd_data.get('location', 'United Kingdom')}
  ROLE_TITLE     : {jd_data.get('role_title', 'Data Scientist')}
  YEARS_EXP      : {jd_data.get('years_exp', '2+')}
  INDUSTRY       : {jd_data.get('industry', '')}
  SUB_CONTEXT    : {jd_data.get('sub_context', '')}
  SKILLS_LIST    : {jd_data.get('skills_list', [])}
  SOFT_SKILLS    : {jd_data.get('soft_skills', [])}
  EXTENDED_SKILLS: {jd_data.get('extended_skills', [])}
"""

    # Build project priority instruction
    project_instruction = f"""
PROJECT PRIORITY ORDER (most relevant first):
  {', '.join(matched_projects)}

Place the most relevant project in the Project 2 slot (5 bullets) for maximum exposure.
"""

    return f"""{template}

--- JD-SPECIFIC INJECTION ---

{extracted_block}

{project_instruction}

Now generate the COMPLETE LaTeX .tex file following all layers above.
Output ONLY the .tex content, starting with \\documentclass and ending with \\end{{document}}.
No markdown code fences. No explanation."""


def extract_text_from_tex(tex_content: str) -> str:
    """Strip LaTeX commands to get plain text for ATS scoring."""
    text = tex_content
    # Remove comments
    text = re.sub(r'%.*$', '', text, flags=re.MULTILINE)
    # Remove common commands but keep their content
    text = re.sub(r'\\textbf\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\textit\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\textcolor\{[^}]*\}\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\href\{[^}]*\}\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\skylink\{[^}]*\}\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\section\*?\{([^}]*)\}', r'\n\1\n', text)
    text = re.sub(r'\\item\s*', '', text)
    text = re.sub(r'\\noindent', '', text)
    text = re.sub(r'\\hfill', ' ', text)
    text = re.sub(r'\\vspace\{[^}]*\}', '', text)
    # Remove remaining commands
    text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', text)
    text = re.sub(r'\\[a-zA-Z]+', '', text)
    # Remove braces, brackets
    text = re.sub(r'[{}]', '', text)
    text = re.sub(r'\[.*?\]', '', text)
    # Clean whitespace
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()


def determine_match_tier(ats_score: float) -> str:
    """Classify into auto/review/skip based on ATS score."""
    if ats_score >= 90:
        return "auto"
    elif ats_score >= 82:
        return "review"
    return "skip"


def compile_tex(tex_path: Path, output_dir: Path) -> Path | None:
    """Compile .tex to PDF using xelatex. Returns PDF path or None on failure."""
    try:
        # Run xelatex twice for correct layout (as per Resume Prompt)
        for _ in range(2):
            result = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-output-directory", str(output_dir),
                 str(tex_path)],
                capture_output=True, text=True, timeout=60, cwd=str(output_dir),
            )
        pdf_path = output_dir / tex_path.with_suffix(".pdf").name
        if pdf_path.exists():
            return pdf_path
        logger.error("xelatex produced no PDF. stderr: %s", result.stderr[:500])
        return None
    except FileNotFoundError:
        logger.error("xelatex not found — install texlive-xetex or mactex")
        return None
    except subprocess.TimeoutExpired:
        logger.error("xelatex compilation timed out")
        return None


def generate_tailored_cv(job: JobListing, matched_projects: list[str]) -> tuple[Path | None, ATSScore]:
    """Full CV tailoring pipeline for one job.

    Returns (pdf_path, ats_score). pdf_path is None if compilation fails.
    """
    job_dir = APPLICATIONS_DIR / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Build JD data for prompt injection
    jd_data = {
        "location": job.location,
        "role_title": job.title,
        "years_exp": "2+",
        "industry": "",
        "sub_context": "",
        "skills_list": job.required_skills,
        "soft_skills": [],
        "extended_skills": [],
    }

    prompt = build_cv_prompt(jd_data, matched_projects)

    # Generate .tex via LLM
    if not OPENAI_API_KEY:
        logger.error("No OPENAI_API_KEY for CV generation")
        return None, ATSScore(total=0, keyword_score=0, section_score=0, format_score=0,
                              missing_keywords=job.required_skills, matched_keywords=[], passed=False)

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    all_jd_skills = job.required_skills + job.preferred_skills
    best_tex = None
    best_score = ATSScore(total=0, keyword_score=0, section_score=0, format_score=0,
                          missing_keywords=all_jd_skills, matched_keywords=[], passed=False)

    for attempt in range(3):  # initial + 2 refinement passes
        messages = [{"role": "user", "content": prompt}]

        if attempt > 0 and best_score.missing_keywords:
            messages.append({"role": "user", "content": (
                f"The previous CV scored {best_score.total}/100 ATS. "
                f"Missing keywords: {', '.join(best_score.missing_keywords)}. "
                f"Add these keywords naturally into the CV without fabricating experience. "
                f"Output the COMPLETE .tex file again."
            )})

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            max_tokens=4000,
            temperature=0.3,
        )

        tex_content = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if tex_content.startswith("```"):
            tex_content = tex_content.split("\n", 1)[1].rsplit("```", 1)[0]

        # Score
        plain_text = extract_text_from_tex(tex_content)
        ats = score_ats(all_jd_skills, plain_text)

        if ats.total > best_score.total:
            best_tex = tex_content
            best_score = ats

        if ats.passed:
            break

        logger.info("CV attempt %d: ATS %.1f (need 95+), missing: %s",
                     attempt + 1, ats.total, ats.missing_keywords)

    if best_tex:
        tex_path = job_dir / "cv.tex"
        tex_path.write_text(best_tex, encoding="utf-8")
        pdf_path = compile_tex(tex_path, job_dir)
        return pdf_path, best_score

    return None, best_score
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cv_tailor.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cv_tailor.py tests/test_cv_tailor.py
git commit -m "feat(jobs): Task 8 — CV tailor with LLM generation, xelatex compile, ATS scoring loop"
```

---

## Task 9: Cover Letter Generator

**Files:**
- Create: `jobpulse/cover_letter_agent.py`
- Test: `tests/test_cover_letter.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cover_letter.py
"""Tests for cover letter generation."""

import pytest
from pathlib import Path


def test_build_cover_letter_prompt():
    """Prompt includes JD, CV skills, and template structure."""
    from jobpulse.cover_letter_agent import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        company="Barclays",
        role="Data Scientist",
        jd_text="We need a data scientist with Python, SQL...",
        matched_skills=["python", "sql", "pytorch"],
        matched_projects=["Velox AI", "90 Days ML"],
    )
    assert "Barclays" in prompt
    assert "Data Scientist" in prompt
    assert "python" in prompt
    assert "Velox AI" in prompt
    assert "4 numbered points" in prompt or "following reasons" in prompt


def test_cover_letter_word_count_instruction():
    """Prompt specifies 250-350 word constraint."""
    from jobpulse.cover_letter_agent import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        company="X", role="Y", jd_text="Z",
        matched_skills=["python"], matched_projects=["P1"],
    )
    assert "250" in prompt
    assert "350" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_cover_letter.py -v
```

- [ ] **Step 3: Implement cover letter agent**

```python
# jobpulse/cover_letter_agent.py
"""Cover Letter Generator — produces cover letters following user's template.

Template structure (from Cover letter template.md):
  1. Greeting + catchy hook
  2. "I have read the JD and feel I'm a great fit due to:"
  3. 4 numbered points — JD duty → user's experience with metrics
  4. Closing paragraph
"""

from pathlib import Path

from jobpulse.config import OPENAI_API_KEY, DATA_DIR
from jobpulse.models.application_models import JobListing
from shared.logging_config import get_logger

logger = get_logger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
COVER_LETTER_TEMPLATE_PATH = TEMPLATES_DIR / "Cover letter template.md"
APPLICATIONS_DIR = DATA_DIR / "applications"


def _load_template() -> str:
    """Load the cover letter template."""
    if COVER_LETTER_TEMPLATE_PATH.exists():
        return COVER_LETTER_TEMPLATE_PATH.read_text(encoding="utf-8")
    return ""


def build_cover_letter_prompt(company: str, role: str, jd_text: str,
                               matched_skills: list[str],
                               matched_projects: list[str]) -> str:
    """Build the LLM prompt for cover letter generation."""
    template = _load_template()

    return f"""Generate a cover letter for the following job application.

TEMPLATE FORMAT (follow this structure exactly):
{template}

SPECIFIC JOB DETAILS:
- Company: {company}
- Role: {role}
- JD: {jd_text[:2000]}

MY MATCHED SKILLS: {', '.join(matched_skills)}
MY RELEVANT PROJECTS: {', '.join(matched_projects)}

INSTRUCTIONS:
1. Follow the template structure: greeting + hook, then "I have read the job description and feel that I'm a great fit due to the following reasons:", then 4 numbered points, then closing
2. Each of the 4 numbered points must map a JD skill/duty to my experience with specific metrics/numbers
3. Reference my projects by name where relevant
4. 250-350 words total
5. Professional tone, confident but not arrogant
6. Do NOT fabricate experience — only reference skills and projects listed above

MY PROFILE:
- Name: Yash B
- MSc Computer Science, University of Dundee (Jan 2025 - Jan 2026)
- MBA Finance, JECRC University (2019-2021)
- Team Leader at Co-op (Apr 2025 - Present)
- Market Research Analyst at Nidhi Herbal (Jul 2021 - Sep 2024)
- Visa: Student Visa, converting to Graduate Visa from 9 May 2026

Output ONLY the cover letter text. No headers, no markdown, no explanation."""


def generate_cover_letter(job: JobListing, matched_skills: list[str],
                           matched_projects: list[str]) -> Path | None:
    """Generate a cover letter for one job. Returns path to saved text file."""
    if not OPENAI_API_KEY:
        logger.error("No OPENAI_API_KEY for cover letter generation")
        return None

    job_dir = APPLICATIONS_DIR / job.job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    prompt = build_cover_letter_prompt(
        company=job.company,
        role=job.title,
        jd_text=job.description_raw,
        matched_skills=matched_skills,
        matched_projects=matched_projects,
    )

    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1000,
            temperature=0.4,
        )
        letter_text = response.choices[0].message.content.strip()

        # Save as text
        text_path = job_dir / "cover_letter.txt"
        text_path.write_text(letter_text, encoding="utf-8")

        logger.info("Cover letter generated for %s at %s (%d words)",
                     job.title, job.company, len(letter_text.split()))
        return text_path

    except Exception as e:
        logger.error("Cover letter generation failed for %s: %s", job.company, e)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_cover_letter.py -v
```

Expected: All 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/cover_letter_agent.py tests/test_cover_letter.py
git commit -m "feat(jobs): Task 9 — cover letter generator using user's template"
```

---

## Task 10: Notion Sync

**Files:**
- Create: `jobpulse/job_notion_sync.py`
- Test: `tests/test_job_notion_sync.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_job_notion_sync.py
"""Tests for Notion sync — builds correct API payloads."""

import pytest
from datetime import datetime, date


def test_build_create_payload():
    """Create payload has all 19 columns."""
    from jobpulse.job_notion_sync import build_create_payload
    from jobpulse.models.application_models import JobListing

    job = JobListing(
        job_id="abc", title="Data Scientist", company="Barclays",
        platform="linkedin", url="https://linkedin.com/jobs/123",
        salary_min=30000, salary_max=35000, location="London",
        remote=False, seniority="junior",
        required_skills=["python", "sql"],
        description_raw="...", ats_platform="greenhouse",
        found_at=datetime(2026, 3, 28, 7, 0, 0),
    )
    payload = build_create_payload(job, "fake_db_id")

    props = payload["properties"]
    assert props["Company"]["title"][0]["text"]["content"] == "Barclays"
    assert props["Role"]["rich_text"][0]["text"]["content"] == "Data Scientist"
    assert props["Platform"]["select"]["name"] == "LinkedIn"
    assert props["Status"]["select"]["name"] == "Found"
    assert props["Seniority"]["select"]["name"] == "Junior"
    assert props["Remote"]["checkbox"] is False
    assert props["JD URL"]["url"] == "https://linkedin.com/jobs/123"


def test_build_update_payload_applied():
    """Update payload for Applied status includes date and follow-up."""
    from jobpulse.job_notion_sync import build_update_payload

    payload = build_update_payload(
        status="Applied",
        ats_score=94.5,
        match_tier="auto",
        matched_projects=["Velox AI", "90 Days ML"],
        applied_date=date(2026, 3, 28),
        follow_up_date=date(2026, 4, 4),
        notes="Auto-applied. ATS: 94.5%",
    )
    props = payload["properties"]
    assert props["Status"]["select"]["name"] == "Applied"
    assert props["ATS Score"]["number"] == 94.5
    assert props["Applied Date"]["date"]["start"] == "2026-03-28"
    assert props["Follow Up Date"]["date"]["start"] == "2026-04-04"


def test_platform_display_name():
    """Platform names are capitalised for Notion select."""
    from jobpulse.job_notion_sync import platform_display

    assert platform_display("linkedin") == "LinkedIn"
    assert platform_display("indeed") == "Indeed"
    assert platform_display("totaljobs") == "TotalJobs"
    assert platform_display("glassdoor") == "Glassdoor"
    assert platform_display("reed") == "Reed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_job_notion_sync.py -v
```

- [ ] **Step 3: Implement Notion sync**

```python
# jobpulse/job_notion_sync.py
"""Notion sync for Job Tracker database — creates and updates application rows."""

import json
import subprocess
from datetime import date, datetime

from jobpulse.config import NOTION_API_KEY, NOTION_APPLICATIONS_DB_ID
from jobpulse.models.application_models import JobListing, ApplicationRecord
from shared.logging_config import get_logger

logger = get_logger(__name__)


PLATFORM_NAMES = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "reed": "Reed",
    "totaljobs": "TotalJobs",
    "glassdoor": "Glassdoor",
}

SENIORITY_NAMES = {
    "intern": "Intern",
    "graduate": "Graduate",
    "junior": "Junior",
    "mid": "Mid",
}

ATS_PLATFORM_NAMES = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "workday": "Workday",
    "smartrecruiters": "SmartRecruiters",
    "icims": "iCIMS",
}


def platform_display(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.title())


def _notion_api(method: str, endpoint: str, data: dict = None) -> dict:
    """Call Notion API via curl (consistent with existing notion_agent.py pattern)."""
    cmd = ["curl", "-s", "-X", method,
           f"https://api.notion.com/v1{endpoint}",
           "-H", f"Authorization: Bearer {NOTION_API_KEY}",
           "-H", "Content-Type: application/json",
           "-H", "Notion-Version: 2022-06-28"]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        logger.error("Notion API error: %s", e)
        return {}


def build_create_payload(job: JobListing, db_id: str) -> dict:
    """Build Notion create page payload with all columns."""
    salary_text = ""
    if job.salary_min and job.salary_max:
        salary_text = f"£{job.salary_min:,.0f} - £{job.salary_max:,.0f}"
    elif job.salary_min:
        salary_text = f"£{job.salary_min:,.0f}+"

    props = {
        "Company": {"title": [{"text": {"content": job.company}}]},
        "Role": {"rich_text": [{"text": {"content": job.title}}]},
        "Platform": {"select": {"name": platform_display(job.platform)}},
        "Status": {"select": {"name": "Found"}},
        "Salary": {"rich_text": [{"text": {"content": salary_text}}]},
        "Location": {"rich_text": [{"text": {"content": job.location}}]},
        "Remote": {"checkbox": job.remote},
        "Found Date": {"date": {"start": job.found_at.strftime("%Y-%m-%d")}},
        "JD URL": {"url": job.url},
    }

    if job.seniority:
        props["Seniority"] = {"select": {"name": SENIORITY_NAMES.get(job.seniority, job.seniority.title())}}

    if job.ats_platform:
        props["ATS Platform"] = {"select": {"name": ATS_PLATFORM_NAMES.get(job.ats_platform, job.ats_platform.title())}}

    return {"parent": {"database_id": db_id}, "properties": props}


def build_update_payload(status: str = None, ats_score: float = None,
                         match_tier: str = None, matched_projects: list[str] = None,
                         applied_date: date = None, follow_up_date: date = None,
                         notes: str = None, ats_platform: str = None) -> dict:
    """Build Notion update page payload with only changed fields."""
    props = {}

    if status:
        props["Status"] = {"select": {"name": status}}
    if ats_score is not None:
        props["ATS Score"] = {"number": ats_score}
    if match_tier:
        tier_names = {"auto": "Auto-apply", "review": "Review", "skip": "Skipped"}
        props["Match Tier"] = {"select": {"name": tier_names.get(match_tier, match_tier)}}
    if matched_projects:
        props["Matched Projects"] = {"multi_select": [{"name": p} for p in matched_projects]}
    if applied_date:
        props["Applied Date"] = {"date": {"start": applied_date.isoformat()}}
    if follow_up_date:
        props["Follow Up Date"] = {"date": {"start": follow_up_date.isoformat()}}
    if notes:
        props["Notes"] = {"rich_text": [{"text": {"content": notes[:2000]}}]}
    if ats_platform:
        props["ATS Platform"] = {"select": {"name": ATS_PLATFORM_NAMES.get(ats_platform, ats_platform.title())}}

    return {"properties": props}


def create_application_page(job: JobListing) -> str | None:
    """Create a new page in the Job Tracker database. Returns page ID."""
    if not NOTION_APPLICATIONS_DB_ID:
        logger.warning("NOTION_APPLICATIONS_DB_ID not set — skipping Notion sync")
        return None

    payload = build_create_payload(job, NOTION_APPLICATIONS_DB_ID)
    result = _notion_api("POST", "/pages", payload)
    page_id = result.get("id")

    if page_id:
        logger.info("Created Notion page for %s at %s: %s", job.title, job.company, page_id)
    else:
        logger.error("Failed to create Notion page: %s", result.get("message", "unknown error"))

    return page_id


def update_application_page(page_id: str, **kwargs) -> bool:
    """Update an existing application page in Notion."""
    if not page_id:
        return False

    payload = build_update_payload(**kwargs)
    result = _notion_api("PATCH", f"/pages/{page_id}", payload)
    return "id" in result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_job_notion_sync.py -v
```

Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_notion_sync.py tests/test_job_notion_sync.py
git commit -m "feat(jobs): Task 10 — Notion sync for Job Tracker database"
```

---

## Task 11: Telegram Intents + Dispatcher Wiring

**Files:**
- Modify: `jobpulse/command_router.py`
- Modify: `jobpulse/dispatcher.py`
- Modify: `jobpulse/telegram_bots.py`
- Modify: `jobpulse/multi_bot_listener.py`
- Modify: `data/intent_examples.json`

- [ ] **Step 1: Add Intent enum values to command_router.py**

Add after `STOP = "stop"` (line 52 of `jobpulse/command_router.py`):

```python
    SHOW_JOBS = "show_jobs"
    APPROVE_JOBS = "approve_jobs"
    REJECT_JOB = "reject_job"
    JOB_STATS = "job_stats"
    SEARCH_CONFIG = "search_config"
    PAUSE_JOBS = "pause_jobs"
    RESUME_JOBS = "resume_jobs"
    JOB_DETAIL = "job_detail"
```

- [ ] **Step 2: Add regex patterns for job intents**

Add these patterns to the PATTERNS list in `jobpulse/command_router.py`, BEFORE the BRIEFING pattern (around line 179):

```python
    # Job Autopilot
    (Intent.APPROVE_JOBS, [
        r"^apply\s+([\d,\s\-]+|all)\s*$",
        r"^approve\s+([\d,\s\-]+|all)\s*$",
    ]),
    (Intent.REJECT_JOB, [
        r"^(reject|skip|pass on|pass)\s+(\d+)\s*$",
    ]),
    (Intent.JOB_DETAIL, [
        r"^job\s+(\d+)\s*$",
        r"^details?\s+(\d+)\s*$",
    ]),
    (Intent.JOB_STATS, [
        r"(job|application|apply|applied)\s*(stats?|statistics|numbers|metrics|count)",
        r"how many (applied|applications|jobs)",
    ]),
    (Intent.SEARCH_CONFIG, [
        r"^search:\s*(.+)",
        r"^job search:\s*(.+)",
    ]),
    (Intent.PAUSE_JOBS, [
        r"^(pause|stop)\s*(jobs?|applying|autopilot|auto.?pilot)\s*$",
    ]),
    (Intent.RESUME_JOBS, [
        r"^(resume|start|unpause)\s*(jobs?|applying|autopilot|auto.?pilot)\s*$",
    ]),
    (Intent.SHOW_JOBS, [
        r"^(jobs?|show jobs?|new jobs?|available jobs?|what.?s available)\s*$",
        r"(pending|review)\s*jobs?\s*$",
    ]),
```

- [ ] **Step 3: Add dispatcher handlers**

Add to the `handlers` dict in `jobpulse/dispatcher.py` (after the existing entries around line 115):

```python
        Intent.SHOW_JOBS: _handle_show_jobs,
        Intent.APPROVE_JOBS: _handle_approve_jobs,
        Intent.REJECT_JOB: _handle_reject_job,
        Intent.JOB_STATS: _handle_job_stats,
        Intent.SEARCH_CONFIG: _handle_search_config,
        Intent.PAUSE_JOBS: _handle_pause_jobs,
        Intent.RESUME_JOBS: _handle_resume_jobs,
        Intent.JOB_DETAIL: _handle_job_detail,
```

Add handler functions at the bottom of `jobpulse/dispatcher.py`:

```python
def _handle_show_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_db import JobDB
    db = JobDB()
    pending = db.get_applications_by_status("Pending Approval")
    ready = db.get_applications_by_status("Ready")
    jobs = pending + ready

    if not jobs:
        return "No jobs pending review. Check 'job stats' for today's numbers."

    lines = [f"📋 {len(jobs)} jobs ready for review:\n"]
    for i, j in enumerate(jobs[:15], 1):
        score = j.get("ats_score", 0)
        lines.append(f"{i}. {j['title']} — {j['company']} ({j['platform']})")
        lines.append(f"   ATS: {score}% | {j.get('location', 'UK')}")
    lines.append(f"\nReply: \"apply 1,3,5\" or \"apply all\" or \"reject 2\"")
    return "\n".join(lines)


def _handle_approve_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import approve_jobs
    return approve_jobs(cmd.args)


def _handle_reject_job(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import reject_job
    return reject_job(cmd.args)


def _handle_job_stats(cmd: ParsedCommand) -> str:
    from jobpulse.job_db import JobDB
    db = JobDB()
    stats = db.get_today_stats()
    return (f"📊 Job Stats Today\n"
            f"Found: {stats['found']}\n"
            f"Applied: {stats['applied']}\n"
            f"Skipped: {stats['skipped']}\n"
            f"Avg ATS: {stats['avg_ats']}%")


def _handle_search_config(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import update_search_config
    return update_search_config(cmd.args)


def _handle_pause_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import set_autopilot_paused
    set_autopilot_paused(True)
    return "⏸️ Job Autopilot paused. No new applications until you 'resume jobs'."


def _handle_resume_jobs(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import set_autopilot_paused
    set_autopilot_paused(False)
    return "▶️ Job Autopilot resumed. Next scan will run on schedule."


def _handle_job_detail(cmd: ParsedCommand) -> str:
    from jobpulse.job_autopilot import get_job_detail
    return get_job_detail(cmd.args)
```

- [ ] **Step 4: Add Jobs bot to telegram_bots.py**

Add to imports in `jobpulse/telegram_bots.py`:

```python
from jobpulse.config import TELEGRAM_JOBS_BOT_TOKEN
```

Add JOBS_INTENTS set after RESEARCH_INTENTS:

```python
JOBS_INTENTS = {
    "show_jobs", "approve_jobs", "reject_job", "job_stats",
    "search_config", "pause_jobs", "resume_jobs", "job_detail",
}
```

Add send_jobs function after send_alert:

```python
def send_jobs(text: str) -> bool:
    """Send via jobs bot. Falls back to main if not configured."""
    token = TELEGRAM_JOBS_BOT_TOKEN or TELEGRAM_BOT_TOKEN
    return _send(token, text)
```

Update send_for_intent to include jobs routing:

```python
def send_for_intent(intent: str, text: str) -> bool:
    if intent in BUDGET_INTENTS:
        return send_budget(text)
    if intent in RESEARCH_INTENTS:
        return send_research(text)
    if intent in JOBS_INTENTS:
        return send_jobs(text)
    return send_main(text)
```

Add HELP_JOBS text and update get_help_for_bot to include "jobs": HELP_JOBS.

```python
HELP_JOBS = """💼 JOBS BOT — Job Autopilot

📋 REVIEW:
  "jobs" — show pending review jobs
  "job 3" — full details for job #3
  "apply 1,3,5" — approve specific jobs
  "apply all" — approve all pending
  "reject 2" — skip a job

📊 STATS:
  "job stats" — today's numbers

🔍 SEARCH:
  "search: add title NLP Engineer"
  "search: exclude company X"
  "search: remove title Y"

⏯️ CONTROL:
  "pause jobs" — stop autopilot
  "resume jobs" — restart autopilot

Runs on schedule: 7am, 10am, 1pm, 4:30pm, 7pm, 2am
Auto-applies 90%+ ATS. Sends 82-89% for your review."""
```

- [ ] **Step 5: Update multi_bot_listener.py**

Import JOBS_INTENTS from telegram_bots and add the jobs bot polling thread. Add processing time estimates for job intents.

In the import section add:

```python
from jobpulse.telegram_bots import JOBS_INTENTS
from jobpulse.config import TELEGRAM_JOBS_BOT_TOKEN
```

Add to the INTENT_ESTIMATES dict:

```python
                    "show_jobs": "Loading pending jobs... ~3s",
                    "approve_jobs": "Submitting applications... ~30s",
                    "reject_job": "Skipping job... ~2s",
                    "job_stats": "Calculating stats... ~3s",
                    "search_config": "Updating search config... ~2s",
                    "pause_jobs": "Pausing autopilot... ~1s",
                    "resume_jobs": "Resuming autopilot... ~1s",
                    "job_detail": "Loading job details... ~3s",
```

Add jobs bot thread start alongside existing bot threads (follow the pattern for budget/research bots).

- [ ] **Step 6: Add intent examples to data/intent_examples.json**

Add ~5 examples per new intent (40 total) to the existing JSON file. Follow the existing format.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/command_router.py jobpulse/dispatcher.py jobpulse/telegram_bots.py jobpulse/multi_bot_listener.py data/intent_examples.json
git commit -m "feat(jobs): Task 11 — Telegram intents, dispatcher wiring, Jobs bot"
```

---

## Task 12: Job Scanner (Platform Scrapers)

**Files:**
- Create: `jobpulse/job_scanner.py`

This task has no unit tests — scrapers depend on live HTTP responses. Integration testing happens in Task 15.

- [ ] **Step 1: Implement job scanner**

```python
# jobpulse/job_scanner.py
"""Job Scanner — scrapes 5 job platforms for listings matching search config.

Platforms:
  - Reed: official API (httpx, free key)
  - Indeed: public search scraping (httpx)
  - LinkedIn: Playwright with authenticated session
  - TotalJobs: public search scraping (httpx)
  - Glassdoor: Playwright with authenticated session

Anti-detection: randomized delays, rotating user agents, rate limits.
"""

import json
import random
import time
import hashlib
from datetime import datetime
from pathlib import Path

import httpx

from jobpulse.config import DATA_DIR, REED_API_KEY
from jobpulse.models.application_models import SearchConfig
from shared.logging_config import get_logger

logger = get_logger(__name__)

SEARCH_CONFIG_PATH = DATA_DIR / "job_search_config.json"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]


def load_search_config() -> SearchConfig:
    """Load search config from JSON file."""
    if SEARCH_CONFIG_PATH.exists():
        data = json.loads(SEARCH_CONFIG_PATH.read_text())
        return SearchConfig(**data)
    return SearchConfig(titles=["Data Scientist", "ML Engineer"])


def save_search_config(config: SearchConfig):
    """Save search config to JSON file."""
    SEARCH_CONFIG_PATH.write_text(config.model_dump_json(indent=2))


def _random_delay(min_s: float = 2.0, max_s: float = 8.0):
    """Random delay for anti-detection."""
    time.sleep(random.uniform(min_s, max_s))


def _make_job_id(url: str) -> str:
    return hashlib.sha256(url.strip().lower().encode()).hexdigest()


# ── Reed API (official, free key) ──

def scan_reed(config: SearchConfig) -> list[dict]:
    """Scan Reed.co.uk via their official API."""
    if not REED_API_KEY:
        logger.warning("No REED_API_KEY — skipping Reed scan")
        return []

    results = []
    for title in config.titles:
        try:
            resp = httpx.get(
                "https://www.reed.co.uk/api/1.0/search",
                params={
                    "keywords": title,
                    "locationName": config.location,
                    "distanceFromLocation": 50,
                    "minimumSalary": config.salary_min,
                    "resultsToTake": 25,
                },
                auth=(REED_API_KEY, ""),
                timeout=15,
            )
            resp.raise_for_status()
            jobs = resp.json().get("results", [])

            for j in jobs:
                job_url = f"https://www.reed.co.uk/jobs/{j.get('jobId', '')}"
                results.append({
                    "title": j.get("jobTitle", ""),
                    "company": j.get("employerName", ""),
                    "url": job_url,
                    "location": j.get("locationName", ""),
                    "salary_min": j.get("minimumSalary"),
                    "salary_max": j.get("maximumSalary"),
                    "description": j.get("jobDescription", "")[:3000],
                    "platform": "reed",
                    "job_id": _make_job_id(job_url),
                })

            _random_delay(1, 3)

        except Exception as e:
            logger.error("Reed scan error for '%s': %s", title, e)

    logger.info("Reed: found %d listings", len(results))
    return results


# ── Indeed (public search scraping) ──

def scan_indeed(config: SearchConfig) -> list[dict]:
    """Scan Indeed.co.uk via public search pages."""
    results = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}

    for title in config.titles[:5]:  # Limit to avoid rate limiting
        try:
            resp = httpx.get(
                "https://uk.indeed.com/jobs",
                params={
                    "q": title,
                    "l": config.location,
                    "fromage": 1,  # last 24 hours
                    "limit": 20,
                },
                headers=headers, timeout=15, follow_redirects=True,
            )

            # Parse results from HTML (basic extraction)
            # Indeed serves JSON-LD or embedded data we can extract
            # For now, return raw page for JD analyzer to process
            # Full implementation requires parsing Indeed's HTML structure
            logger.info("Indeed: fetched search page for '%s' (%d bytes)", title, len(resp.text))
            _random_delay()

        except Exception as e:
            logger.error("Indeed scan error for '%s': %s", title, e)

    logger.info("Indeed: found %d listings", len(results))
    return results


# ── LinkedIn (Playwright — requires authenticated session) ──

def scan_linkedin(config: SearchConfig) -> list[dict]:
    """Scan LinkedIn jobs via Playwright with saved session."""
    results = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping LinkedIn scan")
        return []

    session_dir = DATA_DIR / "linkedin_session"
    if not session_dir.exists():
        logger.warning("No LinkedIn session at %s — run 'playwright codegen linkedin.com' to create one", session_dir)
        return []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch_persistent_context(
                str(session_dir), headless=True,
            )
            page = browser.pages[0] if browser.pages else browser.new_page()

            for title in config.titles[:5]:
                search_url = (
                    f"https://www.linkedin.com/jobs/search/?"
                    f"keywords={title.replace(' ', '%20')}"
                    f"&location={config.location.replace(' ', '%20')}"
                    f"&f_TPR=r86400"  # past 24 hours
                    f"&f_E=1%2C2"  # entry level + internship
                )
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                _random_delay(3, 6)

                # Extract job cards
                cards = page.query_selector_all(".job-card-container")
                for card in cards[:20]:
                    try:
                        title_el = card.query_selector(".job-card-list__title")
                        company_el = card.query_selector(".job-card-container__primary-description")
                        link_el = card.query_selector("a[href*='/jobs/view/']")

                        if title_el and company_el and link_el:
                            job_url = link_el.get_attribute("href") or ""
                            if job_url.startswith("/"):
                                job_url = f"https://www.linkedin.com{job_url}"

                            results.append({
                                "title": title_el.inner_text().strip(),
                                "company": company_el.inner_text().strip(),
                                "url": job_url.split("?")[0],
                                "location": config.location,
                                "description": "",  # fetched later on detail page
                                "platform": "linkedin",
                                "job_id": _make_job_id(job_url.split("?")[0]),
                            })
                    except Exception:
                        continue

                _random_delay(2, 5)

            browser.close()

    except Exception as e:
        logger.error("LinkedIn scan error: %s", e)

    logger.info("LinkedIn: found %d listings", len(results))
    return results


# ── TotalJobs (public search scraping) ──

def scan_totaljobs(config: SearchConfig) -> list[dict]:
    """Scan TotalJobs via public search pages."""
    results = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}

    for title in config.titles[:5]:
        try:
            resp = httpx.get(
                "https://www.totaljobs.com/jobs",
                params={
                    "keywords": title,
                    "location": config.location,
                    "postedWithin": 1,
                },
                headers=headers, timeout=15, follow_redirects=True,
            )
            logger.info("TotalJobs: fetched search for '%s' (%d bytes)", title, len(resp.text))
            _random_delay()

        except Exception as e:
            logger.error("TotalJobs scan error for '%s': %s", title, e)

    logger.info("TotalJobs: found %d listings", len(results))
    return results


# ── Glassdoor (Playwright — requires session) ──

def scan_glassdoor(config: SearchConfig) -> list[dict]:
    """Scan Glassdoor via Playwright with saved session."""
    results = []
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping Glassdoor scan")
        return []

    session_dir = DATA_DIR / "glassdoor_session"
    if not session_dir.exists():
        logger.warning("No Glassdoor session — skipping")
        return []

    logger.info("Glassdoor: found %d listings", len(results))
    return results


# ── Orchestrator ──

PLATFORM_SCANNERS = {
    "reed": scan_reed,
    "indeed": scan_indeed,
    "linkedin": scan_linkedin,
    "totaljobs": scan_totaljobs,
    "glassdoor": scan_glassdoor,
}

ALL_PLATFORMS = list(PLATFORM_SCANNERS.keys())
QUICK_PLATFORMS = ["linkedin", "indeed", "reed"]
SLOW_PLATFORMS = ["glassdoor", "totaljobs"]


def scan_platforms(platforms: list[str] | None = None) -> list[dict]:
    """Scan specified platforms (or all) and return raw job listings."""
    config = load_search_config()
    platforms = platforms or ALL_PLATFORMS

    all_results = []
    for name in platforms:
        scanner = PLATFORM_SCANNERS.get(name)
        if scanner:
            try:
                results = scanner(config)
                all_results.extend(results)
            except Exception as e:
                logger.error("Scanner %s failed: %s", name, e)

    logger.info("Total scan: %d listings from %s", len(all_results), platforms)
    return all_results
```

- [ ] **Step 2: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "feat(jobs): Task 12 — job scanner with Reed API + LinkedIn/Indeed/TotalJobs/Glassdoor scrapers"
```

---

## Task 13: ATS Adapters (Base + Stubs)

**Files:**
- Create: `jobpulse/ats_adapters/__init__.py`
- Create: `jobpulse/ats_adapters/base.py`
- Create: `jobpulse/ats_adapters/linkedin.py`
- Create: `jobpulse/ats_adapters/indeed.py`
- Create: `jobpulse/ats_adapters/greenhouse.py`
- Create: `jobpulse/ats_adapters/lever.py`
- Create: `jobpulse/ats_adapters/workday.py`
- Create: `jobpulse/ats_adapters/generic.py`
- Test: `tests/test_applicator.py`

- [ ] **Step 1: Write failing tests for applicator tier logic**

```python
# tests/test_applicator.py
"""Tests for applicator tier logic and adapter selection."""

import pytest


def test_classify_tier_auto_easy():
    from jobpulse.applicator import classify_action
    action = classify_action(ats_score=95.0, easy_apply=True)
    assert action == "auto_submit"


def test_classify_tier_auto_complex():
    from jobpulse.applicator import classify_action
    action = classify_action(ats_score=92.0, easy_apply=False)
    assert action == "auto_submit_with_preview"


def test_classify_tier_review():
    from jobpulse.applicator import classify_action
    action = classify_action(ats_score=85.0, easy_apply=True)
    assert action == "send_for_review"


def test_classify_tier_skip():
    from jobpulse.applicator import classify_action
    action = classify_action(ats_score=78.0, easy_apply=False)
    assert action == "skip"


def test_select_adapter():
    from jobpulse.applicator import select_adapter
    assert select_adapter("greenhouse").name == "greenhouse"
    assert select_adapter("lever").name == "lever"
    assert select_adapter("workday").name == "workday"
    assert select_adapter(None).name == "generic"
    assert select_adapter("unknown_ats").name == "generic"


def test_work_auth_answers():
    from jobpulse.applicator import WORK_AUTH
    assert WORK_AUTH["requires_sponsorship"] is False
    assert "Graduate Visa" in WORK_AUTH["visa_status"]
    assert WORK_AUTH["right_to_work_uk"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_applicator.py -v
```

- [ ] **Step 3: Create adapter package and base class**

```python
# jobpulse/ats_adapters/__init__.py
"""ATS Adapter registry for job application form automation."""

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.ats_adapters.linkedin import LinkedInAdapter
from jobpulse.ats_adapters.indeed import IndeedAdapter
from jobpulse.ats_adapters.greenhouse import GreenhouseAdapter
from jobpulse.ats_adapters.lever import LeverAdapter
from jobpulse.ats_adapters.workday import WorkdayAdapter
from jobpulse.ats_adapters.generic import GenericAdapter

ADAPTERS: dict[str, BaseATSAdapter] = {
    "linkedin": LinkedInAdapter(),
    "indeed": IndeedAdapter(),
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "workday": WorkdayAdapter(),
    "generic": GenericAdapter(),
}


def get_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Get the appropriate adapter for an ATS platform."""
    if ats_platform and ats_platform in ADAPTERS:
        return ADAPTERS[ats_platform]
    return ADAPTERS["generic"]
```

```python
# jobpulse/ats_adapters/base.py
"""Base class for ATS form adapters."""

from abc import ABC, abstractmethod
from pathlib import Path
from shared.logging_config import get_logger

logger = get_logger(__name__)


class BaseATSAdapter(ABC):
    """Base adapter for filling and submitting job application forms."""

    name: str = "base"

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Returns True if this adapter handles this URL."""
        ...

    @abstractmethod
    def fill_and_submit(self, url: str, cv_path: Path, cover_letter_path: Path | None,
                        profile: dict, custom_answers: dict) -> dict:
        """Fill form and submit. Returns {'success': bool, 'screenshot': Path|None, 'error': str|None}."""
        ...
```

- [ ] **Step 4: Create 6 adapter stubs**

Each adapter follows the same pattern. Example for greenhouse:

```python
# jobpulse/ats_adapters/greenhouse.py
"""Greenhouse ATS adapter."""

from pathlib import Path
from jobpulse.ats_adapters.base import BaseATSAdapter, logger


class GreenhouseAdapter(BaseATSAdapter):
    name = "greenhouse"

    def detect(self, url: str) -> bool:
        return "greenhouse.io" in url.lower() or "boards.greenhouse" in url.lower()

    def fill_and_submit(self, url: str, cv_path: Path, cover_letter_path: Path | None,
                        profile: dict, custom_answers: dict) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Fill standard Greenhouse fields
                for field, value in [
                    ("#first_name", profile.get("first_name", "")),
                    ("#last_name", profile.get("last_name", "")),
                    ("#email", profile.get("email", "")),
                    ("#phone", profile.get("phone", "")),
                ]:
                    el = page.query_selector(field)
                    if el:
                        el.fill(value)

                # Upload CV
                cv_input = page.query_selector("input[type='file']")
                if cv_input and cv_path:
                    cv_input.set_input_files(str(cv_path))

                # Screenshot before submit
                screenshot_path = cv_path.parent / "screenshot.png" if cv_path else None
                if screenshot_path:
                    page.screenshot(path=str(screenshot_path))

                # Submit
                submit = page.query_selector("input[type='submit'], button[type='submit']")
                if submit:
                    submit.click()
                    page.wait_for_timeout(3000)

                browser.close()
                return {"success": True, "screenshot": screenshot_path, "error": None}

        except Exception as e:
            logger.error("Greenhouse adapter error: %s", e)
            return {"success": False, "screenshot": None, "error": str(e)}
```

Create similar stubs for `linkedin.py`, `indeed.py`, `lever.py`, `workday.py`, and `generic.py` — each with appropriate `name`, `detect()`, and `fill_and_submit()` methods.

- [ ] **Step 5: Implement applicator**

```python
# jobpulse/applicator.py
"""Applicator — orchestrates job application submission via ATS adapters.

Tier logic:
  90%+ ATS + Easy Apply → auto-submit, notify after
  90%+ ATS + Complex    → auto-submit with 15-min preview window
  82-89% ATS            → send to Telegram for review
  <82% ATS              → skip silently
"""

from pathlib import Path

from jobpulse.ats_adapters import get_adapter
from jobpulse.ats_adapters.base import BaseATSAdapter
from shared.logging_config import get_logger

logger = get_logger(__name__)

WORK_AUTH = {
    "requires_sponsorship": False,
    "visa_status": "Student Visa (converting to Graduate Visa from 9 May 2026, valid 2 years)",
    "right_to_work_uk": True,
    "notice_period": "Available immediately",
    "salary_expectation": "27,000 - 32,000",
}

PROFILE = {
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
    """Determine what action to take based on ATS score and application type."""
    if ats_score >= 90:
        return "auto_submit" if easy_apply else "auto_submit_with_preview"
    elif ats_score >= 82:
        return "send_for_review"
    return "skip"


def select_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Select the right ATS adapter."""
    return get_adapter(ats_platform)


def apply_job(url: str, ats_platform: str | None, cv_path: Path,
              cover_letter_path: Path | None, custom_answers: dict | None = None) -> dict:
    """Submit a job application via the appropriate adapter.

    Returns {'success': bool, 'screenshot': Path|None, 'error': str|None}
    """
    adapter = select_adapter(ats_platform)
    answers = {**WORK_AUTH, **(custom_answers or {})}

    logger.info("Applying via %s adapter to %s", adapter.name, url[:80])
    return adapter.fill_and_submit(url, cv_path, cover_letter_path, PROFILE, answers)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_applicator.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add jobpulse/ats_adapters/ jobpulse/applicator.py tests/test_applicator.py
git commit -m "feat(jobs): Task 13 — ATS adapters (base + 6 platforms) + applicator tier logic"
```

---

## Task 14: Job Autopilot Orchestrator

**Files:**
- Create: `jobpulse/job_autopilot.py`

- [ ] **Step 1: Implement the orchestrator**

```python
# jobpulse/job_autopilot.py
"""Job Autopilot — top-level orchestrator for the job application pipeline.

Runs the full pipeline per scan window:
  Scan → Analyze → Dedup → Match → Tailor → Cover Letter → Score → Apply → Notify
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path

from jobpulse.config import DATA_DIR, JOB_AUTOPILOT_ENABLED, JOB_AUTOPILOT_MAX_DAILY
from jobpulse.models.application_models import ApplicationStatus
from jobpulse.job_scanner import scan_platforms, load_search_config, save_search_config, ALL_PLATFORMS, QUICK_PLATFORMS, SLOW_PLATFORMS
from jobpulse.jd_analyzer import analyze_jd, generate_job_id
from jobpulse.job_deduplicator import deduplicate
from jobpulse.job_db import JobDB
from jobpulse.github_matcher import pick_top_projects, fetch_and_cache_repos
from jobpulse.cv_tailor import generate_tailored_cv, determine_match_tier
from jobpulse.cover_letter_agent import generate_cover_letter
from jobpulse.job_notion_sync import create_application_page, update_application_page
from jobpulse.applicator import classify_action, apply_job
from jobpulse.telegram_bots import send_jobs
from jobpulse.process_logger import ProcessTrail
from shared.logging_config import get_logger

logger = get_logger(__name__)

PAUSE_FILE = DATA_DIR / "job_autopilot_paused.txt"
PENDING_REVIEW_FILE = DATA_DIR / "pending_review_jobs.json"


def is_paused() -> bool:
    return PAUSE_FILE.exists()


def set_autopilot_paused(paused: bool):
    if paused:
        PAUSE_FILE.write_text("paused")
    elif PAUSE_FILE.exists():
        PAUSE_FILE.unlink()


def run_scan_window(platforms: list[str] | None = None):
    """Execute one scan window — full pipeline."""
    if not JOB_AUTOPILOT_ENABLED or is_paused():
        logger.info("Job Autopilot is %s — skipping", "paused" if is_paused() else "disabled")
        return

    db = JobDB()
    trail = ProcessTrail("job_autopilot", f"scan_{datetime.now().strftime('%H%M')}")

    platforms = platforms or ALL_PLATFORMS

    # Check daily cap
    today_stats = db.get_today_stats()
    if today_stats["applied"] >= JOB_AUTOPILOT_MAX_DAILY:
        logger.info("Daily cap reached (%d/%d) — skipping scan", today_stats["applied"], JOB_AUTOPILOT_MAX_DAILY)
        send_jobs(f"⏸️ Daily cap reached ({today_stats['applied']}/{JOB_AUTOPILOT_MAX_DAILY}). Resuming tomorrow.")
        return

    # 1. Scan
    with trail.step("scan", f"Scanning {', '.join(platforms)}") as s:
        raw_listings = scan_platforms(platforms)
        s["output"] = f"Found {len(raw_listings)} raw listings"

    if not raw_listings:
        trail.finalize("No listings found")
        return

    # 2. Analyze + build JobListing objects
    with trail.step("analyze", f"Analyzing {len(raw_listings)} JDs") as s:
        analyzed = []
        for raw in raw_listings:
            try:
                job = analyze_jd(
                    url=raw["url"], title=raw["title"], company=raw["company"],
                    platform=raw["platform"], jd_text=raw.get("description", ""),
                )
                analyzed.append(job)
            except Exception as e:
                logger.error("Analysis failed for %s: %s", raw.get("url", "?"), e)
        s["output"] = f"Analyzed {len(analyzed)} JDs"

    # 3. Dedup
    with trail.step("dedup", f"Deduplicating {len(analyzed)} listings") as s:
        new_jobs = deduplicate(analyzed, db)
        s["output"] = f"{len(new_jobs)} new, {len(analyzed) - len(new_jobs)} duplicates"

    if not new_jobs:
        trail.finalize("All listings were duplicates")
        send_jobs(f"📋 Scan complete: {len(analyzed)} found, all duplicates. Nothing new.")
        return

    # 4. Get GitHub repos (cached)
    repos = fetch_and_cache_repos()

    # Process each job
    auto_applied = []
    review_batch = []
    skipped = []

    for job in new_jobs:
        try:
            # Save listing
            db.save_listing(job)
            db.save_application(job_id=job.job_id, status="Analyzing")

            # Create Notion page
            notion_id = create_application_page(job)

            # Match GitHub projects
            matched = pick_top_projects(repos, job.required_skills, job.preferred_skills, top_n=4)
            matched_names = [m["name"] for m in matched]

            # Tailor CV
            cv_path, ats = generate_tailored_cv(job, matched_names)
            tier = determine_match_tier(ats.total)

            # Generate cover letter
            cl_path = generate_cover_letter(job, job.required_skills[:5], matched_names)

            # Update DB
            db.save_application(
                job_id=job.job_id, status="Ready",
                ats_score=ats.total, match_tier=tier,
                matched_projects=matched_names,
                cv_path=str(cv_path) if cv_path else None,
                cover_letter_path=str(cl_path) if cl_path else None,
                notion_page_id=notion_id,
            )

            # Update Notion
            if notion_id:
                update_application_page(
                    notion_id, status="Ready", ats_score=ats.total,
                    match_tier=tier, matched_projects=matched_names,
                    notes=f"ATS: {ats.total}%. Missing: {', '.join(ats.missing_keywords[:5])}",
                    ats_platform=job.ats_platform,
                )

            # Classify action
            if tier == "skip":
                db.update_status(job.job_id, "Skipped")
                if notion_id:
                    update_application_page(notion_id, status="Skipped",
                                            notes=f"ATS {ats.total}% < 82%. Skipped.")
                skipped.append(job)
            elif tier == "auto":
                action = classify_action(ats.total, job.easy_apply)
                if action in ("auto_submit", "auto_submit_with_preview"):
                    result = apply_job(job.url, job.ats_platform, cv_path, cl_path)
                    if result.get("success"):
                        now = datetime.now()
                        db.save_application(
                            job_id=job.job_id, status="Applied",
                            ats_score=ats.total, match_tier=tier,
                            matched_projects=matched_names,
                            cv_path=str(cv_path) if cv_path else None,
                            cover_letter_path=str(cl_path) if cl_path else None,
                            applied_at=now,
                            follow_up_date=(now + timedelta(days=7)).date(),
                            notion_page_id=notion_id,
                        )
                        if notion_id:
                            update_application_page(
                                notion_id, status="Applied",
                                applied_date=now.date(),
                                follow_up_date=(now + timedelta(days=7)).date(),
                            )
                        auto_applied.append(job)
                    else:
                        review_batch.append(job)
                else:
                    review_batch.append(job)
            else:
                review_batch.append(job)

        except Exception as e:
            logger.error("Pipeline failed for %s at %s: %s", job.title, job.company, e)

    # Save review batch for Telegram approval
    if review_batch:
        pending = [{"job_id": j.job_id, "title": j.title, "company": j.company,
                     "platform": j.platform, "location": j.location,
                     "ats_score": db.get_application(j.job_id).get("ats_score", 0) if db.get_application(j.job_id) else 0}
                    for j in review_batch]
        PENDING_REVIEW_FILE.write_text(json.dumps(pending, indent=2))

        lines = [f"📋 {len(review_batch)} jobs ready for review (82-89% ATS):\n"]
        for i, j in enumerate(pending, 1):
            lines.append(f"{i}. {j['title']} — {j['company']} ({j['platform']})")
            lines.append(f"   ATS: {j['ats_score']}% | {j['location']}")
        lines.append(f"\nReply: \"apply 1,3,5\" or \"apply all\" or \"reject 2\"")
        send_jobs("\n".join(lines))

    # Send summary
    time_label = datetime.now().strftime("%-I:%M %p")
    summary = (
        f"📊 Job Autopilot ({time_label} scan)\n"
        f"Found: {len(raw_listings)} | New: {len(new_jobs)}\n"
        f"Auto-applied: {len(auto_applied)}\n"
        f"Ready for review: {len(review_batch)}\n"
        f"Skipped: {len(skipped)} (<82% match)"
    )
    send_jobs(summary)
    trail.finalize(summary)


def approve_jobs(args: str) -> str:
    """Approve specific jobs from the review batch."""
    if not PENDING_REVIEW_FILE.exists():
        return "No pending jobs to approve."

    pending = json.loads(PENDING_REVIEW_FILE.read_text())

    if args.strip().lower() == "all":
        indices = list(range(len(pending)))
    else:
        try:
            indices = [int(x.strip()) - 1 for x in args.split(",")]
        except ValueError:
            return "Invalid format. Use: apply 1,3,5 or apply all"

    db = JobDB()
    applied = 0
    for idx in indices:
        if 0 <= idx < len(pending):
            job_data = pending[idx]
            app = db.get_application(job_data["job_id"])
            if app:
                result = apply_job(
                    url=db.get_listing(job_data["job_id"])["url"],
                    ats_platform=db.get_listing(job_data["job_id"]).get("ats_platform"),
                    cv_path=Path(app["cv_path"]) if app.get("cv_path") else None,
                    cover_letter_path=Path(app["cover_letter_path"]) if app.get("cover_letter_path") else None,
                )
                if result.get("success"):
                    now = datetime.now()
                    db.save_application(
                        job_id=job_data["job_id"], status="Applied",
                        ats_score=app.get("ats_score", 0),
                        applied_at=now,
                        follow_up_date=(now + timedelta(days=7)).date(),
                        notion_page_id=app.get("notion_page_id"),
                    )
                    applied += 1

    return f"✅ Applied to {applied}/{len(indices)} jobs."


def reject_job(args: str) -> str:
    """Reject/skip a specific job from the review batch."""
    if not PENDING_REVIEW_FILE.exists():
        return "No pending jobs."

    pending = json.loads(PENDING_REVIEW_FILE.read_text())
    try:
        idx = int(args.strip()) - 1
    except ValueError:
        return "Invalid format. Use: reject 3"

    if 0 <= idx < len(pending):
        job_data = pending[idx]
        db = JobDB()
        db.update_status(job_data["job_id"], "Skipped")
        return f"⏭️ Skipped: {job_data['title']} at {job_data['company']}"
    return "Invalid job number."


def get_job_detail(args: str) -> str:
    """Get full details for a job from the pending review batch."""
    if not PENDING_REVIEW_FILE.exists():
        return "No pending jobs."

    pending = json.loads(PENDING_REVIEW_FILE.read_text())
    try:
        idx = int(args.strip()) - 1
    except ValueError:
        return "Invalid format. Use: job 3"

    if 0 <= idx < len(pending):
        job_data = pending[idx]
        db = JobDB()
        listing = db.get_listing(job_data["job_id"])
        app = db.get_application(job_data["job_id"])
        if listing:
            matched = json.loads(app.get("matched_projects", "[]")) if app else []
            return (
                f"📄 {listing['title']} at {listing['company']}\n"
                f"Platform: {listing['platform']}\n"
                f"Location: {listing['location']}\n"
                f"Salary: £{listing.get('salary_min', '?'):,.0f} - £{listing.get('salary_max', '?'):,.0f}\n"
                f"ATS: {app.get('ats_score', 0)}%\n"
                f"Matched projects: {', '.join(matched)}\n"
                f"URL: {listing['url']}"
            )
    return "Job not found."


def update_search_config(args: str) -> str:
    """Update search config from Telegram command."""
    config = load_search_config()

    if "add title" in args.lower():
        title = args.lower().replace("add title", "").strip()
        if title and title not in [t.lower() for t in config.titles]:
            config.titles.append(title.title())
            save_search_config(config)
            return f"✅ Added search title: {title.title()}"
        return f"Title '{title}' already exists."

    if "remove title" in args.lower():
        title = args.lower().replace("remove title", "").strip()
        config.titles = [t for t in config.titles if t.lower() != title]
        save_search_config(config)
        return f"✅ Removed search title: {title}"

    if "exclude company" in args.lower():
        company = args.lower().replace("exclude company", "").strip()
        if company and company not in [c.lower() for c in config.exclude_companies]:
            config.exclude_companies.append(company.title())
            save_search_config(config)
            return f"✅ Excluded company: {company.title()}"
        return f"Company '{company}' already excluded."

    return "Unknown search command. Try: search: add title X, search: exclude company X, search: remove title Y"


def check_follow_ups():
    """Check for applications due for follow-up. Called daily."""
    db = JobDB()
    due = db.get_follow_ups_due(date.today())
    if due:
        lines = [f"📬 {len(due)} follow-ups due today:\n"]
        for j in due:
            lines.append(f"• {j['title']} at {j['company']} (applied {j.get('follow_up_date', 'N/A')})")
        send_jobs("\n".join(lines))
```

- [ ] **Step 2: Commit**

```bash
git add jobpulse/job_autopilot.py
git commit -m "feat(jobs): Task 14 — Job Autopilot orchestrator with full pipeline + approval flow"
```

---

## Task 15: Cron Setup + Runner Integration

**Files:**
- Modify: `scripts/install_cron.py`
- Modify: `jobpulse/runner.py`

- [ ] **Step 1: Add scan window crons to install_cron.py**

Add these cron entries to the existing `install_cron.py`:

```python
# Job Autopilot scan windows
("0 7 * * *", "job-scan-7am", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window()\""),
("0 10 * * *", "job-scan-10am", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window(['linkedin', 'indeed', 'reed'])\""),
("0 13 * * *", "job-scan-1pm", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window()\""),
("30 16 * * *", "job-scan-430pm", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window(['linkedin', 'indeed', 'reed'])\""),
("0 19 * * *", "job-scan-7pm", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window()\""),
("0 2 * * *", "job-scan-overnight", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import run_scan_window; run_scan_window(['glassdoor', 'totaljobs'])\""),
# Follow-up check
("0 9 * * *", "job-follow-ups", f"cd {PROJECT_DIR} && python -c \"from jobpulse.job_autopilot import check_follow_ups; check_follow_ups()\""),
```

- [ ] **Step 2: Add runner commands**

Add to `jobpulse/runner.py` (follow existing pattern for briefing/weekly-report):

```python
@app.command()
def job_scan(platforms: str = None):
    """Run a manual job scan."""
    from jobpulse.job_autopilot import run_scan_window
    platform_list = platforms.split(",") if platforms else None
    run_scan_window(platform_list)

@app.command()
def job_stats():
    """Show today's job application stats."""
    from jobpulse.job_db import JobDB
    db = JobDB()
    stats = db.get_today_stats()
    print(f"Applied: {stats['applied']}")
    print(f"Found: {stats['found']}")
    print(f"Skipped: {stats['skipped']}")
    print(f"Avg ATS: {stats['avg_ats']}%")
```

- [ ] **Step 3: Commit**

```bash
git add scripts/install_cron.py jobpulse/runner.py
git commit -m "feat(jobs): Task 15 — cron setup for 6 scan windows + runner commands"
```

---

## Task 16: Cross-Agent Integration

**Files:**
- Modify: `jobpulse/morning_briefing.py`
- Modify: `jobpulse/weekly_report.py`

- [ ] **Step 1: Add job summary to morning briefing**

Add to the briefing collection section in `jobpulse/morning_briefing.py`:

```python
    # Job Autopilot stats
    try:
        from jobpulse.job_db import JobDB
        job_db = JobDB()
        job_stats = job_db.get_today_stats()
        from datetime import date
        follow_ups = job_db.get_follow_ups_due(date.today())
        sections.append(
            f"💼 Jobs: Applied {job_stats['applied']} (avg ATS {job_stats['avg_ats']}%). "
            f"{len(follow_ups)} follow-ups due today."
        )
    except Exception as e:
        logger.error("Job stats for briefing failed: %s", e)
```

- [ ] **Step 2: Add job metrics to weekly report**

Add to the weekly report aggregation in `jobpulse/weekly_report.py`:

```python
    # Weekly job application metrics
    try:
        from jobpulse.job_db import JobDB
        job_db = JobDB()
        conn = job_db._conn()
        week_applied = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE applied_at >= date('now', '-7 days') AND status = 'Applied'"
        ).fetchone()["c"]
        week_interviews = conn.execute(
            "SELECT COUNT(*) as c FROM applications WHERE status = 'Interview' AND updated_at >= date('now', '-7 days')"
        ).fetchone()["c"]
        avg_ats = conn.execute(
            "SELECT AVG(ats_score) as avg FROM applications WHERE applied_at >= date('now', '-7 days') AND ats_score > 0"
        ).fetchone()["avg"]
        conn.close()
        sections.append(
            f"💼 Applications: {week_applied} sent, {week_interviews} interviews. "
            f"Avg ATS: {round(avg_ats, 1) if avg_ats else 0}%."
        )
    except Exception as e:
        logger.error("Job stats for weekly report failed: %s", e)
```

- [ ] **Step 3: Commit**

```bash
git add jobpulse/morning_briefing.py jobpulse/weekly_report.py
git commit -m "feat(jobs): Task 16 — cross-agent integration (briefing + weekly report)"
```

---

## Task 17: Install Dependencies + Verify

- [ ] **Step 1: Install new pip dependencies**

```bash
pip install playwright pymupdf --break-system-packages
playwright install chromium
```

- [ ] **Step 2: Verify xelatex is installed**

```bash
which xelatex
# If not found: brew install --cask mactex (macOS) or apt install texlive-xetex (Linux)
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/test_application_models.py tests/test_job_db.py tests/test_jd_analyzer.py tests/test_job_deduplicator.py tests/test_github_matcher.py tests/test_ats_scorer.py tests/test_cv_tailor.py tests/test_cover_letter.py tests/test_job_notion_sync.py tests/test_applicator.py -v
```

Expected: All tests pass.

- [ ] **Step 4: Run a manual scan to verify end-to-end**

```bash
python -m jobpulse.runner job-scan --platforms reed
```

- [ ] **Step 5: Install crons**

```bash
python scripts/install_cron.py
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(jobs): Task 17 — dependencies installed, full test suite passing"
```
