/**
 * scanner.js — Per-platform job scan logic for Chrome MV3 extension
 *
 * Orchestrates job scanning across platforms:
 *   - Reed / LinkedIn: Python backend (API-based, zero browser risk)
 *   - Indeed / Greenhouse: Extension-internal (fetch + tab injection)
 *
 * Scan lock: chrome.storage.local key "scan_lock"
 *   { locked: true, platform, since: <timestamp> }
 *   Auto-expires after 10 minutes.
 */

import {
  SEARCH_TITLES,
  SEARCH_FILTERS,
  SCAN_SCHEDULE,
  SCAN_RATE_LIMITS,
  shouldOpenJob,
} from './config.js';

import { callBackend, notifyTelegram } from './native_bridge.js';

import {
  addJob,
  isDuplicate,
  saveScanCheckpoint,
  getScanCheckpoint,
} from './job_queue.js';

// ─── Constants ────────────────────────────────────────────────────────────────

const LOG = '[JobPulse Scanner]';

/** Scan lock auto-expires after 10 minutes (ms). */
const SCAN_LOCK_TTL_MS = 10 * 60 * 1000;

/** Alarm name prefix — alarm names are "<prefix>:<platform>". */
const ALARM_PREFIX = 'jobpulse_scan';

/** Indeed rate-limit: 2–5s randomised delay between fetches, max 40/day. */
const INDEED_MIN_DELAY_MS = 2000;
const INDEED_MAX_DELAY_MS = 5000;

/** Greenhouse: max jobs viewed per day. */
const GREENHOUSE_MAX_VIEWS = 60;

// ─── Scan Lock ────────────────────────────────────────────────────────────────

/**
 * Acquire the scan lock for a platform.
 * Returns true if acquired, false if already locked by another scan.
 * Expired locks (> SCAN_LOCK_TTL_MS old) are automatically cleared.
 *
 * @param {string} platform
 * @returns {Promise<boolean>}
 */
async function acquireScanLock(platform) {
  return new Promise((resolve) => {
    chrome.storage.local.get('scan_lock', ({ scan_lock }) => {
      if (scan_lock && scan_lock.locked) {
        const age = Date.now() - scan_lock.since;
        if (age < SCAN_LOCK_TTL_MS) {
          console.warn(
            `${LOG} Lock held by "${scan_lock.platform}" for ${Math.round(age / 1000)}s — skipping ${platform}`
          );
          resolve(false);
          return;
        }
        // Stale lock — clear it
        console.warn(`${LOG} Stale lock for "${scan_lock.platform}" expired — clearing`);
      }

      chrome.storage.local.set(
        { scan_lock: { locked: true, platform, since: Date.now() } },
        () => resolve(true)
      );
    });
  });
}

/**
 * Release the scan lock.
 * @returns {Promise<void>}
 */
