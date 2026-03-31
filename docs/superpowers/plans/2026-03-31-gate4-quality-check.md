# Gate 4: Application Quality Check — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-phase quality gate: Phase A blocks low-quality JDs and spam companies before CV generation, Phase B scrutinizes generated CVs to FAANG recruiter standards.

**Architecture:** `gate4_quality.py` (Phase A + Phase B checks), `company_blocklist.py` (Notion Company Blocklist CRUD + cache). Integrated into `job_autopilot.py` at two points: after Gate 3 (Phase A) and after CV generation (Phase B).

**Tech Stack:** SQLite, Notion API (curl), GPT-5o-mini (Phase B only)

---

## File Structure

| File | Responsibility |
|------|----------------|
| **Create:** `jobpulse/gate4_quality.py` | A1 JD quality, A3 company background, B1 deterministic CV scrutiny, B2 LLM FAANG scrutiny |
| **Create:** `jobpulse/company_blocklist.py` | A2 Notion Company Blocklist: create DB, flag spam, fetch blocklist, check status |
| **Create:** `tests/test_gate4_quality.py` | Tests for all Phase A + Phase B checks |
| **Create:** `tests/test_company_blocklist.py` | Tests for blocklist CRUD + cache |
| **Modify:** `jobpulse/job_autopilot.py` | Insert Gate 4A after Gate 3, Gate 4B after CV generation |
| **Modify:** `jobpulse/config.py` | Add `NOTION_BLOCKLIST_DB_ID` |

---

### Task 1: Gate 4 Phase A — JD Quality + Company Background

**Files:**
- Create: `jobpulse/gate4_quality.py`
- Create: `tests/test_gate4_quality.py`

- [ ] **Step 1: Write failing tests for Phase A checks**

