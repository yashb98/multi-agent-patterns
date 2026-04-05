// extension/scanner.js — Per-platform scan orchestration

import { callBackend } from "./native_bridge.js";
import {
  SEARCH_TITLES, SEARCH_FILTERS, SCAN_RATE_LIMITS,
  shouldOpenJob,
} from "./config.js";
import {
  addJob, isDuplicate, makeJobId, createJobEntry,
  saveCheckpoint, getCheckpoint, clearCheckpoint,
} from "./job_queue.js";
import { incrementDailyScan } from "./phase_engine.js";

// ─── Reed (Python API — no browser) ──────────────────

export async function scanReed() {
  const resp = await callBackend("scan-reed", {
    titles: SEARCH_TITLES,
    location: SEARCH_FILTERS.location[0],
  });

  let added = 0;
  for (const raw of (resp.jobs || [])) {
    if (!shouldOpenJob(raw.title || "")) continue;
    const id = await makeJobId(raw.url);
    if (await isDuplicate(raw.url, raw.company, raw.title)) continue;

    // Evaluate through gates
    const gateResult = await callBackend("evaluate", {
      url: raw.url,
      title: raw.title,
      company: raw.company || "",
      platform: "reed",
      jd_text: raw.description || "",
    });

    await addJob(createJobEntry({
      id,
      url: raw.url,
      title: raw.title,
      company: raw.company || "",
      platform: "reed",
      jd_text: raw.description || "",
      gate_results: gateResult,
      apply_status: gateResult.passed ? "pending" : "rejected",
    }));
    added++;
  }

  await incrementDailyScan("reed", resp.jobs?.length || 0);
  return { platform: "reed", scanned: resp.jobs?.length || 0, passed: added };
}

// ─── LinkedIn (Python guest API — no browser) ────────

export async function scanLinkedIn() {
  const resp = await callBackend("scan-linkedin", {
    titles: SEARCH_TITLES,
    location: SEARCH_FILTERS.location[0],
  });

  let added = 0;
  for (const raw of (resp.jobs || [])) {
    if (!shouldOpenJob(raw.title || "")) continue;
    const id = await makeJobId(raw.url);
    if (await isDuplicate(raw.url, raw.company, raw.title)) continue;

    const gateResult = await callBackend("evaluate", {
      url: raw.url,
      title: raw.title,
      company: raw.company || "",
      platform: "linkedin",
      jd_text: raw.description || "",
    });

    await addJob(createJobEntry({
      id,
      url: raw.url,
      title: raw.title,
      company: raw.company || "",
      platform: "linkedin",
      jd_text: raw.description || "",
      gate_results: gateResult,
      apply_status: gateResult.passed ? "pending" : "rejected",
    }));
    added++;
  }

  await incrementDailyScan("linkedin", resp.jobs?.length || 0);
  return { platform: "linkedin", scanned: resp.jobs?.length || 0, passed: added };
}

// ─── Indeed (Extension internal API) ──────────────────

