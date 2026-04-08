// extension/core/timing.js — Human behavior calibration and timing
//
// Zero dependencies on other core/ modules.
// Passively observes real user keystrokes and clicks to calibrate
// typing speed and interaction timing.

window.JobPulse = window.JobPulse || {};

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

window.JobPulse.timing = { behaviorProfile, getFieldGap };