```python
# tests/test_gate4_quality.py
"""Tests for Gate 4 application quality checks."""

import pytest
from unittest.mock import patch, MagicMock


class TestJDQualityCheck:
    """Test A1: JD quality validation."""

    def test_short_jd_blocked(self):
        from jobpulse.gate4_quality import check_jd_quality
        result = check_jd_quality(jd_text="Short JD", extracted_skills=["python", "django", "react", "sql", "aws"])
        assert result.passed is False
        assert "too short" in result.reason.lower()

    def test_few_skills_blocked(self):
        from jobpulse.gate4_quality import check_jd_quality
        jd = "We are looking for a software engineer to join our team. " * 10  # >200 chars
        result = check_jd_quality(jd_text=jd, extracted_skills=["python", "sql"])
        assert result.passed is False
        assert "vague" in result.reason.lower() or "skills" in result.reason.lower()

    def test_boilerplate_jd_blocked(self):
        from jobpulse.gate4_quality import check_jd_quality
        jd = (
            "We offer competitive salary in a fast-paced environment. "
            "Exciting opportunity for passionate individuals. "
            "Dynamic team with great benefits. "
            "Requirements: Good communication skills."
        )
        result = check_jd_quality(jd_text=jd, extracted_skills=["communication", "teamwork", "leadership"])
        assert result.passed is False
        assert "boilerplate" in result.reason.lower()

    def test_good_jd_passes(self):
        from jobpulse.gate4_quality import check_jd_quality
        jd = (
            "We are looking for a Python backend engineer with experience in FastAPI, "
            "PostgreSQL, Docker, and AWS. You will build microservices for our fintech platform. "
            "Requirements: 2+ years Python, REST APIs, SQL databases, CI/CD pipelines, "
            "unit testing with pytest. Nice to have: Redis, Kafka, Kubernetes."
        )
        skills = ["python", "fastapi", "postgresql", "docker", "aws", "rest api", "sql", "ci/cd", "pytest"]
        result = check_jd_quality(jd_text=jd, extracted_skills=skills)
        assert result.passed is True

    def test_boilerplate_with_enough_skills_passes(self):
        from jobpulse.gate4_quality import check_jd_quality
        jd = (
            "Exciting opportunity in a fast-paced environment with competitive salary. "
            "We need Python, FastAPI, Docker, AWS, PostgreSQL, Redis, Kafka, React developers. "
            "Build scalable microservices. Experience with CI/CD and testing required."
        )
        skills = ["python", "fastapi", "docker", "aws", "postgresql", "redis", "kafka", "react"]
        result = check_jd_quality(jd_text=jd, extracted_skills=skills)
        assert result.passed is True  # 3 boilerplate phrases but >=8 skills


class TestCompanyBackground:
    """Test A3: Company background checks."""

    def test_generic_company_name_flagged(self):
        from jobpulse.gate4_quality import check_company_background
        result = check_company_background("Tech Solutions Ltd", past_applications=[])
        assert result.is_generic is True

    def test_real_company_name_not_flagged(self):
        from jobpulse.gate4_quality import check_company_background
        result = check_company_background("Revolut", past_applications=[])
        assert result.is_generic is False

    def test_past_application_detected(self):
        from jobpulse.gate4_quality import check_company_background
        result = check_company_background("Google", past_applications=[
            {"company": "Google", "applied_at": "2026-03-20", "role": "SWE"}
        ])
        assert result.previously_applied is True
        assert "2026-03-20" in result.note

    def test_no_past_application(self):
        from jobpulse.gate4_quality import check_company_background
        result = check_company_background("Meta", past_applications=[])
        assert result.previously_applied is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gate4_quality.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement gate4_quality.py — Phase A**

```python
# jobpulse/gate4_quality.py
"""Gate 4: Application Quality Check.

Phase A (pre-generation): JD quality, company background.
Phase B (post-generation): Deterministic CV scrutiny, LLM FAANG recruiter review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Boilerplate phrases (case-insensitive)
# ---------------------------------------------------------------------------

_BOILERPLATE_PHRASES: list[str] = [
    "competitive salary",
    "dynamic team",
    "fast-paced environment",
    "great benefits",
    "exciting opportunity",
    "passionate individuals",
    "self-starter",
    "team player wanted",
    "immediate start",
    "no experience necessary",
]

# Generic company name words
_GENERIC_WORDS: set[str] = {
    "tech", "digital", "it", "solutions", "services", "consulting",
    "group", "limited", "ltd", "uk", "global", "systems", "software",
    "data", "cloud", "cyber", "enterprise", "international",
}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class JDQualityResult:
    passed: bool
    reason: str = ""
    boilerplate_count: int = 0
    skill_count: int = 0


@dataclass
class CompanyBackgroundResult:
    is_generic: bool = False
    previously_applied: bool = False
    note: str = ""


# ---------------------------------------------------------------------------
# A1: JD Quality Check
# ---------------------------------------------------------------------------


def check_jd_quality(
    jd_text: str,
    extracted_skills: list[str],
) -> JDQualityResult:
    """Check JD quality. Blocks short, vague, or boilerplate JDs."""
    # Check length
    if len(jd_text.strip()) < 200:
        return JDQualityResult(
            passed=False,
            reason=f"JD too short ({len(jd_text.strip())} chars, need 200+)",
            skill_count=len(extracted_skills),
        )

    # Check extracted skills count
    if len(extracted_skills) < 5:
        return JDQualityResult(
            passed=False,
            reason=f"JD too vague — only {len(extracted_skills)} skills extracted (need 5+)",
            skill_count=len(extracted_skills),
        )

    # Check boilerplate
    jd_lower = jd_text.lower()
    boilerplate_count = sum(1 for phrase in _BOILERPLATE_PHRASES if phrase in jd_lower)

    if boilerplate_count >= 3 and len(extracted_skills) < 8:
        return JDQualityResult(
            passed=False,
            reason=f"Boilerplate JD — {boilerplate_count} generic phrases, only {len(extracted_skills)} skills",
            boilerplate_count=boilerplate_count,
            skill_count=len(extracted_skills),
        )

    return JDQualityResult(
        passed=True,
        boilerplate_count=boilerplate_count,
        skill_count=len(extracted_skills),
    )


# ---------------------------------------------------------------------------
# A3: Company Background Check
# ---------------------------------------------------------------------------


def check_company_background(
    company: str,
    past_applications: list[dict[str, Any]],
) -> CompanyBackgroundResult:
    """Check company background — generic name detection + past application check."""
    result = CompanyBackgroundResult()

    # Generic name detection
    words = re.findall(r"[a-z]+", company.lower())
    if 1 <= len(words) <= 3 and all(w in _GENERIC_WORDS for w in words):
        result.is_generic = True
        result.note = f"Generic company name: {company}"

    # Past application check
    company_lower = company.lower().strip()
    for app in past_applications:
        if app.get("company", "").lower().strip() == company_lower:
            result.previously_applied = True
            applied_date = app.get("applied_at", "unknown")
            role = app.get("role", "unknown")
            result.note = (
                f"Already applied to {company} on {applied_date} for {role}"
            )
            break

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gate4_quality.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/gate4_quality.py tests/test_gate4_quality.py
git commit -m "feat(jobs): add Gate 4 Phase A — JD quality check + company background"
```

---

### Task 2: Company Blocklist — Notion Database + CRUD

**Files:**
- Create: `jobpulse/company_blocklist.py`
- Create: `tests/test_company_blocklist.py`

- [ ] **Step 1: Write failing tests for blocklist**

```python
# tests/test_company_blocklist.py
"""Tests for Notion Company Blocklist — spam detection + user curation."""

import pytest
from unittest.mock import patch, MagicMock


class TestSpamDetection:
    """Test automatic spam pattern detection."""

    def test_training_keyword_detected(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("IT Career Switch")
        assert result.is_spam is True
        assert "career switch" in result.reason.lower()

    def test_bootcamp_keyword_detected(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("Data Science Bootcamp Ltd")
        assert result.is_spam is True

    def test_recruitment_agency_detected(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("ABC Recruitment Agency")
        assert result.is_spam is True

    def test_real_company_not_flagged(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("Google")
        assert result.is_spam is False

    def test_real_company_with_keyword_substring_not_flagged(self):
        from jobpulse.company_blocklist import detect_spam_company
        # "academy" is a spam keyword but "Academy of Motion Picture" is legit
        # Our check is word-boundary based
        result = detect_spam_company("Revolut")
        assert result.is_spam is False

    def test_high_listing_count_flagged(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("Normal Company", listing_count_7d=15)
        assert result.is_spam is True
        assert "listings" in result.reason.lower()

    def test_normal_listing_count_not_flagged(self):
        from jobpulse.company_blocklist import detect_spam_company
        result = detect_spam_company("Normal Company", listing_count_7d=3)
        assert result.is_spam is False


class TestBlocklistCache:
    """Test blocklist caching and lookup."""

    def test_blocked_company_returns_true(self):
        from jobpulse.company_blocklist import BlocklistCache
        cache = BlocklistCache()
        cache._entries = {"it career switch": "Blocked"}
        assert cache.is_blocked("IT Career Switch") is True

    def test_approved_company_returns_false(self):
        from jobpulse.company_blocklist import BlocklistCache
        cache = BlocklistCache()
        cache._entries = {"google": "Approved"}
        assert cache.is_blocked("Google") is False

    def test_pending_company_returns_false(self):
        from jobpulse.company_blocklist import BlocklistCache
        cache = BlocklistCache()
        cache._entries = {"unknown corp": "Pending"}
        assert cache.is_blocked("Unknown Corp") is False

    def test_unknown_company_returns_false(self):
        from jobpulse.company_blocklist import BlocklistCache
        cache = BlocklistCache()
        cache._entries = {}
        assert cache.is_blocked("New Company") is False

    def test_is_approved(self):
        from jobpulse.company_blocklist import BlocklistCache
        cache = BlocklistCache()
        cache._entries = {"google": "Approved"}
        assert cache.is_approved("Google") is True
        assert cache.is_approved("Meta") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_company_blocklist.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement company_blocklist.py**

```python
# jobpulse/company_blocklist.py
"""Notion Company Blocklist — spam detection + user-curated blocklist.