async function releaseScanLock() {
  return new Promise((resolve) => {
    chrome.storage.local.set({ scan_lock: { locked: false } }, resolve);
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Pause for a random duration between min and max milliseconds.
 * @param {number} minMs
 * @param {number} maxMs
 * @returns {Promise<void>}
 */
function randomDelay(minMs, maxMs) {
  const ms = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Filter an array of job objects by title using shouldOpenJob().
 * @param {Object[]} jobs
 * @returns {Object[]}
 */
function titleFilter(jobs) {
  return jobs.filter((job) => {
    if (!shouldOpenJob(job.title)) {
      console.log(`${LOG} Title filtered: "${job.title}"`);
      return false;
    }
    return true;
  });
}

// ─── Platform Scanners ────────────────────────────────────────────────────────

/**
 * scanReed — Call Python backend for Reed jobs (official API, zero risk).
 * Title-filters results locally before returning.
 *
 * @param {string} keywords
 * @param {string} location
 * @returns {Promise<Object[]>} Filtered job objects
 */
export async function scanReed(keywords, location) {
  console.log(`${LOG} Starting Reed scan: keywords="${keywords}" location="${location}"`);

  let jobs = [];
  try {
    const result = await callBackend('scan-reed', { keywords, location });
    jobs = Array.isArray(result?.jobs) ? result.jobs : [];
    console.log(`${LOG} Reed backend returned ${jobs.length} jobs`);
  } catch (err) {
    console.error(`${LOG} Reed backend error:`, err);
    return [];
  }

  const filtered = titleFilter(jobs);
  console.log(`${LOG} Reed after title filter: ${filtered.length}/${jobs.length}`);
  return filtered;
}

/**
 * scanLinkedIn — Call Python backend for LinkedIn jobs (guest API, zero risk).
 * Title-filters results locally before returning.
 *
 * @param {string} keywords
 * @param {string} location
 * @returns {Promise<Object[]>} Filtered job objects
 */
export async function scanLinkedIn(keywords, location) {
  console.log(`${LOG} Starting LinkedIn scan: keywords="${keywords}" location="${location}"`);

  let jobs = [];
  try {
    const result = await callBackend('scan-linkedin', { keywords, location });
    jobs = Array.isArray(result?.jobs) ? result.jobs : [];
    console.log(`${LOG} LinkedIn backend returned ${jobs.length} jobs`);
  } catch (err) {
    console.error(`${LOG} LinkedIn backend error:`, err);
    return [];
  }

  const filtered = titleFilter(jobs);
  console.log(`${LOG} LinkedIn after title filter: ${filtered.length}/${jobs.length}`);
  return filtered;
}

/**
 * scanIndeed — Fetch jobs directly from Indeed's internal search using session
 * cookies. Runs in the service worker via fetch(). Parses server-rendered HTML
 * for job cards. Rate-limited to 2–5s between fetches, max 40/day.
 *
 * @returns {Promise<Object[]>} Jobs with jd_text populated
 */
export async function scanIndeed() {
  console.log(`${LOG} Starting Indeed scan`);

  const location = SEARCH_FILTERS.location[0] || 'United Kingdom';
  const maxResults = SCAN_RATE_LIMITS.indeed?.scan ?? 40;

  const allJobs = [];
  let fetchCount = 0;

  for (const title of SEARCH_TITLES) {
    if (fetchCount >= maxResults) {
      console.log(`${LOG} Indeed daily fetch limit (${maxResults}) reached — stopping`);
      break;
    }

    const query = encodeURIComponent(title);
    const url = `https://uk.indeed.com/jobs?q=${query}&l=United+Kingdom&fromage=1&sort=date`;

    console.log(`${LOG} Indeed fetching: ${url}`);

    let html;
    try {
      const response = await fetch(url, {
        credentials: 'include', // Send session cookies
        headers: {
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-GB,en;q=0.9',
          'Cache-Control': 'no-cache',
        },
      });
      fetchCount++;

      if (!response.ok) {
        console.warn(`${LOG} Indeed HTTP ${response.status} for "${title}" — skipping`);
        await randomDelay(INDEED_MIN_DELAY_MS, INDEED_MAX_DELAY_MS);
        continue;
      }

      html = await response.text();
    } catch (err) {
      console.error(`${LOG} Indeed fetch error for "${title}":`, err);
      await randomDelay(INDEED_MIN_DELAY_MS, INDEED_MAX_DELAY_MS);
      continue;
    }

    // Parse job cards from server-rendered HTML
    const cards = parseIndeedJobCards(html, title);
    console.log(`${LOG} Indeed parsed ${cards.length} cards for "${title}"`);

    for (const card of cards) {
      if (!shouldOpenJob(card.title)) {
        console.log(`${LOG} Indeed title filtered: "${card.title}"`);
        continue;
      }

      if (fetchCount >= maxResults) break;

      // Fetch full JD text for matching cards
      if (card.url) {
        try {
          await randomDelay(INDEED_MIN_DELAY_MS, INDEED_MAX_DELAY_MS);
          fetchCount++;

          const jdResponse = await fetch(card.url, {
            credentials: 'include',
            headers: { 'Accept': 'text/html', 'Accept-Language': 'en-GB,en;q=0.9' },
          });

          if (jdResponse.ok) {
            const jdHtml = await jdResponse.text();
            card.jd_text = extractIndeedJdText(jdHtml);
          }
        } catch (jdErr) {
          console.warn(`${LOG} Indeed JD fetch failed for "${card.title}":`, jdErr);
          // Non-fatal: continue without JD text
        }
      }

      allJobs.push(card);
    }

    // Rate-limit between title queries
    if (fetchCount < maxResults) {
      await randomDelay(INDEED_MIN_DELAY_MS, INDEED_MAX_DELAY_MS);
    }
  }

  console.log(`${LOG} Indeed scan complete: ${allJobs.length} jobs (${fetchCount} fetches)`);
  return allJobs;
}

/**
 * Parse Indeed's server-rendered HTML for job card data.
 * Extracts title, company, location, url, job_id from the card elements.
 *
 * @param {string} html - Raw HTML from Indeed search results page
 * @param {string} queryTitle - The search title used (for fallback platform labelling)
 * @returns {Object[]} Array of partial job objects
 */
function parseIndeedJobCards(html, queryTitle) {
  const jobs = [];

  // Indeed embeds job data in JSON inside <script id="mosaic-data"> or
  // server-renders cards with data-jk attributes. Use regex to extract
  // structured data from the page without a DOM parser in the service worker.

  // Pattern 1: data-jk="<jobKey>" — present on <a> and <div> card elements
  const jobKeyPattern = /data-jk="([^"]+)"/g;
  const titlePattern = /data-jk="([^"]+)"[^>]*>[\s\S]*?class="[^"]*jobTitle[^"]*"[^>]*><[^>]+>([^<]+)<\//g;

  // Extract via JSON blob if present (more reliable)
  const jsonBlobMatch = html.match(/<script[^>]*id="mosaic-data"[^>]*>([\s\S]*?)<\/script>/);
  if (jsonBlobMatch) {
    try {
      const blob = JSON.parse(jsonBlobMatch[1]);
      const results =
        blob?.mosaic?.mosaicProviderJobCardsModel?.results ||
        blob?.metaData?.mosaicProviderJobCardsModel?.results ||
        [];

      for (const r of results) {
        const jobKey = r.jobkey || r.jobKey || '';
        const jobTitle = r.title || r.jobTitle || '';
        if (!jobKey || !jobTitle) continue;

        jobs.push({
          platform: 'indeed',
          job_id: jobKey,
          title: jobTitle,
          company: r.company || r.companyName || '',
          location: r.formattedLocation || r.jobLocationCity || '',
          url: `https://uk.indeed.com/viewjob?jk=${jobKey}`,
          salary: r.salarySnippet?.text || '',
          posted_at: r.pubDate || null,
          jd_text: '',
          scraped_at: Date.now(),
          apply_status: 'pending',
        });
      }
      return jobs;
    } catch (_) {
      // Fall through to regex fallback
    }
  }

  // Regex fallback: extract job keys and build stub records
  const seenKeys = new Set();
  let match;
  // eslint-disable-next-line no-cond-assign
  while ((match = jobKeyPattern.exec(html)) !== null) {
    const jobKey = match[1];
    if (seenKeys.has(jobKey)) continue;
    seenKeys.add(jobKey);

    jobs.push({
      platform: 'indeed',
      job_id: jobKey,
      title: queryTitle, // Will be confirmed from JD page
      company: '',
      location: '',
      url: `https://uk.indeed.com/viewjob?jk=${jobKey}`,
      salary: '',
      posted_at: null,
      jd_text: '',
      scraped_at: Date.now(),
      apply_status: 'pending',
    });
  }

  return jobs;
}