export async function scanIndeed() {
  const results = [];
  const rateLimit = SCAN_RATE_LIMITS.indeed;

  for (const title of SEARCH_TITLES) {
    if (results.length >= rateLimit.max_jobs) break;

    const searchUrl = `https://uk.indeed.com/jobs?q=${encodeURIComponent(title)}&l=United+Kingdom&fromage=1&explvl=entry_level&sort=date`;

    try {
      const resp = await fetch(searchUrl, { credentials: "include" });
      if (!resp.ok) {
        if (resp.status === 403) {
          // Cloudflare challenge — stop immediately
          return { platform: "indeed", scanned: results.length, passed: 0, error: "cloudflare_challenge" };
        }
        continue;
      }

      const html = await resp.text();
      const parser = new DOMParser();
      const doc = parser.parseFromString(html, "text/html");

      const cards = doc.querySelectorAll(".job_seen_beacon, .jobsearch-ResultsList .result");
      for (const card of cards) {
        if (results.length >= rateLimit.max_jobs) break;

        const titleEl = card.querySelector("h2 a, .jobTitle a");
        const companyEl = card.querySelector("[data-testid='company-name'], .companyName");
        const linkEl = card.querySelector("h2 a, .jobTitle a");

        const jobTitle = titleEl?.textContent?.trim() || "";
        const company = companyEl?.textContent?.trim() || "";
        const href = linkEl?.getAttribute("href") || "";
        const jobUrl = href.startsWith("http") ? href : `https://uk.indeed.com${href}`;

        if (!shouldOpenJob(jobTitle)) continue;
        if (await isDuplicate(jobUrl, company, jobTitle)) continue;

        results.push({ title: jobTitle, company, url: jobUrl, platform: "indeed" });

        // Rate limiting: 3-5s between fetches
        await new Promise(r => setTimeout(r, 3000 + Math.random() * 2000));
      }
    } catch (e) {
      console.error(`scanIndeed: error on "${title}":`, e.message);
    }
  }

  // Evaluate each through gates (need full JD for this — fetch detail pages)
  let added = 0;
  for (const job of results) {
    try {
      // Fetch full JD from detail page
      const detailResp = await fetch(job.url, { credentials: "include" });
      if (!detailResp.ok) continue;
      const detailHtml = await detailResp.text();
      const detailDoc = new DOMParser().parseFromString(detailHtml, "text/html");
      const jdEl = detailDoc.querySelector("#jobDescriptionText, .jobsearch-jobDescriptionText");
      const jdText = jdEl?.textContent?.trim() || "";

      if (!jdText || jdText.length < 100) continue;

      const id = await makeJobId(job.url);
      const gateResult = await callBackend("evaluate", {
        url: job.url,
        title: job.title,
        company: job.company,
        platform: "indeed",
        jd_text: jdText,
      });

      await addJob(createJobEntry({
        id,
        url: job.url,
        title: job.title,
        company: job.company,
        platform: "indeed",
        jd_text: jdText,
        gate_results: gateResult,
        apply_status: gateResult.passed ? "pending" : "rejected",
      }));
      if (gateResult.passed) added++;

      await new Promise(r => setTimeout(r, 2000 + Math.random() * 3000));
    } catch (e) {
      console.error(`scanIndeed: detail error for ${job.url}:`, e.message);
    }
  }

  await incrementDailyScan("indeed", results.length);
  return { platform: "indeed", scanned: results.length, passed: added };
}

// ─── Scan Orchestrator ────────────────────────────────

const SCAN_LOCK_KEY = "scan_lock";

async function acquireLock() {
  const result = await chrome.storage.local.get(SCAN_LOCK_KEY);
  const lock = result[SCAN_LOCK_KEY];
  if (lock?.locked && Date.now() - lock.since < 600000) {
    return false; // locked within last 10 min
  }
  await chrome.storage.local.set({
    [SCAN_LOCK_KEY]: { locked: true, since: Date.now() },
  });
  return true;
}

async function releaseLock() {
  await chrome.storage.local.set({
    [SCAN_LOCK_KEY]: { locked: false, since: null },
  });
}

/**
 * Run a scan for a specific platform.
 * @param {string} platform
 * @returns {Promise<{platform, scanned, passed, error?}>}
 */
export async function runScan(platform) {
  if (!(await acquireLock())) {
    return { platform, scanned: 0, passed: 0, error: "scan_locked" };
  }

  try {
    switch (platform) {
      case "reed":      return await scanReed();
      case "linkedin":  return await scanLinkedIn();
      case "indeed":    return await scanIndeed();
      default:
        return { platform, scanned: 0, passed: 0, error: "unknown_platform" };
    }
  } finally {
    await releaseLock();
  }
}

/**
 * Run scans for all platforms.
 */
export async function runAllScans() {
  const results = [];
  for (const platform of ["reed", "linkedin", "indeed"]) {
    const result = await runScan(platform);
    results.push(result);
    // 30s gap between platform scans
    await new Promise(r => setTimeout(r, 30000));
  }
  return results;
}
