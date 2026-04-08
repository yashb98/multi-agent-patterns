# Phase 3: Extract fillers/ (Tasks 8-15)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract all form-filling functions from content.js into `fillers/` modules, then add new label-based fillers.

**Depends on:** Phase 1 (core/) and Phase 2 (scanners/).

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

### Task 8: Create fillers/fill_text.js

**Files:**
- Create: `extension/fillers/fill_text.js`
- Reference: `extension/content.js:968-1092` (fillField, fillContentEditable)

- [ ] **Step 1: Create fillers/fill_text.js**

```js
// extension/fillers/fill_text.js — Human-like text input and contenteditable filling
// Changes when: text input or rich text typing interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillField(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  if (el.getAttribute("contenteditable") === "true" || el.isContentEditable) {
    return fillContentEditable(el, value);
  }
  // COPY the rest of fillField from content.js:978-1039
  // Replace: delay → JP.dom.delay, moveCursorTo → JP.cursor.moveCursorTo,
  //          highlightElement → JP.cursor.highlightElement, cursorClickFlash → JP.cursor.cursorClickFlash,
  //          setNativeValue → JP.form.setNativeValue, behaviorProfile → JP.timing.behaviorProfile
}

async function fillContentEditable(el, value) {
  const JP = window.JobPulse;
  if (!el) return { success: false, error: "Contenteditable element is null" };
  // COPY from content.js:1047-1092
  // Same replacements as above
}

window.JobPulse.fillers.text = { fillField, fillContentEditable };
```

Copy each function body verbatim, only replacing top-level function references with `JP.*` namespace calls.

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_text.js
git commit -m "refactor(ext): extract fillers/fill_text.js — text + contenteditable"
```

---

### Task 9: Create fillers/fill_select.js

**Files:**
- Create: `extension/fillers/fill_select.js`
- Reference: `extension/content.js:1158-1219` (selectOption, checkBox), `extension/content.js:1790-1822` (checkConsentBoxes)

- [ ] **Step 1: Create fillers/fill_select.js**

```js
// extension/fillers/fill_select.js — Native select, checkbox, and consent interaction
// Changes when: native select/checkbox interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function selectOption(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1162-1206
  // Replace: normalizeText → JP.form.normalizeText, fuzzyMatchOption → JP.form.fuzzyMatchOption,
  //          verifyFieldValue → JP.form.verifyFieldValue, delay → JP.dom.delay
}

async function checkBox(selector, shouldCheck) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  const want = shouldCheck === "true" || shouldCheck === true;
  if (el.checked !== want) el.click();
  return { success: true, value_set: String(el.checked), value_verified: el.checked === want };
}

async function checkConsentBoxes(rootSelector) {
  const JP = window.JobPulse;
  const root = rootSelector ? JP.dom.resolveSelector(rootSelector) : document;
  if (!root) return { success: false, error: "Root not found" };
  // COPY from content.js:1794-1822
  // Replace: delay → JP.dom.delay
}

window.JobPulse.fillers.select = { selectOption, checkBox, checkConsentBoxes };
```

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_select.js
git commit -m "refactor(ext): extract fillers/fill_select.js — select, checkbox, consent"
```

---

### Task 10: Create fillers/fill_radio.js

**Files:**
- Create: `extension/fillers/fill_radio.js`
- Reference: `extension/content.js:1225-1304` (fillRadioGroup)

- [ ] **Step 1: Create fillers/fill_radio.js**

```js
// extension/fillers/fill_radio.js — Label-aware radio group selection
// Changes when: radio group detection or selection changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillRadioGroup(groupSelector, value) {
  const JP = window.JobPulse;
  // COPY VERBATIM from content.js:1226-1304
  // Replace: resolveSelector → JP.dom.resolveSelector,
  //          fuzzyMatchOption → JP.form.fuzzyMatchOption,
  //          smartScroll → JP.dom.smartScroll, delay → JP.dom.delay,
  //          getFieldGap → JP.timing.getFieldGap
}

window.JobPulse.fillers.radio = { fillRadioGroup };
```

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_radio.js
git commit -m "refactor(ext): extract fillers/fill_radio.js — radio group fill"
```

---

### Task 11: Create fillers/fill_combobox.js

**Files:**
- Create: `extension/fillers/fill_combobox.js`
- Reference: `extension/content.js:1450-1648` (fillCombobox), `extension/content.js:2296-2361` (revealOptions from message handler)

- [ ] **Step 1: Create fillers/fill_combobox.js**

```js
// extension/fillers/fill_combobox.js — Custom dropdown open/search/select + option reveal
// Changes when: custom dropdown open/search/select logic changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

