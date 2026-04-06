# Extension-Driven Job Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken Playwright pipeline with a fully autonomous Chrome extension that scans, evaluates, and applies to jobs — with Python as brain-on-demand via Native Messaging bootstrap + HTTP API.

**Architecture:** Chrome extension drives scanning (Alarms API + internal platform APIs), delegates gate evaluation and CV generation to Python via HTTP (FastAPI on :8000), persists state in IndexedDB + chrome.storage.local. 4-phase trust system (observation → dry_run → supervised → auto) with per-platform caps and automatic demotion.

**Tech Stack:** Chrome MV3 Extension (JS), FastAPI (Python), Native Messaging (bootstrap), IndexedDB, chrome.storage, Chrome Alarms API

**Spec:** `docs/superpowers/specs/2026-04-04-extension-driven-pipeline-design.md`

---

## File Structure

### New Files — Extension

| File | Responsibility |
|------|---------------|
| `extension/scanner.js` | Per-platform scan logic: LinkedIn card extraction via Python API, Indeed/Glassdoor via fetch(), Greenhouse/Lever via DOM. Title pre-filter. Pagination. Rate limits. |
| `extension/job_queue.js` | IndexedDB CRUD for job queue. Store/retrieve/update jobs. Deduplication. Cleanup. |
| `extension/phase_engine.js` | 4-phase trust system. Per-platform graduation counters. Auto-demotion triggers. PSI drift detection. Platform phase caps. |
| `extension/native_bridge.js` | Native Messaging bootstrap + HTTP API wrapper. `callBackend(endpoint, data)` with auto-retry. `ensureBackendRunning()`. |
| `extension/config.js` | Search titles, role keywords, exclude keywords, scan schedules, rate limits, platform phase caps. Single source of truth. |

### New Files — Python

| File | Responsibility |
|------|---------------|
| `jobpulse/job_api.py` | FastAPI router: `/api/job/evaluate`, `/api/job/evaluate-batch`, `/api/job/generate-cv`, `/api/job/scan-reed`, `/api/job/scan-linkedin`, `/api/job/ralph-learn`, `/api/job/notify`, `/api/job/health` |
| `jobpulse/native_host.py` | Native Messaging host bootstrap. Reads stdin JSON, checks if FastAPI alive, starts it if not, returns status via stdout. |
| `com.jobpulse.brain.json` | Native Messaging host manifest. Registered in Chrome's NativeMessagingHosts directory. |

### Modified Files

| File | Changes |
|------|---------|
| `extension/manifest.json` | Add permissions: `nativeMessaging`, `alarms`, `offscreen`. Empty `web_accessible_resources`. |
| `extension/background.js` | Replace WebSocket with Native Messaging bootstrap + HTTP. Add Alarms registration. Add scan orchestration. Remove heartbeat/reconnect. |
| `extension/content.js` | Add `scan_jd` case to message handler — extracts full JD text from page. |
| `extension/sidepanel.html` | Rebuild as control center: job queue, phase status, approve/reject, scan settings, manual trigger. |
| `extension/sidepanel.js` | Rebuild: render job queue, phase dashboard, approve/reject handlers, settings panel. |
| `mindgraph_app/main.py` | Add `job_api_router` import and include. |
| `jobpulse/ats_adapters/__init__.py` | Remove all Playwright adapter imports. ExtensionAdapter only. Simplify `get_adapter()`. |
| `jobpulse/runner.py` | Replace `ext-bridge` command with `api-server`. Add install-native-host command. |
| `jobpulse/config.py` | Change APPLICATION_ENGINE default to "extension". Remove "playwright" option. |

### Files to DELETE

| File | Reason |
|------|--------|
| `jobpulse/browser_manager.py` | Playwright-only |
| `jobpulse/ats_adapters/linkedin.py` | Playwright adapter |
| `jobpulse/ats_adapters/greenhouse.py` | Playwright adapter |
| `jobpulse/ats_adapters/indeed.py` | Playwright adapter |
| `jobpulse/ats_adapters/lever.py` | Playwright adapter |
| `jobpulse/ats_adapters/workday.py` | Playwright adapter |
| `jobpulse/ats_adapters/generic.py` | Playwright adapter |
| `scripts/linkedin_login.py` | Playwright login helper |
| `tests/test_browser_manager.py` | Tests for deleted code |
| `data/chrome_profile/` | Playwright browser data |
| `data/linkedin_storage.json` | Playwright storage state |
| `data/indeed_profile/` | Playwright browser data |

---

## Task 1: Delete Playwright Code

**Files:**
- Delete: `jobpulse/browser_manager.py`, `jobpulse/ats_adapters/linkedin.py`, `jobpulse/ats_adapters/greenhouse.py`, `jobpulse/ats_adapters/indeed.py`, `jobpulse/ats_adapters/lever.py`, `jobpulse/ats_adapters/workday.py`, `jobpulse/ats_adapters/generic.py`, `scripts/linkedin_login.py`, `tests/test_browser_manager.py`
- Modify: `jobpulse/ats_adapters/__init__.py`, `jobpulse/utils/safe_io.py`, `jobpulse/config.py`

- [ ] **Step 1: Delete Playwright-only files**

```bash
git rm jobpulse/browser_manager.py
git rm jobpulse/ats_adapters/linkedin.py
git rm jobpulse/ats_adapters/greenhouse.py
git rm jobpulse/ats_adapters/indeed.py
git rm jobpulse/ats_adapters/lever.py
git rm jobpulse/ats_adapters/workday.py
git rm jobpulse/ats_adapters/generic.py
git rm scripts/linkedin_login.py
git rm tests/test_browser_manager.py
```

- [ ] **Step 2: Delete Playwright data artifacts**

```bash
rm -rf data/chrome_profile/
rm -f data/linkedin_storage.json
rm -rf data/indeed_profile/
```

- [ ] **Step 3: Simplify ats_adapters/__init__.py — remove Playwright imports**

Replace the entire file with:

```python
"""ATS adapter registry — extension-only mode.

All job applications route through ExtensionAdapter which uses the
Chrome extension via HTTP API for form filling.
"""

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.ext_adapter import ExtensionAdapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the ExtensionAdapter (sole adapter).

    The ats_platform parameter is retained for interface compatibility
    but is not used for routing — all platforms go through the extension.
    """
    from jobpulse.ext_bridge import ExtensionBridge

    if not hasattr(get_adapter, "_instance"):
        bridge = ExtensionBridge()
        get_adapter._instance = ExtensionAdapter(bridge)
    return get_adapter._instance


__all__ = ["BaseATSAdapter", "ExtensionAdapter", "get_adapter"]
```

- [ ] **Step 4: Remove managed_browser functions from safe_io.py**

Remove lines 17-81 (the `_import_playwright`, `managed_browser`, and `managed_persistent_browser` functions) from `jobpulse/utils/safe_io.py`. Keep `safe_openai_call`, `locked_json_file`, `atomic_sqlite`.

- [ ] **Step 5: Update config.py — default to extension**

In `jobpulse/config.py`, change line 84:

```python
# Old:
APPLICATION_ENGINE = os.getenv("APPLICATION_ENGINE", "playwright")

# New:
APPLICATION_ENGINE = os.getenv("APPLICATION_ENGINE", "extension")
```

- [ ] **Step 6: Grep for any remaining Playwright references**

