/**
 * Phase Engine: 4-phase trust system for job automation
 * Tracks automation confidence per platform and manages graduation/demotion logic
 *
 * Phases: observation -> dry_run -> supervised -> auto
 * State persisted in chrome.storage.local under key "phases"
 */

import { PLATFORM_MAX_PHASE, GRADUATION_THRESHOLDS } from './config.js';

// Phase hierarchy for comparison
const PHASE_ORDER = ['observation', 'dry_run', 'supervised', 'auto'];

// Storage key for phase state
const STORAGE_KEY = 'phases';

// Default stats structure
const DEFAULT_STATS = {
  consecutive_correct: 0,
  clean_runs: 0,
  unmodified_approvals: 0,
  errors: 0,
  last_error_time: null,
  last_demotion_time: null,
  psi_baseline: null // Population Stability Index baseline distribution
};

/**
 * Initialize phases from chrome.storage.local
 * Creates default phases for all platforms if not found
 *
 * @returns {Promise<Object>} - Full phases object
 */
export async function initPhases() {
  return new Promise((resolve) => {
    chrome.storage.local.get([STORAGE_KEY], (result) => {
      if (result[STORAGE_KEY]) {
        resolve(result[STORAGE_KEY]);
      } else {
        // Create default phases for known platforms
        const defaultPhases = {
          linkedin: { current: 'observation', stats: { ...DEFAULT_STATS } },
          indeed: { current: 'observation', stats: { ...DEFAULT_STATS } },
          workday: { current: 'observation', stats: { ...DEFAULT_STATS } },
          glassdoor: { current: 'observation', stats: { ...DEFAULT_STATS } },
          reed: { current: 'observation', stats: { ...DEFAULT_STATS } },
          greenhouse: { current: 'observation', stats: { ...DEFAULT_STATS } },
          lever: { current: 'observation', stats: { ...DEFAULT_STATS } },
          generic: { current: 'observation', stats: { ...DEFAULT_STATS } }
        };

        chrome.storage.local.set({ [STORAGE_KEY]: defaultPhases }, () => {
          resolve(defaultPhases);
        });
      }
    });
  });
}

/**
 * Get current phase for a platform
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<string>} - Current phase ('observation', 'dry_run', 'supervised', 'auto')
 */
export async function getPhase(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) {
    console.warn(`Platform ${platform} not found, returning observation`);
    return 'observation';
  }
  return phases[platform].current;
}

/**
 * Get phase statistics for a platform
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<Object>} - Stats object
 */
export async function getPhaseStats(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) {
    return { ...DEFAULT_STATS };
  }
  return phases[platform].stats;
}

/**
 * Get all phases
 *
 * @returns {Promise<Object>} - Full phases object
 */
export async function getAllPhases() {
  return _getPhases();
}

/**
 * Check if platform can fill fields (dry_run or higher)
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<boolean>} - True if phase >= dry_run
 */
export async function canFill(platform) {
  const phase = await getPhase(platform);
  return PHASE_ORDER.indexOf(phase) >= PHASE_ORDER.indexOf('dry_run');
}

/**
 * Check if platform can auto-submit (auto phase only AND ATS score >= 95)
 *
 * @param {string} platform - Platform identifier
 * @param {number} atsScore - ATS confidence score (0-100)
 * @returns {Promise<boolean>} - True if phase == auto AND atsScore >= 95
 */
export async function canSubmit(platform, atsScore = 0) {
  const phase = await getPhase(platform);
  return phase === 'auto' && atsScore >= 95;
}

/**
 * Check if platform requires human approval (supervised phase)
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<boolean>} - True if phase == supervised
 */
export async function needsApproval(platform) {
  const phase = await getPhase(platform);
  return phase === 'supervised';
}

/**
 * Record a correct field mapping
 * Increments consecutive_correct and checks for graduation
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<Object|null>} - Graduation result or null
 */
export async function recordCorrectMapping(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return null;

  const stats = phases[platform].stats;
  stats.consecutive_correct = (stats.consecutive_correct || 0) + 1;

  await _savePhases(phases);
  return checkGraduation(platform);
}

/**
 * Record a field mapping error
 * Resets consecutive_correct and checks for demotion (2 consecutive errors)
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<Object|null>} - Demotion result or null
 */
export async function recordMappingError(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return null;

  const stats = phases[platform].stats;
  const now = Date.now();

  // Check if we already had an error recently (within 1 minute)
  if (stats.last_error_time && (now - stats.last_error_time) < 60000) {
    // Two consecutive errors within 1 minute - demote
    stats.consecutive_correct = 0;
    stats.errors = (stats.errors || 0) + 1;
    await _savePhases(phases);
    return demote(platform, 'observation', 'consecutive_mapping_errors');
  }

  // First error - just reset counter and record time
  stats.consecutive_correct = 0;
  stats.last_error_time = now;
  await _savePhases(phases);

  return null;
}

