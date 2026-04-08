// extension/fillers/fill_simple.js — Tag input, date, and file upload
// Changes when: tag/date/file input handling changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillTagInput(selector, values) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  await JP.dom.smartScroll(el);
  el.focus();

  const added = [];
  for (const val of values) {
    JP.form.setNativeValue(el, "");
    el.dispatchEvent(new Event("input", { bubbles: true }));

    for (const char of val) {
      JP.form.setNativeValue(el, el.value + char);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      await JP.dom.delay(60 + Math.random() * 40);
    }
    await JP.dom.delay(300);
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true }));
    await JP.dom.delay(400);
    added.push(val);
  }

  return { success: true, value_set: added.join(", "), count: added.length, value_verified: added.length > 0 };
}

async function fillDate(selector, isoDate) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  await JP.dom.smartScroll(el);

  const inputType = (el.getAttribute("type") || "text").toLowerCase();

  // Native date input
  if (inputType === "date") {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value"
    ).set;
    nativeInputValueSetter.call(el, isoDate);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: isoDate, value_verified: el.value === isoDate };
  }

  // Detect date format from placeholder, aria-label, or parent context
  const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
  const ariaLabel = (el.getAttribute("aria-label") || "").toLowerCase();
  const parentText = (el.closest("[class*='date']")?.textContent || "").toLowerCase();
  const formatHint = placeholder || ariaLabel || parentText;
  let formatted = isoDate;
  try {
    const [y, m, d] = isoDate.split("-");
    if (formatHint.includes("dd/mm")) {
      formatted = `${d}/${m}/${y}`;
    } else if (formatHint.includes("mm/dd")) {
      formatted = `${m}/${d}/${y}`;
    } else if (formatHint.includes("dd-mm")) {
      formatted = `${d}-${m}-${y}`;
    } else if (formatHint.includes("mm-dd")) {
      formatted = `${m}-${d}-${y}`;
    }
  } catch (_) { /* keep ISO format */ }

  // Try native value setter first (works with React/Vue/Lyte controlled inputs)
  el.focus();
  JP.form.setNativeValue(el, formatted);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  await JP.dom.delay(200);

  // If value didn't stick, try character-by-character typing
  if (!el.value || el.value !== formatted) {
    JP.form.setNativeValue(el, "");
    el.dispatchEvent(new Event("input", { bubbles: true }));
    for (const char of formatted) {
      el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
      JP.form.setNativeValue(el, el.value + char);
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
      await JP.dom.delay(60 + Math.random() * 30);
    }
  }

  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // Dismiss any date picker popup that appeared
  document.body.click();
  await JP.dom.delay(100);

  return { success: true, value_set: formatted, value_verified: el.value.includes(formatted.substring(0, 4)) };
}

async function uploadFile(selector, base64Data, fileName, mimeType) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const bytes = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
  const file = new File([bytes], fileName, { type: mimeType || "application/pdf" });

  const dt = new DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new Event("change", { bubbles: true }));

  return { success: true, value_set: fileName };
}

window.JobPulse.fillers.simple = { fillTagInput, fillDate, uploadFile };
