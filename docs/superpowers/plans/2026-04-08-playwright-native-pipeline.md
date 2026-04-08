# Playwright Native Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a native Playwright form-filling pipeline that uses Playwright's locator API and LLM calls instead of extension-style snapshots and state machines. Follows SOLID principles — the native pipeline lives in its own class with clean dependency injection.

**Architecture:** `NativeFormFiller` is a self-contained class that receives a Playwright `Page` via constructor. It encapsulates field scanning, LLM-powered mapping, label-based filling, and navigation. The orchestrator's `_fill_application()` creates a `NativeFormFiller` when `engine="playwright"` and delegates to it — zero native logic leaks into the orchestrator. Human-like behavior (Bezier mouse, scroll delays) is delegated to the `PlaywrightDriver` via its existing methods.

**Tech Stack:** Playwright async API (locators, roles, auto-waiting), OpenAI API (gpt-4.1-mini), Python asyncio

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `jobpulse/native_form_filler.py` | **NEW** — `NativeFormFiller` class: field scanning, LLM calls, fill-by-label, navigation, uploads, consent | Create |
| `jobpulse/playwright_driver.py` | Playwright CDP driver | Add `page` property to expose `_page` |
| `jobpulse/application_orchestrator.py` | Application lifecycle | Add 5-line branch in `_fill_application()` to delegate to `NativeFormFiller` |
| `tests/jobpulse/test_native_form_filler.py` | Tests for all native methods | Create |

### Class Design

```
NativeFormFiller
├── __init__(page: Page, driver: PlaywrightDriver)
│   page — Playwright Page for locator access
│   driver — PlaywrightDriver for human-like _move_mouse_to / _smart_scroll
│
├── fill(platform, cv_path, cl_path, profile, custom_answers, dry_run) → dict
│   Main per-page loop. Only public method.
│
├── _scan_fields() → list[dict]
│   Role-based locator scanning (textbox, combobox, radiogroup, checkbox, textarea, file)
│
├── _get_accessible_name(locator) → str
│   Extract label/aria-label/placeholder from element
│
├── _fill_by_label(label, value) → dict
│   Find field by label, fill with type-appropriate method, verify
│
├── _map_fields(fields, profile, custom_answers, platform) → dict
│   LLM Call 1: profile → field mapping
│
├── _screen_questions(unresolved, job_context) → dict
│   LLM Call 2: answer screening questions
│
├── _review_form() → dict
│   LLM Call 3: screenshot-based pre-submit review
│
├── _upload_files(cv_path, cl_path) → None
│   Deterministic CV/CL upload by label keyword
│
├── _check_consent() → None
│   Auto-check consent/terms/privacy checkboxes
│
├── _is_confirmation_page() → bool
│   Body text check for thank-you phrases
│
├── _is_submit_page() → bool
│   Check for visible Submit/Apply button
│
└── _click_navigation(dry_run) → str
    Find and click next/submit button, return action taken
```

---

### Task 1: Expose Playwright Page via property

**Files:**
- Modify: `jobpulse/playwright_driver.py:97-104`
- Test: `tests/jobpulse/test_playwright_driver.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/jobpulse/test_playwright_driver.py`:

```python
def test_page_property_before_connect():
    """page property returns None before connect()."""
    driver = PlaywrightDriver()
    assert driver.page is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_playwright_driver.py::test_page_property_before_connect -v`
Expected: FAIL with `AttributeError: 'PlaywrightDriver' object has no attribute 'page'`

- [ ] **Step 3: Add the page property to PlaywrightDriver**

In `jobpulse/playwright_driver.py`, add after the `__init__` method (after line 104):

```python
    @property
    def page(self) -> Page | None:
        """Expose the Playwright Page for native locator access."""
        return self._page
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_playwright_driver.py::test_page_property_before_connect -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/playwright_driver.py tests/jobpulse/test_playwright_driver.py
git commit -m "feat(playwright): expose page property for native locator access"
```

---

### Task 2: NativeFormFiller scaffold + _get_accessible_name + _scan_fields

**Files:**
- Create: `jobpulse/native_form_filler.py`
- Create: `tests/jobpulse/test_native_form_filler.py`

Create the class with constructor, field scanning via role-based locators, and the label extraction helper.

- [ ] **Step 1: Write the failing tests**

Create `tests/jobpulse/test_native_form_filler.py`:

```python
"""Tests for NativeFormFiller — Playwright native pipeline."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_filler(page_mock=None, driver_mock=None):
    """Create a NativeFormFiller with mocked dependencies."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = page_mock or MagicMock()
    driver = driver_mock or AsyncMock()
    driver.page = page
    return NativeFormFiller(page=page, driver=driver)


# ── _get_accessible_name ──


@pytest.mark.asyncio
async def test_get_accessible_name_returns_label():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="Email Address")

    result = await filler._get_accessible_name(locator)
    assert result == "Email Address"
    locator.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_get_accessible_name_empty_fallback():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="")

    result = await filler._get_accessible_name(locator)
    assert result == ""


# ── _scan_fields ──


@pytest.mark.asyncio
async def test_scan_fields_text_inputs():
    """Scans textbox role elements and returns field dicts."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    textbox = AsyncMock()
    textbox.input_value = AsyncMock(return_value="")
    textbox.get_attribute = AsyncMock(return_value=None)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[textbox])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role

    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="First Name"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["label"] == "First Name"
    assert fields[0]["type"] == "text"
    assert fields[0]["locator"] is textbox


@pytest.mark.asyncio
async def test_scan_fields_select_with_options():
    """Scans combobox (select) elements and captures options."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    select_el = AsyncMock()
    select_el.input_value = AsyncMock(return_value="")
    option_locator = MagicMock()
    option_locator.all_text_contents = AsyncMock(return_value=["USA", "UK", "Canada"])
    select_el.locator = lambda sel: option_locator

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[select_el])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Country"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "select"
    assert fields[0]["options"] == ["USA", "UK", "Canada"]


@pytest.mark.asyncio
async def test_scan_fields_checkbox():
    """Scans checkbox elements with checked state."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Agree to terms"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox"
    assert fields[0]["checked"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "accessible_name or scan_fields"`
Expected: FAIL — `jobpulse.native_form_filler` doesn't exist

- [ ] **Step 3: Create NativeFormFiller with constructor + scanning methods**

Create `jobpulse/native_form_filler.py`:

```python
"""NativeFormFiller — Playwright native form-filling pipeline.

Uses Playwright's locator API (get_by_label, get_by_role, accessibility tree)
and LLM calls instead of extension-style snapshots and state machines.

Single Responsibility: this class owns field scanning, LLM mapping, label-based
filling, file uploads, consent, and navigation for the native engine. The
ApplicationOrchestrator delegates to this class when engine="playwright".
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import random
from typing import TYPE_CHECKING, Any

from openai import OpenAI

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

# Per-platform minimum page times (seconds) — kept in sync with orchestrator
_PLATFORM_MIN_PAGE_TIME: dict[str, float] = {
    "workday": 45.0,
    "linkedin": 8.0,
    "greenhouse": 5.0,
    "lever": 5.0,
    "indeed": 10.0,
    "generic": 5.0,
}

MAX_FORM_PAGES = 20


def _get_field_gap(label_text: str = "") -> float:
    """Return delay in seconds based on label length (simulates reading)."""
    length = len(label_text)
    if length < 10:
        return 0.3 + random.uniform(0, 0.15)
    if length < 30:
        return 0.5 + random.uniform(0, 0.3)
    if length < 60:
        return 0.8 + random.uniform(0, 0.4)
    return 1.2 + random.uniform(0, 0.5)


class NativeFormFiller:
    """Playwright-native form filler using locators and LLM calls.

    Constructor receives:
        page — Playwright Page for locator-based field access
        driver — PlaywrightDriver for human-like mouse/scroll behavior
    """

    def __init__(self, page: "Page", driver: Any) -> None:
        self._page = page
        self._driver = driver

    # ── Label Extraction ──

    async def _get_accessible_name(self, locator: Any) -> str:
        """Extract the label a screen reader would announce for this element."""
        return await locator.evaluate(
            "el => el.labels?.[0]?.textContent?.trim() || "
            "el.getAttribute('aria-label') || "
            "el.placeholder || ''"
        )

    # ── Field Scanning ──

    async def _scan_fields(self) -> list[dict]:
        """Scan visible form fields using Playwright role-based locators.

        Returns a list of dicts with: label, type, locator, and
        type-specific keys (value, options, checked, required).
        """
        page = self._page
        fields: list[dict] = []

        # Text inputs (textbox role covers input[type=text/email/tel/number/etc])
        for loc in await page.get_by_role("textbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "text", "locator": loc,
                "value": await loc.input_value(),
                "required": await loc.get_attribute("required") is not None,
            })

        # Dropdowns (combobox role = native <select>)
        for loc in await page.get_by_role("combobox").all():
            label = await self._get_accessible_name(loc)
            options = await loc.locator("option").all_text_contents()
            fields.append({
                "label": label, "type": "select", "locator": loc,
                "options": options, "value": await loc.input_value(),
            })

        # Radio groups
        for loc in await page.get_by_role("radiogroup").all():
            label = await self._get_accessible_name(loc)
            radios = await loc.get_by_role("radio").all()
            option_labels = [await self._get_accessible_name(r) for r in radios]
            fields.append({
                "label": label, "type": "radio", "options": option_labels,
                "locator": loc,
            })

        # Checkboxes
        for loc in await page.get_by_role("checkbox").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "checkbox", "locator": loc,
                "checked": await loc.is_checked(),
            })

        # Textareas
        for loc in await page.locator("textarea:visible").all():
            label = await self._get_accessible_name(loc)
            fields.append({
                "label": label, "type": "textarea", "locator": loc,
                "value": await loc.input_value(),
            })

        # File inputs
        for loc in await page.locator("input[type='file']").all():
            label = await self._get_accessible_name(loc)
            fields.append({"label": label, "type": "file", "locator": loc})

        return fields
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "accessible_name or scan_fields"`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): NativeFormFiller scaffold with field scanning"
```

---

### Task 3: Fill by label — _fill_by_label

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

Fills a single form field by label (Playwright locator). Falls back to placeholder. Handles text, select, checkbox, and radio types with post-fill verification.

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── _fill_by_label ──


@pytest.mark.asyncio
async def test_fill_by_label_text_input():
    """Fills a text field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="john@example.com")

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Email", "john@example.com")

    assert result["success"] is True
    el.fill.assert_called_once_with("john@example.com")


@pytest.mark.asyncio
async def test_fill_by_label_select():
    """Fills a select field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(side_effect=["select", "United States"])
    el.get_attribute = AsyncMock(return_value=None)
    el.select_option = AsyncMock()

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country", "United States")

    assert result["success"] is True
    el.select_option.assert_called_once_with(label="United States")


@pytest.mark.asyncio
async def test_fill_by_label_not_found():
    """Returns error when no field matches label or placeholder."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=empty_locator)

    with patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Nonexistent", "value")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_fill_by_label_checkbox():
    """Checks a checkbox found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value="checkbox")
    el.check = AsyncMock()
    el.is_checked = AsyncMock(return_value=True)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("I agree", "yes")

    assert result["success"] is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_by_label_placeholder_fallback():
    """Falls back to placeholder when label locator finds nothing."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="test")

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    placeholder_locator = MagicMock()
    placeholder_locator.count = AsyncMock(return_value=1)
    placeholder_locator.first = el

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=placeholder_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Search", "test")

    assert result["success"] is True
    page.get_by_placeholder.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "fill_by_label"`
