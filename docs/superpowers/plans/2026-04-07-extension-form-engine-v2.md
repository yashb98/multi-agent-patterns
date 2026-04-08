# Extension Form Engine v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make content.js a complete, self-sufficient form engine that surpasses Playwright — form group pairing, modal scoping, 12 specialized fill handlers, post-fill verification, conditional re-scan, fuzzy matching.

**Architecture:** 6-stage pipeline in content.js (SCAN → CLASSIFY → CONTEXTUALIZE → FILL → VERIFY → ADAPT). Python ext_bridge.py gains matching async methods. State machines route to new action types. All changes are additive — existing handlers stay intact.

**Tech Stack:** Vanilla JS (content.js), Python asyncio (ext_bridge.py), Pydantic (ext_models.py), pytest + websockets (tests)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `extension/content.js` | Modify | Add 12 new action handlers, upgrade deepScan with form group pairing, add fuzzy matching, modal scoping, post-fill verification |
| `extension/protocol.js` | Modify | Add new message type constants |
| `jobpulse/ext_bridge.py` | Modify | Add Python-side async methods for all new actions |
| `jobpulse/ext_models.py` | Modify | Add new action types to ExtCommand and Action Literals, add FormGroup model |
| `jobpulse/state_machines/__init__.py` | Modify | Fix radio vs checkbox routing, use new action types, add re-scan support |
| `jobpulse/application_orchestrator.py` | Modify | Fix stale snapshot bug (line 434), use modal-scoped scanning, add consent auto-check |
| `tests/jobpulse/test_ext_bridge.py` | Modify | Add tests for all new bridge methods |
| `tests/extension/test_content_handlers.py` | Create | JS handler unit tests via Python harness |

---

### Task 1: Add FormGroup model and new action types to ext_models.py

**Files:**
- Modify: `jobpulse/ext_models.py:43-68` (FieldInfo), `106-124` (ExtCommand), `143-150` (Action)

- [ ] **Step 1: Add FormGroup model and update FieldInfo**

In `jobpulse/ext_models.py`, add a `FormGroup` model after `ButtonInfo` (after line 80) and add `group_context` fields to `FieldInfo`:

```python
# Add to FieldInfo class (after line 71, before closing of class):
    group_label: str = ""
    group_selector: str = ""
    parent_text: str = ""
    fieldset_legend: str = ""
    help_text: str = ""
    error_text: str = ""
    aria_describedby: str = ""


class FormGroup(BaseModel):
    """A form group: label + input(s) paired together."""

    group_selector: str
    question: str  # The label/legend text
    fields: list[FieldInfo] = []
    is_required: bool = False
    is_answered: bool = False
    fieldset_legend: str = ""
    help_text: str = ""
```

- [ ] **Step 2: Update ExtCommand action Literal**

Replace the `action` Literal in `ExtCommand` (line 110-123):

```python
    action: Literal[
        "navigate",
        "fill",
        "click",
        "upload",
        "screenshot",
        "select",
        "check",
        "scroll",
        "wait",
        "close_tab",
        "analyze_field",
        "get_snapshot",
        # v2 actions
        "fill_radio_group",
        "fill_custom_select",
        "fill_autocomplete",
        "fill_tag_input",
        "fill_date",
        "scroll_to",
        "wait_for_selector",
        "get_field_context",
        "scan_form_groups",
        "check_consent_boxes",
        "force_click",
        "rescan_after_fill",
    ]
```

- [ ] **Step 3: Update Action type Literal**

Replace the `type` Literal in `Action` (line 146):

```python
    type: Literal[
        "fill", "upload", "click", "select", "check", "wait",
        # v2 action types
        "fill_radio_group", "fill_custom_select", "fill_autocomplete",
        "fill_tag_input", "fill_date", "scroll_to", "force_click",
        "check_consent_boxes",
    ]
```

- [ ] **Step 4: Add FormGroup to PageSnapshot**

Add `form_groups` field to `PageSnapshot` (after line 97):

```python
    form_groups: list[FormGroup] = []
    progress: tuple[int, int] | None = None  # (current_step, total_steps)
    modal_detected: bool = False
```

Note: `FormGroup` import needs to be added, but since it's in the same file, just ensure `FormGroup` is defined before `PageSnapshot`.

- [ ] **Step 5: Run existing tests to verify no breakage**

Run: `python -m pytest tests/jobpulse/test_ext_models.py -v`
Expected: All PASS (new fields have defaults, so existing data still parses)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/ext_models.py
git commit -m "feat(ext): add FormGroup model, new action types, and field context to ext_models"
```

---

### Task 2: Update protocol.js with new message types

**Files:**
- Modify: `extension/protocol.js:4-34`

- [ ] **Step 1: Add new command constants**

Add after `CMD_WAIT` (line 14) in the `MSG` object:

```javascript
  // v2 form engine commands
  CMD_FILL_RADIO_GROUP: "fill_radio_group",
  CMD_FILL_CUSTOM_SELECT: "fill_custom_select",
  CMD_FILL_AUTOCOMPLETE: "fill_autocomplete",
  CMD_FILL_TAG_INPUT: "fill_tag_input",
  CMD_FILL_DATE: "fill_date",
  CMD_SCROLL_TO: "scroll_to",
  CMD_WAIT_FOR_SELECTOR: "wait_for_selector",
  CMD_GET_FIELD_CONTEXT: "get_field_context",
  CMD_SCAN_FORM_GROUPS: "scan_form_groups",
  CMD_CHECK_CONSENT_BOXES: "check_consent_boxes",
  CMD_FORCE_CLICK: "force_click",
  CMD_RESCAN_AFTER_FILL: "rescan_after_fill",
```

- [ ] **Step 2: Commit**

```bash
git add extension/protocol.js
git commit -m "feat(ext): add v2 form engine message types to protocol.js"
```

---

### Task 3: Add fuzzy matching utilities to content.js

**Files:**
- Modify: `extension/content.js` (add after Utilities section, before Deep Page Scanner section, ~line 86)

- [ ] **Step 1: Add fuzzy matching functions**

Insert after the `resolveSelector` function (after line 85):

```javascript
// ═══════════════════════════════════════════════════════════════
// Fuzzy Matching — mirrors Python select_filler._fuzzy_match_option
// ═══════════════════════════════════════════════════════════════

