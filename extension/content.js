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
 * Calculate field-to-field gap based on label complexity.
 */
function getFieldGap(labelText) {
  const len = (labelText || "").length;
  if (len < 10) return 300 + Math.random() * 200;
  if (len < 40) return 500 + Math.random() * 300;
  if (len < 100) return 800 + Math.random() * 500;
  return 1200 + Math.random() * 500;
}

/**
 * Scroll element into view and wait proportional to distance scrolled.
 */
async function smartScroll(el) {
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);
  return scrollWait;
}

/**
 * Retry wrapper for fill operations.
 * Retries on element-not-found or fill failure. Max 2 retries with 500ms gap.
 * Does NOT retry on success (even partial).
 */
async function withRetry(fn, maxRetries = 2, retryDelayMs = 500) {
  let lastResult;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    lastResult = await fn();
    if (lastResult.success) return lastResult;

    // Only retry on transient errors
    const isRetryable = lastResult.error &&
      (lastResult.error.includes("not found") ||
       lastResult.error.includes("No options") ||
       lastResult.error.includes("not visible"));
    if (!isRetryable) return lastResult;

    if (attempt < maxRetries) {
      await delay(retryDelayMs);
    }
  }
  lastResult.retries_exhausted = true;
  return lastResult;
}

/**
 * Exhaustive DOM context extraction for a form field.
 *
 * Captures EVERYTHING around the field — every text node, sibling, ancestor,
 * ARIA attribute, data attribute, title, tooltip, etc. The LLM uses this
 * context to understand what each field is asking, regardless of the site's
 * HTML structure. No hardcoded strategies — just a thorough DOM walk.
 *
 * Returns { label, context } where label is our best guess and context is
 * the full surrounding text for the LLM.
 */
