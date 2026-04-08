// extension/detectors/verification.js — Verification wall detection for label strategy
window.JobPulse = window.JobPulse || {};
window.JobPulse.detectors = window.JobPulse.detectors || {};

/**
 * Detect verification walls (CAPTCHAs, Cloudflare challenges, HTTP blocks).
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

window.JobPulse.detectors.verification = {
  detectVerificationWall,
};
