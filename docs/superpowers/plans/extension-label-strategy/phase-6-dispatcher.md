# Phase 6: Rewrite content.js as dispatcher (Task 22)

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the 2500-line content.js monolith with a ~150-line thin dispatcher that routes messages to the extracted modules. Update manifest.json to load all 25 files in dependency order.

**Depends on:** ALL previous phases (1-5) must be complete.

**Spec:** `docs/superpowers/specs/2026-04-08-extension-label-strategy-design.md`

---

### Task 22: Replace content.js with thin dispatcher

**Files:**
- Modify: `extension/content.js` (complete rewrite)
- Modify: `extension/manifest.json` (content_scripts update)

This is the critical step. The new `content.js` replaces the 2500-line monolith with a ~150-line dispatcher that routes messages to the extracted modules.

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