const ABBREVIATIONS = {
  "uk": "united kingdom",
  "us": "united states",
  "usa": "united states of america",
  "nyc": "new york city",
  "sf": "san francisco",
  "la": "los angeles",
  "phd": "doctor of philosophy",
  "msc": "master of science",
  "bsc": "bachelor of science",
};

function normalizeText(text) {
  return (text || "").toLowerCase().trim().replace(/[.,;:!?]+$/, "");
}

/**
 * Find the best matching option for a value.
 * Priority: exact → abbreviation → startswith → contains → null.
 * Mirrors Python's _fuzzy_match_option() from select_filler.py.
 */
function fuzzyMatchOption(value, options) {
  const norm = normalizeText(value);
  const expanded = ABBREVIATIONS[norm] || norm;

  // Exact match
  for (const opt of options) {
    if (normalizeText(opt) === expanded) return opt;
  }
  // Starts with
  for (const opt of options) {
    if (normalizeText(opt).startsWith(expanded)) return opt;
  }
  // Contains
  for (const opt of options) {
    if (normalizeText(opt).includes(expanded)) return opt;
  }
  // Reverse contains (value contains option)
  for (const opt of options) {
    if (expanded.includes(normalizeText(opt)) && normalizeText(opt).length > 2) return opt;
  }
  return null;
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add fuzzy matching utilities to content.js"
```

---

### Task 4: Upgrade deepScan with form group pairing and modal scoping

**Files:**
- Modify: `extension/content.js` — replace/extend `extractFieldInfo` and `deepScan`, add `scanFormGroups`

- [ ] **Step 1: Add parent context extraction to extractFieldInfo**

Add after the `iframe_index` field in the return object of `extractFieldInfo` (around line 153), before the closing `};`:

```javascript
    // v2: parent context for form intelligence
    group_label: (() => {
      const group = el.closest("fieldset, .form-group, .field, [data-test-form-element], .jobs-easy-apply-form-section__grouping, .fb-dash-form-element");
      if (!group) return "";
      const legend = group.querySelector("label, legend, .field-label, .fb-form-element-label, span.t-14");
      return legend ? legend.textContent.trim().substring(0, 200) : "";
    })(),
    group_selector: (() => {
      const group = el.closest("fieldset, .form-group, .field, [data-test-form-element], .jobs-easy-apply-form-section__grouping, .fb-dash-form-element");
      if (!group) return "";
      if (group.id) return `#${group.id}`;
      const tag = group.tagName.toLowerCase();
      const cls = group.className && typeof group.className === "string"
        ? group.className.split(/\s+/).filter(c => c.length > 3)[0]
        : "";
      return cls ? `${tag}.${cls}` : tag;
    })(),
    parent_text: (() => {
      const p = el.parentElement;
      return p ? p.textContent.trim().substring(0, 300) : "";
    })(),
    fieldset_legend: (() => {
      const fs = el.closest("fieldset");
      if (!fs) return "";
      const leg = fs.querySelector("legend");
      return leg ? leg.textContent.trim() : "";
    })(),
    help_text: (() => {
      const describedBy = el.getAttribute("aria-describedby");
      if (describedBy) {
        const desc = document.getElementById(describedBy);
        if (desc) return desc.textContent.trim().substring(0, 200);
      }
      // Check sibling hint/help text
      const next = el.nextElementSibling;
      if (next && /help|hint|description|info/.test(next.className || "")) {
        return next.textContent.trim().substring(0, 200);
      }
      return "";
    })(),
    error_text: (() => {
      const errId = el.getAttribute("aria-errormessage");
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) return errEl.textContent.trim();
      }
      const parent = el.closest(".form-group, .field-wrapper, .form-field, [data-test-form-element]");
      if (parent) {
        const errEl = parent.querySelector(".error, .invalid-feedback, [role='alert'], .field-error");
        if (errEl) return errEl.textContent.trim();
      }
      return "";
    })(),
    aria_describedby: el.getAttribute("aria-describedby") || "",
```

- [ ] **Step 2: Add scanFormGroups function**

Add after the `deepScan` function (after line 200):

```javascript
/**
 * Scan for form groups — pairs labels with their input elements.
 * Mirrors Playwright's answer_screening_questions() group-based scanning.
 *
 * Optionally scoped to a container (modal, fieldset, etc.)
 * Returns [{group_selector, question, fields, is_required, is_answered, help_text}]
 */
function scanFormGroups(rootSelector) {
  const root = rootSelector ? resolveSelector(rootSelector) : document;
  if (!root) return [];

  const groupSelectors =
    "fieldset, .form-group, .field, [data-test-form-element], " +
    ".jobs-easy-apply-form-section__grouping, .fb-dash-form-element, " +
    ".application-question, .field-wrapper";

  const groups = [];
  const seen = new Set(); // Avoid duplicating fields across groups

  for (const group of root.querySelectorAll(groupSelectors)) {
    // Find question/label text
    const labelEl = group.querySelector(
      "label, legend, .field-label, .application-label, " +
      ".fb-form-element-label, span.t-14, span.t-bold"
    );
    const question = labelEl ? labelEl.textContent.trim().substring(0, 300) : "";
    if (!question || question.length < 2) continue;

    // Find input elements within this group
    const inputSelector =
      "input:not([type='hidden']):not([type='submit']), select, textarea, " +
      "[contenteditable='true'], [role='listbox'], [role='combobox'], " +
      "[role='radiogroup'], [role='switch'], [role='textbox']";
    const inputs = group.querySelectorAll(inputSelector);
    if (inputs.length === 0) continue;

    const fields = [];
    let isAnswered = true;

    for (const inp of inputs) {
      if (seen.has(inp)) continue;
      seen.add(inp);
      const fieldInfo = extractFieldInfo(inp, null);
      fields.push(fieldInfo);

      // Check if this field has a value
      const val = inp.value || inp.textContent || "";
      const isRadioChecked = inp.type === "radio" && inp.checked;
      const isCheckboxChecked = inp.type === "checkbox" && inp.checked;
      if (!val.trim() && !isRadioChecked && !isCheckboxChecked) {
        isAnswered = false;
      }
    }

    // Build group selector
    let grpSelector = "";
    if (group.id) grpSelector = `#${group.id}`;
    else {
      const tag = group.tagName.toLowerCase();
      const cls = (group.className && typeof group.className === "string")
        ? group.className.split(/\s+/).filter(c => c.length > 3)[0] : "";
      grpSelector = cls ? `${tag}.${cls}` : tag;
    }

    // Help text
    const helpEl = group.querySelector(".help-text, .field-hint, .description, [class*='helper']");
    const helpText = helpEl ? helpEl.textContent.trim().substring(0, 200) : "";

    // Required check
    const isRequired = group.querySelector("[required], [aria-required='true']") !== null
      || /\*|required/i.test(question);

    groups.push({
      group_selector: grpSelector,
      question,
      fields,
      is_required: isRequired,
      is_answered: isAnswered,
      fieldset_legend: group.closest("fieldset")?.querySelector("legend")?.textContent?.trim() || "",
      help_text: helpText,
    });
  }

  return groups;
}
```

- [ ] **Step 3: Add modal detection to buildSnapshot**

In `buildSnapshot()` (around line 240), add modal detection and progress parsing before the return statement:

```javascript
  // Detect modal (LinkedIn Easy Apply, generic dialogs)
  const modal = document.querySelector(
    "[role='dialog'], .artdeco-modal, .jobs-easy-apply-modal, " +
    "[aria-modal='true'], .modal--open, .modal-dialog"
  );
  const modalDetected = modal !== null;

  // Parse progress indicators ("Step 2 of 5", "Page 1/3")
  let progress = null;
  const pageText = document.body?.innerText || "";
  const progressMatch = pageText.match(/(?:step|page)\s+(\d+)\s+(?:of|\/)\s+(\d+)/i)
    || pageText.match(/(\d+)\s+(?:of|\/)\s+(\d+)/);
  if (progressMatch) {
    const current = parseInt(progressMatch[1]);
    const total = parseInt(progressMatch[2]);
    if (current >= 1 && current <= total && total <= 20) {
      progress = [current, total];
    }
  }

  // Scan form groups (scoped to modal if present)
  const formGroups = scanFormGroups(modal ? (
    modal.id ? `#${modal.id}` : "[role='dialog']"
  ) : null);
