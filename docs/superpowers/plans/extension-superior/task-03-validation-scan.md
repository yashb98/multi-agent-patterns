# Task 3: Validation Error Scanning + Protocol Command

**Files:**
- Modify: `extension/content.js` — add `scanValidationErrors` function + wire message handler
- Modify: `extension/protocol.js` — add `CMD_SCAN_VALIDATION_ERRORS` and `CMD_FILL_CONTENTEDITABLE`

**Why:** Python has no way to ask the extension "are there validation errors on the page?" before clicking submit. The existing `rescanAfterFill()` does a partial check for one field, but not a full page scan.

**Dependencies:** None (standalone function)

---

- [ ] **Step 1: Add new command constants to protocol.js**

In `extension/protocol.js`, add to the v2 form engine commands section (after `CMD_RESCAN_AFTER_FILL`):

```javascript
CMD_SCAN_VALIDATION_ERRORS: "scan_validation_errors",
CMD_FILL_CONTENTEDITABLE: "fill_contenteditable",
```

- [ ] **Step 2: Add `scanValidationErrors` function to content.js**

Add after `rescanAfterFill` function:

```javascript
/**
 * Comprehensive validation error scan — checks 5 strategies:
 * 1. aria-invalid="true" elements
 * 2. role="alert" elements (excluding header/nav)
 * 3. .error / .invalid-feedback / .field-error class elements
 * 4. aria-errormessage references
 * 5. ATS-specific error patterns (Greenhouse, LinkedIn Easy Apply, Workday)
 *
 * Returns { errors: [{selector, field_label, error_message}], has_errors, count }
 */
function scanValidationErrors() {
  const errors = [];
  const seen = new Set();

  // Strategy 1: aria-invalid elements
  for (const el of document.querySelectorAll("[aria-invalid='true']")) {
    const errId = el.getAttribute("aria-errormessage");
    let errMsg = "";
    if (errId) {
      const errEl = document.getElementById(errId);
      if (errEl) errMsg = errEl.textContent.trim();
    }
    if (!errMsg) {
      const parent = el.closest(".form-group, .field-wrapper, .form-field, [data-test-form-element]");
      if (parent) {
        const errEl = parent.querySelector(".error, .invalid-feedback, [role='alert'], .field-error");
        if (errEl) errMsg = errEl.textContent.trim();
      }
    }
    const key = (el.id || el.name || "") + errMsg;
    if (!seen.has(key)) {
      seen.add(key);
      errors.push({
        selector: el.id ? `#${el.id}` : `[name="${el.name || ""}"]`,
        field_label: extractFieldContext(el).label,
        error_message: errMsg || "Field marked as invalid",
      });
    }
  }

  // Strategy 2: role="alert" elements (not inside header/nav)
  for (const alert of document.querySelectorAll("[role='alert']")) {
    if (alert.closest("header, nav, [role='banner']")) continue;
    const text = alert.textContent.trim();
    if (text && text.length > 2 && text.length < 500 && !seen.has(text)) {
      seen.add(text);
      errors.push({
        selector: "[role='alert']",
        field_label: "",
        error_message: text,
      });
    }
  }

  // Strategy 3: Error class elements near form fields
  const errorSelectors = [
    ".error:not(header .error)",
    ".invalid-feedback",
    ".field-error",
    ".form-error",
    "[class*='error-message']",
    "[class*='validation-error']",
    "[class*='errorText']",
    // ATS-specific
    ".jobs-easy-apply-form-section__error",
    ".fb-dash-form-element__error",
    "[data-test='form-field-error']",
  ];

  for (const sel of errorSelectors) {
    for (const errEl of document.querySelectorAll(sel)) {
      const text = errEl.textContent.trim();
      if (text && text.length > 2 && text.length < 300 && !seen.has(text)) {
        seen.add(text);
        const parent = errEl.closest(".form-group, .field-wrapper, .form-field, fieldset");
        let fieldLabel = "";
        if (parent) {
          const labelEl = parent.querySelector("label, legend, [class*='label']");
          if (labelEl) fieldLabel = labelEl.textContent.trim().substring(0, 100);
        }
        errors.push({
          selector: sel,
          field_label: fieldLabel,
          error_message: text,
        });
      }
    }
  }

  return { errors, has_errors: errors.length > 0, count: errors.length };
}
```

- [ ] **Step 3: Wire into message handler**

In the `switch(action)` block (line ~1748), add before the `default` case:

```javascript
case "scan_validation_errors":
  result = scanValidationErrors();
  break;
case "fill_contenteditable": {
  const ceEl = resolveSelector(payload.selector);
  result = ceEl
    ? await fillContentEditable(ceEl, payload.value)
    : { success: false, error: "Element not found: " + payload.selector };
  break;
}
```

- [ ] **Step 4: Commit**

```bash
git add extension/content.js extension/protocol.js
git commit -m "feat(extension): validation error scanning + protocol commands

5-strategy validation error detection: aria-invalid, role=alert, error
classes, aria-errormessage, ATS-specific patterns. Python can now call
scan_validation_errors before submit to catch issues."
```
