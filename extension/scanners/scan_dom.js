// extension/scanners/scan_dom.js — Deep DOM scanning for form fields
//
// Depends on: core/dom.js, scanners/field_info.js
// Exports: window.JobPulse.scanners.dom.deepScan, .scanFormGroups

window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

/**
 * Recursively scan for form fields across:
 *   1. Regular DOM elements
 *   2. Shadow DOM roots
 *   3. Same-origin iframes (cross-origin iframes are skipped)
 *
 * Max depth: 5 levels to prevent runaway recursion.
 */
function deepScan(root, depth, iframeIndex) {
  const JP = window.JobPulse;
  root = root || document;
  depth = depth || 0;
  iframeIndex = iframeIndex === undefined ? null : iframeIndex;
  const fields = [];
  if (depth > 5) return fields; // Safety limit

  // 1. Regular form fields
  const selector =
    "input:not([type='hidden']), select, textarea, [contenteditable='true'], " +
    "[role='listbox'], [role='combobox'], [role='radiogroup'], [role='switch'], [role='textbox']";
  for (const el of root.querySelectorAll(selector)) {
    // Skip honeypot fields — hidden inputs that bots fill, humans never see
    // Exception: file inputs are often hidden and triggered via custom upload buttons
    if (!JP.dom.isFieldVisible(el) && el.type !== "file") continue;
    fields.push(JP.scanners.fieldInfo.extractFieldInfo(el, iframeIndex));
  }

  // 2. Shadow DOM roots
  root.querySelectorAll("*").forEach((el) => {
    if (el.shadowRoot) {
      fields.push(...deepScan(el.shadowRoot, depth + 1, iframeIndex));
    }
  });

  // 3. Same-origin iframes
  root.querySelectorAll("iframe").forEach((iframe, idx) => {
    try {
      if (iframe.contentDocument) {
        fields.push(...deepScan(iframe.contentDocument, depth + 1, idx));
      }
    } catch (_) {
      // Cross-origin iframe — skip (would need background.js injection)
    }
  });

  return fields;
}

function scanFormGroups(rootSelector) {
  const JP = window.JobPulse;
  const root = rootSelector ? JP.dom.resolveSelector(rootSelector) : document;
  if (!root) return [];

  const groupSelectors =
    "fieldset, .form-group, .field, [data-test-form-element], " +
    ".jobs-easy-apply-form-section__grouping, .fb-dash-form-element, " +
    ".application-question, .field-wrapper";

  const groups = [];
  const seen = new Set();

  for (const group of root.querySelectorAll(groupSelectors)) {
    const labelEl = group.querySelector(
      "label, legend, .field-label, .application-label, " +
      ".fb-form-element-label, span.t-14, span.t-bold"
    );
    const question = labelEl ? labelEl.textContent.trim().substring(0, 300) : "";
    if (!question || question.length < 2) continue;

    const inputSelector =
      "input:not([type='hidden']):not([type='submit']), select, textarea, " +
      "[contenteditable='true'], [role='listbox'], [role='combobox'], " +
      "[role='radiogroup'], [role='switch'], [role='textbox']";
    const inputs = group.querySelectorAll(inputSelector);
    if (inputs.length === 0) continue;

    const fields = [];
    let isAnswered = true;

    for (const inp of inputs) {
      if (seen.has(inp)) continue;
      seen.add(inp);
      // Skip honeypot fields within groups
      if (!JP.dom.isFieldVisible(inp) && inp.type !== "file") continue;
      const fieldInfo = JP.scanners.fieldInfo.extractFieldInfo(inp, null);
      fields.push(fieldInfo);

      const val = inp.value || inp.textContent || "";
      const isRadioChecked = inp.type === "radio" && inp.checked;
      const isCheckboxChecked = inp.type === "checkbox" && inp.checked;
      if (!val.trim() && !isRadioChecked && !isCheckboxChecked) {
        isAnswered = false;
      }
    }

    let grpSelector = "";
    if (group.id) grpSelector = `#${group.id}`;
    else {
      const tag = group.tagName.toLowerCase();
      const cls = (group.className && typeof group.className === "string")
        ? group.className.split(/\s+/).filter(c => c.length > 3)[0] : "";
      grpSelector = cls ? `${tag}.${cls}` : tag;
    }

    const helpEl = group.querySelector(".help-text, .field-hint, .description, [class*='helper']");
    const helpText = helpEl ? helpEl.textContent.trim().substring(0, 200) : "";

    const isRequired = group.querySelector("[required], [aria-required='true']") !== null
      || /\*|required/i.test(question);

    groups.push({
      group_selector: grpSelector,
      question,
      fields,
      is_required: isRequired,
      is_answered: isAnswered,
      fieldset_legend: group.closest("fieldset")?.querySelector("legend")?.textContent?.trim() || "",
      help_text: helpText,
    });
  }

  return groups;
}

window.JobPulse.scanners.dom = { deepScan, scanFormGroups };