```

Then update the return object to include the new fields:

```javascript
  return {
    url: window.location.href,
    title: document.title,
    fields,
    buttons,
    verification_wall: detectVerificationWall(),
    page_text_preview: (document.body?.innerText || "").substring(0, 500),
    has_file_inputs: document.querySelector("input[type='file']") !== null,
    iframe_count: document.querySelectorAll("iframe").length,
    page_stable:
      !document.querySelector('[aria-busy="true"]') &&
      !document.querySelector('.loading, .spinner, [class*="loading"]'),
    timestamp: Date.now(),
    // v2 additions
    form_groups: formGroups,
    progress,
    modal_detected: modalDetected,
  };
```

- [ ] **Step 4: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add form group pairing, modal detection, progress parsing to deepScan"
```

---

### Task 5: Add radio group fill handler to content.js

**Files:**
- Modify: `extension/content.js` — add `fillRadioGroup` function after `checkBox` (~line 404)

- [ ] **Step 1: Add fillRadioGroup function**

```javascript
/**
 * Fill a radio button group by matching option labels to the desired value.
 * Clicks the <label> element (not the radio directly) to avoid interception.
 * Mirrors Python radio_filler.fill_radio_group().
 */
async function fillRadioGroup(groupSelector, value) {
  // Find all radios in the group (by name attribute or container)
  let radios;
  const container = resolveSelector(groupSelector);
  if (container && container.tagName.toLowerCase() !== "input") {
    // Container-based: find radios within
    radios = container.querySelectorAll("input[type='radio']");
  } else {
    // Name-based: find all radios with same name
    const nameEl = resolveSelector(groupSelector);
    if (nameEl) {
      const name = nameEl.getAttribute("name");
      radios = name
        ? document.querySelectorAll(`input[type='radio'][name='${name}']`)
        : [nameEl];
    } else {
      return { success: false, error: "Radio group not found: " + groupSelector };
    }
  }

  if (!radios || radios.length === 0) {
    return { success: false, error: "No radio buttons found in: " + groupSelector };
  }

  // Build label map: [{text, radio, labelEl}]
  const labelMap = [];
  for (const radio of radios) {
    let labelText = "";
    let labelEl = null;

    // Try <label for="id">
    const radioId = radio.id;
    if (radioId) {
      labelEl = document.querySelector(`label[for='${radioId}']`);
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    // Try wrapping <label>
    if (!labelText) {
      labelEl = radio.closest("label");
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    // Try aria-label
    if (!labelText) {
      labelText = radio.getAttribute("aria-label") || "";
    }

    // Try next sibling text
    if (!labelText && radio.nextSibling) {
      labelText = (radio.nextSibling.textContent || "").trim();
    }

    // Try parent text (last resort)
    if (!labelText && radio.parentElement) {
      labelText = radio.parentElement.textContent.trim();
    }

    if (labelText) {
      labelMap.push({ text: labelText, radio, labelEl });
    }
  }

  if (labelMap.length === 0) {
    return { success: false, error: "No labels found for radio buttons" };
  }

  // Fuzzy match
  const labels = labelMap.map(l => l.text);
  const match = fuzzyMatchOption(value, labels);

  if (!match) {
    return {
      success: false,
      error: `No matching radio for '${value}' in [${labels.slice(0, 5).join(", ")}]`,
    };
  }

  // Click the matching option — prefer clicking label over radio (avoids interception)
  const matched = labelMap.find(l => l.text === match);
  if (matched) {
    const target = matched.labelEl || matched.radio;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    await delay(behaviorProfile.reading_pause * 300 * (0.5 + Math.random()));
    target.click();
    // Also dispatch change event on the radio
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match };
  }

  return { success: false, error: "Match found but click failed" };
}
```

- [ ] **Step 2: Wire into message handler**

Add a new case in the `switch (action)` block in the message handler (around line 622):

```javascript
      case "fill_radio_group":
        result = await fillRadioGroup(payload.selector, payload.value);
        break;
```

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add fillRadioGroup handler with label clicking and fuzzy match"
```

---

### Task 6: Add custom select (React dropdown) handler to content.js

**Files:**
- Modify: `extension/content.js` — add `fillCustomSelect` function

- [ ] **Step 1: Add fillCustomSelect function**

Add after `fillRadioGroup`:

```javascript
/**
 * Fill a custom React/Angular dropdown widget.
 * Flow: click trigger → wait for options → type to filter → fuzzy match → click option.
 * Mirrors Python select_filler.fill_custom_select().
 */