```bash
grep -rn "playwright" --include="*.py" jobpulse/ tests/ scripts/ | grep -v __pycache__ | grep -v ".pyc"
```

Fix any remaining imports or references. Expected: only comments, docstrings, or test mocks that reference the old mode.

- [ ] **Step 7: Run existing tests to check nothing critical broke**

```bash
python -m pytest tests/ -x --tb=short -q --ignore=tests/test_browser_manager.py 2>&1 | tail -20
```

Expected: tests that depended on Playwright adapters may fail. Note which ones need updating (Task 12).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: delete Playwright pipeline — extension-only mode

Remove 6 Playwright ATS adapters (~1,200 lines), browser_manager,
linkedin_login script, and browser profile data. Simplify adapter
registry to extension-only routing. Change APPLICATION_ENGINE default
to 'extension'."
```

---

## Task 2: Extension Config Module

**Files:**
- Create: `extension/config.js`

- [ ] **Step 1: Create config.js with all search and filter configuration**

```javascript
// extension/config.js — Single source of truth for all extension configuration

export const SEARCH_TITLES = [
  "data scientist",
  "data analyst",
  "data engineer",
  "machine learning engineer",
  "ai engineer",
  "software engineer",
];

export const SEARCH_FILTERS = {
  location: ["United Kingdom", "Scotland, United Kingdom"],
  experience: ["intern", "entry_level"],
  date_posted: "last_24_hours",
  sort: "most_recent",
};

export const ROLE_KEYWORDS = [
  "data scientist", "data analyst", "data engineer",
  "ml engineer", "machine learning engineer",
  "ai engineer", "artificial intelligence",
  "nlp engineer", "natural language processing",
  "software engineer",
  "devops",
  "frontend engineer", "front-end engineer", "frontend developer",
  "cloud engineer",
];

export const EXCLUDE_KEYWORDS = [
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

export const SCAN_SCHEDULE = {
  reed:       { times: ["09:00", "14:00"], days: "all week" },
  linkedin:   { times: ["10:00", "16:00"], days: "all week" },
  indeed:     { times: ["11:00"],          days: "all week" },
  greenhouse: { times: ["09:30", "15:00"], days: "all week" },
  glassdoor:  { times: ["13:00", "17:00"], days: "all week" },
};

export const SCAN_RATE_LIMITS = {
  reed:       { max_requests: 100, max_jobs: 100 },
  linkedin:   { max_requests: 80,  max_jobs: 80 },
  indeed:     { max_requests: 40,  max_jobs: 40 },
  greenhouse: { max_requests: 60,  max_jobs: 60 },
  glassdoor:  { max_requests: 20,  max_jobs: 20 },
};

export const APPLY_RATE_LIMITS = {
  linkedin:   10,
  indeed:     8,
  greenhouse: 7,
  lever:      7,
  workday:    5,
  glassdoor:  5,
  reed:       7,
  generic:    5,
};

export const PLATFORM_MAX_PHASE = {
  linkedin:   "supervised",
  indeed:     "supervised",
  workday:    "supervised",
  glassdoor:  "supervised",
  reed:       "auto",
  greenhouse: "auto",
  lever:      "auto",
  generic:    "supervised",
};

export const GRADUATION_THRESHOLDS = {
  observation_to_dry_run: 20,   // consecutive correct field mappings
  dry_run_to_supervised:  15,   // consecutive clean dry runs
  supervised_to_auto:     10,   // consecutive unmodified approvals
};

export const BACKEND_URL = "http://localhost:8000";
export const NATIVE_HOST_NAME = "com.jobpulse.brain";

export function shouldOpenJob(title) {
  const lower = title.toLowerCase();
  const matchesRole = ROLE_KEYWORDS.some(k => lower.includes(k));
  const excluded = EXCLUDE_KEYWORDS.some(k => lower.includes(k));
  return matchesRole && !excluded;
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/config.js
git commit -m "feat(ext): add config module — search titles, filters, rate limits, phase caps"
```

---

## Task 3: IndexedDB Job Queue

**Files:**
- Create: `extension/job_queue.js`
- Test: Manual test via Chrome DevTools console

- [ ] **Step 1: Create job_queue.js**

```javascript
// extension/job_queue.js — IndexedDB CRUD for job queue

const DB_NAME = "jobpulse";
const DB_VERSION = 1;
const STORE_JOBS = "jobs";
const STORE_PATTERNS = "ralph_patterns";
const STORE_CHECKPOINTS = "scan_checkpoints";

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_JOBS)) {
        const store = db.createObjectStore(STORE_JOBS, { keyPath: "id" });
        store.createIndex("platform", "platform", { unique: false });
        store.createIndex("apply_status", "apply_status", { unique: false });
        store.createIndex("scraped_at", "scraped_at", { unique: false });
      }
      if (!db.objectStoreNames.contains(STORE_PATTERNS)) {
        db.createObjectStore(STORE_PATTERNS, { keyPath: "id", autoIncrement: true });
      }
      if (!db.objectStoreNames.contains(STORE_CHECKPOINTS)) {
        db.createObjectStore(STORE_CHECKPOINTS, { keyPath: "platform" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// ─── Job CRUD ─────────────────────────────────────────

export async function addJob(job) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readwrite");
    tx.objectStore(STORE_JOBS).put(job);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export async function getJob(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readonly");
    const req = tx.objectStore(STORE_JOBS).get(id);
    req.onsuccess = () => { db.close(); resolve(req.result); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}

export async function updateJob(id, updates) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readwrite");
    const store = tx.objectStore(STORE_JOBS);
    const req = store.get(id);
    req.onsuccess = () => {
      const job = req.result;
      if (!job) { db.close(); reject(new Error(`Job ${id} not found`)); return; }
      Object.assign(job, updates);
      store.put(job);
    };
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export async function getJobsByStatus(status) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readonly");
    const idx = tx.objectStore(STORE_JOBS).index("apply_status");
    const req = idx.getAll(status);
    req.onsuccess = () => { db.close(); resolve(req.result); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}

export async function getJobsByPlatform(platform) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readonly");
    const idx = tx.objectStore(STORE_JOBS).index("platform");
    const req = idx.getAll(platform);
    req.onsuccess = () => { db.close(); resolve(req.result); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}

export async function getAllJobs() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readonly");
    const req = tx.objectStore(STORE_JOBS).getAll();
    req.onsuccess = () => { db.close(); resolve(req.result); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}

export async function deleteJob(id) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_JOBS, "readwrite");
    tx.objectStore(STORE_JOBS).delete(id);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

// ─── Deduplication ────────────────────────────────────

export async function isDuplicate(url, company, title) {
  const id = await makeJobId(url);
  const existing = await getJob(id);
  if (existing) return true;

  // Also check company+title combo for cross-platform dedup
  const all = await getAllJobs();
  const normalTitle = title.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
  const normalCompany = company.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
  return all.some(j => {
    const jTitle = j.title.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    const jCompany = j.company.toLowerCase().replace(/[^a-z0-9 ]/g, "").trim();
    return jTitle === normalTitle && jCompany === normalCompany;
  });
}

// ─── Helpers ──────────────────────────────────────────

export async function makeJobId(url) {
  const encoder = new TextEncoder();
  const data = encoder.encode(url.toLowerCase().replace(/\/$/, ""));
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, "0")).join("");
}

export function createJobEntry(raw) {
  return {
    id: raw.id,
    url: raw.url,
    title: raw.title,
    company: raw.company,
    platform: raw.platform,
    jd_text: raw.jd_text || "",
    scraped_at: new Date().toISOString(),
    gate_results: raw.gate_results || null,
    phase: raw.phase || "observation",
    apply_status: raw.apply_status || "pending",
    field_mapping: null,
    dry_run_result: null,
    applied_at: null,
    screenshots: [],
    error_log: [],
  };
}

// ─── Scan Checkpoints ─────────────────────────────────

export async function saveCheckpoint(platform, data) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CHECKPOINTS, "readwrite");
    tx.objectStore(STORE_CHECKPOINTS).put({ platform, ...data, saved_at: Date.now() });
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}

