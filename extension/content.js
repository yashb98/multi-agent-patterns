// extension/content.js
// Deep page scanner, form filler, behavior profiler, mutation observer.

// -- Behavior Profile (calibration from user's real patterns) --

const behaviorProfile = {
  avg_typing_speed: 80,      // ms per char (default, calibrated over time)
  typing_variance: 0.3,       // 0-1
  scroll_speed: 400,           // px/s
  reading_pause: 1.0,          // seconds
  field_to_field_gap: 500,     // ms between fields
  click_offset: { x: 0, y: 0 },
  calibrated: false,
  keystrokes: 0,
  clicks: 0,
};

// Load saved profile
chrome.storage.local.get("behaviorProfile", (data) => {
  if (data.behaviorProfile) Object.assign(behaviorProfile, data.behaviorProfile);
});

// Calibration listeners (passive observation)
document.addEventListener("keydown", (e) => {
  if (!behaviorProfile._lastKey) behaviorProfile._lastKey = performance.now();
  else {
    const gap = performance.now() - behaviorProfile._lastKey;
    if (gap > 20 && gap < 500) {
      behaviorProfile.avg_typing_speed =
        behaviorProfile.avg_typing_speed * 0.95 + gap * 0.05;
    }
    behaviorProfile._lastKey = performance.now();
  }
  behaviorProfile.keystrokes++;
  if (behaviorProfile.keystrokes > 500 && !behaviorProfile.calibrated) {
    behaviorProfile.calibrated = true;
    chrome.storage.local.set({ behaviorProfile });
  }
}, { passive: true });

document.addEventListener("click", (e) => {
  behaviorProfile.clicks++;
}, { passive: true });