function extractFieldContext(el) {
  const texts = [];       // All candidate label texts, ranked by proximity
  const contextParts = []; // Full surrounding context for the LLM

  // Helper: clean text
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  // Helper: is this an input element?
  const isInput = (e) => e && ["INPUT","SELECT","TEXTAREA","BUTTON"].includes(e.tagName);

  // ─── Explicit associations (highest confidence) ───

  // <label for="id">
  if (el.id) {
    const labelFor = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (labelFor) texts.push({ text: clean(labelFor.textContent), rank: 1, src: "label[for]" });
  }

  // Wrapping <label>
  const wrappingLabel = el.closest("label");
  if (wrappingLabel) {
    // Get label text excluding the input's own text
    const clone = wrappingLabel.cloneNode(true);
    clone.querySelectorAll("input,select,textarea").forEach(c => c.remove());
    const t = clean(clone.textContent);
    if (t) texts.push({ text: t, rank: 1, src: "wrapping-label" });
  }

  // aria-labelledby
  const labelledBy = el.getAttribute("aria-labelledby");
  if (labelledBy) {
    const parts = labelledBy.split(/\s+/).map(id => {
      const ref = document.getElementById(id);
      return ref ? clean(ref.textContent) : "";
    }).filter(Boolean);
    if (parts.length) texts.push({ text: parts.join(" "), rank: 1, src: "aria-labelledby" });
  }

  // aria-label
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) texts.push({ text: clean(ariaLabel), rank: 2, src: "aria-label" });

  // aria-description / aria-describedby
  const describedBy = el.getAttribute("aria-describedby");
  if (describedBy) {
    const parts = describedBy.split(/\s+/).map(id => {
      const ref = document.getElementById(id);
      return ref ? clean(ref.textContent) : "";
    }).filter(Boolean);
    if (parts.length) contextParts.push("described-by: " + parts.join(" "));
  }

  // title attribute
  if (el.title) texts.push({ text: clean(el.title), rank: 3, src: "title" });

  // placeholder
  if (el.placeholder) texts.push({ text: clean(el.placeholder), rank: 4, src: "placeholder" });

  // data-* attributes that might contain labels
  for (const attr of el.attributes) {
    if (attr.name.startsWith("data-") && /label|name|field|title|desc|hint|question/i.test(attr.name)) {
      const v = clean(attr.value);
      if (v && v.length < 200) texts.push({ text: v, rank: 3, src: `attr:${attr.name}` });
    }
  }

  // ─── DOM proximity (walk outward from the element) ───

  // Previous siblings (immediate + up to 3 levels)
  const collectPrevSiblings = (node, maxDepth) => {
    for (let depth = 0; node && depth < maxDepth; depth++) {
      let prev = node.previousElementSibling;
      while (prev) {
        if (!isInput(prev)) {
          const t = clean(prev.textContent);
          if (t.length > 0 && t.length < 200) {
            texts.push({ text: t, rank: 5 + depth, src: `prev-sib-d${depth}` });
            break; // Take the closest one at this depth
          }
        }
        prev = prev.previousElementSibling;
      }
      node = node.parentElement;
    }
  };
  collectPrevSiblings(el, 4);

  // Walk up ancestors, collecting container text
  let ancestor = el.parentElement;
  for (let depth = 0; ancestor && depth < 6; depth++, ancestor = ancestor.parentElement) {
    // Direct text nodes of this ancestor (not from child elements)
    const directText = Array.from(ancestor.childNodes)
      .filter(n => n.nodeType === 3)
      .map(n => clean(n.textContent))
      .filter(t => t.length > 0 && t.length < 150)
      .join(" ");
    if (directText) texts.push({ text: directText, rank: 7 + depth, src: `parent-text-d${depth}` });

    // Short text children of this ancestor that precede our element
    for (const child of ancestor.children) {
      if (child === el || child.contains(el)) break; // Stop at our element
      if (isInput(child)) continue;
      const t = clean(child.textContent);
      if (t.length > 0 && t.length < 150 && !child.querySelector("input,select,textarea")) {
        texts.push({ text: t, rank: 6 + depth, src: `ancestor-child-d${depth}` });
      }
    }

    // Check for legend, header, or label-like elements in this container
    const labelLike = ancestor.querySelector(
      ":scope > label, :scope > legend, :scope > h1, :scope > h2, :scope > h3, " +
      ":scope > h4, :scope > h5, :scope > h6, :scope > [class*='label'], " +
      ":scope > [class*='title'], :scope > [class*='header'], :scope > [class*='question']"
    );
    if (labelLike && !labelLike.contains(el) && !isInput(labelLike)) {
      const t = clean(labelLike.textContent);
      if (t.length > 0 && t.length < 200) {
        texts.push({ text: t, rank: 4 + depth, src: `label-like-d${depth}` });
      }
    }

    // Stop walking up if we hit a form, dialog, or major container
    const tag = ancestor.tagName.toLowerCase();
    if (["form", "dialog", "main", "body", "html"].includes(tag)) break;
  }

  // ─── Sibling fields context (what's around this field) ───
  // Next sibling text (sometimes labels come after)
  let nextSib = el.nextElementSibling;
  if (!nextSib && el.parentElement) nextSib = el.parentElement.nextElementSibling;
  if (nextSib && !isInput(nextSib)) {
    const t = clean(nextSib.textContent);
    if (t.length > 0 && t.length < 150) {
      texts.push({ text: t, rank: 10, src: "next-sib" });
    }
  }

  // ─── Build context string for the LLM ───
  // Deduplicate and sort by rank (lower = more likely to be the label)
  const seen = new Set();
  const unique = texts.filter(t => {
    if (seen.has(t.text)) return false;
    seen.add(t.text);
    return true;
  }).sort((a, b) => a.rank - b.rank);

  // Best label = highest ranked text
  const bestLabel = unique.length > 0 ? unique[0].text : "";

  // Full context = all unique texts joined (for the LLM to see everything)
  const fullContext = unique
    .map(t => t.text)
    .slice(0, 8) // Top 8 most relevant texts
    .join(" | ");

  return {
    label: bestLabel.substring(0, 200),
    context: fullContext.substring(0, 500),
    sources: unique.slice(0, 5).map(t => `${t.src}: "${t.text.substring(0, 60)}"`),
  };
}

// ═══════════════════════════════════════════════════════════════
// Visual Cursor — animated overlay so the user can see automation
// ═══════════════════════════════════════════════════════════════

let _cursor = null;

