# Task 4: Retry Wrapper for All Fill Operations

**Files:**
- Modify: `extension/content.js` — add `withRetry` utility + wrap fill cases in message handler

**Why:** ATS forms lazy-load dependent fields. A single "element not found" often succeeds 500ms later after a DOM mutation. Currently every fill is fire-once with no retry.

**Dependencies:** Tasks 1-3 (fill functions must exist before wrapping)

---

- [ ] **Step 1: Add `withRetry` utility**

Add after the `delay` function (line ~66) in content.js:

```javascript
/**
 * Retry wrapper for fill operations.
 * Retries on element-not-found or fill failure. Max 2 retries with 500ms gap.
 * Does NOT retry on success (even partial).
 */
async function withRetry(fn, maxRetries = 2, retryDelayMs = 500) {
  let lastResult;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    lastResult = await fn();
    if (lastResult.success) return lastResult;

    // Only retry on transient errors
    const isRetryable = lastResult.error &&
      (lastResult.error.includes("not found") ||
       lastResult.error.includes("No options") ||
       lastResult.error.includes("not visible"));
    if (!isRetryable) return lastResult;

    if (attempt < maxRetries) {
      await delay(retryDelayMs);
    }
  }
  lastResult.retries_exhausted = true;
  return lastResult;
}
```

- [ ] **Step 2: Wrap fill operations in the message handler**

In the message handler switch block (~line 1748), wrap these cases with `withRetry`:

**Replace each pattern like:**
```javascript
case "fill":
  result = await fillField(payload.selector, payload.value);
  break;
```

**With:**
```javascript
case "fill":
  result = await withRetry(() => fillField(payload.selector, payload.value));
  break;
```

**Apply to ALL of these cases:**
- `"fill"` → `withRetry(() => fillField(...))`
- `"select"` → `withRetry(() => selectOption(...))`
- `"check"` → `withRetry(() => checkBox(...))`
- `"fill_radio_group"` → `withRetry(() => fillRadioGroup(...))`
- `"fill_custom_select"` → `withRetry(() => fillCustomSelect(...))`
- `"fill_autocomplete"` → `withRetry(() => fillAutocomplete(...))`
- `"fill_combobox"` → `withRetry(() => fillCombobox(...))`
- `"fill_tag_input"` → `withRetry(() => fillTagInput(...))`
- `"fill_date"` → `withRetry(() => fillDate(...))`
- `"fill_contenteditable"` → `withRetry(() => fillContentEditable(...))`

**Do NOT wrap** (these are not fill operations):
- `click`, `upload`, `scroll_to`, `wait_for_selector`, `get_snapshot`, `force_click`
- `scan_*`, `check_consent_boxes`, `save/get/clear_form_progress`
- `analyze_field`, `scan_jd`, `scan_cards`, `wait_for_apply`

- [ ] **Step 3: Commit**

```bash
git add extension/content.js
git commit -m "feat(extension): retry wrapper for all fill operations

withRetry() retries up to 2 times on element-not-found or no-options
errors with 500ms delay. Handles lazy-loaded ATS form fields that
render after DOM mutations (cascading dropdowns, conditional fields)."
```
