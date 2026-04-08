# Extension Label Strategy — Design Spec

**Date:** 2026-04-08
**Goal:** Add a label-based form filling strategy to the Chrome extension alongside the existing selector-based approach, enabling A/B comparison between the two strategies within the same engine.

## Motivation

The Playwright native pipeline spec proposed role-based locators and accessible-name field discovery as improvements over the extension's snapshot-based approach. However, these improvements are not Playwright-specific — the extension can adopt the same patterns. By implementing both strategies in the same extension, we can A/B test them under identical conditions (same Chrome profile, same anti-detection, same timing) with the only variable being how fields are discovered and filled.

## Architecture: Strategy Pattern

**Grouping principle: each file has exactly ONE reason to change.** Files are grouped by "what changes together", not by arbitrary line count. Target: 60-120 lines per file, 25 files total.

```
extension/
├── content.js                    (~70)  — Message dispatcher + MutationObserver + strategy router
│
├── core/                         — Pure utilities, zero knowledge of scanning or filling
│   ├── dom.js                    (~90)  — delay, smartScroll, withRetry, isFieldVisible, resolveSelector
│   │                                      Changes when: DOM interaction primitives change
│   ├── form.js                   (~80)  — setNativeValue, verifyFieldValue, normalizeText, fuzzyMatchOption
│   │                                      Changes when: form value handling or matching logic changes
│   ├── timing.js                 (~65)  — BehaviorProfile, calibration listeners, getFieldGap, getTypingDelay
│   │                                      Changes when: anti-detection timing or typing speed changes
│   └── cursor.js                 (~80)  — ensureCursor, moveCursorTo, bezierCurve, clickFlash, highlight
│                                          Changes when: visual feedback appearance or animation changes
│
├── scanners/                     — Read-only field discovery, never mutate DOM
│   ├── field_context.js          (~100) — extractFieldContext (exhaustive DOM label extraction)
│   │                                      Changes when: label extraction heuristics change
│   ├── field_info.js             (~95)  — extractFieldInfo (build FieldInfo + CSS selector from element)
│   │                                      Changes when: FieldInfo schema or selector-building changes
│   ├── scan_dom.js               (~110) — deepScan (recursive DOM/shadow/iframe) + scanFormGroups
│   │                                      Changes when: DOM traversal strategy or form group detection changes
│   └── label_scan.js             (~100) — getAccessibleName, buildLocatorHint, scan() for all field types
│                                          Changes when: accessible name extraction or label strategy adds field types
│
├── fillers/                      — DOM mutation, one file per form control family
│   ├── fill_text.js              (~120) — fillField + fillContentEditable (char-by-char typing)
│   │                                      Changes when: text input or rich text interaction changes
│   ├── fill_select.js            (~110) — selectOption + checkBox + checkConsentBoxes
│   │                                      Changes when: native select/checkbox interaction changes
│   ├── fill_radio.js             (~80)  — fillRadioGroup (label-aware radio selection)
│   │                                      Changes when: radio group detection or selection changes
│   ├── fill_combobox.js          (~120) — fillCombobox + revealOptions (shared option-finding logic)
│   │                                      Changes when: custom dropdown open/search/select logic changes
│   ├── fill_dropdown.js          (~110) — fillCustomSelect + fillAutocomplete
│   │                                      Changes when: trigger-based dropdown or type-ahead interaction changes
│   ├── fill_simple.js            (~100) — fillTagInput + fillDate + uploadFile
│   │                                      Changes when: tag/date/file input handling changes
│   ├── fill_actions.js           (~80)  — clickElement, forceClick, scrollTo, waitForSelector
│   │                                      Changes when: click/scroll/wait interaction changes
│   ├── fill_validate.js          (~120) — scanValidationErrors + rescanAfterFill
│   │                                      Changes when: error detection strategies change
│   ├── label_fill.js             (~80)  — findByLabel + fillByLabel router + textByLabel + checkByLabel
│   │                                      Changes when: label-based element lookup or text/check fill changes
│   └── label_fill_choice.js      (~70)  — selectByLabel + comboboxByLabel + radioByLabel
│                                          Changes when: label-based choice control interaction changes
│
├── detectors/                    — Read-only page analysis
│   ├── snapshot.js               (~110) — buildSnapshot + button extraction + modal/progress/stability
│   │                                      Changes when: PageSnapshot schema or page state detection changes
│   ├── job_extract.js            (~100) — extractJobCards + extractJDText
│   │                                      Changes when: job listing page structure changes (Indeed, Greenhouse, etc.)
│   ├── verification.js           (~35)  — detectVerificationWall (CAPTCHA/block detection, shared by both strategies)
│   │                                      Changes when: new CAPTCHA types appear
│   └── native.js                 (~80)  — isConfirmationPage, isSubmitPage, detectNavigationButton, detectProgress
│                                          Changes when: page classification or navigation button detection changes
│
├── ai/
│   └── gemini.js                 (~55)  — analyzeFieldLocally, writeShortAnswer (Gemini Nano, Chrome built-in)
│                                          Changes when: local AI prompts or API changes
├── persistence/
│   └── form_progress.js          (~45)  — saveFormProgress, getFormProgress, clearFormProgress
│                                          Changes when: MV3 state persistence strategy changes
│
├── background.js                 — Unchanged (WebSocket, command dispatch, tab tracking)
├── protocol.js                   — Add new label-based message types
├── config.js                     — Unchanged (search config, rate limits, phase thresholds)
├── scanner.js                    — Unchanged (job scanning alarms, not form scanning)
├── job_queue.js                  — Unchanged (IndexedDB job queue)
├── phase_engine.js               — Unchanged (automation phase graduation)
├── native_bridge.js              — Unchanged (Python backend HTTP API)
└── manifest.json                 — Update content_scripts to load all modules
```

