# Job Pipeline Error Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 60+ edge cases and error handling gaps across the job application pipeline using 4 shared utilities + surgical fixes in 4 phases.

**Architecture:** Create `jobpulse/utils/safe_io.py` with 4 utilities (managed_browser, safe_openai_call, locked_json_file, atomic_sqlite), then apply targeted fixes across 10 files. No new abstractions beyond these utilities.

**Tech Stack:** Python 3.12, Playwright, OpenAI SDK, SQLite3, fcntl (macOS file locking)

---

## Phase 1: Shared Utilities

### Task 1: Create `jobpulse/utils/__init__.py` and `safe_io.py` with `managed_browser`

**Files:**
- Create: `jobpulse/utils/__init__.py`
- Create: `jobpulse/utils/safe_io.py`
- Create: `tests/jobpulse/test_safe_io.py`

- [x] **Step 1: Create the utils package**

```bash
mkdir -p /Users/yashbishnoi/Downloads/multi_agent_patterns/jobpulse/utils
mkdir -p /Users/yashbishnoi/Downloads/multi_agent_patterns/tests/jobpulse
```

Create `jobpulse/utils/__init__.py`:

```python
"""Shared I/O utilities for the jobpulse pipeline."""
```

- [x] **Step 2: Write failing test for `managed_browser`**

Create `tests/jobpulse/test_safe_io.py`:

```python
"""Tests for jobpulse/utils/safe_io.py utilities."""

from unittest.mock import MagicMock, patch


def test_managed_browser_closes_on_success():
    """Browser is closed even when body succeeds."""
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_page = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page

    mock_pw_ctx = MagicMock()
    mock_pw_ctx.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_ctx.__exit__ = MagicMock(return_value=False)

    with patch("jobpulse.utils.safe_io.sync_playwright", return_value=mock_pw_ctx):
        from jobpulse.utils.safe_io import managed_browser

        with managed_browser() as (browser, page):
            assert browser is mock_browser
            assert page is mock_page

    mock_browser.close.assert_called_once()


def test_managed_browser_closes_on_exception():
    """Browser is closed even when body raises."""
    mock_pw = MagicMock()
    mock_browser = MagicMock()
    mock_page = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page

    mock_pw_ctx = MagicMock()
    mock_pw_ctx.__enter__ = MagicMock(return_value=mock_pw)
    mock_pw_ctx.__exit__ = MagicMock(return_value=False)

    with patch("jobpulse.utils.safe_io.sync_playwright", return_value=mock_pw_ctx):
        from jobpulse.utils.safe_io import managed_browser

        try:
            with managed_browser() as (browser, page):
                raise RuntimeError("test crash")
        except RuntimeError:
            pass

    mock_browser.close.assert_called_once()
```

- [x] **Step 3: Run test to verify it fails**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py::test_managed_browser_closes_on_success -v`
Expected: FAIL (ImportError — module does not exist yet)

- [x] **Step 4: Implement `managed_browser`**

Create `jobpulse/utils/safe_io.py`:

```python
"""Shared I/O utilities — browser lifecycle, OpenAI calls, file locking, SQLite atomicity."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Generator

from shared.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. managed_browser — guaranteed browser cleanup
# ---------------------------------------------------------------------------

def _import_playwright():
    """Lazy import to avoid hard dependency."""
    from playwright.sync_api import sync_playwright  # type: ignore[import]
    return sync_playwright


@contextlib.contextmanager
def managed_browser(
    headless: bool = True,
    **launch_args: Any,
) -> Generator[tuple[Any, Any], None, None]:
    """Context manager that guarantees browser.close() even on exception.

    Yields (browser, page) tuple.

    Usage:
        with managed_browser(headless=False) as (browser, page):
            page.goto("https://example.com")
    """
    sync_playwright = _import_playwright()
    browser = None
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=headless, **launch_args)
            page = browser.new_page()
            yield browser, page
        finally:
            if browser:
                with contextlib.suppress(Exception):
                    browser.close()
                logger.debug("managed_browser: browser closed")


@contextlib.contextmanager
def managed_persistent_browser(
    user_data_dir: str,
    **launch_args: Any,
) -> Generator[tuple[Any, Any], None, None]:
    """Context manager for persistent browser contexts (e.g. LinkedIn with saved cookies).

    Yields (context, page) — context IS the browser for persistent contexts.
    """
    sync_playwright = _import_playwright()
    context = None
    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(user_data_dir, **launch_args)
            page = context.new_page()
            yield context, page
        finally:
            if context:
                with contextlib.suppress(Exception):
                    context.close()
                logger.debug("managed_persistent_browser: context closed")
```

- [x] **Step 5: Run test to verify it passes**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py -v`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add jobpulse/utils/__init__.py jobpulse/utils/safe_io.py tests/jobpulse/test_safe_io.py
git commit -m "feat(jobs): add managed_browser context manager for guaranteed cleanup"
```

---

### Task 2: Add `safe_openai_call` to `safe_io.py`

**Files:**
- Modify: `jobpulse/utils/safe_io.py`
- Modify: `tests/jobpulse/test_safe_io.py`

- [x] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_safe_io.py`:

```python
def test_safe_openai_call_returns_content():
    """Returns content string on successful API call."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Hello world"
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result == "Hello world"


