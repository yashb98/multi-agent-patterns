// extension/content.js — Page Scanner & Form Automation
//
// Runs on every page (<all_urls>, document_idle, all_frames).
//
// Responsibilities:
//   1. Deep page scanning: form fields, buttons, shadow DOM, iframes
//   2. Verification wall detection: Cloudflare, reCAPTCHA, hCaptcha
//   3. Form filling with human-like typing (calibrated from real behavior)
//   4. File upload via DataTransfer API
//   5. Local AI field analysis via Gemini Nano (Chrome built-in AI)
//   6. MutationObserver: sends snapshot updates on DOM changes
//   7. Navigation event: sends full snapshot after page load

// ═══════════════════════════════════════════════════════════════
// Behavior Profile — human-like interaction calibration
// ═══════════════════════════════════════════════════════════════
//
// Passively observes real user keystrokes and clicks to calibrate
// typing speed and interaction timing. After 500+ keystrokes,
// the profile is saved to chrome.storage for future sessions.

const behaviorProfile = {
  avg_typing_speed: 80,    // ms per character
  typing_variance: 0.3,    // 0-1 randomness factor
  scroll_speed: 400,       // px/s for smooth scrolling
  reading_pause: 1.0,      // seconds pause before clicking
  field_to_field_gap: 500, // ms delay between form fields
  click_offset: { x: 0, y: 0 },
  calibrated: false,
  keystrokes: 0,
  clicks: 0,
};

// Restore saved profile from previous sessions
chrome.storage.local.get("behaviorProfile", (data) => {
  if (data.behaviorProfile) Object.assign(behaviorProfile, data.behaviorProfile);
});

// Passive calibration: learn from real user typing speed
document.addEventListener("keydown", () => {
  const now = performance.now();
  if (behaviorProfile._lastKey) {
    const gap = now - behaviorProfile._lastKey;
    // Only count plausible keystroke gaps (20-500ms)
    if (gap > 20 && gap < 500) {
      behaviorProfile.avg_typing_speed =
        behaviorProfile.avg_typing_speed * 0.95 + gap * 0.05; // Exponential moving average
    }
  }
  behaviorProfile._lastKey = now;
  behaviorProfile.keystrokes++;

  // Save after enough samples for statistical significance
  if (behaviorProfile.keystrokes > 500 && !behaviorProfile.calibrated) {
    behaviorProfile.calibrated = true;
    chrome.storage.local.set({ behaviorProfile });
  }
}, { passive: true });

document.addEventListener("click", () => { behaviorProfile.clicks++; }, { passive: true });

// ═══════════════════════════════════════════════════════════════
// Utilities
// ═══════════════════════════════════════════════════════════════

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * Resolve a CSS selector, including shadow DOM paths.
 * Shadow DOM syntax: "host-selector>>inner-selector"
 * Example: "#my-component>>input.email"
 */
function resolveSelector(selector) {
  if (selector.includes(">>")) {
    const parts = selector.split(">>");
    let el = document.querySelector(parts[0].trim());
    for (let i = 1; i < parts.length && el; i++) {
      el = (el.shadowRoot || el).querySelector(parts[i].trim());
    }
    return el;
  }
  return document.querySelector(selector);
}

// ═══════════════════════════════════════════════════════════════
// Fuzzy Matching — mirrors Python select_filler._fuzzy_match_option
// ═══════════════════════════════════════════════════════════════

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

// ═══════════════════════════════════════════════════════════════
// Deep Page Scanner
// ═══════════════════════════════════════════════════════════════

/**
 * Extract structured field info from a form element.
 * Maps HTML input types + ARIA roles to our FieldInfo schema.
 */
