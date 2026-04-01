# LinkedIn Live Apply Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply for the Gousto LinkedIn job (`/jobs/view/4395143521/`) in dry-run mode, learning and fixing the adapter as we go, with all fixes persisted to Ralph Loop SQLite.

**Architecture:** Build a standalone live-apply harness that calls `LinkedInAdapter` directly (bypassing rate limiter), add login-wall detection + verbose DOM capture to the adapter, run it against the real Gousto URL, fix each issue as it surfaces, and commit all fixes.

**Tech Stack:** Playwright (sync API), `jobpulse/ats_adapters/linkedin.py`, `jobpulse/ralph_loop/pattern_store.py`, `jobpulse/cv_templates/generate_cv.py`, pytest + MagicMock

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `jobpulse/ats_adapters/linkedin.py` | **Modify** | Add `_handle_login_wall()`, `_dump_page_context()`; fix gaps discovered during live run |
| `scripts/live_apply_linkedin.py` | **Create** | Standalone dry-run harness — generates CV, calls adapter, verbose output, pauses on failure |
| `tests/test_linkedin_adapter.py` | **Create** | Unit tests for login wall detection, DOM capture, and any adapter fixes |

---

## Task 1: Add login wall detection + verbose DOM capture to linkedin.py

**Files:**
- Modify: `jobpulse/ats_adapters/linkedin.py`
- Test: `tests/test_linkedin_adapter.py`

These two helpers are added *before* any live run so we have full visibility into every page.

- [ ] **Step 1.1: Write failing tests for `_handle_login_wall` and `_dump_page_context`**

Create `tests/test_linkedin_adapter.py`:

```python
"""Tests for LinkedIn adapter helpers — login wall detection and DOM capture.

All Playwright interactions mocked with MagicMock.
"""
from __future__ import annotations
from unittest.mock import MagicMock
import pytest

from jobpulse.ats_adapters.linkedin import (
    _handle_login_wall,
    _dump_page_context,
)


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/4395143521/"
    page.title.return_value = "Gousto | LinkedIn"
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    return page


# --- _handle_login_wall ---

def test_handle_login_wall_no_wall_returns_no_wall(mock_page):
    mock_page.query_selector.return_value = None
    result = _handle_login_wall(mock_page)
    assert result == "no_wall"


def test_handle_login_wall_continue_as_button_clicks_and_returns(mock_page):
    btn = MagicMock()
    def qs(sel):
        if "Continue as" in sel:
            return btn
        return None
    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "clicked_continue"
    btn.click.assert_called_once()


def test_handle_login_wall_sign_in_button_returns_needs_login(mock_page):
    sign_in = MagicMock()
    def qs(sel):
        if "Sign in" in sel and "Continue" not in sel:
            return sign_in
        return None
    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "needs_login"


def test_handle_login_wall_continue_takes_priority_over_signin(mock_page):
    """'Continue as Yash' must be tried before generic Sign-in."""
    continue_btn = MagicMock()
    signin_btn = MagicMock()
    def qs(sel):
        if "Continue as" in sel:
            return continue_btn
        if "Sign in" in sel:
            return signin_btn
        return None
    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "clicked_continue"
    continue_btn.click.assert_called_once()
    signin_btn.click.assert_not_called()


# --- _dump_page_context ---

def test_dump_page_context_returns_required_keys(mock_page):
    ctx = _dump_page_context(mock_page)
    for key in ("url", "inputs", "buttons", "modal_text", "selects"):
        assert key in ctx, f"Missing key: {key}"


def test_dump_page_context_captures_url(mock_page):
    mock_page.url = "https://www.linkedin.com/jobs/view/999/"
    ctx = _dump_page_context(mock_page)
    assert ctx["url"] == "https://www.linkedin.com/jobs/view/999/"


def test_dump_page_context_no_modal_gives_empty_modal_text(mock_page):
    mock_page.query_selector.return_value = None
    ctx = _dump_page_context(mock_page)
    assert ctx["modal_text"] == ""


def test_dump_page_context_captures_input_aria_labels(mock_page):
    inp = MagicMock()
    inp.get_attribute.side_effect = lambda a: {
        "type": "text", "name": "phoneNumber", "id": "ph-1",
        "placeholder": "Mobile phone number", "aria-label": "Mobile phone number",
    }.get(a, "")
    inp.input_value.return_value = ""
    # query_selector_all returns [inp] for "input:not([type='hidden'])"
    def qsa(sel):
        if "input" in sel:
            return [inp]
        return []
    mock_page.query_selector_all.side_effect = qsa
    ctx = _dump_page_context(mock_page)
    assert len(ctx["inputs"]) == 1
    assert ctx["inputs"][0]["aria_label"] == "Mobile phone number"


def test_dump_page_context_caps_inputs_at_20(mock_page):
    inputs = [MagicMock() for _ in range(30)]
    for inp in inputs:
        inp.get_attribute.return_value = ""
        inp.input_value.return_value = ""
    def qsa(sel):
        if "input" in sel:
            return inputs
        return []
    mock_page.query_selector_all.side_effect = qsa
    ctx = _dump_page_context(mock_page)
    assert len(ctx["inputs"]) <= 20
```

