// extension/detectors/native.js — Page classification and navigation for label strategy
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

function isConfirmationPage() {
  const body = (document.body?.innerText || "").toLowerCase().slice(0, 2000);
  return ["thank you for applying", "application has been received",
    "application submitted", "successfully submitted",
    "application is complete", "we have received your application",
  ].some(phrase => body.includes(phrase));
}

function isSubmitPage() {
  for (const name of ["Submit Application", "Submit", "Apply", "Apply Now"]) {
    for (const btn of document.querySelectorAll("button, input[type='submit'], [role='button']")) {
      const text = (btn.textContent || btn.value || "").trim();
      if (text.toLowerCase().includes(name.toLowerCase()) && window.JobPulse.dom.isFieldVisible(btn))
        return true;
    }
  }
  return false;
}

function detectNavigationButton() {
  const JP = window.JobPulse;
  const groups = [
    { action: "submit", names: ["Submit Application", "Submit", "Apply", "Apply Now"] },
    { action: "next", names: ["Save & Continue", "Continue", "Next", "Proceed", "Save and Continue"] },
  ];
  for (const { action, names } of groups) {
    for (const name of names) {
      for (const btn of document.querySelectorAll("button, input[type='submit'], [role='button']")) {
        const text = (btn.textContent || btn.value || "").trim();
        if (text.toLowerCase().includes(name.toLowerCase()) && JP.dom.isFieldVisible(btn) && !btn.disabled)
          return { action, element: btn, text };
      }
      if (action === "submit") {
        for (const link of document.querySelectorAll("a")) {
          const text = (link.textContent || "").trim();
          if (text.toLowerCase().includes(name.toLowerCase()) && JP.dom.isFieldVisible(link))
            return { action: "next", element: link, text };
        }
      }
    }
  }
  return null;
}

function detectProgress() {
  const text = document.body?.innerText || "";
  const match = text.match(/(?:step|page)\s+(\d+)\s+(?:of|\/)\s+(\d+)/i);
  if (match) {
    const [, current, total] = [null, parseInt(match[1]), parseInt(match[2])];
    if (current >= 1 && current <= total && total <= 20) return { current, total };
  }
  return null;
}

function hasUnfilledRequired() {
  const JP = window.JobPulse;
  for (const el of document.querySelectorAll("[required], [aria-required='true']")) {
    if (!JP.dom.isFieldVisible(el)) continue;
    const val = el.value || el.textContent || "";
    if (!val.trim() && el.type !== "hidden") return true;
  }
  return false;
}

window.JobPulse.detectors.native = {
  isConfirmationPage, isSubmitPage, detectNavigationButton, detectProgress, hasUnfilledRequired,
};
