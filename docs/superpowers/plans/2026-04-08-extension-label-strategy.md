# Extension Label Strategy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the 2500-line content.js monolith into 25 focused files using a strategy pattern, then add a label-based form filling strategy alongside the existing selector-based approach for A/B testing.

**Architecture:** Namespace-based module system (`window.JobPulse`) since MV3 content scripts cannot use ES modules. Files loaded in dependency order via manifest.json. Strategy selection via `payload.strategy` parameter in messages from Python — defaults to `"selector"` for backward compatibility.

**Tech Stack:** Vanilla JS (Chrome MV3 content scripts), no build step, no dependencies.

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

## Phase 1: Extract core/ utilities (Tasks 1-4)

These have zero dependencies on scanning/filling code. Extracting them first gives all later files a stable foundation.

### Task 1: Create core/dom.js

**Files:**
- Create: `extension/core/dom.js`
- Reference: `extension/content.js:66-120` (delay, smartScroll, withRetry), `extension/content.js:410-487` (isFieldVisible, resolveSelector)

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p extension/core extension/scanners extension/fillers extension/detectors extension/ai extension/persistence
```

- [ ] **Step 2: Create core/dom.js with namespace registration**

Extract these functions from `content.js` into `extension/core/dom.js`:

```js
// extension/core/dom.js — DOM interaction primitives
// Changes when: DOM interaction primitives change
window.JobPulse = window.JobPulse || {};

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function smartScroll(el) {
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);
  return scrollWait;
}

async function withRetry(fn, maxRetries = 2, retryDelayMs = 500) {
  let lastResult;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    lastResult = await fn();
    if (lastResult.success) return lastResult;
    const isRetryable = lastResult.error &&
      (lastResult.error.includes("not found") ||
       lastResult.error.includes("No options") ||
       lastResult.error.includes("not visible"));
    if (!isRetryable) return lastResult;
    if (attempt < maxRetries) await delay(retryDelayMs);
  }
  lastResult.retries_exhausted = true;
  return lastResult;
}

function isFieldVisible(el) {
  const style = window.getComputedStyle(el);
  if (style.display === "none") return false;
  if (style.visibility === "hidden") return false;
  if (parseFloat(style.opacity) === 0) return false;
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;
  if (rect.top < -9000 || rect.left < -9000) return false;
  if (el.getAttribute("aria-hidden") === "true" && el.tabIndex === -1) return false;
  return true;
}

function resolveSelector(selector) {
  const fixed = selector.replace(/#(\d[^\s\[>+~,]*)/g, (_, id) => `[id="${id}"]`);
  if (fixed.includes(">>")) {
    const parts = fixed.split(">>");
    let el = document.querySelector(parts[0].trim());
    for (let i = 1; i < parts.length && el; i++) {
      el = (el.shadowRoot || el).querySelector(parts[i].trim());
    }
    return el;
  }
  return document.querySelector(fixed);
}

window.JobPulse.dom = { delay, smartScroll, withRetry, isFieldVisible, resolveSelector };
```

- [ ] **Step 3: Verify file is under 80 lines**

```bash
wc -l extension/core/dom.js
```

Expected: ~65 lines.

- [ ] **Step 4: Commit**

```bash
git add extension/core/dom.js
git commit -m "refactor(ext): extract core/dom.js — delay, scroll, retry, visibility, selector"
```

---

### Task 2: Create core/form.js

**Files:**
- Create: `extension/core/form.js`
- Reference: `extension/content.js:430-468` (setNativeValue, verifyFieldValue), `extension/content.js:493-526` (normalizeText, fuzzyMatchOption, ABBREVIATIONS)

- [ ] **Step 1: Create core/form.js**

```js
// extension/core/form.js — Form value handling and fuzzy matching
// Changes when: form value handling or matching logic changes
window.JobPulse = window.JobPulse || {};

function setNativeValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  if (descriptor && descriptor.set) {
    descriptor.set.call(el, value);
  } else {
    el.value = value;
  }
}

function verifyFieldValue(el, intended) {
  if (!el) return false;
  const tag = el.tagName.toLowerCase();
  if (tag === "select") {
    const selected = el.options[el.selectedIndex];
    return selected && (
      normalizeText(selected.text) === normalizeText(intended) ||
      normalizeText(selected.value) === normalizeText(intended)
    );
  }
  if (el.type === "radio") return el.checked;
  if (el.type === "checkbox") {
    const want = intended === "true" || intended === true || intended === "yes";
    return el.checked === want;
  }
  return (el.value || "") === intended ||
    (el.value || "").includes(intended.substring(0, 10));
}

const ABBREVIATIONS = {
  "uk": "united kingdom", "us": "united states",
  "usa": "united states of america", "nyc": "new york city",
  "sf": "san francisco", "la": "los angeles",
  "phd": "doctor of philosophy", "msc": "master of science",
  "bsc": "bachelor of science",
};

function normalizeText(text) {
  return (text || "").toLowerCase().trim().replace(/[.,;:!?]+$/, "");
}

function fuzzyMatchOption(value, options) {
  const norm = normalizeText(value);
  const expanded = ABBREVIATIONS[norm] || norm;
  for (const opt of options) { if (normalizeText(opt) === expanded) return opt; }
  for (const opt of options) { if (normalizeText(opt).startsWith(expanded)) return opt; }
  for (const opt of options) { if (normalizeText(opt).includes(expanded)) return opt; }
  for (const opt of options) {
    if (expanded.includes(normalizeText(opt)) && normalizeText(opt).length > 2) return opt;
  }
  return null;
}