**Total: 25 content-script files.** Each has one clear reason to change, documented in the tree above.

### SOLID Principles Applied

- **Single Responsibility**: Each file does one thing (scan, fill, detect, utilities)
- **Open/Closed**: Add new strategies without modifying existing ones
- **Liskov Substitution**: Both scanners return the same shape; both fillers accept the same interface
- **Interface Segregation**: Python only sees the actions it needs per strategy
- **Dependency Inversion**: Dispatcher depends on abstractions (scan/fill interface via namespace), not concrete implementations

### Module Boundaries

- `core/` has zero knowledge of scanning or filling — pure utilities
- `scanners/` return data, never mutate DOM
- `fillers/` mutate DOM, never scan (they receive field descriptors)
- `detectors/` are read-only page analysis
- `ai/` is isolated — easy to swap or disable

## MV3 Namespace Pattern

Content scripts cannot use ES modules. All files register on a shared namespace:

```js
// Each file registers itself on window.JobPulse
window.JobPulse = window.JobPulse || {};

// core/utils.js
window.JobPulse.utils = { delay, smartScroll, withRetry, setNativeValue, verifyFieldValue };

// scanners/LabelScanner.js
window.JobPulse.scanners = window.JobPulse.scanners || {};
window.JobPulse.scanners.label = { scan, getAccessibleName };

// fillers/LabelFiller.js
window.JobPulse.fillers = window.JobPulse.fillers || {};
window.JobPulse.fillers.label = { fillByLabel, selectByLabel, checkByLabel };
```

**manifest.json** loads files in dependency order (core → scanners → fillers → detectors → ai → persistence → dispatcher):
```json
"content_scripts": [{
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
  ]
}]
```

## LabelScanner — Role-Based Field Discovery

Finds fields by accessibility role and extracts human-readable labels. Output: `[{label, type, options, value, required, locator_hint}]`.

### Field Types Scanned

