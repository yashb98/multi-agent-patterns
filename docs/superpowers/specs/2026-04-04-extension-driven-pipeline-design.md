# Extension-Driven Job Pipeline — Design Spec

**Date:** 2026-04-04
**Status:** Draft
**Replaces:** Playwright-based scanning + WebSocket bridge architecture

## Summary

Redesign the entire job automation pipeline so the Chrome extension is the autonomous driver and Python is the brain-on-demand. Delete all Playwright code. Replace WebSocket with Native Messaging bootstrap + HTTP API. Implement a 4-phase trust system with data-driven graduation and automatic demotion.

## Goals

1. **Zero Playwright dependency** — delete ~1,225 lines of Playwright-only code
2. **Extension drives everything** — scanning, form filling, applying
3. **Python is on-demand** — not a server you start manually; Chrome spawns it via Native Messaging
4. **4-phase trust** — observation → dry_run → supervised → auto, with per-platform caps
5. **Risk-aware** — never risk valuable accounts (LinkedIn, Indeed) for automation convenience
6. **Edge-case hardened** — mid-form CAPTCHAs, session expiry, ghost applications, all handled

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│ Chrome Extension (AUTONOMOUS DRIVER)                  │
│                                                       │
│  Service Worker:                                      │
│    → Chrome Alarms API (scheduled scans)              │
│    → Scan orchestration (per-platform strategy)       │
│    → Apply queue management                           │
│    → Phase graduation/demotion engine                 │
│    → PSI drift detection                              │
│                                                       │
│  Content Script:                                      │
│    → JD text extraction (scan_jd command)             │
│    → Form field deep scan (existing)                  │
│    → Form filling with human-like timing (existing)   │
│    → Gemini Nano local inference (existing)           │
│    → Platform-specific DOM selectors                  │
│                                                       │
│  Side Panel:                                          │
│    → Real-time dashboard                              │
│    → Job queue with gate results                      │
│    → Phase status per platform                        │
│    → Approve/reject/edit controls                     │
│    → Scan settings (schedule, keywords, filters)      │
│    → Manual "Scan Now" trigger                        │
│                                                       │
│  IndexedDB:                                           │
│    → Job queue (JD text, gate results, screenshots)   │
│    → Field mappings from observation phase             │
│    → Dry run results                                  │
│    → Ralph Loop learned patterns                      │
│                                                       │
│  chrome.storage.local:                                │
│    → Phase config per platform                        │
│    → Graduation counters                              │
│    → PSI baselines                                    │
│    → Daily rate limit counters                        │
│    → Scan schedules                                   │
└──────────────────┬───────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │ Native Messaging    │ (bootstrap only)
        │ Auto-starts Python  │
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │ HTTP API            │ fetch("http://localhost:8000/api/...")
        │ (FastAPI on :8000)  │
        └──────────┬──────────┘
                   │