// Placeholder values to skip — shared between fillCombobox and revealOptions
const PLACEHOLDER_VALUES = new Set([
  "-none-", "none", "loading", "-none- loading", "select", "select...",
  "choose", "please select", "-- select --", "---", "--", "",
]);

const OPTION_SELECTORS = [
  "[role='option']", "[role='listbox'] li", "[role='listbox'] [role='option']",
  "lyte-drop-box li", ".lyte-dropdown-items li", ".cxDropdownMenuList li",
  "[class*='dropdown'] li", "[class*='dropdown'] [class*='option']",
  "[class*='menu'] li[class*='option']", "[class*='listbox'] li",
  "ul[class*='select'] li", ".select-options li", "[data-value]",
];

function findDropdownTargets(el) {
  const targets = [el];
  let parent = el.parentElement;
  for (let depth = 0; depth < 5 && parent; depth++) {
    const tag = parent.tagName.toLowerCase();
    const cls = parent.className || "";
    if (tag.startsWith("lyte-") || cls.includes("dropdown") || cls.includes("select") ||
        parent.getAttribute("role") === "combobox" || parent.getAttribute("role") === "listbox") {
      targets.push(parent); break;
    }
    parent = parent.parentElement;
  }
  const innerTrigger = el.querySelector("input, [class*='trigger'], [class*='arrow'], [class*='toggle'], button, lyte-icon");
  if (innerTrigger) targets.push(innerTrigger);
  return targets;
}

function clickTargets(targets) {
  for (const t of targets) {
    t.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    t.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
    t.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    t.click();
  }
}

async function fillCombobox(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1453-1648
  // Replace internal helpers with shared functions above
  // Replace: moveCursorTo → JP.cursor.moveCursorTo, highlightElement → JP.cursor.highlightElement,
  //          smartScroll → JP.dom.smartScroll, delay → JP.dom.delay,
  //          setNativeValue → JP.form.setNativeValue, cursorClickFlash → JP.cursor.cursorClickFlash
}

async function revealOptions(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  const targets = findDropdownTargets(el);
  clickTargets(targets);
  await JP.dom.delay(350);

  const options = [];
  const seen = new Set();
  for (const sel of OPTION_SELECTORS) {
    for (const opt of document.querySelectorAll(sel)) {
      const text = opt.textContent.trim();
      if (text && text.length < 200 && !seen.has(text) && !PLACEHOLDER_VALUES.has(text.toLowerCase())) {
        seen.add(text); options.push(text);
      }
    }
    if (options.length > 0) break;
  }

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await JP.dom.delay(100);

  return { success: true, options, selector };
}

window.JobPulse.fillers.combobox = { fillCombobox, revealOptions };
```

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_combobox.js
git commit -m "refactor(ext): extract fillers/fill_combobox.js — combobox + reveal"
```

---

### Task 12: Create fillers/fill_dropdown.js

**Files:**
- Create: `extension/fillers/fill_dropdown.js`
- Reference: `extension/content.js:1306-1443` (fillCustomSelect, fillAutocomplete)

- [ ] **Step 1: Create fillers/fill_dropdown.js**

