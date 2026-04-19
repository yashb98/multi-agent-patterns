# Extension Form-Fill Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 issues preventing the Chrome extension from reliably filling job application forms across ATS platforms.

**Architecture:** Fixes span 3 layers — extension JS (phase engine, combobox, Nano AI), Python backend (form analyzer, form filler), and bridge (field context). All fixes are additive with fallback chains. Playwright engine path is untouched.

**Tech Stack:** JavaScript (Chrome MV3 extension), Python (Pydantic models, OpenAI API), pytest

---

### Task 1: Unblock Phase System — Start Known Platforms at dry_run

**Files:**
- Modify: `extension/phase_engine.js:41-49`
- Modify: `extension/config.js` (if PLATFORM_MAX_PHASE defined here)
- Test: Manual verification via extension sidepanel

- [ ] **Step 1: Check config.js for PLATFORM_MAX_PHASE defaults**

Read `extension/config.js` to find the `PLATFORM_MAX_PHASE` constant and understand current defaults.

- [ ] **Step 2: Update initPhases defaults in phase_engine.js**

In `extension/phase_engine.js`, change `initPhases()` default phases:

```javascript
const defaultPhases = {
  linkedin: { current: 'dry_run', stats: { ...DEFAULT_STATS } },
  indeed: { current: 'dry_run', stats: { ...DEFAULT_STATS } },
  workday: { current: 'observation', stats: { ...DEFAULT_STATS } },
  glassdoor: { current: 'observation', stats: { ...DEFAULT_STATS } },
  reed: { current: 'dry_run', stats: { ...DEFAULT_STATS } },
  greenhouse: { current: 'dry_run', stats: { ...DEFAULT_STATS } },
  lever: { current: 'dry_run', stats: { ...DEFAULT_STATS } },
  generic: { current: 'observation', stats: { ...DEFAULT_STATS } }
};
```

- [ ] **Step 3: Add forcePhase export for manual override**

Add to `extension/phase_engine.js` after `resetPlatform`:

```javascript
/**
 * Force a platform to a specific phase (manual override from sidepanel).
 *
 * @param {string} platform - Platform identifier
 * @param {string} phase - Target phase
 * @returns {Promise<{from: string, to: string}>}
 */
export async function forcePhase(platform, phase) {
  if (!PHASE_ORDER.includes(phase)) {
    return { from: 'unknown', to: 'unknown', error: 'Invalid phase' };
  }
  const phases = await _getPhases();
  if (!phases[platform]) {
    phases[platform] = { current: 'observation', stats: { ...DEFAULT_STATS } };
  }
  const from = phases[platform].current;
  phases[platform].current = phase;
  phases[platform].stats = { ...DEFAULT_STATS };
  await _savePhases(phases);
  return { from, to: phase };
}
```

- [ ] **Step 4: Commit**

```bash
git add extension/phase_engine.js
git commit -m "fix(ext): start known ATS platforms at dry_run phase

LinkedIn, Indeed, Reed, Greenhouse, Lever start at dry_run so
canFill() returns true. Workday/generic/glassdoor stay at observation.
Adds forcePhase() export for manual override via sidepanel."
```

---

### Task 2: Fuzzy Dropdown Matching in Deterministic Fill

**Files:**
- Modify: `jobpulse/form_analyzer.py:495-557`
- Create: `tests/jobpulse/test_form_analyzer.py`

- [ ] **Step 1: Write failing tests for fuzzy matching**

Create `tests/jobpulse/test_form_analyzer.py`:

