// extension/fillers/label_fill.js — Label-based element lookup, text fill, checkbox fill
// Changes when: label-based element lookup or text/check fill changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

function findByLabel(label) {
  const JP = window.JobPulse;
  const cleanLabel = clean(label);

  // Strategy 1: <label for="id"> exact match
  for (const lbl of document.querySelectorAll("label")) {
    if (clean(lbl.textContent) === cleanLabel) {
      const forId = lbl.getAttribute("for");
      if (forId) { const el = document.getElementById(forId); if (el && JP.dom.isFieldVisible(el)) return el; }
      const inner = lbl.querySelector("input, select, textarea, [contenteditable='true']");
      if (inner && JP.dom.isFieldVisible(inner)) return inner;
    }
  }
  // Strategy 2: aria-label exact
  for (const tag of ["input", "select", "textarea"]) {
    const el = document.querySelector(`${tag}[aria-label="${CSS.escape(label)}"]`);
    if (el && JP.dom.isFieldVisible(el)) return el;
  }
  // Strategy 3: placeholder
  for (const tag of ["input", "textarea"]) {
    const el = document.querySelector(`${tag}[placeholder="${CSS.escape(label)}"]`);
    if (el && JP.dom.isFieldVisible(el)) return el;
  }
  // Strategy 4: fuzzy — label contains or is contained
  for (const lbl of document.querySelectorAll("label")) {
    const t = clean(lbl.textContent);
    if (t && (t.includes(cleanLabel) || cleanLabel.includes(t))) {
      const forId = lbl.getAttribute("for");
      if (forId) return document.getElementById(forId);
      const inner = lbl.querySelector("input, select, textarea");
      if (inner) return inner;
    }
  }
  return null;
}

async function fillByLabel(label, value, fieldType) {
  const JP = window.JobPulse;
  await JP.dom.delay(JP.timing.getFieldGap(label));
  const el = findByLabel(label);
  if (!el) return { success: false, error: `No field found for label '${label}'` };
  await JP.dom.smartScroll(el);
  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);
  await JP.cursor.cursorClickFlash();

  const tag = el.tagName.toLowerCase();
  const inputType = (el.getAttribute("type") || "").toLowerCase();

  if (tag === "select") return JP.fillers.labelChoice.selectByLabel(el, value);
  if (inputType === "checkbox") return checkByLabel(el, value);
  if (inputType === "radio") return JP.fillers.labelChoice.radioByLabel(label, value);
  if (el.getAttribute("role") === "combobox" || el.getAttribute("role") === "listbox")
    return JP.fillers.labelChoice.comboboxByLabel(el, value);
  if (el.isContentEditable) return contentEditableByLabel(el, value);
  return textByLabel(el, value);
}

async function textByLabel(el, value) {
  const JP = window.JobPulse;
  el.focus(); el.dispatchEvent(new Event("focus", { bubbles: true }));
  JP.form.setNativeValue(el, ""); el.dispatchEvent(new Event("input", { bubbles: true }));
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    JP.form.setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    await JP.dom.delay(Math.max(30, JP.timing.getTypingDelay()));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  await JP.dom.delay(100);
  return { success: true, value_set: el.value, value_verified: el.value === value };
}

async function contentEditableByLabel(el, value) {
  const JP = window.JobPulse;
  el.focus(); el.dispatchEvent(new Event("focus", { bubbles: true }));
  el.innerText = ""; el.dispatchEvent(new Event("input", { bubbles: true }));
  for (const char of value) {
    document.execCommand("insertText", false, char);
    await JP.dom.delay(Math.max(30, JP.timing.getTypingDelay()));
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));
  const actual = (el.innerText || el.textContent || "").trim();
  return { success: true, value_set: actual, value_verified: actual.includes(value.substring(0, 20)) };
}

function checkByLabel(el, value) {
  const want = value === "true" || value === true || value === "yes";
  if (el.checked !== want) el.click();
  return { success: true, value_set: String(el.checked), value_verified: el.checked === want };
}

window.JobPulse.fillers.labelFill = { findByLabel, fillByLabel };
