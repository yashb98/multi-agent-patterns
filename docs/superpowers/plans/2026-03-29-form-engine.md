# Form Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic form engine that detects and fills any standard HTML input type (select, radio, checkbox, textarea, date, file, search/autocomplete, multi-select) with edge case handling for production ATS forms.

**Architecture:** A `form_engine/` package with one module per input type, a detector that classifies DOM elements, a validation module for error detection/retry, and a shared `FillResult` data class. Each filler is a standalone function that takes a Playwright page + selector + value and returns a structured result. The engine integrates with the existing `screening_answers.py` for question→answer resolution.

**Tech Stack:** Python 3.12, Playwright (sync API), Pydantic for result types, existing `shared/logging_config.py`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/form_engine/__init__.py` | Package exports |
| `jobpulse/form_engine/models.py` | `InputType` enum, `FillResult` dataclass, `FieldInfo` dataclass |
| `jobpulse/form_engine/gotchas.py` | Runtime gotchas DB — learn and remember form quirks per domain |
| `tests/jobpulse/form_engine/test_gotchas.py` | Gotchas DB tests |
| `jobpulse/form_engine/detector.py` | Detect input type from a DOM element |
| `jobpulse/form_engine/text_filler.py` | Fill text inputs, textareas, rich text editors, search/autocomplete |
| `jobpulse/form_engine/select_filler.py` | Fill native `<select>` and custom React dropdowns |
| `jobpulse/form_engine/radio_filler.py` | Fill radio button groups |
| `jobpulse/form_engine/checkbox_filler.py` | Fill checkboxes, toggles, consent boxes |
| `jobpulse/form_engine/date_filler.py` | Fill native and custom date pickers |
| `jobpulse/form_engine/file_filler.py` | File upload (standard, hidden, drag-drop zone) |
| `jobpulse/form_engine/multi_select_filler.py` | Tag inputs, checkbox lists, `<select multiple>` |
| `jobpulse/form_engine/validation.py` | Scan for errors, find required fields, retry logic |
| `jobpulse/form_engine/page_filler.py` | Orchestrator: scan page → detect fields → fill all → validate |
| `tests/jobpulse/form_engine/__init__.py` | Test package |
| `tests/jobpulse/form_engine/test_models.py` | Model tests |
| `tests/jobpulse/form_engine/test_detector.py` | Detector tests |
| `tests/jobpulse/form_engine/test_text_filler.py` | Text filler tests |
| `tests/jobpulse/form_engine/test_select_filler.py` | Select filler tests |
| `tests/jobpulse/form_engine/test_radio_filler.py` | Radio filler tests |
| `tests/jobpulse/form_engine/test_checkbox_filler.py` | Checkbox filler tests |
| `tests/jobpulse/form_engine/test_date_filler.py` | Date filler tests |
| `tests/jobpulse/form_engine/test_file_filler.py` | File filler tests |
| `tests/jobpulse/form_engine/test_multi_select_filler.py` | Multi-select filler tests |
| `tests/jobpulse/form_engine/test_validation.py` | Validation tests |
| `tests/jobpulse/form_engine/test_page_filler.py` | Page filler integration tests |

---

### Task 0: Gotchas DB — runtime learning for form quirks

**Files:**
- Create: `jobpulse/form_engine/gotchas.py`
- Create: `tests/jobpulse/form_engine/test_gotchas.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for form_engine gotchas DB."""

import sys
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.fixture
def gotchas_db(tmp_path):
    from jobpulse.form_engine.gotchas import GotchasDB
    return GotchasDB(db_path=str(tmp_path / "gotchas.db"))


def test_store_and_retrieve_gotcha(gotchas_db):
    gotchas_db.store("workday.com", "#country", "native_select_failed", "use_custom_select")
    result = gotchas_db.lookup("workday.com", "#country")
    assert result is not None
    assert result["solution"] == "use_custom_select"
    assert result["times_used"] == 0


def test_lookup_miss_returns_none(gotchas_db):
    result = gotchas_db.lookup("unknown.com", "#field")
    assert result is None


def test_record_usage_increments(gotchas_db):
    gotchas_db.store("lever.co", "#phone", "format_rejected", "prepend_plus44")
    gotchas_db.record_usage("lever.co", "#phone")
    gotchas_db.record_usage("lever.co", "#phone")
    result = gotchas_db.lookup("lever.co", "#phone")
    assert result["times_used"] == 2


def test_lookup_by_domain_pattern(gotchas_db):
    gotchas_db.store("workday.com", "select", "native_select_failed", "use_custom_select")
    results = gotchas_db.lookup_domain("workday.com")
    assert len(results) == 1
    assert results[0]["selector_pattern"] == "select"


def test_store_overwrites_existing(gotchas_db):
    gotchas_db.store("lever.co", "#phone", "old_problem", "old_solution")
    gotchas_db.store("lever.co", "#phone", "new_problem", "new_solution")
    result = gotchas_db.lookup("lever.co", "#phone")
    assert result["solution"] == "new_solution"


def test_get_skip_domains(gotchas_db):
    gotchas_db.store("amazon.jobs", "*", "captcha_always", "skip_manual_review")
    skips = gotchas_db.get_skip_domains()
    assert "amazon.jobs" in skips
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_gotchas.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement gotchas DB**

Create `jobpulse/form_engine/gotchas.py`:

```python
"""Runtime gotchas DB — learn and remember form-filling quirks per domain.

When the form engine encounters a problem and figures out the fix, it stores
that knowledge here so the daemon never hits the same wall twice.

Schema:
    domain          — e.g. "workday.com", "lever.co"
    selector_pattern — CSS selector or pattern, e.g. "#country", "select", "*"
    problem         — what went wrong, e.g. "native_select_failed", "captcha_always"
    solution        — what worked, e.g. "use_custom_select", "skip_manual_review"
    times_used      — how many times this gotcha was applied
    created_at      — when first discovered
    last_used_at    — when last applied
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "form_gotchas.db")


