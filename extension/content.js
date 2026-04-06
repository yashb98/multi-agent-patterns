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
 * Select a dropdown option by matching text or value (case-insensitive).
 */
async function selectOption(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  for (const opt of el.querySelectorAll("option")) {
    if (
      opt.textContent.trim().toLowerCase().includes(value.toLowerCase()) ||
      opt.value.toLowerCase() === value.toLowerCase()
    ) {
      el.value = opt.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return { success: true, value_set: opt.textContent.trim() };
    }
  }
  return { success: false, error: "Option not found: " + value };
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
      case "analyze_field": {
        // Tier 3: try Prompt API first, fall back to Writer API for textarea
        let answer = await analyzeFieldLocally(payload.question, payload.input_type, payload.options || []);
        if (!answer && payload.input_type === "textarea") {
          answer = await writeShortAnswer(payload.question);
        }
        result = { success: !!answer, answer: answer || "" };
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
