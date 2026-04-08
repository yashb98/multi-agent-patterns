# Extension Label Strategy — Design Spec

**Date:** 2026-04-08
**Goal:** Add a label-based form filling strategy to the Chrome extension alongside the existing selector-based approach, enabling A/B comparison between the two strategies within the same engine.

## Motivation

The Playwright native pipeline spec proposed role-based locators and accessible-name field discovery as improvements over the extension's snapshot-based approach. However, these improvements are not Playwright-specific — the extension can adopt the same patterns. By implementing both strategies in the same extension, we can A/B test them under identical conditions (same Chrome profile, same anti-detection, same timing) with the only variable being how fields are discovered and filled.

## Architecture: Strategy Pattern

**Hard constraint: every file ≤ 60-80 lines.** This keeps each file fully readable in one context window and enforces single-responsibility at a granular level.

```
extension/
├── content.js                          — Thin bootstrap: namespace init, message dispatcher (~70 lines)
├── core/
│   ├── delay.js                        — delay(), sleep utilities (~15 lines)
│   ├── scroll.js                       — smartScroll(), scrollTo() (~30 lines)
│   ├── retry.js                        — withRetry() wrapper (~25 lines)
│   ├── native_value.js                 — setNativeValue(), verifyFieldValue() (~40 lines)
│   ├── visibility.js                   — isFieldVisible() (~30 lines)
│   ├── selector.js                     — resolveSelector() inc. shadow DOM (~30 lines)
│   ├── fuzzy.js                        — normalizeText(), fuzzyMatchOption(), ABBREVIATIONS (~45 lines)
│   ├── timing.js                       — BehaviorProfile, calibration listeners, getFieldGap(), getTypingDelay() (~65 lines)
│   ├── cursor_core.js                  — ensureCursor(), hideCursor(), cursorClickFlash(), highlightElement() (~60 lines)
│   ├── cursor_move.js                  — moveCursorTo(), bezierCurve() (~55 lines)
│   └── events.js                       — dispatchMouseEvents(), dispatchKeyEvents() — shared event helpers (~40 lines)
├── scanners/
│   ├── field_context.js                — extractFieldContext() — exhaustive DOM label extraction (~70 lines)
│   ├── field_info.js                   — extractFieldInfo() — build FieldInfo from element (~75 lines)
│   ├── deep_scan.js                    — deepScan() — recursive DOM + shadow DOM + iframe (~45 lines)
│   ├── form_groups.js                  — scanFormGroups() — fieldset/group scanning (~70 lines)
│   ├── label_core.js                   — getAccessibleName(), buildLocatorHint(), clean() (~55 lines)
│   ├── label_scan.js                   — LabelScanner.scan() — role-based field discovery (~75 lines)
│   └── label_scan_custom.js            — scanRadioGroups(), scanComboboxes(), scanContentEditable() (~60 lines)
├── fillers/
│   ├── fill_text.js                    — fillField() — human-like char-by-char typing (~70 lines)
│   ├── fill_contenteditable.js         — fillContentEditable() — execCommand typing (~50 lines)
│   ├── fill_select.js                  — selectOption() — native <select> fuzzy match (~50 lines)
│   ├── fill_checkbox.js                — checkBox() (~20 lines)
│   ├── fill_radio.js                   — fillRadioGroup() — label-aware radio selection (~75 lines)
│   ├── fill_combobox.js                — fillCombobox() — open dropdown, search, select (~80 lines)
│   ├── fill_custom_select.js           — fillCustomSelect() — trigger + option panel (~70 lines)
│   ├── fill_autocomplete.js            — fillAutocomplete() — type + suggestion pick (~55 lines)
│   ├── fill_tag_input.js               — fillTagInput() — multi-value with Enter (~30 lines)
│   ├── fill_date.js                    — fillDate() — format detection + native setter (~55 lines)
│   ├── fill_upload.js                  — uploadFile() — DataTransfer API (~20 lines)
│   ├── fill_actions.js                 — clickElement(), forceClick(), scrollTo(), waitForSelector() (~70 lines)
│   ├── consent.js                      — checkConsentBoxes() (~40 lines)
│   ├── validation.js                   — scanValidationErrors() — 5-strategy error scan (~75 lines)
│   ├── rescan.js                       — rescanAfterFill() (~40 lines)
│   ├── reveal_options.js               — revealOptions() — click dropdown, capture options, close (~65 lines)
│   ├── label_find.js                   — findByLabel() — 4-strategy element lookup by accessible name (~60 lines)
│   ├── label_fill.js                   — fillByLabel() — router to type-specific sub-fillers (~50 lines)
│   ├── label_fill_text.js              — textByLabel(), contentEditableByLabel() (~55 lines)
│   ├── label_fill_select.js            — selectByLabel(), comboboxByLabel(), radioByLabel() (~70 lines)
│   └── label_fill_check.js             — checkByLabel(), uploadByLabel() (~30 lines)
├── detectors/
│   ├── snapshot_builder.js             — buildSnapshot() — assemble PageSnapshot (~70 lines)
│   ├── snapshot_buttons.js             — extractButtons() — button/link scanning (~60 lines)
│   ├── snapshot_meta.js                — detectModal(), detectProgress(), pageStability() (~40 lines)
│   ├── job_cards.js                    — extractJobCards() — Indeed/Greenhouse/generic card parsing (~75 lines)
│   ├── jd_text.js                      — extractJDText() — platform-specific JD extraction (~35 lines)
│   ├── verification.js                 — detectVerificationWall() — CAPTCHA/block detection (~35 lines)
│   ├── native_page.js                  — isConfirmationPage(), isSubmitPage(), hasUnfilledRequired() (~45 lines)
│   └── native_nav.js                   — detectNavigationButton(), detectProgress() (~50 lines)
├── ai/
│   └── gemini.js                       — analyzeFieldLocally(), writeShortAnswer() (~55 lines)
├── persistence/
│   └── form_progress.js                — saveFormProgress(), getFormProgress(), clearFormProgress() (~45 lines)
├── legacy/
│   └── message_handler.js              — Legacy switch cases for all selector-only actions (~75 lines)
├── background.js                       — Unchanged
├── protocol.js                         — Add new label-based message types
├── config.js                           — Unchanged
├── scanner.js                          — Unchanged (job scanning, not form scanning)
├── job_queue.js                        — Unchanged
├── phase_engine.js                     — Unchanged
├── native_bridge.js                    — Unchanged
└── manifest.json                       — Update content_scripts to load all modules
```