class GotchasDB:
    """SQLite-backed store for form-filling gotchas."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS gotchas (
                    domain TEXT NOT NULL,
                    selector_pattern TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    times_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    PRIMARY KEY (domain, selector_pattern)
                )"""
            )
            conn.commit()

    def store(
        self,
        domain: str,
        selector_pattern: str,
        problem: str,
        solution: str,
    ) -> None:
        """Store or update a gotcha.

        If the same domain + selector_pattern exists, overwrites it
        (the form may have changed, new solution may be better).
        """
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO gotchas (domain, selector_pattern, problem, solution, times_used, created_at)
                   VALUES (?, ?, ?, ?, 0, ?)
                   ON CONFLICT(domain, selector_pattern) DO UPDATE SET
                       problem = excluded.problem,
                       solution = excluded.solution,
                       created_at = excluded.created_at,
                       times_used = 0""",
                (domain, selector_pattern, problem, solution, now),
            )
            conn.commit()
        logger.info("gotchas: stored %s/%s → %s", domain, selector_pattern, solution)

    def lookup(self, domain: str, selector_pattern: str) -> dict | None:
        """Look up a gotcha by exact domain + selector match.

        Returns dict with keys: solution, problem, times_used, created_at.
        Returns None if no match.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND selector_pattern = ?",
                (domain, selector_pattern),
            ).fetchone()
            return dict(row) if row else None

    def lookup_domain(self, domain: str) -> list[dict]:
        """Get all gotchas for a domain."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? ORDER BY times_used DESC",
                (domain,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_usage(self, domain: str, selector_pattern: str) -> None:
        """Increment times_used and update last_used_at for a gotcha."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE gotchas SET times_used = times_used + 1, last_used_at = ?
                   WHERE domain = ? AND selector_pattern = ?""",
                (now, domain, selector_pattern),
            )
            conn.commit()

    def get_skip_domains(self) -> list[str]:
        """Get domains that should always be routed to manual review.

        These are gotchas with selector_pattern='*' and solution='skip_manual_review'.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT domain FROM gotchas WHERE selector_pattern = '*' AND solution = 'skip_manual_review'"
            ).fetchall()
            return [r[0] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_gotchas.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/gotchas.py tests/jobpulse/form_engine/test_gotchas.py
git commit -m "feat(form-engine): add gotchas DB for runtime form-filling learning"
git push
```

---

### Task 1: Create models — `InputType`, `FillResult`, `FieldInfo`

**Files:**
- Create: `jobpulse/form_engine/__init__.py`
- Create: `jobpulse/form_engine/models.py`
- Create: `tests/jobpulse/form_engine/__init__.py`
- Create: `tests/jobpulse/form_engine/test_models.py`

- [ ] **Step 1: Write failing test**

```python
"""Tests for form_engine models."""

import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_input_type_enum_has_all_types():
    from jobpulse.form_engine.models import InputType

    expected = {
        "text", "textarea", "select_native", "select_custom",
        "radio", "checkbox", "date_native", "date_custom",
        "search_autocomplete", "file_upload", "multi_select",
        "tag_input", "toggle_switch", "rich_text_editor",
        "readonly", "unknown",
    }
    actual = {t.value for t in InputType}
    assert actual == expected


def test_fill_result_success():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=True, selector="#email", value_attempted="a@b.com", value_set="a@b.com")
    assert r.success is True
    assert r.error is None


def test_fill_result_failure():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=False, selector="#name", value_attempted="Yash", error="element not found")
    assert r.success is False
    assert r.value_set is None


def test_fill_result_skipped():
    from jobpulse.form_engine.models import FillResult

    r = FillResult(success=True, selector="#readonly", value_attempted="", skipped=True)
    assert r.skipped is True


def test_field_info_basic():
    from jobpulse.form_engine.models import FieldInfo, InputType

    f = FieldInfo(
        selector="#email",
        input_type=InputType.TEXT,
        label="Email Address",
        required=True,
        current_value="",
    )
    assert f.input_type == InputType.TEXT
    assert f.required is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_models.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Create package and implement models**

Create `jobpulse/form_engine/__init__.py`:

```python
"""Generic form engine — detect and fill any HTML input type."""
```

Create `jobpulse/form_engine/models.py`:

```python
"""Data models for the form engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class InputType(str, Enum):
    """Semantic type of a form input element."""

    TEXT = "text"
    TEXTAREA = "textarea"
    SELECT_NATIVE = "select_native"
    SELECT_CUSTOM = "select_custom"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    DATE_NATIVE = "date_native"
    DATE_CUSTOM = "date_custom"
    SEARCH_AUTOCOMPLETE = "search_autocomplete"
    FILE_UPLOAD = "file_upload"
    MULTI_SELECT = "multi_select"
    TAG_INPUT = "tag_input"
    TOGGLE_SWITCH = "toggle_switch"
    RICH_TEXT_EDITOR = "rich_text_editor"
    READONLY = "readonly"
    UNKNOWN = "unknown"


@dataclass
class FillResult:
    """Result of attempting to fill a single form field."""

    success: bool
    selector: str
    value_attempted: str
    value_set: str | None = None
    error: str | None = None
    skipped: bool = False


@dataclass
class FieldInfo:
    """Detected information about a form field."""

    selector: str
    input_type: InputType
    label: str = ""
    required: bool = False
    current_value: str = ""
    options: list[str] = field(default_factory=list)
    attributes: dict[str, str] = field(default_factory=dict)
```

Create `tests/jobpulse/form_engine/__init__.py` (empty).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_models.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/ tests/jobpulse/form_engine/
git commit -m "feat(form-engine): add InputType, FillResult, FieldInfo models"
git push
```

---

### Task 2: Input type detector (`detector.py`)

**Files:**
- Create: `jobpulse/form_engine/detector.py`
- Create: `tests/jobpulse/form_engine/test_detector.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for form_engine detector."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


def _mock_element(tag: str, attrs: dict | None = None, inner_html: str = ""):
    """Create a mock Playwright ElementHandle."""
    el = MagicMock()
    el.evaluate = AsyncMock(side_effect=lambda js: {
        "el => el.tagName.toLowerCase()": tag,
        "el => el.outerHTML": f"<{tag}>{inner_html}</{tag}>",
    }.get(js, ""))
    el.get_attribute = AsyncMock(side_effect=lambda name: (attrs or {}).get(name))
    el.query_selector = AsyncMock(return_value=None)
    el.query_selector_all = AsyncMock(return_value=[])
    return el


@pytest.mark.asyncio
async def test_detect_native_select():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("select")
    result = await detect_input_type(el)
    assert result == InputType.SELECT_NATIVE


@pytest.mark.asyncio
async def test_detect_radio():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "radio"})
    result = await detect_input_type(el)
    assert result == InputType.RADIO


@pytest.mark.asyncio
async def test_detect_checkbox():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "checkbox"})
    result = await detect_input_type(el)
    assert result == InputType.CHECKBOX


@pytest.mark.asyncio
async def test_detect_date_native():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "date"})
    result = await detect_input_type(el)
    assert result == InputType.DATE_NATIVE


@pytest.mark.asyncio
async def test_detect_file_upload():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "file"})
    result = await detect_input_type(el)
    assert result == InputType.FILE_UPLOAD


@pytest.mark.asyncio
async def test_detect_textarea():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("textarea")
    result = await detect_input_type(el)
    assert result == InputType.TEXTAREA


@pytest.mark.asyncio
async def test_detect_readonly():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "text", "readonly": ""})
    result = await detect_input_type(el)
    assert result == InputType.READONLY


@pytest.mark.asyncio
async def test_detect_disabled():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "text", "disabled": ""})
    result = await detect_input_type(el)
    assert result == InputType.READONLY


@pytest.mark.asyncio
async def test_detect_custom_select_by_role():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("div", {"role": "listbox"})
    result = await detect_input_type(el)
    assert result == InputType.SELECT_CUSTOM


@pytest.mark.asyncio
async def test_detect_text_input_default():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "text"})
    result = await detect_input_type(el)
    assert result == InputType.TEXT


