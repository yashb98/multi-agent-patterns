# Phase 4: Extract detectors/ (Tasks 16-19)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract page detection, job extraction, and verification wall detection into `detectors/` modules, then add the new native page detector for the label strategy.

**Depends on:** Phase 1 (core/), Phase 2 (scanners/).

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

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