function extractFieldInfo(el, iframeIndex) {
  const tag = el.tagName.toLowerCase();

  // Determine input type from tag, type attribute, and ARIA role
  let inputType = "text";
  if (tag === "select") inputType = "select";
  else if (tag === "textarea") inputType = "textarea";
  else if (el.getAttribute("contenteditable") === "true") inputType = "rich_text";
  else if (el.getAttribute("role") === "listbox") inputType = "custom_select";
  else if (el.getAttribute("role") === "combobox") inputType = "search_autocomplete";
  else if (el.getAttribute("role") === "radiogroup") inputType = "radio";
  else if (el.getAttribute("role") === "switch") inputType = "toggle";
  else inputType = (el.getAttribute("type") || "text").toLowerCase();

  // Find label: explicit <label for=>, wrapping <label>, aria-label, placeholder
  let label = "";
  const labelEl = el.closest("label") || (el.id && document.querySelector(`label[for="${el.id}"]`));
  if (labelEl) label = labelEl.textContent.trim();
  if (!label) label = el.getAttribute("aria-label") || el.getAttribute("placeholder") || "";

  // Extract <select> options (skip placeholder "Select..." options)
  const options = [];
  if (tag === "select") {
    el.querySelectorAll("option").forEach((opt) => {
      const text = opt.textContent.trim();
      if (text && !text.toLowerCase().startsWith("select")) options.push(text);
    });
  }

  // Build a unique CSS selector for this element
  let selector = "";
  if (el.id) {
    // Use attribute selector for IDs with special chars (React uses :r4: etc)
    selector = /[:#.\[\]]/.test(el.id) ? `[id="${el.id}"]` : `#${el.id}`;
  }
  else if (el.name) selector = `${tag}[name="${el.name}"]`;
  else {
    const parent = el.parentElement;
    if (parent) {
      const siblings = Array.from(parent.querySelectorAll(tag));
      selector = `${tag}:nth-of-type(${siblings.indexOf(el) + 1})`;
    }
  }

  return {
    selector,
    input_type: inputType,
    label: label.substring(0, 200),
    required: el.required || el.getAttribute("aria-required") === "true",
    current_value: el.value || el.textContent || "",
    options,
    attributes: {
      name: el.name || "",
      id: el.id || "",
      placeholder: el.placeholder || "",
      "aria-label": el.getAttribute("aria-label") || "",
    },
    in_shadow_dom: false,
    in_iframe: iframeIndex !== null && iframeIndex !== undefined,
    iframe_index: iframeIndex,
    // v2: parent context for form intelligence
    group_label: (() => {
      const group = el.closest("fieldset, .form-group, .field, [data-test-form-element], .jobs-easy-apply-form-section__grouping, .fb-dash-form-element");
      if (!group) return "";
      const legend = group.querySelector("label, legend, .field-label, .fb-form-element-label, span.t-14");
      return legend ? legend.textContent.trim().substring(0, 200) : "";
    })(),
    group_selector: (() => {
      const group = el.closest("fieldset, .form-group, .field, [data-test-form-element], .jobs-easy-apply-form-section__grouping, .fb-dash-form-element");
      if (!group) return "";
      if (group.id) return `#${group.id}`;
      const tag = group.tagName.toLowerCase();
      const cls = group.className && typeof group.className === "string"
        ? group.className.split(/\s+/).filter(c => c.length > 3)[0]
        : "";
      return cls ? `${tag}.${cls}` : tag;
    })(),
    parent_text: (() => {
      const p = el.parentElement;
      return p ? p.textContent.trim().substring(0, 300) : "";
    })(),
    fieldset_legend: (() => {
      const fs = el.closest("fieldset");
      if (!fs) return "";
      const leg = fs.querySelector("legend");
      return leg ? leg.textContent.trim() : "";
    })(),
    help_text: (() => {
      const describedBy = el.getAttribute("aria-describedby");
      if (describedBy) {
        const desc = document.getElementById(describedBy);
        if (desc) return desc.textContent.trim().substring(0, 200);
      }
      const next = el.nextElementSibling;
      if (next && /help|hint|description|info/.test(next.className || "")) {
        return next.textContent.trim().substring(0, 200);
      }
      return "";
    })(),
    error_text: (() => {
      const errId = el.getAttribute("aria-errormessage");
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) return errEl.textContent.trim();
      }
      const parent = el.closest(".form-group, .field-wrapper, .form-field, [data-test-form-element]");
      if (parent) {
        const errEl = parent.querySelector(".error, .invalid-feedback, [role='alert'], .field-error");
        if (errEl) return errEl.textContent.trim();
      }
      return "";
    })(),
    aria_describedby: el.getAttribute("aria-describedby") || "",
  };
}

/**
 * Recursively scan for form fields across:
 *   1. Regular DOM form elements (input, select, textarea, ARIA roles)
 *   2. Shadow DOM roots (web components)
 *   3. Same-origin iframes (cross-origin iframes are skipped)
 *
 * Max depth: 5 levels to prevent runaway recursion.
 */