@pytest.mark.asyncio
async def test_detect_email_as_text():
    from jobpulse.form_engine.detector import detect_input_type
    from jobpulse.form_engine.models import InputType

    el = _mock_element("input", {"type": "email"})
    result = await detect_input_type(el)
    assert result == InputType.TEXT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_detector.py -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement detector**

Create `jobpulse/form_engine/detector.py`:

```python
"""Detect the semantic input type of a DOM element."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import InputType

logger = get_logger(__name__)

# Input type="X" values that map directly to TEXT
_TEXT_LIKE_TYPES = {"text", "email", "tel", "url", "number", "password", "search", ""}


async def detect_input_type(element) -> InputType:
    """Examine a DOM element and return its semantic InputType.

    Args:
        element: A Playwright ElementHandle.

    Returns:
        The detected InputType enum value.
    """
    tag = await element.evaluate("el => el.tagName.toLowerCase()")

    # --- Tag-based detection ---
    if tag == "select":
        multi = await element.get_attribute("multiple")
        return InputType.MULTI_SELECT if multi is not None else InputType.SELECT_NATIVE

    if tag == "textarea":
        return InputType.TEXTAREA

    if tag == "input":
        input_type = (await element.get_attribute("type") or "text").lower()

        # Readonly / disabled → skip
        readonly = await element.get_attribute("readonly")
        disabled = await element.get_attribute("disabled")
        if readonly is not None or disabled is not None:
            return InputType.READONLY

        if input_type == "radio":
            return InputType.RADIO
        if input_type == "checkbox":
            return InputType.CHECKBOX
        if input_type == "file":
            return InputType.FILE_UPLOAD
        if input_type == "date":
            return InputType.DATE_NATIVE
        if input_type in _TEXT_LIKE_TYPES:
            return InputType.TEXT

        # Fallback for unknown input types
        return InputType.TEXT

    # --- Role-based detection (custom widgets) ---
    role = await element.get_attribute("role")
    if role in ("listbox", "combobox"):
        return InputType.SELECT_CUSTOM
    if role == "switch":
        return InputType.TOGGLE_SWITCH
    if role == "radiogroup":
        return InputType.RADIO

    # --- Content-editable detection (rich text) ---
    contenteditable = await element.get_attribute("contenteditable")
    if contenteditable == "true":
        return InputType.RICH_TEXT_EDITOR

    # --- Aria-based detection ---
    aria_multi = await element.get_attribute("aria-multiselectable")
    if aria_multi == "true":
        return InputType.MULTI_SELECT

    logger.debug("detector: unknown element tag=%s role=%s", tag, role)
    return InputType.UNKNOWN
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_detector.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/detector.py tests/jobpulse/form_engine/test_detector.py
git commit -m "feat(form-engine): add input type detector with tag/role/aria detection"
git push
```

---

### Task 3: Select filler — native + custom dropdowns

**Files:**
- Create: `jobpulse/form_engine/select_filler.py`
- Create: `tests/jobpulse/form_engine/test_select_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for select_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_native_select_exact_match():
    from jobpulse.form_engine.select_filler import fill_select

    page = MagicMock()
    page.select_option = AsyncMock(return_value=["United Kingdom"])
    page.query_selector = AsyncMock(return_value=MagicMock())

    result = await fill_select(page, "#country", "United Kingdom")
    assert result.success is True
    page.select_option.assert_called_once()


@pytest.mark.asyncio
async def test_fill_native_select_element_not_found():
    from jobpulse.form_engine.select_filler import fill_select

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_select(page, "#missing", "United Kingdom")
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_fuzzy_match_finds_close_option():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["United Kingdom", "United States", "Canada", "Germany"]
    match = _fuzzy_match_option("UK", options)
    assert match == "United Kingdom"


@pytest.mark.asyncio
async def test_fuzzy_match_exact():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Yes", "No", "Prefer not to say"]
    match = _fuzzy_match_option("Yes", options)
    assert match == "Yes"


@pytest.mark.asyncio
async def test_fuzzy_match_no_match():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Red", "Green", "Blue"]
    match = _fuzzy_match_option("Purple", options)
    assert match is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_select_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement select filler**

Create `jobpulse/form_engine/select_filler.py`:

```python
"""Fill dropdown/select elements — native <select> and custom React widgets."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)

# Common abbreviation→full mappings for fuzzy matching
_ABBREVIATIONS: dict[str, str] = {
    "uk": "united kingdom",
    "us": "united states",
    "usa": "united states of america",
}


def _normalize(text: str) -> str:
    """Lowercase, strip whitespace and punctuation for comparison."""
    return text.lower().strip().strip(".,;:!?")


def _fuzzy_match_option(value: str, options: list[str]) -> str | None:
    """Find the best matching option for a value.

    Priority: exact → abbreviation → startswith → contains → None.
    """
    norm_value = _normalize(value)

    # Check abbreviation expansion
    expanded = _ABBREVIATIONS.get(norm_value, norm_value)

    for opt in options:
        if _normalize(opt) == expanded:
            return opt

    for opt in options:
        if _normalize(opt).startswith(expanded):
            return opt

    for opt in options:
        if expanded in _normalize(opt):
            return opt

    return None


async def fill_select(
    page,
    selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a native <select> element by matching visible option text.

    Tries exact match first, then fuzzy match (abbreviations, startswith, contains).
    """
    try:
        element = await page.query_selector(selector)
        if element is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        # Check if disabled/readonly
        disabled = await element.get_attribute("disabled")
        if disabled is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        # Get available options
        options = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(e => e.textContent.trim())",
        )

        if not options:
            # Might be async-loaded — wait and retry
            await page.wait_for_timeout(2000)
            options = await page.eval_on_selector_all(
                f"{selector} option",
                "els => els.map(e => e.textContent.trim())",
            )

        if not options:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error="No options found in select",
            )

        # Find the best match
        match = _fuzzy_match_option(value, options)
        if match is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {options[:5]}",
            )

        await page.select_option(selector, label=match)
        logger.debug("select_filler: filled %s with '%s'", selector, match)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=match,
        )

    except Exception as exc:
        logger.error("select_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )


async def fill_custom_select(
    page,
    trigger_selector: str,
    value: str,
    options_selector: str = "[role='option'], li",
    timeout: int = 5000,
) -> FillResult:
    """Fill a custom React/JS dropdown widget.

    Flow: click trigger → wait for options panel → fuzzy match → click option.
    """
    try:
        trigger = await page.query_selector(trigger_selector)
        if trigger is None:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error=f"Trigger {trigger_selector} not found",
            )

        # Click to open the dropdown
        await trigger.scroll_into_view_if_needed()
        await trigger.click()
        await page.wait_for_timeout(500)

        # Try typing to filter if there's a search input inside
        search_input = await page.query_selector(
            f"{trigger_selector} input, [role='combobox'] input"
        )
        if search_input:
            await search_input.fill(value)
            await page.wait_for_timeout(1000)  # wait for debounce

        # Get visible options
        option_els = await page.query_selector_all(options_selector)
        option_texts = []
        for el in option_els:
            text = await el.text_content()
            if text and text.strip():
                option_texts.append((text.strip(), el))

        if not option_texts:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error="No options visible after opening dropdown",
            )

        # Fuzzy match
        texts_only = [t for t, _ in option_texts]
        match = _fuzzy_match_option(value, texts_only)
        if match is None:
            # Press Escape to close and report failure
            await page.keyboard.press("Escape")
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {texts_only[:5]}",
            )

        # Click the matching option
        for text, el in option_texts:
            if text == match:
                await el.click()
                break

        logger.debug("custom_select: filled %s with '%s'", trigger_selector, match)
        return FillResult(
            success=True, selector=trigger_selector,
            value_attempted=value, value_set=match,
        )

    except Exception as exc:
        logger.error("custom_select: error filling %s: %s", trigger_selector, exc)
        return FillResult(
            success=False, selector=trigger_selector,
            value_attempted=value, error=str(exc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_select_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/select_filler.py tests/jobpulse/form_engine/test_select_filler.py
git commit -m "feat(form-engine): add select filler with fuzzy matching for native + custom dropdowns"
git push
```

---

### Task 4: Radio filler

**Files:**
- Create: `jobpulse/form_engine/radio_filler.py`
- Create: `tests/jobpulse/form_engine/test_radio_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for radio_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


def _mock_radio(label_text: str, checked: bool = False):
    """Create a mock radio element with associated label."""
    el = MagicMock()
    el.click = AsyncMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: {
        "type": "radio",
        "id": f"radio_{label_text.lower().replace(' ', '_')}",
    }.get(name))
    el.is_checked = AsyncMock(return_value=checked)
    el.evaluate = AsyncMock(return_value=label_text)
    return el, label_text


@pytest.mark.asyncio
async def test_fill_radio_exact_match():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    yes_el, _ = _mock_radio("Yes")
    no_el, _ = _mock_radio("No")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[yes_el, no_el])
    page.eval_on_selector_all = AsyncMock(return_value=["Yes", "No"])

    result = await fill_radio_group(page, "input[name='sponsorship']", "No")
    assert result.success is True
    assert result.value_set == "No"