Expected: FAIL — `_fill_by_label` doesn't exist

- [ ] **Step 3: Implement _fill_by_label and human-like helpers**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_scan_fields`:

```python
    # ── Human-Like Behavior (delegates to driver) ──

    async def _smart_scroll(self, el: Any) -> None:
        """Scroll element into view with human-like delay."""
        if hasattr(self._driver, '_smart_scroll'):
            await self._driver._smart_scroll(el)
        else:
            await el.scroll_into_view_if_needed()

    async def _move_mouse_to(self, el: Any) -> None:
        """Move mouse to element with Bezier curve."""
        if hasattr(self._driver, '_move_mouse_to'):
            await self._driver._move_mouse_to(el)

    # ── Fill By Label ──

    async def _fill_by_label(self, label: str, value: str) -> dict:
        """Fill a single form field using Playwright's label-based locator.

        Tries get_by_label first, falls back to get_by_placeholder.
        Handles text, select, checkbox, and radio input types.
        Returns {"success": bool, "value_set": str, "value_verified": bool}.
        """
        page = self._page
        await asyncio.sleep(_get_field_gap(label))

        # Try label-based locator first
        locator = page.get_by_label(label, exact=False)

        if not await locator.count():
            locator = page.get_by_placeholder(label, exact=False)

        if not await locator.count():
            logger.warning("No field found for label '%s'", label)
            return {"success": False, "error": f"No field for '{label}'"}

        el = locator.first
        await self._smart_scroll(el)
        await self._move_mouse_to(el)

        tag = await el.evaluate("el => el.tagName.toLowerCase()")
        input_type = await el.get_attribute("type") or ""

        if tag == "select":
            await el.select_option(label=value)
        elif input_type == "checkbox":
            if value.lower() in ("true", "yes", "1"):
                await el.check()
            else:
                await el.uncheck()
        elif input_type == "radio":
            await page.get_by_label(value).check()
        else:
            await el.fill(value)

        # Post-fill verification
        if tag == "select":
            actual = await el.evaluate(
                "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
        elif input_type in ("checkbox", "radio"):
            actual = str(await el.is_checked())
        else:
            actual = await el.input_value()

        verified = value[:10].lower() in actual.lower() if actual else False
        return {"success": True, "value_set": value, "value_verified": verified}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "fill_by_label"`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add _fill_by_label with label/placeholder fallback"
```

---

### Task 4: LLM field mapping — _map_fields

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

LLM Call 1: maps profile data to form fields. Returns `{"label": "value"}`. Uses OpenAI directly (same pattern as `jobpulse/form_analyzer.py:656`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── _map_fields (LLM Call 1) ──


@pytest.mark.asyncio
async def test_map_fields_basic():
    """Maps profile data to form fields via LLM."""
    filler = _make_filler()
    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Phone", "type": "text", "value": "", "required": False},
        {"label": "Resume", "type": "file"},
    ]
    profile = {"email": "test@example.com", "phone": "+44123456789"}

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Email": "test@example.com", "Phone": "+44123456789"}'

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._map_fields(fields, profile, {}, "greenhouse")

    assert result == {"Email": "test@example.com", "Phone": "+44123456789"}


@pytest.mark.asyncio
async def test_map_fields_skips_file_fields():
    """File fields are excluded from the LLM prompt."""
    filler = _make_filler()
    fields = [
        {"label": "Resume", "type": "file"},
    ]

    result = await filler._map_fields(fields, {}, {}, "linkedin")
    assert result == {}


@pytest.mark.asyncio
async def test_map_fields_includes_options():
    """Dropdown options are passed in the prompt."""
    filler = _make_filler()
    fields = [
        {"label": "Country", "type": "select", "options": ["USA", "UK"], "value": ""},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Country": "UK"}'

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._map_fields(fields, {}, {}, "greenhouse")

    assert result == {"Country": "UK"}
    prompt = mock_openai.return_value.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "USA" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "map_fields"`
Expected: FAIL — `_map_fields` doesn't exist

- [ ] **Step 3: Implement _map_fields**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_fill_by_label`:

```python
    # ── LLM Calls ──

    async def _map_fields(
        self, fields: list[dict], profile: dict,
        custom_answers: dict, platform: str,
    ) -> dict:
        """LLM Call 1: map profile data to form field labels.

        Returns {"label": "value"} for each field the LLM can fill.
        Skips file upload fields. Marks already-filled fields in the prompt.
        """
        field_descriptions = []
        for f in fields:
            if f["type"] == "file":
                continue
            desc = f"- {f['label']} ({f['type']})"
            if f.get("options"):
                desc += f" options: {f['options'][:10]}"
            if f.get("value"):
                desc += f" [already filled: {f['value']}]"
            if f.get("required"):
                desc += " *required"
            field_descriptions.append(desc)

        if not field_descriptions:
            return {}

        prompt = (
            f'Map profile data to form fields. Return JSON {{"label": "value"}}.\n'
            f"Skip already-filled fields. Skip file upload fields.\n\n"
            f"Fields:\n{chr(10).join(field_descriptions)}\n\n"
            f"Profile: {json.dumps(profile)}\n"
            f"Platform: {platform}\n"
            f"Known answers: {json.dumps(custom_answers)}"
        )

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "map_fields"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add _map_fields — LLM Call 1 for profile-to-field mapping"
```

---

### Task 5: LLM screening + review — _screen_questions + _review_form

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

LLM Call 2 (screening) and Call 3 (screenshot review) in one task — both are small, self-contained LLM wrappers.

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── _screen_questions (LLM Call 2) ──


@pytest.mark.asyncio
async def test_screen_questions_basic():
    filler = _make_filler()
    unresolved = [
        {"label": "Are you authorized to work in the UK?", "type": "radio",
         "options": ["Yes", "No"]},
        {"label": "Expected salary", "type": "text"},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"Are you authorized to work in the UK?": "Yes", "Expected salary": "50000"}'
    )

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._screen_questions(unresolved, "SWE at Acme")

    assert result["Are you authorized to work in the UK?"] == "Yes"
    assert result["Expected salary"] == "50000"


@pytest.mark.asyncio
async def test_screen_questions_includes_options():
    filler = _make_filler()
    unresolved = [
        {"label": "Years of experience", "type": "select",
         "options": ["0-1", "2-3", "4-5", "6+"]},
    ]

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Years of experience": "2-3"}'

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._screen_questions(unresolved, "Data Analyst")

    prompt = mock_openai.return_value.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "0-1" in prompt


# ── _review_form (LLM Call 3) ──


@pytest.mark.asyncio
async def test_review_form_pass():
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"pass": true}'

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._review_form()

    assert result["pass"] is True