- [ ] **Step 1.2: Run tests — verify they fail (functions not yet defined)**

```bash
cd /Users/yashbishnoi/Downloads/multi_agent_patterns
python -m pytest tests/test_linkedin_adapter.py -v 2>&1 | head -30
```

Expected: `ImportError` or `AttributeError` — `_handle_login_wall` and `_dump_page_context` not defined.

- [ ] **Step 1.3: Add `_handle_login_wall` and `_dump_page_context` to `linkedin.py`**

Add these two functions after the existing `_find_modal` function (around line 66):

```python
def _handle_login_wall(page) -> str:
    """Detect and handle LinkedIn auth prompts before Easy Apply.

    Checks for 'Continue as [Name]' (session refresh) and generic Sign-in.
    Returns: 'clicked_continue' | 'needs_login' | 'no_wall'
    """
    # 'Continue as [Name]' — session cookie valid but needs confirmation
    for sel in [
        "button:has-text('Continue as')",
        "a:has-text('Continue as')",
        "[data-tracking-control-name*='continue']",
        ".sign-in-modal__continue-btn",
    ]:
        el = page.query_selector(sel)
        if el:
            logger.info("LinkedIn: 'Continue as' button found — clicking")
            el.click()
            _human_delay(2.0, 3.0)
            return "clicked_continue"

    # Generic sign-in wall (session expired / guest layout)
    for sel in [
        "button:has-text('Sign in')",
        "a:has-text('Sign in')",
        ".authwall-join-form__form-toggle--bottom a",
    ]:
        el = page.query_selector(sel)
        if el:
            logger.warning(
                "LinkedIn: Sign-in wall detected — session may be expired. "
                "Run: python scripts/linkedin_login.py"
            )
            return "needs_login"

    return "no_wall"


def _dump_page_context(page) -> dict:
    """Capture current page state for verbose logging and Ralph Loop diagnosis.

    Returns a dict with url, modal_text, inputs (list), buttons (list), selects (list).
    Each input entry: {type, name, id, placeholder, aria_label, value}.
    """
    ctx: dict = {
        "url": page.url,
        "modal_text": "",
        "inputs": [],
        "buttons": [],
        "selects": [],
    }

    # Modal content
    modal = _find_modal(page)
    if modal:
        ctx["modal_text"] = (modal.text_content() or "")[:500]

    # Inputs — cap at 20 to avoid log spam
    try:
        for inp in page.query_selector_all("input:not([type='hidden'])")[:20]:
            inp_type = inp.get_attribute("type") or "text"
            value = ""
            if inp_type not in ("file", "checkbox", "radio", "submit"):
                try:
                    value = inp.input_value() or ""
                except Exception:
                    pass
            ctx["inputs"].append({
                "type": inp_type,
                "name": inp.get_attribute("name") or "",
                "id": inp.get_attribute("id") or "",
                "placeholder": inp.get_attribute("placeholder") or "",
                "aria_label": inp.get_attribute("aria-label") or "",
                "value": value[:80],
            })
    except Exception as exc:
        logger.debug("_dump_page_context: input scan failed: %s", exc)

    # Buttons
    try:
        for btn in page.query_selector_all("button")[:20]:
            text = (btn.text_content() or "").strip()
            if text:
                ctx["buttons"].append(text)
    except Exception:
        pass

    # Selects
    try:
        for sel in page.query_selector_all("select")[:10]:
            ctx["selects"].append({
                "name": sel.get_attribute("name") or "",
                "id": sel.get_attribute("id") or "",
                "aria_label": sel.get_attribute("aria-label") or "",
            })
    except Exception:
        pass

    return ctx
```