@pytest.mark.asyncio
async def test_fill_radio_no_matching_option():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    yes_el, _ = _mock_radio("Yes")
    no_el, _ = _mock_radio("No")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[yes_el, no_el])
    page.eval_on_selector_all = AsyncMock(return_value=["Yes", "No"])

    result = await fill_radio_group(page, "input[name='test']", "Maybe")
    assert result.success is False
    assert "no matching" in result.error.lower()


@pytest.mark.asyncio
async def test_fill_radio_no_elements():
    from jobpulse.form_engine.radio_filler import fill_radio_group

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])

    result = await fill_radio_group(page, "input[name='missing']", "Yes")
    assert result.success is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_radio_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement radio filler**

Create `jobpulse/form_engine/radio_filler.py`:

```python
"""Fill radio button groups by matching label text to desired value."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult
from jobpulse.form_engine.select_filler import _fuzzy_match_option

logger = get_logger(__name__)


async def _get_radio_label(page, radio_el) -> str:
    """Extract the label text for a radio button.

    Tries: <label for="id">, sibling text, parent text, aria-label.
    """
    # Try <label for="id">
    radio_id = await radio_el.get_attribute("id")
    if radio_id:
        label_el = await page.query_selector(f"label[for='{radio_id}']")
        if label_el:
            text = await label_el.text_content()
            if text and text.strip():
                return text.strip()

    # Try aria-label
    aria = await radio_el.get_attribute("aria-label")
    if aria:
        return aria.strip()

    # Try parent element text
    parent_text = await radio_el.evaluate(
        "el => el.parentElement ? el.parentElement.textContent.trim() : ''"
    )
    if parent_text:
        return parent_text

    return ""


async def fill_radio_group(
    page,
    group_selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a radio button group by selecting the option matching value.

    Args:
        page: Playwright page.
        group_selector: CSS selector for the radio inputs (e.g. "input[name='sponsor']").
        value: The desired answer text (e.g. "No", "Yes", "Prefer not to say").
        timeout: Max wait time in ms.

    Returns:
        FillResult with success status.
    """
    try:
        radios = await page.query_selector_all(group_selector)
        if not radios:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value, error="No radio elements found",
            )

        # Build label→element mapping
        label_map: list[tuple[str, object]] = []
        for radio in radios:
            label = await _get_radio_label(page, radio)
            if label:
                label_map.append((label, radio))

        if not label_map:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value, error="No labels found for radio buttons",
            )

        # Fuzzy match
        labels = [lbl for lbl, _ in label_map]
        match = _fuzzy_match_option(value, labels)
        if match is None:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value,
                error=f"No matching radio option for '{value}' in {labels}",
            )

        # Click the matching radio
        for label, radio in label_map:
            if label == match:
                await radio.scroll_into_view_if_needed()
                await radio.click()
                logger.debug("radio_filler: selected '%s' in %s", match, group_selector)
                return FillResult(
                    success=True, selector=group_selector,
                    value_attempted=value, value_set=match,
                )

        return FillResult(
            success=False, selector=group_selector,
            value_attempted=value, error="Match found but click failed",
        )

    except Exception as exc:
        logger.error("radio_filler: error filling %s: %s", group_selector, exc)
        return FillResult(
            success=False, selector=group_selector,
            value_attempted=value, error=str(exc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_radio_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/radio_filler.py tests/jobpulse/form_engine/test_radio_filler.py
git commit -m "feat(form-engine): add radio filler with label detection and fuzzy matching"
git push
```

---

### Task 5: Checkbox filler

**Files:**
- Create: `jobpulse/form_engine/checkbox_filler.py`
- Create: `tests/jobpulse/form_engine/test_checkbox_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for checkbox_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_is_consent_checkbox_detects_terms():
    from jobpulse.form_engine.checkbox_filler import _is_consent_checkbox
    assert _is_consent_checkbox("I agree to the terms and conditions") is True


@pytest.mark.asyncio
async def test_is_consent_checkbox_rejects_normal():
    from jobpulse.form_engine.checkbox_filler import _is_consent_checkbox
    assert _is_consent_checkbox("I have a disability") is False


@pytest.mark.asyncio
async def test_fill_checkbox_checks_when_should_be_true():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=False)
    el.check = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#terms", should_check=True)
    assert result.success is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_checkbox_skips_when_already_correct():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=True)
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#terms", should_check=True)
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_checkbox_unchecks_when_should_be_false():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=True)
    el.uncheck = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#sponsor", should_check=False)
    assert result.success is True
    el.uncheck.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_checkbox_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement checkbox filler**

Create `jobpulse/form_engine/checkbox_filler.py`:

```python
"""Fill checkboxes, toggles, and consent boxes."""

from __future__ import annotations

import re

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)

_CONSENT_KEYWORDS = re.compile(
    r"agree|consent|terms|privacy|gdpr|accept|acknowledge|policy|conditions",
    re.IGNORECASE,
)


def _is_consent_checkbox(label_text: str) -> bool:
    """Return True if the label indicates a consent/terms checkbox."""
    return bool(_CONSENT_KEYWORDS.search(label_text))


