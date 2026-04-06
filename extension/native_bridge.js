// extension/native_bridge.js — HTTP API wrapper + Native Messaging bootstrap
//
// All communication with the Python backend goes through this module.
// Native Messaging is used only to bootstrap the FastAPI process when it
// isn't running — all job operations use plain HTTP fetch to localhost:8000.
//
// Usage:
//   import { callBackend, evaluateJob, generateCV, ... } from "./native_bridge.js";

// ═══════════════════════════════════════════════════════════════
// Config
// ═══════════════════════════════════════════════════════════════

const BASE_URL = "http://localhost:8000/api/job";
const NATIVE_HOST_ID = "com.jobpulse.brain";
const DEFAULT_TIMEOUT_MS = 30_000;
const CV_TIMEOUT_MS = 120_000;
const BACKEND_CACHE_TTL_MS = 30_000;

// ═══════════════════════════════════════════════════════════════
// Backend readiness cache — prevents re-bootstrapping on every call
// ═══════════════════════════════════════════════════════════════

let _backendReadyAt = 0; // epoch ms, 0 = not cached

function _isCacheValid() {
  return Date.now() - _backendReadyAt < BACKEND_CACHE_TTL_MS;
}

function _markBackendReady() {
  _backendReadyAt = Date.now();
}

// ═══════════════════════════════════════════════════════════════
// Health check
// ═══════════════════════════════════════════════════════════════

/**
 * GET /api/job/health — returns true if the backend is up and responding.
 * Never throws; any failure (network error, non-2xx) returns false.
 */
export async function isBackendHealthy() {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);
    const res = await fetch(`${BASE_URL}/health`, { signal: controller.signal });
    clearTimeout(timer);
    return res.ok;
  } catch {
    return false;
  }
}

// ═══════════════════════════════════════════════════════════════
// Native Messaging bootstrap
// ═══════════════════════════════════════════════════════════════

/**
 * Send {action: "ensure_running"} to the native host and wait for
 * {status: "ready"}.  Result is cached for 30s so the first call pays
 * the bootstrap cost and subsequent calls are instant.
 */
export async function ensureBackendRunning() {
  if (_isCacheValid()) return;

  console.log("[JobPulse] Bootstrapping backend via Native Messaging...");

  await new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(
      NATIVE_HOST_ID,
      { action: "ensure_running" },
      (response) => {
        if (chrome.runtime.lastError) {
          const msg = chrome.runtime.lastError.message;
          console.error("[JobPulse] Native Messaging error:", msg);
          reject(new Error(`Native Messaging failed: ${msg}`));
          return;
        }
        if (!response) {
          reject(new Error("Native host returned no response"));
          return;
        }
        if (response.status === "ready") {
          console.log("[JobPulse] Backend ready on port", response.port ?? 8000);
          _markBackendReady();
          resolve();
        } else {
          const err = response.message ?? "Unknown error from native host";
          console.error("[JobPulse] Native host error:", err);
          reject(new Error(err));
        }
      }
    );
  });
}

// ═══════════════════════════════════════════════════════════════
// Core HTTP caller
// ═══════════════════════════════════════════════════════════════

/**
 * POST to http://localhost:8000/api/job/<endpoint> with a JSON body.
 *
 * On network failure, calls ensureBackendRunning() once and retries.
 * Throws on any non-2xx response or if the retry also fails.
 *
 * @param {string} endpoint  — path segment, e.g. "evaluate"
 * @param {object} data      — request body (JSON-serialisable)
 * @param {number} timeoutMs — request timeout in ms (default 30s)
 * @returns {Promise<any>}   — parsed JSON response body
 */
export async function callBackend(endpoint, data = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const url = `${BASE_URL}/${endpoint}`;

  const doFetch = async () => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        signal: controller.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`HTTP ${res.status} from ${endpoint}: ${text}`);
      }
      return await res.json();
    } catch (err) {
      clearTimeout(timer);
      throw err;
    }
  };

  // First attempt
  try {
    return await doFetch();
  } catch (firstErr) {
    // Only retry on network-level failures (TypeError from fetch), not HTTP errors
    if (!(firstErr instanceof TypeError)) {
      console.error(`[JobPulse] ${endpoint} failed:`, firstErr.message);
      throw firstErr;
    }

    console.warn(`[JobPulse] Network error on ${endpoint}, bootstrapping backend...`);
    try {
      await ensureBackendRunning();
    } catch (bootstrapErr) {
      console.error("[JobPulse] Bootstrap failed:", bootstrapErr.message);
      throw bootstrapErr;
    }

    // Single retry after bootstrap
    console.log(`[JobPulse] Retrying ${endpoint}...`);
    return await doFetch();
  }
}