@pytest.mark.asyncio
async def test_review_form_fail_with_issues():
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"pass": false, "issues": ["Phone empty", "Wrong country"]}'
    )

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result = await filler._review_form()

    assert result["pass"] is False
    assert len(result["issues"]) == 2


@pytest.mark.asyncio
async def test_review_form_sends_image():
    """Screenshot is sent as base64 image_url in the LLM message."""
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG test")
    filler = _make_filler(page_mock=page)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"pass": true}'

    with patch("jobpulse.native_form_filler.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        await filler._review_form()

    messages = mock_openai.return_value.chat.completions.create.call_args[1]["messages"]
    content = messages[0]["content"]
    assert isinstance(content, list)
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "screen_questions or review_form"`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement _screen_questions and _review_form**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_map_fields`:

```python
    async def _screen_questions(
        self, unresolved_fields: list[dict], job_context: str | None,
    ) -> dict:
        """LLM Call 2: answer screening questions not mapped from profile.

        Only called when _map_fields left non-file fields unresolved.
        Returns {"label": "answer"} dict.
        """
        questions = []
        for f in unresolved_fields:
            opts = f.get("options", "free text")
            questions.append(f"Q: {f['label']} Options: {opts}")

        prompt = (
            f"Answer these screening questions for a job application.\n"
            f"Context: {job_context or 'Not provided'}\n\n"
            f"{chr(10).join(questions)}\n\n"
            f'Return JSON {{"label": "answer"}}. Be truthful.'
        )

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)

    async def _review_form(self) -> dict:
        """LLM Call 3: screenshot-based pre-submit review of the filled form.

        Returns {"pass": true} or {"pass": false, "issues": [...]}.
        """
        screenshot_bytes = await self._page.screenshot(type="png")
        b64 = base64.b64encode(screenshot_bytes).decode()

        prompt = (
            "Review this filled application form. Any empty required fields, "
            'wrong values, or mismatches? Return {"pass": true} or '
            '{"pass": false, "issues": [...]}'
        )

        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=1000,
            temperature=0.0,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                    }},
                ],
            }],
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "screen_questions or review_form"`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add _screen_questions + _review_form — LLM Calls 2-3"
```

---

### Task 6: Deterministic helpers — _upload_files + _check_consent

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

No LLM calls. File uploads match by label keyword (CV vs cover letter). Consent boxes auto-check.

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── _upload_files ──


@pytest.mark.asyncio
async def test_upload_files_cv_only():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    fi = AsyncMock()
    fi.set_input_files = AsyncMock()

    file_locator = MagicMock()
    file_locator.all = AsyncMock(return_value=[fi])
    page.locator = MagicMock(return_value=file_locator)

    with patch.object(filler, "_get_accessible_name", return_value="Upload Resume"):
        await filler._upload_files("/tmp/cv.pdf", None)

    fi.set_input_files.assert_called_once_with("/tmp/cv.pdf")


@pytest.mark.asyncio
async def test_upload_files_cv_and_cl():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    fi_cv = AsyncMock()
    fi_cv.set_input_files = AsyncMock()
    fi_cl = AsyncMock()
    fi_cl.set_input_files = AsyncMock()

    file_locator = MagicMock()
    file_locator.all = AsyncMock(return_value=[fi_cv, fi_cl])
    page.locator = MagicMock(return_value=file_locator)

    labels = iter(["Upload Resume", "Upload Cover Letter"])
    with patch.object(filler, "_get_accessible_name", side_effect=lambda _: next(labels)):
        await filler._upload_files("/tmp/cv.pdf", "/tmp/cl.pdf")

    fi_cv.set_input_files.assert_called_once_with("/tmp/cv.pdf")
    fi_cl.set_input_files.assert_called_once_with("/tmp/cl.pdf")


@pytest.mark.asyncio
async def test_upload_files_skips_autofill():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    fi = AsyncMock()
    fi.set_input_files = AsyncMock()

    file_locator = MagicMock()
    file_locator.all = AsyncMock(return_value=[fi])
    page.locator = MagicMock(return_value=file_locator)

    with patch.object(filler, "_get_accessible_name", return_value="Autofill from resume"):
        await filler._upload_files("/tmp/cv.pdf", None)

    fi.set_input_files.assert_not_called()


# ── _check_consent ──


@pytest.mark.asyncio
async def test_check_consent_checks_unchecked():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="I agree to the terms"):
        await filler._check_consent()

    cb.check.assert_called_once()


@pytest.mark.asyncio
async def test_check_consent_skips_non_consent():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="Subscribe to newsletter"):
        await filler._check_consent()

    cb.check.assert_not_called()


@pytest.mark.asyncio
async def test_check_consent_skips_already_checked():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=True)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    with patch.object(filler, "_get_accessible_name", return_value="I accept privacy policy"):
        await filler._check_consent()

    cb.check.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "upload_files or check_consent"`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement _upload_files and _check_consent**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_review_form`:

```python
    # ── Deterministic Helpers ──

    async def _upload_files(
        self, cv_path: str | None, cl_path: str | None,
    ) -> None:
        """Upload CV and cover letter to file inputs (deterministic, no LLM).

        Matches by label keyword. Skips autofill/drag-and-drop inputs.
        Uploads CV at most once (deduplication).
        """
        file_inputs = await self._page.locator("input[type='file']").all()
        cv_uploaded = False

        for fi in file_inputs:
            label = await self._get_accessible_name(fi)
            label_lower = label.lower()

            if "autofill" in label_lower or "drag and drop" in label_lower:
                continue

            if "cover" in label_lower and cl_path:
                await fi.set_input_files(str(cl_path))
            elif cv_path and not cv_uploaded:
                await fi.set_input_files(str(cv_path))
                cv_uploaded = True

    async def _check_consent(self) -> None:
        """Auto-check unchecked consent/terms/privacy checkboxes."""
        consent_keywords = [
            "agree", "consent", "terms", "privacy", "accept", "acknowledge",
        ]
        checkboxes = await self._page.get_by_role("checkbox").all()

        for cb in checkboxes:
            label = await self._get_accessible_name(cb)
            if any(kw in label.lower() for kw in consent_keywords):
                if not await cb.is_checked():
                    await cb.check()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "upload_files or check_consent"`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add _upload_files + _check_consent deterministic helpers"
```

---

### Task 7: Page detection + navigation — _is_confirmation_page, _is_submit_page, _click_navigation

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

Replaces the state machine's page classification with direct DOM checks and native button detection.

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── _is_confirmation_page ──


@pytest.mark.asyncio
async def test_is_confirmation_page_true():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Thank you for applying! We will review your application."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is True


@pytest.mark.asyncio
async def test_is_confirmation_page_false():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Please fill in your details below."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is False


# ── _is_submit_page ──


@pytest.mark.asyncio
async def test_is_submit_page_true():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if "Submit" in (name or ""):
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role
    assert await filler._is_submit_page() is True


@pytest.mark.asyncio
async def test_is_submit_page_false():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    assert await filler._is_submit_page() is False


# ── _click_navigation ──


@pytest.mark.asyncio
async def test_click_navigation_submit():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "submitted"


@pytest.mark.asyncio
async def test_click_navigation_dry_run_stop():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    result = await filler._click_navigation(dry_run=True)
    assert result == "dry_run_stop"


@pytest.mark.asyncio
async def test_click_navigation_next():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Continue" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "next"


@pytest.mark.asyncio
async def test_click_navigation_none_found():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    result = await filler._click_navigation(dry_run=False)
    assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "confirmation_page or submit_page or click_navigation"`
Expected: FAIL — methods don't exist

- [ ] **Step 3: Implement all three methods**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_check_consent`:

```python
    # ── Page Detection ──

    async def _is_confirmation_page(self) -> bool:
        """Check if current page is a confirmation/thank-you page."""
        body = await self._page.locator("body").text_content()
        body_lower = (body or "").lower()[:2000]
        return any(phrase in body_lower for phrase in (
            "thank you for applying",
            "application has been received",
            "application submitted",
            "successfully submitted",
        ))

    async def _is_submit_page(self) -> bool:
        """Check if current page has a visible submit button (final page)."""
        for name in ["Submit Application", "Submit", "Apply"]:
            btn = self._page.get_by_role("button", name=name, exact=False)
            if await btn.count() and await btn.first.is_visible():
                return True
        return False

    # ── Navigation ──

    async def _click_navigation(self, dry_run: bool) -> str:
        """Find and click the next/submit button.

        Returns:
            'submitted' — clicked a submit button
            'next' — clicked a continue/next button
            'dry_run_stop' — submit found but dry_run=True
            '' — no navigation button found
        """
        page = self._page
        button_names = [
            ("submit", ["Submit Application", "Submit", "Apply"]),
            ("next", ["Save & Continue", "Continue", "Next", "Proceed"]),
        ]

        for action, names in button_names:
            for name in names:
                btn = page.get_by_role("button", name=name, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    if action == "submit" and dry_run:
                        return "dry_run_stop"
                    await self._move_mouse_to(btn.first)
                    await btn.first.click()
                    await page.wait_for_load_state(
                        "networkidle", timeout=10000,
                    )
                    return "submitted" if action == "submit" else "next"

        # Fallback: links with submit-like text
        for name in ["Submit", "Apply Now", "Continue"]:
            link = page.get_by_role("link", name=name, exact=False)
            if await link.count() and await link.first.is_visible():
                await link.first.click()
                await page.wait_for_load_state(
                    "networkidle", timeout=10000,
                )
                return "next"

        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "confirmation_page or submit_page or click_navigation"`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add page detection + navigation button clicking"
```

---

### Task 8: Main loop — fill()

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `tests/jobpulse/test_native_form_filler.py`

The public `fill()` method — the per-page loop that ties all methods together.

- [ ] **Step 1: Write the failing tests**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── fill() — main loop ──


@pytest.mark.asyncio
async def test_fill_single_page_success():
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Resume", "type": "file", "locator": AsyncMock()},
    ]

    with patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Email": "test@test.com"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "test@test.com"}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_dry_run_stops():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Name": "John"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=True), \
         patch.object(filler, "_review_form", return_value={"pass": True}), \
         patch.object(filler, "_click_navigation", return_value="dry_run_stop"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=True,
        )

    assert result["success"] is True
    assert result["dry_run"] is True


@pytest.mark.asyncio
async def test_fill_confirmation_page():
    filler = _make_filler()

    with patch.object(filler, "_scan_fields", return_value=[]), \
         patch.object(filler, "_is_confirmation_page", return_value=True), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_no_nav_button():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Name": "John"}), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value=""), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is False
    assert "No navigation button" in result["error"]


@pytest.mark.asyncio
async def test_fill_calls_screening_for_unresolved():
    """fill() calls _screen_questions for unresolved non-file fields."""
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Work auth?", "type": "radio", "options": ["Yes", "No"]},
    ]

    with patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch.object(filler, "_map_fields", return_value={"Email": "a@b.com"}), \
         patch.object(filler, "_screen_questions", return_value={"Work auth?": "Yes"}) as mock_screen, \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch.object(filler, "_upload_files", new_callable=AsyncMock), \
         patch.object(filler, "_check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "a@b.com"}, custom_answers={}, dry_run=False,
        )

    mock_screen.assert_called_once()
    assert result["success"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "test_fill_"`
Expected: FAIL — `fill()` doesn't exist

- [ ] **Step 3: Implement fill()**

Add to `NativeFormFiller` in `jobpulse/native_form_filler.py`, after `_click_navigation`. This is the only public method:

```python
    # ── Public Interface ──

    async def fill(
        self,
        platform: str,
        cv_path: str | None,
        cl_path: str | None,
        profile: dict,
        custom_answers: dict,
        dry_run: bool,
    ) -> dict:
        """Fill an application form using native Playwright locators + LLM.

        Per-page loop:
        1. Scan fields via role-based locators
        2. Detect confirmation page -> done
        3. LLM Call 1: map profile -> field values
        4. LLM Call 2: screening questions (optional, for unresolved fields)
        5. Fill each field by label (DOM order)
        6. Upload files (deterministic)
        7. Auto-check consent boxes
        8. Anti-detection timing
        9. Pre-submit review on final page (LLM Call 3)
        10. Click next/submit
        """
        for page_num in range(1, MAX_FORM_PAGES + 1):
            # 1. Scan fields
            fields = await self._scan_fields()

            # 2. Confirmation page?
            if await self._is_confirmation_page():
                return {"success": True, "pages_filled": page_num}

            # 3. LLM Call 1: map fields
            mapping = await self._map_fields(
                fields, profile, custom_answers, platform,
            )

            # 4. LLM Call 2: screening for unresolved non-file fields
            unresolved = [
                f for f in fields
                if f["label"] not in mapping and f["type"] != "file"
            ]
            if unresolved:
                screening = await self._screen_questions(
                    unresolved, custom_answers.get("_job_context"),
                )
                mapping.update(screening)

            # 5. Fill each field by label
            for label, value in mapping.items():
                await self._fill_by_label(label, value)

            # 6. File uploads
            await self._upload_files(cv_path, cl_path)

            # 7. Consent boxes
            await self._check_consent()

            # 8. Anti-detection timing
            min_time = _PLATFORM_MIN_PAGE_TIME.get(platform, 5.0)
            await asyncio.sleep(min_time * random.uniform(0.8, 1.2))

            # 9. Pre-submit review on final page
            if await self._is_submit_page():
                if dry_run:
                    return {
                        "success": True, "dry_run": True,
                        "pages_filled": page_num,
                    }
                review = await self._review_form()
                if not review.get("pass"):
                    logger.warning(
                        "Pre-submit review failed: %s", review.get("issues"),
                    )

            # 10. Click next/submit
            clicked = await self._click_navigation(dry_run)
            if clicked == "submitted":
                return {"success": True, "pages_filled": page_num}
            if clicked == "dry_run_stop":
                return {
                    "success": True, "dry_run": True,
                    "pages_filled": page_num,
                }
            if not clicked:
                return {
                    "success": False,
                    "error": f"No navigation button on page {page_num}",
                }

        return {
            "success": False,
            "error": f"Exhausted {MAX_FORM_PAGES} form pages",
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "test_fill_"`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): add fill() — main per-page form-filling loop"
```

---

### Task 9: Wire _fill_application branch in orchestrator

**Files:**
- Modify: `jobpulse/application_orchestrator.py:595-600`
- Modify: `tests/jobpulse/test_native_form_filler.py`

The orchestrator's `_fill_application()` creates a `NativeFormFiller` and delegates to it when `engine="playwright"`. Only 6 lines of new code in the orchestrator.

- [ ] **Step 1: Write the failing test**

Append to `tests/jobpulse/test_native_form_filler.py`:

```python
# ── Orchestrator integration ──

