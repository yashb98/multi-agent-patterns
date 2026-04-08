// extension/fillers/fill_actions.js — Click, scroll, wait interactions
// Changes when: click/scroll/wait interaction changes
window.JobPulse = window.JobPulse || {};
window.JobPulse.fillers = window.JobPulse.fillers || {};

/**
 * Click an element with human-like scroll-into-view and reading pause.
 */
async function clickElement(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  // Visual: scroll into view, move cursor, highlight
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await JP.dom.delay(300);
  await JP.cursor.moveCursorTo(el);
  JP.cursor.highlightElement(el);
  await JP.cursor.cursorClickFlash();

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
  await JP.dom.delay(50 + Math.random() * 80);
  el.dispatchEvent(new MouseEvent("mousedown", { ...evtOpts, button: 0 }));
  await JP.dom.delay(30 + Math.random() * 60);
  el.dispatchEvent(new MouseEvent("mouseup", { ...evtOpts, button: 0 }));
  el.dispatchEvent(new MouseEvent("click", { ...evtOpts, button: 0 }));

  // Also call .click() as final fallback
  el.click();

  return { success: true };
}

async function forceClick(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await JP.dom.delay(200);

  el.dispatchEvent(new MouseEvent("click", {
    bubbles: true, cancelable: true, view: window,
  }));

  return { success: true };
}

async function scrollTo(selector) {
  const JP = window.JobPulse;
  const el = JP.dom.resolveSelector(selector);
  if (!el) return { success: false, error: "Element not found: " + selector };

  el.scrollIntoView({ behavior: "smooth", block: "center" });
  await JP.dom.delay(500);
  return { success: true };
}

async function waitForSelector(selector, timeoutMs) {
  const JP = window.JobPulse;
  const maxWait = timeoutMs || 10000;
  const pollInterval = 300;
  let elapsed = 0;

  while (elapsed < maxWait) {
    const el = JP.dom.resolveSelector(selector);
    if (el) {
      return {
        success: true,
        found_after_ms: elapsed,
        tag: el.tagName.toLowerCase(),
        text: (el.textContent || "").trim().substring(0, 100),
      };
    }
    await JP.dom.delay(pollInterval);
    elapsed += pollInterval;
  }

  return { success: false, error: `Selector '${selector}' not found after ${maxWait}ms` };
}

window.JobPulse.fillers.actions = { clickElement, forceClick, scrollTo, waitForSelector };