function ensureCursor() {
  if (_cursor && document.body.contains(_cursor)) return _cursor;
  _cursor = document.createElement("div");
  _cursor.id = "jobpulse-cursor";
  _cursor.style.cssText = `
    position: fixed; z-index: 2147483647; pointer-events: none;
    width: 20px; height: 20px; border-radius: 50%;
    background: rgba(59, 130, 246, 0.7);
    border: 2px solid rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 12px rgba(59, 130, 246, 0.5), 0 0 4px rgba(0,0,0,0.3);
    transition: left 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                top 0.4s cubic-bezier(0.25, 0.1, 0.25, 1),
                transform 0.15s ease;
    left: -100px; top: -100px;
    transform: translate(-50%, -50%);
  `;
  document.body.appendChild(_cursor);
  return _cursor;
}

/** Smoothly move the visual cursor along a Bezier curve to an element's center. */
async function moveCursorTo(el) {
  const cursor = ensureCursor();
  const rect = el.getBoundingClientRect();
  const targetX = rect.left + rect.width / 2;
  const targetY = rect.top + rect.height / 2;

  const currentX = parseFloat(cursor.style.left) || -100;
  const currentY = parseFloat(cursor.style.top) || -100;

  const dist = Math.sqrt((targetX - currentX) ** 2 + (targetY - currentY) ** 2);
  if (dist < 30) {
    cursor.style.left = targetX + "px";
    cursor.style.top = targetY + "px";
    cursor.style.display = "block";
    await delay(50);
    return;
  }

  cursor.style.transition = "transform 0.15s ease";

  const points = bezierCurve(currentX, currentY, targetX, targetY);
  cursor.style.display = "block";

  for (let i = 0; i < points.length; i++) {
    cursor.style.left = points[i].x + "px";
    cursor.style.top = points[i].y + "px";
    const t = i / points.length;
    const easeMs = 8 + 20 * (1 - Math.abs(2 * t - 1));
    await delay(easeMs + Math.random() * 5);
  }
}

/** Flash a click animation on the cursor. */
async function cursorClickFlash() {
  const cursor = ensureCursor();
  cursor.style.transform = "translate(-50%, -50%) scale(0.6)";
  await delay(100);
  cursor.style.transform = "translate(-50%, -50%) scale(1.0)";
  await delay(100);
}

/** Highlight an element briefly (glow effect). */
function highlightElement(el) {
  const prev = el.style.outline;
  const prevTransition = el.style.transition;
  el.style.transition = "outline 0.2s ease";
  el.style.outline = "2px solid rgba(59, 130, 246, 0.8)";
  setTimeout(() => {
    el.style.outline = prev;
    el.style.transition = prevTransition;
  }, 1500);
}

/** Hide the cursor after automation is done. */
function hideCursor() {
  if (_cursor) _cursor.style.display = "none";
}

/**
 * Generate points along a cubic Bezier curve with randomized control
 * points. Creates natural-looking mouse trajectories that overshoot
 * slightly and curve, unlike Playwright's straight lines.
 */
function bezierCurve(x0, y0, x1, y1, steps = 18) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const distance = Math.sqrt(dx * dx + dy * dy);

  const perpX = -dy / (distance || 1);
  const perpY = dx / (distance || 1);
  const curvature = (Math.random() - 0.5) * distance * 0.3;
  const overshoot = 1.0 + (Math.random() * 0.08 - 0.02);

  const cp1x = x0 + dx * 0.3 + perpX * curvature;
  const cp1y = y0 + dy * 0.3 + perpY * curvature;
  const cp2x = x0 + dx * 0.7 * overshoot + perpX * curvature * 0.3;
  const cp2y = y0 + dy * 0.7 * overshoot + perpY * curvature * 0.3;

  const points = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    const x = u*u*u*x0 + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*x1;
    const y = u*u*u*y0 + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*y1;
    points.push({ x, y });
  }
  return points;
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
 * Check if a form field is visible to the user.
 * Hidden fields may be honeypot traps — bots fill them, humans never see them.
 * ATS platforms silently discard submissions that fill honeypot fields.
 */