from jobpulse.application_orchestrator import ApplicationOrchestrator


@pytest.mark.asyncio
async def test_fill_application_routes_to_native_filler():
    """_fill_application creates NativeFormFiller when engine='playwright'."""
    driver = AsyncMock()
    driver.page = MagicMock()
    orch = ApplicationOrchestrator(driver=driver, engine="playwright")

    with patch("jobpulse.application_orchestrator.NativeFormFiller") as MockFiller:
        mock_instance = AsyncMock()
        mock_instance.fill = AsyncMock(return_value={"success": True, "pages_filled": 1})
        MockFiller.return_value = mock_instance

        result = await orch._fill_application(
            platform="greenhouse",
            snapshot={"url": "https://example.com", "fields": [], "buttons": []},
            cv_path="/tmp/cv.pdf",
            cover_letter_path=None,
            profile={"email": "test@test.com"},
            custom_answers={},
            overrides=None,
            dry_run=False,
            form_intelligence=None,
        )

    MockFiller.assert_called_once_with(page=driver.page, driver=driver)
    mock_instance.fill.assert_called_once()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_application_extension_still_uses_state_machine():
    """_fill_application uses state machine when engine='extension'."""
    driver = AsyncMock()
    driver.page = None
    orch = ApplicationOrchestrator(driver=driver, engine="extension")

    # Snapshot that triggers CONFIRMATION in state machine
    snapshot = {
        "url": "https://example.com",
        "title": "Apply",
        "fields": [],
        "buttons": [{"text": "Submit", "selector": "#submit", "type": "submit", "enabled": True}],
        "page_text_preview": "Thank you for applying! Your application has been received.",
    }

    result = await orch._fill_application(
        platform="greenhouse",
        snapshot=snapshot,
        cv_path="/tmp/cv.pdf",
        cover_letter_path=None,
        profile={},
        custom_answers={},
        overrides=None,
        dry_run=False,
        form_intelligence=None,
    )

    assert result.get("success") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "fill_application"`
Expected: FAIL — orchestrator doesn't import or use NativeFormFiller yet

- [ ] **Step 3: Add the engine branch to _fill_application**

In `jobpulse/application_orchestrator.py`, add the import at the top (after the existing imports, around line 38):

```python
from jobpulse.native_form_filler import NativeFormFiller
```

Then modify `_fill_application` (line 595-600). Replace:

```python
    async def _fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        """Multi-page form filling via state machine."""
        machine = get_state_machine(platform)