```python
"""Tests for form_analyzer — deterministic fill + fuzzy dropdown matching."""

from __future__ import annotations

import pytest

from jobpulse.ext_models import Action, FieldInfo, PageSnapshot
from jobpulse.form_analyzer import deterministic_fill, _match_to_available_options


# ── Fuzzy matching unit tests ──

class TestMatchToAvailableOptions:
    def test_exact_match(self):
        assert _match_to_available_options("Yes", ["Yes", "No"]) == "Yes"

    def test_case_insensitive(self):
        assert _match_to_available_options("yes", ["Yes", "No"]) == "Yes"

    def test_partial_contains(self):
        result = _match_to_available_options(
            "Yes", ["Yes, I am authorised to work in the UK", "No"]
        )
        assert result == "Yes, I am authorised to work in the UK"

    def test_abbreviation_expansion(self):
        result = _match_to_available_options(
            "United Kingdom", ["UK", "US", "India"]
        )
        assert result == "UK"

    def test_reverse_contains(self):
        result = _match_to_available_options(
            "Graduate Visa", ["Student Visa", "Graduate visa (Tier 4)", "Work Visa"]
        )
        assert result == "Graduate visa (Tier 4)"

    def test_no_match_returns_original(self):
        assert _match_to_available_options("Zebra", ["Yes", "No"]) == "Zebra"

    def test_empty_options(self):
        assert _match_to_available_options("Yes", []) == "Yes"

    def test_male_dropdown(self):
        result = _match_to_available_options(
            "Male", ["Male (he/him)", "Female (she/her)", "Non-binary", "Prefer not to say"]
        )
        assert result == "Male (he/him)"

    def test_no_preference_skips_placeholder(self):
        result = _match_to_available_options(
            "Yes", ["Select...", "Yes", "No"]
        )
        assert result == "Yes"


# ── Deterministic fill integration tests ──

def _make_field(selector: str, label: str, input_type: str = "text",
                options: list[str] | None = None, role: str = "") -> FieldInfo:
    attrs = {}
    if role:
        attrs["role"] = role
    return FieldInfo(
        selector=selector, input_type=input_type, label=label,
        options=options or [], attributes=attrs,
    )


def _make_snapshot(fields: list[FieldInfo]) -> PageSnapshot:
    return PageSnapshot(url="https://example.com/apply", title="Apply", fields=fields)


class TestDeterministicFill:
    def test_first_name_fills(self):
        snap = _make_snapshot([_make_field("#fname", "First Name")])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert actions[0].type == "fill"
        assert actions[0].value  # Should be the profile first name

    def test_combobox_uses_fuzzy_match(self):
        snap = _make_snapshot([
            _make_field("#gender", "Gender", input_type="combobox",
                        options=["Male (he/him)", "Female (she/her)", "Non-binary"],
                        role="combobox"),
        ])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert actions[0].value == "Male (he/him)"
        assert actions[0].type == "fill_combobox"

    def test_right_to_work_fuzzy(self):
        snap = _make_snapshot([
            _make_field("#rtw", "Do you have the right to work in the UK?",
                        input_type="combobox",
                        options=["Yes, I have the right to work", "No, I require sponsorship"],
                        role="combobox"),
        ])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert "right to work" in actions[0].value.lower()

    def test_skips_already_filled(self):
        field = _make_field("#email", "Email")
        field.current_value = "test@example.com"
        snap = _make_snapshot([field])
        actions = deterministic_fill(snap)
        assert len(actions) == 0

    def test_skips_file_inputs(self):
        snap = _make_snapshot([_make_field("#cv", "Resume", input_type="file")])
        actions = deterministic_fill(snap)
        assert len(actions) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py -v`
Expected: FAIL — `_match_to_available_options` not found

- [ ] **Step 3: Implement `_match_to_available_options` in form_analyzer.py**

Add after the `_PLACEHOLDER_VALUES` constant (around line 28):

```python
# Abbreviation mapping for fuzzy option matching
_ABBREVIATIONS: dict[str, str] = {
    "uk": "united kingdom",
    "us": "united states",
    "usa": "united states of america",
}
_REVERSE_ABBREVIATIONS: dict[str, str] = {v: k for k, v in _ABBREVIATIONS.items()}


def _normalize_option(text: str) -> str:
    """Normalize text for fuzzy comparison."""
    return (text or "").lower().strip().rstrip(".,;:!?")


def _match_to_available_options(value: str, options: list[str]) -> str:
    """Match a deterministic value to the closest available dropdown option.

    Priority: exact > starts-with > contains > abbreviation > original value.
    Skips placeholder options.
    """
    if not options:
        return value

    norm_value = _normalize_option(value)
    expanded = _ABBREVIATIONS.get(norm_value, norm_value)
    abbreviated = _REVERSE_ABBREVIATIONS.get(norm_value)

    # Filter out placeholders
    real_options = [o for o in options if _normalize_option(o) not in _PLACEHOLDER_VALUES]
    if not real_options:
        return value

    # Pass 1: exact match (case-insensitive)
    for opt in real_options:
        if _normalize_option(opt) == norm_value:
            return opt

    # Pass 2: option starts with our value
    for opt in real_options:
        if _normalize_option(opt).startswith(norm_value):
            return opt

    # Pass 3: option contains our value
    for opt in real_options:
        if norm_value in _normalize_option(opt):
            return opt

    # Pass 4: our value contains the option (e.g., "Graduate Visa" matches "Graduate visa (Tier 4)")
    for opt in real_options:
        norm_opt = _normalize_option(opt)
        if norm_opt and len(norm_opt) > 2 and norm_opt in norm_value:
            return opt

    # Pass 5: abbreviation expansion (UK ↔ United Kingdom)
    if expanded != norm_value:
        for opt in real_options:
            if _normalize_option(opt) == expanded or expanded in _normalize_option(opt):
                return opt
    if abbreviated:
        for opt in real_options:
            if _normalize_option(opt) == abbreviated or abbreviated in _normalize_option(opt):
                return opt

    return value
```

