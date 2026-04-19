# Post-Apply Learning & Bookkeeping System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every successful job submission, automatically learn from the form experience (field types, adapter, page count, screening questions) and complete all Notion bookkeeping (Drive links, applied date, follow-up, status), so cron jobs can replay applications faster and without supervision.

**Architecture:** A new `post_apply_hook()` function in `jobpulse/post_apply_hook.py` is called at the end of `apply_job()` after a successful result. It orchestrates three concerns: (1) form experience recording into a new `FormExperienceDB` SQLite store, (2) Drive upload + Notion update with all required fields, (3) screening answer tagging with domain context. The hook is called from one place — `applicator.py` — so both cron and manual paths get it for free.

**Tech Stack:** Python 3.12, SQLite, Google Drive API (existing `drive_uploader.py`), Notion API (existing `job_notion_sync.py`), pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `jobpulse/form_experience_db.py` | CREATE | SQLite store for per-domain form experience: adapter, page count, field types, time taken, screening questions |
| `jobpulse/post_apply_hook.py` | CREATE | Unified post-apply orchestrator: record experience, upload Drive docs, update Notion, tag screening answers |
| `jobpulse/applicator.py` | MODIFY | Call `post_apply_hook()` after successful submission (lines 341-357) |
| `jobpulse/scan_pipeline.py` | MODIFY | Remove duplicated post-apply Notion update from `route_and_apply()` (lines 777-801), delegate to hook |
| `jobpulse/job_notion_sync.py` | MODIFY | Add `applied_time` (datetime, not just date) to `build_update_payload()` |
| `jobpulse/application_orchestrator_pkg/_form_filler.py` | MODIFY | Enrich success result dict with `field_types`, `screening_questions`, `platform` metadata |
| `.claude/rules/jobs.md` | MODIFY | Add "Post-Apply Hook" section documenting the system |
| `jobpulse/CLAUDE.md` | MODIFY | Add `post_apply_hook.py` and `form_experience_db.py` to agent list |
| `CLAUDE.md` | NO CHANGE | Top-level file does not need changes (module-level docs handle it) |
| `tests/jobpulse/test_form_experience_db.py` | CREATE | Tests for FormExperienceDB |
| `tests/jobpulse/test_post_apply_hook.py` | CREATE | Tests for post_apply_hook |

---

### Task 1: FormExperienceDB — SQLite Store

**Files:**
- Create: `jobpulse/form_experience_db.py`
- Create: `tests/jobpulse/test_form_experience_db.py`

- [ ] **Step 1: Write failing tests for FormExperienceDB**

```python
"""Tests for FormExperienceDB — per-domain form experience storage."""
import json

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def db(tmp_path):
    return FormExperienceDB(db_path=str(tmp_path / "form_exp.db"))


def test_record_and_lookup(db):
    db.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="extension",
        pages_filled=3,
        field_types=["text", "select", "upload", "radio"],
        screening_questions=["Do you require sponsorship?", "Expected salary?"],
        time_seconds=42.5,
        success=True,
    )
    exp = db.lookup("boards.greenhouse.io")
    assert exp is not None
    assert exp["platform"] == "greenhouse"
    assert exp["adapter"] == "extension"
    assert exp["pages_filled"] == 3
    assert json.loads(exp["field_types"]) == ["text", "select", "upload", "radio"]
    assert json.loads(exp["screening_questions"]) == [
        "Do you require sponsorship?", "Expected salary?"
    ]
    assert exp["time_seconds"] == pytest.approx(42.5)
    assert exp["success"] == 1
    assert exp["apply_count"] == 1


def test_repeat_updates_count(db):
    db.record(domain="jobs.lever.co", platform="lever", adapter="extension",
              pages_filled=2, field_types=["text"], screening_questions=[],
              time_seconds=20.0, success=True)
    db.record(domain="jobs.lever.co", platform="lever", adapter="extension",
              pages_filled=2, field_types=["text", "select"], screening_questions=["Salary?"],
              time_seconds=18.0, success=True)
    exp = db.lookup("jobs.lever.co")
    assert exp["apply_count"] == 2
    # Latest data overwrites
    assert json.loads(exp["field_types"]) == ["text", "select"]
    assert exp["time_seconds"] == pytest.approx(18.0)


def test_lookup_missing_returns_none(db):
    assert db.lookup("nonexistent.com") is None


def test_get_stats(db):
    db.record(domain="a.com", platform="greenhouse", adapter="extension",
              pages_filled=1, field_types=[], screening_questions=[],
              time_seconds=10.0, success=True)
    db.record(domain="b.com", platform="lever", adapter="extension",
              pages_filled=2, field_types=[], screening_questions=[],
              time_seconds=15.0, success=False)
    stats = db.get_stats()
    assert stats["total_domains"] == 2
    assert stats["successful_domains"] == 1


def test_failed_record_does_not_overwrite_success(db):
    db.record(domain="x.com", platform="greenhouse", adapter="extension",
              pages_filled=3, field_types=["text"], screening_questions=[],
              time_seconds=30.0, success=True)
    db.record(domain="x.com", platform="greenhouse", adapter="extension",
              pages_filled=0, field_types=[], screening_questions=[],
              time_seconds=5.0, success=False)
    exp = db.lookup("x.com")
    # Success data preserved, count still incremented
    assert exp["success"] == 1
    assert exp["pages_filled"] == 3
    assert exp["apply_count"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.form_experience_db'`

