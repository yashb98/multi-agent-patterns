// extension/fillers/fill_dropdown.js — Trigger-based dropdown and type-ahead autocomplete
// Changes when: trigger-based dropdown or type-ahead interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

async function fillCustomSelect(triggerSelector, value) {
  const JP = window.JobPulse;
  const trigger = JP.dom.resolveSelector(triggerSelector);
  if (!trigger) return { success: false, error: "Trigger not found: " + triggerSelector };

  await JP.dom.smartScroll(trigger);
  trigger.click();
  await JP.dom.delay(600);

  const searchInput = trigger.querySelector("input")
    || document.querySelector("[role='combobox'] input:focus")
    || document.querySelector(".select__input input, .search-typeahead input");

  if (searchInput) {
    JP.form.setNativeValue(searchInput, "");
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    const filterText = value.substring(0, Math.min(value.length, 5));
    for (const char of filterText) {
      JP.form.setNativeValue(searchInput, searchInput.value + char);
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      await JP.dom.delay(80 + Math.random() * 40);
    }
    await JP.dom.delay(800);
  }

  const optionSelectors = [
    "[role='option']",
    "[role='listbox'] li",
    ".select__option",
    ".basic-typeahead__selectable",
    "li.search-typeahead-v2__hit",
    ".dropdown-item",
    ".artdeco-dropdown__item",
    "ul[role='listbox'] > li",
  ];

  let optionEls = [];
  for (const sel of optionSelectors) {
    optionEls = document.querySelectorAll(sel);
    if (optionEls.length > 0) break;
  }

  if (optionEls.length === 0) {
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return { success: false, error: "No options visible after opening dropdown" };
  }

  const options = [];
  for (const opt of optionEls) {
    const text = opt.textContent.trim();
    if (text) options.push({ text, el: opt });
  }

  const match = JP.form.fuzzyMatchOption(value, options.map(o => o.text));
  if (!match) {
    document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    return {
      success: false,
      error: `No match for '${value}' in [${options.slice(0, 5).map(o => o.text).join(", ")}]`,
    };
  }

  const matched = options.find(o => o.text === match);
  if (matched) {
    matched.el.scrollIntoView({ block: "nearest" });
    await JP.dom.delay(200);
    matched.el.click();
    return { success: true, value_set: match, value_verified: true };
  }

  return { success: false, error: "Match found but click failed" };
}

async function fillAutocomplete(selector, value) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  await JP.dom.smartScroll(el);
  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  JP.form.setNativeValue(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await JP.dom.delay(200);

  const typeText = value.substring(0, Math.min(value.length, 5));
  for (const char of typeText) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    JP.form.setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    await JP.dom.delay(JP.timing.behaviorProfile.avg_typing_speed * (1 + (Math.random() - 0.5) * 0.3));
  }

  await JP.dom.delay(1500);

  const suggestionSelectors = [
    "[role='option']",
    "[role='listbox'] li",
    ".basic-typeahead__selectable",
    "li.search-typeahead-v2__hit",
    ".autocomplete-result",
    ".pac-item",
    ".suggestion-item",
    "ul.suggestions li",
  ];

  for (const sugSel of suggestionSelectors) {
    const suggestions = document.querySelectorAll(sugSel);
    if (suggestions.length === 0) continue;

    for (const sug of suggestions) {
      const sugText = sug.textContent.trim();
      if (sugText && (value.toLowerCase().includes(sugText.toLowerCase().substring(0, 5))
          || sugText.toLowerCase().includes(value.toLowerCase().substring(0, 5)))) {
        sug.click();
        await JP.dom.delay(300);
        return { success: true, value_set: sugText, value_verified: true };
      }
    }

    const firstSug = suggestions[0];
    if (firstSug) {
      const firstText = firstSug.textContent.trim();
      firstSug.click();
      await JP.dom.delay(300);
      return { success: true, value_set: firstText, used_first_suggestion: true, value_verified: true };
    }
  }

  JP.form.setNativeValue(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await JP.dom.delay(200);
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: value, no_suggestions: true, value_verified: el.value === value };
}

window.JobPulse.fillers.dropdown = { fillCustomSelect, fillAutocomplete };