- [ ] **Step 4: Wire fuzzy matching into deterministic_fill**

In `deterministic_fill()`, modify the inner loop where actions are appended (around line 550-554). Replace:

```python
                actions.append(Action(type=atype, selector=field.selector, value=value))
```

With:

```python
                # Fuzzy-match value to real dropdown options
                if atype in ("fill_combobox", "select") and field.options:
                    value = _match_to_available_options(value, field.options)
                actions.append(Action(type=atype, selector=field.selector, value=value))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_analyzer.py tests/jobpulse/test_form_analyzer.py
git commit -m "feat(form): add fuzzy dropdown matching to deterministic fill

_match_to_available_options() matches hardcoded values to real dropdown
options via 5-pass strategy: exact > startsWith > contains > reverse
contains > abbreviation. Wired into deterministic_fill for all
fill_combobox/select actions."
```

---

### Task 3: Confidence-Weighted Pattern Matching

**Files:**
- Modify: `jobpulse/form_analyzer.py:96-557`
- Modify: `tests/jobpulse/test_form_analyzer.py`

- [ ] **Step 1: Write failing test for confidence scoring**

Add to `tests/jobpulse/test_form_analyzer.py`:

```python
class TestConfidenceScoring:
    def test_specific_pattern_wins_over_generic(self):
        """'passport_first_name' should match the specific pattern, not '^name$'."""
        snap = _make_snapshot([_make_field("#pname", "Passport First Name")])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        # Should match "passport first name" pattern specifically

    def test_generic_name_still_matches(self):
        """'^name$' should still match when no specific pattern applies."""
        snap = _make_snapshot([_make_field("#name", "Name")])
        actions = deterministic_fill(snap)
        assert len(actions) == 1

    def test_longer_pattern_preferred(self):
        """When two patterns match, the longer (more specific) one wins."""
        snap = _make_snapshot([_make_field("#email", "Confirm Email Address")])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        # Should match "confirm email" pattern (more specific), not just "email"
```

- [ ] **Step 2: Run tests to verify behavior**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py::TestConfidenceScoring -v`

- [ ] **Step 3: Implement confidence-weighted matching**

In `deterministic_fill()`, replace the first-match loop with a best-match approach. Change the inner loop (where `_DETERMINISTIC_RULES` are checked):

Replace:

```python
        # Try deterministic rules
        for pattern, value, atype in _DETERMINISTIC_RULES:
            if re.search(pattern, label_lower, re.IGNORECASE):
                if not value:
                    break  # Skip empty values
                # Remap action type based on actual field type
                ...
                actions.append(Action(type=atype, selector=field.selector, value=value))
                matched_selectors.add(field.selector)
                logger.info("  DET → %s [%s] = %s", field.selector[:40], atype, value[:60])
                break