async function fillCustomSelect(triggerSelector, value) {
  const trigger = resolveSelector(triggerSelector);
  if (!trigger) return { success: false, error: "Trigger not found: " + triggerSelector };

  // Click to open the dropdown
  trigger.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  trigger.click();
  await delay(600); // Wait for dropdown animation

  // Try typing to filter if there's a search input inside
  const searchInput = trigger.querySelector("input")
    || document.querySelector("[role='combobox'] input:focus")
    || document.querySelector(".select__input input, .search-typeahead input");

  if (searchInput) {
    searchInput.value = "";
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    // Type partial value to filter
    const filterText = value.substring(0, Math.min(value.length, 5));
    for (const char of filterText) {
      searchInput.value += char;
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      await delay(80 + Math.random() * 40);
    }
    await delay(800); // Wait for debounced filter
  }

  // Get visible options
  const optionSelectors = [
    "[role='option']",
    "[role='listbox'] li",
    ".select__option",
    ".basic-typeahead__selectable",
    "li.search-typeahead-v2__hit",
    ".dropdown-item",
    ".artdeco-dropdown__item",
    "ul[role='listbox'] > li",
  ];

  let optionEls = [];
  for (const sel of optionSelectors) {
    optionEls = document.querySelectorAll(sel);
    if (optionEls.length > 0) break;
  }

  if (optionEls.length === 0) {
    // Press Escape to close and fail
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return { success: false, error: "No options visible after opening dropdown" };
  }

  // Build option texts
  const options = [];
  for (const opt of optionEls) {
    const text = opt.textContent.trim();
    if (text) options.push({ text, el: opt });
  }

  // Fuzzy match
  const match = fuzzyMatchOption(value, options.map(o => o.text));
  if (!match) {
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return {
      success: false,
      error: `No match for '${value}' in [${options.slice(0, 5).map(o => o.text).join(", ")}]`,
    };
  }

  // Click the matching option
  const matched = options.find(o => o.text === match);
  if (matched) {
    matched.el.scrollIntoView({ block: "nearest" });
    await delay(200);
    matched.el.click();
    return { success: true, value_set: match };
  }

  return { success: false, error: "Match found but click failed" };
}
```

- [ ] **Step 2: Wire into message handler**

```javascript
      case "fill_custom_select":
        result = await fillCustomSelect(payload.selector, payload.value);
        break;
```

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add fillCustomSelect handler for React/Angular dropdowns"
```

---

### Task 7: Add autocomplete/typeahead handler to content.js

**Files:**
- Modify: `extension/content.js`

- [ ] **Step 1: Add fillAutocomplete function**

```javascript
/**
 * Fill a search/autocomplete field.
 * Types partial text → waits for suggestion dropdown → clicks matching suggestion.
 * Falls back to typing full value if no suggestion matches.
 * Handles LinkedIn location typeahead, school field, company name, etc.
 */
async function fillAutocomplete(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing value
  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await delay(200);

  // Type first 3-5 chars to trigger autocomplete
  const typeText = value.substring(0, Math.min(value.length, 5));
  for (const char of typeText) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    await delay(behaviorProfile.avg_typing_speed * (1 + (Math.random() - 0.5) * 0.3));
  }

  // Wait for suggestions to appear (1.5s — LinkedIn typeahead debounces at ~300ms)
  await delay(1500);

  // Look for suggestion dropdown
  const suggestionSelectors = [
    "[role='option']",
    "[role='listbox'] li",
    ".basic-typeahead__selectable",
    "li.search-typeahead-v2__hit",
    ".autocomplete-result",
    ".pac-item",           // Google Places
    ".suggestion-item",
    "ul.suggestions li",
  ];

  for (const sugSel of suggestionSelectors) {
    const suggestions = document.querySelectorAll(sugSel);
    if (suggestions.length === 0) continue;

    // Try to find a matching suggestion
    for (const sug of suggestions) {
      const sugText = sug.textContent.trim();
      if (sugText && value.toLowerCase().includes(sugText.toLowerCase().substring(0, 5))
          || sugText.toLowerCase().includes(value.toLowerCase().substring(0, 5))) {
        sug.click();
        await delay(300);
        return { success: true, value_set: sugText };
      }
    }

    // No exact match — click first suggestion as best guess
    const firstSug = suggestions[0];
    if (firstSug) {
      const firstText = firstSug.textContent.trim();
      firstSug.click();
      await delay(300);
      return { success: true, value_set: firstText, used_first_suggestion: true };
    }
  }

  // No suggestions appeared — type the full value and press Escape/Tab
  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await delay(200);
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: value, no_suggestions: true };
}
```

- [ ] **Step 2: Wire into message handler**

```javascript
      case "fill_autocomplete":
        result = await fillAutocomplete(payload.selector, payload.value);
        break;
```

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add fillAutocomplete handler for typeahead fields"
```

---

### Task 8: Add tag input, date, scroll, wait, force click, consent handlers

**Files:**
- Modify: `extension/content.js`

- [ ] **Step 1: Add fillTagInput function**

```javascript
/**
 * Fill a tag/chip input by typing each value and pressing Enter.
 * Used for skills, technologies, languages etc.
 */
async function fillTagInput(selector, values) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  el.focus();

  const added = [];
  for (const val of values) {
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));

    // Type the value
    for (const char of val) {
      el.value += char;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      await delay(60 + Math.random() * 40);
    }
    await delay(300); // Wait for any autocomplete
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true }));
    await delay(400);
    added.push(val);
  }

  return { success: true, value_set: added.join(", "), count: added.length };
}
```

- [ ] **Step 2: Add fillDate function**

```javascript
/**
 * Fill a date field — handles native <input type="date">, text inputs with format detection,
 * and custom calendar widgets.
 */
async function fillDate(selector, isoDate) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);

  const inputType = (el.getAttribute("type") || "text").toLowerCase();

  // Native date input — uses YYYY-MM-DD internally
  if (inputType === "date") {
    // Use native value setter to bypass React's synthetic event system
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value"
    ).set;
    nativeInputValueSetter.call(el, isoDate);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: isoDate };
  }

  // Text-based date — detect format from placeholder
  const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
  let formatted = isoDate;
  try {
    const [y, m, d] = isoDate.split("-");
    if (placeholder.includes("dd/mm")) {
      formatted = `${d}/${m}/${y}`;
    } else if (placeholder.includes("mm/dd")) {
      formatted = `${m}/${d}/${y}`;
    }
  } catch (_) { /* keep ISO format */ }

  el.focus();
  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type date with human-like speed
  for (const char of formatted) {
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    await delay(80 + Math.random() * 40);
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: formatted };
}
```

- [ ] **Step 3: Add scrollTo function**

```javascript
/**
 * Scroll an element into view with smooth behavior.
 */