/**
 * Extract plain-text JD content from an Indeed job detail page.
 * Targets the #jobDescriptionText div (server-rendered).
 *
 * @param {string} html - Raw HTML of the job detail page
 * @returns {string} Plain text of the JD, or empty string
 */
function extractIndeedJdText(html) {
  const match = html.match(/<div[^>]+id="jobDescriptionText"[^>]*>([\s\S]*?)<\/div>/);
  if (!match) return '';

  // Strip tags and decode basic HTML entities
  return match[1]
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

/**
 * scanGreenhouse — Open each company's Greenhouse careers page in a background
 * tab, inject content script to extract job listings, then close the tab.
 * Title-filtered. Respects max 60 jobs/day viewed.
 *
 * @param {string[]} companyUrls - Array of Greenhouse careers page URLs
 * @returns {Promise<Object[]>} Filtered job objects
 */
export async function scanGreenhouse(companyUrls) {
  console.log(`${LOG} Starting Greenhouse scan: ${companyUrls.length} companies`);

  const allJobs = [];
  let viewedCount = 0;

  for (const careerUrl of companyUrls) {
    if (viewedCount >= GREENHOUSE_MAX_VIEWS) {
      console.log(`${LOG} Greenhouse daily view limit (${GREENHOUSE_MAX_VIEWS}) reached — stopping`);
      break;
    }

    let tab;
    try {
      // Open background tab
      tab = await chrome.tabs.create({ url: careerUrl, active: false });
      console.log(`${LOG} Greenhouse opened tab ${tab.id} for ${careerUrl}`);

      // Wait for page load
      await waitForTabLoad(tab.id);

      // Inject extraction script
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: extractGreenhouseJobs,
      });

      const rawJobs = Array.isArray(result?.result) ? result.result : [];
      console.log(`${LOG} Greenhouse extracted ${rawJobs.length} jobs from ${careerUrl}`);

      for (const job of rawJobs) {
        if (viewedCount >= GREENHOUSE_MAX_VIEWS) break;

        if (!shouldOpenJob(job.title)) {
          console.log(`${LOG} Greenhouse title filtered: "${job.title}"`);
          continue;
        }

        allJobs.push({
          ...job,
          platform: 'greenhouse',
          scraped_at: Date.now(),
          apply_status: 'pending',
        });
        viewedCount++;
      }
    } catch (err) {
      console.error(`${LOG} Greenhouse error for ${careerUrl}:`, err);
      // Non-fatal: continue with next company
    } finally {
      if (tab?.id) {
        try {
          await chrome.tabs.remove(tab.id);
        } catch (_) {
          // Tab may already be closed
        }
      }
    }
  }

  console.log(`${LOG} Greenhouse scan complete: ${allJobs.length} jobs (${viewedCount} viewed)`);
  return allJobs;
}

