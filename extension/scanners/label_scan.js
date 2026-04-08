// extension/scanners/label_scan.js — Role-based field discovery using accessible names
window.JobPulse = window.JobPulse || {};
window.JobPulse.scanners = window.JobPulse.scanners || {};

const clean = (s) => (s || "").replace(/\s+/g, " ").trim();

function getAccessibleName(el) {
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const text = labelledBy.split(/\s+/)
      .map(id => document.getElementById(id)?.textContent?.trim())
      .filter(Boolean).join(" ");
    if (text) return clean(text);
  }
  if (el.id) {
    const labelFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (labelFor) return clean(labelFor.textContent);
  }
  const wrapping = el.closest("label");
  if (wrapping) {
    const clone = wrapping.cloneNode(true);
    clone.querySelectorAll("input,select,textarea").forEach(c => c.remove());
    const t = clean(clone.textContent);
    if (t) return t;
  }
  if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
  if (el.placeholder) return clean(el.placeholder);
  return "";
}

function buildLocatorHint(el) {
  return {
    tag: el.tagName.toLowerCase(),
    id: el.id || null,
    name: el.name || null,
    type: el.type || null,
    ariaLabel: el.getAttribute("aria-label") || null,
    index: el.id ? null : [...(el.parentElement?.children || [])].filter(
      c => c.tagName === el.tagName
    ).indexOf(el),
  };
}

function scan(root) {
  const JP = window.JobPulse;
  const fields = [];
  root = root || document;

  // Text inputs
  for (const el of root.querySelectorAll(
    'input:not([type="hidden"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"]):not([type="file"])'
  )) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const label = getAccessibleName(el);
    if (!label) continue;
    fields.push({
      label, type: (el.type || "text").toLowerCase(),
      value: el.value || "", required: el.required || el.getAttribute("aria-required") === "true",
      locator_hint: buildLocatorHint(el),
    });
  }

  // Native <select>
  for (const el of root.querySelectorAll("select")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const label = getAccessibleName(el);
    const options = [...el.options].map(o => o.text.trim())
      .filter(t => t && !/^select|^choose|^--/i.test(t));
    fields.push({ label: label || "", type: "select", options, value: el.value,
      locator_hint: buildLocatorHint(el) });
  }

  // Textareas
  for (const el of root.querySelectorAll("textarea")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "textarea", value: el.value || "",
      required: el.required || el.getAttribute("aria-required") === "true",
      locator_hint: buildLocatorHint(el) });
  }

  // Checkboxes
  for (const el of root.querySelectorAll('input[type="checkbox"]')) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "checkbox", checked: el.checked,
      locator_hint: buildLocatorHint(el) });
  }

  // Radio groups
  const radioNames = new Set();
  for (const el of root.querySelectorAll('input[type="radio"]')) {
    if (!JP.dom.isFieldVisible(el) || !el.name || radioNames.has(el.name)) continue;
    radioNames.add(el.name);
    const radios = root.querySelectorAll(`input[type="radio"][name="${el.name}"]`);
    const options = [...radios].map(r => getAccessibleName(r)).filter(Boolean);
    const group = el.closest('[role="radiogroup"]') || el.closest("fieldset");
    const groupLabel = group ? getAccessibleName(group) || clean(group.querySelector("legend")?.textContent) : "";
    fields.push({ label: groupLabel || options[0] || el.name, type: "radio", options,
      locator_hint: { name: el.name } });
  }

  // File inputs
  for (const el of root.querySelectorAll('input[type="file"]')) {
    fields.push({ label: getAccessibleName(el) || "file upload", type: "file",
      locator_hint: buildLocatorHint(el) });
  }

  // Contenteditable
  for (const el of root.querySelectorAll('[contenteditable="true"]')) {
    if (!JP.dom.isFieldVisible(el)) continue;
    fields.push({ label: getAccessibleName(el), type: "contenteditable",
      value: (el.innerText || "").trim(), locator_hint: buildLocatorHint(el) });
  }

  return fields;
}

window.JobPulse.scanners.label = { scan, getAccessibleName, buildLocatorHint };