1. **Text inputs** — `input[type=text/email/tel/number/url/search]` (excludes hidden, submit, checkbox, radio, file)
2. **Selects** — native `<select>` elements, options extracted with placeholder filtering
3. **Textareas** — `<textarea>` elements
4. **Custom dropdowns** — `[role="combobox"]`, `[role="listbox"]`
5. **Radio groups** — `[role="radiogroup"]` or grouped by `name` attribute
6. **Checkboxes** — `input[type="checkbox"]`
7. **File inputs** — `input[type="file"]`
8. **Contenteditable** — `[contenteditable="true"]`

All types filtered through `isFieldVisible()` to skip honeypots.

### getAccessibleName(el)

Extracts the label a screen reader would announce, in priority order:

1. `aria-labelledby` — concatenated text of referenced elements
2. `<label for="id">` — explicit label association
3. Wrapping `<label>` — parent label with input text stripped
4. `aria-label` attribute
5. `placeholder` attribute (last resort)

### buildLocatorHint(el)

Minimal metadata for the filler to re-find the element without a CSS selector:

```js
{ tag, id, name, type, ariaLabel, index }
```

### Output Comparison

| SelectorScanner output | LabelScanner output |
|---|---|
| `{selector: "#input-r4c", input_type: "text", dom_context: "Email\|...", label_sources: [...]}` | `{label: "Email Address", type: "email", required: true, value: ""}` |

The label scanner output is what Python sends to the LLM — simpler prompts, fewer tokens, better mapping accuracy.

## LabelFiller — Fill by Accessible Name

### findByLabel(label)

Re-finds an element by its accessible name using 4 strategies in order:

1. **`<label for>` match** — iterate all `<label>` elements, compare text, follow `for` attribute
2. **`aria-label` exact match** — `querySelector` with attribute selector
3. **Placeholder match** — `querySelector` with placeholder attribute
4. **Fuzzy match** — label text contains/is contained by target label text

### fillByLabel(label, value, fieldType)

Routes to the appropriate sub-filler based on the element found:

- `<select>` → `selectByLabel()` — fuzzy match option text
- `checkbox` → `checkByLabel()` — true/false/yes/no
- `radio` → `radioByLabel()` — find group, fuzzy match option
- `combobox/listbox` → `comboboxByLabel()` — open, search, select
- `contenteditable` → `contentEditableByLabel()` — execCommand typing
- Default → `textByLabel()` — human-like character-by-character typing

All sub-fillers reuse the same human-like interaction patterns (cursor movement, typing speed, scroll, highlight) from `core/`.

### Post-Fill Verification

Same `verifyFieldValue()` from `core/utils.js` — shared by both strategies.

## NativeDetector — Page Detection Without Snapshots

Direct DOM queries, no snapshot, no state machine:

### isConfirmationPage()

Checks body text (first 2000 chars) for thank-you phrases:
- "thank you for applying", "application has been received", "application submitted", "successfully submitted", "application is complete", "we have received your application"

### isSubmitPage()

Scans visible buttons/submit inputs for submit-like text:
- "Submit Application", "Submit", "Apply", "Apply Now"

### detectNavigationButton()

Priority-ordered scan for the next clickable element:
1. **Submit group**: "Submit Application", "Submit", "Apply", "Apply Now"
2. **Next group**: "Save & Continue", "Continue", "Next", "Proceed", "Save and Continue"
3. **Link fallback**: `<a>` tags with submit-like text

Returns `{action, element, text}` or `null`.

### detectProgress()

Regex on body text: `/(?:step|page)\s+(\d+)\s+(?:of|\/)\s+(\d+)/i`

### hasUnfilledRequired()

Quick scan of `[required]` and `[aria-required="true"]` elements for empty visible fields.

## Content.js Message Dispatcher

Thin router that resolves strategy and delegates:

```js
const strategy = payload?.strategy || "selector";  // default: backward compat

switch (action) {
  case "scan_fields":    → getScanner(strategy).scan()
  case "fill":           → getFiller(strategy).fill(...)
  case "fill_by_label":  → getFiller("label").fillByLabel(...)
  case "detect_page":    → getDetector(strategy) aggregated result
  case "click_navigation" → NativeDetector.detectNavigationButton() + click
  case "get_snapshot":   → selector: buildSnapshot(), label: scan()
  // ... all legacy selector actions route through JP.legacy.handle()
}
```