/**
 * Content script function injected into Greenhouse career pages.
 * Extracts job listings from the standard Greenhouse jobs board DOM.
 * Runs in the page context (no imports available).
 *
 * @returns {Object[]} Array of job objects
 */
function extractGreenhouseJobs() {
  const jobs = [];

  // Greenhouse hosted jobs board: <div class="opening"> per job
  const openings = document.querySelectorAll('.opening');
  openings.forEach((opening) => {
    const anchor = opening.querySelector('a');
    if (!anchor) return;

    const title = anchor.textContent.trim();
    const relUrl = anchor.getAttribute('href') || '';
    const url = relUrl.startsWith('http') ? relUrl : `${location.origin}${relUrl}`;
    const department = opening.closest('.department')?.querySelector('h3')?.textContent.trim() || '';
    const locationEl = opening.querySelector('.location');
    const jobLocation = locationEl ? locationEl.textContent.trim() : '';

    jobs.push({ title, url, company: document.title, location: jobLocation, department });
  });

  // Fallback: Greenhouse JSON embed (some pages render a <script> with job data)
  if (jobs.length === 0) {
    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
    scripts.forEach((script) => {
      try {
        const data = JSON.parse(script.textContent);
        const listings = Array.isArray(data) ? data : [data];
        listings.forEach((item) => {
          if (item['@type'] === 'JobPosting') {
            jobs.push({
              title: item.title || '',
              url: item.url || location.href,
              company: item.hiringOrganization?.name || document.title,
              location: item.jobLocation?.address?.addressLocality || '',
              department: '',
            });
          }
        });
      } catch (_) {
        // Not valid JSON — skip
      }
    });
  }

  return jobs;
}

/**
 * Wait for a tab to finish loading (status === "complete").
 * Resolves after load or after 15s timeout.
 *
 * @param {number} tabId
 * @returns {Promise<void>}
 */
function waitForTabLoad(tabId) {
  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, 15_000);

    function listener(updatedTabId, changeInfo) {
      if (updatedTabId === tabId && changeInfo.status === 'complete') {
        clearTimeout(timeout);
        chrome.tabs.onUpdated.removeListener(listener);
        // Let JS settle
        setTimeout(resolve, 1000);
      }
    }

    chrome.tabs.onUpdated.addListener(listener);
  });
}