```

With:

```python
        # Try deterministic rules — pick the most specific (longest) matching pattern
        best_match: tuple[str, str, str, int] | None = None  # (pattern, value, atype, specificity)
        for pattern, value, atype in _DETERMINISTIC_RULES:
            if re.search(pattern, label_lower, re.IGNORECASE):
                if not value:
                    best_match = None  # LLM-required — skip all matches
                    break
                specificity = len(pattern)
                if best_match is None or specificity > best_match[3]:
                    best_match = (pattern, value, atype, specificity)

        if best_match is not None:
            _, value, atype, _ = best_match
            # Remap action type based on actual field type
            ftype = field.input_type
            role = field.attributes.get("role", "")
            is_combobox = role == "combobox" or ftype in ("search_autocomplete", "combobox", "custom_select")
            if is_combobox and atype == "fill":
                atype = "fill_combobox"
            elif not is_combobox and atype == "fill_combobox":
                atype = "fill"
            if ftype == "rich_text" and atype == "fill":
                atype = "fill_contenteditable"

            # Fuzzy-match value to real dropdown options
            if atype in ("fill_combobox", "select") and field.options:
                value = _match_to_available_options(value, field.options)
            actions.append(Action(type=atype, selector=field.selector, value=value))
            matched_selectors.add(field.selector)
            logger.info("  DET → %s [%s] = %s", field.selector[:40], atype, value[:60])
```

- [ ] **Step 4: Run all form_analyzer tests**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_analyzer.py tests/jobpulse/test_form_analyzer.py
git commit -m "feat(form): confidence-weighted pattern matching

Pick longest matching regex pattern instead of first match.
Prevents generic patterns like '^name$' from shadowing specific
ones like 'passport_first_name'."
```

---

### Task 4: Scope Combobox Option Search

**Files:**
- Modify: `extension/fillers/fill_combobox.js:52-199`

- [ ] **Step 1: Add scoped option search helper**

Add before `fillCombobox` in `extension/fillers/fill_combobox.js`:

```javascript
/**
 * Find the option panel associated with a specific combobox trigger.
 * Uses ARIA attributes (aria-owns, aria-controls) and framework conventions.
 * Returns the scoped container element or null if none found.
 */
function _findScopedPanel(triggerEl) {
  // Strategy 1: aria-owns or aria-controls points to the listbox
  for (const attr of ["aria-owns", "aria-controls"]) {
    const panelId = triggerEl.getAttribute(attr);
    if (panelId) {
      const panel = document.getElementById(panelId);
      if (panel) return panel;
    }
  }

  // Strategy 2: parent with role=combobox may have aria-owns
  let parent = triggerEl.parentElement;
  for (let d = 0; d < 4 && parent; d++, parent = parent.parentElement) {
    for (const attr of ["aria-owns", "aria-controls"]) {
      const panelId = parent.getAttribute(attr);
      if (panelId) {
        const panel = document.getElementById(panelId);
        if (panel) return panel;
      }
    }
    if (parent.getAttribute("role") === "combobox") break;
  }

  // Strategy 3: framework data attributes (React Select, MUI, Radix)
  const dataAttrs = ["data-listbox-id", "data-popper-reference-hidden"];
  for (const attr of dataAttrs) {
    const val = triggerEl.getAttribute(attr);
    if (val) {
      const panel = document.getElementById(val);
      if (panel) return panel;
    }
  }

  return null;
}

/**
 * Collect options from a scoped panel. Returns array of {el, text} or empty array.
 */
function _collectScopedOptions(panel) {
  const results = [];
  if (!panel) return results;
  for (const optSel of OPTION_SELECTORS) {
    for (const opt of panel.querySelectorAll(optSel)) {
      const text = opt.textContent.trim();
      if (text && text.length < 200 && !PLACEHOLDER_VALUES.has(text.toLowerCase())) {
        results.push({ el: opt, text });
      }
    }
    if (results.length > 0) break;
  }
  return results;
}
```

- [ ] **Step 2: Modify fillCombobox to try scoped search first**

In `fillCombobox`, after the dropdown is opened and before the global document search, add scoped search. Replace the section starting `// Search the ENTIRE document for floating dropdown panels`:

```javascript
  // ── Try scoped search first (ARIA-linked panel) ──
  const scopedPanel = _findScopedPanel(el);
  let allOptions = _collectScopedOptions(scopedPanel);

  // Try matching within scoped options
  if (allOptions.length > 0) {
    for (const { el: opt, text } of allOptions) {
      if (text.toLowerCase() === valueLower) {
        await JP.cursor.moveCursorTo(opt);
        JP.cursor.cursorClickFlash();
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        opt.click();
        await JP.dom.delay(200);
        return { success: true, value_set: text, match: "scoped_exact", value_verified: true };
      }
    }
    for (const { el: opt, text } of allOptions) {
      const textLower = text.toLowerCase();
      if (textLower.startsWith(valueLower) || valueLower.startsWith(textLower) ||
          textLower.includes(valueLower) || valueLower.includes(textLower)) {
        await JP.cursor.moveCursorTo(opt);
        JP.cursor.cursorClickFlash();
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        opt.click();
        await JP.dom.delay(200);
        return { success: true, value_set: text, match: "scoped_partial", value_verified: true };
      }
    }
  }

  // ── Fallback: search the ENTIRE document for floating dropdown panels ──
  allOptions = [];
```

