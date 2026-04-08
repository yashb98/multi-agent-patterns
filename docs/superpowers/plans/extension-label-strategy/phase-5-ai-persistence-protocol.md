# Phase 5: Extract remaining modules (Tasks 20-21)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract AI and persistence modules, update protocol with label strategy message types.

**Depends on:** Phase 1 (core/).

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

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