def test_safe_openai_call_returns_none_on_none_content():
    """Returns None when API returns None content."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = None
    mock_client.chat.completions.create.return_value = MagicMock(choices=[mock_choice])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None


def test_safe_openai_call_returns_none_on_exception():
    """Returns None when API raises an exception."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = TimeoutError("API timeout")

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None


def test_safe_openai_call_returns_none_on_empty_choices():
    """Returns None when API returns empty choices list."""
    from jobpulse.utils.safe_io import safe_openai_call

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(choices=[])

    result = safe_openai_call(mock_client, model="gpt-4o-mini", messages=[{"role": "user", "content": "hi"}])
    assert result is None
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py::test_safe_openai_call_returns_content -v`
Expected: FAIL (ImportError — function not defined)

- [x] **Step 3: Implement `safe_openai_call`**

Add to `jobpulse/utils/safe_io.py` after the browser section:

```python
# ---------------------------------------------------------------------------
# 2. safe_openai_call — timeout + None-safe wrapper
# ---------------------------------------------------------------------------


def safe_openai_call(
    client: Any,
    *,
    model: str = "gpt-4o-mini",
    messages: list[dict[str, str]],
    temperature: float = 0.5,
    timeout: float = 60.0,
    caller: str = "",
    **kwargs: Any,
) -> str | None:
    """Call OpenAI chat completions with timeout and None-safety.

    Returns content string on success, None on any failure.
    Never raises — logs the error instead.
    """
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
        if not response.choices:
            logger.warning("safe_openai_call(%s): empty choices list", caller)
            return None

        content = response.choices[0].message.content
        if content is None:
            logger.warning("safe_openai_call(%s): response content is None", caller)
            return None

        return content

    except Exception as exc:
        logger.error("safe_openai_call(%s): %s: %s", caller, type(exc).__name__, exc)
        return None
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add jobpulse/utils/safe_io.py tests/jobpulse/test_safe_io.py
git commit -m "feat(jobs): add safe_openai_call wrapper with timeout and None-safety"
```

---

### Task 3: Add `locked_json_file` to `safe_io.py`

**Files:**
- Modify: `jobpulse/utils/safe_io.py`
- Modify: `tests/jobpulse/test_safe_io.py`

- [x] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_safe_io.py`:

```python
import tempfile


def test_locked_json_file_reads_and_writes(tmp_path):
    """Reads existing JSON, allows mutation, writes back."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[{"id": 1}]')

    with locked_json_file(json_file) as data:
        assert data == [{"id": 1}]
        data.append({"id": 2})

    result = json.loads(json_file.read_text())
    assert result == [{"id": 1}, {"id": 2}]


def test_locked_json_file_creates_file_if_missing(tmp_path):
    """Creates file with default value if it doesn't exist."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "new.json"
    assert not json_file.exists()

    with locked_json_file(json_file, default=[]) as data:
        data.append("hello")

    assert json_file.exists()
    assert json.loads(json_file.read_text()) == ["hello"]


def test_locked_json_file_no_write_on_exception(tmp_path):
    """Does NOT write back if body raises an exception."""
    from jobpulse.utils.safe_io import locked_json_file

    json_file = tmp_path / "test.json"
    json_file.write_text('[1, 2, 3]')

    try:
        with locked_json_file(json_file) as data:
            data.append(4)
            raise ValueError("abort!")
    except ValueError:
        pass

    # File should still have the original content
    assert json.loads(json_file.read_text()) == [1, 2, 3]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py::test_locked_json_file_reads_and_writes -v`
Expected: FAIL

- [x] **Step 3: Implement `locked_json_file`**

Add to `jobpulse/utils/safe_io.py`:

```python
# ---------------------------------------------------------------------------
# 3. locked_json_file — atomic read-modify-write with file locking
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def locked_json_file(
    path: Path,
    default: Any = None,
) -> Generator[Any, None, None]:
    """Read-modify-write a JSON file with file locking.

    - Acquires an exclusive lock before reading.
    - Yields the parsed data for mutation.
    - Writes back atomically (tmp + rename) on clean exit.
    - Does NOT write back if the body raises an exception.
    - Creates the file with `default` if it doesn't exist.

    Usage:
        with locked_json_file(Path("data.json"), default=[]) as data:
            data.append(new_item)
        # file is now updated
    """
    import fcntl

    if default is None:
        default = []

    path.parent.mkdir(parents=True, exist_ok=True)

    # Open or create
    if not path.exists():
        path.write_text(json.dumps(default), encoding="utf-8")

    with open(path, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            raw = fh.read()
            data = json.loads(raw) if raw.strip() else default

            yield data

            # Write back atomically — only reached if body didn't raise
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp_path.rename(path)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add jobpulse/utils/safe_io.py tests/jobpulse/test_safe_io.py
git commit -m "feat(jobs): add locked_json_file for atomic JSON read-modify-write"
```

---

### Task 4: Add `atomic_sqlite` to `safe_io.py`

**Files:**
- Modify: `jobpulse/utils/safe_io.py`
- Modify: `tests/jobpulse/test_safe_io.py`

- [x] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_safe_io.py`:

```python
def test_atomic_sqlite_commits_on_success(tmp_path):
    """Transaction commits when body succeeds."""
    from jobpulse.utils.safe_io import atomic_sqlite

    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")

    with atomic_sqlite(db_path) as conn:
        conn.execute("INSERT INTO t VALUES (1)")

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT id FROM t").fetchone()
        assert row == (1,)