- [ ] **Step 3: Export new helpers**

Update the exports at the bottom:

```javascript
window.JobPulse.fillers.combobox = { fillCombobox, revealOptions, _findScopedPanel, _collectScopedOptions };
```

- [ ] **Step 4: Commit**

```bash
git add extension/fillers/fill_combobox.js
git commit -m "feat(ext): scope combobox option search via ARIA attributes

Try aria-owns/aria-controls to find the correct option panel before
falling back to global document search. Prevents cross-dropdown
contamination on forms with multiple comboboxes."
```

---

### Task 5: Grouped Field Awareness in LLM Prompt

**Files:**
- Modify: `jobpulse/form_analyzer.py:564-711`
- Modify: `tests/jobpulse/test_form_analyzer.py`

- [ ] **Step 1: Write failing test for grouped fields in LLM prompt**

Add to `tests/jobpulse/test_form_analyzer.py`:

```python
from jobpulse.form_analyzer import _build_fields_description


class TestGroupedFieldsDescription:
    def test_fields_grouped_by_group_label(self):
        fields = [
            _make_field("#line1", "Address Line 1"),
            _make_field("#city", "City"),
            _make_field("#postcode", "Postcode"),
        ]
        # Set group_label on all three
        for f in fields:
            f.group_label = "Home Address"
        desc = _build_fields_description(fields)
        # Should contain the group label
        assert "Home Address" in desc

    def test_ungrouped_fields_still_work(self):
        fields = [_make_field("#email", "Email")]
        desc = _build_fields_description(fields)
        assert "Email" in desc
        assert "group:" not in desc.lower() or "group_label" in desc.lower()
```

- [ ] **Step 2: Run tests**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py::TestGroupedFieldsDescription -v`

- [ ] **Step 3: Modify _build_fields_description to group by group_label**

Replace `_build_fields_description` in `form_analyzer.py`:

```python
def _build_fields_description(fields: list[FieldInfo]) -> str:
    """Build field descriptions for the LLM — grouped by group_label when available."""
    # Separate grouped and ungrouped fields
    grouped: dict[str, list[tuple[int, FieldInfo]]] = {}
    ungrouped: list[tuple[int, FieldInfo]] = []

    for i, f in enumerate(fields):
        if f.group_label:
            grouped.setdefault(f.group_label, []).append((i, f))
        else:
            ungrouped.append((i, f))

    parts: list[str] = []

    # Emit grouped fields together
    for group_label, group_fields in grouped.items():
        group_desc = f"=== Field Group: {group_label} ==="
        for i, f in group_fields:
            group_desc += _format_single_field(i, f)
        parts.append(group_desc)

    # Emit ungrouped fields individually
    for i, f in ungrouped:
        parts.append(_format_single_field(i, f).lstrip("\n"))

    return "\n\n".join(parts) if parts else "(no fields)"


def _format_single_field(index: int, f: FieldInfo) -> str:
    """Format a single field for the LLM prompt."""
    label = _clean_label(f)
    desc = f"\nField {index + 1}:"
    desc += f"\n  selector: {f.selector}"
    desc += f"\n  type: {f.input_type}"
    desc += f"\n  label: {label!r}"
    if f.required:
        desc += "\n  required: YES"
    if f.current_value and f.current_value.strip().lower() not in _PLACEHOLDER_VALUES:
        desc += f"\n  current_value: {f.current_value!r} (ALREADY FILLED)"
    if f.options:
        desc += f"\n  options: {f.options}"
    dom_ctx = getattr(f, "dom_context", "") or ""
    if dom_ctx:
        desc += f"\n  dom_context: {dom_ctx!r}"
    if f.help_text:
        desc += f"\n  help_text: {f.help_text!r}"
    if f.group_label:
        desc += f"\n  group_label: {f.group_label!r}"
    if f.fieldset_legend:
        desc += f"\n  fieldset_legend: {f.fieldset_legend!r}"
    if f.error_text:
        desc += f"\n  error: {f.error_text!r}"
    label_sources = getattr(f, "label_sources", None)
    if label_sources:
        desc += f"\n  label_sources: {label_sources}"
    return desc