async def fill_checkbox(
    page,
    selector: str,
    should_check: bool = True,
    timeout: int = 5000,
) -> FillResult:
    """Check or uncheck a checkbox element.

    Args:
        page: Playwright page.
        selector: CSS selector for the checkbox.
        should_check: True to check, False to uncheck.
        timeout: Max wait time in ms.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(should_check), error=f"Element {selector} not found",
            )

        disabled = await el.get_attribute("disabled")
        if disabled is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=str(should_check), skipped=True,
            )

        current = await el.is_checked()
        if current == should_check:
            logger.debug("checkbox: %s already %s", selector, "checked" if current else "unchecked")
            return FillResult(
                success=True, selector=selector,
                value_attempted=str(should_check),
                value_set=str(should_check), skipped=True,
            )

        await el.scroll_into_view_if_needed()
        if should_check:
            await el.check()
        else:
            await el.uncheck()

        logger.debug("checkbox: %s set to %s", selector, should_check)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(should_check), value_set=str(should_check),
        )

    except Exception as exc:
        logger.error("checkbox: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(should_check), error=str(exc),
        )


async def auto_check_consent_boxes(page) -> list[FillResult]:
    """Find and check all consent/terms/privacy checkboxes on the page."""
    results: list[FillResult] = []
    checkboxes = await page.query_selector_all("input[type='checkbox']")

    for cb in checkboxes:
        # Get label text
        cb_id = await cb.get_attribute("id")
        label_text = ""
        if cb_id:
            label_el = await page.query_selector(f"label[for='{cb_id}']")
            if label_el:
                label_text = await label_el.text_content() or ""

        if not label_text:
            label_text = await cb.evaluate(
                "el => el.parentElement ? el.parentElement.textContent.trim() : ''"
            ) or ""

        if _is_consent_checkbox(label_text):
            selector = f"#{cb_id}" if cb_id else "input[type='checkbox']"
            result = await fill_checkbox(page, selector, should_check=True)
            results.append(result)

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_checkbox_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/checkbox_filler.py tests/jobpulse/form_engine/test_checkbox_filler.py
git commit -m "feat(form-engine): add checkbox filler with consent auto-detection"
git push
```

---

### Task 6: Text filler — text inputs, textareas, autocomplete

**Files:**
- Create: `jobpulse/form_engine/text_filler.py`
- Create: `tests/jobpulse/form_engine/test_text_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for text_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_text_basic():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#email", "test@example.com")
    assert result.success is True
    el.fill.assert_called_once_with("test@example.com")


@pytest.mark.asyncio
async def test_fill_text_respects_maxlength():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "10" if name == "maxlength" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#short", "This is a very long text that exceeds the limit")
    assert result.success is True
    el.fill.assert_called_once_with("This is a ")


@pytest.mark.asyncio
async def test_fill_text_clears_prefilled():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "old value" if name == "value" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#name", "New Value", clear_first=True)
    assert result.success is True


@pytest.mark.asyncio
async def test_fill_text_element_not_found():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_text(page, "#missing", "value")
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_fill_textarea_basic():
    from jobpulse.form_engine.text_filler import fill_textarea

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_textarea(page, "#cover", "My cover letter text here")
    assert result.success is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_text_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement text filler**

Create `jobpulse/form_engine/text_filler.py`:

```python
"""Fill text inputs, textareas, and search/autocomplete fields."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


async def fill_text(
    page,
    selector: str,
    value: str,
    clear_first: bool = True,
    timeout: int = 5000,
) -> FillResult:
    """Fill a text input field.

    Respects maxlength attribute. Clears pre-filled content if clear_first=True.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        disabled = await el.get_attribute("disabled")
        readonly = await el.get_attribute("readonly")
        if disabled is not None or readonly is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        # Respect maxlength
        maxlength = await el.get_attribute("maxlength")
        fill_value = value
        if maxlength:
            try:
                max_len = int(maxlength)
                fill_value = value[:max_len]
            except ValueError:
                pass

        await el.scroll_into_view_if_needed()
        await el.fill(fill_value)

        logger.debug("text_filler: filled %s (%d chars)", selector, len(fill_value))
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=fill_value,
        )

    except Exception as exc:
        logger.error("text_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )


async def fill_textarea(
    page,
    selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a textarea element. Handles maxlength and pre-filled content."""
    return await fill_text(page, selector, value, clear_first=True, timeout=timeout)