```js
// extension/fillers/fill_dropdown.js — Trigger-based dropdown and type-ahead autocomplete
// Changes when: trigger-based dropdown or type-ahead interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillCustomSelect(triggerSelector, value) {
  const JP = window.JobPulse;
  const trigger = JP.dom.resolveSelector(triggerSelector);
  if (!trigger) return { success: false, error: "Trigger not found: " + triggerSelector };
  // COPY from content.js:1309-1376
  // Replace: smartScroll → JP.dom.smartScroll, delay → JP.dom.delay,
  //          setNativeValue → JP.form.setNativeValue, fuzzyMatchOption → JP.form.fuzzyMatchOption
}

async function fillAutocomplete(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1378-1443
  // Same replacements as above + behaviorProfile → JP.timing.behaviorProfile
}

window.JobPulse.fillers.dropdown = { fillCustomSelect, fillAutocomplete };
```

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_dropdown.js
git commit -m "refactor(ext): extract fillers/fill_dropdown.js — custom select + autocomplete"
```

---

### Task 13: Create fillers/fill_simple.js

**Files:**
- Create: `extension/fillers/fill_simple.js`
- Reference: `extension/content.js:1650-1743` (fillTagInput, fillDate), `extension/content.js:1098-1111` (uploadFile)

- [ ] **Step 1: Create fillers/fill_simple.js**

```js
// extension/fillers/fill_simple.js — Tag input, date, and file upload
// Changes when: tag/date/file input handling changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillTagInput(selector, values) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1653-1675
  // Replace: smartScroll → JP.dom.smartScroll, setNativeValue → JP.form.setNativeValue,
  //          delay → JP.dom.delay
}

async function fillDate(selector, isoDate) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1677-1743
  // Same replacements
}

async function uploadFile(selector, base64Data, fileName, mimeType) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  const bytes = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
  const file = new File([bytes], fileName, { type: mimeType || "application/pdf" });
  const dt = new DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { success: true, value_set: fileName };
}

window.JobPulse.fillers.simple = { fillTagInput, fillDate, uploadFile };
```

- [ ] **Step 2: Commit**

```bash
git add extension/fillers/fill_simple.js
git commit -m "refactor(ext): extract fillers/fill_simple.js — tag, date, upload"
```

---

### Task 14: Create fillers/fill_actions.js and fillers/fill_validate.js

**Files:**
- Create: `extension/fillers/fill_actions.js`
- Create: `extension/fillers/fill_validate.js`
- Reference: `extension/content.js:1116-1153` (clickElement), `extension/content.js:1745-1788` (scrollTo, waitForSelector, forceClick), `extension/content.js:1824-1952` (rescanAfterFill, scanValidationErrors)

- [ ] **Step 1: Create fillers/fill_actions.js**

```js
// extension/fillers/fill_actions.js — Click, scroll, wait interactions
// Changes when: click/scroll/wait interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function clickElement(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  // COPY from content.js:1119-1153
  // Replace: delay → JP.dom.delay, moveCursorTo → JP.cursor.moveCursorTo,
  //          highlightElement → JP.cursor.highlightElement, cursorClickFlash → JP.cursor.cursorClickFlash
}

async function forceClick(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await JP.dom.delay(200);
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
  return { success: true };
}

async function scrollTo(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await JP.dom.delay(500);
  return { success: true };
}

async function waitForSelector(selector, timeoutMs) {
  const JP = window.JobPulse;
  const maxWait = timeoutMs || 10000;
  const pollInterval = 300;
  let elapsed = 0;
  while (elapsed < maxWait) {
    const el = JP.dom.resolveSelector(selector);
    if (el) return { success: true, found_after_ms: elapsed, tag: el.tagName.toLowerCase(),
      text: (el.textContent || "").trim().substring(0, 100) };
    await JP.dom.delay(pollInterval);
    elapsed += pollInterval;
  }
  return { success: false, error: `Selector '${selector}' not found after ${maxWait}ms` };
}

window.JobPulse.fillers.actions = { clickElement, forceClick, scrollTo, waitForSelector };
```

- [ ] **Step 2: Create fillers/fill_validate.js**

```js
// extension/fillers/fill_validate.js — Validation error scanning and post-fill rescan
// Changes when: error detection strategies change
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

function scanValidationErrors() {
  const JP = window.JobPulse;
  // COPY VERBATIM from content.js:1873-1952
  // Replace: extractFieldContext → JP.scanners.fieldContext.extractFieldContext
}