function deepScan(root, depth, iframeIndex) {
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
    fields.push(extractFieldInfo(el, iframeIndex));
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
  const root = rootSelector ? resolveSelector(rootSelector) : document;
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
      const fieldInfo = extractFieldInfo(inp, null);
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

/**
 * Detect CAPTCHA / verification walls.
 * Checks: DOM selectors, iframe sources, and body text patterns.
 * Returns null if no wall detected.
 */
function detectVerificationWall() {
  // Check for known CAPTCHA DOM elements
  const captchaSelectors = [
    { sel: "#challenge-running, .cf-turnstile, #cf-challenge-running", type: "cloudflare", conf: 0.95 },
    { sel: ".g-recaptcha, #recaptcha-anchor, [data-sitekey]", type: "recaptcha", conf: 0.90 },
    { sel: ".h-captcha", type: "hcaptcha", conf: 0.90 },
  ];
  for (const { sel, type, conf } of captchaSelectors) {
    if (document.querySelector(sel)) return { wall_type: type, confidence: conf, details: sel };
  }

  // Check iframe sources for CAPTCHA services
  for (const frame of document.querySelectorAll("iframe")) {
    const src = frame.src || "";
    if (src.includes("challenges.cloudflare.com")) return { wall_type: "cloudflare", confidence: 0.95, details: src };
    if (src.includes("google.com/recaptcha")) return { wall_type: "recaptcha", confidence: 0.90, details: src };
    if (src.includes("hcaptcha.com")) return { wall_type: "hcaptcha", confidence: 0.90, details: src };
  }

  // Check body text for verification/block messages
  const body = document.body?.innerText?.toLowerCase() || "";
  if (/verify you are human|are you a robot|confirm you're not a robot/.test(body))
    return { wall_type: "text_challenge", confidence: 0.85, details: "text match" };
  if (/access denied|403 forbidden|you have been blocked/.test(body))
    return { wall_type: "http_block", confidence: 0.80, details: "text match" };

  return null;
}

/**
 * Build a complete PageSnapshot of the current page state.
 * This is the primary data structure sent to the Python backend.
 */
function buildSnapshot() {
  const fields = deepScan();

  // Extract clickable buttons and submit elements
  // Include role='button' elements + <a> tags with button-like classes (LinkedIn uses a.artdeco-button for Apply)
  const buttons = [];
  const seen = new Set();
  document.querySelectorAll("button, input[type='submit'], [role='button'], [class*='apply'], [class*='btn'], a[class*='button']").forEach((el) => {
    if (seen.has(el)) return;
    seen.add(el);
    // Get text: textContent first, then aria-label, then value
    let text = el.textContent?.trim() || "";
    // Clean up multi-line/whitespace text from nested elements
    if (text) text = text.replace(/\s+/g, " ").trim();
    // Fallback to aria-label (LinkedIn uses this for icon-only buttons)
    if (!text) text = el.getAttribute("aria-label") || "";
    if (!text) text = el.value || "";
    if (text) {
      const tag = el.tagName.toLowerCase();
      let selector = "";
      if (el.id) {
        // Use attribute selector for IDs with special chars (React uses :r4: etc)
        if (/[:#.\[\]]/.test(el.id)) selector = `[id="${el.id}"]`;
        else selector = `#${el.id}`;
      }
      else if (el.className && typeof el.className === "string") {
        // Prefer jobs-apply-button (LinkedIn) or other meaningful class
        const classes = el.className.split(/\s+/).filter(c => c.length > 3);
        const applyClass = classes.find(c => c.includes("apply") || c.includes("submit"));
        const meaningful = applyClass || classes.find(c => !c.startsWith("artdeco"));
        if (meaningful) selector = `${tag}.${meaningful}`;
        else if (classes[0]) selector = `${tag}.${classes[0]}`;
      }
      if (!selector) selector = `${tag}:nth-of-type(${buttons.length + 1})`;
      buttons.push({
        selector,
        text: text.substring(0, 100),
        type: el.type || (tag === "a" ? "link" : "button"),
        enabled: !el.disabled && !el.getAttribute("aria-disabled"),
      });
    }
  });

  // Detect modal (LinkedIn Easy Apply, generic dialogs)
  const modal = document.querySelector(
    "[role='dialog'], .artdeco-modal, .jobs-easy-apply-modal, " +
    "[aria-modal='true'], .modal--open, .modal-dialog"
  );
  const modalDetected = modal !== null;

  // Parse progress indicators ("Step 2 of 5", "Page 1/3")
  let progress = null;
  const pageText = document.body?.innerText || "";
  const progressMatch = pageText.match(/(?:step|page)\s+(\d+)\s+(?:of|\/)\s+(\d+)/i)
    || pageText.match(/(\d+)\s+(?:of|\/)\s+(\d+)/);
  if (progressMatch) {
    const current = parseInt(progressMatch[1]);
    const total = parseInt(progressMatch[2]);
    if (current >= 1 && current <= total && total <= 20) {
      progress = [current, total];
    }
  }

  // Scan form groups (scoped to modal if present)
  const formGroups = scanFormGroups(modal ? (
    modal.id ? `#${modal.id}` : "[role='dialog']"
  ) : null);

  return {
    url: window.location.href,
    title: document.title,
    fields,
    buttons,
    verification_wall: detectVerificationWall(),
    page_text_preview: (document.body?.innerText || "").substring(0, 500),
    has_file_inputs: document.querySelector("input[type='file']") !== null,
    iframe_count: document.querySelectorAll("iframe").length,
    page_stable:
      !document.querySelector('[aria-busy="true"]') &&
      !document.querySelector('.loading, .spinner, [class*="loading"]'),
    timestamp: Date.now(),
    // v2 additions
    form_groups: formGroups,
    progress,
    modal_detected: modalDetected,
  };
}

// ═══════════════════════════════════════════════════════════════
// Form Actions — fill, click, upload, select, check
// ═══════════════════════════════════════════════════════════════

/**
 * Fill a text field with human-like character-by-character typing.
 * Dispatches proper keyboard events so React/Angular forms register the input.
 */
async function fillField(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Scroll into view and pause (mimics human reading the label)
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing value
  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type each character with realistic timing variance
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
  }

  // Finalize: blur triggers validation on most forms
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: el.value };
}

/**
 * Upload a file via DataTransfer API.
 * Receives base64 data from Python, creates a File object, assigns to input.
 */
async function uploadFile(selector, base64Data, fileName, mimeType) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const bytes = Uint8Array.from(atob(base64Data), (c) => c.charCodeAt(0));
  const file = new File([bytes], fileName, { type: mimeType || "application/pdf" });

  const dt = new DataTransfer();
  dt.items.add(file);
  el.files = dt.files;
  el.dispatchEvent(new Event("change", { bubbles: true }));

  return { success: true, value_set: fileName };
}

/**
 * Click an element with human-like scroll-into-view and reading pause.
 */
async function clickElement(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.reading_pause * 500 * (0.5 + Math.random()));
  el.click();

  return { success: true };
}