async function scrollTo(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(500);
  return { success: true };
}
```

- [ ] **Step 4: Add waitForSelector function**

```javascript
/**
 * Poll DOM for a selector with configurable timeout.
 * Returns snapshot of the element when found, or error on timeout.
 */
async function waitForSelector(selector, timeoutMs) {
  const maxWait = timeoutMs || 10000;
  const pollInterval = 300;
  let elapsed = 0;

  while (elapsed < maxWait) {
    const el = resolveSelector(selector);
    if (el) {
      return {
        success: true,
        found_after_ms: elapsed,
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || "").trim().substring(0, 100),
      };
    }
    await delay(pollInterval);
    elapsed += pollInterval;
  }

  return { success: false, error: `Selector '${selector}' not found after ${maxWait}ms` };
}
```

- [ ] **Step 5: Add forceClick function**

```javascript
/**
 * Click an element even if obscured — dispatches click event directly.
 * Used when tooltips, banners, or overlays block the target.
 */
async function forceClick(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(200);

  // Dispatch a synthetic click event directly on the element
  el.dispatchEvent(new MouseEvent("click", {
    bubbles: true, cancelable: true, view: window,
  }));

  return { success: true };
}
```

- [ ] **Step 6: Add checkConsentBoxes function**

```javascript
/**
 * Auto-check all GDPR/terms/privacy/consent checkboxes on the page.
 * Mirrors Python checkbox_filler.auto_check_consent_boxes().
 */
async function checkConsentBoxes(rootSelector) {
  const root = rootSelector ? resolveSelector(rootSelector) : document;
  if (!root) return { success: false, error: "Root not found" };

  const consentPattern = /agree|consent|terms|privacy|gdpr|accept|acknowledge|policy|conditions|certify|confirm.*read/i;
  const checkboxes = root.querySelectorAll("input[type='checkbox']");
  const checked = [];

  for (const cb of checkboxes) {
    if (cb.checked || cb.disabled) continue;

    // Get label text
    let labelText = "";
    if (cb.id) {
      const labelEl = document.querySelector(`label[for='${cb.id}']`);
      if (labelEl) labelText = labelEl.textContent.trim();
    }
    if (!labelText && cb.parentElement) {
      labelText = cb.parentElement.textContent.trim();
    }
    if (!labelText) {
      labelText = cb.getAttribute("aria-label") || "";
    }

    if (consentPattern.test(labelText)) {
      cb.click();
      cb.dispatchEvent(new Event("change", { bubbles: true }));
      checked.push(labelText.substring(0, 60));
      await delay(200);
    }
  }

  return { success: true, checked_count: checked.length, labels: checked };
}
```

- [ ] **Step 7: Add rescanAfterFill function**

```javascript
/**
 * Re-scan page for conditional fields and validation errors after filling a field.
 * Detects: newly visible fields, validation errors, cascading dropdown updates.
 */
async function rescanAfterFill(filledSelector) {
  // Wait for DOM to settle (React re-renders, AJAX, animations)
  await delay(800);

  const result = {
    new_fields: [],
    validation_errors: [],
    snapshot: buildSnapshot(),
  };

  // Check for validation errors on the filled field
  const filledEl = resolveSelector(filledSelector);
  if (filledEl) {
    const isInvalid = filledEl.getAttribute("aria-invalid") === "true";
    if (isInvalid) {
      const errId = filledEl.getAttribute("aria-errormessage");
      let errMsg = "";
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) errMsg = errEl.textContent.trim();
      }
      result.validation_errors.push({
        selector: filledSelector,
        error: errMsg || "Field marked as invalid",
      });
    }
  }

  // Check for page-level errors (role="alert")
  for (const alert of document.querySelectorAll("[role='alert']")) {
    const text = alert.textContent.trim();
    if (text) {
      result.validation_errors.push({
        selector: "[role='alert']",
        error: text,
      });
    }
  }

  return result;
}
```

- [ ] **Step 8: Wire ALL new handlers into message handler**

Add these cases to the `switch (action)` block:

```javascript
      case "fill_tag_input":
        result = await fillTagInput(payload.selector, payload.values || []);
        break;
      case "fill_date":
        result = await fillDate(payload.selector, payload.value);
        break;
      case "scroll_to":
        result = await scrollTo(payload.selector);
        break;
      case "wait_for_selector":
        result = await waitForSelector(payload.selector, payload.timeout_ms);
        break;
      case "force_click":
        result = await forceClick(payload.selector);
        break;
      case "check_consent_boxes":
        result = await checkConsentBoxes(payload.root_selector || null);
        break;
      case "rescan_after_fill":
        result = await rescanAfterFill(payload.selector);
        break;
      case "scan_form_groups":
        result = { success: true, groups: scanFormGroups(payload.root_selector || null) };
        break;
      case "get_field_context": {
        const ctxEl = resolveSelector(payload.selector);
        if (!ctxEl) {
          result = { success: false, error: "Element not found" };
        } else {
          result = {
            success: true,
            context: extractFieldInfo(ctxEl, null),
          };
        }
        break;
      }
```

- [ ] **Step 9: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add tag input, date, scroll, wait, force click, consent, rescan handlers"
```

---

### Task 9: Upgrade existing selectOption with fuzzy matching

**Files:**
- Modify: `extension/content.js:376-391` (existing `selectOption` function)

- [ ] **Step 1: Replace selectOption with fuzzy-matching version**

Replace the existing `selectOption` function:

```javascript
/**
 * Select a dropdown option using fuzzy matching (abbreviation expansion, startswith, contains).
 * Upgraded from simple case-insensitive includes to full fuzzy matching.
 */
async function selectOption(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const options = [];
  for (const opt of el.querySelectorAll("option")) {
    const text = opt.textContent.trim();
    if (text && !text.toLowerCase().startsWith("select")) {
      options.push({ text, value: opt.value, el: opt });
    }
  }

  if (options.length === 0) {
    // Options may be async-loaded — wait and retry
    await delay(2000);
    for (const opt of el.querySelectorAll("option")) {
      const text = opt.textContent.trim();
      if (text && !text.toLowerCase().startsWith("select")) {
        options.push({ text, value: opt.value, el: opt });
      }
    }
  }

  if (options.length === 0) {
    return { success: false, error: "No options found in select" };
  }

  // Fuzzy match
  const match = fuzzyMatchOption(value, options.map(o => o.text));
  if (match) {
    const matched = options.find(o => o.text === match);
    if (matched) {
      el.value = matched.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: match };
    }
  }

  // Fallback: try matching by option value attribute
  for (const opt of options) {
    if (normalizeText(opt.value) === normalizeText(value)) {
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: opt.text };
    }
  }

  return {
    success: false,
    error: `No match for '${value}' in [${options.slice(0, 5).map(o => o.text).join(", ")}]`,
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): upgrade selectOption with fuzzy matching and abbreviation expansion"
```

---

### Task 10: Add new bridge methods to ext_bridge.py

**Files:**
- Modify: `jobpulse/ext_bridge.py` — add methods after `wait_for_apply` (after line 327)

- [ ] **Step 1: Add all new bridge methods**

Add after the `get_snapshot` method:

```python
    # ─── v2 Form Engine API ─────────────────────────────────────

    async def fill_radio_group(
        self, selector: str, value: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Fill a radio button group by matching option labels to value."""
        return await self._send_command(
            "fill_radio_group",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_custom_select(
        self, selector: str, value: str, timeout_ms: int = 15000
    ) -> dict[str, Any]:
        """Fill a custom React/Angular dropdown widget."""
        return await self._send_command(
            "fill_custom_select",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_autocomplete(
        self, selector: str, value: str, timeout_ms: int = 15000
    ) -> dict[str, Any]:
        """Fill a typeahead/autocomplete field — types partial, clicks suggestion."""
        return await self._send_command(
            "fill_autocomplete",
            {"selector": selector, "value": value},
            timeout_ms=timeout_ms,
        )

    async def fill_tag_input(
        self, selector: str, values: list[str], timeout_ms: int = 20000
    ) -> dict[str, Any]:
        """Fill a tag/chip input — types each value + Enter."""
        return await self._send_command(
            "fill_tag_input",
            {"selector": selector, "values": values},
            timeout_ms=timeout_ms,
        )

    async def fill_date(
        self, selector: str, iso_date: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Fill a date field (native or text-based)."""
        return await self._send_command(
            "fill_date",
            {"selector": selector, "value": iso_date},
            timeout_ms=timeout_ms,
        )

    async def scroll_to(self, selector: str, timeout_ms: int = 5000) -> bool:
        """Scroll an element into view."""
        result = await self._send_command(
            "scroll_to", {"selector": selector}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def wait_for_selector(
        self, selector: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Wait for a selector to appear in the DOM."""
        return await self._send_command(
            "wait_for_selector",
            {"selector": selector, "timeout_ms": timeout_ms},
            timeout_ms=timeout_ms + 3000,
        )

    async def force_click(self, selector: str, timeout_ms: int = 10000) -> bool:
        """Click element even if obscured (dispatches event directly)."""
        result = await self._send_command(
            "force_click", {"selector": selector}, timeout_ms=timeout_ms
        )
        return bool(result.get("success", False))

    async def check_consent_boxes(
        self, root_selector: str | None = None, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Auto-check all consent/GDPR/terms checkboxes."""
        return await self._send_command(
            "check_consent_boxes",
            {"root_selector": root_selector or ""},
            timeout_ms=timeout_ms,
        )

    async def scan_form_groups(
        self, root_selector: str | None = None, timeout_ms: int = 10000
    ) -> list[dict[str, Any]]:
        """Scan for form groups (label+input pairs) within a container."""
        result = await self._send_command(
            "scan_form_groups",
            {"root_selector": root_selector or ""},
            timeout_ms=timeout_ms,
        )
        return result.get("groups", [])

    async def rescan_after_fill(
        self, selector: str, timeout_ms: int = 10000
    ) -> dict[str, Any]:
        """Re-scan page after filling a field for conditional fields and errors."""
        return await self._send_command(
            "rescan_after_fill",
            {"selector": selector},
            timeout_ms=timeout_ms,
        )
```

- [ ] **Step 2: Run existing bridge tests**

Run: `python -m pytest tests/jobpulse/test_ext_bridge.py -v`
Expected: All existing tests PASS (new methods are additive)

- [ ] **Step 3: Commit**

```bash
git add jobpulse/ext_bridge.py
git commit -m "feat(ext): add 12 new bridge methods for v2 form engine"
```

---

### Task 11: Fix state machine radio vs checkbox routing

**Files:**
- Modify: `jobpulse/state_machines/__init__.py:179-227` (`_actions_screening` method)

- [ ] **Step 1: Fix _actions_screening to use correct action types**

Replace the `_actions_screening` method:

```python
    def _actions_screening(
        self,
        snapshot: PageSnapshot,
        profile: dict[str, str],
        custom_answers: dict[str, str],
        form_intelligence: object | None = None,
    ) -> list[Action]:
        """Answer screening questions — uses FormIntelligence router when provided,
        otherwise falls back to screening_answers.get_answer().

        Routes to correct v2 action types:
        - select → fill_custom_select (for role=listbox/combobox) or select (native)
        - radio → fill_radio_group (NOT check)
        - checkbox → check
        - search_autocomplete → fill_autocomplete
        - date → fill_date
        - textarea/text → fill
        """
        from jobpulse.screening_answers import get_answer

        actions: list[Action] = []
        job_context = custom_answers.get("_job_context")
        context_dict = None
        if isinstance(job_context, dict):
            context_dict = job_context

        for field in snapshot.fields:
            if field.current_value:
                continue  # Already filled

            if form_intelligence is not None:
                field_answer = form_intelligence.resolve(  # type: ignore[union-attr]
                    question=field.label,
                    job_context=context_dict,
                    input_type=field.input_type,
                    platform=self.platform,
                )
                answer = field_answer.answer if field_answer.answer else None
            else:
                answer = get_answer(
                    field.label,
                    context_dict,
                    input_type=field.input_type,
                    platform=self.platform,
                )

            if not answer:
                continue

            # Route to correct action type based on input_type
            if field.input_type in ("select", "custom_select"):
                actions.append(Action(type="select", selector=field.selector, value=answer))
            elif field.input_type == "search_autocomplete":
                actions.append(Action(type="fill_autocomplete", selector=field.selector, value=answer))
            elif field.input_type == "radio":
                actions.append(Action(type="fill_radio_group", selector=field.selector, value=answer))
            elif field.input_type == "checkbox":
                actions.append(Action(type="check", selector=field.selector, value=answer))
            elif field.input_type == "date":
                actions.append(Action(type="fill_date", selector=field.selector, value=answer))
            else:
                actions.append(Action(type="fill", selector=field.selector, value=answer))

        return actions
```