- [ ] **Step 3: Implement FormExperienceDB**

```python
"""Per-domain form experience store.

Records what the form looked like (adapter, pages, field types, screening questions,
time) after each successful application. Cron jobs query this to skip LLM page
detection and pre-load the right expectations for known domains.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_experience.db")


class FormExperienceDB:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_experience (
                    domain TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    pages_filled INTEGER NOT NULL,
                    field_types TEXT NOT NULL,
                    screening_questions TEXT NOT NULL,
                    time_seconds REAL NOT NULL,
                    success INTEGER NOT NULL,
                    apply_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def record(
        self,
        domain: str,
        platform: str,
        adapter: str,
        pages_filled: int,
        field_types: list[str],
        screening_questions: list[str],
        time_seconds: float,
        success: bool,
    ) -> None:
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        ft_json = json.dumps(field_types)
        sq_json = json.dumps(screening_questions)

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT success FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()

            if existing and existing[0] == 1 and not success:
                # Don't overwrite successful experience with failure data
                conn.execute(
                    "UPDATE form_experience SET apply_count = apply_count + 1, updated_at = ? WHERE domain = ?",
                    (now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO form_experience
                       (domain, platform, adapter, pages_filled, field_types,
                        screening_questions, time_seconds, success, apply_count,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(domain) DO UPDATE SET
                           platform = excluded.platform,
                           adapter = excluded.adapter,
                           pages_filled = excluded.pages_filled,
                           field_types = excluded.field_types,
                           screening_questions = excluded.screening_questions,
                           time_seconds = excluded.time_seconds,
                           success = excluded.success,
                           apply_count = apply_count + 1,
                           updated_at = excluded.updated_at""",
                    (domain, platform, adapter, pages_filled, ft_json, sq_json,
                     time_seconds, int(success), now, now),
                )
        logger.info(
            "form_experience: recorded %s (platform=%s, pages=%d, success=%s, fields=%d)",
            domain, platform, pages_filled, success, len(field_types),
        )

    def lookup(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
            successful = conn.execute(
                "SELECT COUNT(*) FROM form_experience WHERE success = 1"
            ).fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_form_experience_db.py
git commit -m "feat: add FormExperienceDB for per-domain form learning"
```

---

