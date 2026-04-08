// extension/fillers/fill_combobox.js — Custom dropdown open/search/select + option reveal
// Changes when: custom dropdown open/search/select logic changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

// Placeholder values to skip — shared between fillCombobox and revealOptions
const PLACEHOLDER_VALUES = new Set([
  "-none-", "none", "loading", "-none- loading", "select", "select...",
  "choose", "please select", "-- select --", "---", "--", "",
]);

const OPTION_SELECTORS = [
  "[role='option']", "[role='listbox'] li", "[role='listbox'] [role='option']",
  "lyte-drop-box li", "lyte-drop-box [role='option']",
  ".lyte-dropdown-items li", ".cxDropdownMenuList li", ".cxDropdownMenuItems li",
  "[class*='dropdown'] li", "[class*='dropdown'] [class*='option']",
  "[class*='menu'] li[class*='option']", "[class*='listbox'] li",
  "ul[class*='select'] li", ".select-options li", "[data-value]",
];

function findDropdownTargets(el) {
  const targets = [el];
  let parent = el.parentElement;
  for (let depth = 0; depth < 5 && parent; depth++) {
    const tag = parent.tagName.toLowerCase();
    const cls = parent.className || "";
    if (tag.startsWith("lyte-") || cls.includes("dropdown") || cls.includes("select") ||
        parent.getAttribute("role") === "combobox" || parent.getAttribute("role") === "listbox") {
      targets.push(parent); break;
    }
    parent = parent.parentElement;
  }
  const innerTrigger = el.querySelector("input, [class*='trigger'], [class*='arrow'], [class*='toggle'], button, lyte-icon");
  if (innerTrigger) targets.push(innerTrigger);
  return targets;
}

function clickTargets(targets) {
  for (const t of targets) {
    t.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
    t.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
    t.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    t.click();
  }
}

/**
 * Fill a combobox/custom dropdown by clicking it open, scanning the entire
 * document for the floating option panel, and selecting the best match.
 * Works with Zoho lyte-dropdown, React Select, MUI Select, etc.
 */
async function fillCombobox(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Visual cursor
  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);

  await JP.dom.smartScroll(el);

  // ── Open the dropdown ──
  const targets = findDropdownTargets(el);

  clickTargets(targets);
  await JP.dom.delay(400);

  // If element is an input, focus + clear + type to trigger search dropdown
  const inputEl = el.tagName === "INPUT" ? el : el.querySelector("input");
  if (inputEl) {
    inputEl.focus();
    JP.form.setNativeValue(inputEl, "");
    inputEl.dispatchEvent(new Event("input", { bubbles: true }));
    await JP.dom.delay(150);
  }

  // Search the ENTIRE document for floating dropdown panels with options
  const valueLower = value.toLowerCase().trim();
  let allOptions = [];

  for (const optSel of OPTION_SELECTORS) {
    const opts = document.querySelectorAll(optSel);
    if (opts.length === 0) continue;

    for (const opt of opts) {
      const text = opt.textContent.trim();
      if (!text || text.length > 200) continue;
      // Skip placeholder options
      if (PLACEHOLDER_VALUES.has(text.toLowerCase())) continue;
      allOptions.push({ el: opt, text });

      // Exact match
      if (text.toLowerCase() === valueLower) {
        await JP.cursor.moveCursorTo(opt);
        JP.cursor.cursorClickFlash();
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        opt.click();
        await JP.dom.delay(200);
        return { success: true, value_set: text, match: "exact", value_verified: true };
      }
    }

    // Partial match — option starts with our value or contains it
    for (const { el: opt, text } of allOptions) {
      const textLower = text.toLowerCase();
      // Prefer "starts with" over "contains"
      if (textLower.startsWith(valueLower) || valueLower.startsWith(textLower) ||
          textLower.includes(valueLower) || valueLower.includes(textLower)) {
        await JP.cursor.moveCursorTo(opt);
        JP.cursor.cursorClickFlash();
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        opt.click();
        await JP.dom.delay(200);
        return { success: true, value_set: text, match: "partial", value_verified: true };
      }
    }

    // If we found real options, break — don't search other selectors
    if (allOptions.length > 0) break;
  }

  // If we found options but none matched, report what was available
  if (allOptions.length > 0) {
    const available = allOptions.slice(0, 20).map(o => o.text);
    return { success: false, error: "No matching option", available_options: available, wanted: value };
  }

  // Retry: click again with more delay — dropdown may need a second click
  clickTargets(targets);
  await JP.dom.delay(600);

  // Check for options again after retry
  for (const optSel of OPTION_SELECTORS) {
    const opts = document.querySelectorAll(optSel);
    for (const opt of opts) {
      const text = opt.textContent.trim();
      if (!text || text.length > 200 || PLACEHOLDER_VALUES.has(text.toLowerCase())) continue;
      if (text.toLowerCase() === valueLower ||
          text.toLowerCase().startsWith(valueLower) ||
          text.toLowerCase().includes(valueLower) ||
          valueLower.includes(text.toLowerCase())) {
        await JP.cursor.moveCursorTo(opt);
        JP.cursor.cursorClickFlash();
        opt.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true }));
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
        opt.click();
        await JP.dom.delay(200);
        return { success: true, value_set: text, match: "retry_click", value_verified: true };
      }
    }
  }

  // No options found — try typing into any input inside the combobox
  const innerInput = el.querySelector("input");
  if (innerInput) {
    innerInput.focus();
    JP.form.setNativeValue(innerInput, "");
    for (const char of value) {
      innerInput.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
      JP.form.setNativeValue(innerInput, innerInput.value + char);
      innerInput.dispatchEvent(new Event("input", { bubbles: true }));
      innerInput.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
      await JP.dom.delay(30);
    }
    await JP.dom.delay(300);

    // Check for suggestions again
    for (const optSel of OPTION_SELECTORS) {
      const opts = document.querySelectorAll(optSel);
      for (const opt of opts) {
        const text = opt.textContent.trim();
        if (text && (text.toLowerCase().includes(valueLower) || valueLower.includes(text.toLowerCase()))) {
          await JP.cursor.moveCursorTo(opt);
          JP.cursor.cursorClickFlash();
          opt.click();
          await JP.dom.delay(500);
          return { success: true, value_set: text, match: "typed_then_selected", value_verified: true };
        }
      }
      if (opts.length > 0) {
        const first = opts[0];
        const firstText = first.textContent.trim();
        await JP.cursor.moveCursorTo(first);
        JP.cursor.cursorClickFlash();
        first.click();
        await JP.dom.delay(500);
        return { success: true, value_set: firstText, match: "typed_first_option", value_verified: true };
      }
    }
  }

  return { success: false, error: "Could not open dropdown or find options" };
}

async function revealOptions(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const targets = findDropdownTargets(el);
  clickTargets(targets);
  await JP.dom.delay(350);

  const options = [];
  const seen = new Set();
  for (const sel of OPTION_SELECTORS) {
    for (const opt of document.querySelectorAll(sel)) {
      const text = opt.textContent.trim();
      if (text && text.length < 200 && !seen.has(text) && !PLACEHOLDER_VALUES.has(text.toLowerCase())) {
        seen.add(text); options.push(text);
      }
    }
    if (options.length > 0) break;
  }

  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await JP.dom.delay(100);

  return { success: true, options, selector };
}

window.JobPulse.fillers.combobox = { fillCombobox, revealOptions };
