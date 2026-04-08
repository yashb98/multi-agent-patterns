// extension/detectors/snapshot.js — Page snapshot builder for label strategy
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

/**
 * Build a complete PageSnapshot of the current page state.
 * This is the primary data structure sent to the Python backend.
 */
function buildSnapshot() {
  const JP = window.JobPulse;
  const fields = JP.scanners.dom.deepScan();

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
        enabled: !el.disabled && el.getAttribute("aria-disabled") !== "true",
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
  const formGroups = JP.scanners.dom.scanFormGroups(modal ? (
    modal.id ? `#${modal.id}` : "[role='dialog']"
  ) : null);

  return {
    url: window.location.href,
    title: document.title,
    fields,
    buttons,
    verification_wall: JP.detectors.verification.detectVerificationWall(),
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

window.JobPulse.detectors.snapshot = {
  buildSnapshot,
};