window.JobPulse.form = {
  setNativeValue, verifyFieldValue, normalizeText, fuzzyMatchOption, ABBREVIATIONS,
};
```

- [ ] **Step 2: Verify file is under 80 lines**

```bash
wc -l extension/core/form.js
```

Expected: ~65 lines.

- [ ] **Step 3: Commit**

```bash
git add extension/core/form.js
git commit -m "refactor(ext): extract core/form.js — native value, verify, fuzzy match"
```

---

### Task 3: Create core/timing.js

**Files:**
- Create: `extension/core/timing.js`
- Reference: `extension/content.js:22-79` (behaviorProfile, calibration, getFieldGap)

- [ ] **Step 1: Create core/timing.js**

```js
// extension/core/timing.js — Human-like interaction timing and calibration
// Changes when: anti-detection timing or typing speed changes
window.JobPulse = window.JobPulse || {};

const behaviorProfile = {
  avg_typing_speed: 80,
  typing_variance: 0.3,
  scroll_speed: 400,
  reading_pause: 1.0,
  field_to_field_gap: 500,
  click_offset: { x: 0, y: 0 },
  calibrated: false,
  keystrokes: 0,
  clicks: 0,
};

// Restore saved profile from previous sessions
chrome.storage.local.get("behaviorProfile", (data) => {
  if (data.behaviorProfile) Object.assign(behaviorProfile, data.behaviorProfile);
});

// Passive calibration: learn from real user typing speed
document.addEventListener("keydown", () => {
  const now = performance.now();
  if (behaviorProfile._lastKey) {
    const gap = now - behaviorProfile._lastKey;
    if (gap > 20 && gap < 500) {
      behaviorProfile.avg_typing_speed =
        behaviorProfile.avg_typing_speed * 0.95 + gap * 0.05;
    }
  }
  behaviorProfile._lastKey = now;
  behaviorProfile.keystrokes++;
  if (behaviorProfile.keystrokes > 500 && !behaviorProfile.calibrated) {
    behaviorProfile.calibrated = true;
    chrome.storage.local.set({ behaviorProfile });
  }
}, { passive: true });

document.addEventListener("click", () => { behaviorProfile.clicks++; }, { passive: true });

function getFieldGap(labelText) {
  const len = (labelText || "").length;
  if (len < 10) return 300 + Math.random() * 200;
  if (len < 40) return 500 + Math.random() * 300;
  if (len < 100) return 800 + Math.random() * 500;
  return 1200 + Math.random() * 500;
}

function getTypingDelay() {
  return behaviorProfile.avg_typing_speed *
    (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
}

window.JobPulse.timing = { behaviorProfile, getFieldGap, getTypingDelay };
```

- [ ] **Step 2: Commit**

```bash
git add extension/core/timing.js
git commit -m "refactor(ext): extract core/timing.js — behavior profile, calibration, field gap"
```

---

### Task 4: Create core/cursor.js

**Files:**
- Create: `extension/core/cursor.js`
- Reference: `extension/content.js:298-408` (visual cursor, bezier curve)

- [ ] **Step 1: Create core/cursor.js**

```js
// extension/core/cursor.js — Visual cursor overlay for automation feedback
// Changes when: visual feedback appearance or animation changes
window.JobPulse = window.JobPulse || {};

let _cursor = null;

function ensureCursor() {
  if (_cursor && document.body.contains(_cursor)) return _cursor;
  _cursor = document.createElement("div");
  _cursor.id = "jobpulse-cursor";
  _cursor.style.cssText = `
    position: fixed; z-index: 2147483647; pointer-events: none;
    width: 20px; height: 20px; border-radius: 50%;
    background: rgba(59, 130, 246, 0.7);
    border: 2px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 12px rgba(59, 130, 246, 0.5), 0 0 4px rgba(0,0,0,0.3);
    transition: left 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                top 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                transform 0.15s ease;
    left: -100px; top: -100px; transform: translate(-50%, -50%);
  `;
  document.body.appendChild(_cursor);
  return _cursor;
}

function bezierCurve(x0, y0, x1, y1, steps = 18) {
  const dx = x1 - x0, dy = y1 - y0;
  const distance = Math.sqrt(dx * dx + dy * dy);
  const perpX = -dy / (distance || 1), perpY = dx / (distance || 1);
  const curvature = (Math.random() - 0.5) * distance * 0.3;
  const overshoot = 1.0 + (Math.random() * 0.08 - 0.02);
  const cp1x = x0 + dx * 0.3 + perpX * curvature;
  const cp1y = y0 + dy * 0.3 + perpY * curvature;
  const cp2x = x0 + dx * 0.7 * overshoot + perpX * curvature * 0.3;
  const cp2y = y0 + dy * 0.7 * overshoot + perpY * curvature * 0.3;
  const points = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps, u = 1 - t;
    points.push({
      x: u*u*u*x0 + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*x1,
      y: u*u*u*y0 + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*y1,
    });
  }
  return points;
}