async def fill_autocomplete(
    page,
    selector: str,
    value: str,
    suggestion_selector: str = "li, [role='option']",
    timeout: int = 5000,
) -> FillResult:
    """Fill a search/autocomplete field.

    Types the value, waits for suggestion dropdown, clicks matching suggestion.
    Falls back to leaving typed text if freeform input is allowed.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        await el.scroll_into_view_if_needed()

        # Type at least 3 chars to trigger autocomplete
        type_text = value[:3] if len(value) >= 3 else value
        await el.fill("")  # clear first
        await el.type(type_text, delay=100)

        # Wait for suggestions to appear
        await page.wait_for_timeout(1500)

        # Look for matching suggestions
        suggestions = await page.query_selector_all(suggestion_selector)
        for suggestion in suggestions:
            text = await suggestion.text_content()
            if text and value.lower() in text.strip().lower():
                await suggestion.click()
                logger.debug("autocomplete: selected '%s' from suggestions", text.strip())
                return FillResult(
                    success=True, selector=selector,
                    value_attempted=value, value_set=text.strip(),
                )

        # No matching suggestion — type full value and press Escape
        await el.fill(value)
        await page.keyboard.press("Escape")
        logger.debug("autocomplete: no suggestion match, typed '%s' directly", value)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=value,
        )

    except Exception as exc:
        logger.error("autocomplete: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_text_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/text_filler.py tests/jobpulse/form_engine/test_text_filler.py
git commit -m "feat(form-engine): add text filler with maxlength, textarea, autocomplete support"
git push
```

---

### Task 7: Date filler

**Files:**
- Create: `jobpulse/form_engine/date_filler.py`
- Create: `tests/jobpulse/form_engine/test_date_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for date_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from datetime import date


@pytest.mark.asyncio
async def test_fill_native_date():
    from jobpulse.form_engine.date_filler import fill_date

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "date" if name == "type" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_date(page, "#start_date", "2026-05-01")
    assert result.success is True
    el.fill.assert_called_once_with("2026-05-01")


@pytest.mark.asyncio
async def test_fill_date_element_not_found():
    from jobpulse.form_engine.date_filler import fill_date

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_date(page, "#missing", "2026-05-01")
    assert result.success is False


def test_format_date_uk():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "DD/MM/YYYY") == "01/05/2026"


def test_format_date_us():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "MM/DD/YYYY") == "05/01/2026"


def test_format_date_iso():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "YYYY-MM-DD") == "2026-05-01"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_date_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement date filler**

Create `jobpulse/form_engine/date_filler.py`:

```python
"""Fill date picker fields — native <input type=date> and custom calendar widgets."""

from __future__ import annotations

from datetime import date, datetime

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


def _format_date(iso_date: str, fmt: str = "YYYY-MM-DD") -> str:
    """Convert ISO date string to the specified format.

    Supported formats: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY.
    """
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date

    if fmt == "DD/MM/YYYY":
        return dt.strftime("%d/%m/%Y")
    if fmt == "MM/DD/YYYY":
        return dt.strftime("%m/%d/%Y")
    return dt.strftime("%Y-%m-%d")


def _detect_date_format(placeholder: str | None) -> str:
    """Detect date format from placeholder text."""
    if not placeholder:
        return "YYYY-MM-DD"
    p = placeholder.lower()
    if "dd/mm" in p:
        return "DD/MM/YYYY"
    if "mm/dd" in p:
        return "MM/DD/YYYY"
    return "YYYY-MM-DD"


async def fill_date(
    page,
    selector: str,
    value: str,
    date_format: str | None = None,
    timeout: int = 5000,
) -> FillResult:
    """Fill a date input field.

    Args:
        page: Playwright page.
        selector: CSS selector for the date field.
        value: Date in ISO format (YYYY-MM-DD).
        date_format: Override format. Auto-detected from placeholder if None.
        timeout: Max wait time in ms.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        disabled = await el.get_attribute("disabled")
        readonly = await el.get_attribute("readonly")
        if disabled is not None or readonly is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        await el.scroll_into_view_if_needed()

        # Detect if native date input
        input_type = await el.get_attribute("type")
        if input_type == "date":
            # Native date inputs always use YYYY-MM-DD internally
            await el.fill(value)
            logger.debug("date_filler: native date %s = %s", selector, value)
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, value_set=value,
            )

        # Text-based date field — format according to placeholder or override
        if date_format is None:
            placeholder = await el.get_attribute("placeholder")
            date_format = _detect_date_format(placeholder)

        formatted = _format_date(value, date_format)
        await el.fill(formatted)

        # Press Tab to trigger validation/confirm
        await page.keyboard.press("Tab")

        logger.debug("date_filler: text date %s = %s (format=%s)", selector, formatted, date_format)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=formatted,
        )

    except Exception as exc:
        logger.error("date_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_date_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/date_filler.py tests/jobpulse/form_engine/test_date_filler.py
git commit -m "feat(form-engine): add date filler with format detection and native/custom support"
git push
```

---

### Task 8: File filler

**Files:**
- Create: `jobpulse/form_engine/file_filler.py`
- Create: `tests/jobpulse/form_engine/test_file_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for file_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_file_upload_basic(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_text("fake pdf")

    page = MagicMock()
    el = MagicMock()
    el.set_input_files = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is True
    el.set_input_files.assert_called_once_with(str(cv_file))


@pytest.mark.asyncio
async def test_fill_file_upload_file_not_exists():
    from jobpulse.form_engine.file_filler import fill_file_upload

    page = MagicMock()

    result = await fill_file_upload(page, "input[type='file']", Path("/nonexistent.pdf"))
    assert result.success is False
    assert "does not exist" in result.error


@pytest.mark.asyncio
async def test_fill_file_upload_element_not_found(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.pdf"
    cv_file.write_text("fake pdf")

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is False


@pytest.mark.asyncio
async def test_fill_file_upload_checks_accept(tmp_path):
    from jobpulse.form_engine.file_filler import fill_file_upload

    cv_file = tmp_path / "cv.txt"
    cv_file.write_text("plain text")

    page = MagicMock()
    el = MagicMock()
    el.set_input_files = AsyncMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: ".pdf,.docx" if name == "accept" else None)
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_file_upload(page, "input[type='file']", cv_file)
    assert result.success is False
    assert "type" in result.error.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_file_filler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement file filler**

Create `jobpulse/form_engine/file_filler.py`:

```python
"""File upload handling — standard, hidden, and drag-drop zone inputs."""

from __future__ import annotations

from pathlib import Path

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


def _check_file_type(file_path: Path, accept_attr: str | None) -> bool:
    """Check if file extension matches the accept attribute."""
    if not accept_attr:
        return True

    suffix = file_path.suffix.lower()
    accepted = [ext.strip().lower() for ext in accept_attr.split(",")]

    for pattern in accepted:
        if pattern.startswith(".") and suffix == pattern:
            return True
        if pattern == "application/pdf" and suffix == ".pdf":
            return True
        if pattern == "application/msword" and suffix in (".doc", ".docx"):
            return True

    return False


async def fill_file_upload(
    page,
    selector: str,
    file_path: Path,
    timeout: int = 30000,
) -> FillResult:
    """Upload a file to an input[type='file'] element.

    Validates file existence and type before uploading.
    Waits for upload progress indicators to complete.
    """
    try:
        if not file_path.exists():
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(file_path),
                error=f"File does not exist: {file_path}",
            )

        el = await page.query_selector(selector)
        if el is None:
            # Try finding hidden file input in drag-drop zone
            el = await page.query_selector("input[type='file']")
            if el is None:
                return FillResult(
                    success=False, selector=selector,
                    value_attempted=str(file_path),
                    error=f"No file input found for {selector}",
                )

        # Check accept attribute
        accept = await el.get_attribute("accept")
        if not _check_file_type(file_path, accept):
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(file_path),
                error=f"File type {file_path.suffix} not accepted. Allowed: {accept}",
            )

        await el.set_input_files(str(file_path))

        # Wait for upload progress to finish (if any indicator exists)
        try:
            await page.wait_for_selector(
                "[class*='progress'], [class*='upload'][class*='complete'], [class*='success']",
                timeout=5000,
                state="attached",
            )
            await page.wait_for_timeout(1000)
        except Exception:
            pass  # No progress indicator — upload was instant

        logger.debug("file_filler: uploaded %s to %s", file_path.name, selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(file_path), value_set=file_path.name,
        )

    except Exception as exc:
        logger.error("file_filler: error uploading to %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(file_path), error=str(exc),
        )


async def find_file_inputs(page) -> dict[str, str]:
    """Scan page for file upload fields and categorise by label.

    Returns dict: {"resume": selector, "cover_letter": selector, ...}
    """
    inputs = await page.query_selector_all("input[type='file']")
    categorised: dict[str, str] = {}

    for inp in inputs:
        inp_id = await inp.get_attribute("id") or ""
        inp_name = await inp.get_attribute("name") or ""
        label_text = ""

        if inp_id:
            label_el = await page.query_selector(f"label[for='{inp_id}']")
            if label_el:
                label_text = (await label_el.text_content() or "").lower()

        combined = f"{inp_id} {inp_name} {label_text}".lower()
        selector = f"#{inp_id}" if inp_id else f"input[name='{inp_name}']" if inp_name else "input[type='file']"

        if any(kw in combined for kw in ("resume", "cv", "curriculum")):
            categorised["resume"] = selector
        elif any(kw in combined for kw in ("cover", "letter", "motivation")):
            categorised["cover_letter"] = selector
        else:
            categorised.setdefault("other", selector)

    return categorised
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_file_filler.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/file_filler.py tests/jobpulse/form_engine/test_file_filler.py
git commit -m "feat(form-engine): add file filler with type validation and drag-drop fallback"
git push
```

---

### Task 9: Multi-select filler

**Files:**
- Create: `jobpulse/form_engine/multi_select_filler.py`
- Create: `tests/jobpulse/form_engine/test_multi_select_filler.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for multi_select_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_tag_input():
    from jobpulse.form_engine.multi_select_filler import fill_tag_input

    page = MagicMock()
    el = MagicMock()
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await fill_tag_input(page, "#skills", ["Python", "React", "AWS"])
    assert result.success is True
    assert page.keyboard.press.call_count == 3  # Enter after each


@pytest.mark.asyncio
async def test_fill_tag_input_empty_values():
    from jobpulse.form_engine.multi_select_filler import fill_tag_input

    page = MagicMock()
    el = MagicMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_tag_input(page, "#skills", [])
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_native_multi_select():
    from jobpulse.form_engine.multi_select_filler import fill_native_multi_select

    page = MagicMock()
    el = MagicMock()
    page.query_selector = AsyncMock(return_value=el)
    page.select_option = AsyncMock(return_value=["Python", "React"])
    page.eval_on_selector_all = AsyncMock(return_value=["Python", "React", "Java", "Go"])

    result = await fill_native_multi_select(page, "#languages", ["Python", "React"])
    assert result.success is True
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_multi_select_filler.py -v`

- [ ] **Step 3: Implement multi-select filler**

Create `jobpulse/form_engine/multi_select_filler.py`:

```python
"""Fill multi-select elements — tag inputs, checkbox lists, native <select multiple>."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult
from jobpulse.form_engine.select_filler import _fuzzy_match_option

logger = get_logger(__name__)


async def fill_tag_input(
    page,
    selector: str,
    values: list[str],
    timeout: int = 5000,
) -> FillResult:
    """Fill a tag/chip input by typing each value and pressing Enter."""
    try:
        if not values:
            return FillResult(
                success=True, selector=selector,
                value_attempted="", skipped=True,
            )

        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values), error=f"Element {selector} not found",
            )

        await el.scroll_into_view_if_needed()
        added: list[str] = []

        for val in values:
            await el.fill(val)
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            added.append(val)

        logger.debug("tag_input: added %d tags to %s", len(added), selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(values), value_set=str(added),
        )

    except Exception as exc:
        logger.error("tag_input: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(values), error=str(exc),
        )


async def fill_native_multi_select(
    page,
    selector: str,
    values: list[str],
    timeout: int = 5000,
) -> FillResult:
    """Fill a native <select multiple> element."""
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values), error=f"Element {selector} not found",
            )

        # Get available options
        options = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(e => e.textContent.trim())",
        )

        # Fuzzy match each value
        matched: list[str] = []
        for val in values:
            match = _fuzzy_match_option(val, options)
            if match:
                matched.append(match)

        if not matched:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values),
                error=f"No matching options for {values} in {options[:10]}",
            )

        await page.select_option(selector, label=matched)
        logger.debug("multi_select: selected %d options in %s", len(matched), selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(values), value_set=str(matched),
        )

    except Exception as exc:
        logger.error("multi_select: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(values), error=str(exc),
        )
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_multi_select_filler.py -v`

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/multi_select_filler.py tests/jobpulse/form_engine/test_multi_select_filler.py
git commit -m "feat(form-engine): add multi-select filler for tag inputs and native multi-select"
git push
```

---

### Task 10: Validation — error detection and required field scanning

**Files:**
- Create: `jobpulse/form_engine/validation.py`
- Create: `tests/jobpulse/form_engine/test_validation.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for form validation detection."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_scan_for_errors_finds_aria_invalid():
    from jobpulse.form_engine.validation import scan_for_errors

    error_el = MagicMock()
    error_el.get_attribute = AsyncMock(side_effect=lambda name: {
        "id": "email",
        "aria-invalid": "true",
    }.get(name))
    error_el.text_content = AsyncMock(return_value="")
    error_el.evaluate = AsyncMock(return_value="Please enter a valid email")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[error_el])

    errors = await scan_for_errors(page)
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_scan_for_errors_empty_page():
    from jobpulse.form_engine.validation import scan_for_errors

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])

    errors = await scan_for_errors(page)
    assert errors == []