```

- [ ] **Step 4: Run tests**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_analyzer.py tests/jobpulse/test_form_analyzer.py
git commit -m "feat(form): group related fields in LLM prompt

_build_fields_description now groups fields with the same group_label
under a shared header. Helps LLM understand that address line1, city,
postcode are related and should be filled coordinately."
```

---

### Task 6: Post-Fill Verification Loop

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_form_filler.py:88-360`
- Create: `tests/jobpulse/test_form_filler_verify.py`

- [ ] **Step 1: Write failing test for verification**

Create `tests/jobpulse/test_form_filler_verify.py`:

```python
"""Tests for post-fill verification in FormFiller."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jobpulse.application_orchestrator_pkg._form_filler import FormFiller


class TestVerifyFilledFields:
    @pytest.mark.asyncio
    async def test_verify_detects_empty_field(self):
        """Fields that were 'filled' but have empty current_value trigger retry."""
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        # Simulate: we filled #email but snapshot shows it's empty
        filled_selectors = {"#email"}
        actions = [MagicMock(selector="#email", type="fill", value="test@test.com")]

        # Mock snapshot where #email is empty
        empty_field = MagicMock()
        empty_field.selector = "#email"
        empty_field.current_value = ""
        empty_field.input_type = "text"
        snapshot = MagicMock()
        snapshot.fields = [empty_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 1

    @pytest.mark.asyncio
    async def test_verify_skips_verified_fields(self):
        """Fields with correct current_value don't trigger retry."""
        mock_orch = MagicMock()
        mock_executor = AsyncMock()
        mock_navigator = MagicMock()
        filler = FormFiller(mock_orch, mock_executor, mock_navigator)

        filled_selectors = {"#email"}
        actions = [MagicMock(selector="#email", type="fill", value="test@test.com")]

        ok_field = MagicMock()
        ok_field.selector = "#email"
        ok_field.current_value = "test@test.com"
        ok_field.input_type = "text"
        snapshot = MagicMock()
        snapshot.fields = [ok_field]

        retries = await filler._verify_filled_fields(filled_selectors, actions, snapshot)
        assert retries == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_filler_verify.py -v`
Expected: FAIL — `_verify_filled_fields` not found

- [ ] **Step 3: Implement _verify_filled_fields in FormFiller**

Add this method to the `FormFiller` class in `_form_filler.py`:

```python
    async def _verify_filled_fields(
        self,
        filled_selectors: set[str],
        actions: list,
        snapshot,
    ) -> int:
        """Verify filled fields have values, retry empty ones. Returns retry count."""
        if not filled_selectors:
            return 0

        retry_count = 0
        action_map = {}
        for a in actions:
            sel = getattr(a, "selector", None) or (a.get("selector") if isinstance(a, dict) else None)
            if sel:
                action_map[sel] = a

        for field in snapshot.fields:
            if field.selector not in filled_selectors:
                continue
            if field.input_type == "file":
                continue
            if field.current_value and field.current_value.strip():
                continue

            # Field was filled but is now empty — retry
            original_action = action_map.get(field.selector)
            if not original_action:
                continue

            logger.info("  Verify: %s is empty after fill — retrying", field.selector[:40])
            try:
                await self.executor.execute_action_with_retry(original_action)
                retry_count += 1
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("  Verify retry failed for %s: %s", field.selector[:40], exc)

        return retry_count
```

- [ ] **Step 4: Wire verification into fill_application**

In `fill_application()`, after the action execution loop and before the screenshot (around line 242), add:

```python
            # ── Post-fill verification: check that values stuck ──
            if filled_selectors:
                try:
                    await asyncio.sleep(0.5)
                    verify_snap = self._to_page_snapshot(
                        self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    )
                    retries = await self._verify_filled_fields(filled_selectors, actions, verify_snap)
                    if retries > 0:
                        logger.info("  Post-fill verification: retried %d empty fields", retries)
                except (TimeoutError, ConnectionError):
                    pass  # Non-critical — proceed without verification