async function moveCursorTo(el) {
  const { delay } = window.JobPulse.dom;
  const cursor = ensureCursor();
  const rect = el.getBoundingClientRect();
  const targetX = rect.left + rect.width / 2;
  const targetY = rect.top + rect.height / 2;
  const currentX = parseFloat(cursor.style.left) || -100;
  const currentY = parseFloat(cursor.style.top) || -100;
  const dist = Math.sqrt((targetX - currentX) ** 2 + (targetY - currentY) ** 2);
  if (dist < 30) {
    cursor.style.left = targetX + "px"; cursor.style.top = targetY + "px";
    cursor.style.display = "block"; await delay(50); return;
  }
  cursor.style.transition = "transform 0.15s ease";
  const points = bezierCurve(currentX, currentY, targetX, targetY);
  cursor.style.display = "block";
  for (let i = 0; i < points.length; i++) {
    cursor.style.left = points[i].x + "px"; cursor.style.top = points[i].y + "px";
    const t = i / points.length;
    await delay(8 + 20 * (1 - Math.abs(2 * t - 1)) + Math.random() * 5);
  }
}

async function cursorClickFlash() {
  const { delay } = window.JobPulse.dom;
  const cursor = ensureCursor();
  cursor.style.transform = "translate(-50%, -50%) scale(0.6)"; await delay(100);
  cursor.style.transform = "translate(-50%, -50%) scale(1.0)"; await delay(100);
}

function highlightElement(el) {
  const prev = el.style.outline, prevT = el.style.transition;
  el.style.transition = "outline 0.2s ease";
  el.style.outline = "2px solid rgba(59, 130, 246, 0.8)";
  setTimeout(() => { el.style.outline = prev; el.style.transition = prevT; }, 1500);
}

function hideCursor() { if (_cursor) _cursor.style.display = "none"; }

window.JobPulse.cursor = {
  ensureCursor, moveCursorTo, cursorClickFlash, highlightElement, hideCursor, bezierCurve,
};
```

- [ ] **Step 2: Commit**

```bash
git add extension/core/cursor.js
git commit -m "refactor(ext): extract core/cursor.js — visual cursor, bezier curve, highlight"
```

---

## Phase 2: Extract scanners/ (Tasks 5-7)

### Task 5: Create scanners/field_context.js

**Files:**
- Create: `extension/scanners/field_context.js`
- Reference: `extension/content.js:133-292` (extractFieldContext)

- [ ] **Step 1: Create scanners/field_context.js**

Extract `extractFieldContext()` verbatim from content.js lines 133-292. Wrap with namespace:

```js
// extension/scanners/field_context.js — Exhaustive DOM label extraction
// Changes when: label extraction heuristics change
window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

function extractFieldContext(el) {
  // COPY VERBATIM from content.js lines 134-292
  // (the full extractFieldContext function body)
  // Only change: reference isFieldVisible via JP.dom.isFieldVisible if needed
  // No other modifications.
}

window.JobPulse.scanners.fieldContext = { extractFieldContext };
```

Copy the **entire** `extractFieldContext` function from `content.js:133-292` without modification. The only addition is the namespace wrapper.

- [ ] **Step 2: Commit**

```bash
git add extension/scanners/field_context.js
git commit -m "refactor(ext): extract scanners/field_context.js — DOM label extraction"
```

---

### Task 6: Create scanners/field_info.js and scanners/scan_dom.js

**Files:**
- Create: `extension/scanners/field_info.js`
- Create: `extension/scanners/scan_dom.js`
- Reference: `extension/content.js:536-816` (extractFieldInfo, deepScan, scanFormGroups)

- [ ] **Step 1: Create scanners/field_info.js**

Extract `extractFieldInfo()` from content.js lines 536-694. Update internal calls to use namespace:

```js
// extension/scanners/field_info.js — Build FieldInfo + CSS selector from element
// Changes when: FieldInfo schema or selector-building logic changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

function extractFieldInfo(el, iframeIndex) {
  // COPY VERBATIM from content.js lines 537-694
  // Replace: extractFieldContext(el) → JP.scanners.fieldContext.extractFieldContext(el)
  // Replace: isFieldVisible(next) → JP.dom.isFieldVisible(next)
  const JP = window.JobPulse;
  // ... rest of function
}

window.JobPulse.scanners.fieldInfo = { extractFieldInfo };
```

- [ ] **Step 2: Create scanners/scan_dom.js**

Extract `deepScan()` and `scanFormGroups()` from content.js lines 704-816:

```js
// extension/scanners/scan_dom.js — Recursive DOM/shadow/iframe traversal + form groups
// Changes when: DOM traversal strategy or form group detection changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

function deepScan(root, depth, iframeIndex) {
  const JP = window.JobPulse;
  root = root || document;
  depth = depth || 0;
  iframeIndex = iframeIndex === undefined ? null : iframeIndex;
  const fields = [];
  if (depth > 5) return fields;

  const selector =
    "input:not([type='hidden']), select, textarea, [contenteditable='true'], " +
    "[role='listbox'], [role='combobox'], [role='radiogroup'], [role='switch'], [role='textbox']";
  for (const el of root.querySelectorAll(selector)) {
    if (!JP.dom.isFieldVisible(el) && el.type !== "file") continue;
    fields.push(JP.scanners.fieldInfo.extractFieldInfo(el, iframeIndex));
  }

  root.querySelectorAll("*").forEach((el) => {
    if (el.shadowRoot) fields.push(...deepScan(el.shadowRoot, depth + 1, iframeIndex));
  });

  root.querySelectorAll("iframe").forEach((iframe, idx) => {
    try {
      if (iframe.contentDocument) fields.push(...deepScan(iframe.contentDocument, depth + 1, idx));
    } catch (_) {}
  });

  return fields;
}