@pytest.mark.asyncio
async def test_find_required_unfilled():
    from jobpulse.form_engine.validation import find_required_unfilled

    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: {
        "required": "",
        "id": "email",
        "value": "",
    }.get(name, None))
    el.evaluate = AsyncMock(return_value="")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[el])

    unfilled = await find_required_unfilled(page)
    assert len(unfilled) >= 1
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_validation.py -v`

- [ ] **Step 3: Implement validation**

Create `jobpulse/form_engine/validation.py`:

```python
"""Form validation error detection and required field scanning."""

from __future__ import annotations

from dataclasses import dataclass

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationError:
    """A detected form validation error."""

    field_selector: str
    error_message: str
    field_label: str = ""


async def scan_for_errors(page) -> list[ValidationError]:
    """Scan the current page for visible validation error messages.

    Detection strategies:
    - [aria-invalid="true"] elements
    - Elements with class containing "error", "invalid"
    - role="alert" elements
    """
    errors: list[ValidationError] = []

    # Strategy 1: aria-invalid elements
    invalid_els = await page.query_selector_all("[aria-invalid='true']")
    for el in invalid_els:
        el_id = await el.get_attribute("id") or ""
        # Try to find associated error message
        error_msg = await el.evaluate(
            """el => {
                // Check aria-errormessage
                const errId = el.getAttribute('aria-errormessage');
                if (errId) {
                    const errEl = document.getElementById(errId);
                    if (errEl) return errEl.textContent.trim();
                }
                // Check sibling/parent for error text
                const parent = el.closest('.form-group, .field-wrapper, .form-field');
                if (parent) {
                    const errEl = parent.querySelector('.error, .invalid-feedback, [role="alert"]');
                    if (errEl) return errEl.textContent.trim();
                }
                return '';
            }"""
        )
        selector = f"#{el_id}" if el_id else "[aria-invalid='true']"
        errors.append(ValidationError(field_selector=selector, error_message=error_msg or "Invalid field"))

    # Strategy 2: role="alert" elements (often used for form errors)
    alerts = await page.query_selector_all("[role='alert']")
    for alert in alerts:
        text = await alert.text_content()
        if text and text.strip():
            errors.append(ValidationError(
                field_selector="[role='alert']",
                error_message=text.strip(),
            ))

    logger.debug("validation: found %d errors on page", len(errors))
    return errors


async def find_required_unfilled(page) -> list[str]:
    """Find all required form fields that are currently empty.

    Returns list of selectors for unfilled required fields.
    """
    unfilled: list[str] = []

    # Check input/select/textarea with required attribute
    required_els = await page.query_selector_all(
        "input[required], select[required], textarea[required], "
        "[aria-required='true']"
    )

    for el in required_els:
        value = await el.evaluate("el => el.value || ''")
        if not value.strip():
            el_id = await el.get_attribute("id") or ""
            el_name = await el.get_attribute("name") or ""
            selector = f"#{el_id}" if el_id else f"[name='{el_name}']" if el_name else "input[required]"
            unfilled.append(selector)

    logger.debug("validation: %d required fields unfilled", len(unfilled))
    return unfilled