- [ ] **Step 2: Run existing tests**

Run: `python -m pytest tests/ -v -k "state_machine or screening" --no-header -q`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add jobpulse/state_machines/__init__.py
git commit -m "fix(ext): route radio to fill_radio_group, autocomplete to fill_autocomplete in state machine"
```

---

### Task 12: Fix application orchestrator — stale snapshot, new action dispatch, consent

**Files:**
- Modify: `jobpulse/application_orchestrator.py:375-487`

- [ ] **Step 1: Fix stale snapshot bug in _fill_application**

In `_fill_application`, replace lines 431-443 (the section after submit/next button click):

```python
            if state == ApplicationState.SUBMIT:
                if dry_run:
                    return {"success": True, "dry_run": True, "screenshot": last_screenshot, "pages_filled": page_num}
                # Use CURRENT page_snapshot (not stale snapshot variable)
                current_buttons = page_snapshot.buttons if hasattr(page_snapshot, 'buttons') else snapshot.get("buttons", [])
                submit_btn = find_next_button(
                    [b.model_dump() if hasattr(b, 'model_dump') else b for b in current_buttons]
                )
                if submit_btn:
                    await self.bridge.click(submit_btn["selector"])
            else:
                # Use CURRENT page_snapshot for next button
                current_buttons = page_snapshot.buttons if hasattr(page_snapshot, 'buttons') else snapshot.get("buttons", [])
                next_btn = find_next_button(
                    [b.model_dump() if hasattr(b, 'model_dump') else b for b in current_buttons]
                )
                if next_btn:
                    await self.bridge.click(next_btn["selector"])

            prev_snapshot = snapshot
            snapshot = self._as_dict(await self.bridge.get_snapshot())