function scanFormGroups(rootSelector) {
  // COPY VERBATIM from content.js lines 743-816
  // Replace internal calls: resolveSelector → JP.dom.resolveSelector
  //                         extractFieldInfo → JP.scanners.fieldInfo.extractFieldInfo
  //                         isFieldVisible → JP.dom.isFieldVisible
  const JP = window.JobPulse;
  const root = rootSelector ? JP.dom.resolveSelector(rootSelector) : document;
  if (!root) return [];
  // ... rest of function verbatim
}

window.JobPulse.scanners.dom = { deepScan, scanFormGroups };
```

- [ ] **Step 3: Commit**

```bash
git add extension/scanners/field_info.js extension/scanners/scan_dom.js
git commit -m "refactor(ext): extract scanners/field_info.js + scan_dom.js"
```

---

### Task 7: Create scanners/label_scan.js (NEW CODE)

**Files:**
- Create: `extension/scanners/label_scan.js`

- [ ] **Step 1: Create scanners/label_scan.js**

```js
// extension/scanners/label_scan.js — Role-based field discovery using accessible names
// Changes when: accessible name extraction or label strategy adds field types
window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

function getAccessibleName(el) {
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy.split(/\s+/)
      .map(id => document.getElementById(id)?.textContent?.trim())
      .filter(Boolean).join(" ");
    if (text) return clean(text);
  }
  if (el.id) {
    const labelFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (labelFor) return clean(labelFor.textContent);
  }
  const wrapping = el.closest("label");
  if (wrapping) {
    const clone = wrapping.cloneNode(true);
    clone.querySelectorAll("input,select,textarea").forEach(c => c.remove());
    const t = clean(clone.textContent);
    if (t) return t;
  }
  if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
  if (el.placeholder) return clean(el.placeholder);
  return "";
}

function buildLocatorHint(el) {
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.name || null,
    type: el.type || null,
    ariaLabel: el.getAttribute("aria-label") || null,
    index: el.id ? null : [...(el.parentElement?.children || [])].filter(
      c => c.tagName === el.tagName
    ).indexOf(el),
  };
}

function scan(root) {
  const JP = window.JobPulse;
  const fields = [];
  root = root || document;

  // Text inputs (excludes hidden, submit, checkbox, radio, file)
  for (const el of root.querySelectorAll(
    'input:not([type="hidden"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"]):not([type="file"])'
  )) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const label = getAccessibleName(el);
    if (!label) continue;
    fields.push({
      label, type: (el.type || "text").toLowerCase(),
      value: el.value || "", required: el.required || el.getAttribute("aria-required") === "true",
      locator_hint: buildLocatorHint(el),
    });
  }

  // Native <select>
  for (const el of root.querySelectorAll("select")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const label = getAccessibleName(el);
    const options = [...el.options].map(o => o.text.trim())
      .filter(t => t && !/^select|^choose|^--/i.test(t));
    fields.push({ label: label || "", type: "select", options, value: el.value,
      locator_hint: buildLocatorHint(el) });
  }

  // Textareas
  for (const el of root.querySelectorAll("textarea")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "textarea", value: el.value || "",
      required: el.required || el.getAttribute("aria-required") === "true",
      locator_hint: buildLocatorHint(el) });
  }

  // Checkboxes
  for (const el of root.querySelectorAll('input[type="checkbox"]')) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "checkbox", checked: el.checked,
      locator_hint: buildLocatorHint(el) });
  }

  // Radio groups (by name attribute)
  const radioNames = new Set();
  for (const el of root.querySelectorAll('input[type="radio"]')) {
    if (!JP.dom.isFieldVisible(el) || !el.name || radioNames.has(el.name)) continue;
    radioNames.add(el.name);
    const radios = root.querySelectorAll(`input[type="radio"][name="${el.name}"]`);
    const options = [...radios].map(r => getAccessibleName(r)).filter(Boolean);
    const group = el.closest('[role="radiogroup"]') || el.closest("fieldset");
    const groupLabel = group ? getAccessibleName(group) || clean(group.querySelector("legend")?.textContent) : "";
    fields.push({ label: groupLabel || options[0] || el.name, type: "radio", options,
      locator_hint: { name: el.name } });
  }

  // File inputs
  for (const el of root.querySelectorAll('input[type="file"]')) {
    fields.push({ label: getAccessibleName(el) || "file upload", type: "file",
      locator_hint: buildLocatorHint(el) });
  }

  // Contenteditable
  for (const el of root.querySelectorAll('[contenteditable="true"]')) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "contenteditable",
      value: (el.innerText || "").trim(), locator_hint: buildLocatorHint(el) });
  }

  return fields;
}

window.JobPulse.scanners.label = { scan, getAccessibleName, buildLocatorHint };
```

- [ ] **Step 2: Commit**

```bash
git add extension/scanners/label_scan.js
git commit -m "feat(ext): add scanners/label_scan.js — role-based field discovery"
```

---

## Phase 3: Extract fillers/ (Tasks 8-15)

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

---

## Phase 4: Extract detectors/ (Tasks 16-19)

### Task 16: Create detectors/snapshot.js

**Files:**
- Create: `extension/detectors/snapshot.js`
- Reference: `extension/content.js:856-958` (buildSnapshot + button extraction + modal/progress)

- [ ] **Step 1: Create detectors/snapshot.js**

```js
// extension/detectors/snapshot.js — Build full PageSnapshot for Python
// Changes when: PageSnapshot schema or page state detection changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

