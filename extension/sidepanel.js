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