/**
 * Select a dropdown option using fuzzy matching (mirrors Python select_filler).
 */
async function selectOption(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const options = [];
  for (const opt of el.querySelectorAll("option")) {
    const text = opt.textContent.trim();
    if (text && !text.toLowerCase().startsWith("select")) {
      options.push({ text, value: opt.value, el: opt });
    }
  }

  if (options.length === 0) {
    await delay(2000);
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

  const match = fuzzyMatchOption(value, options.map(o => o.text));
  if (match) {
    const matched = options.find(o => o.text === match);
    if (matched) {
      el.value = matched.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: match };
    }
  }

  for (const opt of options) {
    if (normalizeText(opt.value) === normalizeText(value)) {
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: opt.text };
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
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const want = shouldCheck === "true" || shouldCheck === true;
  if (el.checked !== want) el.click();

  return { success: true, value_set: String(el.checked) };
}

// ═══════════════════════════════════════════════════════════════
// v2 Form Engine — Specialized Fill Handlers
// ═══════════════════════════════════════════════════════════════

async function fillRadioGroup(groupSelector, value) {
  let radios;
  const container = resolveSelector(groupSelector);
  if (container && container.tagName.toLowerCase() !== "input") {
    radios = container.querySelectorAll("input[type='radio']");
  } else {
    const nameEl = resolveSelector(groupSelector);
    if (nameEl) {
      const name = nameEl.getAttribute("name");
      radios = name
        ? document.querySelectorAll(`input[type='radio'][name='${name}']`)
        : [nameEl];
    } else {
      return { success: false, error: "Radio group not found: " + groupSelector };
    }
  }

  if (!radios || radios.length === 0) {
    return { success: false, error: "No radio buttons found in: " + groupSelector };
  }

  const labelMap = [];
  for (const radio of radios) {
    let labelText = "";
    let labelEl = null;

    const radioId = radio.id;
    if (radioId) {
      labelEl = document.querySelector(`label[for='${radioId}']`);
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    if (!labelText) {
      labelEl = radio.closest("label");
      if (labelEl) labelText = labelEl.textContent.trim();
    }

    if (!labelText) {
      labelText = radio.getAttribute("aria-label") || "";
    }

    if (!labelText && radio.nextSibling) {
      labelText = (radio.nextSibling.textContent || "").trim();
    }

    if (!labelText && radio.parentElement) {
      labelText = radio.parentElement.textContent.trim();
    }

    if (labelText) {
      labelMap.push({ text: labelText, radio, labelEl });
    }
  }

  if (labelMap.length === 0) {
    return { success: false, error: "No labels found for radio buttons" };
  }

  const labels = labelMap.map(l => l.text);
  const match = fuzzyMatchOption(value, labels);

  if (!match) {
    return {
      success: false,
      error: `No matching radio for '${value}' in [${labels.slice(0, 5).join(", ")}]`,
    };
  }

  const matched = labelMap.find(l => l.text === match);
  if (matched) {
    const target = matched.labelEl || matched.radio;
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    await delay(behaviorProfile.reading_pause * 300 * (0.5 + Math.random()));
    target.click();
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match };
  }

  return { success: false, error: "Match found but click failed" };
}

async function fillCustomSelect(triggerSelector, value) {
  const trigger = resolveSelector(triggerSelector);
  if (!trigger) return { success: false, error: "Trigger not found: " + triggerSelector };

  trigger.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  trigger.click();
  await delay(600);

  const searchInput = trigger.querySelector("input")
    || document.querySelector("[role='combobox'] input:focus")
    || document.querySelector(".select__input input, .search-typeahead input");

  if (searchInput) {
    searchInput.value = "";
    searchInput.dispatchEvent(new Event("input", { bubbles: true }));
    const filterText = value.substring(0, Math.min(value.length, 5));
    for (const char of filterText) {
      searchInput.value += char;
      searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      await delay(80 + Math.random() * 40);
    }
    await delay(800);
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

  const match = fuzzyMatchOption(value, options.map(o => o.text));
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
    await delay(200);
    matched.el.click();
    return { success: true, value_set: match };
  }

  return { success: false, error: "Match found but click failed" };
}

async function fillAutocomplete(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));
  await delay(200);

  const typeText = value.substring(0, Math.min(value.length, 5));
  for (const char of typeText) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    await delay(behaviorProfile.avg_typing_speed * (1 + (Math.random() - 0.5) * 0.3));
  }

  await delay(1500);

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
        await delay(300);
        return { success: true, value_set: sugText };
      }
    }

    const firstSug = suggestions[0];
    if (firstSug) {
      const firstText = firstSug.textContent.trim();
      firstSug.click();
      await delay(300);
      return { success: true, value_set: firstText, used_first_suggestion: true };
    }
  }

  el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await delay(200);
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: value, no_suggestions: true };
}