function buildSnapshot() {
  const JP = window.JobPulse;
  const fields = JP.scanners.dom.deepScan();
  // COPY the button extraction, modal detection, progress parsing, form groups
  // from content.js:862-958
  // Replace: deepScan → JP.scanners.dom.deepScan,
  //          scanFormGroups → JP.scanners.dom.scanFormGroups,
  //          detectVerificationWall → JP.detectors.verification.detectVerificationWall
  // Return the full snapshot object
}

window.JobPulse.detectors.snapshot = { buildSnapshot };
```

Copy the entire `buildSnapshot()` function body from content.js:856-958, replacing internal function calls with namespace equivalents.

- [ ] **Step 2: Commit**

```bash
git add extension/detectors/snapshot.js
git commit -m "refactor(ext): extract detectors/snapshot.js — PageSnapshot builder"
```

---

### Task 17: Create detectors/job_extract.js

**Files:**
- Create: `extension/detectors/job_extract.js`
- Reference: `extension/content.js:2029-2158` (extractJobCards, extractJDText)

- [ ] **Step 1: Create detectors/job_extract.js**

```js
// extension/detectors/job_extract.js — Job listing card and JD text extraction
// Changes when: job listing page structure changes (Indeed, Greenhouse, etc.)
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

function extractJobCards() {
  // COPY VERBATIM from content.js:2029-2123
  // No internal dependencies — pure DOM queries
}

function extractJDText() {
  // COPY VERBATIM from content.js:2130-2158
  // No internal dependencies
}

window.JobPulse.detectors.jobExtract = { extractJobCards, extractJDText };
```

- [ ] **Step 2: Commit**

```bash
git add extension/detectors/job_extract.js
git commit -m "refactor(ext): extract detectors/job_extract.js — job cards + JD text"
```

---

### Task 18: Create detectors/verification.js

**Files:**
- Create: `extension/detectors/verification.js`
- Reference: `extension/content.js:823-850` (detectVerificationWall)

- [ ] **Step 1: Create detectors/verification.js**

```js
// extension/detectors/verification.js — CAPTCHA and verification wall detection
// Changes when: new CAPTCHA types appear
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

function detectVerificationWall() {
  const captchaSelectors = [
    { sel: "#challenge-running, .cf-turnstile, #cf-challenge-running", type: "cloudflare", conf: 0.95 },
    { sel: ".g-recaptcha, #recaptcha-anchor, [data-sitekey]", type: "recaptcha", conf: 0.90 },
    { sel: ".h-captcha", type: "hcaptcha", conf: 0.90 },
  ];
  for (const { sel, type, conf } of captchaSelectors) {
    if (document.querySelector(sel)) return { wall_type: type, confidence: conf, details: sel };
  }
  for (const frame of document.querySelectorAll("iframe")) {
    const src = frame.src || "";
    if (src.includes("challenges.cloudflare.com")) return { wall_type: "cloudflare", confidence: 0.95, details: src };
    if (src.includes("google.com/recaptcha")) return { wall_type: "recaptcha", confidence: 0.90, details: src };
    if (src.includes("hcaptcha.com")) return { wall_type: "hcaptcha", confidence: 0.90, details: src };
  }
  const body = document.body?.innerText?.toLowerCase() || "";
  if (/verify you are human|are you a robot|confirm you're not a robot/.test(body))
    return { wall_type: "text_challenge", confidence: 0.85, details: "text match" };
  if (/access denied|403 forbidden|you have been blocked/.test(body))
    return { wall_type: "http_block", confidence: 0.80, details: "text match" };
  return null;
}

window.JobPulse.detectors.verification = { detectVerificationWall };
```

- [ ] **Step 2: Commit**

```bash
git add extension/detectors/verification.js
git commit -m "refactor(ext): extract detectors/verification.js — CAPTCHA detection"
```

---

### Task 19: Create detectors/native.js (NEW CODE)

**Files:**
- Create: `extension/detectors/native.js`

- [ ] **Step 1: Create detectors/native.js**

```js
// extension/detectors/native.js — Page classification and navigation for label strategy
// Changes when: page classification or navigation button detection changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

function isConfirmationPage() {
  const body = (document.body?.innerText || "").toLowerCase().slice(0, 2000);
  return ["thank you for applying", "application has been received",
    "application submitted", "successfully submitted",
    "application is complete", "we have received your application",
  ].some(phrase => body.includes(phrase));
}

function isSubmitPage() {
  for (const name of ["Submit Application", "Submit", "Apply", "Apply Now"]) {
    for (const btn of document.querySelectorAll("button, input[type='submit'], [role='button']")) {
      const text = (btn.textContent || btn.value || "").trim();
      if (text.toLowerCase().includes(name.toLowerCase()) && window.JobPulse.dom.isFieldVisible(btn))
        return true;
    }
  }
  return false;
}

function detectNavigationButton() {
  const JP = window.JobPulse;
  const groups = [
    { action: "submit", names: ["Submit Application", "Submit", "Apply", "Apply Now"] },
    { action: "next", names: ["Save & Continue", "Continue", "Next", "Proceed", "Save and Continue"] },
  ];
  for (const { action, names } of groups) {
    for (const name of names) {
      for (const btn of document.querySelectorAll("button, input[type='submit'], [role='button']")) {
        const text = (btn.textContent || btn.value || "").trim();
        if (text.toLowerCase().includes(name.toLowerCase()) && JP.dom.isFieldVisible(btn) && !btn.disabled)
          return { action, element: btn, text };
      }
      if (action === "submit") {
        for (const link of document.querySelectorAll("a")) {
          const text = (link.textContent || "").trim();
          if (text.toLowerCase().includes(name.toLowerCase()) && JP.dom.isFieldVisible(link))
            return { action: "next", element: link, text };
        }
      }
    }
  }
  return null;
}

function detectProgress() {
  const text = document.body?.innerText || "";
  const match = text.match(/(?:step|page)\s+(\d+)\s+(?:of|\/)\s+(\d+)/i);
  if (match) {
    const [, current, total] = [null, parseInt(match[1]), parseInt(match[2])];
    if (current >= 1 && current <= total && total <= 20) return { current, total };
  }
  return null;
}

function hasUnfilledRequired() {
  const JP = window.JobPulse;
  for (const el of document.querySelectorAll("[required], [aria-required='true']")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const val = el.value || el.textContent || "";
    if (!val.trim() && el.type !== "hidden") return true;
  }
  return false;
}

window.JobPulse.detectors.native = {
  isConfirmationPage, isSubmitPage, detectNavigationButton, detectProgress, hasUnfilledRequired,
};
```

- [ ] **Step 2: Commit**

```bash
git add extension/detectors/native.js
git commit -m "feat(ext): add detectors/native.js — label strategy page detection"
```

---

## Phase 5: Extract remaining modules (Tasks 20-21)

### Task 20: Create ai/gemini.js and persistence/form_progress.js

**Files:**
- Create: `extension/ai/gemini.js`
- Create: `extension/persistence/form_progress.js`
- Reference: `extension/content.js:1969-2018` (Gemini Nano), `extension/content.js:2475-2515` (form progress)

- [ ] **Step 1: Create ai/gemini.js**

```js
// extension/ai/gemini.js — Chrome built-in Gemini Nano for local field analysis
// Changes when: local AI prompts or Chrome AI API changes
window.JobPulse = window.JobPulse || {};

async function analyzeFieldLocally(question, inputType, options) {
  // COPY VERBATIM from content.js:1969-1993
}

async function writeShortAnswer(question) {
  // COPY VERBATIM from content.js:1999-2018
}

window.JobPulse.ai = { analyzeFieldLocally, writeShortAnswer };
```

- [ ] **Step 2: Create persistence/form_progress.js**

```js
// extension/persistence/form_progress.js — MV3 session storage for form state
// Changes when: MV3 state persistence strategy changes
window.JobPulse = window.JobPulse || {};

function saveFormProgress(url, progress) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  chrome.storage.session.set({ [key]: { url, ...progress, timestamp: Date.now() } }).catch(() => {});
}