// ─── Gate Evaluation ──────────────────────────────────────────────────────────

/**
 * evaluateJobs — Send jobs to Python backend for gate evaluation.
 * Attaches gate_results to each job. Jobs that error individually are
 * returned with gate_results = null (one failure does not stop the batch).
 *
 * @param {Object[]} jobs
 * @returns {Promise<Object[]>} Jobs with gate_results attached
 */
export async function evaluateJobs(jobs) {
  if (!jobs.length) return [];

  console.log(`${LOG} Evaluating ${jobs.length} jobs via backend`);

  try {
    const result = await callBackend('evaluate-batch', { jobs });
    const evaluated = Array.isArray(result?.jobs) ? result.jobs : [];

    // Merge gate_results back by index — backend returns same order
    return jobs.map((job, idx) => ({
      ...job,
      gate_results: evaluated[idx]?.gate_results ?? null,
    }));
  } catch (err) {
    console.error(`${LOG} evaluateJobs backend error:`, err);
    // Return jobs with null gate_results rather than failing the scan
    return jobs.map((job) => ({ ...job, gate_results: null }));
  }
}

// ─── Main Scan Entry Points ───────────────────────────────────────────────────

/**
 * scanPlatform — Dispatch to the platform-specific scanner.
 * Returns an array of job objects (filtered by title, not yet gate-evaluated).
 *
 * @param {string} platform - "reed" | "linkedin" | "indeed" | "greenhouse"
 * @returns {Promise<Object[]>}
 */
export async function scanPlatform(platform) {
  const keywords = SEARCH_TITLES.join(' OR ');
  const location = SEARCH_FILTERS.location[0] || 'United Kingdom';

  switch (platform) {
    case 'reed':
      return scanReed(keywords, location);

    case 'linkedin':
      return scanLinkedIn(keywords, location);

    case 'indeed':
      return scanIndeed();

    case 'greenhouse': {
      // Greenhouse URLs are expected to come from chrome.storage or config;
      // fall back to empty list if none configured.
      const stored = await new Promise((resolve) =>
        chrome.storage.local.get('greenhouse_urls', ({ greenhouse_urls }) =>
          resolve(Array.isArray(greenhouse_urls) ? greenhouse_urls : [])
        )
      );
      return scanGreenhouse(stored);
    }

    default:
      console.warn(`${LOG} Unknown platform: ${platform}`);
      return [];
  }
}

/**
 * runScanCycle — Full scan lifecycle for a single platform:
 *   1. Acquire scan lock
 *   2. Check network availability
 *   3. Scan platform (title-filtered)
 *   4. Deduplicate against IndexedDB
 *   5. Evaluate gates via backend
 *   6. Store passing jobs in IndexedDB
 *   7. Save scan checkpoint
 *   8. Send Telegram notification
 *   9. Release scan lock
 *
 * @param {string} platform
 * @returns {Promise<{scanned: number, new: number, stored: number, errors: number}>}
 */