### Task 2: Enrich Fill Result with Form Metadata

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_form_filler.py`
- Modify: `jobpulse/ext_models.py` (read FieldInfo to understand field type enum)

The `fill_application()` method returns `{"success": True, "pages_filled": N}` but doesn't include what field types were encountered or what screening questions were asked. We need this metadata for the experience DB.

- [ ] **Step 1: Read FieldInfo model to understand available field type data**

Check `jobpulse/ext_models.py` for the `FieldInfo` model — it has a `type` field (text, select, radio, checkbox, file, etc.) and a `label` field. These are already available in the page snapshot.

- [ ] **Step 2: Add metadata collection to fill_application()**

In `jobpulse/application_orchestrator_pkg/_form_filler.py`, add tracking variables at the start of `fill_application()` (after `filled_selectors` init around line 128), and populate the success result:

```python
# --- Add after line 128 (after filled_selectors init) ---
# Collect form metadata for post-apply learning
_seen_field_types: list[str] = []
_seen_screening_questions: list[str] = []
```

Then inside the page loop (around line 136-180), after actions are computed for each page, collect field types and screening questions from the snapshot:

```python
# --- Add after actions are computed (after line 169) ---
# Collect field types from this page's snapshot for learning
snap_dict = self._as_dict(snapshot)
for f in snap_dict.get("fields", []):
    ftype = f.get("type", "unknown")
    if ftype not in _seen_field_types:
        _seen_field_types.append(ftype)
    # Screening questions: fields with question-like labels
    label = f.get("label", "")
    if label and "?" in label and label not in _seen_screening_questions:
        _seen_screening_questions.append(label)
```

Then enrich all success return statements (lines 142, 279, 318) to include the metadata:

```python
# Change from:
return {"success": True, "screenshot": last_screenshot, "pages_filled": page_num}
# To:
return {
    "success": True, "screenshot": last_screenshot, "pages_filled": page_num,
    "field_types": _seen_field_types, "screening_questions": _seen_screening_questions,
}
```

Also enrich the "verified" success return at line 318:
```python
return {
    "success": True, "verified": True, "screenshot": last_screenshot,
    "pages_filled": page_num,
    "field_types": _seen_field_types, "screening_questions": _seen_screening_questions,
}
```

And the dry_run success return at line 279:
```python
return {
    "success": True, "dry_run": True, "screenshot": last_screenshot,
    "pages_filled": page_num,
    "field_types": _seen_field_types, "screening_questions": _seen_screening_questions,
}
```

- [ ] **Step 3: Run existing form filler tests to ensure no regressions**

Run: `python -m pytest tests/jobpulse/ -v -k "form_filler or page_filler" --timeout=30`
Expected: All existing tests still PASS (new keys in dict don't break anything)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_form_filler.py
git commit -m "feat: enrich fill result with field_types and screening_questions metadata"
```

---

### Task 3: Add Applied Time to Notion Sync

**Files:**
- Modify: `jobpulse/job_notion_sync.py:151-223`

Currently `build_update_payload()` accepts `applied_date` (date only). We need `applied_time` (full ISO timestamp) for precision tracking.

- [ ] **Step 1: Add `applied_time` parameter to `build_update_payload()`**

In `jobpulse/job_notion_sync.py`, add `applied_time: str | None = None` parameter to `build_update_payload()` signature (after `applied_date`):

```python
def build_update_payload(
    status: str | None = None,
    ats_score: float | None = None,
    match_tier: str | None = None,
    matched_projects: list[str] | None = None,
    applied_date: date | None = None,
    applied_time: str | None = None,      # <-- NEW: ISO timestamp string
    follow_up_date: date | None = None,
    notes: str | None = None,
    ats_platform: str | None = None,
    cv_drive_link: str | None = None,
    cl_drive_link: str | None = None,
    recruiter_email: str | None = None,
    company: str | None = None,
    manually_applied: bool | None = None,
) -> dict:
```

Add the property builder inside the function (after the `applied_date` block around line 190):

```python
    if applied_time is not None:
        properties["Applied Time"] = {
            "rich_text": [{"text": {"content": applied_time}}]
        }
```

- [ ] **Step 2: Verify update_application_page still works (kwargs passthrough)**

`update_application_page()` uses `**kwargs` passthrough to `build_update_payload()`, so the new param is automatically supported. No changes needed there.

- [ ] **Step 3: Run existing Notion sync tests**