async function getFormProgress(url) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  try {
    const data = await chrome.storage.session.get(key);
    return data[key] || null;
  } catch (_) { return null; }
}

function clearFormProgress(url) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  chrome.storage.session.remove(key).catch(() => {});
}

window.JobPulse.persistence = { saveFormProgress, getFormProgress, clearFormProgress };
```

- [ ] **Step 3: Commit**

```bash
git add extension/ai/gemini.js extension/persistence/form_progress.js
git commit -m "refactor(ext): extract ai/gemini.js + persistence/form_progress.js"
```

---

### Task 21: Update protocol.js with label strategy message types

**Files:**
- Modify: `extension/protocol.js`

- [ ] **Step 1: Add new message types**

Add these entries to the `MSG` object in `extension/protocol.js` after the existing v2 form engine commands:

```js
  // Label strategy commands
  CMD_SCAN_FIELDS: "scan_fields",
  CMD_FILL_BY_LABEL: "fill_by_label",
  CMD_DETECT_PAGE: "detect_page",
  CMD_CLICK_NAVIGATION: "click_navigation",
  CMD_CHECK_CONSENT: "check_consent",
  CMD_UPLOAD_FILES: "upload_files",
```

- [ ] **Step 2: Commit**

```bash
git add extension/protocol.js
git commit -m "feat(ext): add label strategy message types to protocol.js"
```

---

## Phase 6: Rewrite content.js as dispatcher (Task 22)

### Task 22: Replace content.js with thin dispatcher

**Files:**
- Modify: `extension/content.js` (complete rewrite)

This is the critical step. The new `content.js` replaces the 2500-line monolith with a ~70-line dispatcher that routes messages to the extracted modules.

- [ ] **Step 1: Back up the original content.js**

```bash
cp extension/content.js extension/content.js.bak
```

- [ ] **Step 2: Write the new content.js**

Replace the entire `extension/content.js` with:

```js
// extension/content.js — Message dispatcher + MutationObserver
// Routes actions to scanner/filler/detector modules based on strategy param.
// Default strategy: "selector" (backward compatible with all existing Python code).
window.JobPulse = window.JobPulse || {};
const JP = window.JobPulse;

