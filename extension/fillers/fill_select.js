// extension/fillers/fill_select.js — Native select, checkbox, and consent interaction
// Changes when: native select/checkbox interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

/**
 * Select a dropdown option using fuzzy matching (mirrors Python select_filler).
 */
async function selectOption(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const options = [];
  for (const opt of el.querySelectorAll("option")) {
    const text = opt.textContent.trim();
    if (text && !text.toLowerCase().startsWith("select")) {
      options.push({ text, value: opt.value, el: opt });
    }
  }

  if (options.length === 0) {
    await JP.dom.delay(2000);
    for (const opt of el.querySelectorAll("option")) {
      const text = opt.textContent.trim();
      if (text && !text.toLowerCase().startsWith("select")) {
        options.push({ text, value: opt.value, el: opt });
      }
    }
  }

  if (options.length === 0) {
    return { success: false, error: "No options found in select" };
  }

  const match = JP.form.fuzzyMatchOption(value, options.map(o => o.text));
  if (match) {
    const matched = options.find(o => o.text === match);
    if (matched) {
      el.value = matched.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: match, value_verified: JP.form.verifyFieldValue(el, match) };
    }
  }

  for (const opt of options) {
    if (JP.form.normalizeText(opt.value) === JP.form.normalizeText(value)) {
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: opt.text, value_verified: JP.form.verifyFieldValue(el, opt.text) };
    }
  }

  return {
    success: false,
    error: `No match for '${value}' in [${options.slice(0, 5).map(o => o.text).join(", ")}]`,
  };
}

/**
 * Check or uncheck a checkbox. Only clicks if state needs to change.
 */
async function checkBox(selector, shouldCheck) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const want = shouldCheck === "true" || shouldCheck === true;
  if (el.checked !== want) el.click();

  return { success: true, value_set: String(el.checked), value_verified: el.checked === want };
}

async function checkConsentBoxes(rootSelector) {
  const JP = window.JobPulse;
  const root = rootSelector ? JP.dom.resolveSelector(rootSelector) : document;
  if (!root) return { success: false, error: "Root not found" };

  const consentPattern = /agree|consent|terms|privacy|gdpr|accept|acknowledge|policy|conditions|certify|confirm.*read/i;
  const checkboxes = root.querySelectorAll("input[type='checkbox']");
  const checked = [];

  for (const cb of checkboxes) {
    if (cb.checked || cb.disabled) continue;

    let labelText = "";
    if (cb.id) {
      const labelEl = document.querySelector(`label[for='${cb.id}']`);
      if (labelEl) labelText = labelEl.textContent.trim();
    }
    if (!labelText && cb.parentElement) {
      labelText = cb.parentElement.textContent.trim();
    }
    if (!labelText) {
      labelText = cb.getAttribute("aria-label") || "";
    }

    if (consentPattern.test(labelText)) {
      cb.click();
      cb.dispatchEvent(new Event("change", { bubbles: true }));
      checked.push(labelText.substring(0, 60));
      await JP.dom.delay(200);
    }
  }

  return { success: true, checked_count: checked.length, labels: checked };
}

window.JobPulse.fillers.select = { selectOption, checkBox, checkConsentBoxes };