/**
 * Record a clean dry run (no errors during fill simulation)
 * Increments clean_runs and checks for graduation
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<Object|null>} - Graduation result or null
 */
export async function recordCleanDryRun(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return null;

  const stats = phases[platform].stats;
  stats.clean_runs = (stats.clean_runs || 0) + 1;

  await _savePhases(phases);
  return checkGraduation(platform);
}

/**
 * Record a dry run error
 * Resets clean_runs counter
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<void>}
 */
export async function recordDryRunError(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return;

  const stats = phases[platform].stats;
  stats.clean_runs = 0;
  stats.errors = (stats.errors || 0) + 1;

  await _savePhases(phases);
}

/**
 * Record human approval of filled form
 * If unmodified: increment counter and check graduation
 * If modified: reset unmodified_approvals counter
 *
 * @param {string} platform - Platform identifier
 * @param {boolean} modified - True if human modified the filled values
 * @returns {Promise<Object|null>} - Graduation result or null if modified
 */
export async function recordApproval(platform, modified = false) {
  const phases = await _getPhases();
  if (!phases[platform]) return null;

  const stats = phases[platform].stats;

  if (modified) {
    stats.unmodified_approvals = 0;
  } else {
    stats.unmodified_approvals = (stats.unmodified_approvals || 0) + 1;
    await _savePhases(phases);
    return checkGraduation(platform);
  }

  await _savePhases(phases);
  return null;
}

/**
 * Record a submission error
 * Demotes to supervised phase
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<Object>} - Demotion result
 */
export async function recordSubmissionError(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return null;

  const stats = phases[platform].stats;
  stats.errors = (stats.errors || 0) + 1;

  await _savePhases(phases);
  return demote(platform, 'supervised', 'submission_error');
}

/**
 * Record CAPTCHA encounter
 * Demotes to supervised and returns alert message
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<{demoted: boolean, message: string, from: string, to: string}>}
 */
export async function recordCaptcha(platform) {
  const phase = await getPhase(platform);
  const result = await demote(platform, 'supervised', 'captcha_triggered');

  return {
    demoted: result.demoted,
    message: `CAPTCHA on ${platform}: demoted from ${phase} to supervised. Manual completion required.`,
    from: result.from,
    to: result.to
  };
}

/**
 * Check if platform is eligible for graduation
 * Evaluates against current phase thresholds
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<{graduated: boolean, from: string, to: string}|null>}
 */
export async function checkGraduation(platform) {
  const phase = await getPhase(platform);
  const stats = await getPhaseStats(platform);
  const maxPhase = PLATFORM_MAX_PHASE[platform] || 'supervised';

  if (phase === maxPhase || phase === 'auto') {
    return null; // Already at max phase
  }

  let eligible = false;
  let nextPhase = null;

  if (phase === 'observation') {
    // observation -> dry_run
    const thresholds = GRADUATION_THRESHOLDS.observation_to_dry_run;
    const meetsCorrectMappings = stats.consecutive_correct >= thresholds.correct_mappings;
    const psiOk = !stats.psi_baseline || stats.psi_baseline < thresholds.psi_threshold;

    eligible = meetsCorrectMappings && psiOk;
    nextPhase = 'dry_run';
  } else if (phase === 'dry_run') {
    // dry_run -> supervised
    const thresholds = GRADUATION_THRESHOLDS.dry_run_to_supervised;
    eligible =
      stats.clean_runs >= thresholds.clean_runs &&
      stats.errors === 0;

    nextPhase = 'supervised';
  } else if (phase === 'supervised' && maxPhase === 'auto') {
    // supervised -> auto
    const thresholds = GRADUATION_THRESHOLDS.supervised_to_auto;
    const psiOk = !stats.psi_baseline || stats.psi_baseline < thresholds.psi_threshold;

    eligible =
      stats.unmodified_approvals >= thresholds.unmodified_approvals &&
      stats.errors === 0 &&
      psiOk;

    nextPhase = 'auto';
  }

  if (eligible && nextPhase) {
    // Perform graduation
    const phases = await _getPhases();
    const oldPhase = phases[platform].current;
    phases[platform].current = nextPhase;
    // Reset stats for new phase
    phases[platform].stats.consecutive_correct = 0;
    phases[platform].stats.clean_runs = 0;
    phases[platform].stats.unmodified_approvals = 0;
    phases[platform].stats.errors = 0;

    await _savePhases(phases);

    return {
      graduated: true,
      from: oldPhase,
      to: nextPhase
    };
  }

  return null;
}

/**
 * Demote platform to a lower phase
 * Resets relevant counters and records demotion time
 *
 * @param {string} platform - Platform identifier
 * @param {string} targetPhase - Target phase to demote to
 * @param {string} reason - Reason for demotion
 * @returns {Promise<{demoted: boolean, from: string, to: string, reason: string}>}
 */