// ── Message handler ──
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const { action, payload } = msg;
  if (!action) return false;

  const strategy = payload?.strategy || "selector";

  (async () => {
    let result;
    try {
      switch (action) {
        case "ping": result = { success: true, alive: true }; break;

        // ── Scanning ──
        case "get_snapshot":
          result = strategy === "label"
            ? { success: true, fields: JP.scanners.label.scan() }
            : JP.detectors.snapshot.buildSnapshot();
          break;
        case "scan_fields":
          result = { success: true, fields: JP.scanners.label.scan() };
          break;

        // ── Page detection ──
        case "detect_page":
          result = {
            is_confirmation: JP.detectors.native.isConfirmationPage(),
            is_submit_page: JP.detectors.native.isSubmitPage(),
            navigation_button: (() => { const b = JP.detectors.native.detectNavigationButton(); return b ? { action: b.action, text: b.text } : null; })(),
            progress: JP.detectors.native.detectProgress(),
            has_unfilled_required: JP.detectors.native.hasUnfilledRequired(),
            verification_wall: JP.detectors.verification.detectVerificationWall(),
          };
          break;

        // ── Label strategy filling ──
        case "fill_by_label":
          result = await JP.dom.withRetry(() => JP.fillers.labelFill.fillByLabel(payload.label, payload.value, payload.type));
          break;

        // ── Navigation click ──
        case "click_navigation": {
          const nav = JP.detectors.native.detectNavigationButton();
          if (!nav) { result = { clicked: "" }; break; }
          if (nav.action === "submit" && payload.dry_run) { result = { clicked: "dry_run_stop" }; break; }
          await JP.cursor.moveCursorTo(nav.element);
          nav.element.click();
          await JP.dom.delay(2000);
          result = { clicked: nav.action === "submit" ? "submitted" : "next" };
          break;
        }

        // ── Selector strategy filling ──
        case "fill":
          result = strategy === "label"
            ? await JP.dom.withRetry(() => JP.fillers.labelFill.fillByLabel(payload.label, payload.value, payload.type))
            : await JP.dom.withRetry(() => JP.fillers.text.fillField(payload.selector, payload.value));
          break;
        case "select":
          result = await JP.dom.withRetry(() => JP.fillers.select.selectOption(payload.selector, payload.value));
          break;
        case "check":
          result = await JP.dom.withRetry(() => JP.fillers.select.checkBox(payload.selector, payload.value));
          break;
        case "upload":
          result = await JP.fillers.simple.uploadFile(payload.selector, payload.file_base64, payload.file_name, payload.mime_type);
          break;
        case "click":
          result = await JP.fillers.actions.clickElement(payload.selector);
          break;
        case "fill_radio_group":
          result = await JP.dom.withRetry(() => JP.fillers.radio.fillRadioGroup(payload.selector, payload.value));
          break;
        case "fill_custom_select":
          result = await JP.fillers.dropdown.fillCustomSelect(payload.selector, payload.value);
          break;
        case "fill_autocomplete":
          result = await JP.dom.withRetry(() => JP.fillers.dropdown.fillAutocomplete(payload.selector, payload.value));
          break;
        case "fill_combobox":
          result = await JP.fillers.combobox.fillCombobox(payload.selector, payload.value);
          break;
        case "fill_tag_input":
          result = await JP.dom.withRetry(() => JP.fillers.simple.fillTagInput(payload.selector, payload.values || []));
          break;
        case "fill_date":
          result = await JP.fillers.simple.fillDate(payload.selector, payload.value);
          break;
        case "fill_contenteditable":
          result = await JP.dom.withRetry(async () => {
            const el = JP.dom.resolveSelector(payload.selector);
            return el ? await JP.fillers.text.fillContentEditable(el, payload.value)
              : { success: false, error: "Element not found: " + payload.selector };
          });
          break;

        // ── Actions ──
        case "scroll_to": result = await JP.fillers.actions.scrollTo(payload.selector); break;
        case "wait_for_selector": result = await JP.fillers.actions.waitForSelector(payload.selector, payload.timeout_ms); break;
        case "force_click": result = await JP.fillers.actions.forceClick(payload.selector); break;

        // ── Validation ──
        case "check_consent_boxes": result = await JP.fillers.select.checkConsentBoxes(payload.root_selector || null); break;
        case "scan_validation_errors": result = JP.fillers.validate.scanValidationErrors(); break;
        case "rescan_after_fill": result = await JP.fillers.validate.rescanAfterFill(payload.selector); break;
        case "reveal_options": result = await JP.fillers.combobox.revealOptions(payload.selector); break;

        // ── Scanners ──
        case "scan_form_groups": result = { success: true, groups: JP.scanners.dom.scanFormGroups(payload.root_selector || null) }; break;
        case "get_field_context": {
          const el = JP.dom.resolveSelector(payload.selector);
          result = el ? { success: true, context: JP.scanners.fieldInfo.extractFieldInfo(el, null) }
            : { success: false, error: "Element not found" };
          break;
        }

        // ── Job extraction ──
        case "scan_jd": result = { success: true, jd_text: JP.detectors.jobExtract.extractJDText() }; break;
        case "scan_cards": result = { success: true, jobs: JP.detectors.jobExtract.extractJobCards() }; break;

        // ── AI ──
        case "analyze_field": {
          let answer = await JP.ai.analyzeFieldLocally(payload.question, payload.input_type, payload.options || []);
          if (!answer && payload.input_type === "textarea") answer = await JP.ai.writeShortAnswer(payload.question);
          result = { success: !!answer, answer: answer || "" };
          break;
        }

        // ── Persistence ──
        case "save_form_progress": JP.persistence.saveFormProgress(payload.url || location.href, payload.progress || {}); result = { success: true }; break;
        case "get_form_progress": result = await JP.persistence.getFormProgress(payload.url || location.href) || { success: false, error: "No saved progress" }; break;
        case "clear_form_progress": JP.persistence.clearFormProgress(payload.url || location.href); result = { success: true }; break;

        // ── wait_for_apply (special) ──
        case "wait_for_apply": {
          const applyRe = /easy\s*apply|apply\s*(now|for\s*this)?|start\s*application|submit\s*application/i;
          const maxWait = payload.timeout_ms || 10000;
          let elapsed = 0, snap = null;
          while (elapsed < maxWait) {
            snap = JP.detectors.snapshot.buildSnapshot();
            if (snap.buttons.some(b => applyRe.test(b.text)) ||
                document.querySelector("a[class*='apply'], a[aria-label*='Apply'], a[href*='apply']")) break;
            await JP.dom.delay(500); elapsed += 500;
          }
          if (!snap) snap = JP.detectors.snapshot.buildSnapshot();
          result = { ...snap, waited_ms: elapsed };
          break;
        }

        // ── Element bounds (for screenshots) ──
        case "element_bounds": {
          const el = JP.dom.resolveSelector(payload.selector);
          if (!el) { result = { success: false, error: "Element not found: " + payload.selector }; break; }
          await JP.dom.smartScroll(el); await JP.dom.delay(200);
          const rect = el.getBoundingClientRect();
          result = { success: true, bounds: {
            x: Math.round(rect.x * devicePixelRatio), y: Math.round(rect.y * devicePixelRatio),
            width: Math.round(rect.width * devicePixelRatio), height: Math.round(rect.height * devicePixelRatio),
          }, dpr: devicePixelRatio };
          break;
        }

        default: result = { success: false, error: "Unknown action: " + action };
      }
    } catch (err) {
      result = { success: false, error: "Content script error: " + (err.message || String(err)) };
    }
    sendResponse(result);
  })();
  return true;
});