// -- Utility --

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function resolveSelector(selector) {
  // Handle shadow DOM paths: "host>>inner"
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

// -- Deep Page Scanner --

function extractFieldInfo(el, iframeIndex) {
  const tag = el.tagName.toLowerCase();
  let inputType = "text";

  if (tag === "select") inputType = "select";
  else if (tag === "textarea") inputType = "textarea";
  else if (el.getAttribute("contenteditable") === "true") inputType = "rich_text";
  else if (el.getAttribute("role") === "listbox") inputType = "custom_select";
  else if (el.getAttribute("role") === "combobox") inputType = "search_autocomplete";
  else if (el.getAttribute("role") === "radiogroup") inputType = "radio";
  else if (el.getAttribute("role") === "switch") inputType = "toggle";
  else inputType = (el.getAttribute("type") || "text").toLowerCase();

  // Find label
  let label = "";
  const labelEl = el.closest("label") || (el.id && document.querySelector(`label[for="${el.id}"]`));
  if (labelEl) label = labelEl.textContent.trim();
  if (!label) label = el.getAttribute("aria-label") || el.getAttribute("placeholder") || "";

  // Options for select/radio
  const options = [];
  if (tag === "select") {
    el.querySelectorAll("option").forEach((opt) => {
      const text = opt.textContent.trim();
      if (text && !text.toLowerCase().startsWith("select")) options.push(text);
    });
  }

  // Build unique selector
  let selector = "";
  if (el.id) selector = `#${el.id}`;
  else if (el.name) selector = `${tag}[name="${el.name}"]`;
  else {
    // Fallback: nth-of-type
    const parent = el.parentElement;
    if (parent) {
      const siblings = Array.from(parent.querySelectorAll(tag));
      const idx = siblings.indexOf(el);
      selector = `${tag}:nth-of-type(${idx + 1})`;
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
  };
}

function deepScan(root, depth, iframeIndex) {
  root = root || document;
  depth = depth || 0;
  iframeIndex = iframeIndex === undefined ? null : iframeIndex;
  const fields = [];
  const MAX_DEPTH = 5;
  if (depth > MAX_DEPTH) return fields;

  // 1. Regular form fields
  const inputs = root.querySelectorAll(
    "input:not([type='hidden']), select, textarea, [contenteditable='true'], " +
    "[role='listbox'], [role='combobox'], [role='radiogroup'], [role='switch'], [role='textbox']"
  );
  for (const el of inputs) {
    fields.push(extractFieldInfo(el, iframeIndex));
  }

  // 2. Shadow roots
  root.querySelectorAll("*").forEach((el) => {
    if (el.shadowRoot) {
      fields.push(...deepScan(el.shadowRoot, depth + 1, iframeIndex));
    }
  });

  // 3. Same-origin iframes
  const iframes = root.querySelectorAll("iframe");
  iframes.forEach((iframe, idx) => {
    try {
      if (iframe.contentDocument) {
        fields.push(...deepScan(iframe.contentDocument, depth + 1, idx));
      }
    } catch (e) {
      // Cross-origin — handled by background.js
    }
  });

  return fields;
}

function detectVerificationWall() {
  const SELECTORS = [
    { sel: "#challenge-running, .cf-turnstile, #cf-challenge-running", type: "cloudflare", conf: 0.95 },
    { sel: ".g-recaptcha, #recaptcha-anchor, [data-sitekey]", type: "recaptcha", conf: 0.90 },
    { sel: ".h-captcha", type: "hcaptcha", conf: 0.90 },
  ];
  for (const { sel, type, conf } of SELECTORS) {
    if (document.querySelector(sel)) return { wall_type: type, confidence: conf, details: sel };
  }

  for (const frame of document.querySelectorAll("iframe")) {
    const src = frame.src || "";
    if (src.includes("challenges.cloudflare.com")) return { wall_type: "cloudflare", confidence: 0.95, details: src };
    if (src.includes("google.com/recaptcha")) return { wall_type: "recaptcha", confidence: 0.90, details: src };
    if (src.includes("hcaptcha.com")) return { wall_type: "hcaptcha", confidence: 0.90, details: src };
  }

  const body = document.body?.innerText?.toLowerCase() || "";
  if (/verify you are human|are you a robot|confirm you're not a robot/.test(body))
    return { wall_type: "text_challenge", confidence: 0.85, details: "text match" };
  if (/access denied|403 forbidden|you have been blocked/.test(body))
    return { wall_type: "http_block", confidence: 0.80, details: "text match" };

  return null;
}

function buildSnapshot() {
  const fields = deepScan();
  const buttons = [];
  document.querySelectorAll("button, input[type='submit'], a[role='button']").forEach((el) => {
    const text = el.textContent?.trim() || el.value || "";
    if (text) {
      buttons.push({
        selector: el.id ? `#${el.id}` : `button:nth-of-type(${buttons.length + 1})`,
        text: text.substring(0, 100),
        type: el.type || (el.tagName === "A" ? "link" : "button"),
        enabled: !el.disabled,
      });
    }
  });

  return {
    url: window.location.href,
    title: document.title,
    fields,
    buttons,
    verification_wall: detectVerificationWall(),
    page_text_preview: (document.body?.innerText || "").substring(0, 500),
    has_file_inputs: document.querySelector("input[type='file']") !== null,
    iframe_count: document.querySelectorAll("iframe").length,
    timestamp: Date.now(),
  };
}

// -- Form Actions --

async function fillField(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.field_to_field_gap);

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear
  el.value = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type char by char
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    el.value += char;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
  }

  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  return { success: true, value_set: el.value };
}

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

async function clickElement(selector) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(behaviorProfile.reading_pause * 500 * (0.5 + Math.random()));

  el.click();
  return { success: true };
}

async function selectOption(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Find matching option
  const options = el.querySelectorAll("option");
  for (const opt of options) {
    if (opt.textContent.trim().toLowerCase().includes(value.toLowerCase()) ||
        opt.value.toLowerCase() === value.toLowerCase()) {
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: opt.textContent.trim() };
    }
  }
  return { success: false, error: "Option not found: " + value };
}

async function checkBox(selector, shouldCheck) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  const isChecked = el.checked;
  const want = shouldCheck === "true" || shouldCheck === true;
  if (isChecked !== want) {
    el.click();
  }
  return { success: true, value_set: String(el.checked) };
}

// -- Message handler (from background.js) --

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const { action, payload } = msg;

  if (!action) return false;

  (async () => {
    let result;
    switch (action) {
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
      default:
        result = { success: false, error: "Unknown action: " + action };
    }
    sendResponse(result);
  })();

  return true;  // Keep channel open for async response
});

// -- MutationObserver --

let scanTimeout;
const observer = new MutationObserver(() => {
  clearTimeout(scanTimeout);
  scanTimeout = setTimeout(() => {
    const snapshot = buildSnapshot();
    chrome.runtime.sendMessage({ type: "mutation", payload: { snapshot } }).catch(() => {});
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

// -- Initial snapshot on load --

window.addEventListener("load", () => {
  setTimeout(() => {
    const snapshot = buildSnapshot();
    chrome.runtime.sendMessage({ type: "navigation", payload: { snapshot } }).catch(() => {});
  }, 1000);
});
