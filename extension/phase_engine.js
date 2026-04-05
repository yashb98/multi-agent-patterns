// extension/phase_engine.js — 4-phase trust system with graduation/demotion

import {
  PLATFORM_MAX_PHASE,
  GRADUATION_THRESHOLDS,
} from "./config.js";

const PHASES = ["observation", "dry_run", "supervised", "auto"];
const STORAGE_KEY = "phase_state";

// ─── State Management ─────────────────────────────────

async function getState() {
  const result = await chrome.storage.local.get(STORAGE_KEY);
  return result[STORAGE_KEY] || {};
}

async function setState(state) {
  await chrome.storage.local.set({ [STORAGE_KEY]: state });
}

export async function getPlatformPhase(platform) {
  const state = await getState();
  return state[platform]?.current || "observation";
}

export async function getPlatformStats(platform) {
  const state = await getState();
  return state[platform] || {
    current: "observation",
    consecutive_correct: 0,
    consecutive_clean: 0,
    consecutive_approved: 0,
    total_observed: 0,
    total_dry_runs: 0,
    total_applied: 0,
    total_errors: 0,
    demotions: [],
  };
}

export async function getAllPhases() {
  const state = await getState();
  return state;
}

// ─── Graduation ───────────────────────────────────────

export async function recordObservationResult(platform, allCorrect) {
  const state = await getState();
  if (!state[platform]) state[platform] = { current: "observation", consecutive_correct: 0, total_observed: 0 };
  const ps = state[platform];

  ps.total_observed++;
  if (allCorrect) {
    ps.consecutive_correct++;
  } else {
    ps.consecutive_correct = 0;
  }

  // Check graduation
  if (ps.consecutive_correct >= GRADUATION_THRESHOLDS.observation_to_dry_run) {
    const maxPhase = PLATFORM_MAX_PHASE[platform] || "supervised";
    if (PHASES.indexOf("dry_run") <= PHASES.indexOf(maxPhase)) {
      ps.current = "dry_run";
      ps.consecutive_correct = 0;
      ps.consecutive_clean = 0;
    }
  }

  await setState(state);
  return ps;
}

export async function recordDryRunResult(platform, clean) {
  const state = await getState();
  if (!state[platform]) state[platform] = { current: "dry_run", consecutive_clean: 0, total_dry_runs: 0 };
  const ps = state[platform];

  ps.total_dry_runs++;
  if (clean) {
    ps.consecutive_clean++;
  } else {
    ps.consecutive_clean = 0;
  }

  // Check graduation
  if (ps.consecutive_clean >= GRADUATION_THRESHOLDS.dry_run_to_supervised) {
    const maxPhase = PLATFORM_MAX_PHASE[platform] || "supervised";
    if (PHASES.indexOf("supervised") <= PHASES.indexOf(maxPhase)) {
      ps.current = "supervised";
      ps.consecutive_clean = 0;
      ps.consecutive_approved = 0;
    }
  }

  await setState(state);
  return ps;
}

export async function recordSupervisedResult(platform, approvedUnmodified) {
  const state = await getState();
  if (!state[platform]) state[platform] = { current: "supervised", consecutive_approved: 0, total_applied: 0 };
  const ps = state[platform];

  ps.total_applied++;
  if (approvedUnmodified) {
    ps.consecutive_approved++;
  } else {
    ps.consecutive_approved = 0;
  }

  // Check graduation
  if (ps.consecutive_approved >= GRADUATION_THRESHOLDS.supervised_to_auto) {
    const maxPhase = PLATFORM_MAX_PHASE[platform] || "supervised";
    if (PHASES.indexOf("auto") <= PHASES.indexOf(maxPhase)) {
      ps.current = "auto";
      ps.consecutive_approved = 0;
    }
  }

  await setState(state);
  return ps;
}

// ─── Demotion ─────────────────────────────────────────

export async function demote(platform, targetPhase, reason) {
  const state = await getState();
  if (!state[platform]) state[platform] = { current: "observation" };
  const ps = state[platform];

  const oldPhase = ps.current;
  ps.current = targetPhase;

  // Reset counters for the target phase
  ps.consecutive_correct = 0;
  ps.consecutive_clean = 0;
  ps.consecutive_approved = 0;

  // Log demotion
  if (!ps.demotions) ps.demotions = [];
  ps.demotions.push({
    from: oldPhase,
    to: targetPhase,
    reason,
    at: new Date().toISOString(),
  });
  // Keep last 20 demotions only
  if (ps.demotions.length > 20) ps.demotions = ps.demotions.slice(-20);

  ps.total_errors = (ps.total_errors || 0) + 1;

  await setState(state);
  return ps;
}

// ─── Manual Override ──────────────────────────────────

export async function setPhase(platform, phase) {
  const maxPhase = PLATFORM_MAX_PHASE[platform] || "supervised";
  const targetIdx = PHASES.indexOf(phase);
  const maxIdx = PHASES.indexOf(maxPhase);

  if (targetIdx > maxIdx) {
    throw new Error(`${platform} capped at ${maxPhase}, cannot set to ${phase}`);
  }

  const state = await getState();
  if (!state[platform]) state[platform] = {};
  state[platform].current = phase;
  state[platform].consecutive_correct = 0;
  state[platform].consecutive_clean = 0;
  state[platform].consecutive_approved = 0;

  await setState(state);
  return state[platform];
}

// ─── Daily Rate Limits ────────────────────────────────

const DAILY_KEY = "daily_limits";

export async function checkDailyLimit(platform, maxApply) {
  const result = await chrome.storage.local.get(DAILY_KEY);
  const limits = result[DAILY_KEY] || {};
  const today = new Date().toISOString().slice(0, 10);

  if (!limits[platform] || limits[platform].date !== today) {
    return { allowed: true, used: 0, max: maxApply };
  }
  const used = limits[platform].applied || 0;
  return { allowed: used < maxApply, used, max: maxApply };
}

export async function incrementDailyApply(platform) {
  const result = await chrome.storage.local.get(DAILY_KEY);
  const limits = result[DAILY_KEY] || {};
  const today = new Date().toISOString().slice(0, 10);

  if (!limits[platform] || limits[platform].date !== today) {
    limits[platform] = { date: today, applied: 0, scanned: 0 };
  }
  limits[platform].applied++;

  await chrome.storage.local.set({ [DAILY_KEY]: limits });
}

export async function incrementDailyScan(platform, count = 1) {
  const result = await chrome.storage.local.get(DAILY_KEY);
  const limits = result[DAILY_KEY] || {};
  const today = new Date().toISOString().slice(0, 10);

  if (!limits[platform] || limits[platform].date !== today) {
    limits[platform] = { date: today, applied: 0, scanned: 0 };
  }
  limits[platform].scanned += count;

  await chrome.storage.local.set({ [DAILY_KEY]: limits });
}

export async function getDailyStats() {
  const result = await chrome.storage.local.get(DAILY_KEY);
  return result[DAILY_KEY] || {};
}