Run: `python -m pytest tests/ -v -k "notion_sync or job_notion" --timeout=30`
Expected: PASS (new optional param doesn't break existing calls)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/job_notion_sync.py
git commit -m "feat: add applied_time field to Notion sync payload"
```

---

### Task 4: post_apply_hook — The Main Module

**Files:**
- Create: `jobpulse/post_apply_hook.py`
- Create: `tests/jobpulse/test_post_apply_hook.py`

This is the unified post-apply function that both cron and manual paths call.

- [ ] **Step 1: Write failing tests for post_apply_hook**

```python
"""Tests for post_apply_hook — unified post-apply orchestration."""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.post_apply_hook import post_apply_hook


@pytest.fixture
def tmp_dbs(tmp_path):
    """Patch all DB paths to tmp_path."""
    return {
        "form_exp_db": str(tmp_path / "form_exp.db"),
        "nav_db": str(tmp_path / "nav.db"),
    }


@pytest.fixture
def mock_result():
    return {
        "success": True,
        "pages_filled": 3,
        "field_types": ["text", "select", "upload"],
        "screening_questions": ["Do you require visa sponsorship?"],
    }


@pytest.fixture
def job_context():
    return {
        "job_id": "abc123",
        "company": "TestCorp",
        "title": "Data Engineer",
        "url": "https://boards.greenhouse.io/testcorp/jobs/123",
        "platform": "greenhouse",
        "ats_platform": "greenhouse",
        "notion_page_id": "notion-page-123",
        "cv_path": "/tmp/cv.pdf",
        "cover_letter_path": "/tmp/cl.pdf",
        "match_tier": "auto",
        "ats_score": 96.5,
        "matched_projects": ["multi_agent_patterns", "JobPulse"],
    }


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv-link")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl-link")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_full_hook_flow(mock_notion, mock_cl_upload, mock_cv_upload,
                        mock_result, job_context, tmp_dbs):
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )

    # Drive uploads called
    mock_cv_upload.assert_called_once_with(Path("/tmp/cv.pdf"), "TestCorp")
    mock_cl_upload.assert_called_once_with(Path("/tmp/cl.pdf"), "TestCorp")

    # Notion updated with all required fields
    mock_notion.assert_called_once()
    call_kwargs = mock_notion.call_args[1]
    assert call_kwargs["status"] == "Applied"
    assert call_kwargs["applied_date"] == date.today()
    assert "applied_time" in call_kwargs
    assert call_kwargs["cv_drive_link"] == "https://drive.google.com/cv-link"
    assert call_kwargs["cl_drive_link"] == "https://drive.google.com/cl-link"
    assert call_kwargs["company"] == "TestCorp"
    assert call_kwargs["follow_up_date"] is not None


@patch("jobpulse.post_apply_hook.upload_cv", return_value=None)
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_tolerates_drive_failure(mock_notion, mock_cl, mock_cv,
                                      mock_result, job_context, tmp_dbs):
    """Drive upload failure should not prevent Notion update."""
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_called_once()
    call_kwargs = mock_notion.call_args[1]
    assert call_kwargs["cv_drive_link"] is None
    assert call_kwargs["cl_drive_link"] is None


@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None)
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_hook_skips_notion_when_no_page_id(mock_notion, mock_cl, mock_cv,
                                            mock_result, job_context, tmp_dbs):
    job_context["notion_page_id"] = None
    post_apply_hook(
        result=mock_result,
        job_context=job_context,
        form_exp_db_path=tmp_dbs["form_exp_db"],
    )
    mock_notion.assert_not_called()


def test_hook_records_form_experience(mock_result, job_context, tmp_dbs):
    with patch("jobpulse.post_apply_hook.upload_cv", return_value=None), \
         patch("jobpulse.post_apply_hook.upload_cover_letter", return_value=None), \
         patch("jobpulse.post_apply_hook.update_application_page", return_value=True):
        post_apply_hook(
            result=mock_result,
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )

    from jobpulse.form_experience_db import FormExperienceDB
    db = FormExperienceDB(db_path=tmp_dbs["form_exp_db"])
    exp = db.lookup("boards.greenhouse.io")
    assert exp is not None
    assert exp["platform"] == "greenhouse"
    assert exp["pages_filled"] == 3
    assert json.loads(exp["field_types"]) == ["text", "select", "upload"]