async function fillTagInput(selector, values) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);
  el.focus();

  const added = [];
  for (const val of values) {
    el.value = "";
    el.dispatchEvent(new Event("input", { bubbles: true }));

    for (const char of val) {
      el.value += char;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      await delay(60 + Math.random() * 40);
    }
    await delay(300);
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", code: "Enter", bubbles: true }));
    await delay(400);
    added.push(val);
  }

  return { success: true, value_set: added.join(", "), count: added.length };
}

async function fillDate(selector, isoDate) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);

  const inputType = (el.getAttribute("type") || "text").toLowerCase();

  if (inputType === "date") {
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, "value"
    ).set;
    nativeInputValueSetter.call(el, isoDate);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: isoDate };
  }

  const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
  let formatted = isoDate;
  try {
    const [y, m, d] = isoDate.split("-");
    if (placeholder.includes("dd/mm")) {
      formatted = `${d}/${m}/${y}`;
    } else if (placeholder.includes("mm/dd")) {
      formatted = `${m}/${d}/${y}`;
    }
  } catch (_) { /* keep ISO format */ }

  el.focus();
  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  for (const char of formatted) {
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    await delay(80 + Math.random() * 40);
  }
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: formatted };
}

async function scrollTo(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(500);
  return { success: true };
}

async function waitForSelector(selector, timeoutMs) {
  const maxWait = timeoutMs || 10000;
  const pollInterval = 300;
  let elapsed = 0;

  while (elapsed < maxWait) {
    const el = resolveSelector(selector);
    if (el) {
      return {
        success: true,
        found_after_ms: elapsed,
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || "").trim().substring(0, 100),
      };
    }
    await delay(pollInterval);
    elapsed += pollInterval;
  }

  return { success: false, error: `Selector '${selector}' not found after ${maxWait}ms` };
}

async function forceClick(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(200);

  el.dispatchEvent(new MouseEvent("click", {
    bubbles: true, cancelable: true, view: window,
  }));

  return { success: true };
}

async function checkConsentBoxes(rootSelector) {
  const root = rootSelector ? resolveSelector(rootSelector) : document;
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
      await delay(200);
    }
  }

  return { success: true, checked_count: checked.length, labels: checked };
}