```

- [ ] **Step 2: Update _execute_action to dispatch v2 action types**

Replace the `_execute_action` method:

```python
    async def _execute_action(self, action: Any, tg_stream: Any = None):
        if hasattr(action, "model_dump"):
            atype = getattr(action, "type", "")
            selector = getattr(action, "selector", "")
            value = getattr(action, "value", "")
            file_path = getattr(action, "file_path", None)
            label = getattr(action, "label", selector)
            tier = getattr(action, "tier", 1)
            confidence = getattr(action, "confidence", 1.0)
        else:
            atype = action.get("type", "")
            selector = action.get("selector", "")
            value = action.get("value", "")
            file_path = action.get("file_path")
            label = action.get("label", selector)
            tier = action.get("tier", 1)
            confidence = action.get("confidence", 1.0)

        if atype == "fill":
            await self.bridge.fill(selector, value)
        elif atype == "upload":
            await self.bridge.upload(selector, str(file_path))
        elif atype == "click":
            await self.bridge.click(selector)
        elif atype == "select":
            await self.bridge.select_option(selector, value)
        elif atype == "check":
            await self.bridge.check(selector, value.lower() in ("true", "yes", "1", "checked") if value else True)
        # v2 action types
        elif atype == "fill_radio_group":
            await self.bridge.fill_radio_group(selector, value)
        elif atype == "fill_custom_select":
            await self.bridge.fill_custom_select(selector, value)
        elif atype == "fill_autocomplete":
            await self.bridge.fill_autocomplete(selector, value)
        elif atype == "fill_tag_input":
            values = [v.strip() for v in value.split(",") if v.strip()] if value else []
            await self.bridge.fill_tag_input(selector, values)
        elif atype == "fill_date":
            await self.bridge.fill_date(selector, value)
        elif atype == "scroll_to":
            await self.bridge.scroll_to(selector)
        elif atype == "force_click":
            await self.bridge.force_click(selector)
        elif atype == "check_consent_boxes":
            await self.bridge.check_consent_boxes(selector or None)

        # Stream field progress to Telegram in real-time
        if tg_stream is not None and atype in ("fill", "select", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date"):
            try:
                await tg_stream.stream_field(
                    label=str(label),
                    value=str(value),
                    tier=int(tier),
                    confident=float(confidence) >= 0.7,
                )
            except Exception as _se:
                logger.debug("stream_field failed: %s", _se)
```

- [ ] **Step 3: Add consent auto-check to _fill_application before submit**

In `_fill_application`, add consent auto-check before the submit/next button section. Insert before `if state == ApplicationState.SUBMIT:`:

```python
            # Auto-check consent boxes before any navigation
            try:
                await self.bridge.check_consent_boxes()
            except (TimeoutError, ConnectionError):
                pass  # Non-critical — proceed without
```

- [ ] **Step 4: Run existing tests**

Run: `python -m pytest tests/jobpulse/test_ext_adapter.py tests/jobpulse/test_ext_routing.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator.py
git commit -m "fix(ext): fix stale snapshot, add v2 action dispatch, consent auto-check in orchestrator"
```

---

### Task 13: Add bridge tests for new methods

**Files:**
- Modify: `tests/jobpulse/test_ext_bridge.py`

- [ ] **Step 1: Add test class for v2 bridge methods**

Append to the test file:

```python
# =========================================================================
# v2 Form Engine methods
# =========================================================================


class TestBridgeV2Methods:
    """Tests for v2 form engine bridge methods."""

    @pytest.mark.asyncio
    async def test_fill_radio_group(self, bridge):
        """fill_radio_group sends correct command and returns result."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "fill_radio_group"
                assert msg["payload"]["selector"] == "fieldset.sponsor"
                assert msg["payload"]["value"] == "No"
                # Send ack then result
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {"success": True, "value_set": "No"},
                }))

            result, _ = await asyncio.gather(
                bridge.fill_radio_group("fieldset.sponsor", "No"),
                respond(),
            )
            assert result["success"] is True
            assert result["value_set"] == "No"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_fill_custom_select(self, bridge):
        """fill_custom_select sends correct command."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "fill_custom_select"
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {"success": True, "value_set": "United Kingdom"},
                }))

            result, _ = await asyncio.gather(
                bridge.fill_custom_select("[role='listbox']", "UK"),
                respond(),
            )
            assert result["success"] is True
            assert result["value_set"] == "United Kingdom"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_fill_autocomplete(self, bridge):
        """fill_autocomplete sends correct command."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "fill_autocomplete"
                assert msg["payload"]["value"] == "Dundee"
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {"success": True, "value_set": "Dundee, United Kingdom"},
                }))

            result, _ = await asyncio.gather(
                bridge.fill_autocomplete("input[aria-label*='City']", "Dundee"),
                respond(),
            )
            assert result["success"] is True

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_fill_tag_input(self, bridge):
        """fill_tag_input sends values list."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "fill_tag_input"
                assert msg["payload"]["values"] == ["Python", "ML"]
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {"success": True, "count": 2},
                }))

            result, _ = await asyncio.gather(
                bridge.fill_tag_input("input.skills", ["Python", "ML"]),
                respond(),
            )
            assert result["success"] is True
            assert result["count"] == 2

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_scan_form_groups(self, bridge):
        """scan_form_groups sends correct command."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "scan_form_groups"
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {"groups": [
                        {"group_selector": "fieldset.q1", "question": "Work auth?", "fields": []},
                    ]},
                }))

            result, _ = await asyncio.gather(
                bridge.scan_form_groups("[role='dialog']"),
                respond(),
            )
            assert len(result) == 1
            assert result[0]["question"] == "Work auth?"

        await bridge.stop()

    @pytest.mark.asyncio
    async def test_rescan_after_fill(self, bridge):
        """rescan_after_fill returns validation errors."""
        await bridge.start()

        async with websockets.connect(f"ws://localhost:{bridge.port}") as ws:
            bridge._connected.set()
            bridge._ws = ws

            async def respond():
                raw = await ws.recv()
                msg = json.loads(raw)
                assert msg["action"] == "rescan_after_fill"
                await ws.send(json.dumps({"id": msg["id"], "type": "ack", "payload": {}}))
                await ws.send(json.dumps({
                    "id": msg["id"], "type": "result",
                    "payload": {
                        "new_fields": [],
                        "validation_errors": [{"selector": "#email", "error": "Invalid email"}],
                        "snapshot": {"url": "https://example.com", "title": "Test", "fields": [], "buttons": []},
                    },
                }))

            result, _ = await asyncio.gather(
                bridge.rescan_after_fill("#email"),
                respond(),
            )
            assert len(result["validation_errors"]) == 1

        await bridge.stop()
```

- [ ] **Step 2: Run all bridge tests**

Run: `python -m pytest tests/jobpulse/test_ext_bridge.py -v`
Expected: All PASS (existing + new)

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_ext_bridge.py
git commit -m "test(ext): add tests for v2 bridge methods (radio, custom select, autocomplete, tags, rescan)"
```

---

### Task 14: Add ext_models tests for new fields

**Files:**
- Modify: `tests/jobpulse/test_ext_models.py`

- [ ] **Step 1: Add tests for FormGroup and updated FieldInfo**

```python
from jobpulse.ext_models import FieldInfo, FormGroup, PageSnapshot


class TestFormGroup:
    def test_form_group_creation(self):
        """FormGroup can be created with defaults."""
        fg = FormGroup(group_selector="fieldset.q1", question="Do you require sponsorship?")
        assert fg.question == "Do you require sponsorship?"
        assert fg.fields == []
        assert fg.is_required is False
        assert fg.is_answered is False

    def test_form_group_with_fields(self):
        """FormGroup can contain FieldInfo objects."""
        field = FieldInfo(
            selector="#sponsor-yes", input_type="radio", label="Yes",
            group_label="Sponsorship", group_selector="fieldset.q1",
        )
        fg = FormGroup(
            group_selector="fieldset.q1",
            question="Do you require sponsorship?",
            fields=[field],
            is_required=True,
        )
        assert len(fg.fields) == 1
        assert fg.fields[0].group_label == "Sponsorship"


class TestFieldInfoV2:
    def test_field_info_with_context(self):
        """FieldInfo includes v2 context fields."""
        fi = FieldInfo(
            selector="#phone", input_type="tel", label="Phone",
            group_label="Contact Info",
            parent_text="Enter your phone number",
            help_text="Include country code",
            error_text="",
        )
        assert fi.group_label == "Contact Info"
        assert fi.help_text == "Include country code"
        assert fi.error_text == ""

    def test_field_info_v2_defaults(self):
        """v2 fields default to empty strings."""
        fi = FieldInfo(selector="#x", input_type="text", label="X")
        assert fi.group_label == ""
        assert fi.group_selector == ""
        assert fi.parent_text == ""
        assert fi.fieldset_legend == ""
        assert fi.help_text == ""
        assert fi.error_text == ""
        assert fi.aria_describedby == ""


class TestPageSnapshotV2:
    def test_snapshot_with_form_groups(self):
        """PageSnapshot includes form_groups and progress."""
        snap = PageSnapshot(
            url="https://linkedin.com/easy-apply",
            title="Apply",
            form_groups=[
                FormGroup(group_selector="fieldset", question="Q1"),
            ],
            progress=[2, 5],
            modal_detected=True,
        )
        assert len(snap.form_groups) == 1
        assert snap.progress == [2, 5]
        assert snap.modal_detected is True

    def test_snapshot_v2_defaults(self):
        """v2 snapshot fields default correctly."""
        snap = PageSnapshot(url="https://example.com", title="Test")
        assert snap.form_groups == []
        assert snap.progress is None
        assert snap.modal_detected is False
```

- [ ] **Step 2: Run model tests**

Run: `python -m pytest tests/jobpulse/test_ext_models.py -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_ext_models.py
git commit -m "test(ext): add tests for FormGroup, FieldInfo v2 context, PageSnapshot v2 fields"
```

---

### Task 15: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v -x --timeout=30 -q 2>&1 | tail -20`
Expected: All PASS, no regressions

- [ ] **Step 2: Verify no import errors**

Run: `python -c "from jobpulse.ext_models import FormGroup, Action, ExtCommand; from jobpulse.ext_bridge import ExtensionBridge; from jobpulse.state_machines import get_state_machine; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 3: Commit all remaining changes**

```bash
git add -A
git commit -m "feat(ext): extension form engine v2 — 12 handlers, form groups, fuzzy matching, modal scoping"
```