def test_hook_no_op_on_failed_result(job_context, tmp_dbs):
    """Hook does nothing if result.success is False."""
    with patch("jobpulse.post_apply_hook.upload_cv") as mock_cv, \
         patch("jobpulse.post_apply_hook.update_application_page") as mock_notion:
        post_apply_hook(
            result={"success": False, "error": "CAPTCHA"},
            job_context=job_context,
            form_exp_db_path=tmp_dbs["form_exp_db"],
        )
    mock_cv.assert_not_called()
    mock_notion.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_post_apply_hook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.post_apply_hook'`

- [ ] **Step 3: Implement post_apply_hook**

```python
"""Unified post-apply hook — called after every successful job submission.

Handles three concerns:
1. Form experience recording (FormExperienceDB) — learn field types, pages, timing
2. Drive upload + Notion update — CV/CL links, applied date/time, follow-up, status
3. Job DB update — mark as Applied with timestamp

Called from applicator.apply_job() so both cron and manual paths get it for free.
"""
from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.drive_uploader import upload_cover_letter, upload_cv
from jobpulse.form_experience_db import FormExperienceDB
from jobpulse.job_notion_sync import update_application_page

logger = get_logger(__name__)


def post_apply_hook(
    result: dict,
    job_context: dict,
    form_exp_db_path: str | None = None,
) -> None:
    """Run all post-apply steps after a successful submission.

    Args:
        result: Return value from adapter.fill_and_submit() — must have
                success, pages_filled, field_types, screening_questions.
        job_context: Dict with keys: job_id, company, title, url, platform,
                     ats_platform, notion_page_id, cv_path, cover_letter_path,
                     match_tier, ats_score, matched_projects.
        form_exp_db_path: Override DB path (for testing with tmp_path).
    """
    if not result.get("success"):
        return

    company = job_context.get("company", "Unknown")
    url = job_context.get("url", "")
    notion_page_id = job_context.get("notion_page_id")
    cv_path = job_context.get("cv_path")
    cl_path = job_context.get("cover_letter_path")

    start = time.monotonic()

    # --- 1. Record form experience ---
    try:
        exp_db = FormExperienceDB(db_path=form_exp_db_path)
        exp_db.record(
            domain=url,
            platform=job_context.get("ats_platform") or job_context.get("platform", "generic"),
            adapter="extension",
            pages_filled=result.get("pages_filled", 0),
            field_types=result.get("field_types", []),
            screening_questions=result.get("screening_questions", []),
            time_seconds=result.get("time_seconds", 0.0),
            success=True,
        )
    except Exception as exc:
        logger.warning("post_apply_hook: form experience recording failed: %s", exc)

    # --- 2. Upload documents to Drive ---
    cv_drive_link = None
    cl_drive_link = None

    if cv_path:
        try:
            cv_drive_link = upload_cv(Path(cv_path), company)
        except Exception as exc:
            logger.warning("post_apply_hook: CV Drive upload failed: %s", exc)

    if cl_path:
        try:
            cl_drive_link = upload_cover_letter(Path(cl_path), company)
        except Exception as exc:
            logger.warning("post_apply_hook: CL Drive upload failed: %s", exc)

    # --- 3. Update Notion with all required fields ---
    if notion_page_id:
        applied_now = datetime.now(UTC)
        follow_up = date.today() + timedelta(days=7)
        try:
            update_application_page(
                notion_page_id,
                status="Applied",
                applied_date=date.today(),
                applied_time=applied_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                follow_up_date=follow_up,
                cv_drive_link=cv_drive_link,
                cl_drive_link=cl_drive_link,
                company=company,
            )
        except Exception as exc:
            logger.warning("post_apply_hook: Notion update failed: %s", exc)

    elapsed = time.monotonic() - start
    logger.info(
        "post_apply_hook: completed for %s in %.1fs (drive_cv=%s, drive_cl=%s, notion=%s)",
        company,
        elapsed,
        "yes" if cv_drive_link else "no",
        "yes" if cl_drive_link else "no",
        "yes" if notion_page_id else "skip",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_post_apply_hook.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_post_apply_hook.py
git commit -m "feat: add unified post_apply_hook for learning and bookkeeping"
```

