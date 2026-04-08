// extension/core/form.js — Form value helpers (set, verify, normalize, fuzzy match)
//
// Zero dependencies on other core/ modules.

window.JobPulse = window.JobPulse || {};

/**
 * Set input value using the native setter — bypasses React/Vue/Angular
 * controlled component wrappers that ignore direct .value assignment.
 */
function setNativeValue(el, value) {
  const proto = el instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
  if (descriptor && descriptor.set) {
    descriptor.set.call(el, value);
  } else {
    el.value = value;
  }
}

/**
 * Verify a field's value matches what was intended.
 */
function verifyFieldValue(el, intended) {
  if (!el) return false;
  const tag = el.tagName.toLowerCase();

  if (tag === "select") {
    const selected = el.options[el.selectedIndex];
    return selected && (
      normalizeText(selected.text) === normalizeText(intended) ||
      normalizeText(selected.value) === normalizeText(intended)
    );
  }

  if (el.type === "radio") return el.checked;
  if (el.type === "checkbox") {
    const want = intended === "true" || intended === true || intended === "yes";
    return el.checked === want;
  }

  return (el.value || "") === intended ||
    (el.value || "").includes(intended.substring(0, 10));
}

const ABBREVIATIONS = {
  "uk": "united kingdom",
  "us": "united states",
  "usa": "united states of america",
  "nyc": "new york city",
  "sf": "san francisco",
  "la": "los angeles",
  "phd": "doctor of philosophy",
  "msc": "master of science",
  "bsc": "bachelor of science",
};

function normalizeText(text) {
  return (text || "").toLowerCase().trim().replace(/[.,;:!?]+$/, "");
}

function fuzzyMatchOption(value, options) {
  const norm = normalizeText(value);
  const expanded = ABBREVIATIONS[norm] || norm;

  for (const opt of options) {
    if (normalizeText(opt) === expanded) return opt;
  }
  for (const opt of options) {
    if (normalizeText(opt).startsWith(expanded)) return opt;
  }
  for (const opt of options) {
    if (normalizeText(opt).includes(expanded)) return opt;
  }
  for (const opt of options) {
    if (expanded.includes(normalizeText(opt)) && normalizeText(opt).length > 2) return opt;
  }
  return null;
}

window.JobPulse.form = { setNativeValue, verifyFieldValue, normalizeText, fuzzyMatchOption, ABBREVIATIONS };