// ═══════════════════════════════════════════════════════════════
// High-level API helpers
// ═══════════════════════════════════════════════════════════════

/**
 * Evaluate a single job posting against the candidate profile.
 *
 * @param {string} jdText   — raw job description text
 * @param {string} title    — job title
 * @param {string} company  — company name
 * @param {string} url      — source URL
 * @param {string} platform — "linkedin" | "reed" | "indeed" | ...
 * @returns {Promise<{score: number, tier: string, matched_skills: string[], ...}>}
 */
export function evaluateJob(jdText, title, company, url, platform) {
  return callBackend("evaluate", { jd_text: jdText, title, company, url, platform });
}

/**
 * Evaluate a batch of jobs in a single call.
 *
 * @param {Array<{jd_text, title, company, url, platform}>} jobs
 * @returns {Promise<Array>}
 */
export function evaluateJobBatch(jobs) {
  return callBackend("evaluate-batch", { jobs });
}

/**
 * Generate a tailored CV PDF for a specific job.
 * Uses the extended 120s timeout — PDF generation involves LLM calls.
 *
 * @param {string}   company         — target company name
 * @param {string}   role            — job title / role
 * @param {string[]} matchedProjects — project names to highlight
 * @param {string[]} requiredSkills  — skills required by the JD
 * @param {string}   location        — job location
 * @returns {Promise<{pdf_url: string, drive_link: string, ...}>}
 */
export function generateCV(company, role, matchedProjects, requiredSkills, location) {
  return callBackend(
    "generate-cv",
    { company, role, matched_projects: matchedProjects, required_skills: requiredSkills, location },
    CV_TIMEOUT_MS
  );
}

/**
 * Trigger a Reed job scan for the given keywords and location.
 *
 * @param {string} keywords  — search query string
 * @param {string} location  — location filter (e.g. "London")
 * @returns {Promise<{jobs: Array, count: number, ...}>}
 */
export function scanReed(keywords, location) {
  return callBackend("scan-reed", { keywords, location });
}

/**
 * Trigger a LinkedIn job scan.
 *
 * @param {string} keywords  — search query string
 * @param {string} location  — location filter
 * @returns {Promise<{jobs: Array, count: number, ...}>}
 */
export function scanLinkedIn(keywords, location) {
  return callBackend("scan-linkedin", { keywords, location });
}

/**
 * Send a Telegram notification to the configured bot/channel.
 *
 * @param {string} message — plain text or Markdown message body
 * @returns {Promise<{ok: boolean, message_id: number, ...}>}
 */
export function notifyTelegram(message) {
  return callBackend("notify", { message });
}

/**
 * Record a successful Ralph Loop fix so the pattern can be replayed.
 *
 * @param {string} platform    — e.g. "linkedin", "greenhouse"
 * @param {string} fixType     — e.g. "selector_update", "field_strategy"
 * @param {object} fixPayload  — serialisable description of the fix
 * @returns {Promise<{stored: boolean, ...}>}
 */
export function ralphLearn(platform, fixType, fixPayload) {
  return callBackend("ralph-learn", { platform, fix_type: fixType, fix_payload: fixPayload });
}

/**
 * Trigger a job application via the Python backend.
 * Calls ralph_apply_sync which handles rate limiting, CV gen, and the
 * self-healing apply loop through the extension WebSocket.
 *
 * @param {string} url       — job listing URL
 * @param {string} platform  — ATS platform (greenhouse, lever, etc.)
 * @param {string} company   — company name (used for CV generation)
 * @param {string} role      — role title (used for CV generation)
 * @param {boolean} dryRun   — if true, fill forms but don't submit
 * @returns {Promise<{success: boolean, error?: string, ...}>}
 */
export function applyJob(url, platform, company = "", role = "", dryRun = false) {
  return callBackend(
    "apply",
    { url, platform, company, role, dry_run: dryRun },
    CV_TIMEOUT_MS  // 120s — apply loop can take a while
  );
}