export async function getCheckpoint(platform) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CHECKPOINTS, "readonly");
    const req = tx.objectStore(STORE_CHECKPOINTS).get(platform);
    req.onsuccess = () => { db.close(); resolve(req.result); };
    req.onerror = () => { db.close(); reject(req.error); };
  });
}

export async function clearCheckpoint(platform) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_CHECKPOINTS, "readwrite");
    tx.objectStore(STORE_CHECKPOINTS).delete(platform);
    tx.oncomplete = () => { db.close(); resolve(); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  });
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/job_queue.js
git commit -m "feat(ext): add IndexedDB job queue — CRUD, dedup, checkpoints"
```

---

## Task 4: Native Messaging Bootstrap + HTTP API Wrapper

**Files:**
- Create: `extension/native_bridge.js`, `jobpulse/native_host.py`, `com.jobpulse.brain.json`
- Test: `tests/jobpulse/test_native_host.py`

- [ ] **Step 1: Write test for native_host.py**

```python
# tests/jobpulse/test_native_host.py
"""Tests for Native Messaging host bootstrap."""

import json
import subprocess
import sys
from unittest.mock import patch, MagicMock

import pytest


def test_health_check_when_backend_running(monkeypatch):
    """When FastAPI is already running, bootstrap returns ready immediately."""
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.get", return_value=mock_resp):
        from jobpulse.native_host import check_backend_health
        assert check_backend_health() is True


def test_health_check_when_backend_down(monkeypatch):
    """When FastAPI is not running, health check returns False."""
    import httpx

    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        from jobpulse.native_host import check_backend_health
        assert check_backend_health() is False


def test_start_backend_launches_subprocess(monkeypatch):
    """start_backend() launches the FastAPI server as a detached process."""
    mock_popen = MagicMock()
    with patch("subprocess.Popen", return_value=mock_popen) as popen_call:
        from jobpulse.native_host import start_backend
        start_backend()
        popen_call.assert_called_once()
        args = popen_call.call_args
        assert "jobpulse.runner" in str(args)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/jobpulse/test_native_host.py -v
```

Expected: FAIL — `jobpulse.native_host` does not exist yet.

- [ ] **Step 3: Create native_host.py**

```python
#!/usr/bin/env python3
"""Native Messaging host for Chrome extension bootstrap.

Chrome calls this via stdin/stdout JSON. Its only job: ensure the
FastAPI backend is running on :8000, then return {"status": "ready"}.

Registered as: com.jobpulse.brain
"""

import json
import os
import struct
import subprocess
import sys
import time

import httpx

BACKEND_URL = "http://localhost:8000"
HEALTH_ENDPOINT = f"{BACKEND_URL}/api/job/health"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_backend_health() -> bool:
    """Check if FastAPI backend is responding."""
    try:
        resp = httpx.get(HEALTH_ENDPOINT, timeout=3.0)
        return resp.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def start_backend() -> None:
    """Start FastAPI backend as a detached background process."""
    subprocess.Popen(
        [sys.executable, "-m", "jobpulse.runner", "api-server"],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def read_message() -> dict:
    """Read a Native Messaging message from stdin."""
    raw_length = sys.stdin.buffer.read(4)
    if not raw_length:
        return {}
    length = struct.unpack("=I", raw_length)[0]
    data = sys.stdin.buffer.read(length)
    return json.loads(data.decode("utf-8"))


def send_message(msg: dict) -> None:
    """Write a Native Messaging message to stdout."""
    encoded = json.dumps(msg).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(encoded)))
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


def main() -> None:
    """Main entry point — handle one bootstrap request."""
    _msg = read_message()

    if check_backend_health():
        send_message({"status": "ready", "port": 8000})
        return

    start_backend()

    # Wait up to 10s for backend to start
    for _ in range(20):
        time.sleep(0.5)
        if check_backend_health():
            send_message({"status": "ready", "port": 8000})
            return

    send_message({"status": "error", "message": "Backend failed to start within 10s"})


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/jobpulse/test_native_host.py -v
```

Expected: PASS

- [ ] **Step 5: Create Native Messaging host manifest**

```json
{
  "name": "com.jobpulse.brain",
  "description": "JobPulse Python backend bootstrap",
  "path": "/Users/yashbishnoi/projects/multi_agent_patterns/jobpulse/native_host.py",
  "type": "stdio",
  "allowed_origins": ["chrome-extension://EXTENSION_ID_HERE/"]
}
```

Save to: `com.jobpulse.brain.json` (project root — will be symlinked during install).

- [ ] **Step 6: Create native_bridge.js for the extension**

```javascript
// extension/native_bridge.js — Native Messaging bootstrap + HTTP API wrapper

import { BACKEND_URL, NATIVE_HOST_NAME } from "./config.js";

let backendReady = false;

/**
 * Ensure the Python backend is running via Native Messaging bootstrap.
 * If backend is already up, this is a no-op (~3ms health check).
 */
export async function ensureBackendRunning() {
  // Fast path: check HTTP health first
  try {
    const resp = await fetch(`${BACKEND_URL}/api/job/health`, {
      signal: AbortSignal.timeout(3000),
    });
    if (resp.ok) { backendReady = true; return; }
  } catch (_) { /* backend not running */ }

  // Slow path: bootstrap via Native Messaging
  return new Promise((resolve, reject) => {
    const port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    port.postMessage({ action: "ensure_running" });
    port.onMessage.addListener((msg) => {
      port.disconnect();
      if (msg.status === "ready") {
        backendReady = true;
        resolve();
      } else {
        reject(new Error(msg.message || "Backend bootstrap failed"));
      }
    });
    port.onDisconnect.addListener(() => {
      if (!backendReady) {
        reject(new Error(chrome.runtime.lastError?.message || "Native host disconnected"));
      }
    });
    // Timeout after 15s
    setTimeout(() => {
      port.disconnect();
      if (!backendReady) reject(new Error("Backend bootstrap timeout"));
    }, 15000);
  });
}

/**
 * Call a backend API endpoint. Auto-bootstraps if backend is down.
 * @param {string} endpoint — e.g. "evaluate", "generate-cv"
 * @param {object} data — request body
 * @returns {Promise<object>} — response JSON
 */
export async function callBackend(endpoint, data = {}) {
  const url = `${BACKEND_URL}/api/job/${endpoint}`;

  async function doFetch() {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
      signal: AbortSignal.timeout(60000), // 60s for CV generation etc.
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Backend ${resp.status}: ${text}`);
    }
    return resp.json();
  }

  try {
    return await doFetch();
  } catch (e) {
    // If connection refused, try bootstrap then retry once
    if (e.message.includes("Failed to fetch") || e.message.includes("NetworkError")) {
      await ensureBackendRunning();
      return await doFetch();
    }
    throw e;
  }
}

/**
 * GET request to backend (for health checks, simple queries).
 */
export async function getBackend(endpoint) {
  const url = `${BACKEND_URL}/api/job/${endpoint}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(5000) });
  if (!resp.ok) throw new Error(`Backend ${resp.status}`);
  return resp.json();
}
```

- [ ] **Step 7: Commit**

```bash
git add extension/native_bridge.js jobpulse/native_host.py com.jobpulse.brain.json tests/jobpulse/test_native_host.py
git commit -m "feat: add Native Messaging bootstrap + HTTP API bridge