export async function runScanCycle(platform) {
  const stats = { scanned: 0, new: 0, stored: 0, errors: 0 };

  // Network guard
  if (!navigator.onLine) {
    console.warn(`${LOG} Offline — skipping scan cycle for ${platform}`);
    return stats;
  }

  // Scan lock guard
  const locked = await acquireScanLock(platform);
  if (!locked) return stats;

  console.log(`${LOG} Starting scan cycle: platform=${platform}`);

  try {
    // 1. Scan
    const rawJobs = await scanPlatform(platform);
    stats.scanned = rawJobs.length;

    // 2. Deduplicate
    const freshJobs = [];
    for (const job of rawJobs) {
      try {
        const dup = await isDuplicate(job.url);
        if (!dup) freshJobs.push(job);
      } catch (err) {
        console.warn(`${LOG} Dedup check failed for "${job.title}":`, err);
        freshJobs.push(job); // Include on error — addJob() will handle duplicates
      }
    }
    stats.new = freshJobs.length;
    console.log(`${LOG} After dedup: ${freshJobs.length}/${rawJobs.length} new jobs`);

    if (!freshJobs.length) {
      await saveScanCheckpoint(platform, { lastScan: Date.now(), found: 0 });
      return stats;
    }

    // 3. Gate evaluation
    const evaluatedJobs = await evaluateJobs(freshJobs);

    // 4. Store in IndexedDB (process each job independently — one error won't halt the rest)
    for (const job of evaluatedJobs) {
      try {
        await addJob(job);
        stats.stored++;
      } catch (err) {
        console.error(`${LOG} Failed to store job "${job.title}":`, err);
        stats.errors++;
      }
    }

    // 5. Checkpoint
    await saveScanCheckpoint(platform, {
      lastScan: Date.now(),
      found: stats.scanned,
      stored: stats.stored,
    });

    // 6. Telegram notification
    const summary =
      `[${platform}] Scan complete: ${stats.scanned} found, ` +
      `${stats.new} new, ${stats.stored} stored, ${stats.errors} errors`;
    console.log(`${LOG} ${summary}`);

    try {
      await notifyTelegram(summary);
    } catch (notifyErr) {
      console.warn(`${LOG} Telegram notify failed:`, notifyErr);
      // Non-fatal
    }
  } catch (err) {
    console.error(`${LOG} Scan cycle error for ${platform}:`, err);
    stats.errors++;
  } finally {
    await releaseScanLock();
  }

  return stats;
}

// ─── Chrome Alarms ────────────────────────────────────────────────────────────

/**
 * registerScanAlarms — Register Chrome Alarms for all platforms based on
 * SCAN_SCHEDULE from config.js. Clears any existing alarms with the same
 * prefix first to prevent duplicates. Call once at extension startup.
 *
 * Alarm names: "jobpulse_scan:<platform>:<HH:MM>"
 *
 * @returns {Promise<void>}
 */
export async function registerScanAlarms() {
  // Clear all existing jobpulse scan alarms
  const existing = await chrome.alarms.getAll();
  for (const alarm of existing) {
    if (alarm.name.startsWith(ALARM_PREFIX)) {
      await chrome.alarms.clear(alarm.name);
    }
  }

  const now = new Date();

  for (const [platform, times] of Object.entries(SCAN_SCHEDULE)) {
    for (const timeStr of times) {
      const [hh, mm] = timeStr.split(':').map(Number);
      const alarmName = `${ALARM_PREFIX}:${platform}:${timeStr}`;

      // Calculate next fire time
      const next = new Date(now);
      next.setHours(hh, mm, 0, 0);
      if (next <= now) {
        // Time already passed today — schedule for tomorrow
        next.setDate(next.getDate() + 1);
      }

      const delayInMinutes = (next.getTime() - now.getTime()) / 60_000;

      chrome.alarms.create(alarmName, {
        delayInMinutes,
        periodInMinutes: 24 * 60, // Repeat daily
      });

      console.log(
        `${LOG} Alarm registered: ${alarmName} fires in ${Math.round(delayInMinutes)} min`
      );
    }
  }
}

/**
 * handleScanAlarm — Called when a Chrome Alarm fires.
 * Parses platform from alarm name, runs runScanCycle().
 * Handles errors and logs the outcome.
 *
 * @param {{ name: string }} alarm - Chrome Alarm object
 * @returns {Promise<void>}
 */
export async function handleScanAlarm(alarm) {
  if (!alarm.name.startsWith(ALARM_PREFIX)) return;

  // Alarm name format: "jobpulse_scan:<platform>:<HH:MM>"
  const parts = alarm.name.split(':');
  if (parts.length < 3) {
    console.warn(`${LOG} Malformed alarm name: ${alarm.name}`);
    return;
  }

  const platform = parts[1];
  console.log(`${LOG} Alarm fired: ${alarm.name} — running scan for ${platform}`);

  try {
    const stats = await runScanCycle(platform);
    console.log(
      `${LOG} Alarm scan done [${platform}]: ` +
        `scanned=${stats.scanned} new=${stats.new} stored=${stats.stored} errors=${stats.errors}`
    );
  } catch (err) {
    console.error(`${LOG} Alarm scan failed [${platform}]:`, err);
    // Release any lingering lock if runScanCycle threw before finally
    await releaseScanLock();
  }
}
