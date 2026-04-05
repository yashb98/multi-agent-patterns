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