Native host checks if FastAPI is alive, starts it if not.
Extension calls backend via standard HTTP fetch with auto-retry."
```

---

## Task 5: FastAPI Job Routes

**Files:**
- Create: `jobpulse/job_api.py`
- Modify: `mindgraph_app/main.py`
- Test: `tests/jobpulse/test_job_api.py`

- [ ] **Step 1: Write tests for job API endpoints**

```python
# tests/jobpulse/test_job_api.py
"""Tests for FastAPI job API routes."""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from mindgraph_app.main import app
    return TestClient(app)


def test_health_endpoint(client):
    resp = client.get("/api/job/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_evaluate_returns_gate_results(client, tmp_path):
    """POST /api/job/evaluate with JD text returns gate evaluation."""
    mock_listing = MagicMock()
    mock_listing.required_skills = ["python", "sql"]

    mock_screen = MagicMock()
    mock_screen.gate1_passed = True
    mock_screen.gate2_passed = True
    mock_screen.gate3_score = 85
    mock_screen.tier = "strong"
    mock_screen.matched_skills = ["python"]
    mock_screen.missing_skills = ["sql"]

    with patch("jobpulse.job_api.analyze_jd", return_value=mock_listing), \
         patch("jobpulse.job_api.gate0_title_relevance", return_value=True), \
         patch("jobpulse.job_api.SkillGraphStore") as MockStore:
        MockStore.return_value.pre_screen_jd.return_value = mock_screen
        resp = client.post("/api/job/evaluate", json={
            "url": "https://example.com/job/123",
            "title": "Data Scientist",
            "company": "Acme Corp",
            "platform": "linkedin",
            "jd_text": "We need a data scientist with Python and SQL...",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["passed"] is True
    assert data["tier"] == "strong"


def test_evaluate_gate0_fail(client):
    """Gate 0 title filter rejects irrelevant jobs."""
    with patch("jobpulse.job_api.gate0_title_relevance", return_value=False):
        resp = client.post("/api/job/evaluate", json={
            "url": "https://example.com/job/456",
            "title": "Senior iOS Developer",
            "company": "Acme",
            "platform": "linkedin",
            "jd_text": "iOS developer needed...",
        })
    assert resp.status_code == 200
    assert resp.json()["passed"] is False
    assert resp.json()["gate_failed"] == "gate0"


def test_scan_reed(client):
    """POST /api/job/scan-reed returns job list."""
    mock_jobs = [{"title": "Data Scientist", "company": "Corp", "url": "https://reed.co.uk/1"}]
    with patch("jobpulse.job_api.scan_reed", return_value=mock_jobs):
        resp = client.post("/api/job/scan-reed", json={
            "titles": ["data scientist"],
            "location": "United Kingdom",
        })
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/jobpulse/test_job_api.py -v
```

Expected: FAIL — `jobpulse.job_api` does not exist.

- [ ] **Step 3: Create job_api.py**

```python
"""FastAPI routes for Chrome extension ↔ Python communication.

All job pipeline logic (gate evaluation, CV generation, scanning)
exposed as HTTP endpoints. Extension calls these via fetch().
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from jobpulse.jd_analyzer import analyze_jd
from jobpulse.recruiter_screen import gate0_title_relevance
from jobpulse.skill_graph_store import SkillGraphStore
from jobpulse.gate4_quality import check_jd_quality
from jobpulse.job_scanner import scan_reed, scan_linkedin
from jobpulse.models.application_models import SearchConfig
from shared.logging_config import get_logger

logger = get_logger(__name__)

job_api_router = APIRouter(prefix="/api/job")

# ─── Lazy singletons ──────────────────────────────────

_store: SkillGraphStore | None = None


def _get_store() -> SkillGraphStore:
    global _store
    if _store is None:
        _store = SkillGraphStore()
    return _store


# ─── Request/Response Models ──────────────────────────

class EvaluateRequest(BaseModel):
    url: str
    title: str
    company: str
    platform: str
    jd_text: str
    apply_url: str = ""


class EvaluateResponse(BaseModel):
    passed: bool
    score: float = 0
    tier: str = "reject"
    gate_failed: str | None = None
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    details: str = ""


class BatchEvaluateRequest(BaseModel):
    jobs: list[EvaluateRequest]


class ScanRequest(BaseModel):
    titles: list[str]
    location: str = "United Kingdom"
    posted_within: int = 1


class GenerateCVRequest(BaseModel):
    company: str
    role: str
    location: str = ""
    matched_projects: list[str] = []
    required_skills: list[str] = []
    generate_cover_letter: bool = False


class NotifyRequest(BaseModel):
    message: str
    bot: str = "jobs"


class RalphLearnRequest(BaseModel):
    platform: str
    url: str
    success: bool
    error: str = ""
    diagnosis: str = ""
    fix_pattern: dict = {}


# ─── Endpoints ────────────────────────────────────────

@job_api_router.get("/health")
def health():
    return {"status": "ok"}


@job_api_router.post("/evaluate", response_model=EvaluateResponse)
def evaluate_job(req: EvaluateRequest):
    """Run Gates 0-4A on a single job."""
    # Gate 0: title relevance
    config = {
        "titles": ["data scientist", "data analyst", "data engineer",
                    "machine learning engineer", "ai engineer",
                    "software engineer"],
        "exclude_keywords": [],
    }
    if not gate0_title_relevance(req.title, req.jd_text, config):
        return EvaluateResponse(passed=False, gate_failed="gate0",
                                details="Title filter rejected")

    # Analyze JD
    listing = analyze_jd(
        url=req.url, title=req.title, company=req.company,
        platform=req.platform, jd_text=req.jd_text, apply_url=req.apply_url,
    )

    # Gates 1-3: skill match
    store = _get_store()
    screen = store.pre_screen_jd(listing)

    if not screen.gate1_passed:
        return EvaluateResponse(passed=False, gate_failed="gate1",
                                tier="reject", details="Kill signal triggered")
    if not screen.gate2_passed:
        return EvaluateResponse(passed=False, gate_failed="gate2",
                                tier="skip", details="Must-have skills missing")

    # Gate 4A: JD quality
    jd_quality = check_jd_quality(req.jd_text, listing.required_skills or [])
    if not jd_quality.passed:
        return EvaluateResponse(passed=False, gate_failed="gate4a",
                                tier="skip", details=jd_quality.reason)

    return EvaluateResponse(
        passed=True,
        score=screen.gate3_score,
        tier=screen.tier,
        matched_skills=list(screen.matched_skills),
        missing_skills=list(screen.missing_skills),
        details=screen.breakdown if hasattr(screen, "breakdown") else "",
    )


@job_api_router.post("/evaluate-batch")
def evaluate_batch(req: BatchEvaluateRequest):
    """Evaluate multiple jobs. Returns list of EvaluateResponse."""
    results = []
    for job in req.jobs:
        try:
            result = evaluate_job(job)
            results.append(result.model_dump())
        except Exception as e:
            logger.error("evaluate_batch: error on %s: %s", job.url, e)
            results.append({"passed": False, "gate_failed": "error",
                            "details": str(e)})
    return {"results": results}


@job_api_router.post("/scan-reed")
def api_scan_reed(req: ScanRequest):
    """Run Reed API scan from Python."""
    config = SearchConfig(
        titles=req.titles,
        location=req.location,
        posted_within=req.posted_within,
    )
    jobs = scan_reed(config)
    return {"jobs": jobs, "count": len(jobs)}


@job_api_router.post("/scan-linkedin")
def api_scan_linkedin(req: ScanRequest):
    """Run LinkedIn guest API scan from Python."""
    config = SearchConfig(
        titles=req.titles,
        location=req.location,
        posted_within=req.posted_within,
    )
    jobs = scan_linkedin(config)
    return {"jobs": jobs, "count": len(jobs)}


@job_api_router.post("/generate-cv")
def api_generate_cv(req: GenerateCVRequest):
    """Generate CV (and optionally cover letter) PDFs."""
    from jobpulse.cv_templates.generate_cv import generate_cv_pdf

    cv_path = generate_cv_pdf(req.company, req.location)

    cl_path = None
    if req.generate_cover_letter:
        from jobpulse.cv_templates.generate_cover_letter import generate_cover_letter_pdf
        cl_path = generate_cover_letter_pdf(
            req.company, req.role,
            req.matched_projects, req.required_skills,
        )

    return {
        "cv_path": str(cv_path),
        "cover_letter_path": str(cl_path) if cl_path else None,
    }


@job_api_router.post("/ralph-learn")
def api_ralph_learn(req: RalphLearnRequest):
    """Store a Ralph Loop learned fix pattern."""
    from jobpulse.ralph_loop.pattern_store import PatternStore
    from jobpulse.config import DATA_DIR

    store = PatternStore(str(DATA_DIR / "ralph_patterns.db"))
    store.save_pattern(
        platform=req.platform,
        url=req.url,
        success=req.success,
        error=req.error,
        diagnosis=req.diagnosis,
        fix=req.fix_pattern,
    )
    return {"status": "saved"}


@job_api_router.post("/notify")
def api_notify(req: NotifyRequest):
    """Send a Telegram notification."""
    from jobpulse.telegram_utils import send_telegram_message
    from jobpulse.config import TELEGRAM_JOBS_BOT_TOKEN, TELEGRAM_CHAT_ID

    token = TELEGRAM_JOBS_BOT_TOKEN
    send_telegram_message(req.message, token=token, chat_id=TELEGRAM_CHAT_ID)
    return {"status": "sent"}
```

- [ ] **Step 4: Add job_api_router to FastAPI app in mindgraph_app/main.py**

Add after the existing router imports (around line 14):

```python
from jobpulse.job_api import job_api_router
```

Add after the existing `app.include_router()` calls (around line 39):

```python
app.include_router(job_api_router)
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/jobpulse/test_job_api.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/job_api.py tests/jobpulse/test_job_api.py mindgraph_app/main.py
git commit -m "feat: add FastAPI job routes — evaluate, scan, generate-cv, notify

Extension communicates with Python via these HTTP endpoints.
Supports single + batch gate evaluation, Reed/LinkedIn scanning,
CV generation, Ralph Loop learning, and Telegram notifications."
```

---

## Task 6: Update Extension Manifest

**Files:**
- Modify: `extension/manifest.json`

- [ ] **Step 1: Update manifest with new permissions and module support**

Replace the entire manifest with:

```json
{
  "manifest_version": 3,
  "name": "JobPulse Application Engine",
  "version": "2.0.0",
  "description": "Autonomous job scanning, evaluation, and application engine.",
  "permissions": [
    "activeTab",
    "scripting",
    "sidePanel",
    "storage",
    "tabs",
    "nativeMessaging",
    "alarms",
    "offscreen"
  ],
  "host_permissions": ["<all_urls>"],
  "background": {
    "service_worker": "background.js",
    "type": "module"
  },
  "content_scripts": [{
    "matches": ["<all_urls>"],
    "js": ["content.js"],
    "run_at": "document_idle",
    "all_frames": true
  }],
  "side_panel": { "default_path": "sidepanel.html" },
  "action": {
    "default_popup": "popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    }
  },
  "web_accessible_resources": []
}
```

Key changes:
- Version bumped to 2.0.0
- Added `nativeMessaging`, `alarms`, `offscreen` permissions
- Added `"type": "module"` to service worker (enables ES module imports)
- Empty `web_accessible_resources` (anti-detection: LinkedIn scans for extensions)

- [ ] **Step 2: Commit**

```bash
git add extension/manifest.json
git commit -m "feat(ext): update manifest — add nativeMessaging, alarms, offscreen permissions"
```

---

## Task 7: Add scan_jd Command to Content Script

**Files:**
- Modify: `extension/content.js`

- [ ] **Step 1: Add scan_jd case to the message handler**

In `extension/content.js`, add a new case inside the `chrome.runtime.onMessage.addListener` switch block (around line 472, after the `analyze_field` case):

```javascript
    case "scan_jd": {
      // Extract full job description text from the current page.
      // Uses platform-specific selectors, falls back to body text.
      const selectors = [
        // LinkedIn
        ".description__text", ".show-more-less-html__markup",
        "[class*='description']", ".jobs-description__content",
        // Indeed
        "#jobDescriptionText", ".jobsearch-jobDescriptionText",
        // Greenhouse
        "#content", ".content-intro", "[data-mapped='true']",
        // Lever
        ".posting-page .content", "[data-qa='job-description']",
        // Generic
        "[class*='job-description']", "[class*='jobDescription']",
        "[id*='job-description']", "[id*='jobDescription']",
        "article", "main", "[role='main']",
      ];

      let jdText = "";
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el && el.innerText.trim().length > 200) {
          jdText = el.innerText.trim();
          break;
        }
      }

      // Fallback: full body text (truncated to 10,000 chars)
      if (!jdText) {
        jdText = (document.body.innerText || "").substring(0, 10000).trim();
      }

      sendResponse({
        success: true,
        jd_text: jdText,
        url: window.location.href,
        title: document.title,
      });
      break;
    }
```

- [ ] **Step 2: Commit**

```bash
git add extension/content.js
git commit -m "feat(ext): add scan_jd command — extract full JD text from any job page"
```

---

## Task 8: Phase Engine

**Files:**
- Create: `extension/phase_engine.js`

- [ ] **Step 1: Create phase_engine.js**

```javascript
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
```

- [ ] **Step 2: Commit**

```bash
git add extension/phase_engine.js
git commit -m "feat(ext): add phase engine — graduation, demotion, daily limits, per-platform tracking"
```

---

## Task 9: Extension Scanner

**Files:**
- Create: `extension/scanner.js`

- [ ] **Step 1: Create scanner.js**

```javascript
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
    posted_within: 1,
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
    posted_within: 1,
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
```

- [ ] **Step 2: Commit**

```bash
git add extension/scanner.js
git commit -m "feat(ext): add scanner — Reed/LinkedIn via Python API, Indeed via internal fetch

Title pre-filter, dedup, gate evaluation, rate limits, scan locking.
Greenhouse/Glassdoor scanners to be added in follow-up."
```

---

## Task 10: Rewrite background.js — Replace WebSocket with Alarms + HTTP

**Files:**
- Modify: `extension/background.js`

- [ ] **Step 1: Rewrite background.js**

Replace the entire file with:

```javascript
// extension/background.js — Service worker: scan scheduling + command relay

import { SCAN_SCHEDULE } from "./config.js";
import { runScan, runAllScans } from "./scanner.js";
import { callBackend } from "./native_bridge.js";
import { getJobsByStatus } from "./job_queue.js";
import { getDailyStats, getAllPhases } from "./phase_engine.js";

// ─── Alarm Registration ───────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  registerAlarms();
  console.log("[JobPulse] Extension installed — alarms registered");
});

chrome.runtime.onStartup.addListener(() => {
  registerAlarms();
  console.log("[JobPulse] Browser started — alarms re-registered");
});

function registerAlarms() {
  // Clear old alarms and re-register
  chrome.alarms.clearAll(() => {
    for (const [platform, schedule] of Object.entries(SCAN_SCHEDULE)) {
      for (let i = 0; i < schedule.times.length; i++) {
        const alarmName = `scan_${platform}_${i}`;
        const [hours, minutes] = schedule.times[i].split(":").map(Number);

        // Calculate delay until next occurrence
        const now = new Date();
        const target = new Date();
        target.setHours(hours, minutes, 0, 0);
        if (target <= now) target.setDate(target.getDate() + 1);

        const delayMinutes = (target - now) / 60000;

        chrome.alarms.create(alarmName, {
          delayInMinutes: delayMinutes,
          periodInMinutes: 1440, // every 24 hours
        });
      }
    }
    console.log("[JobPulse] Alarms registered:", Object.keys(SCAN_SCHEDULE));
  });
}

// ─── Alarm Handler ────────────────────────────────────

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm.name.startsWith("scan_")) return;

  const platform = alarm.name.replace(/^scan_/, "").replace(/_\d+$/, "");
  console.log(`[JobPulse] Alarm fired: ${alarm.name} → scanning ${platform}`);

  try {
    const result = await runScan(platform);
    console.log(`[JobPulse] Scan complete:`, result);

    // Notify via Telegram
    const summary = `🔍 Scanned ${result.scanned} ${platform} jobs: ${result.passed} passed gates`;
    try {
      await callBackend("notify", { message: summary, bot: "jobs" });
    } catch (e) {
      console.error("[JobPulse] Telegram notify failed:", e.message);
    }

    // Update side panel
    broadcastToUI({ type: "scan_complete", payload: result });
  } catch (e) {
    console.error(`[JobPulse] Scan error for ${platform}:`, e.message);
  }
});

// ─── Message Handlers (from side panel, popup, content script) ──

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "scan_now") {
    // Manual scan trigger from side panel
    const platform = msg.platform || "all";
    (async () => {
      try {
        const result = platform === "all"
          ? await runAllScans()
          : await runScan(platform);
        sendResponse({ success: true, result });
      } catch (e) {
        sendResponse({ success: false, error: e.message });
      }
    })();
    return true; // async response
  }

  if (msg.type === "get_status") {
    (async () => {
      const pending = await getJobsByStatus("pending");
      const daily = await getDailyStats();
      const phases = await getAllPhases();
      sendResponse({
        queue_count: pending.length,
        daily_stats: daily,
        phases,
      });
    })();
    return true;
  }

  if (msg.type === "get_queue") {
    (async () => {
      const jobs = await getJobsByStatus(msg.status || "pending");
      sendResponse({ jobs });
    })();
    return true;
  }

  // Forward commands to content script (for form filling)
  if (msg.type === "ext_command") {
    const { action, payload, tabId } = msg;
    chrome.tabs.sendMessage(tabId || sender.tab?.id, { action, ...payload }, sendResponse);
    return true;
  }
});

// ─── Broadcast to UI ──────────────────────────────────

function broadcastToUI(msg) {
  chrome.runtime.sendMessage(msg).catch(() => {
    // Side panel not open — ignore
  });
}

// ─── Keep Service Worker Alive During Scans ───────────

// Chrome Alarms wake the SW automatically. No heartbeat needed.
// The SW will stay alive as long as event handlers are processing.
```

- [ ] **Step 2: Commit**

```bash
git add extension/background.js
git commit -m "refactor(ext): rewrite background.js — replace WebSocket with Alarms + HTTP

Remove WebSocket connection, heartbeat, reconnect logic. Add Chrome
Alarms for scheduled scanning, message handlers for manual triggers,
and queue status queries. SW stays alive during event processing."
```

---

## Task 11: Rebuild Side Panel as Control Center

**Files:**
- Modify: `extension/sidepanel.html`, `extension/sidepanel.js`

- [ ] **Step 1: Rewrite sidepanel.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>JobPulse</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: -apple-system, system-ui, sans-serif; font-size: 13px; background: #f8f9fa; color: #333; padding: 12px; }
    h2 { font-size: 15px; margin-bottom: 8px; }
    h3 { font-size: 13px; margin-bottom: 6px; color: #555; }
    .section { background: #fff; border-radius: 8px; padding: 12px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }
    .badge-green { background: #d4edda; color: #155724; }
    .badge-yellow { background: #fff3cd; color: #856404; }
    .badge-red { background: #f8d7da; color: #721c24; }
    .badge-blue { background: #d1ecf1; color: #0c5460; }
    .badge-gray { background: #e2e3e5; color: #383d41; }
    .btn { padding: 6px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
    .btn-primary { background: #1a5276; color: #fff; }
    .btn-success { background: #28a745; color: #fff; }
    .btn-danger { background: #dc3545; color: #fff; }
    .btn-sm { padding: 3px 8px; font-size: 11px; }
    .btn:hover { opacity: 0.85; }
    .stats { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; text-align: center; }
    .stat-box { background: #f1f3f5; border-radius: 6px; padding: 8px; }
    .stat-box .num { font-size: 20px; font-weight: 700; }
    .stat-box .label { font-size: 10px; color: #777; }
    .job-card { padding: 8px; border-bottom: 1px solid #eee; }
    .job-card:last-child { border-bottom: none; }
    .job-title { font-weight: 600; }
    .job-meta { font-size: 11px; color: #777; margin-top: 2px; }
    .phase-row { display: flex; justify-content: space-between; align-items: center; padding: 4px 0; }
    .controls { display: flex; gap: 6px; margin-top: 8px; }
    #queue-list { max-height: 300px; overflow-y: auto; }
    .empty { text-align: center; color: #999; padding: 20px; font-style: italic; }
  </style>
</head>
<body>
  <h2>JobPulse <span id="backend-status" class="badge badge-gray">checking...</span></h2>

  <!-- Daily Stats -->
  <div class="section">
    <h3>Today</h3>
    <div class="stats">
      <div class="stat-box"><div class="num" id="stat-scanned">0</div><div class="label">Scanned</div></div>
      <div class="stat-box"><div class="num" id="stat-passed">0</div><div class="label">Passed</div></div>
      <div class="stat-box"><div class="num" id="stat-applied">0</div><div class="label">Applied</div></div>
    </div>
  </div>

  <!-- Phase Status -->
  <div class="section">
    <h3>Trust Phases</h3>
    <div id="phase-list"></div>
  </div>

  <!-- Scan Controls -->
  <div class="section">
    <h3>Scanning</h3>
    <div class="controls">
      <button class="btn btn-primary" id="btn-scan-all">Scan All Now</button>
      <select id="scan-platform">
        <option value="all">All Platforms</option>
        <option value="reed">Reed</option>
        <option value="linkedin">LinkedIn</option>
        <option value="indeed">Indeed</option>
      </select>
    </div>
    <div id="scan-status" style="margin-top:6px; font-size:11px; color:#777;"></div>
  </div>

  <!-- Job Queue -->
  <div class="section">
    <h3>Queue <span id="queue-count" class="badge badge-blue">0</span></h3>
    <div id="queue-list"><div class="empty">No jobs in queue</div></div>
  </div>

  <script src="sidepanel.js" type="module"></script>
</body>
</html>
```

- [ ] **Step 2: Rewrite sidepanel.js**

```javascript
// extension/sidepanel.js — Control center UI

import { PLATFORM_MAX_PHASE } from "./config.js";

// ─── State ────────────────────────────────────────────

let currentStatus = {};

// ─── Init ─────────────────────────────────────────────

async function init() {
  await refreshStatus();
  await refreshQueue();
  setInterval(refreshStatus, 30000); // refresh every 30s
}

// ─── Status ───────────────────────────────────────────

async function refreshStatus() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "get_status" });
    currentStatus = resp;
    renderStats(resp.daily_stats);
    renderPhases(resp.phases);
    document.getElementById("queue-count").textContent = resp.queue_count;
    document.getElementById("backend-status").textContent = "connected";
    document.getElementById("backend-status").className = "badge badge-green";
  } catch (e) {
    document.getElementById("backend-status").textContent = "offline";
    document.getElementById("backend-status").className = "badge badge-red";
  }
}

function renderStats(daily) {
  let scanned = 0, applied = 0;
  for (const p of Object.values(daily || {})) {
    scanned += p.scanned || 0;
    applied += p.applied || 0;
  }
  document.getElementById("stat-scanned").textContent = scanned;
  document.getElementById("stat-applied").textContent = applied;
  // "passed" is queue count
  document.getElementById("stat-passed").textContent = currentStatus.queue_count || 0;
}

function renderPhases(phases) {
  const el = document.getElementById("phase-list");
  const platforms = ["linkedin", "indeed", "reed", "greenhouse", "lever", "workday"];
  el.innerHTML = platforms.map(p => {
    const ps = phases?.[p] || {};
    const phase = ps.current || "observation";
    const max = PLATFORM_MAX_PHASE[p] || "supervised";
    const badgeClass = {
      observation: "badge-gray", dry_run: "badge-yellow",
      supervised: "badge-blue", auto: "badge-green",
    }[phase] || "badge-gray";
    return `<div class="phase-row">
      <span>${p}</span>
      <span class="badge ${badgeClass}">${phase}</span>
      <span style="font-size:10px;color:#999">max: ${max}</span>
    </div>`;
  }).join("");
}

// ─── Queue ────────────────────────────────────────────

async function refreshQueue() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "get_queue", status: "pending" });
    renderQueue(resp.jobs || []);
  } catch (e) {
    document.getElementById("queue-list").innerHTML = `<div class="empty">Error loading queue</div>`;
  }
}

function renderQueue(jobs) {
  const el = document.getElementById("queue-list");
  if (!jobs.length) {
    el.innerHTML = `<div class="empty">No jobs in queue</div>`;
    return;
  }
  el.innerHTML = jobs.slice(0, 50).map(j => `
    <div class="job-card" data-id="${j.id}">
      <div class="job-title">${escapeHtml(j.title)}</div>
      <div class="job-meta">${escapeHtml(j.company)} · ${j.platform} · ATS: ${j.gate_results?.score || "?"}</div>
      <div class="controls" style="margin-top:4px">
        <button class="btn btn-success btn-sm btn-approve" data-id="${j.id}">Approve</button>
        <button class="btn btn-danger btn-sm btn-reject" data-id="${j.id}">Reject</button>
      </div>
    </div>
  `).join("");

  // Bind approve/reject buttons
  el.querySelectorAll(".btn-approve").forEach(btn => {
    btn.addEventListener("click", () => approveJob(btn.dataset.id));
  });
  el.querySelectorAll(".btn-reject").forEach(btn => {
    btn.addEventListener("click", () => rejectJob(btn.dataset.id));
  });
}

async function approveJob(id) {
  // TODO: wire to apply phase based on current platform phase
  console.log("Approve:", id);
  await refreshQueue();
}

async function rejectJob(id) {
  // TODO: update job status to rejected
  console.log("Reject:", id);
  await refreshQueue();
}

// ─── Scan Controls ────────────────────────────────────

document.getElementById("btn-scan-all").addEventListener("click", async () => {
  const platform = document.getElementById("scan-platform").value;
  const statusEl = document.getElementById("scan-status");
  statusEl.textContent = `Scanning ${platform}...`;
  document.getElementById("btn-scan-all").disabled = true;

  try {
    const resp = await chrome.runtime.sendMessage({ type: "scan_now", platform });
    if (resp.success) {
      const results = Array.isArray(resp.result) ? resp.result : [resp.result];
      const total = results.reduce((s, r) => s + (r.scanned || 0), 0);
      const passed = results.reduce((s, r) => s + (r.passed || 0), 0);
      statusEl.textContent = `Done: ${total} scanned, ${passed} passed gates`;
    } else {
      statusEl.textContent = `Error: ${resp.error}`;
    }
  } catch (e) {
    statusEl.textContent = `Error: ${e.message}`;
  }

  document.getElementById("btn-scan-all").disabled = false;
  await refreshStatus();
  await refreshQueue();
});

// ─── Live Updates ─────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "scan_complete") {
    refreshStatus();
    refreshQueue();
  }
  if (msg.type === "phase_change") {
    refreshStatus();
  }
  if (msg.type === "job_applied") {
    refreshStatus();
    refreshQueue();
  }
});

// ─── Helpers ──────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str || "";
  return div.innerHTML;
}

// ─── Start ────────────────────────────────────────────

init();
```

- [ ] **Step 3: Commit**

```bash
git add extension/sidepanel.html extension/sidepanel.js
git commit -m "feat(ext): rebuild side panel — dashboard, phase status, queue, scan controls"
```

---

## Task 12: Update Runner CLI + Install Script

**Files:**
- Modify: `jobpulse/runner.py`

- [ ] **Step 1: Replace ext-bridge command with api-server and add install-native-host**

In `jobpulse/runner.py`, replace the `ext-bridge` command block (lines ~318-334) with:

```python
    elif command == "api-server":
        import uvicorn
        logger.info("Starting JobPulse API server on http://0.0.0.0:8000")
        uvicorn.run("mindgraph_app.main:app", host="0.0.0.0", port=8000, reload=True)

    elif command == "install-native-host":
        import json
        import os
        import stat
        host_manifest = {
            "name": "com.jobpulse.brain",
            "description": "JobPulse Python backend bootstrap",
            "path": os.path.abspath("jobpulse/native_host.py"),
            "type": "stdio",
            "allowed_origins": [],
        }
        # Prompt for extension ID
        ext_id = input("Enter your Chrome extension ID (from chrome://extensions): ").strip()
        if ext_id:
            host_manifest["allowed_origins"] = [f"chrome-extension://{ext_id}/"]

        # Save manifest
        nm_dir = os.path.expanduser(
            "~/Library/Application Support/Google/Chrome/NativeMessagingHosts"
        )
        os.makedirs(nm_dir, exist_ok=True)
        manifest_path = os.path.join(nm_dir, "com.jobpulse.brain.json")
        with open(manifest_path, "w") as f:
            json.dump(host_manifest, f, indent=2)
        logger.info("Native Messaging host manifest written to %s", manifest_path)

        # Make native_host.py executable
        host_script = os.path.abspath("jobpulse/native_host.py")
        st = os.stat(host_script)
        os.chmod(host_script, st.st_mode | stat.S_IEXEC)
        logger.info("Made %s executable", host_script)
        print("Done. Restart Chrome to pick up the native host.")
```

- [ ] **Step 2: Commit**

```bash
git add jobpulse/runner.py
git commit -m "feat: add api-server + install-native-host commands to runner CLI

Replaces ext-bridge command. api-server starts FastAPI on :8000.
install-native-host registers Native Messaging manifest in Chrome."
```

---

## Task 13: Update Tests for New Architecture

**Files:**
- Modify: `tests/jobpulse/test_phase3_wiring.py`, `tests/jobpulse/test_safe_io.py`
- Delete: tests referencing Playwright adapters

- [ ] **Step 1: Update test_phase3_wiring.py for extension-only routing**

The test should verify `get_adapter()` always returns `ExtensionAdapter`:

```python
# tests/jobpulse/test_phase3_wiring.py
"""Test that adapter routing always returns ExtensionAdapter."""

from unittest.mock import patch, MagicMock
from jobpulse.ats_adapters import get_adapter
from jobpulse.ext_adapter import ExtensionAdapter


def test_get_adapter_returns_extension_adapter():
    """get_adapter() always returns ExtensionAdapter regardless of platform."""
    with patch("jobpulse.ats_adapters.ExtensionBridge") as MockBridge:
        mock_bridge = MagicMock()
        MockBridge.return_value = mock_bridge
        # Clear cached instance
        if hasattr(get_adapter, "_instance"):
            del get_adapter._instance

        adapter = get_adapter("linkedin")
        assert isinstance(adapter, ExtensionAdapter)

        adapter2 = get_adapter("greenhouse")
        assert adapter is adapter2  # singleton


def test_get_adapter_singleton():
    """get_adapter() returns the same instance on subsequent calls."""
    with patch("jobpulse.ats_adapters.ExtensionBridge"):
        if hasattr(get_adapter, "_instance"):
            del get_adapter._instance
        a1 = get_adapter()
        a2 = get_adapter()
        assert a1 is a2
```

- [ ] **Step 2: Remove Playwright test functions from test_safe_io.py**

Remove any test functions that reference `managed_browser` or `managed_persistent_browser`. Keep tests for `safe_openai_call`, `locked_json_file`, `atomic_sqlite`.

- [ ] **Step 3: Run all tests**

```bash
python -m pytest tests/ -x --tb=short -q 2>&1 | tail -30
```

Fix any remaining failures from Playwright removal.

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: update tests for extension-only architecture

Remove Playwright adapter tests, update routing tests for
ExtensionAdapter singleton, clean up safe_io tests."
```

---

## Task 14: Update Documentation

**Files:**
- Modify: `CLAUDE.md`, `.claude/rules/jobs.md`, `.claude/mistakes.md`

- [ ] **Step 1: Update CLAUDE.md**

In the Architecture table, change:
- Chrome Extension Engine description to: "MV3 extension — autonomous scanning + applying via Alarms API + HTTP API to Python backend"
- Remove "APPLICATION_ENGINE=extension" env var docs (it's the only mode now)
- Update runner commands: replace `ext-bridge` with `api-server`, add `install-native-host`

- [ ] **Step 2: Update .claude/rules/jobs.md**

Remove Application Engine Modes section (no more Playwright mode). Update to reflect extension-only architecture. Add phase system documentation.

- [ ] **Step 3: Add mistake entry**

Add to `.claude/mistakes.md`:

```markdown
### [2026-04-05] Playwright pipeline abandoned — extension-only architecture
- **Cause**: Playwright adapters stopped working, detection risk too high.
- **Fix**: Full migration to Chrome extension-driven pipeline with Native Messaging bootstrap + HTTP API.
- **Rule**: NEVER add Playwright back. All browser automation goes through the Chrome extension.
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/jobs.md .claude/mistakes.md
git commit -m "docs: update for extension-only architecture

Remove Playwright references, document new api-server command,
add Native Messaging setup, document 4-phase trust system."
```

---

## Task 15: Reindex Code Graph

- [ ] **Step 1: Reindex after all changes**

```bash
python -c "
from shared.code_intelligence import CodeIntelligence
ci = CodeIntelligence('data/code_intelligence.db')
ci.full_reindex()
stats = ci.get_stats()
print(f'Nodes: {stats[\"nodes\"]}  Edges: {stats[\"edges\"]}  Files: {stats[\"files\"]}')
"
```

- [ ] **Step 2: Verify deleted files are gone from index**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/code_intelligence.db')
results = conn.execute(\"SELECT DISTINCT file_path FROM nodes WHERE file_path LIKE '%browser_manager%' OR file_path LIKE '%ats_adapters/linkedin%'\").fetchall()
print('Should be empty:', results)
conn.close()
"
```

Expected: empty list.

- [ ] **Step 3: Check new files are indexed**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/code_intelligence.db')
for f in ['jobpulse/job_api.py', 'jobpulse/native_host.py']:
    count = conn.execute('SELECT COUNT(*) FROM nodes WHERE file_path = ?', (f,)).fetchone()[0]
    print(f'{f}: {count} nodes')
conn.close()
"
```

- [ ] **Step 4: Commit**

```bash
git add data/code_intelligence.db
git commit -m "chore: reindex code graph after extension pipeline migration"
```

---

## Task 16: Integration Smoke Test

- [ ] **Step 1: Start the API server**

```bash
python -m jobpulse.runner api-server &
sleep 3
```

- [ ] **Step 2: Test health endpoint**

```bash
curl -s http://localhost:8000/api/job/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Test evaluate endpoint**

```bash
curl -s -X POST http://localhost:8000/api/job/evaluate \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/job/1","title":"Junior Data Scientist","company":"Acme","platform":"linkedin","jd_text":"We need a data scientist with Python, SQL, and machine learning experience. UK based, entry level."}'
```

Expected: JSON with `passed`, `tier`, `score` fields.

- [ ] **Step 4: Test scan-reed endpoint**

```bash
curl -s -X POST http://localhost:8000/api/job/scan-reed \
  -H "Content-Type: application/json" \
  -d '{"titles":["data scientist"],"location":"United Kingdom"}'
```

Expected: JSON with `jobs` array and `count`.

- [ ] **Step 5: Load extension in Chrome**

1. Open `chrome://extensions`
2. Enable Developer mode
3. Click "Load unpacked" → select `extension/` directory
4. Note the extension ID
5. Run: `python -m jobpulse.runner install-native-host`
6. Enter the extension ID when prompted
7. Restart Chrome

- [ ] **Step 6: Verify side panel**

1. Click the extension icon → open side panel
2. Should show "connected" badge (if API server is running)
3. Click "Scan All Now" → should show scan results

- [ ] **Step 7: Commit final state**

```bash
git add -A
git commit -m "test: integration smoke test passed — extension pipeline operational"
```
