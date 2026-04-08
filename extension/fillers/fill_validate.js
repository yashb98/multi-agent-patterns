// extension/fillers/fill_validate.js — Validation error scanning and post-fill rescan
// Changes when: error detection strategies change
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

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
  const JP = window.JobPulse;
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
        field_label: JP.scanners.fieldContext.extractFieldContext(el).label,
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

async function rescanAfterFill(filledSelector) {
  const JP = window.JobPulse;
  await JP.dom.delay(800);

  const result = {
    new_fields: [],
    validation_errors: [],
    snapshot: JP.detectors.snapshot.buildSnapshot(),
  };

  const filledEl = JP.dom.resolveSelector(filledSelector);
  if (filledEl) {
    const isInvalid = filledEl.getAttribute("aria-invalid") === "true";
    if (isInvalid) {
      const errId = filledEl.getAttribute("aria-errormessage");
      let errMsg = "";
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) errMsg = errEl.textContent.trim();
      }
      result.validation_errors.push({
        selector: filledSelector,
        error: errMsg || "Field marked as invalid",
      });
    }
  }

  for (const alert of document.querySelectorAll("[role='alert']")) {
    const text = alert.textContent.trim();
    if (text) {
      result.validation_errors.push({
        selector: "[role='alert']",
        error: text,
      });
    }
  }

  return result;
}

window.JobPulse.fillers.validate = { scanValidationErrors, rescanAfterFill };