async def has_errors(page) -> bool:
    """Quick check: are there any validation errors on the page?"""
    errors = await scan_for_errors(page)
    return len(errors) > 0
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_validation.py -v`

- [ ] **Step 5: Commit and push**

```bash
git add jobpulse/form_engine/validation.py tests/jobpulse/form_engine/test_validation.py
git commit -m "feat(form-engine): add validation error detection and required field scanning"
git push
```

---

### Task 11: Page filler — orchestrator that ties everything together

**Files:**
- Create: `jobpulse/form_engine/page_filler.py`
- Create: `tests/jobpulse/form_engine/test_page_filler.py`
- Modify: `jobpulse/form_engine/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
"""Tests for page_filler orchestrator."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_field_by_type_text():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import InputType, FieldInfo

    field = FieldInfo(
        selector="#email", input_type=InputType.TEXT,
        label="Email", required=True,
    )
    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_field_by_type(page, field, "test@example.com")
    assert result.success is True


@pytest.mark.asyncio
async def test_fill_field_by_type_skips_readonly():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import InputType, FieldInfo

    field = FieldInfo(
        selector="#readonly", input_type=InputType.READONLY,
        label="ID", required=False,
    )
    page = MagicMock()

    result = await fill_field_by_type(page, field, "anything")
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_field_by_type_unknown():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import InputType, FieldInfo

    field = FieldInfo(
        selector="#weird", input_type=InputType.UNKNOWN,
        label="Unknown", required=False,
    )
    page = MagicMock()

    result = await fill_field_by_type(page, field, "anything")
    assert result.success is False
    assert "unsupported" in result.error.lower()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/test_page_filler.py -v`

- [ ] **Step 3: Implement page filler**

Create `jobpulse/form_engine/page_filler.py`:

```python
"""Page-level form filler — orchestrates detection and filling of all fields on a page."""

from __future__ import annotations

from pathlib import Path

from shared.logging_config import get_logger
from jobpulse.form_engine.models import InputType, FillResult, FieldInfo
from jobpulse.form_engine import text_filler, select_filler, radio_filler
from jobpulse.form_engine import checkbox_filler, date_filler, file_filler
from jobpulse.form_engine import multi_select_filler

logger = get_logger(__name__)


async def fill_field_by_type(
    page,
    field: FieldInfo,
    value: str,
    file_path: Path | None = None,
) -> FillResult:
    """Fill a single form field based on its detected InputType.

    Routes to the appropriate filler function.
    """
    if field.input_type == InputType.READONLY:
        return FillResult(
            success=True, selector=field.selector,
            value_attempted=value, skipped=True,
        )

    if field.input_type == InputType.UNKNOWN:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error="Unsupported input type: unknown",
        )

    if field.input_type in (InputType.TEXT, InputType.TEXTAREA):
        if field.input_type == InputType.TEXTAREA:
            return await text_filler.fill_textarea(page, field.selector, value)
        return await text_filler.fill_text(page, field.selector, value)

    if field.input_type == InputType.SELECT_NATIVE:
        return await select_filler.fill_select(page, field.selector, value)

    if field.input_type == InputType.SELECT_CUSTOM:
        return await select_filler.fill_custom_select(page, field.selector, value)

    if field.input_type == InputType.RADIO:
        return await radio_filler.fill_radio_group(page, field.selector, value)

    if field.input_type == InputType.CHECKBOX:
        should_check = value.lower() in ("true", "yes", "1", "checked")
        return await checkbox_filler.fill_checkbox(page, field.selector, should_check)

    if field.input_type in (InputType.DATE_NATIVE, InputType.DATE_CUSTOM):
        return await date_filler.fill_date(page, field.selector, value)

    if field.input_type == InputType.FILE_UPLOAD:
        if file_path is None:
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="No file path provided for file upload",
            )
        return await file_filler.fill_file_upload(page, field.selector, file_path)

    if field.input_type == InputType.SEARCH_AUTOCOMPLETE:
        return await text_filler.fill_autocomplete(page, field.selector, value)

    if field.input_type == InputType.TAG_INPUT:
        values = [v.strip() for v in value.split(",") if v.strip()]
        return await multi_select_filler.fill_tag_input(page, field.selector, values)

    if field.input_type == InputType.MULTI_SELECT:
        values = [v.strip() for v in value.split(",") if v.strip()]
        return await multi_select_filler.fill_native_multi_select(page, field.selector, values)

    if field.input_type == InputType.TOGGLE_SWITCH:
        should_check = value.lower() in ("true", "yes", "1", "on")
        return await checkbox_filler.fill_checkbox(page, field.selector, should_check)

    if field.input_type == InputType.RICH_TEXT_EDITOR:
        return await text_filler.fill_text(page, field.selector, value)

    return FillResult(
        success=False, selector=field.selector,
        value_attempted=value, error=f"Unsupported input type: {field.input_type}",
    )
```

- [ ] **Step 4: Update `__init__.py` with public exports**

Update `jobpulse/form_engine/__init__.py`:

```python
"""Generic form engine — detect and fill any HTML input type."""

from jobpulse.form_engine.models import InputType, FillResult, FieldInfo
from jobpulse.form_engine.detector import detect_input_type
from jobpulse.form_engine.page_filler import fill_field_by_type
from jobpulse.form_engine.validation import scan_for_errors, find_required_unfilled, has_errors
from jobpulse.form_engine.checkbox_filler import auto_check_consent_boxes
from jobpulse.form_engine.file_filler import find_file_inputs

__all__ = [
    "InputType", "FillResult", "FieldInfo",
    "detect_input_type", "fill_field_by_type",
    "scan_for_errors", "find_required_unfilled", "has_errors",
    "auto_check_consent_boxes", "find_file_inputs",
]
```

- [ ] **Step 5: Run all form engine tests**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/jobpulse/form_engine/ -v`
Expected: ALL PASS

- [ ] **Step 6: Verify package import**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -c "from jobpulse.form_engine import InputType, FillResult, detect_input_type, fill_field_by_type; print('Form engine OK')"`
Expected: "Form engine OK"

- [ ] **Step 7: Commit and push**

```bash
git add jobpulse/form_engine/ tests/jobpulse/form_engine/
git commit -m "feat(form-engine): add page filler orchestrator and package exports"
git push
```

---

### Task 12: Final verification — full test suite + lint

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m pytest tests/ -v --no-header 2>&1 | tail -20`
Expected: ALL PASS (including form engine tests)

- [ ] **Step 2: Lint check**

Run: `cd /Users/yashbishnoi/Downloads/multi_agent_patterns && python -m ruff check jobpulse/form_engine/ tests/jobpulse/form_engine/ 2>&1`
Expected: No errors

- [ ] **Step 3: Fix any lint issues and commit**

```bash
python -m ruff check --fix jobpulse/form_engine/ tests/jobpulse/form_engine/
git add -u
git commit -m "chore(form-engine): lint fixes"
git push
```
