# Extension Label Strategy — Design Spec

**Date:** 2026-04-08
**Goal:** Add a label-based form filling strategy to the Chrome extension alongside the existing selector-based approach, enabling A/B comparison between the two strategies within the same engine.

## Motivation

The Playwright native pipeline spec proposed role-based locators and accessible-name field discovery as improvements over the extension's snapshot-based approach. However, these improvements are not Playwright-specific — the extension can adopt the same patterns. By implementing both strategies in the same extension, we can A/B test them under identical conditions (same Chrome profile, same anti-detection, same timing) with the only variable being how fields are discovered and filled.

## Architecture: Strategy Pattern

```
extension/
├── content.js              — Thin bootstrap: namespace init, message dispatcher
├── core/
│   ├── utils.js            — delay(), smartScroll(), withRetry(), setNativeValue(), verifyFieldValue()
│   ├── cursor.js           — Visual cursor (ensureCursor, moveCursorTo, clickFlash, highlight, bezierCurve)
│   ├── fuzzy.js            — normalizeText(), fuzzyMatchOption(), ABBREVIATIONS
│   ├── timing.js           — BehaviorProfile class (calibration, getFieldGap, getTypingDelay)
│   └── visibility.js       — isFieldVisible(), resolveSelector()
├── scanners/
│   ├── SelectorScanner.js  — Current deepScan() + extractFieldInfo() + extractFieldContext()
│   └── LabelScanner.js     — New: role-based scan using accessible names
├── fillers/
│   ├── SelectorFiller.js   — fillField(), selectOption(), checkBox(), fillRadioGroup(), etc.
│   └── LabelFiller.js      — fillByLabel(), selectByLabel(), checkByLabel() — label-first lookup
├── detectors/
│   ├── SnapshotDetector.js  — buildSnapshot(), scanFormGroups(), extractJobCards(), extractJDText()
│   ├── NativeDetector.js    — isConfirmationPage(), isSubmitPage(), detectNavigationButton()
│   └── VerificationDetector.js — detectVerificationWall() (shared by both strategies)
├── ai/
│   └── gemini.js           — analyzeFieldLocally(), writeShortAnswer() (Gemini Nano)
├── persistence/
│   └── form_progress.js    — saveFormProgress(), getFormProgress(), clearFormProgress()
├── background.js           — Unchanged
├── protocol.js             — Add new label-based message types
├── config.js               — Unchanged
├── scanner.js              — Unchanged (job scanning, not form scanning)
├── job_queue.js            — Unchanged
├── phase_engine.js         — Unchanged
├── native_bridge.js        — Unchanged
└── manifest.json           — Update content_scripts to load new modules
```

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

**manifest.json** loads files in dependency order:
```json
"content_scripts": [{
  "js": [
    "core/utils.js",
    "core/visibility.js",
    "core/fuzzy.js",
    "core/timing.js",
    "core/cursor.js",
    "scanners/SelectorScanner.js",
    "scanners/LabelScanner.js",
    "fillers/SelectorFiller.js",
    "fillers/LabelFiller.js",
    "detectors/VerificationDetector.js",
    "detectors/SnapshotDetector.js",
    "detectors/NativeDetector.js",
    "ai/gemini.js",
    "persistence/form_progress.js",
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

Pure extraction from current `content.js` monolith — zero behavioral changes to selector path:

| Current location (content.js lines) | Destination |
|---|---|
| `delay()`, `smartScroll()`, `withRetry()`, `setNativeValue()`, `verifyFieldValue()` (1-120) | `core/utils.js` |
| `isFieldVisible()`, `resolveSelector()` (410-490) | `core/visibility.js` |
| `normalizeText()`, `fuzzyMatchOption()`, `ABBREVIATIONS` (490-530) | `core/fuzzy.js` |
| `behaviorProfile`, calibration, `getFieldGap()` (22-80) | `core/timing.js` |
| Visual cursor functions, `bezierCurve()` (298-408) | `core/cursor.js` |
| `extractFieldContext()`, `extractFieldInfo()`, `deepScan()`, `scanFormGroups()` (133-816) | `scanners/SelectorScanner.js` |
| All fill functions (968-1743) | `fillers/SelectorFiller.js` |
| `buildSnapshot()`, `extractJobCards()`, `extractJDText()` (856-2158) | `detectors/SnapshotDetector.js` |
| `detectVerificationWall()` (823-850) | `detectors/VerificationDetector.js` |
| `analyzeFieldLocally()`, `writeShortAnswer()` (1969-2018) | `ai/gemini.js` |
| `saveFormProgress()`, `getFormProgress()`, `clearFormProgress()` (2475-2515) | `persistence/form_progress.js` |
| `scrollTo()`, `waitForSelector()`, `forceClick()`, `checkConsentBoxes()`, etc. (1745-1952) | `fillers/SelectorFiller.js` (secondary ops) |
| MutationObserver + load event (2430-2463) | `content.js` (stays, behind strategy toggle) |
| Message listener switch (2164-2420) | `content.js` (rewritten as thin dispatcher) |

**New files** (genuinely new code, ~300 lines total):

| File | Purpose |
|---|---|
| `scanners/LabelScanner.js` | Role-based field discovery with `getAccessibleName()` |
| `fillers/LabelFiller.js` | `findByLabel()` + `fillByLabel()` + type-specific sub-fillers |
| `detectors/NativeDetector.js` | `isConfirmationPage()`, `isSubmitPage()`, `detectNavigationButton()` |

## Cost

- Zero additional LLM cost — same number of calls, just simpler prompts
- Label strategy prompts are ~40% smaller (no selectors, no dom_context), saving tokens
- No new dependencies, no build step, vanilla JS throughout
