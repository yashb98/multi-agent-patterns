// extension/persistence/form_progress.js
// Form progress persistence via chrome.storage.session.
// Extracted from content.js — Phase 5, Task 20.

window.JobPulse = window.JobPulse || {};

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

window.JobPulse.persistence = { saveFormProgress, getFormProgress, clearFormProgress };