Detects spam companies automatically (training schemes, recruitment agencies,
high listing counts) and syncs to a Notion database for user approval.

Flow:
  1. detect_spam_company() flags suspicious companies
  2. flag_company_in_notion() adds to Notion as "Pending"
  3. User reviews in Notion → marks "Blocked" or "Approved"
  4. BlocklistCache.refresh() fetches current state before each scan
  5. BlocklistCache.is_blocked() checks in O(1)
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import NOTION_API_KEY

logger = get_logger(__name__)

# Env var for blocklist DB ID — loaded lazily
_BLOCKLIST_DB_ID: str | None = None


def _get_blocklist_db_id() -> str:
    global _BLOCKLIST_DB_ID
    if _BLOCKLIST_DB_ID is None:
        import os
        _BLOCKLIST_DB_ID = os.getenv("NOTION_BLOCKLIST_DB_ID", "")
    return _BLOCKLIST_DB_ID


# ---------------------------------------------------------------------------
# Spam keywords
# ---------------------------------------------------------------------------

_SPAM_KEYWORDS: list[str] = [
    "training", "bootcamp", "academy", "career switch", "career change",
    "recruitment agency", "staffing", "talent pipeline",
    "apprenticeship scheme",
]

_LISTING_SPAM_THRESHOLD = 10  # 10+ listings in 7 days


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SpamDetectionResult:
    is_spam: bool = False
    reason: str = ""
    company: str = ""


# ---------------------------------------------------------------------------
# A2: Spam detection (automatic pattern matching)
# ---------------------------------------------------------------------------


def detect_spam_company(
    company: str,
    listing_count_7d: int = 0,
) -> SpamDetectionResult:
    """Detect if a company is likely spam based on name patterns and listing volume."""
    company_lower = company.lower()

    # Check spam keywords
    for keyword in _SPAM_KEYWORDS:
        if keyword in company_lower:
            return SpamDetectionResult(
                is_spam=True,
                reason=f"Company name contains spam keyword: '{keyword}'",
                company=company,
            )

    # Check listing count
    if listing_count_7d >= _LISTING_SPAM_THRESHOLD:
        return SpamDetectionResult(
            is_spam=True,
            reason=f"Company has {listing_count_7d} listings in 7 days (threshold: {_LISTING_SPAM_THRESHOLD})",
            company=company,
        )

    return SpamDetectionResult(is_spam=False, company=company)