```

With:

```python
    async def _fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        """Multi-page form filling — branches by engine.

        engine='playwright': NativeFormFiller (locators + LLM)
        engine='extension': state machine + snapshots (original path)
        """
        if self.engine == "playwright":
            filler = NativeFormFiller(page=self.driver.page, driver=self.driver)
            return await filler.fill(
                platform=platform,
                cv_path=str(cv_path) if cv_path else None,
                cl_path=str(cover_letter_path) if cover_letter_path else None,
                profile=profile or {},
                custom_answers=custom_answers or {},
                dry_run=dry_run,
            )

        machine = get_state_machine(platform)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "fill_application"`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v`
Expected: ALL PASS (39 tests)

Run: `python -m pytest tests/ -v -x --timeout=60 -k "not slow" 2>&1 | tail -20`
Expected: No regressions

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator.py jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(native): wire NativeFormFiller into orchestrator engine branch"
```

---

## Appendix: Class Dependency Map

```
ApplicationOrchestrator._fill_application()
  └─ engine="playwright" ─→ NativeFormFiller(page, driver)
       │
       ├─ fill()              ← only public method
       │   ├─ _scan_fields()
       │   │    └─ _get_accessible_name()
       │   ├─ _is_confirmation_page()
       │   ├─ _map_fields()           (LLM Call 1)
       │   ├─ _screen_questions()     (LLM Call 2, optional)
       │   ├─ _fill_by_label()
       │   │    ├─ _smart_scroll()    → delegates to PlaywrightDriver
       │   │    └─ _move_mouse_to()   → delegates to PlaywrightDriver
       │   ├─ _upload_files()
       │   │    └─ _get_accessible_name()
       │   ├─ _check_consent()
       │   │    └─ _get_accessible_name()
       │   ├─ _is_submit_page()
       │   ├─ _review_form()          (LLM Call 3)
       │   └─ _click_navigation()
       │        └─ _move_mouse_to()
       │
       └─ Dependencies injected via constructor:
            page: Playwright Page (from PlaywrightDriver.page property)
            driver: PlaywrightDriver (for human-like mouse/scroll behavior)