- [ ] **Step 1.4: Wire `_handle_login_wall` + `_dump_page_context` into `fill_and_submit`**

In `fill_and_submit`, after `page.goto(url, ...)` and `_screenshot(page, cv_path, "01_job_page")`, add:

```python
                # --- Login wall check ---
                wall_result = _handle_login_wall(page)
                logger.info("LinkedIn: login wall check → %s", wall_result)
                if wall_result == "needs_login":
                    _screenshot(page, cv_path, "01b_needs_login")
                    return {
                        "success": False,
                        "screenshot": cv_path.parent / "linkedin_01b_needs_login.png",
                        "error": "LinkedIn session expired. Run: python scripts/linkedin_login.py",
                    }
                if wall_result == "clicked_continue":
                    _screenshot(page, cv_path, "01b_continued_as_yash")
                    _human_delay(1.0, 2.0)

                # --- Verbose page context ---
                ctx = _dump_page_context(page)
                logger.info("LinkedIn [PAGE CONTEXT]: url=%s buttons=%s", ctx["url"], ctx["buttons"][:5])
```

Also add verbose context dump inside the wizard page loop, after `_screenshot(page, cv_path, f"page_{page_num:02d}")`:

```python
                    ctx = _dump_page_context(page)
                    logger.info(
                        "LinkedIn [PAGE %d CONTEXT] modal_text=%s... inputs=%s buttons=%s",
                        page_num,
                        ctx["modal_text"][:100],
                        [{k: v for k, v in i.items() if v} for i in ctx["inputs"]],
                        ctx["buttons"],
                    )
```

- [ ] **Step 1.5: Run tests — verify they pass**

```bash
python -m pytest tests/test_linkedin_adapter.py -v
```

Expected output:
```
tests/test_linkedin_adapter.py::test_handle_login_wall_no_wall_returns_no_wall PASSED
tests/test_linkedin_adapter.py::test_handle_login_wall_continue_as_button_clicks_and_returns PASSED
tests/test_linkedin_adapter.py::test_handle_login_wall_sign_in_button_returns_needs_login PASSED
tests/test_linkedin_adapter.py::test_handle_login_wall_continue_takes_priority_over_signin PASSED
tests/test_linkedin_adapter.py::test_dump_page_context_returns_required_keys PASSED
tests/test_linkedin_adapter.py::test_dump_page_context_captures_url PASSED
tests/test_linkedin_adapter.py::test_dump_page_context_no_modal_gives_empty_modal_text PASSED
tests/test_linkedin_adapter.py::test_dump_page_context_captures_input_aria_labels PASSED
tests/test_linkedin_adapter.py::test_dump_page_context_caps_inputs_at_20 PASSED

9 passed in 0.XX s
```

- [ ] **Step 1.6: Commit**

```bash
git add jobpulse/ats_adapters/linkedin.py tests/test_linkedin_adapter.py
git commit -m "feat(linkedin): add login wall detection + verbose DOM capture"
```

---

## Task 2: Write the live apply harness

**Files:**
- Create: `scripts/live_apply_linkedin.py`

- [ ] **Step 2.1: Create `scripts/live_apply_linkedin.py`**