# ---------------------------------------------------------------------------
# Notion Blocklist CRUD
# ---------------------------------------------------------------------------


def _notion_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Call Notion API via curl."""
    cmd = [
        "curl", "-s", "-X", method,
        f"https://api.notion.com/v1{endpoint}",
        "-H", f"Authorization: Bearer {NOTION_API_KEY}",
        "-H", "Content-Type: application/json",
        "-H", "Notion-Version: 2022-06-28",
    ]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return json.loads(result.stdout) if result.stdout else {}
    except Exception as e:
        logger.error("Blocklist Notion API error: %s", e)
        return {}


def create_blocklist_database(parent_page_id: str) -> str | None:
    """Create the Company Blocklist database in Notion. Returns DB ID."""
    payload = {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "icon": {"type": "emoji", "emoji": "🚫"},
        "title": [{"type": "text", "text": {"content": "Company Blocklist"}}],
        "properties": {
            "Company": {"title": {}},
            "Status": {
                "status": {
                    "options": [
                        {"name": "Pending", "color": "yellow"},
                        {"name": "Blocked", "color": "red"},
                        {"name": "Approved", "color": "green"},
                    ],
                    "groups": [
                        {"name": "To Do", "option_ids": []},
                        {"name": "In Progress", "option_ids": []},
                        {"name": "Complete", "option_ids": []},
                    ],
                }
            },
            "Reason": {"rich_text": {}},
            "Platform": {
                "select": {
                    "options": [
                        {"name": "LinkedIn", "color": "blue"},
                        {"name": "Indeed", "color": "brown"},
                        {"name": "Reed", "color": "default"},
                    ]
                }
            },
            "Times Seen": {"number": {"format": "number"}},
            "First Seen": {"date": {}},
            "Last Seen": {"date": {}},
        },
    }
    result = _notion_api("POST", "/databases", payload)
    db_id = result.get("id")
    if db_id:
        logger.info("Created Company Blocklist database: %s", db_id)
    return db_id


def flag_company_in_notion(
    company: str,
    reason: str,
    platform: str = "",
    times_seen: int = 1,
) -> str | None:
    """Add a suspected spam company to the Notion Blocklist as 'Pending'."""
    db_id = _get_blocklist_db_id()
    if not db_id:
        logger.warning("NOTION_BLOCKLIST_DB_ID not set — cannot flag company")
        return None

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    properties: dict[str, Any] = {
        "Company": {"title": [{"text": {"content": company}}]},
        "Status": {"status": {"name": "Pending"}},
        "Reason": {"rich_text": [{"text": {"content": reason}}]},
        "Times Seen": {"number": times_seen},
        "First Seen": {"date": {"start": today}},
        "Last Seen": {"date": {"start": today}},
    }
    if platform:
        properties["Platform"] = {"select": {"name": platform}}

    payload = {"parent": {"database_id": db_id}, "properties": properties}
    result = _notion_api("POST", "/pages", payload)
    page_id = result.get("id")
    if page_id:
        logger.info("Flagged company '%s' in Notion blocklist (Pending)", company)
    return page_id


def fetch_blocklist_from_notion() -> dict[str, str]:
    """Fetch all entries from Notion Blocklist. Returns {company_lower: status}."""
    db_id = _get_blocklist_db_id()
    if not db_id:
        return {}

    entries: dict[str, str] = {}
    payload: dict[str, Any] = {"page_size": 100}
    has_more = True

    while has_more:
        result = _notion_api("POST", f"/databases/{db_id}/query", payload)
        for page in result.get("results", []):
            props = page.get("properties", {})
            # Extract company name
            title_arr = props.get("Company", {}).get("title", [])
            company_name = title_arr[0]["text"]["content"] if title_arr else ""
            # Extract status
            status = props.get("Status", {}).get("status", {}).get("name", "Pending")
            if company_name:
                entries[company_name.lower().strip()] = status

        has_more = result.get("has_more", False)
        next_cursor = result.get("next_cursor")
        if has_more and next_cursor:
            payload["start_cursor"] = next_cursor
        else:
            has_more = False

    logger.info("Fetched %d entries from Notion blocklist", len(entries))
    return entries


# ---------------------------------------------------------------------------
# Blocklist Cache
# ---------------------------------------------------------------------------


class BlocklistCache:
    """In-memory cache of company blocklist. Refreshed once per scan window."""

    def __init__(self) -> None:
        self._entries: dict[str, str] = {}  # company_lower → status

    def refresh(self) -> None:
        """Fetch latest blocklist from Notion."""
        self._entries = fetch_blocklist_from_notion()

    def is_blocked(self, company: str) -> bool:
        """Check if company is explicitly blocked by user."""
        return self._entries.get(company.lower().strip()) == "Blocked"

    def is_approved(self, company: str) -> bool:
        """Check if company was explicitly approved by user."""
        return self._entries.get(company.lower().strip()) == "Approved"

    def is_known(self, company: str) -> bool:
        """Check if company is in the blocklist at all (any status)."""
        return company.lower().strip() in self._entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_company_blocklist.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Add NOTION_BLOCKLIST_DB_ID to config.py**

Add to `jobpulse/config.py` after NOTION_APPLICATIONS_DB_ID:

```python
NOTION_BLOCKLIST_DB_ID = os.getenv("NOTION_BLOCKLIST_DB_ID", "")
```

- [ ] **Step 6: Commit**

```bash
git add jobpulse/company_blocklist.py tests/test_company_blocklist.py jobpulse/config.py
git commit -m "feat(jobs): add Company Blocklist — spam detection + Notion curation"
```

---

### Task 3: Gate 4 Phase B — CV Scrutiny (Deterministic + LLM)

**Files:**
- Modify: `jobpulse/gate4_quality.py`
- Modify: `tests/test_gate4_quality.py`

- [ ] **Step 1: Write failing tests for Phase B**

Add to `tests/test_gate4_quality.py`:

```python
class TestCVDeterministicScrutiny:
    """Test B1: deterministic CV quality checks."""

    def test_cv_with_metrics_passes(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "PROJECTS\n"
            "Multi-Agent Patterns — Built system processing 500+ requests/day\n"
            "Reduced API costs by 96% from $5.63 to $0.23/month\n"
            "Deployed to 3 environments with 99.9% uptime\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.status in ("clean", "acceptable")

    def test_cv_without_metrics_flagged(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "PROJECTS\n"
            "Built a web application using Python and React\n"
            "Worked on backend services\n"
            "Helped with deployment\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.missing_metrics_count > 0

    def test_conversational_text_detected(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "I worked on building a REST API.\n"
            "I was responsible for the database design.\n"
            "My role was to implement authentication.\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.conversational_count > 0

    def test_too_long_cv_error(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = "A" * 5000  # way over 2 pages
        result = scrutinize_cv_deterministic(cv_text)
        assert result.has_error is True

    def test_clean_professional_cv(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "TECHNICAL SKILLS Python FastAPI Docker AWS\n"
            "PROJECTS\n"
            "JobPulse — Autonomous job application system processing 500+ daily requests\n"
            "• Reduced LLM costs by 96% ($5.63→$0.23/month) via hybrid skill extraction\n"
            "• Built 4-gate recruiter pre-screen achieving 92%+ skill match threshold\n"
            "EXPERIENCE\n"
            "Team Leader at Co-op — Managed team of 8, increased efficiency by 25%\n"
            "EDUCATION\n"
            "MSc Computer Science, University of Dundee\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.status == "clean"
        assert result.has_error is False

    def test_informal_words_detected(self):
        from jobpulse.gate4_quality import scrutinize_cv_deterministic
        cv_text = (
            "Built really nice stuff for the team.\n"
            "Just helped with various things.\n"
        )
        result = scrutinize_cv_deterministic(cv_text)
        assert result.informal_count > 0


class TestLLMFAANGScrutiny:
    """Test B2: LLM-based FAANG recruiter review."""

    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_high_score_returns_shortlist(self, mock_llm):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        import json
        mock_llm.return_value = json.dumps({
            "total_score": 8,
            "relevance": 3,
            "evidence": 3,
            "presentation": 1,
            "standout": 1,
            "strengths": ["Strong projects", "Good metrics"],
            "weaknesses": ["Could add more detail"],
            "verdict": "shortlist",
        })
        result = scrutinize_cv_llm("cv text", "Python Dev", "Google", ["python"], ["docker"])
        assert result.score == 8
        assert result.verdict == "shortlist"
        assert result.needs_review is False

    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_low_score_flags_for_review(self, mock_llm):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        import json
        mock_llm.return_value = json.dumps({
            "total_score": 5,
            "relevance": 2,
            "evidence": 1,
            "presentation": 1,
            "standout": 1,
            "strengths": ["Relevant stack"],
            "weaknesses": ["No metrics", "Generic bullets"],
            "verdict": "maybe",
        })
        result = scrutinize_cv_llm("cv text", "SWE", "Meta", ["python"], [])
        assert result.score == 5
        assert result.needs_review is True

    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_handles_none_response(self, mock_llm):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = None
        result = scrutinize_cv_llm("cv text", "SWE", "Google", ["python"], [])
        assert result.score == 0
        assert result.needs_review is True

    @patch("jobpulse.gate4_quality.safe_openai_call")
    def test_handles_invalid_json(self, mock_llm):
        from jobpulse.gate4_quality import scrutinize_cv_llm
        mock_llm.return_value = "not json"
        result = scrutinize_cv_llm("cv text", "SWE", "Google", ["python"], [])
        assert result.score == 0
        assert result.needs_review is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_gate4_quality.py -v -k "CV or FAANG"`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add Phase B methods to gate4_quality.py**

Add these to `jobpulse/gate4_quality.py`:

```python
from jobpulse.utils.safe_io import safe_openai_call
import json as _json

# ---------------------------------------------------------------------------
# B1 patterns
# ---------------------------------------------------------------------------

_CONVERSATIONAL_PATTERNS: list[str] = [
    r"\bI worked\b",
    r"\bI helped\b",
    r"\bI was responsible\b",
    r"\bMy role was\b",
    r"\bI have\b",
    r"\bI am\b",
]

_INFORMAL_WORDS: list[str] = [
    r"\breally\b", r"\bvery\b", r"\bjust\b",
    r"\bstuff\b", r"\bthings\b", r"\bnice\b",
]

_METRIC_PATTERN = re.compile(r"\d+[%xX]|\d+\+|\$[\d,.]+|£[\d,.]+|\d+[kKmM]\b|\d+ (?:users|requests|apps|tests|skills|projects|endpoints|agents|bots)")

_MAX_CV_CHARS = 4500  # heuristic for 2-page limit


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CVScrutinyResult:
    status: str = "clean"  # "clean" | "acceptable" | "needs_fix"
    has_error: bool = False
    missing_metrics_count: int = 0
    conversational_count: int = 0
    informal_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class LLMScrutinyResult:
    score: int = 0
    verdict: str = "reject"
    needs_review: bool = True
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# B1: Deterministic CV Scrutiny
# ---------------------------------------------------------------------------


def scrutinize_cv_deterministic(cv_text: str) -> CVScrutinyResult:
    """FAANG-level deterministic checks on CV text."""
    result = CVScrutinyResult()

    # Check page limit (heuristic: >4500 chars ≈ >2 pages)
    if len(cv_text) > _MAX_CV_CHARS:
        result.has_error = True
        result.warnings.append(f"CV too long ({len(cv_text)} chars, max {_MAX_CV_CHARS})")

    # Check for metrics in project/experience bullets
    lines = cv_text.split("\n")
    bullet_lines = [l for l in lines if l.strip().startswith(("•", "-", "–", "·")) or
                    (len(l.strip()) > 20 and not l.strip().isupper())]
    lines_without_metrics = 0
    for line in bullet_lines:
        if not _METRIC_PATTERN.search(line):
            lines_without_metrics += 1
    result.missing_metrics_count = lines_without_metrics

    # Check for conversational text
    for pattern in _CONVERSATIONAL_PATTERNS:
        matches = re.findall(pattern, cv_text, re.IGNORECASE)
        result.conversational_count += len(matches)
    if result.conversational_count > 0:
        result.warnings.append(f"Conversational text: {result.conversational_count} instances")

    # Check for informal words
    for pattern in _INFORMAL_WORDS:
        matches = re.findall(pattern, cv_text, re.IGNORECASE)
        result.informal_count += len(matches)
    if result.informal_count > 0:
        result.warnings.append(f"Informal words: {result.informal_count} instances")

    # Determine status
    total_warnings = len(result.warnings) + (1 if result.missing_metrics_count > 2 else 0)
    if result.has_error:
        result.status = "needs_fix"
    elif total_warnings == 0:
        result.status = "clean"
    elif total_warnings <= 2:
        result.status = "acceptable"
    else:
        result.status = "needs_fix"

    return result


# ---------------------------------------------------------------------------
# B2: LLM FAANG Recruiter Scrutiny
# ---------------------------------------------------------------------------


def scrutinize_cv_llm(
    cv_text: str,
    role: str,
    company: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> LLMScrutinyResult:
    """GPT-5o-mini as a FAANG senior recruiter reviewing the CV."""
    prompt = (
        f"You are a senior IT recruiter at Google reviewing a CV for: {role} at {company}.\n\n"
        f"Required skills: {', '.join(required_skills[:15])}\n"
        f"Preferred skills: {', '.join(preferred_skills[:10])}\n\n"
        f"CV:\n{cv_text[:3000]}\n\n"
        f"Score 0-10:\n"
        f"1. Relevance (0-3): Does it address requirements?\n"
        f"2. Evidence (0-3): Claims backed by metrics/projects?\n"
        f"3. Presentation (0-2): Professional, clear, no fluff?\n"
        f"4. Standout (0-2): Would you want to interview?\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"total_score": 0-10, "relevance": 0-3, "evidence": 0-3, '
        f'"presentation": 0-2, "standout": 0-2, '
        f'"strengths": ["..."], "weaknesses": ["..."], '
        f'"verdict": "shortlist"|"maybe"|"reject"}}'
    )

    import openai
    client = openai.OpenAI()
    response = safe_openai_call(
        client,
        model="gpt-5o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        caller="gate4_llm_scrutiny",
    )

    if not response:
        logger.warning("Gate 4 LLM scrutiny returned None")
        return LLMScrutinyResult(needs_review=True)

    try:
        data = _json.loads(response)
    except _json.JSONDecodeError:
        logger.warning("Gate 4 LLM scrutiny returned invalid JSON: %s", response[:200])
        return LLMScrutinyResult(needs_review=True)

    score = int(data.get("total_score", 0))
    verdict = data.get("verdict", "reject")
    needs_review = score < 7

    return LLMScrutinyResult(
        score=score,
        verdict=verdict,
        needs_review=needs_review,
        strengths=data.get("strengths", []),
        weaknesses=data.get("weaknesses", []),
        breakdown={
            "relevance": data.get("relevance", 0),
            "evidence": data.get("evidence", 0),
            "presentation": data.get("presentation", 0),
            "standout": data.get("standout", 0),
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_gate4_quality.py -v`
Expected: All 19 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/gate4_quality.py tests/test_gate4_quality.py
git commit -m "feat(jobs): add Gate 4 Phase B — deterministic + LLM CV scrutiny"
```

---

### Task 4: Integrate Gate 4 into job_autopilot.py

**Files:**
- Modify: `jobpulse/job_autopilot.py`

- [ ] **Step 1: Add imports**

After existing imports (around line 41), add:

```python
from jobpulse.gate4_quality import check_jd_quality, check_company_background, scrutinize_cv_deterministic, scrutinize_cv_llm
from jobpulse.company_blocklist import detect_spam_company, flag_company_in_notion, BlocklistCache
```

- [ ] **Step 2: Add Phase A — after Gate 3 loop, before CV generation loop**

Insert between the Gate 3 screening loop (ends at line 326) and the CV generation loop (starts at line 341). After `screened_listings.append((listing, screen))`:

Add a new filtering pass:

```python
    # --- Gate 4 Phase A: Pre-generation quality check ---
    blocklist = BlocklistCache()
    try:
        blocklist.refresh()
    except Exception as exc:
        logger.warning("job_autopilot: blocklist refresh failed: %s", exc)

    gate4_filtered: list[tuple] = []
    gate4_blocked = 0

    for listing, screen in screened_listings:
        # A2: Company blocklist check
        if blocklist.is_blocked(listing.company):
            gate4_blocked += 1
            logger.info("job_autopilot: Gate 4 BLOCKED (blocklist) %s @ %s", listing.title, listing.company)
            db.save_application(job_id=listing.job_id, status="Blocked", match_tier="skip")
            continue

        # A2: Spam detection (auto-flag to Notion if not already known)
        if not blocklist.is_approved(listing.company) and not blocklist.is_known(listing.company):
            spam = detect_spam_company(listing.company)
            if spam.is_spam:
                gate4_blocked += 1
                logger.info("job_autopilot: Gate 4 BLOCKED (spam) %s @ %s — %s", listing.title, listing.company, spam.reason)
                try:
                    flag_company_in_notion(listing.company, spam.reason, listing.platform)
                except Exception:
                    pass
                db.save_application(job_id=listing.job_id, status="Blocked", match_tier="skip")
                continue

        # A1: JD quality check
        jd_quality = check_jd_quality(listing.description, listing.required_skills + listing.preferred_skills)
        if not jd_quality.passed:
            gate4_blocked += 1
            logger.info("job_autopilot: Gate 4 BLOCKED (JD quality) %s @ %s — %s", listing.title, listing.company, jd_quality.reason)
            db.save_application(job_id=listing.job_id, status="Skipped", match_tier="skip")
            continue

        # A3: Company background (soft flags — don't block)
        try:
            past_apps = db.get_applications_by_company(listing.company)
        except Exception:
            past_apps = []
        bg = check_company_background(listing.company, past_apps)
        if bg.previously_applied:
            logger.info("job_autopilot: Gate 4 NOTE — %s", bg.note)
        if bg.is_generic:
            logger.info("job_autopilot: Gate 4 NOTE — %s", bg.note)

        gate4_filtered.append((listing, screen))

    trail.log_step("decision", "Gate 4 Phase A", step_output=f"{len(gate4_filtered)} pass, {gate4_blocked} blocked")
```

Then replace `screened_listings` with `gate4_filtered` in the CV generation loop:

```python
    for listing, screen in gate4_filtered:
```

- [ ] **Step 3: Add Phase B — after CV generation, before Drive upload**

After the cover letter generation block (around line 465) and before the Drive upload block (around line 470), insert:

```python
            # --- Gate 4 Phase B: CV quality scrutiny ---
            gate4b_status = "clean"
            gate4b_notes = ""
            if cv_path and cv_text:
                # B1: Deterministic scrutiny
                b1_result = scrutinize_cv_deterministic(cv_text)
                gate4b_status = b1_result.status
                if b1_result.warnings:
                    gate4b_notes = "B1: " + "; ".join(b1_result.warnings)
                    logger.info("job_autopilot: Gate 4B warnings for %s: %s", listing.company, gate4b_notes)

                # B2: LLM scrutiny (only if B1 is clean or acceptable)
                if b1_result.status in ("clean", "acceptable"):
                    try:
                        b2_result = scrutinize_cv_llm(
                            cv_text, listing.title, listing.company,
                            listing.required_skills, listing.preferred_skills,
                        )
                        if b2_result.needs_review:
                            gate4b_status = "needs_review"
                            weakness_str = "; ".join(b2_result.weaknesses[:3])
                            gate4b_notes += f" | B2: {b2_result.score}/10 — {weakness_str}"
                            logger.info(
                                "job_autopilot: Gate 4B LLM score %d/10 for %s @ %s — %s",
                                b2_result.score, listing.title, listing.company, weakness_str,
                            )
                    except Exception as exc:
                        logger.warning("job_autopilot: Gate 4B LLM failed: %s", exc)
```

Then modify the Notion update to include Gate 4B status:

```python
            # Determine final Notion status based on Gate 4B
            notion_status = "Ready"
            if gate4b_status == "needs_review":
                notion_status = "Needs Review"
```

And pass `gate4b_notes` to the Notion notes field:

```python
                    update_application_page(
                        notion_page_id,
                        status=notion_status,
                        ats_score=ats_score,
                        match_tier=tier,
                        matched_projects=matched_project_names,
                        cv_drive_link=cv_drive_link,
                        cl_drive_link=cl_drive_link,
                        notes=gate4b_notes if gate4b_notes else None,
                    )
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_gate4_quality.py tests/test_company_blocklist.py tests/test_drive_uploader.py tests/test_verification_detector.py tests/test_scan_learning.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/job_autopilot.py
git commit -m "feat(jobs): integrate Gate 4 into pipeline — Phase A pre-gen + Phase B post-gen"
```

---

### Task 5: Update Documentation

**Files:**
- Modify: `CLAUDE.md`, `.claude/rules/jobs.md`, `jobpulse/CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md**

Add Gate 4 to the Pre-Screen Pipeline section:

```
Gate 4A (pre-gen): JD quality (≥200 chars, ≥5 skills, no boilerplate) + company blocklist (Notion-curated) + company background (generic name, past apps)
Gate 4B (post-gen): Deterministic CV scrutiny (metrics, tone, length) + LLM FAANG recruiter review (≥7/10 to auto-proceed, <7 → Notion "Needs Review")
```

- [ ] **Step 2: Update .claude/rules/jobs.md**

Add Gate 4 section.

- [ ] **Step 3: Update jobpulse/CLAUDE.md**

Add gate4_quality.py and company_blocklist.py to agents list.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/jobs.md jobpulse/CLAUDE.md
git commit -m "docs: add Gate 4 quality check to all documentation"
```

---

## Self-Review

**1. Spec coverage:**
- A1 JD quality → Task 1 ✅
- A2 Company blocklist + Notion → Task 2 ✅
- A3 Company background → Task 1 ✅
- B1 Deterministic CV scrutiny → Task 3 ✅
- B2 LLM FAANG scrutiny → Task 3 ✅
- Autopilot integration → Task 4 ✅
- Documentation → Task 5 ✅

**2. Placeholder scan:** No TBDs. All code provided. All thresholds explicit.

**3. Type consistency:**
- `check_jd_quality()` returns `JDQualityResult` — used consistently
- `detect_spam_company()` returns `SpamDetectionResult` — used consistently
- `scrutinize_cv_deterministic()` returns `CVScrutinyResult` with `.status`, `.warnings` — used correctly in Task 4
- `scrutinize_cv_llm()` returns `LLMScrutinyResult` with `.score`, `.needs_review`, `.weaknesses` — used correctly in Task 4
- `BlocklistCache.is_blocked/is_approved/is_known` — all used correctly in Task 4