export async function demote(platform, targetPhase, reason) {
  const phases = await _getPhases();
  if (!phases[platform]) {
    return { demoted: false, from: 'unknown', to: 'unknown', reason };
  }

  const oldPhase = phases[platform].current;
  const oldOrder = PHASE_ORDER.indexOf(oldPhase);
  const newOrder = PHASE_ORDER.indexOf(targetPhase);

  // Don't "demote" to the same or higher phase
  if (newOrder >= oldOrder) {
    return { demoted: false, from: oldPhase, to: oldPhase, reason };
  }

  phases[platform].current = targetPhase;
  const stats = phases[platform].stats;

  // Reset stats for demotion
  stats.consecutive_correct = 0;
  stats.clean_runs = 0;
  stats.unmodified_approvals = 0;
  stats.errors = 0;
  stats.last_demotion_time = Date.now();

  await _savePhases(phases);

  return {
    demoted: true,
    from: oldPhase,
    to: targetPhase,
    reason
  };
}

/**
 * Compute Population Stability Index (PSI) between two distributions
 * PSI measures how much a distribution has shifted from baseline
 * Formula: sum((current% - baseline%) * ln(current% / baseline%))
 *
 * @param {Object} current - Current distribution (field_type -> count)
 * @param {Object} baseline - Baseline distribution (field_type -> count)
 * @returns {number} - PSI value (0 = identical, <0.1 = stable, >0.2 = significant drift)
 */
export function computePSI(current, baseline) {
  if (!current || !baseline) {
    return 0;
  }

  const allKeys = new Set([...Object.keys(current), ...Object.keys(baseline)]);
  let psi = 0;

  const currentTotal = Object.values(current).reduce((a, b) => a + b, 0);
  const baselineTotal = Object.values(baseline).reduce((a, b) => a + b, 0);

  for (const key of allKeys) {
    const currentCount = current[key] || 0;
    const baselineCount = baseline[key] || 0;

    const currentPct = currentCount / Math.max(currentTotal, 1);
    const baselinePct = baselineCount / Math.max(baselineTotal, 1);

    // Handle zero percentages to avoid log(0)
    if (baselinePct === 0 && currentPct === 0) {
      continue;
    }
    if (baselinePct === 0) {
      // Baseline has 0, current has something - assign high penalty
      psi += 0.1;
      continue;
    }

    psi += (currentPct - baselinePct) * Math.log(currentPct / baselinePct);
  }

  return Math.abs(psi);
}

/**
 * Check for PSI drift in field detection
 * Compares current distribution to stored baseline
 * Returns action recommendation based on drift level
 *
 * @param {string} platform - Platform identifier
 * @param {Object} currentDistribution - Current field type distribution
 * @returns {Promise<{drift: number, action: 'none'|'alert'|'demote'}>}
 */
export async function checkPSIDrift(platform, currentDistribution) {
  const phases = await _getPhases();
  if (!phases[platform]) {
    return { drift: 0, action: 'none' };
  }

  const stats = phases[platform].stats;

  // Initialize baseline on first run
  if (!stats.psi_baseline) {
    stats.psi_baseline = currentDistribution;
    await _savePhases(phases);
    return { drift: 0, action: 'none' };
  }

  const drift = computePSI(currentDistribution, stats.psi_baseline);

  let action = 'none';
  if (drift > 0.2) {
    action = 'demote';
  } else if (drift > 0.1) {
    action = 'alert';
  }

  return { drift, action };
}

/**
 * Update PSI baseline (call this after successful run)
 *
 * @param {string} platform - Platform identifier
 * @param {Object} distribution - New distribution to use as baseline
 * @returns {Promise<void>}
 */
export async function updatePSIBaseline(platform, distribution) {
  const phases = await _getPhases();
  if (!phases[platform]) return;

  phases[platform].stats.psi_baseline = distribution;
  await _savePhases(phases);
}

/**
 * Reset all stats for a platform (testing/manual reset)
 *
 * @param {string} platform - Platform identifier
 * @returns {Promise<void>}
 */
export async function resetPlatform(platform) {
  const phases = await _getPhases();
  if (!phases[platform]) return;

  phases[platform].current = 'observation';
  phases[platform].stats = { ...DEFAULT_STATS };

  await _savePhases(phases);
}

/**
 * Get phases from storage (internal helper)
 *
 * @returns {Promise<Object>}
 * @private
 */
async function _getPhases() {
  return new Promise((resolve) => {
    chrome.storage.local.get([STORAGE_KEY], (result) => {
      resolve(result[STORAGE_KEY] || {});
    });
  });
}

/**
 * Save phases to storage (internal helper)
 *
 * @param {Object} phases - Phases object to save
 * @returns {Promise<void>}
 * @private
 */
async function _savePhases(phases) {
  return new Promise((resolve) => {
    chrome.storage.local.set({ [STORAGE_KEY]: phases }, resolve);
  });
}