function isFieldVisible(el) {
  const style = window.getComputedStyle(el);
  if (style.display === "none") return false;
  if (style.visibility === "hidden") return false;
  if (parseFloat(style.opacity) === 0) return false;
  // Off-screen positioning (common honeypot technique)
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return false;
  if (rect.top < -9000 || rect.left < -9000) return false;
  // aria-hidden + negative tabindex = intentionally hidden from users
  if (el.getAttribute("aria-hidden") === "true" && el.tabIndex === -1) return false;
  return true;
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

  // ── Dynamic DOM context extraction ──
  // Instead of hardcoded strategies, we scrape EVERYTHING around the field.
  // The LLM decides what's relevant. The content script is a thorough scanner.
  const domContext = extractFieldContext(el);
  let label = domContext.label;

  // Clean up label
  label = label.replace(/\s*\*\s*$/, "").replace(/\s+/g, " ").trim();
  label = label.replace(/\s*Please enter a valid.*$/i, "").trim();

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
    // No id or name — build a unique selector by walking up the DOM
    // to find a parent with a distinguishing id, data attribute, or class
    let built = false;
    let ancestor = el.parentElement;
    for (let depth = 0; ancestor && depth < 8; depth++, ancestor = ancestor.parentElement) {
      let anchorSel = "";
      if (ancestor.id) {
        anchorSel = /[:#.\[\]]/.test(ancestor.id) ? `[id="${ancestor.id}"]` : `#${ancestor.id}`;
      } else if (ancestor.getAttribute("data-zcqa")) {
        anchorSel = `[data-zcqa="${ancestor.getAttribute("data-zcqa")}"]`;
      } else if (ancestor.getAttribute("data-field")) {
        anchorSel = `[data-field="${ancestor.getAttribute("data-field")}"]`;
      } else if (ancestor.getAttribute("data-name")) {
        anchorSel = `[data-name="${ancestor.getAttribute("data-name")}"]`;
      } else if (ancestor.className && typeof ancestor.className === "string" && ancestor.className.length > 2 && ancestor.className.length < 80) {
        // Use class-based selector only if it matches exactly one element on the page
        const cls = ancestor.className.split(/\s+/).filter(c => c.length > 2).join(".");
        if (cls && document.querySelectorAll("." + cls.split(".")[0]).length <= 3) {
          anchorSel = `${ancestor.tagName.toLowerCase()}.${cls}`;
        }
      }
      if (anchorSel) {
        // Find the element relative to this anchor
        const role = el.getAttribute("role");
        const ariaLabel = el.getAttribute("aria-label");
        if (role) {
          const matches = ancestor.querySelectorAll(`[role="${role}"]`);
          if (matches.length === 1) {
            selector = `${anchorSel} [role="${role}"]`;
          } else {
            const idx = Array.from(matches).indexOf(el);
            selector = `${anchorSel} [role="${role}"]:nth-of-type(${idx + 1})`;
          }
        } else if (ariaLabel) {
          selector = `${anchorSel} [aria-label="${ariaLabel}"]`;
        } else {
          const matches = ancestor.querySelectorAll(tag);
          const idx = Array.from(matches).indexOf(el);
          selector = `${anchorSel} ${tag}:nth-of-type(${idx + 1})`;
        }
        built = true;
        break;
      }
    }
    if (!built) {
      const parent = el.parentElement;
      if (parent) {
        const siblings = Array.from(parent.querySelectorAll(tag));
        selector = `${tag}:nth-of-type(${siblings.indexOf(el) + 1})`;
      }
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
    // Dynamic DOM context — exhaustive surrounding text for the LLM
    dom_context: domContext.context,
    label_sources: domContext.sources,
    group_label: domContext.context.split(" | ")[1] || "", // Second-best label candidate
    group_selector: "",
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
      // Collect everything that could be help text
      const parts = [];
      const describedBy = el.getAttribute("aria-describedby");
      if (describedBy) {
        describedBy.split(/\s+/).forEach(id => {
          const desc = document.getElementById(id);
          if (desc) parts.push(desc.textContent.trim());
        });
      }
      const next = el.nextElementSibling;
      if (next && !isFieldVisible(next)) {} // skip hidden
      else if (next && next.textContent.trim().length < 200 &&
               !["INPUT","SELECT","TEXTAREA","BUTTON"].includes(next.tagName)) {
        parts.push(next.textContent.trim());
      }
      return parts.join(" ").substring(0, 300);
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
    // Skip honeypot fields — hidden inputs that bots fill, humans never see
    // Exception: file inputs are often hidden and triggered via custom upload buttons
    if (!isFieldVisible(el) && el.type !== "file") continue;
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
      // Skip honeypot fields within groups
      if (!isFieldVisible(inp) && inp.type !== "file") continue;
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
  document.querySelectorAll("button, input[type='submit'], [role='button'], [class*='apply'], [class*='btn'], a[class*='button'], a[href*='apply'], a[aria-label*='Apply']").forEach((el) => {
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
      // For <a> links: prefer href-based selector (unique, unlike CSS module hashes)
      else if (tag === "a" && el.href) {
        const href = el.getAttribute("href");
        if (href && href.length < 200) {
          selector = `a[href="${href.replace(/"/g, '\\"')}"]`;
        }
      }
      if (!selector && el.className && typeof el.className === "string") {
        // Prefer jobs-apply-button (LinkedIn) or other meaningful class
        const classes = el.className.split(/\s+/).filter(c => c.length > 3);
        const applyClass = classes.find(c => c.includes("apply") || c.includes("submit"));
        const meaningful = applyClass || classes.find(c => !c.startsWith("artdeco"));
        if (meaningful) selector = `${tag}.${meaningful}`;
        else if (classes[0]) selector = `${tag}.${classes[0]}`;
      }
      if (!selector) selector = `${tag}:nth-of-type(${buttons.length + 1})`;
      const btnData = {
        selector,
        text: text.substring(0, 100),
        type: el.type || (tag === "a" ? "link" : "button"),
        enabled: !el.disabled && !el.getAttribute("aria-disabled"),
      };
      // For links: include href so Python can navigate directly instead of clicking
      if (tag === "a" && el.href) {
        btnData.href = el.href.substring(0, 500);
        btnData.target = el.target || "";
      }
      // Include aria-label for icon-only buttons
      const ariaLabel = el.getAttribute("aria-label");
      if (ariaLabel) btnData.ariaLabel = ariaLabel.substring(0, 100);
      buttons.push(btnData);
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
  await delay(scrollWait);

  // Visual cursor + highlight
  await moveCursorTo(el);
  highlightElement(el);
  await cursorClickFlash();

  // Smart read-time: longer labels = more reading time
  const labelLength = (el.getAttribute("aria-label") || el.placeholder || "").length;
  const readDelay = Math.min(1500, 200 + labelLength * 15);
  await delay(readDelay);

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing value using native setter (React-safe)
  setNativeValue(el, "");
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type each character with realistic timing variance
  for (const char of value) {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
    setNativeValue(el, el.value + char);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
  }

  // Finalize: blur triggers validation on most forms
  el.dispatchEvent(new Event("change", { bubbles: true }));
  el.dispatchEvent(new Event("blur", { bubbles: true }));

  // ── Post-fill verification ──
  await delay(100);
  const actualValue = el.value || "";
  const verified = actualValue === value;

  if (!verified && actualValue !== value) {
    setNativeValue(el, value);
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    await delay(100);

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
  if (!el) return { success: false, error: "Contenteditable element is null" };

  // Scroll-aware timing
  const rectBefore = el.getBoundingClientRect();
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  const rectAfter = el.getBoundingClientRect();
  const scrollDistance = Math.abs(rectAfter.top - rectBefore.top);
  const scrollWait = scrollDistance > 10
    ? Math.min(800, Math.max(100, scrollDistance * 0.4))
    : 50;
  await delay(scrollWait);

  await moveCursorTo(el);
  highlightElement(el);
  await cursorClickFlash();

  el.focus();
  el.dispatchEvent(new Event("focus", { bubbles: true }));

  // Clear existing content
  el.innerText = "";
  el.dispatchEvent(new Event("input", { bubbles: true }));

  // Type character by character using execCommand
  for (const char of value) {
    document.execCommand("insertText", false, char);
    const speed = behaviorProfile.avg_typing_speed *
      (1 + (Math.random() - 0.5) * behaviorProfile.typing_variance);
    await delay(Math.max(30, speed));
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

  // Visual: scroll into view, move cursor, highlight
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await delay(300);
  await moveCursorTo(el);
  highlightElement(el);
  await cursorClickFlash();

  // For <a> links with target="_blank" (e.g. LinkedIn "Apply ↗"), navigate in
  // the current tab instead of opening a new one — the bot needs to follow the link.
  if (el.tagName === "A" && el.target === "_blank" && el.href) {
    const href = el.href;
    window.location.href = href;
    return { success: true, navigated: href };
  }

  // Dispatch real mouse events with coordinates — some sites (Zoho, etc.)
  // listen for mousedown/mouseup/mouseover, not just .click()
  const rect = el.getBoundingClientRect();
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  const evtOpts = { bubbles: true, cancelable: true, clientX: cx, clientY: cy, view: window };

  el.dispatchEvent(new MouseEvent("mouseover", evtOpts));
  await delay(50 + Math.random() * 80);
  el.dispatchEvent(new MouseEvent("mousedown", { ...evtOpts, button: 0 }));
  await delay(30 + Math.random() * 60);
  el.dispatchEvent(new MouseEvent("mouseup", { ...evtOpts, button: 0 }));
  el.dispatchEvent(new MouseEvent("click", { ...evtOpts, button: 0 }));

  // Also call .click() as final fallback
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
    await smartScroll(target);
    await delay(getFieldGap(match));
    target.click();
    matched.radio.dispatchEvent(new Event("change", { bubbles: true }));
    return { success: true, value_set: match };
  }

  return { success: false, error: "Match found but click failed" };
}

async function fillCustomSelect(triggerSelector, value) {
  const trigger = resolveSelector(triggerSelector);
  if (!trigger) return { success: false, error: "Trigger not found: " + triggerSelector };

  await smartScroll(trigger);
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

  await smartScroll(el);
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

/**
 * Fill a combobox/custom dropdown by clicking it open, scanning the entire
 * document for the floating option panel, and selecting the best match.
 * Works with Zoho lyte-dropdown, React Select, MUI Select, etc.
 */
async function fillCombobox(selector, value) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Visual cursor
  await moveCursorTo(el);
  highlightElement(el);

  await smartScroll(el);

  // Click the combobox trigger to open the dropdown
  el.click();
  el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  await delay(800);

  // Also try clicking any inner trigger (arrow button, input, etc.)
  const innerTrigger = el.querySelector("input, [class*='trigger'], [class*='arrow'], [class*='toggle'], button");
  if (innerTrigger) {
    innerTrigger.click();
    innerTrigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    await delay(800);
  }

  // Search the ENTIRE document for floating dropdown panels with options
  const optionSelectors = [
    "[role='option']",
    "[role='listbox'] li",
    "[role='listbox'] [role='option']",
    "lyte-drop-box li",
    "lyte-drop-box [role='option']",
    ".lyte-dropdown-items li",
    ".cxDropdownMenuList li",
    ".cxDropdownMenuItems li",
    "[class*='dropdown'] li",
    "[class*='dropdown'] [class*='option']",
    "[class*='menu'] li[class*='option']",
    "[class*='listbox'] li",
    "ul[class*='select'] li",
    ".select-options li",
    "[data-value]",
  ];

  const valueLower = value.toLowerCase().trim();
  let allOptions = [];

  for (const optSel of optionSelectors) {
    const opts = document.querySelectorAll(optSel);
    if (opts.length === 0) continue;

    for (const opt of opts) {
      const text = opt.textContent.trim();
      if (!text || text.length > 200) continue;
      allOptions.push({ el: opt, text });

      // Exact match
      if (text.toLowerCase() === valueLower) {
        await moveCursorTo(opt);
        cursorClickFlash();
        opt.click();
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await delay(500);
        return { success: true, value_set: text, match: "exact" };
      }
    }

    // Partial match — option contains our value or vice versa
    for (const opt of opts) {
      const text = opt.textContent.trim();
      if (!text) continue;
      if (text.toLowerCase().includes(valueLower) || valueLower.includes(text.toLowerCase())) {
        await moveCursorTo(opt);
        cursorClickFlash();
        opt.click();
        opt.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await delay(500);
        return { success: true, value_set: text, match: "partial" };
      }
    }
  }

  // If we found options but none matched, report what was available
  if (allOptions.length > 0) {
    const available = allOptions.slice(0, 20).map(o => o.text);
    return { success: false, error: "No matching option", available_options: available, wanted: value };
  }

  // No options found — try typing into any input inside the combobox
  const innerInput = el.querySelector("input");
  if (innerInput) {
    innerInput.focus();
    innerInput.value = "";
    for (const char of value) {
      innerInput.dispatchEvent(new KeyboardEvent("keydown", { key: char, bubbles: true }));
      innerInput.value += char;
      innerInput.dispatchEvent(new Event("input", { bubbles: true }));
      innerInput.dispatchEvent(new KeyboardEvent("keyup", { key: char, bubbles: true }));
      await delay(80);
    }
    await delay(1000);

    // Check for suggestions again
    for (const optSel of optionSelectors) {
      const opts = document.querySelectorAll(optSel);
      for (const opt of opts) {
        const text = opt.textContent.trim();
        if (text && (text.toLowerCase().includes(valueLower) || valueLower.includes(text.toLowerCase()))) {
          await moveCursorTo(opt);
          cursorClickFlash();
          opt.click();
          await delay(500);
          return { success: true, value_set: text, match: "typed_then_selected" };
        }
      }
      if (opts.length > 0) {
        const first = opts[0];
        const firstText = first.textContent.trim();
        await moveCursorTo(first);
        cursorClickFlash();
        first.click();
        await delay(500);
        return { success: true, value_set: firstText, match: "typed_first_option" };
      }
    }
  }

  return { success: false, error: "Could not open dropdown or find options" };
}

async function fillTagInput(selector, values) {
  const el = resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  await smartScroll(el);
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

  await smartScroll(el);

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

/**
 * Comprehensive validation error scan — checks 5 strategies:
 * 1. aria-invalid="true" elements
 * 2. role="alert" elements (excluding header/nav)
 * 3. .error / .invalid-feedback / .field-error class elements
 * 4. aria-errormessage references
 * 5. ATS-specific error patterns (Greenhouse, LinkedIn Easy Apply, Workday)
 *
 * Returns { errors: [{selector, field_label, error_message}], has_errors, count }
 */
function scanValidationErrors() {
  const errors = [];
  const seen = new Set();

  // Strategy 1: aria-invalid elements
  for (const el of document.querySelectorAll("[aria-invalid='true']")) {
    const errId = el.getAttribute("aria-errormessage");
    let errMsg = "";
    if (errId) {
      const errEl = document.getElementById(errId);
      if (errEl) errMsg = errEl.textContent.trim();
    }
    if (!errMsg) {
      const parent = el.closest(".form-group, .field-wrapper, .form-field, [data-test-form-element]");
      if (parent) {
        const errEl = parent.querySelector(".error, .invalid-feedback, [role='alert'], .field-error");
        if (errEl) errMsg = errEl.textContent.trim();
      }
    }
    const key = (el.id || el.name || "") + errMsg;
    if (!seen.has(key)) {
      seen.add(key);
      errors.push({
        selector: el.id ? `#${el.id}` : `[name="${el.name || ""}"]`,
        field_label: extractFieldContext(el).label,
        error_message: errMsg || "Field marked as invalid",
      });
    }
  }

  // Strategy 2: role="alert" elements (not inside header/nav)
  for (const alert of document.querySelectorAll("[role='alert']")) {
    if (alert.closest("header, nav, [role='banner']")) continue;
    const text = alert.textContent.trim();
    if (text && text.length > 2 && text.length < 500 && !seen.has(text)) {
      seen.add(text);
      errors.push({
        selector: "[role='alert']",
        field_label: "",
        error_message: text,
      });
    }
  }

  // Strategy 3: Error class elements near form fields
  const errorSelectors = [
    ".error:not(header .error)",
    ".invalid-feedback",
    ".field-error",
    ".form-error",
    "[class*='error-message']",
    "[class*='validation-error']",
    "[class*='errorText']",
    ".jobs-easy-apply-form-section__error",
    ".fb-dash-form-element__error",
    "[data-test='form-field-error']",
  ];

  for (const sel of errorSelectors) {
    for (const errEl of document.querySelectorAll(sel)) {
      const text = errEl.textContent.trim();
      if (text && text.length > 2 && text.length < 300 && !seen.has(text)) {
        seen.add(text);
        const parent = errEl.closest(".form-group, .field-wrapper, .form-field, fieldset");
        let fieldLabel = "";
        if (parent) {
          const labelEl = parent.querySelector("label, legend, [class*='label']");
          if (labelEl) fieldLabel = labelEl.textContent.trim().substring(0, 100);
        }
        errors.push({
          selector: sel,
          field_label: fieldLabel,
          error_message: text,
        });
      }
    }
  }

  return { errors, has_errors: errors.length > 0, count: errors.length };
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
        result = await withRetry(() => fillField(payload.selector, payload.value));
        break;
      case "upload":
        result = await uploadFile(payload.selector, payload.file_base64, payload.file_name, payload.mime_type);
        break;
      case "click":
        result = await clickElement(payload.selector);
        break;
      case "select":
        result = await withRetry(() => selectOption(payload.selector, payload.value));
        break;
      case "check":
        result = await withRetry(() => checkBox(payload.selector, payload.value));
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
          // Also check for <a> links with "apply" in class/aria-label (LinkedIn external Apply ↗)
          const hasApplyLink = !!document.querySelector("a[class*='apply'], a[aria-label*='Apply'], a[href*='apply']");
          if (hasApply || hasApplyLink) break;
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
        result = await withRetry(() => fillRadioGroup(payload.selector, payload.value));
        break;
      case "fill_custom_select":
        result = await withRetry(() => fillCustomSelect(payload.selector, payload.value));
        break;
      case "fill_autocomplete":
        result = await withRetry(() => fillAutocomplete(payload.selector, payload.value));
        break;
      case "fill_combobox":
        result = await withRetry(() => fillCombobox(payload.selector, payload.value));
        break;
      case "fill_tag_input":
        result = await withRetry(() => fillTagInput(payload.selector, payload.values || []));
        break;
      case "fill_date":
        result = await withRetry(() => fillDate(payload.selector, payload.value));
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
      case "scan_validation_errors":
        result = scanValidationErrors();
        break;
      case "fill_contenteditable": {
        result = await withRetry(async () => {
          const ceEl = resolveSelector(payload.selector);
          return ceEl
            ? await fillContentEditable(ceEl, payload.value)
            : { success: false, error: "Element not found: " + payload.selector };
        });
        break;
      }
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
      case "save_form_progress":
        saveFormProgress(payload.url || location.href, payload.progress || {});
        result = { success: true };
        break;
      case "get_form_progress":
        result = await getFormProgress(payload.url || location.href);
        result = result || { success: false, error: "No saved progress" };
        break;
      case "clear_form_progress":
        clearFormProgress(payload.url || location.href);
        result = { success: true };
        break;
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

// ═══════════════════════════════════════════════════════════════
// MV3 State Persistence — survive service worker restarts
// ═══════════════════════════════════════════════════════════════
//
// Chrome MV3 service workers go idle after 30s of inactivity.
// When mid-fill and the worker restarts, in-progress form data
// would be lost. These functions persist form state to
// chrome.storage.session (per-session, cleared on browser close)
// so the orchestrator can resume where it left off.

/**
 * Save current form progress to session storage.
 * Called after each successful field fill.
 * @param {string} url - Current page URL
 * @param {Object} progress - { filled_fields: [{selector, value}], current_step: number, total_steps: number }
 */
function saveFormProgress(url, progress) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  const data = {
    url,
    ...progress,
    timestamp: Date.now(),
  };
  chrome.storage.session.set({ [key]: data }).catch(() => {});
}

/**
 * Retrieve saved form progress for a URL.
 * Called on reconnection after MV3 service worker restart.
 * @param {string} url - Page URL to look up
 * @returns {Promise<Object|null>} Saved progress or null
 */
async function getFormProgress(url) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  try {
    const data = await chrome.storage.session.get(key);
    const progress = data[key];
    if (!progress) return null;
    // Expire after 10 minutes — stale progress is dangerous
    if (Date.now() - progress.timestamp > 10 * 60 * 1000) {
      chrome.storage.session.remove(key).catch(() => {});
      return null;
    }
    return progress;
  } catch (_) {
    return null;
  }
}

/**
 * Clear form progress for a URL (called after successful submit).
 * @param {string} url - Page URL to clear
 */
function clearFormProgress(url) {
  const key = "formProgress_" + btoa(url).slice(0, 40);
  chrome.storage.session.remove(key).catch(() => {});
}
