// extension/fillers/fill_text.js — Human-like text input and contenteditable filling
// Changes when: text input or rich text typing interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

/**
 * Fill a text field with human-like character-by-character typing.
 * Dispatches proper keyboard events so React/Angular forms register the input.
 */
async function fillField(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Detect contenteditable (Lever, some Workday forms)
  if (el.getAttribute("contenteditable") === "true" || el.isContentEditable) {
    return fillContentEditable(el, value);
  }

  // Scroll-aware timing: measure if scroll actually happens
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await JP.dom.delay(scrollWait);

  // Visual cursor + highlight
  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);
  await JP.cursor.cursorClickFlash();

  // Smart read-time: longer labels = more reading time
  const labelLength = (el.getAttribute("aria-label") || el.placeholder || "").length;
  const readDelay = Math.min(1500, 200 + labelLength * 15);
  await JP.dom.delay(readDelay);

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing value using native setter (React-safe)
  JP.form.setNativeValue(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type each character with realistic timing variance
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    JP.form.setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    const speed = JP.timing.behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * JP.timing.behaviorProfile.typing_variance);
    await JP.dom.delay(Math.max(30, speed));
  }

  // Finalize: blur triggers validation on most forms
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // ── Post-fill verification ──
  await JP.dom.delay(100);
  const actualValue = el.value || "";
  const verified = actualValue === value;

  if (!verified && actualValue !== value) {
    JP.form.setNativeValue(el, value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await JP.dom.delay(100);

    const retryValue = el.value || "";
    return {
      success: retryValue === value || retryValue.length > 0,
      value_set: retryValue,
      value_verified: retryValue === value,
      retried: true,
    };
  }

  return { success: true, value_set: actualValue, value_verified: true };
}

/**
 * Fill a contenteditable element (Lever cover letter, Workday rich text).
 * Uses document.execCommand('insertText') which preserves undo stack
 * and triggers framework event listeners correctly.
 */
async function fillContentEditable(el, value) {
  const JP = window.JobPulse;
  if (!el) return { success: false, error: "Contenteditable element is null" };

  // Scroll-aware timing
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await JP.dom.delay(scrollWait);

  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);
  await JP.cursor.cursorClickFlash();

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing content
  el.innerText = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type character by character using execCommand
  for (const char of value) {
    document.execCommand("insertText", false, char);
    const speed = JP.timing.behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * JP.timing.behaviorProfile.typing_variance);
    await JP.dom.delay(Math.max(30, speed));
  }

  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // Verify
  const actualText = (el.innerText || el.textContent || "").trim();
  const verified = actualText.includes(value.substring(0, 20));

  return {
    success: true,
    value_set: actualText,
    value_verified: verified,
    contenteditable: true,
  };
}

window.JobPulse.fillers.text = { fillField, fillContentEditable };