┌──────────────────▼───────────────────────────────────┐
│ Python Backend (BRAIN-ON-DEMAND)                      │
│                                                       │
│  FastAPI routes (new):                                │
│    POST /api/job/evaluate      → Gates 0-4            │
│    POST /api/job/evaluate-batch → Batch gate eval     │
│    POST /api/job/generate-cv   → ReportLab CV/CL     │
│    POST /api/job/ralph-learn   → Store fix patterns   │
│    POST /api/job/scan-reed     → Reed API scan        │
│    POST /api/job/scan-linkedin → Guest API scan       │
│    GET  /api/job/health        → Backend alive check  │
│    POST /api/job/notify        → Telegram notification│
│                                                       │
│  Existing (unchanged):                                │
│    → jd_analyzer.py (JD analysis)                     │
│    → recruiter_screen.py (Gate 0)                     │
│    → skill_graph_store.py (Gates 1-3)                 │
│    → gate4_quality.py (Gate 4)                        │
│    → ats_scorer.py (ATS scoring)                      │
│    → cv_templates/ (PDF generation)                   │
│    → ralph_loop/ (pattern learning)                   │
│    → form_intelligence.py (5-tier answer resolver)    │
│    → screening_answers.py (pattern + LLM + cache)     │
│                                                       │
│  Native Messaging host:                               │
│    → 20-line bootstrap script                         │
│    → Checks if FastAPI running on :8000               │
│    → If not, starts it as background daemon           │
│    → Returns {"status": "ready", "port": 8000}       │
└──────────────────────────────────────────────────────┘
```

---

## Communication Layer

### Native Messaging (bootstrap only)

**Purpose:** Auto-start Python backend when extension needs it. Nothing else.

**Host manifest** (`com.jobpulse.brain.json`):
```json
{
  "name": "com.jobpulse.brain",
  "description": "JobPulse Python backend bootstrap",
  "path": "/Users/yashbishnoi/projects/multi_agent_patterns/jobpulse/native_host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://<extension-id>/"]
}
```
Installed at: `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.jobpulse.brain.json`

**Bootstrap script** (`jobpulse/native_host.py`):
- Receives JSON from stdin: `{"action": "ensure_running"}`
- Checks if FastAPI alive: `GET http://localhost:8000/api/job/health`
- If not running: starts `python -m jobpulse.runner api-server` as detached subprocess
- Waits up to 10s for health check to pass
- Returns via stdout: `{"status": "ready", "port": 8000}` or `{"status": "error", "message": "..."}`

### HTTP API (all communication)

**Why HTTP over WebSocket:**
- No 1MB message size limit (screenshots can be 2-5MB)
- Concurrent requests naturally supported
- Testable with curl
- Swagger docs for free
- Already have FastAPI infrastructure on :8000
- Standard fetch() from extension — no library needed

