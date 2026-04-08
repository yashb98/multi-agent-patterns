// extension/core/dom.js — DOM utilities (delay, scroll, retry, visibility, selectors)
//
// Zero dependencies. Loaded first in manifest.json content_scripts.

window.JobPulse = window.JobPulse || {};

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
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

/**
 * Resolve a CSS selector, including shadow DOM paths.
 * Shadow DOM syntax: "host-selector>>inner-selector"
 * Example: "#my-component>>input.email"
 */
function resolveSelector(selector) {
  // Fix invalid CSS: #<digit>... → [id="..."] (CSS forbids # + digit)
  const fixed = selector.replace(/#(\d[^\s\[>+~,]*)/g, (_, id) => `[id="${id}"]`);
  if (fixed.includes(">>")) {
    const parts = fixed.split(">>");
    let el = document.querySelector(parts[0].trim());
    for (let i = 1; i < parts.length && el; i++) {
      el = (el.shadowRoot || el).querySelector(parts[i].trim());
    }
    return el;
  }
  return document.querySelector(fixed);
}

window.JobPulse.dom = { delay, smartScroll, withRetry, isFieldVisible, resolveSelector };