async function rescanAfterFill(filledSelector) {
  const JP = window.JobPulse;
  await JP.dom.delay(800);
  // COPY from content.js:1824-1861
  // Replace: resolveSelector → JP.dom.resolveSelector,
  //          buildSnapshot → JP.detectors.snapshot.buildSnapshot
  // NOTE: buildSnapshot reference will be available after Task 17
}

window.JobPulse.fillers.validate = { scanValidationErrors, rescanAfterFill };
```

- [ ] **Step 3: Commit**

```bash
git add extension/fillers/fill_actions.js extension/fillers/fill_validate.js
git commit -m "refactor(ext): extract fillers/fill_actions.js + fill_validate.js"
```

---

### Task 15: Create fillers/label_fill.js and fillers/label_fill_choice.js (NEW CODE)

**Files:**
- Create: `extension/fillers/label_fill.js`
- Create: `extension/fillers/label_fill_choice.js`

- [ ] **Step 1: Create fillers/label_fill.js**

```js
// extension/fillers/label_fill.js — Label-based element lookup, text fill, checkbox fill
// Changes when: label-based element lookup or text/check fill changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

function findByLabel(label) {
  const JP = window.JobPulse;
  const cleanLabel = clean(label);

  // Strategy 1: <label for="id"> exact match
  for (const lbl of document.querySelectorAll("label")) {
    if (clean(lbl.textContent) === cleanLabel) {
      const forId = lbl.getAttribute("for");
      if (forId) { const el = document.getElementById(forId); if (el && JP.dom.isFieldVisible(el)) return el; }
      const inner = lbl.querySelector("input, select, textarea, [contenteditable='true']");
      if (inner && JP.dom.isFieldVisible(inner)) return inner;
    }
  }
  // Strategy 2: aria-label exact
  for (const tag of ["input", "select", "textarea"]) {
    const el = document.querySelector(`${tag}[aria-label="${CSS.escape(label)}"]`);
    if (el && JP.dom.isFieldVisible(el)) return el;
  }
  // Strategy 3: placeholder
  for (const tag of ["input", "textarea"]) {
    const el = document.querySelector(`${tag}[placeholder="${CSS.escape(label)}"]`);
    if (el && JP.dom.isFieldVisible(el)) return el;
  }
  // Strategy 4: fuzzy — label contains or is contained
  for (const lbl of document.querySelectorAll("label")) {
    const t = clean(lbl.textContent);
    if (t && (t.includes(cleanLabel) || cleanLabel.includes(t))) {
      const forId = lbl.getAttribute("for");
      if (forId) return document.getElementById(forId);
      const inner = lbl.querySelector("input, select, textarea");
      if (inner) return inner;
    }
  }
  return null;
}

async function fillByLabel(label, value, fieldType) {
  const JP = window.JobPulse;
  await JP.dom.delay(JP.timing.getFieldGap(label));
  const el = findByLabel(label);
  if (!el) return { success: false, error: `No field found for label '${label}'` };
  await JP.dom.smartScroll(el);
  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);
  await JP.cursor.cursorClickFlash();

  const tag = el.tagName.toLowerCase();
  const inputType = (el.getAttribute("type") || "").toLowerCase();

  if (tag === "select") return JP.fillers.labelChoice.selectByLabel(el, value);
  if (inputType === "checkbox") return checkByLabel(el, value);
  if (inputType === "radio") return JP.fillers.labelChoice.radioByLabel(label, value);
  if (el.getAttribute("role") === "combobox" || el.getAttribute("role") === "listbox")
    return JP.fillers.labelChoice.comboboxByLabel(el, value);
  if (el.isContentEditable) return contentEditableByLabel(el, value);
  return textByLabel(el, value);
}

async function textByLabel(el, value) {
  const JP = window.JobPulse;
  el.focus(); el.dispatchEvent(new Event("focus", { bubbles: true }));
  JP.form.setNativeValue(el, ""); el.dispatchEvent(new Event("input", { bubbles: true }));
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    JP.form.setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    await JP.dom.delay(Math.max(30, JP.timing.getTypingDelay()));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  await JP.dom.delay(100);
  return { success: true, value_set: el.value, value_verified: el.value === value };
}