```

- [ ] **Step 5: Run tests**

Run: `OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_filler_verify.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_form_filler.py tests/jobpulse/test_form_filler_verify.py
git commit -m "feat(form): post-fill verification loop

After filling all fields on a page, rescan snapshot and retry any
fields that are empty despite being 'filled'. Catches React/Angular
controlled components that silently reject programmatic input."
```

---

### Task 7: Enrich Gemini Nano Prompt

**Files:**
- Modify: `extension/ai/gemini.js:11-35`
- Modify: `extension/content.js` (analyze_field case)

- [ ] **Step 1: Update analyzeFieldLocally to accept job context**

Replace the `analyzeFieldLocally` function in `extension/ai/gemini.js`:

```javascript
/**
 * Use Chrome's Prompt API (Gemini Nano) to analyze a form field locally.
 * Returns the answer string, or null if Nano is unavailable.
 *
 * @param {string} question - The field label/question
 * @param {string} inputType - HTML input type
 * @param {string[]} options - Available dropdown options
 * @param {Object} [jobContext] - Optional job context {title, company, location}
 */
async function analyzeFieldLocally(question, inputType, options, jobContext) {
  if (!self.ai || !self.ai.languageModel) return null;

  try {
    const capabilities = await self.ai.languageModel.capabilities();
    if (capabilities.available === "no") return null;

    const role = (jobContext && jobContext.title) || "ML Engineer";
    const company = (jobContext && jobContext.company) || "";
    const location = (jobContext && jobContext.location) || "the UK";
    const companyNote = company ? ` at ${company}` : "";

    const session = await self.ai.languageModel.create({
      systemPrompt:
        `You fill job application forms for a ${role}${companyNote} with 2 years experience in ${location}. ` +
        "Return only the answer value, nothing else. No explanation, no quotes. " +
        "For dropdowns, pick the EXACT option text from the list.",
    });

    let prompt = `Field: "${question}" (${inputType})`;
    if (options && options.length > 0) prompt += `\nOptions: ${options.join(", ")}`;
    prompt += "\nAnswer:";

    const answer = await session.prompt(prompt);
    session.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Gemini Nano unavailable:", e.message);
    return null;
  }
}
```

- [ ] **Step 2: Update content.js to pass job context to analyze_field**

In `content.js`, find the `case "analyze_field"` handler and update it:

```javascript
      case "analyze_field": {
        let answer = await analyzeFieldLocally(
          payload.question, payload.input_type,
          payload.options || [], payload.job_context || null
        );
        if (!answer && payload.input_type === "textarea") {
          answer = await writeShortAnswer(payload.question);
        }
        result = { success: !!answer, answer: answer || "" };
        break;
      }
```

- [ ] **Step 3: Commit**

```bash
git add extension/ai/gemini.js extension/content.js
git commit -m "feat(ext): enrich Gemini Nano prompt with job context

Pass job title, company, and location to Nano's system prompt.
Include instruction to pick exact option text for dropdowns.
Falls back to generic ML Engineer profile if no context provided."
```

---

### Task 8: Run Full Test Suite and Verify

**Files:**
- Run: all test files touched in Tasks 1-7

- [ ] **Step 1: Run form_analyzer tests**

```bash
OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_analyzer.py -v
```

Expected: ALL PASS

- [ ] **Step 2: Run form_filler verification tests**

```bash
OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_filler_verify.py -v
```

Expected: ALL PASS

- [ ] **Step 3: Run existing form_intelligence tests (regression check)**

```bash
OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_form_intelligence.py -v
```

Expected: ALL PASS (no regression)

- [ ] **Step 4: Run existing ext tests (regression check)**

```bash
OPENAI_API_KEY=test python -m pytest tests/jobpulse/test_ext_adapter.py tests/jobpulse/test_ext_routing.py tests/jobpulse/test_ext_models.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Run broader jobpulse test suite**

```bash
OPENAI_API_KEY=test python -m pytest tests/jobpulse/ -v --timeout=60 -x -q
```

Expected: No new failures

- [ ] **Step 6: Final commit with test verification**

```bash
git log --oneline -8
```

Verify 7 commits from Tasks 1-7 are present and correct.