---

### Task 5: Wire Hook into applicator.py

**Files:**
- Modify: `jobpulse/applicator.py:340-357`

- [ ] **Step 1: Add job_context parameter to apply_job signature**

Change the `apply_job` signature at line 117:

```python
def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    cl_generator: Any | None = None,
    custom_answers: dict | None = None,
    overrides: dict | None = None,
    dry_run: bool = False,
    engine: str = "extension",
    job_context: dict | None = None,      # <-- NEW: metadata for post-apply hook
) -> dict:
```

- [ ] **Step 2: Add post_apply_hook call after successful submission**

In `jobpulse/applicator.py`, replace lines 340-357 (after the external redirect block, before the return). The hook runs BEFORE the anti-detection delay so Drive/Notion work happens during the wait:

```python
    platform_name = result.get("external_platform", adapter.name)
    if result.get("success"):
        logger.info("Application submitted via %s (%d today)", platform_name, total)
    else:
        logger.warning(
            "Application failed via %s: %s (quota already consumed)",
            platform_name,
            result.get("error"),
        )

    # Post-apply hook: record experience + Drive upload + Notion update
    if result.get("success") and not dry_run:
        ctx = job_context or {}
        try:
            from jobpulse.post_apply_hook import post_apply_hook

            post_apply_hook(
                result=result,
                job_context={
                    "job_id": ctx.get("job_id", ""),
                    "company": ctx.get("company", ""),
                    "title": ctx.get("title", ""),
                    "url": result.get("external_url", url),
                    "platform": platform_key,
                    "ats_platform": ats_platform or platform_key,
                    "notion_page_id": ctx.get("notion_page_id"),
                    "cv_path": str(cv_path),
                    "cover_letter_path": str(cover_letter_path) if cover_letter_path else None,
                    "match_tier": ctx.get("match_tier"),
                    "ats_score": ctx.get("ats_score"),
                    "matched_projects": ctx.get("matched_projects"),
                },
            )
        except Exception as exc:
            logger.warning("post_apply_hook failed: %s — application still recorded", exc)

    if not dry_run:
        # Anti-detection: random delay between submissions (20-45s with jitter)
        delay = random.uniform(20, 45)
        logger.info("Anti-detection delay: %.0fs before next application", delay)
        time.sleep(delay)

    result["rate_limited"] = False
    return result
```

- [ ] **Step 3: Run applicator tests**