async function rescanAfterFill(filledSelector) {
  await delay(800);

  const result = {
    new_fields: [],
    validation_errors: [],
    snapshot: buildSnapshot(),
  };

  const filledEl = resolveSelector(filledSelector);
  if (filledEl) {
    const isInvalid = filledEl.getAttribute("aria-invalid") === "true";
    if (isInvalid) {
      const errId = filledEl.getAttribute("aria-errormessage");
      let errMsg = "";
      if (errId) {
        const errEl = document.getElementById(errId);
        if (errEl) errMsg = errEl.textContent.trim();
      }
      result.validation_errors.push({
        selector: filledSelector,
        error: errMsg || "Field marked as invalid",
      });
    }
  }

  for (const alert of document.querySelectorAll("[role='alert']")) {
    const text = alert.textContent.trim();
    if (text) {
      result.validation_errors.push({
        selector: "[role='alert']",
        error: text,
      });
    }
  }

  return result;
}

// ═══════════════════════════════════════════════════════════════
// Gemini Nano — Tier 3 local AI (Chrome built-in, zero cost)
// ═══════════════════════════════════════════════════════════════
//
// Used by the 5-tier form intelligence system:
//   Tier 1: Pattern match (regex) — free, <1ms
//   Tier 2: Semantic cache (embeddings) — free, ~5ms
//   Tier 3: Gemini Nano (this) — free, ~500ms
//   Tier 4: LLM API (GPT-4.1-mini) — $0.002, ~1s
//   Tier 5: Vision (screenshot) — $0.01, ~3s

/**
 * Use Chrome's Prompt API (Gemini Nano) to analyze a form field locally.
 * Returns the answer string, or null if Nano is unavailable.
 */
async function analyzeFieldLocally(question, inputType, options) {
  if (!self.ai || !self.ai.languageModel) return null;

  try {
    const capabilities = await self.ai.languageModel.capabilities();
    if (capabilities.available === "no") return null;

    const session = await self.ai.languageModel.create({
      systemPrompt:
        "You fill job application forms for an ML Engineer with 2 years experience in the UK. " +
        "Return only the answer value, nothing else. No explanation, no quotes.",
    });

    let prompt = `Field: "${question}" (${inputType})`;
    if (options && options.length > 0) prompt += `\nOptions: ${options.join(", ")}`;
    prompt += "\nAnswer:";

    const answer = await session.prompt(prompt);
    session.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Gemini Nano unavailable:", e.message);
    return null;
  }
}

/**
 * Use Chrome's Writer API for longer-form answers (textarea fields).
 * Falls back from Prompt API for questions needing paragraph answers.
 */
async function writeShortAnswer(question) {
  if (!self.ai || !self.ai.writer) return null;

  try {
    const capabilities = await self.ai.writer.capabilities();
    if (capabilities.available === "no") return null;

    const writer = await self.ai.writer.create({
      tone: "formal",
      length: "short",
      sharedContext: "Job application for ML Engineer position in the UK.",
    });
    const answer = await writer.write(question);
    writer.destroy();
    return answer ? answer.trim() : null;
  } catch (e) {
    console.log("[JobPulse] Writer API unavailable:", e.message);
    return null;
  }
}

// ═══════════════════════════════════════════════════════════════
// JD Text Extraction — used by scanning flow
// ═══════════════════════════════════════════════════════════════

/**
 * Extract job listing cards from search results pages.
 * Supports: Indeed, Greenhouse job boards, generic job listing pages.
 * Returns an array of {title, company, url, location, description} objects.
 */
