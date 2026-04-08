// extension/fillers/label_fill_choice.js — Label-based select, combobox, radio
// Changes when: label-based choice control interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function selectByLabel(el, value) {
  const JP = window.JobPulse;
  const options = [...el.options].map(o => o.text.trim()).filter(Boolean);
  const match = JP.form.fuzzyMatchOption(value, options);
  if (!match) return { success: false, error: `No match for '${value}'`, available: options.slice(0, 10) };
  const opt = [...el.options].find(o => o.text.trim() === match);
  el.value = opt.value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return { success: true, value_set: match, value_verified: true };
}

async function comboboxByLabel(el, value) {
  const JP = window.JobPulse;
  // Click to open
  el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
  el.click();
  await JP.dom.delay(400);

  // Type to filter if there's an inner input
  const inputEl = el.tagName === "INPUT" ? el : el.querySelector("input");
  if (inputEl) {
    inputEl.focus();
    JP.form.setNativeValue(inputEl, "");
    const filterText = value.substring(0, 5);
    for (const char of filterText) {
      JP.form.setNativeValue(inputEl, inputEl.value + char);
      inputEl.dispatchEvent(new Event("input", { bubbles: true }));
      await JP.dom.delay(80);
    }
    await JP.dom.delay(600);
  }

  // Search for options
  const optSelectors = ["[role='option']", "[role='listbox'] li", "[class*='dropdown'] li",
    "[class*='option']", "ul li"];
  const valueLower = value.toLowerCase().trim();
  for (const sel of optSelectors) {
    for (const opt of document.querySelectorAll(sel)) {
      const text = opt.textContent.trim();
      if (!text || text.length > 200) continue;
      if (text.toLowerCase().includes(valueLower) || valueLower.includes(text.toLowerCase())) {
        opt.click(); await JP.dom.delay(200);
        return { success: true, value_set: text, value_verified: true };
      }
    }
  }
  // Close and report failure
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  return { success: false, error: `No match for '${value}' in combobox` };
}

async function radioByLabel(groupLabel, value) {
  const JP = window.JobPulse;
  // Find all radios, match their labels
  const allRadios = document.querySelectorAll('input[type="radio"]');
  const labelMap = [];
  for (const radio of allRadios) {
    const name = JP.scanners.label.getAccessibleName(radio);
    if (name) labelMap.push({ text: name, radio });
  }
  const match = JP.form.fuzzyMatchOption(value, labelMap.map(l => l.text));
  if (!match) return { success: false, error: `No matching radio for '${value}'` };
  const matched = labelMap.find(l => l.text === match);
  if (matched) {
    await JP.dom.smartScroll(matched.radio);
    matched.radio.click();
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match, value_verified: matched.radio.checked };
  }
  return { success: false, error: "Match found but click failed" };
}

window.JobPulse.fillers.labelChoice = { selectByLabel, comboboxByLabel, radioByLabel };