**Extension calls Python:**
```javascript
async function callBackend(endpoint, data) {
  try {
    const resp = await fetch(`http://localhost:8000/api/job/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!resp.ok) throw new Error(`Backend ${resp.status}`);
    return await resp.json();
  } catch (e) {
    // Backend not running — bootstrap via Native Messaging
    await ensureBackendRunning();
    // Retry once
    const resp = await fetch(`http://localhost:8000/api/job/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return await resp.json();
  }
}
```

---

## Scanning Architecture

### Principle: Never risk a valuable account for scanning

| Platform | Scan method | Auth required? | Risk to account |
|---|---|---|---|
| **Reed** | Official REST API (Python) | API key only | **Zero** |
| **LinkedIn** | Public guest API (Python) | No — unauthenticated | **Zero** |
| **Indeed** | Extension internal API (fetch + session cookies) | Yes | **Low** |
| **Greenhouse** | Extension navigates public career pages | No | **Zero** |
| **Lever** | Extension navigates public career pages | No | **Zero** |
| **Glassdoor** | Extension internal API (fetch + session cookies) | Yes | **Low** |

### LinkedIn: Two completely separated paths

```
SCANNING (zero risk):
  Extension → POST /api/job/scan-linkedin
  Python → httpx → linkedin.com/jobs-guest/jobs/api/...
  → No cookies, no session, no login
  → Returns: title, company, URL, truncated description
  → NEVER touches li_at cookie

APPLYING (controlled risk, supervised only):
  Extension → your real Chrome session
  → Only when YOU tap "Approve" in side panel
  → Max 10/day (well under 50 hard cap)
```

### Scan Triggers

**Scheduled:** Chrome Alarms API fires per-platform on configured schedule:
```javascript
const SCAN_SCHEDULE = {
  reed:       { times: ["09:00", "14:00"], days: "all week" },
  linkedin:   { times: ["10:00", "16:00"], days: "all week" },
  indeed:     { times: ["11:00"],          days: "all week" },
  greenhouse: { times: ["09:30", "15:00"], days: "all week" },
  glassdoor:  { times: ["13:00", "17:00"], days: "all week" },
};
```

**Manual:** "Scan Now" button in side panel. Fires immediately.

### Search Configuration

```javascript
const SEARCH_TITLES = [
  "data scientist",
  "data analyst",
  "data engineer",
  "machine learning engineer",
  "ai engineer",
  "software engineer",
];

const SEARCH_FILTERS = {
  location: ["United Kingdom", "Scotland, United Kingdom"],
  experience: ["intern", "entry_level"],
  date_posted: "last_24_hours",
  sort: "most_recent",
};
```

### Pre-Filter: Title Check Before Opening Any Job

Gate 0 runs locally in the service worker on the job card title BEFORE opening the job page. Zero cost, instant.

```javascript
const ROLE_KEYWORDS = [
  "data scientist", "data analyst", "data engineer",
  "ml engineer", "machine learning engineer",
  "ai engineer", "artificial intelligence",
  "nlp engineer", "natural language processing",
  "software engineer",
  "devops",
  "frontend engineer", "front-end engineer", "frontend developer",
  "cloud engineer",
];

const EXCLUDE_KEYWORDS = [
  // seniority
  "senior", "lead", "principal", "staff", "director",
  "manager", "head of", "vp", "architect", "chief",
  "10+ years", "8+ years", "5+ years", "3+ years",
  // wrong domain
  "ios", "android", "java developer", "php",
  "salesforce", "sap", "mainframe", ".net", "ruby", "golang",
  "embedded", "firmware", "hardware", "network engineer",
  "security engineer", "site reliability",
  "mechanical", "electrical", "civil", "chemical",
  "nurse", "doctor", "clinical", "pharmaceutical",
  "accounting", "finance analyst", "audit",
  "legal", "compliance officer", "solicitor",
  "teaching", "lecturer", "professor",
  "marketing manager", "content writer", "seo",
  "recruitment", "talent acquisition",
  "warehouse", "forklift", "driver",
  // wrong level
  "consultant", "contractor", "freelance",
  // wrong type
  "unpaid", "volunteer", "training contract",
  "apprenticeship level 2",
];

function shouldOpenJob(title) {
  const lower = title.toLowerCase();
  const matchesRole = ROLE_KEYWORDS.some(k => lower.includes(k));
  const excluded = EXCLUDE_KEYWORDS.some(k => lower.includes(k));
  return matchesRole && !excluded;
}
```

### Scan Flow (per platform)

**Reed + LinkedIn (Python-handled):**
```
Alarm fires → Extension POSTs to /api/job/scan-{platform}
  → Python calls API, returns job list
  → Extension title-filters locally
  → Matching jobs: POST /api/job/evaluate-batch for gate evaluation
  → Store results in IndexedDB
  → Update side panel badge
```

**Indeed + Glassdoor (Extension internal API):**
```
Alarm fires → Service worker wakes
  → fetch() to platform internal search API (with session cookies)
  → Returns JSON job cards
  → Title-filter locally
  → For each matching job: fetch full JD via detail API
  → POST /api/job/evaluate-batch to Python for gates
  → Store in IndexedDB
  → Natural delay 2-5s between fetches
  → Update side panel badge
```

**Greenhouse/Lever (Extension navigates public pages):**
```
Alarm fires → Service worker wakes
  → Opens background tab to company career page
  → Content script extracts job listings
  → Title-filter locally
  → For each matching job: navigate to detail page, extract JD
  → POST /api/job/evaluate-batch to Python for gates
  → Close tab
  → Store in IndexedDB
  → Update side panel badge
```

### Pagination

```
Page 1 → filter cards by title → process matching ones
  → Next page? → navigate
Page 2 → same
  ...
Until:
  → No next page (end of results)
  → Daily scan budget for platform reached
  → All remaining cards >24h old
  → 5 consecutive pages with zero matches (exhausted)
```

### Scan Rate Limits

| Platform | Max scan requests/day | Max jobs viewed/day |
|---|---|---|
| Reed | 100 | 100 |
| LinkedIn (guest) | 80 | 80 |
| Indeed | 40 | 40 |
| Greenhouse/Lever | 60 | 60 |
| Glassdoor | 20 | 20 |

### Scan Edge Cases

| Edge Case | Handling |
|---|---|
| LinkedIn guest API 429 | Backoff 5/10/15s, max 3 retries, then skip |
| LinkedIn truncated JD | Accept for gate eval. Full JD fetched only at apply time |
| Indeed Cloudflare challenge | Stop. 2-hour cooldown. Telegram alert |
| Indeed session expired | Alert user to visit Indeed in Chrome. Never auto-login |
| Greenhouse page structure changed | 5 fallback selectors. If all fail, skip, log, alert |
| React SPA career page | Wait for networkidle 500ms. Retry after 3s if empty |
| Duplicate job in queue | Dedup by URL + company + title hash. Skip silently |
| Job expired | Detect "no longer accepting" text. Remove from queue |
| Chrome closed mid-scan | Resume from checkpoint in IndexedDB on next alarm |
| Overlapping scans | Lock flag in chrome.storage. Skip if locked. 10-min timeout |
| Network offline | Check navigator.onLine. Skip scan. Next alarm retries |
| Python backend down | Native Messaging bootstrap. If still fails after 10s, skip, show "Backend offline" |
| Large results (500+ jobs) | Process in batches of 20. 5s pause between batches |

---

## 4-Phase Trust System

### Phase Behaviors

| | Observation | Dry Run | Supervised | Auto |
|---|---|---|---|---|
| Scans jobs | Yes | Yes | Yes | Yes |
| Runs gates | Yes | Yes | Yes | Yes |
| Opens form | Yes | Yes | Yes | Yes |
| Maps fields | Yes | Yes | Yes | Yes |
| Fills fields | **No** | Yes | Yes | Yes |
| Clicks submit | **No** | **No** | **No** (queues) | Yes (95+ ATS) |
| Human reviews | Field mapping | Filled screenshot | One-click approve | Dashboard |

### Platform Phase Caps (hardcoded)

```javascript
const PLATFORM_MAX_PHASE = {
  linkedin:   "supervised",  // NEVER auto — account irreplaceable
  indeed:     "supervised",  // NEVER auto — aggressive detection
  workday:    "supervised",  // NEVER auto — ghost application risk
  glassdoor:  "supervised",  // NEVER auto — aggressive Cloudflare
  reed:       "auto",        // Official API
  greenhouse: "auto",        // Per-company boards
  lever:      "auto",        // Same as Greenhouse
  generic:    "supervised",  // Unknown risk
};
```

### Auto-Graduation (per-platform, data-driven)

```
Observation → Dry Run:
  - 20 consecutive correct field mappings on THIS platform
  - Zero unknown field types
  - PSI < 0.1 on question type distribution (stable forms)

Dry Run → Supervised:
  - 15 consecutive clean dry runs (zero Ralph Loop retries)
  - Answer accuracy >= 95%
  - Field detection rate >= 98%
  - Zero CAPTCHA triggers during form fill

Supervised → Auto:
  - 10 consecutive unmodified approvals
  - Zero submission errors
  - Zero ATS rejection emails within 24h
  - PSI < 0.2 on question distributions
  - Platform allows auto (PLATFORM_MAX_PHASE check)
```

### Auto-Demotion (instant rollback)

| Trigger | Action |
|---|---|
| 2 consecutive field mapping errors | Demote to Observation |
| 1 submission error (403, timeout, CAPTCHA) | Demote to Supervised |
| New question type detected (PSI > 0.2) | Demote to Observation for that question type |
| ATS rejection email within 24h | Demote to Dry Run |
| CAPTCHA mid-form | Pause + Telegram alert + demote to Supervised |
| "Already Applied" detected | Skip job, log ghost application |
| Session expires mid-form | Save state to IndexedDB, re-auth, resume |
| 5% error rate over rolling 20 applications | Demote to Supervised |

### PSI Drift Detection (weekly)

Compute Population Stability Index on:
- Question types seen per platform
- Form page counts per platform
- Answer distribution per platform
- Success rates (rolling 20-application window)

```
PSI < 0.1  → No concern
PSI 0.1-0.2 → Alert side panel, human reviews next 3 applications
PSI > 0.2  → Auto-demote to Observation, Telegram alert
```

### Observation Phase Detail

```
Job passes gates → Extension navigates to apply page
  → Content script scans form fields
  → Form Intelligence maps each field to profile data
  → Records: [{field: "First Name", would_fill: "Yash", confidence: 0.97}]
  → Screenshots empty form
  → Side panel shows mapping table
  → User marks each: ✓ correct / ✗ wrong
  → All correct: consecutive_correct++
  → Any wrong: consecutive_correct = 0, log error for learning
  → NEVER types anything into the form
  → If side panel closed: observations queue in IndexedDB
  → User reviews queued observations next time side panel opens
  → Graduation counter only advances after human review
```

---

## Apply Phase

### Pre-Apply Checks

Before opening any form:
1. **Already Applied** — check IndexedDB for same URL/company+title
2. **Daily limit** — platform counter in chrome.storage
3. **Cooldown** — platform recently flagged? (CAPTCHA, 429, warning)
4. **Session health** — lightweight auth check per platform
5. **Generate CV/CL** — POST /api/job/generate-cv to Python
6. All pass → proceed. Any fail → skip, log reason, next job.

### Form Fill Sequence

```
Navigate to apply URL
  → Cookie dismisser first
  → Page analyzer detects page type
  → State machine determines state

Per form page:
  1. Content script deep-scans fields
  2. Form Intelligence resolves each answer (5 tiers)
  3. Fill with human-like timing:
     → Text: 50-150ms/char, 5% typo rate with correction
     → Dropdown: click, 500ms, scroll, click
     → Radio/checkbox: 200-500ms reading pause
     → File: DataTransfer API
     → Typeahead: type 3-4 chars, wait 1s, select suggestion
  4. Screenshot filled page
  5. Click next/continue
  6. Wait for page transition
  7. Repeat

Stuck detection: content hash (chars 300-700) comparison
  → 2 identical pages → abort

Limits:
  → 20 form pages max
  → 10 navigation steps max before reaching form
  → 5 minute total timeout per application
```

### Error Recovery

| Error | Detection | Recovery |
|---|---|---|
| CAPTCHA mid-form | reCAPTCHA/Turnstile elements detected | Pause, screenshot, Telegram alert, wait 5min for human, abort if unsolved, demote |
| Session expired | 401/403 or login redirect | Save fields to IndexedDB, alert user, resume after re-login |
| CSRF expired | 403 on submit | Refresh page, re-fill from saved answers, retry once |
| File upload failed | No callback within 10s | Retry once. CV fail → abort. CL fail → proceed without |
| Field not found | Selector returns null | 3 fallback selectors. Skip if optional, abort if required |
| Dropdown option missing | Value not in list | Fuzzy match (Levenshtein < 3). Log mismatch |
| Submit button missing | No submit/apply text | 5 fallback selectors + `<a>` tags. Screenshot, abort if all fail |
| Duplicate detected | "Already applied" text | Close tab, mark in IndexedDB, skip |
| Network timeout on submit | No response 15s | Do NOT retry (duplicate risk). Mark "submit_unknown". Check email 24h |
| Greenhouse Real Talent | Unusual error post-submit | Log, reduce rate, increase delays for Greenhouse |
| Validation error | Red border / error text | Parse error, fix value, retry fill (max 2 per field) |
| Conditional field appears | New field after dropdown | MutationObserver, 500ms wait, scan, fill |
| Modal blocks form | Overlay detected | Try dismiss (X, Escape, click outside). 3 attempts then abort |
| Redirect to different ATS | URL domain changes | Detect via webNavigation. Switch state machine. Continue |
| Ghost application | Partial save blocks resubmit | Never use "Save & Continue". Complete or abort cleanly |
| Back button corruption | Server-side state broken | Never go back. If stuck, abort and restart |
| Progress bar lies | Dynamic step count | Track content hash, not progress indicator |

---

## Anti-Detection Hardening

### Extension Stealth

```javascript
// manifest.json
"web_accessible_resources": []  // EMPTY — LinkedIn scans 6,236+ extensions

// Content script: isolated world only (default MV3)
// Page JS cannot see our variables

// Minimize DOM mutations — only modify form fields
// Never inject UI elements into host page
// MutationObservers on pages can detect added elements

// Extension sideloaded (not on Web Store) = random ID per install
// Cannot be probed by known extension ID
```

### Behavioral Mimicry

```
Text input: 50-150ms per character, 5% typo rate with backspace correction
Dropdown: click → 500ms pause → scroll → click option
Scroll: 200-500px increments, random speed
Between actions: 2-8s random delay
Between applications: 3-10 min gap
Page load: wait for networkidle, then 1-3s "reading" pause
Mouse: smooth movement to element before click (not teleport)
```

### Session Separation

- Scanning (LinkedIn/Reed): Python-only, no browser session
- Scanning (Indeed/Greenhouse): Extension uses session cookies but separate from apply flow
- Applying: Extension uses authenticated session, human-approved
- Never scan and apply on same platform within 5 minutes

---

## State Persistence

### chrome.storage.local (unlimited with permission)

```javascript
{
  phases: {
    linkedin: { current: "observation", stats: { consecutive_correct: 14, threshold: 20 } },
    indeed:   { current: "observation", stats: { consecutive_correct: 7, threshold: 20 } },
    // ...per platform
  },
  daily_limits: {
    linkedin: { scanned: 23, applied: 2, date: "2026-04-04" },
    // ...per platform
  },
  scan_schedule: { /* configurable */ },
  psi_baselines: { /* per platform question distributions */ },
  scan_lock: { locked: false, since: null },
}
```

### IndexedDB (large structured data)

```javascript
// Stores: job_queue
{
  id, url, title, company, platform,
  jd_text, scraped_at,
  gate_results: { passed, score, tier, gate_failed, details },
  phase: "observation" | "dry_run" | "supervised" | "auto",
  apply_status: "pending" | "observing" | "dry_ran" | "ready" | "approved" | "applied" | "rejected" | "error",
  field_mapping: [{ field, would_fill, confidence, correct: null|true|false }],
  dry_run_result: { success, retries, screenshots: [], errors: [] },
  applied_at: null,
  screenshots: [],
}

// Stores: ralph_patterns (per platform learned fixes)
// Stores: scan_checkpoints (resume interrupted scans)
// Stores: psi_history (weekly distribution snapshots)
```

### Recovery

IndexedDB can be evicted under storage pressure. Recovery strategy:
- On startup, verify IndexedDB stores exist
- If evicted: re-fetch pending jobs from Python backend cache
- Phase config in chrome.storage.local is NOT evictable (unlimited permission)
- Critical state (phases, counters) always in chrome.storage, not IndexedDB

---

## Telegram Notifications

```
After scan:    "🔍 Scanned 45 jobs: 12 passed gates (3 LinkedIn, 5 Reed, 4 Greenhouse)"
After observe: "👁 Observed: {company} - {role}. 8/8 fields correct. (18/20 to graduate)"
After dry run: "🧪 Dry run: {company} - {role}. Clean, 0 retries. (12/15 to graduate)"
After submit:  "✅ Applied: {company} - {role} via {platform}. ATS: 92."
On error:      "⚠️ {platform} CAPTCHA. Paused. Solve manually."
On demotion:   "⬇️ {platform} demoted to {phase}. Reason: {reason}."
Daily summary: "📊 Today: scanned 120, gates passed 28, applied 6, errors 0"
```

---

## Playwright Removal Plan

### Files to DELETE (Playwright-only, ~1,225 lines)

1. `jobpulse/browser_manager.py` (~150 lines)
2. `jobpulse/ats_adapters/linkedin.py` (~500 lines)
3. `jobpulse/ats_adapters/greenhouse.py` (~120 lines)
4. `jobpulse/ats_adapters/indeed.py` (~100 lines)
5. `jobpulse/ats_adapters/lever.py` (~100 lines)
6. `jobpulse/ats_adapters/workday.py` (~130 lines)
7. `jobpulse/ats_adapters/generic.py` (~150 lines)
8. `scripts/linkedin_login.py` (~75 lines)

### Files to MIGRATE (mixed use)

1. `jobpulse/utils/safe_io.py` — Remove managed_browser functions (lines 17-81), keep safe_openai_call, locked_json_file, atomic_sqlite
2. `jobpulse/job_scanner.py` — Remove scan_indeed Playwright code, replace with extension scan API. Keep scan_reed and scan_linkedin_guest.
3. `jobpulse/config.py` — Change APPLICATION_ENGINE default from "playwright" to "extension". Remove playwright as an option.
4. `jobpulse/ats_adapters/__init__.py` — Remove all Playwright adapter imports. ExtensionAdapter becomes the only adapter.

### Files to DELETE (tests)

1. `tests/test_browser_manager.py`
2. Parts of `tests/jobpulse/test_safe_io.py` (managed_browser tests)
3. Update `tests/jobpulse/test_phase3_wiring.py` for extension-only

### Data to DELETE

1. `data/chrome_profile/` — Playwright persistent context
2. `data/linkedin_storage.json` — Playwright storage state
3. `data/indeed_profile/` — Playwright persistent context

### Dependency to REMOVE

- `requirements.txt` — delete `playwright>=1.49.0` line (currently commented out)

---

## New Files to CREATE

### Extension

1. `extension/scanner.js` — Platform-specific scan logic (internal API calls, DOM extraction)
2. `extension/job_queue.js` — IndexedDB CRUD for job queue
3. `extension/phase_engine.js` — Graduation/demotion logic, PSI computation
4. `extension/native_bridge.js` — Native Messaging bootstrap + HTTP API wrapper

### Python

1. `jobpulse/native_host.py` — Native Messaging host bootstrap script (~30 lines)
2. `jobpulse/job_api.py` — FastAPI routes for extension communication
3. `com.jobpulse.brain.json` — Native Messaging host manifest

### Manifest changes

```json
{
  "permissions": [
    "activeTab", "scripting", "sidePanel", "storage", "tabs",
    "nativeMessaging", "alarms", "offscreen"
  ],
  "web_accessible_resources": []
}
```

---

## Code Intelligence Updates

After migration, reindex the code graph:
- New files added to index (scanner.js, job_queue.js, phase_engine.js, native_bridge.js, native_host.py, job_api.py)
- Deleted Playwright files removed from index
- Risk scores recomputed (ExtensionAdapter becomes higher risk — it's now the only apply path)
- Edge connectivity updated (new call paths: extension → HTTP API → gates)
- Embedding index updated for semantic search on new code

---

## Migration Strategy

**Approach B: Clean rebuild.** Playwright pipeline is not working. No fallback to preserve.

**Order:**
1. Delete all Playwright code + data artifacts + tests
2. Create Native Messaging host + bootstrap script
3. Add FastAPI job routes (evaluate, generate-cv, scan-reed, scan-linkedin)
4. Build extension scanner (internal API calls per platform)
5. Build IndexedDB job queue + chrome.storage state management
6. Build 4-phase trust engine with graduation/demotion
7. Rebuild side panel as control center
8. Wire Chrome Alarms for scheduled scanning
9. Wire content script JD extraction (scan_jd command)
10. Integration test: full pipeline dry run
11. Reindex code graph, update embeddings
12. Update CLAUDE.md, rules, mistakes.md