function extractJobCards() {
  const hostname = window.location.hostname.toLowerCase();
  const cards = [];

  // ── Indeed ──────────────────────────────────────────────────────────────
  if (hostname.includes("indeed")) {
    const cardEls = document.querySelectorAll(
      ".job_seen_beacon, .resultContent, [data-jk], .jobsearch-ResultsList > li"
    );
    for (const card of cardEls) {
      const titleEl = card.querySelector("h2.jobTitle a, h2 a, .jobTitle a, a[data-jk]");
      const companyEl = card.querySelector(
        "[data-testid='company-name'], .companyName, .company, [data-company-name]"
      );
      const locationEl = card.querySelector(
        "[data-testid='text-location'], .companyLocation, .location"
      );
      const salaryEl = card.querySelector(
        ".salary-snippet-container, .estimated-salary, [data-testid='attribute_snippet_testid']"
      );
      const snippetEl = card.querySelector(".job-snippet, .underShelfFooter");

      const title = titleEl?.innerText?.trim() || "";
      const company = companyEl?.innerText?.trim() || "";
      let href = titleEl?.getAttribute("href") || "";
      if (href && !href.startsWith("http")) href = "https://uk.indeed.com" + href;

      if (!title || !href) continue;

      cards.push({
        title,
        company,
        url: href,
        location: locationEl?.innerText?.trim() || "",
        salary: salaryEl?.innerText?.trim() || "",
        description: snippetEl?.innerText?.trim() || "",
        platform: "indeed",
      });
    }
    return cards;
  }

  // ── Greenhouse job board ────────────────────────────────────────────────
  if (hostname.includes("greenhouse") || document.querySelector("#main .opening")) {
    const openings = document.querySelectorAll(".opening, [data-mapped='true'], .job-post");
    for (const el of openings) {
      const linkEl = el.querySelector("a");
      const locationEl = el.querySelector(".location, .job-post-location");

      const title = linkEl?.innerText?.trim() || "";
      let href = linkEl?.getAttribute("href") || "";
      if (href && !href.startsWith("http")) {
        href = window.location.origin + href;
      }

      if (!title || !href) continue;

      cards.push({
        title,
        company: document.querySelector(".company-name, h1")?.innerText?.trim() || "",
        url: href,
        location: locationEl?.innerText?.trim() || "",
        salary: "",
        description: "",
        platform: "greenhouse",
      });
    }
    return cards;
  }

  // ── Generic fallback — look for common job card patterns ────────────────
  const genericCards = document.querySelectorAll(
    "[class*='job-card'], [class*='job-listing'], [class*='vacancy'], [class*='search-result']"
  );
  for (const card of genericCards) {
    const linkEl = card.querySelector("a[href]");
    const title = linkEl?.innerText?.trim() || card.querySelector("h2, h3")?.innerText?.trim() || "";
    let href = linkEl?.getAttribute("href") || "";
    if (href && !href.startsWith("http")) {
      href = window.location.origin + href;
    }
    if (!title || !href) continue;

    cards.push({
      title,
      company: card.querySelector("[class*='company']")?.innerText?.trim() || "",
      url: href,
      location: card.querySelector("[class*='location']")?.innerText?.trim() || "",
      salary: card.querySelector("[class*='salary']")?.innerText?.trim() || "",
      description: "",
      platform: "generic",
    });
  }
  return cards;
}


/**
 * Extract full job description text from the current page.
 * Tries platform-specific selectors first, falls back to body text.
 */
function extractJDText() {
  const selectors = [
    // LinkedIn
    ".description__text", ".show-more-less-html__markup", "#job-details",
    // Indeed
    "#jobDescriptionText", ".jobsearch-jobDescriptionText",
    // Greenhouse
    "#content .body", ".job__description",
    // Lever
    ".posting-page .content", '[data-qa="job-description"]',
    // Workday
    '[data-automation-id="jobPostingDescription"]',
    // Generic
    "article", '[class*="description"]', '[class*="job-detail"]',
  ];

  for (const sel of selectors) {
    const el = document.querySelector(sel);
    if (el) {
      const text = el.innerText?.trim();
      if (text && text.length > 100) {
        return text.replace(/\s+/g, " ").substring(0, 10000);
      }
    }
  }

  // Fallback: body text (limited)
  return (document.body?.innerText || "").replace(/\s+/g, " ").substring(0, 10000);
}