Run: `python -m pytest tests/test_applicator.py -v --timeout=30`
Expected: PASS (new optional param doesn't break existing calls)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/applicator.py
git commit -m "feat: wire post_apply_hook into apply_job after successful submission"
```

---

### Task 6: Update scan_pipeline.py to Pass job_context and Remove Duplication

**Files:**
- Modify: `jobpulse/scan_pipeline.py:762-806`

The cron path in `route_and_apply()` currently duplicates post-apply logic (lines 777-801: save_application, Notion update). Since the hook now handles Notion updates, we remove the duplicated Notion call and pass `job_context` to `apply_job()`.

- [ ] **Step 1: Update the apply_job call in route_and_apply to pass job_context**

Replace lines 762-806 (the auto-apply success block):

```python
        try:
            result = apply_job(
                url=listing.url,
                ats_platform=listing.ats_platform,
                cv_path=bundle.cv_path,
                cover_letter_path=bundle.cover_letter_path,
                cl_generator=None,
                custom_answers={
                    "_job_context": {
                        "job_title": listing.title,
                        "company": listing.company,
                        "location": listing.location,
                    },
                },
                job_context={
                    "job_id": listing.job_id,
                    "company": listing.company,
                    "title": listing.title,
                    "notion_page_id": notion_page_id,
                    "cv_path": str(bundle.cv_path),
                    "cover_letter_path": str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
                    "match_tier": tier,
                    "ats_score": ats_score,
                    "matched_projects": bundle.matched_project_names,
                },
            )
            if result.get("success"):
                applied_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
                follow_up = (date.today() + timedelta(days=7)).isoformat()
                db.save_application(
                    job_id=listing.job_id,
                    status="Applied",
                    ats_score=ats_score,
                    match_tier=tier,
                    matched_projects=bundle.matched_project_names,
                    cv_path=str(bundle.cv_path),
                    cover_letter_path=str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
                    applied_at=applied_at,
                    notion_page_id=notion_page_id,
                    follow_up_date=follow_up,
                )
                # Notion update now handled by post_apply_hook inside apply_job
                logger.info(
                    "scan_pipeline: AUTO-APPLIED %s @ %s (ATS %.1f%%)",
                    listing.title, listing.company, ats_score,
                )
                return RouteResult("auto_applied", listing.job_id, listing.title, listing.company)
```

Note: We keep `db.save_application()` here because the hook doesn't know about `JobDB` — the hook handles Notion + Drive + experience learning, while the pipeline handles its own DB record. This separation is intentional: the hook is called from applicator.py (doesn't import JobDB), the pipeline manages its own state.

- [ ] **Step 2: Run scan pipeline tests**

Run: `python -m pytest tests/ -v -k "scan_pipeline or route_and_apply" --timeout=30`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add jobpulse/scan_pipeline.py
git commit -m "feat: pass job_context to apply_job, remove duplicated Notion update"
```

---

### Task 7: Update Documentation and Rules

**Files:**
- Modify: `.claude/rules/jobs.md`
- Modify: `jobpulse/CLAUDE.md`

- [ ] **Step 1: Add Post-Apply Hook section to .claude/rules/jobs.md**

Add after the "Post-Apply Steps" / "Dry Run" sections:

```markdown
## Post-Apply Hook (Automatic)
- `post_apply_hook()` in `jobpulse/post_apply_hook.py` runs after EVERY successful submission
- Called from `apply_job()` in `applicator.py` — both cron and manual paths get it automatically
- Three concerns:
  1. Form experience: records domain, adapter, pages, field types, screening questions, time to `data/form_experience.db`
  2. Drive upload: uploads CV + CL PDFs to Google Drive, gets shareable links
  3. Notion update: sets status=Applied, applied date+time, follow-up date (+7 days), CV/CL Drive links
- `FormExperienceDB` in `jobpulse/form_experience_db.py` — per-domain form learning
  - Cron jobs query this to know form shape before applying (skip LLM page detection for known domains)
  - Success data never overwritten by failures (preserves what worked)
  - Tracks apply_count per domain for confidence scoring
- Hook is non-blocking: any failure is logged but doesn't affect the application result
- Hook runs BEFORE the anti-detection delay so Drive/Notion work happens during the wait
```

- [ ] **Step 2: Add new modules to jobpulse/CLAUDE.md agent list**

Add to the `## Agents` section:

```markdown
- post_apply_hook.py — Unified post-apply: form experience DB, Drive upload, Notion update
- form_experience_db.py — Per-domain form experience store (SQLite): adapter, pages, fields, timing
```

- [ ] **Step 3: Commit**

```bash
git add .claude/rules/jobs.md jobpulse/CLAUDE.md
git commit -m "docs: add post-apply hook and form experience DB to rules and agent list"
```

---

### Task 8: Integration Test — Full Flow

**Files:**
- Create: `tests/jobpulse/test_post_apply_integration.py`

- [ ] **Step 1: Write integration test that exercises the full apply_job → hook → experience path**

```python
"""Integration test: apply_job → post_apply_hook → FormExperienceDB + Notion."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jobpulse.form_experience_db import FormExperienceDB


@pytest.fixture
def exp_db(tmp_path):
    return FormExperienceDB(db_path=str(tmp_path / "form_exp.db"))


@patch("jobpulse.applicator._call_fill_and_submit")
@patch("jobpulse.post_apply_hook.upload_cv", return_value="https://drive.google.com/cv")
@patch("jobpulse.post_apply_hook.upload_cover_letter", return_value="https://drive.google.com/cl")
@patch("jobpulse.post_apply_hook.update_application_page", return_value=True)
def test_apply_job_triggers_hook(mock_notion, mock_cl_up, mock_cv_up, mock_fill, tmp_path):
    """apply_job with job_context triggers the full post-apply chain."""
    mock_fill.return_value = {
        "success": True,
        "pages_filled": 2,
        "field_types": ["text", "select"],
        "screening_questions": ["Salary expectation?"],
    }

    # Patch rate limiter to allow the apply
    with patch("jobpulse.applicator.RateLimiter") as MockLimiter:
        limiter = MockLimiter.return_value
        limiter.can_apply.return_value = True
        limiter.get_remaining.return_value = {"linkedin": 10}
        limiter.get_total_today.return_value = 1
        limiter.should_take_break.return_value = False
        limiter.get_platform_count.return_value = 1

        # Patch the anti-detection sleep to avoid slow test
        with patch("jobpulse.applicator.time.sleep"):
            from jobpulse.applicator import apply_job

            # Patch the form experience DB path inside the hook
            with patch("jobpulse.post_apply_hook.FormExperienceDB") as MockExpDB:
                mock_db_instance = MagicMock()
                MockExpDB.return_value = mock_db_instance

                result = apply_job(
                    url="https://boards.greenhouse.io/testcorp/jobs/123",
                    ats_platform="greenhouse",
                    cv_path=Path("/tmp/test_cv.pdf"),
                    cover_letter_path=Path("/tmp/test_cl.pdf"),
                    job_context={
                        "job_id": "test-123",
                        "company": "TestCorp",
                        "title": "ML Engineer",
                        "notion_page_id": "notion-abc",
                        "match_tier": "auto",
                        "ats_score": 97.0,
                        "matched_projects": ["proj1"],
                    },
                )

    assert result["success"] is True

    # Verify hook ran: Drive upload called
    mock_cv_up.assert_called_once()
    mock_cl_up.assert_called_once()

    # Verify hook ran: Notion updated
    mock_notion.assert_called_once()
    notion_kwargs = mock_notion.call_args[1]
    assert notion_kwargs["status"] == "Applied"
    assert notion_kwargs["cv_drive_link"] == "https://drive.google.com/cv"

    # Verify hook ran: form experience recorded
    mock_db_instance.record.assert_called_once()
    record_kwargs = mock_db_instance.record.call_args[1]
    assert record_kwargs["platform"] == "greenhouse"
    assert record_kwargs["pages_filled"] == 2
```

- [ ] **Step 2: Run integration test**

Run: `python -m pytest tests/jobpulse/test_post_apply_integration.py -v --timeout=30`
Expected: PASS

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `python -m pytest tests/ -v --timeout=60 -x -q`
Expected: All tests PASS, no regressions

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_post_apply_integration.py
git commit -m "test: add integration test for apply_job → post_apply_hook flow"
```

---

## Summary of Changes

| Concern | Before | After |
|---------|--------|-------|
| Form learning | None — cron hits every domain blind | `FormExperienceDB` records adapter, pages, field types, timing per domain |
| Drive upload (cron) | Only in `generate_materials()` pre-apply | Also in `post_apply_hook()` post-apply (redundant uploads are idempotent) |
| Notion update (cron) | Partial: status + date only, no Drive links | Full: status, date, time, follow-up, CV/CL Drive links, company |
| Notion update (manual) | Not automated | Same hook fires for manual `apply_job()` calls |
| Post-apply logic | Scattered across `route_and_apply()` + manual callers | Single `post_apply_hook()` called from `apply_job()` |
| Screening questions | Cached cross-domain | Now also recorded per-domain in experience DB |

## What This Enables for Cron

After a few manual applications to a domain, the cron can:
1. Query `FormExperienceDB.lookup(domain)` to know: "Greenhouse forms have 3 pages, text/select/upload fields, asks about sponsorship"
2. Skip LLM page detection for known domains (already handled by `NavigationLearner`)
3. Pre-load screening answers that worked for this domain's question set
4. All Notion fields are filled automatically — no manual cleanup needed after cron runs