def test_atomic_sqlite_rolls_back_on_exception(tmp_path):
    """Transaction rolls back when body raises."""
    from jobpulse.utils.safe_io import atomic_sqlite

    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")

    try:
        with atomic_sqlite(db_path) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("abort!")
    except RuntimeError:
        pass

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM t").fetchone()
        assert row == (0,)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py::test_atomic_sqlite_commits_on_success -v`
Expected: FAIL

- [x] **Step 3: Implement `atomic_sqlite`**

Add to `jobpulse/utils/safe_io.py`:

```python
# ---------------------------------------------------------------------------
# 4. atomic_sqlite — exclusive transaction with auto-commit/rollback
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def atomic_sqlite(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """SQLite context manager with BEGIN EXCLUSIVE for atomic operations.

    - Acquires exclusive lock on the database.
    - Auto-commits on clean exit.
    - Auto-rolls back on exception.

    Usage:
        with atomic_sqlite("rate_limits.db") as conn:
            conn.execute("INSERT ...")
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/test_safe_io.py -v`
Expected: ALL PASS

- [x] **Step 5: Commit**

```bash
git add jobpulse/utils/safe_io.py tests/jobpulse/test_safe_io.py
git commit -m "feat(jobs): add atomic_sqlite for exclusive transactions"
```

---

## Phase 2: P0 Crash / Data-Loss Fixes

### Task 5: Fix browser resource leak in `job_scanner.py`

**Files:**
- Modify: `jobpulse/job_scanner.py:252-343` (scan_linkedin browser lifecycle)

- [x] **Step 1: Replace raw Playwright with `managed_persistent_browser`**

In `jobpulse/job_scanner.py`, replace the browser open/close block in `scan_linkedin()`.

Find at line 252-343 the block:
```python
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch_persistent_context(
                str(chrome_profile),
                headless=False,
                executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
                user_agent=_random_ua(),
                viewport={"width": 1280, "height": 800},
            )
            page = browser.new_page()
```

Replace with:

```python
    try:
        from jobpulse.utils.safe_io import managed_persistent_browser

        with managed_persistent_browser(
            user_data_dir=str(chrome_profile),
            headless=False,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
            user_agent=_random_ua(),
            viewport={"width": 1280, "height": 800},
        ) as (browser, page):
```

Then indent the entire body (lines 268-341) one level deeper inside the `with` block.

Remove the old `browser.close()` call at line 343 (now handled by the context manager).

Remove the now-unnecessary `from playwright.sync_api import sync_playwright` lazy import inside the try block (line 252 area), since `managed_persistent_browser` handles it internally. Keep the earlier ImportError check at lines 231-238.

- [x] **Step 2: Run existing tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "scanner or job_scan" -v --no-header 2>&1 | head -30`
Expected: PASS (or no tests exist for this yet — verify no regressions)

- [x] **Step 3: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "fix(jobs): use managed_persistent_browser in scan_linkedin for guaranteed cleanup"
```

---

### Task 6: Fix browser resource leak in all ATS adapters

**Files:**
- Modify: `jobpulse/ats_adapters/generic.py:35-72`
- Modify: `jobpulse/ats_adapters/greenhouse.py:33-77`
- Modify: `jobpulse/ats_adapters/indeed.py:33-66`
- Modify: `jobpulse/ats_adapters/lever.py:33-76`
- Modify: `jobpulse/ats_adapters/linkedin.py:33-67`
- Modify: `jobpulse/ats_adapters/workday.py:33-68`

- [x] **Step 1: Fix `generic.py`**

Replace the inner try block (lines 35-72) in `fill_and_submit`:

```python
        logger.info("Generic form fill: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(headless=True) as (browser, page):
                page.goto(url, timeout=30000)

                # Best-effort: fill common input patterns by name/placeholder/label
                _fill_by_pattern(page, "email", profile.get("email", ""))
                _fill_by_pattern(page, "phone", profile.get("phone", ""))
                _fill_by_pattern(page, "first", profile.get("first_name", ""))
                _fill_by_pattern(page, "last", profile.get("last_name", ""))
                _fill_by_pattern(
                    page,
                    "name",
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
                )

                # Upload CV to first file input found
                file_input = page.query_selector("input[type='file']")
                if file_input and cv_path.exists():
                    file_input.set_input_files(str(cv_path))

                # Fill custom answers by field name or id
                for field_key, answer in custom_answers.items():
                    el = page.query_selector(f"[name='{field_key}'], [id='{field_key}']")
                    if el:
                        el.fill(str(answer))

                screenshot_path = cv_path.parent / "generic_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Generic adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
```

Remove the duplicate `from playwright.sync_api import sync_playwright` import at line 36 (the `managed_browser` handles it). Keep the ImportError check at lines 28-32.

- [x] **Step 2: Apply the same pattern to the other 5 adapters**

For each adapter (`greenhouse.py`, `indeed.py`, `lever.py`, `linkedin.py`, `workday.py`):

1. Replace `from playwright.sync_api import sync_playwright` (inner duplicate) with `from jobpulse.utils.safe_io import managed_browser`
2. Replace raw `with sync_playwright() as p: browser = p.chromium.launch(...)` + `page = browser.new_page()` with `with managed_browser(headless=True) as (browser, page):`
3. Remove the `browser.close()` line (context manager handles it)
4. Keep the outer `except Exception` handler

The pattern is identical across all adapters — the only differences are the form-filling logic inside the `with` block, which stays unchanged.

- [x] **Step 3: Run tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "adapter or ats" -v --no-header 2>&1 | head -30`
Expected: PASS

- [x] **Step 4: Commit**

```bash
git add jobpulse/ats_adapters/
git commit -m "fix(jobs): use managed_browser in all 6 ATS adapters for guaranteed cleanup"
```

---

### Task 7: Fix OpenAI None responses in `cv_tailor.py`

**Files:**
- Modify: `jobpulse/cv_tailor.py:220-225` and line 248

- [x] **Step 1: Replace raw OpenAI call with `safe_openai_call`**

In `jobpulse/cv_tailor.py`, in the `generate_tailored_cv` function:

Replace lines 220-225:
```python
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=messages,
        )
        tex_content: str = response.choices[0].message.content or ""
```

With:
```python
        from jobpulse.utils.safe_io import safe_openai_call

        tex_content = safe_openai_call(
            client,
            model="gpt-4o-mini",
            temperature=0.3,
            messages=messages,
            caller=f"cv_tailor:attempt_{attempt}",
        )
        if tex_content is None:
            logger.warning("cv_tailor: LLM returned None for job %s attempt %d", job.job_id, attempt)
            continue
```

Add logger import at top of function (after existing lazy imports):
```python
    from shared.logging_config import get_logger
    logger = get_logger(__name__)
```

- [x] **Step 2: Replace assert with proper error handling**

Replace line 248:
```python
    assert best_score is not None
```

With:
```python
    if best_score is None:
        from jobpulse.models.application_models import ATSScore
        logger.error("cv_tailor: all %d refinement attempts returned None for job %s", 3, job.job_id)
        return None, ATSScore(total=0, keyword_score=0, section_score=0, format_score=0,
                              missing_keywords=[], matched_keywords=[], passed=False)
```

- [x] **Step 3: Run tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "cv_tailor or cv" -v --no-header 2>&1 | head -30`
Expected: PASS

- [x] **Step 4: Commit**

```bash
git add jobpulse/cv_tailor.py
git commit -m "fix(jobs): use safe_openai_call in cv_tailor, replace assert with proper error"
```

---

### Task 8: Fix OpenAI None response in `cover_letter_agent.py`

**Files:**
- Modify: `jobpulse/cover_letter_agent.py:40-42` and `144-150`

- [x] **Step 1: Add template file missing fallback**

Replace `_load_template()` (line 40-42):

```python
def _load_template() -> str:
    """Load cover letter template from jobpulse/templates/Cover letter template.md."""
    try:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        from shared.logging_config import get_logger
        get_logger(__name__).error(
            "cover_letter_agent: template not found at %s. "
            "Ensure jobpulse/templates/ directory contains 'Cover letter template.md'.",
            _TEMPLATE_PATH,
        )
        return ""
```

- [x] **Step 2: Replace raw OpenAI call with `safe_openai_call`**

Replace lines 144-150 inside the try block:

```python
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
        )
        cover_letter_text: str = response.choices[0].message.content or ""
```

With:

```python
        from jobpulse.utils.safe_io import safe_openai_call

        cover_letter_text = safe_openai_call(
            client,
            model="gpt-4o-mini",
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
            caller="cover_letter_agent",
        )
        if cover_letter_text is None:
            logger.warning("cover_letter_agent: LLM returned None for job %s", job.job_id)
            return None
```

- [x] **Step 3: Import PROFILE from applicator instead of hardcoding**

Replace lines 21-32 (the `_PROFILE` dict):

```python
# Use the canonical profile from applicator.py — single source of truth
from jobpulse.applicator import PROFILE as _APPLICATOR_PROFILE

_PROFILE = {
    "name": f"{_APPLICATOR_PROFILE.get('first_name', '')} {_APPLICATOR_PROFILE.get('last_name', '')}".strip(),
    "education": [_APPLICATOR_PROFILE.get("education", "")],
    "experience": [
        "Team Leader, Co-op (Apr 2025 - Present)",
        "Market Research Analyst, Nidhi Herbal (Jul 2021 - Sep 2024)",
    ],
    "visa": "Student Visa; converting to Graduate Visa from 9 May 2026",
}
```

Note: The experience list is not in PROFILE so we keep it here, but name and education now come from the canonical source.

- [x] **Step 4: Fix JD truncation to respect sentence boundaries**

Replace line 68:
```python
    jd_snippet = jd_text[:2000]
```

With:
```python
    # Truncate at sentence boundary to avoid cutting mid-requirement
    if len(jd_text) <= 2000:
        jd_snippet = jd_text
    else:
        cut = jd_text[:2000]
        # Find last sentence-ending punctuation
        last_period = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind(".\t"))
        jd_snippet = cut[:last_period + 1] if last_period > 1500 else cut
```

- [x] **Step 5: Run tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "cover_letter" -v --no-header 2>&1 | head -30`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git add jobpulse/cover_letter_agent.py
git commit -m "fix(jobs): safe_openai_call, template fallback, profile import, sentence truncation"
```

---

### Task 9: Fix pending review race condition in `job_autopilot.py`

**Files:**
- Modify: `jobpulse/job_autopilot.py:85-99` (_load_pending and _save_pending)

- [x] **Step 1: Replace `_load_pending` and `_save_pending` with `locked_json_file`**

Replace lines 85-99:

```python
def _load_pending() -> list[dict[str, Any]]:
    """Load pending review jobs from file. Returns [] if file missing or invalid."""
    if not PENDING_REVIEW_FILE.exists():
        return []
    try:
        return json.loads(PENDING_REVIEW_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("job_autopilot: could not load pending_review_jobs: %s", exc)
        return []


def _save_pending(jobs: list[dict[str, Any]]) -> None:
    """Persist pending review jobs to file."""
    PENDING_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEW_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
```

With:

```python
def _load_pending() -> list[dict[str, Any]]:
    """Load pending review jobs from file. Returns [] if file missing or invalid."""
    if not PENDING_REVIEW_FILE.exists():
        return []
    try:
        return json.loads(PENDING_REVIEW_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("job_autopilot: could not load pending_review_jobs: %s", exc)
        return []


def _save_pending(jobs: list[dict[str, Any]]) -> None:
    """Persist pending review jobs to file."""
    PENDING_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEW_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")


def _append_pending(new_jobs: list[dict[str, Any]]) -> None:
    """Atomically append jobs to the pending review file (race-safe)."""
    from jobpulse.utils.safe_io import locked_json_file

    with locked_json_file(PENDING_REVIEW_FILE, default=[]) as data:
        data.extend(new_jobs)
```

Then in `_queue_for_review` (line 457 area) and `_send_review_batch` (line 473 area), replace calls to `_load_pending()` + `_save_pending()` with `_append_pending()` where appropriate. Specifically, anywhere the pattern is:

```python
pending = _load_pending()
pending.append(job_dict)
_save_pending(pending)
```

Replace with:

```python
_append_pending([job_dict])
```

- [x] **Step 2: Run tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "autopilot" -v --no-header 2>&1 | head -30`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add jobpulse/job_autopilot.py
git commit -m "fix(jobs): atomic pending review file access via locked_json_file"
```

---

## Phase 3: P1 Silent Failure Fixes

### Task 10: Add rate limit detection + backoff to `job_scanner.py`

**Files:**
- Modify: `jobpulse/job_scanner.py:147-191` (scan_reed HTTP handling)

- [x] **Step 1: Add retry with backoff for 429 responses**

In `scan_reed()`, replace the HTTP request block (inside the try at line 149-191):

Find the existing `with httpx.Client(timeout=20) as client:` block and wrap the request in a retry loop:

```python
        with httpx.Client(timeout=20) as client:
            for retry in range(3):
                resp = client.get(
                    base_url,
                    params=params,
                    auth=(REED_API_KEY, ""),
                    headers={"User-Agent": _random_ua()},
                )

                if resp.status_code == 429:
                    wait = 2 ** (retry + 1)  # 2s, 4s, 8s
                    logger.warning(
                        "scan_reed: rate limited (429), retrying in %ds (attempt %d/3)",
                        wait, retry + 1,
                    )
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                break
            else:
                # All 3 retries hit 429
                logger.error("scan_reed: rate limited after 3 retries for '%s'", title)
                continue  # skip to next title
```

- [x] **Step 2: Add template file missing fallback to `cv_tailor.py`**

In `cv_tailor.py`, replace line 39:

```python
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
```

With:

```python
    try:
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        from shared.logging_config import get_logger
        get_logger(__name__).error(
            "cv_tailor: Resume Prompt template not found at %s", _TEMPLATE_PATH
        )
        return ""
```

- [x] **Step 3: Add stub platform warnings to `job_scanner.py`**

In `scan_platforms()` (line 422), modify the platform dispatch loop. Find where unknown/stub platforms are handled. Add explicit warnings:

In `scan_totaljobs()` (line 352), change the return to include a log at WARNING level (it already does at line 365-368, so this is already handled).

In `scan_glassdoor()` (line 372), ensure the stub warning is at WARNING level too (check it already is).

In `scan_indeed()` (line 198), add a similar warning if it's a stub:

Check if `scan_indeed` is also a stub. If yes, add:
```python
    logger.warning("scan_indeed: stub — returning []. HTML scraper not yet implemented.")
```

- [x] **Step 4: Commit**

```bash
git add jobpulse/job_scanner.py jobpulse/cv_tailor.py
git commit -m "fix(jobs): rate limit backoff, template fallback, stub platform warnings"
```

---

### Task 11: Notion failure visibility in `job_autopilot.py`

**Files:**
- Modify: `jobpulse/job_autopilot.py` (multiple Notion try/except blocks)

- [x] **Step 1: Add Notion failure counter**

Near the top of `run_scan_window()` (after line 136), add:

```python
    notion_failures: list[str] = []
```

- [x] **Step 2: Track Notion failures instead of silently ignoring**

In each Notion try/except block within `run_scan_window()` (lines 236-241, 304-317, 354-365, 396-399), change the except handler to also append to the failure list:

Example pattern — replace:
```python
        except Exception as exc:
            logger.warning("job_autopilot: Notion update failed: %s", exc)
```

With:
```python
        except Exception as exc:
            logger.warning("job_autopilot: Notion update failed: %s", exc)
            notion_failures.append(f"{listing.title}: {exc}")
```

- [x] **Step 3: Send summary alert at end of pipeline**

At the end of `run_scan_window()`, before the return, add:

```python
    if notion_failures:
        logger.warning("job_autopilot: %d Notion sync failures this run", len(notion_failures))
        # Append to summary so Telegram picks it up
        summary += f"\n\n⚠️ {len(notion_failures)} Notion sync(s) failed — check logs."
```

- [x] **Step 4: Commit**

```bash
git add jobpulse/job_autopilot.py
git commit -m "fix(jobs): track and surface Notion sync failures instead of silent ignore"
```

---

### Task 12: Distinguish errors from empty results in `github_agent.py`

**Files:**
- Modify: `jobpulse/github_agent.py:25-35` (_gh_api function)

- [x] **Step 1: Return tuple instead of plain list**

Change `_gh_api` to return `(data, error)` tuple:

Replace lines 25-35:

```python
def _gh_api(endpoint: str) -> list:
    """Call gh api and return parsed JSON list."""
    try:
        result = subprocess.run(
            [_find_gh(), "api", endpoint, "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except Exception as exc:
        logger.error("gh api error: %s", exc)
        return []
```

With:

```python
def _gh_api(endpoint: str) -> tuple[list, str | None]:
    """Call gh api and return (parsed_data, error_string | None)."""
    try:
        result = subprocess.run(
            [_find_gh(), "api", endpoint, "--paginate"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip() or f"gh api exited with code {result.returncode}"
            logger.error("gh api error for %s: %s", endpoint, err)
            return [], err
        if not result.stdout.strip():
            return [], None  # Success, but no data
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            data = [data]
        return data, None
    except Exception as exc:
        logger.error("gh api error: %s", exc)
        return [], str(exc)
```

- [x] **Step 2: Update callers to handle the tuple**

In `get_yesterday_commits()` and `get_trending_repos()`, update calls from:
```python
repos = _gh_api(f"/users/{GITHUB_USERNAME}/repos?sort=pushed&per_page=30")
```
To:
```python
repos, api_err = _gh_api(f"/users/{GITHUB_USERNAME}/repos?sort=pushed&per_page=30")
if api_err:
    trail.log_step("error", f"GitHub API failed: {api_err}")
```

Do the same for the commits API call inside the loop.

- [x] **Step 3: Commit**

```bash
git add jobpulse/github_agent.py
git commit -m "fix(jobs): distinguish API errors from empty results in github_agent"
```

---

### Task 13: Distinguish errors from empty results in `jd_analyzer.py`

**Files:**
- Modify: `jobpulse/jd_analyzer.py:280-333` (extract_skills_llm)

- [x] **Step 1: Add empty JD validation**

At the start of `extract_skills_llm()` (line 280), add:

```python
def extract_skills_llm(jd_text: str) -> dict:
    """Extract skills from JD using LLM. Returns dict with required/preferred skills or empty on failure."""
    if not jd_text or not jd_text.strip():
        logger.warning("extract_skills_llm: received empty JD text, skipping LLM call")
        return {"required_skills": [], "preferred_skills": [], "error": "empty_jd"}
```

- [x] **Step 2: Add error field to return value**

In the existing except block (around line 293-333), change the return from:
```python
        return {"required_skills": [], "preferred_skills": []}
```
To:
```python
        return {"required_skills": [], "preferred_skills": [], "error": str(exc)}
```

And on success path, add `"error": None` to the return dict.

- [x] **Step 3: Commit**

```bash
git add jobpulse/jd_analyzer.py
git commit -m "fix(jobs): validate empty JD, add error field to skill extraction result"
```

---

## Phase 4: P2 Concurrency + Validation Fixes

### Task 14: Atomic rate limiter

**Files:**
- Modify: `jobpulse/rate_limiter.py:87-103` (record_application method)

- [x] **Step 1: Replace `record_application` with atomic transaction**

Replace `record_application` method:

```python
    def record_application(self, platform: str) -> None:
        """Increment today's count for the given platform (atomic)."""
        from jobpulse.utils.safe_io import atomic_sqlite

        platform = platform.lower()
        today = self._today()
        with atomic_sqlite(self.db_path) as conn:
            conn.execute(
                """INSERT INTO daily_counts (date, platform, count) VALUES (?, ?, 1)
                   ON CONFLICT(date, platform) DO UPDATE SET count = count + 1""",
                (today, platform),
            )
            row = conn.execute(
                "SELECT COALESCE(SUM(count), 0) FROM daily_counts WHERE date = ?",
                (today,),
            ).fetchone()
            total = row[0] if row else 0
            conn.execute(
                """INSERT INTO session_tracker (date, total_today, last_break_at) VALUES (?, ?, 0)
                   ON CONFLICT(date) DO UPDATE SET total_today = ?""",
                (today, total, total),
            )
        logger.info("Recorded application on %s (total today: %d)", platform, total)
```

Note: We compute `total` inside the same exclusive transaction instead of calling `self.get_total_today()` which opens a separate connection.

- [x] **Step 2: Run tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -k "rate_limit" -v --no-header 2>&1 | head -30`
Expected: PASS

- [x] **Step 3: Commit**

```bash
git add jobpulse/rate_limiter.py
git commit -m "fix(jobs): atomic rate limiter via BEGIN EXCLUSIVE transaction"
```

---

### Task 15: ATS scorer robustness

**Files:**
- Modify: `jobpulse/ats_scorer.py:120-131` and `175-186`

- [x] **Step 1: Warn when synonym file is missing**

In `_load_synonyms()`, change the except block:

```python
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning(
            "ats_scorer: synonym file missing or invalid at %s — "
            "keyword matching will not use synonyms. Error: %s",
            synonym_path, exc,
        )
        return {}
```

(Add `synonym_path` variable before the try block if not already defined.)

- [x] **Step 2: Use word-boundary regex for keyword matching**

In `_word_present()` (line 175), replace:

```python
def _word_present(keyword: str, text: str) -> bool:
```

Update the body to use word boundaries:

```python
def _word_present(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word in text."""
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))
```

(If the function already uses a similar pattern, verify and adjust. The key fix is adding `\b` word boundaries to prevent "Python" matching inside "PythonScript" or company names.)

- [x] **Step 3: Commit**

```bash
git add jobpulse/ats_scorer.py
git commit -m "fix(jobs): warn on missing synonyms, use word-boundary regex in ATS scorer"
```

---

### Task 16: Input validation in `job_scanner.py`

**Files:**
- Modify: `jobpulse/job_scanner.py:88-106` (load_search_config)
- Modify: `jobpulse/job_scanner.py:422-455` (scan_platforms)

- [x] **Step 1: Add config validation**

In `load_search_config()` after `json.loads()` / validation, add a check:

```python
    if not config.titles:
        logger.warning("load_search_config: no job titles configured — scan will find nothing")
```

- [x] **Step 2: Warn on unimplemented platforms**

In `scan_platforms()`, after the platform dispatch loop, for any platform that returned `[]` AND is known to be a stub, log clearly:

Check if the function already has a mapping of platform→scanner. If it dispatches via a dict like `scanners = {"reed": scan_reed, "linkedin": scan_linkedin, ...}`, add a set of stub platforms:

```python
    STUB_PLATFORMS = {"indeed", "totaljobs", "glassdoor"}
```

And in the loop:
```python
        if platform in STUB_PLATFORMS:
            logger.warning(
                "scan_platforms: '%s' is not yet implemented — skipping. "
                "Only reed and linkedin are functional.",
                platform,
            )
            continue
```

- [x] **Step 3: Validate empty URL hashing**

In `_make_job_id()` (line 69), add:

```python
def _make_job_id(url: str) -> str:
    if not url:
        logger.warning("_make_job_id: received empty URL, generating random ID")
        import uuid
        return f"unknown-{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(url.encode()).hexdigest()[:16]
```

- [x] **Step 4: Commit**

```bash
git add jobpulse/job_scanner.py
git commit -m "fix(jobs): input validation — config, stub platforms, empty URL hashing"
```

---

### Task 17: Final verification

**Files:** None (verification only)

- [x] **Step 1: Run full test suite**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -v --no-header 2>&1 | tail -20`
Expected: ALL PASS

- [x] **Step 2: Verify imports resolve**

Run:
```bash
cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -c "
from jobpulse.utils.safe_io import managed_browser, managed_persistent_browser, safe_openai_call, locked_json_file, atomic_sqlite
print('All imports OK')
"
```
Expected: "All imports OK"

- [x] **Step 3: Lint check**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m ruff check jobpulse/utils/ jobpulse/job_scanner.py jobpulse/cv_tailor.py jobpulse/cover_letter_agent.py jobpulse/job_autopilot.py jobpulse/rate_limiter.py jobpulse/github_agent.py jobpulse/jd_analyzer.py jobpulse/ats_scorer.py jobpulse/ats_adapters/ 2>&1 | head -20`
Expected: No errors (or only pre-existing ones)

- [x] **Step 4: Commit any lint fixes**

```bash
git add -u
git commit -m "chore(jobs): lint fixes from error hardening"
```