// ═══════════════════════════════════════════════════════════════
// Message handler — commands from background.js
// ═══════════════════════════════════════════════════════════════

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const { action, payload } = msg;
  if (!action) return false;

  (async () => {
    let result;
    switch (action) {
      case "get_snapshot":
        result = buildSnapshot();
        break;
      case "fill":
        result = await fillField(payload.selector, payload.value);
        break;
      case "upload":
        result = await uploadFile(payload.selector, payload.file_base64, payload.file_name, payload.mime_type);
        break;
      case "click":
        result = await clickElement(payload.selector);
        break;
      case "select":
        result = await selectOption(payload.selector, payload.value);
        break;
      case "check":
        result = await checkBox(payload.selector, payload.value);
        break;
      case "wait_for_apply": {
        // Poll DOM for up to 10s waiting for an apply-like button to render.
        // LinkedIn SPA renders the Easy Apply button lazily after page load.
        const applyRe = /easy\s*apply|apply\s*(now|for\s*this)?|start\s*application|submit\s*application/i;
        const maxWait = payload.timeout_ms || 10000;
        const pollInterval = 500;
        let elapsed = 0;
        let snap = null;
        while (elapsed < maxWait) {
          snap = buildSnapshot();
          const hasApply = snap.buttons.some(b => applyRe.test(b.text));
          if (hasApply) break;
          await delay(pollInterval);
          elapsed += pollInterval;
        }
        // Final snapshot regardless
        if (!snap) snap = buildSnapshot();
        // Also dump all elements containing "apply" for diagnostics
        const applyDiag = [];
        document.querySelectorAll("*").forEach(el => {
          const t = (el.textContent || "").trim();
          if (t.length < 100 && /apply/i.test(t)) {
            applyDiag.push({
              tag: el.tagName.toLowerCase(),
              classes: (el.className && typeof el.className === "string") ? el.className.substring(0, 120) : "",
              text: t.substring(0, 80),
              role: el.getAttribute("role") || "",
              ariaLabel: el.getAttribute("aria-label") || "",
            });
          }
        });
        result = { ...snap, apply_diagnostics: applyDiag.slice(0, 30), waited_ms: elapsed };
        break;
      }
      case "scan_jd": {
        const jdText = extractJDText();
        result = { success: true, jd_text: jdText };
        break;
      }
      case "scan_cards": {
        // Extract job listing cards from search results pages (Indeed, Greenhouse board, etc.)
        const cards = extractJobCards();
        result = { success: true, jobs: cards };
        break;
      }
      case "analyze_field": {
        // Tier 3: try Prompt API first, fall back to Writer API for textarea
        let answer = await analyzeFieldLocally(payload.question, payload.input_type, payload.options || []);
        if (!answer && payload.input_type === "textarea") {
          answer = await writeShortAnswer(payload.question);
        }
        result = { success: !!answer, answer: answer || "" };
        break;
      }
      case "fill_radio_group":
        result = await fillRadioGroup(payload.selector, payload.value);
        break;
      case "fill_custom_select":
        result = await fillCustomSelect(payload.selector, payload.value);
        break;
      case "fill_autocomplete":
        result = await fillAutocomplete(payload.selector, payload.value);
        break;
      case "fill_tag_input":
        result = await fillTagInput(payload.selector, payload.values || []);
        break;
      case "fill_date":
        result = await fillDate(payload.selector, payload.value);
        break;
      case "scroll_to":
        result = await scrollTo(payload.selector);
        break;
      case "wait_for_selector":
        result = await waitForSelector(payload.selector, payload.timeout_ms);
        break;
      case "force_click":
        result = await forceClick(payload.selector);
        break;
      case "check_consent_boxes":
        result = await checkConsentBoxes(payload.root_selector || null);
        break;
      case "rescan_after_fill":
        result = await rescanAfterFill(payload.selector);
        break;
      case "scan_form_groups":
        result = { success: true, groups: scanFormGroups(payload.root_selector || null) };
        break;
      case "get_field_context": {
        const ctxEl = resolveSelector(payload.selector);
        if (!ctxEl) {
          result = { success: false, error: "Element not found" };
        } else {
          result = {
            success: true,
            context: extractFieldInfo(ctxEl, null),
          };
        }
        break;
      }
      default:
        result = { success: false, error: "Unknown action: " + action };
    }
    sendResponse(result);
  })();

  return true; // Keep message channel open for async sendResponse
});

// ═══════════════════════════════════════════════════════════════
// MutationObserver — live DOM change detection
// ═══════════════════════════════════════════════════════════════
//
// Debounced (500ms): DOM changes trigger a fresh snapshot sent to
// background.js → Python. This keeps the bridge's cached snapshot
// up-to-date without polling.

function safeSendMessage(msg) {
  if (!chrome.runtime?.id) return; // Extension context invalidated (reload)
  try {
    chrome.runtime.sendMessage(msg).catch(() => {});
  } catch (_) { /* service worker inactive — message will be lost */ }
}

let scanTimeout;
const observer = new MutationObserver(() => {
  clearTimeout(scanTimeout);
  scanTimeout = setTimeout(() => {
    safeSendMessage({ type: "mutation", payload: { snapshot: buildSnapshot() } });
  }, 500);
});

if (document.body) {
  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["class", "style", "hidden", "disabled", "aria-hidden"],
  });
}

// ═══════════════════════════════════════════════════════════════
// Initial snapshot — sent 1s after page load
// ═══════════════════════════════════════════════════════════════

window.addEventListener("load", () => {
  setTimeout(() => {
    safeSendMessage({ type: "navigation", payload: { snapshot: buildSnapshot() } });
  }, 1000);
});