**Default strategy is `"selector"`** — zero breaking changes to existing Python code. All current commands work unchanged.

## Protocol Updates

New message types in `protocol.js`:

```js
CMD_SCAN_FIELDS: "scan_fields",           // → [{label, type, options, value, required}]
CMD_FILL_BY_LABEL: "fill_by_label",       // {label, value, type?}
CMD_DETECT_PAGE: "detect_page",           // → {is_confirmation, is_submit_page, nav_button, ...}
CMD_CLICK_NAVIGATION: "click_navigation", // {dry_run} → "submitted"|"next"|"dry_run_stop"|""
CMD_CHECK_CONSENT: "check_consent",       // → {checked_count, labels}
CMD_UPLOAD_FILES: "upload_files",         // {cv_base64, cl_base64} — label-based file detection
```

## Python Integration

### Label Strategy Flow (per page)

```python
async def _fill_page_native(self, profile, custom_answers, platform, cv_path, cl_path, dry_run):
    # 1. Scan fields by label
    fields = await self.bridge.send("scan_fields", strategy="label")

    # 2. Check if confirmation page
    page = await self.bridge.send("detect_page", strategy="label")
    if page["is_confirmation"]:
        return {"success": True, "confirmed": True}

    # 3. LLM: map labels → values
    mapping = await self._llm_map_fields(fields, profile, custom_answers, platform)

    # 4. Fill each field by label (DOM order, top-to-bottom)
    for field in fields:
        if field["label"] in mapping and field["type"] != "file":
            await self.bridge.send("fill_by_label", label=field["label"], value=mapping[field["label"]], strategy="label")

    # 5. Upload files (deterministic)
    await self.bridge.send("upload_files", cv_base64=..., cl_base64=..., strategy="label")

    # 6. Consent boxes
    await self.bridge.send("check_consent", strategy="label")

    # 7. Click next/submit
    return await self.bridge.send("click_navigation", dry_run=dry_run, strategy="label")
```

### A/B Selection

The orchestrator picks strategy based on engine config:

```python
if self.engine == "extension" and self._strategy == "label":
    return await self._fill_page_native(...)
else:
    return await self._fill_page_snapshot(...)
```

Both strategies run through the same extension, same Chrome profile, same anti-detection. The only variable is field discovery and fill method.

## Strategy Comparison

| Aspect | Selector strategy | Label strategy |
|---|---|---|
| Scan | `buildSnapshot()` → full PageSnapshot JSON | `LabelScanner.scan()` → `[{label, type, options, value}]` |
| Fill | `fill(selector, value)` | `fill_by_label(label, value)` |
| Page detect | Snapshot buttons/fields analysis | NativeDetector — check headings/button text directly |
| LLM input | Snapshot JSON with selectors + dom_context | Simple `{label, type, options}` list |
| Navigation | `find_next_button(snapshot)` | `detectNavigationButton()` — scan for Submit/Next by text |
| Snapshots | Full PageSnapshot per scan, MutationObserver pushes | Never built or sent |

## Migration Map

Pure extraction from current `content.js` monolith — zero behavioral changes to selector path. Each file grouped by "what changes together".

### core/ — Utilities (extracted from content.js lines 1-530)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `core/dom.js` | `delay()`, `smartScroll()`, `withRetry()`, `isFieldVisible()`, `resolveSelector()` | 90 | DOM interaction primitives change |
| `core/form.js` | `setNativeValue()`, `verifyFieldValue()`, `normalizeText()`, `fuzzyMatchOption()`, `ABBREVIATIONS` | 80 | Form value handling or matching logic changes |
| `core/timing.js` | `behaviorProfile`, calibration listeners, `getFieldGap()`, `getTypingDelay()` | 65 | Anti-detection timing or typing speed changes |
| `core/cursor.js` | `ensureCursor()`, `moveCursorTo()`, `bezierCurve()`, `cursorClickFlash()`, `highlightElement()`, `hideCursor()` | 80 | Visual feedback appearance or animation changes |