async function contentEditableByLabel(el, value) {
  const JP = window.JobPulse;
  el.focus(); el.dispatchEvent(new Event("focus", { bubbles: true }));
  el.innerText = ""; el.dispatchEvent(new Event("input", { bubbles: true }));
  for (const char of value) {
    document.execCommand("insertText", false, char);
    await JP.dom.delay(Math.max(30, JP.timing.getTypingDelay()));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  const actual = (el.innerText || el.textContent || "").trim();
  return { success: true, value_set: actual, value_verified: actual.includes(value.substring(0, 20)) };
}

function checkByLabel(el, value) {
  const want = value === "true" || value === true || value === "yes";
  if (el.checked !== want) el.click();
  return { success: true, value_set: String(el.checked), value_verified: el.checked === want };
}

window.JobPulse.fillers.labelFill = { findByLabel, fillByLabel };
```

- [ ] **Step 2: Create fillers/label_fill_choice.js**

```js
// extension/fillers/label_fill_choice.js — Label-based select, combobox, radio
// Changes when: label-based choice control interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function selectByLabel(el, value) {
  const JP = window.JobPulse;
  const options = [...el.options].map(o => o.text.trim()).filter(Boolean);
  const match = JP.form.fuzzyMatchOption(value, options);
  if (!match) return { success: false, error: `No match for '${value}'`, available: options.slice(0, 10) };
  const opt = [...el.options].find(o => o.text.trim() === match);
  el.value = opt.value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { success: true, value_set: match, value_verified: true };
}

async function comboboxByLabel(el, value) {
  const JP = window.JobPulse;
  // Click to open
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  el.click();
  await JP.dom.delay(400);

  // Type to filter if there's an inner input
  const inputEl = el.tagName === "INPUT" ? el : el.querySelector("input");
  if (inputEl) {
    inputEl.focus();
    JP.form.setNativeValue(inputEl, "");
    const filterText = value.substring(0, 5);
    for (const char of filterText) {
      JP.form.setNativeValue(inputEl, inputEl.value + char);
      inputEl.dispatchEvent(new Event("input", { bubbles: true }));
      await JP.dom.delay(80);
    }
    await JP.dom.delay(600);
  }

  // Search for options
  const optSelectors = ["[role='option']", "[role='listbox'] li", "[class*='dropdown'] li",
    "[class*='option']", "ul li"];
  const valueLower = value.toLowerCase().trim();
  for (const sel of optSelectors) {
    for (const opt of document.querySelectorAll(sel)) {
      const text = opt.textContent.trim();
      if (!text || text.length > 200) continue;
      if (text.toLowerCase().includes(valueLower) || valueLower.includes(text.toLowerCase())) {
        opt.click(); await JP.dom.delay(200);
        return { success: true, value_set: text, value_verified: true };
      }
    }
  }
  // Close and report failure
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  return { success: false, error: `No match for '${value}' in combobox` };
}

async function radioByLabel(groupLabel, value) {
  const JP = window.JobPulse;
  // Find all radios, match their labels
  const allRadios = document.querySelectorAll('input[type="radio"]');
  const labelMap = [];
  for (const radio of allRadios) {
    const name = JP.scanners.label.getAccessibleName(radio);
    if (name) labelMap.push({ text: name, radio });
  }
  const match = JP.form.fuzzyMatchOption(value, labelMap.map(l => l.text));
  if (!match) return { success: false, error: `No matching radio for '${value}'` };
  const matched = labelMap.find(l => l.text === match);
  if (matched) {
    await JP.dom.smartScroll(matched.radio);
    matched.radio.click();
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match, value_verified: matched.radio.checked };
  }
  return { success: false, error: "Match found but click failed" };
}

window.JobPulse.fillers.labelChoice = { selectByLabel, comboboxByLabel, radioByLabel };
```

- [ ] **Step 3: Commit**

```bash
git add extension/fillers/label_fill.js extension/fillers/label_fill_choice.js
git commit -m "feat(ext): add fillers/label_fill.js + label_fill_choice.js — fill by label"
```
