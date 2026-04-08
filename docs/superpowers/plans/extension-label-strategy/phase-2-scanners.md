# Phase 2: Extract scanners/ (Tasks 5-7)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract field scanning and DOM traversal from content.js into `scanners/` modules, then add the new label-based scanner.

**Depends on:** Phase 1 (core/ modules must exist).

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

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