### scanners/ — Field Discovery (extracted from lines 133-816)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `scanners/field_context.js` | `extractFieldContext()` — exhaustive DOM walk for labels | 100 | Label extraction heuristics change |
| `scanners/field_info.js` | `extractFieldInfo()` — build FieldInfo + CSS selector from element | 95 | FieldInfo schema or selector-building logic changes |
| `scanners/scan_dom.js` | `deepScan()` + `scanFormGroups()` — recursive DOM/shadow/iframe traversal | 110 | DOM traversal strategy or form group detection changes |

### fillers/ — Form Filling (extracted from lines 968-1952)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `fillers/fill_text.js` | `fillField()` + `fillContentEditable()` | 120 | Text input or rich text typing interaction changes |
| `fillers/fill_select.js` | `selectOption()` + `checkBox()` + `checkConsentBoxes()` | 110 | Native select, checkbox, or consent interaction changes |
| `fillers/fill_radio.js` | `fillRadioGroup()` | 80 | Radio group detection or selection changes |
| `fillers/fill_combobox.js` | `fillCombobox()` + `revealOptions()` (shared option-finding logic) | 120 | Custom dropdown open/search/select logic changes |
| `fillers/fill_dropdown.js` | `fillCustomSelect()` + `fillAutocomplete()` | 110 | Trigger-based dropdown or type-ahead interaction changes |
| `fillers/fill_simple.js` | `fillTagInput()` + `fillDate()` + `uploadFile()` | 100 | Tag, date, or file input handling changes |
| `fillers/fill_actions.js` | `clickElement()`, `forceClick()`, `scrollTo()`, `waitForSelector()` | 80 | Click, scroll, or wait interaction changes |
| `fillers/fill_validate.js` | `scanValidationErrors()` + `rescanAfterFill()` | 120 | Error detection strategies change |

### detectors/ — Page Analysis (extracted from lines 823-2158)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `detectors/snapshot.js` | `buildSnapshot()` + button extraction + modal/progress/stability | 110 | PageSnapshot schema or page state detection changes |
| `detectors/job_extract.js` | `extractJobCards()` + `extractJDText()` | 100 | Job listing page structure changes (Indeed, Greenhouse, etc.) |
| `detectors/verification.js` | `detectVerificationWall()` (shared by both strategies) | 35 | New CAPTCHA types appear |

### Other (extracted from lines 1955-2520)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `ai/gemini.js` | `analyzeFieldLocally()`, `writeShortAnswer()` | 55 | Local AI prompts or Chrome AI API changes |
| `persistence/form_progress.js` | `saveFormProgress()`, `getFormProgress()`, `clearFormProgress()` | 45 | MV3 state persistence strategy changes |
| `content.js` | Message dispatcher + MutationObserver + strategy router | 70 | New actions added or strategy routing changes |

### New files (genuinely new code, ~450 lines total)

| Destination | Contains | ~Lines | Changes when |
|---|---|---|---|
| `scanners/label_scan.js` | `getAccessibleName()`, `buildLocatorHint()`, `scan()` for all field types | 100 | Accessible name extraction or new field types added |
| `fillers/label_fill.js` | `findByLabel()`, `fillByLabel()` router, `textByLabel()`, `checkByLabel()` | 80 | Label-based element lookup or text/check fill changes |
| `fillers/label_fill_choice.js` | `selectByLabel()`, `comboboxByLabel()`, `radioByLabel()` | 70 | Label-based choice control interaction changes |
| `detectors/native.js` | `isConfirmationPage()`, `isSubmitPage()`, `detectNavigationButton()`, `detectProgress()`, `hasUnfilledRequired()` | 80 | Page classification or navigation button detection changes |

## Cost

- Zero additional LLM cost — same number of calls, just simpler prompts
- Label strategy prompts are ~40% smaller (no selectors, no dom_context), saving tokens
- No new dependencies, no build step, vanilla JS throughout