// ── MutationObserver — live DOM change detection ──
function safeSendMessage(msg) {
  if (!chrome.runtime?.id) return;
  try { chrome.runtime.sendMessage(msg).catch(() => {}); } catch (_) {}
}

let scanTimeout;
const observer = new MutationObserver(() => {
  clearTimeout(scanTimeout);
  scanTimeout = setTimeout(() => {
    safeSendMessage({ type: "mutation", payload: { snapshot: JP.detectors.snapshot.buildSnapshot() } });
  }, 500);
});

if (document.body) {
  observer.observe(document.body, {
    childList: true, subtree: true, attributes: true,
    attributeFilter: ["class", "style", "hidden", "disabled", "aria-hidden"],
  });
}

window.addEventListener("load", () => {
  setTimeout(() => {
    safeSendMessage({ type: "navigation", payload: { snapshot: JP.detectors.snapshot.buildSnapshot() } });
  }, 1000);
});
```

- [ ] **Step 3: Update manifest.json content_scripts**

Replace the `content_scripts` section in `extension/manifest.json`:

```json
"content_scripts": [{
  "matches": ["<all_urls>"],
  "js": [
    "core/dom.js", "core/form.js", "core/timing.js", "core/cursor.js",
    "scanners/field_context.js", "scanners/field_info.js", "scanners/scan_dom.js",
    "scanners/label_scan.js",
    "fillers/fill_text.js", "fillers/fill_select.js", "fillers/fill_radio.js",
    "fillers/fill_combobox.js", "fillers/fill_dropdown.js", "fillers/fill_simple.js",
    "fillers/fill_actions.js", "fillers/fill_validate.js",
    "fillers/label_fill.js", "fillers/label_fill_choice.js",
    "detectors/snapshot.js", "detectors/job_extract.js",
    "detectors/verification.js", "detectors/native.js",
    "ai/gemini.js", "persistence/form_progress.js",
    "content.js"
  ],
  "run_at": "document_idle",
  "all_frames": true
}]
```

- [ ] **Step 4: Verify no syntax errors by loading extension**

```
1. Open chrome://extensions
2. Click "Load unpacked" → select extension/ directory
3. Check for errors in the service worker console
4. Navigate to any page → check content script console for errors
```

- [ ] **Step 5: Commit**

```bash
git add extension/content.js extension/manifest.json
git commit -m "refactor(ext): rewrite content.js as thin dispatcher — 25 module architecture"
```

---

## Phase 7: Smoke test + cleanup (Task 23)

### Task 23: End-to-end smoke test and backup removal

**Files:**
- Remove: `extension/content.js.bak` (after verification)

- [ ] **Step 1: Test selector strategy (backward compat)**

Start the ext-bridge and send a `get_snapshot` command. Verify the response contains fields, buttons, and page_text_preview — same shape as before the refactor.

```bash
python -m jobpulse.runner ext-bridge
# In another terminal, or via Telegram:
python -m jobpulse.runner ralph-test https://boards.greenhouse.io/example
```

Expected: Same behavior as before. All existing Python code works without changes.

- [ ] **Step 2: Test label strategy**

Send a `scan_fields` command with `strategy: "label"`. Verify it returns `[{label, type, value, ...}]` instead of the full snapshot.

Send a `detect_page` command with `strategy: "label"`. Verify it returns `{is_confirmation, is_submit_page, ...}`.

- [ ] **Step 3: Remove backup**

```bash
rm extension/content.js.bak
git add -A extension/
git commit -m "chore(ext): remove content.js backup after successful smoke test"
```

---

## Summary

| Phase | Tasks | What it does |
|---|---|---|
| 1 | 1-4 | Extract `core/` utilities (dom, form, timing, cursor) |
| 2 | 5-7 | Extract `scanners/` + new label scanner |
| 3 | 8-15 | Extract `fillers/` + new label fillers |
| 4 | 16-19 | Extract `detectors/` + new native detector |
| 5 | 20-21 | Extract AI, persistence, protocol updates |
| 6 | 22 | Rewrite content.js as dispatcher + update manifest |
| 7 | 23 | Smoke test + cleanup |

**Total: 23 tasks, ~25 commits.** Extension works identically after each commit (no big-bang switchover). Label strategy becomes available after Task 22.