```python
#!/usr/bin/env python3
"""Live apply test harness — Gousto on LinkedIn, dry-run mode.

Bypasses rate limiter. AUTO_SUBMIT hardcoded to false.
Generates a Gousto-tailored CV, runs the full adapter, streams verbose logs,
saves screenshots to data/applications/gousto_test/, pauses on failure.

Usage:
    python scripts/live_apply_linkedin.py
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Project root on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Safety: force dry-run no matter what .env says
os.environ["JOB_AUTOPILOT_AUTO_SUBMIT"] = "false"

# Verbose logging to stdout
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  [%(levelname)-8s]  %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("live_apply")

from jobpulse.applicator import PROFILE
from jobpulse.ats_adapters.linkedin import LinkedInAdapter
from jobpulse.config import DATA_DIR
from jobpulse.cv_templates.generate_cv import generate_cv_pdf

GOUSTO_URL = "https://www.linkedin.com/jobs/view/4395143521/"
OUTPUT_DIR = DATA_DIR / "applications" / "gousto_test"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DIVIDER = "=" * 62


def _banner(title: str) -> None:
    print(f"\n{DIVIDER}")
    print(f"  {title}")
    print(DIVIDER)


def _pause_on_failure(result: dict) -> bool:
    """Print failure details and pause for human review.

    Returns True to continue (user pressed Enter), False to abort (Ctrl+C).
    """
    print(f"\n[ERROR] Application step failed.")
    print(f"  error:      {result.get('error')}")
    print(f"  screenshot: {result.get('screenshot')}")
    print(f"\nReview the screenshot and tell Claude what you see.")
    print("Claude will diagnose + fix the issue, then re-run.\n")
    try:
        input("Press Enter when ready to continue, or Ctrl+C to abort... ")
        return True
    except KeyboardInterrupt:
        print("\nAborted by user.")
        return False


def main() -> None:
    _banner("LinkedIn Live Apply — Gousto Dry Run")
    print(f"  URL:         {GOUSTO_URL}")
    print(f"  Output:      {OUTPUT_DIR}")
    print(f"  AUTO_SUBMIT: false (hardcoded)")

    # ---- Step 1: Generate Gousto CV ----
    _banner("Step 1: Generating Gousto CV")
    cv_path = generate_cv_pdf(
        company="Gousto",
        location="London, UK",
        output_dir=str(OUTPUT_DIR),
    )
    print(f"  ✅ CV: {cv_path.name}")

    # ---- Step 2: Run adapter ----
    _banner("Step 2: Running LinkedIn adapter")
    print("  Browser will open. Watch it fill the form.\n")

    adapter = LinkedInAdapter()
    result = adapter.fill_and_submit(
        url=GOUSTO_URL,
        cv_path=cv_path,
        cover_letter_path=None,
        profile=PROFILE,
        custom_answers={},
        overrides=None,
    )

    # ---- Step 3: Report ----
    _banner("Result")
    print(f"  success:          {result.get('success')}")
    print(f"  error:            {result.get('error') or 'None'}")
    print(f"  screenshot:       {result.get('screenshot') or 'None'}")
    print(f"  needs_manual:     {result.get('needs_manual_submit', False)}")

    if result.get("needs_manual_submit"):
        print("\n  ✅ Reached Review page — all pages filled!")
        print("  Dry-run complete. When ready to submit for real,")
        print("  set JOB_AUTOPILOT_AUTO_SUBMIT=true and re-run.\n")
    elif not result.get("success"):
        _pause_on_failure(result)
    else:
        print("\n  ✅ Done.\n")

    # Print all screenshots generated
    screenshots = sorted(OUTPUT_DIR.glob("linkedin_*.png"))
    if screenshots:
        print(f"\n  Screenshots saved ({len(screenshots)} total):")
        for s in screenshots:
            print(f"    {s.name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2.2: Verify the script is importable (no syntax errors)**

```bash
cd /Users/yashbishnoi/Downloads/multi_agent_patterns
python -c "import scripts.live_apply_linkedin" 2>&1 || python scripts/live_apply_linkedin.py --help 2>&1 | head -5
```

Expected: no `SyntaxError` or `ImportError`. (Script will exit immediately if run without proper env — that's fine at this stage.)

- [ ] **Step 2.3: Commit**

```bash
git add scripts/live_apply_linkedin.py
git commit -m "feat(scripts): add LinkedIn live apply dry-run harness"
```

---

## Task 3: Run the live session against the Gousto URL

**This is the interactive phase.** The browser opens, fills each page, and logs every field it sees. We observe and fix.

- [ ] **Step 3.1: Run the harness**

```bash
cd /Users/yashbishnoi/Downloads/multi_agent_patterns
python scripts/live_apply_linkedin.py 2>&1 | tee /tmp/linkedin_run.log
```

Watch the terminal. For each page the adapter processes you will see lines like:

```
[INFO] LinkedIn [PAGE 1 CONTEXT] modal_text=Contact info... inputs=[{'aria_label': 'Mobile phone number', ...}] buttons=['Next', 'Dismiss']
```

- [ ] **Step 3.2: Observe and record findings**

For each page reached, note:
- What `[PAGE N CONTEXT]` shows for `inputs` (especially `aria_label` values)
- Which page caused a failure (if any)
- What the screenshot at that page shows

Common pages to watch:
| Page | Look for in inputs | Known gap |
|------|--------------------|-----------|
| Contact | `aria_label` containing "location" or "city" | Location typeahead selector |
| Resume | file upload inputs | Should auto-upload CV |
| Experience | Any inputs/selects for dates, job titles | `_fill_experience_page` just waits |
| Questions | Various labels | Screening answer coverage |

- [ ] **Step 3.3: Fix any failure encountered**

For each failure:

1. Read the `[PAGE N CONTEXT]` log output to see actual input `aria_label` / `id` / `placeholder`
2. Update the relevant function in `jobpulse/ats_adapters/linkedin.py`
3. Save the fix to Ralph Loop (see Task 4)
4. Re-run: `python scripts/live_apply_linkedin.py`

---

## Task 4: Fix discovered gaps + save Ralph Loop patterns

**Files:**
- Modify: `jobpulse/ats_adapters/linkedin.py`
- Modify: `tests/test_linkedin_adapter.py`

This task is executed immediately after each failure found in Task 3. Repeat for each gap.

### 4A — Fix pattern: Location typeahead

When `[PAGE 1 CONTEXT]` shows the location input, its `aria_label` will be visible. Add that selector to `_fill_location_typeahead()`:

- [ ] **Step 4A.1: Update `_fill_location_typeahead()` with observed selector**

In `_fill_location_typeahead`, add the observed selector at the **top** of the `for sel in [...]` list:

```python
def _fill_location_typeahead(page, location: str) -> None:
    """Fill the location typeahead field in the Easy Apply modal."""
    # Selectors ordered: most-specific (logged-in) first, fallbacks after
    for sel in [
        # ← INSERT OBSERVED SELECTOR HERE, e.g.:
        # "input[aria-label='City, state, or zip code']",
        # "input[aria-label='Search for location']",
        "input[aria-label*='typeahead'][aria-label*='ocation']",
        "input[aria-label*='City']",
        "input[id*='location']",
        "input[placeholder*='City']",
        "input[placeholder*='ocation']",
    ]:
```

- [ ] **Step 4A.2: Write a test for the new selector**

In `tests/test_linkedin_adapter.py`, add:

```python
from jobpulse.ats_adapters.linkedin import _fill_location_typeahead

def test_fill_location_typeahead_clicks_first_suggestion(mock_page):
    """Location field found by aria-label → types → clicks suggestion."""
    location_input = MagicMock()
    suggestion = MagicMock()

    def qs(sel):
        # Match the first selector that fires — using the observed one
        if "aria-label" in sel and ("ocation" in sel or "City" in sel or "typeahead" in sel):
            return location_input
        return None

    def qsa(sel):
        if "option" in sel or "selectable" in sel or "hit" in sel:
            return [suggestion]
        return []

    mock_page.query_selector.side_effect = qs
    mock_page.query_selector_all.side_effect = qsa
    mock_page.keyboard = MagicMock()

    _fill_location_typeahead(mock_page, "Dundee, UK")

    location_input.fill.assert_called_once_with("")
    suggestion.click.assert_called_once()


def test_fill_location_typeahead_presses_enter_when_no_suggestion(mock_page):
    """No suggestion dropdown → fall back to pressing Enter."""
    location_input = MagicMock()

    def qs(sel):
        if "ocation" in sel or "City" in sel or "typeahead" in sel:
            return location_input
        return None

    mock_page.query_selector.side_effect = qs
    mock_page.query_selector_all.return_value = []
    mock_page.keyboard = MagicMock()

    _fill_location_typeahead(mock_page, "Dundee, UK")
    mock_page.keyboard.press.assert_called_once_with("Enter")
