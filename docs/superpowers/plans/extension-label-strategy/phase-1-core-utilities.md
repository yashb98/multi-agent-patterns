# Phase 1: Extract core/ utilities (Tasks 1-4)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract zero-dependency utility functions from content.js into `core/` modules. These have no dependencies on scanning/filling code — extracting them first gives all later files a stable foundation.

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

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