```

## Appendix: SOLID Principles Applied

| Principle | Application |
|---|---|
| **Single Responsibility** | `NativeFormFiller` owns native filling. Orchestrator owns lifecycle. Neither knows the other's internals. |
| **Open/Closed** | Adding a new engine = creating a new filler class. No modification to existing `NativeFormFiller` or extension path. |
| **Liskov Substitution** | Both engines produce the same `dict` return type from `_fill_application()` — callers don't know which engine ran. |
| **Interface Segregation** | `NativeFormFiller` has one public method (`fill()`). Callers don't see 12 internal methods. |
| **Dependency Inversion** | `NativeFormFiller` receives `page` and `driver` via constructor — no hard-coded dependencies on concrete classes. |

## Appendix: Test Count Summary

| Task | Tests | Cumulative |
|---|---|---|
| Task 1: page property | 1 | 1 |
| Task 2: scaffold + scanning | 5 | 6 |
| Task 3: fill by label | 5 | 11 |
| Task 4: LLM map fields | 3 | 14 |
| Task 5: screening + review | 5 | 19 |
| Task 6: uploads + consent | 6 | 25 |
| Task 7: page detection + nav | 8 | 33 |
| Task 8: fill() loop | 5 | 38 |
| Task 9: wire branch | 2 | 40 |
| **Total** | **40** | |