```

- [ ] **Step 4A.3: Save location fix to Ralph Loop**

Run this Python snippet (or add to the harness script):

```python
from jobpulse.ralph_loop.pattern_store import PatternStore, compute_error_signature

store = PatternStore()
# Replace OBSERVED_SELECTOR with the actual aria-label from [PAGE 1 CONTEXT] log output.
# Example: if log shows aria_label='City, state, or zip code' then:
#   OBSERVED_SELECTOR = "input[aria-label='City, state, or zip code']"
OBSERVED_SELECTOR = "input[aria-label='<from_log_output>']"
ORIGINAL_SELECTOR = "input[aria-label*='typeahead'][aria-label*='ocation']"

sig = compute_error_signature("linkedin", "contact_location", "location typeahead element not found")
store.save_fix(
    platform="linkedin",
    step_name="contact_location",
    error_signature=sig,
    fix_type="selector_override",
    fix_payload={
        "original_selector": ORIGINAL_SELECTOR,
        "new_selector": OBSERVED_SELECTOR,
    },
    confidence=0.9,
)
print("Ralph Loop pattern saved.")
```

### 4B — Fix pattern: Work experience page

When `[PAGE N CONTEXT]` shows the experience page, it may have "Confirm", "Save", or employment dropdowns.

- [ ] **Step 4B.1: Update `_fill_experience_page()` with observed interactions**

Replace the current stub with what the page actually needs. Pattern:

```python
def _fill_experience_page(page) -> None:
    """Fill Page 3: Work experience — confirm existing or fill missing fields."""
    _human_delay(1.0, 2.0)

    modal = _find_modal(page)
    if not modal:
        return

    # Log context to see what's actually on this page
    ctx = _dump_page_context(page)
    logger.info("Experience page inputs: %s", ctx["inputs"])
    logger.info("Experience page buttons: %s", ctx["buttons"])

    # If there's a "Confirm" or "Save" button, click it
    for label in ["Confirm", "Save", "Continue"]:
        for btn in modal.query_selector_all("button"):
            if label.lower() in (btn.text_content() or "").lower():
                btn.scroll_into_view_if_needed()
                _human_delay(0.3, 0.8)
                btn.click()
                _human_delay(1.0, 2.0)
                logger.info("Experience page: clicked '%s'", label)
                return

    # Nothing to click — experience pre-filled, just wait
    logger.info("Experience page: pre-filled, no action needed")
```

- [ ] **Step 4B.2: Run tests + verify**

```bash
python -m pytest tests/test_linkedin_adapter.py -v
```

All tests should pass.

### 4C — Fix pattern: Any other selector failure

If a different page breaks:

1. Read `[PAGE N CONTEXT]` from the log to find the actual selector
2. Add it via `resolve_selector()` to the relevant function
3. Save to Ralph Loop as a `selector_override` pattern (same pattern as 4A.3)
4. Add a test (same pattern as 4A.2)
5. Re-run

---

## Task 5: Commit all session fixes + update memory

- [ ] **Step 5.1: Run full test suite to verify nothing broke**

```bash
python -m pytest tests/test_linkedin_adapter.py tests/test_ralph_loop.py -v
```

Expected: all tests pass.

- [ ] **Step 5.2: Commit all changes**

```bash
cd /Users/yashbishnoi/Downloads/multi_agent_patterns
git add jobpulse/ats_adapters/linkedin.py tests/test_linkedin_adapter.py
git commit -m "fix(linkedin): location typeahead + experience page + session fixes from Gousto live run"
```

- [ ] **Step 5.3: Update MEMORY.md with session findings**

Add a new memory file `linkedin_session_findings.md` documenting:
- Which selectors were discovered (logged-in layout vs guest)
- Which pages needed extra interaction
- Any Ralph Loop patterns saved
- Update MEMORY.md index to include it

- [ ] **Step 5.4: Final verification — re-run harness end-to-end**

```bash
python scripts/live_apply_linkedin.py 2>&1 | tee /tmp/linkedin_final_run.log
```

Expected: runs to Review page without any failures, `needs_manual_submit: True` in output.