**Total: 42 content-script files, each ≤ 80 lines.** Every file has one clear purpose and can be read in full without scrolling.

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

**manifest.json** loads files in dependency order (core → scanners → fillers → detectors → ai → persistence → legacy → dispatcher):
```json
"content_scripts": [{
  "js": [
    "core/delay.js", "core/scroll.js", "core/retry.js", "core/native_value.js",
    "core/visibility.js", "core/selector.js", "core/fuzzy.js", "core/timing.js",
    "core/events.js", "core/cursor_core.js", "core/cursor_move.js",
    "scanners/field_context.js", "scanners/field_info.js", "scanners/deep_scan.js",
    "scanners/form_groups.js", "scanners/label_core.js", "scanners/label_scan.js",
    "scanners/label_scan_custom.js",
    "fillers/fill_text.js", "fillers/fill_contenteditable.js", "fillers/fill_select.js",
    "fillers/fill_checkbox.js", "fillers/fill_radio.js", "fillers/fill_combobox.js",
    "fillers/fill_custom_select.js", "fillers/fill_autocomplete.js",
    "fillers/fill_tag_input.js", "fillers/fill_date.js", "fillers/fill_upload.js",
    "fillers/fill_actions.js", "fillers/consent.js", "fillers/validation.js",
    "fillers/rescan.js", "fillers/reveal_options.js",
    "fillers/label_find.js", "fillers/label_fill.js", "fillers/label_fill_text.js",
    "fillers/label_fill_select.js", "fillers/label_fill_check.js",
    "detectors/snapshot_builder.js", "detectors/snapshot_buttons.js",
    "detectors/snapshot_meta.js", "detectors/job_cards.js", "detectors/jd_text.js",
    "detectors/verification.js", "detectors/native_page.js", "detectors/native_nav.js",
    "ai/gemini.js", "persistence/form_progress.js",
    "legacy/message_handler.js", "content.js"
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

Pure extraction from current `content.js` monolith — zero behavioral changes to selector path. Each destination file stays ≤ 80 lines.

### core/ — Utilities (extracted from lines 1-530)

| Source | Destination | ~Lines |
|---|---|---|
| `delay()` | `core/delay.js` | 15 |
| `smartScroll()` | `core/scroll.js` | 30 |
| `withRetry()` | `core/retry.js` | 25 |
| `setNativeValue()`, `verifyFieldValue()` | `core/native_value.js` | 40 |
| `isFieldVisible()` | `core/visibility.js` | 30 |
| `resolveSelector()` | `core/selector.js` | 30 |
| `normalizeText()`, `fuzzyMatchOption()`, `ABBREVIATIONS` | `core/fuzzy.js` | 45 |
| `behaviorProfile`, calibration, `getFieldGap()` | `core/timing.js` | 65 |
| `ensureCursor()`, `hideCursor()`, `cursorClickFlash()`, `highlightElement()` | `core/cursor_core.js` | 60 |
| `moveCursorTo()`, `bezierCurve()` | `core/cursor_move.js` | 55 |
| Mouse/keyboard event dispatch helpers (extracted from fill functions) | `core/events.js` | 40 |

### scanners/ — Field Discovery (extracted from lines 133-816)

| Source | Destination | ~Lines |
|---|---|---|
| `extractFieldContext()` | `scanners/field_context.js` | 70 |
| `extractFieldInfo()` | `scanners/field_info.js` | 75 |
| `deepScan()` (recursive DOM/shadow/iframe) | `scanners/deep_scan.js` | 45 |
| `scanFormGroups()` | `scanners/form_groups.js` | 70 |

### fillers/ — Form Filling (extracted from lines 968-1952)

| Source | Destination | ~Lines |
|---|---|---|
| `fillField()` (text inputs) | `fillers/fill_text.js` | 70 |
| `fillContentEditable()` | `fillers/fill_contenteditable.js` | 50 |
| `selectOption()` | `fillers/fill_select.js` | 50 |
| `checkBox()` | `fillers/fill_checkbox.js` | 20 |
| `fillRadioGroup()` | `fillers/fill_radio.js` | 75 |
| `fillCombobox()` | `fillers/fill_combobox.js` | 80 |
| `fillCustomSelect()` | `fillers/fill_custom_select.js` | 70 |
| `fillAutocomplete()` | `fillers/fill_autocomplete.js` | 55 |
| `fillTagInput()` | `fillers/fill_tag_input.js` | 30 |
| `fillDate()` | `fillers/fill_date.js` | 55 |
| `uploadFile()` | `fillers/fill_upload.js` | 20 |
| `clickElement()`, `forceClick()`, `scrollTo()`, `waitForSelector()` | `fillers/fill_actions.js` | 70 |
| `checkConsentBoxes()` | `fillers/consent.js` | 40 |
| `scanValidationErrors()` | `fillers/validation.js` | 75 |
| `rescanAfterFill()` | `fillers/rescan.js` | 40 |
| `revealOptions()` | `fillers/reveal_options.js` | 65 |

### detectors/ — Page Analysis (extracted from lines 823-2158)

| Source | Destination | ~Lines |
|---|---|---|
| `buildSnapshot()` | `detectors/snapshot_builder.js` | 70 |
| Button/link extraction from `buildSnapshot()` | `detectors/snapshot_buttons.js` | 60 |
| Modal/progress/stability from `buildSnapshot()` | `detectors/snapshot_meta.js` | 40 |
| `extractJobCards()` | `detectors/job_cards.js` | 75 |
| `extractJDText()` | `detectors/jd_text.js` | 35 |
| `detectVerificationWall()` | `detectors/verification.js` | 35 |

### Other (extracted from lines 1955-2520)

| Source | Destination | ~Lines |
|---|---|---|
| `analyzeFieldLocally()`, `writeShortAnswer()` | `ai/gemini.js` | 55 |
| `saveFormProgress()`, `getFormProgress()`, `clearFormProgress()` | `persistence/form_progress.js` | 45 |
| Legacy switch statement cases | `legacy/message_handler.js` | 75 |
| MutationObserver + load event + dispatcher | `content.js` | 70 |

### New files (genuinely new code, ~500 lines total)

| File | Purpose | ~Lines |
|---|---|---|
| `scanners/label_core.js` | `getAccessibleName()`, `buildLocatorHint()`, `clean()` | 55 |
| `scanners/label_scan.js` | `LabelScanner.scan()` — text, select, textarea, file, checkbox | 75 |
| `scanners/label_scan_custom.js` | `scanRadioGroups()`, `scanComboboxes()`, `scanContentEditable()` | 60 |
| `fillers/label_find.js` | `findByLabel()` — 4-strategy element lookup | 60 |
| `fillers/label_fill.js` | `fillByLabel()` — router to type-specific sub-fillers | 50 |
| `fillers/label_fill_text.js` | `textByLabel()`, `contentEditableByLabel()` | 55 |
| `fillers/label_fill_select.js` | `selectByLabel()`, `comboboxByLabel()`, `radioByLabel()` | 70 |
| `fillers/label_fill_check.js` | `checkByLabel()`, `uploadByLabel()` | 30 |
| `detectors/native_page.js` | `isConfirmationPage()`, `isSubmitPage()`, `hasUnfilledRequired()` | 45 |
| `detectors/native_nav.js` | `detectNavigationButton()`, `detectProgress()` | 50 |

## Cost

- Zero additional LLM cost — same number of calls, just simpler prompts
- Label strategy prompts are ~40% smaller (no selectors, no dom_context), saving tokens
- No new dependencies, no build step, vanilla JS throughout
