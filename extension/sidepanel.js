// extension/sidepanel.js — Dashboard control center
//
// Renders: phase status, scan controls, job queue, daily stats, rate limits.
// Communicates with background.js via chrome.runtime.sendMessage.

import { DAILY_APPLY_LIMITS } from './config.js';

const PLATFORMS = ["linkedin", "reed", "indeed", "greenhouse", "lever", "workday"];

// ═══════════════════════════════════════════════════════════════
// DOM references
// ═══════════════════════════════════════════════════════════════

const $ = (id) => document.getElementById(id);
const backendDot = $("backend-status");
const wsStatus = $("ws-status");
const phaseGrid = $("phase-grid");
const scanControls = $("scan-controls");
const jobList = $("job-list");
const statsEl = $("stats");
const limitsEl = $("limits");

// ═══════════════════════════════════════════════════════════════
// Render functions
// ═══════════════════════════════════════════════════════════════

function renderPhaseStatus(phases) {
  if (!phases) { phaseGrid.innerHTML = '<div class="empty">Loading...</div>'; return; }
  phaseGrid.innerHTML = PLATFORMS.map(p => {
    const info = phases[p] || { current: "observation" };
    const phase = info.current || "observation";
    return `<div class="phase-badge">
      <div class="platform">${p}</div>
      <span class="phase ${phase}">${phase.replace("_", " ")}</span>
    </div>`;
  }).join("");
}

function renderScanControls() {
  scanControls.innerHTML = PLATFORMS.map(p =>
    `<button class="scan-btn" data-platform="${p}">Scan ${p}</button>`
  ).join("");
  scanControls.querySelectorAll(".scan-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      btn.disabled = true;
      btn.textContent = "Scanning...";
      chrome.runtime.sendMessage({ type: "scan_now", platform: btn.dataset.platform }, (resp) => {
        btn.disabled = false;
        btn.textContent = `Scan ${btn.dataset.platform}`;
        if (resp?.error) console.error("[JobPulse] Scan error:", resp.error);
        refresh();
      });
    });
  });
}

function renderJobQueue(jobs) {
  if (!jobs || jobs.length === 0) {
    jobList.innerHTML = '<div class="empty">No jobs in queue</div>';
    return;
  }
  jobList.innerHTML = jobs.map(j => {
    const score = j.gate_results?.score?.toFixed(0) || "--";
    const tier = j.gate_results?.tier || "--";
    const actions = getActionsForJob(j);
    return `<div class="job-card" data-id="${j.id}">
      <div class="title">${esc(j.title || "Untitled")}</div>
      <div class="company">${esc(j.company || "Unknown")}</div>
      <div class="meta">
        <span class="platform-tag">${j.platform || "?"}</span>
        <span class="score">Score: ${score}</span>
        <span>${tier}</span>
        <span>${j.apply_status || "pending"}</span>
      </div>
      ${actions ? `<div class="actions">${actions}</div>` : ""}
    </div>`;
  }).join("");

  // Wire action buttons
  jobList.querySelectorAll("[data-action]").forEach(btn => {
    btn.addEventListener("click", () => handleJobAction(btn.dataset.action, parseInt(btn.dataset.jobId)));
  });
}

function getActionsForJob(job) {
  const id = job.id;
  switch (job.apply_status) {
    case "pending":
    case "ready":
      return `<button class="btn-approve" data-action="approve" data-job-id="${id}">Approve</button>
              <button class="btn-reject" data-action="reject" data-job-id="${id}">Reject</button>`;
    case "observing":
      return `<button class="btn-review" data-action="review" data-job-id="${id}">Review Mapping</button>`;
    case "dry_ran":
      return `<button class="btn-review" data-action="review" data-job-id="${id}">View Results</button>`;
    default:
      return "";
  }
}

function renderStats(stats) {
  if (!stats) {
    statsEl.innerHTML = ["Scanned", "Passed", "Applied", "Errors"].map(l =>
      `<div class="stat-box"><div class="value">0</div><div class="label">${l}</div></div>`
    ).join("");
    return;
  }
  const items = [
    { value: stats.total || 0, label: "Scanned" },
    { value: stats.by_status?.ready || 0, label: "Passed" },
    { value: stats.by_status?.applied || 0, label: "Applied" },
    { value: stats.by_status?.error || 0, label: "Errors" },
  ];
  statsEl.innerHTML = items.map(i =>
    `<div class="stat-box"><div class="value">${i.value}</div><div class="label">${i.label}</div></div>`
  ).join("");
}

function renderDailyLimits(stats) {
  limitsEl.innerHTML = PLATFORMS.map(p => {
    const max = DAILY_APPLY_LIMITS[p] || 5;
    const applied = stats?.by_platform?.[p]?.applied || 0;
    const pct = Math.min((applied / max) * 100, 100);
    const cls = pct > 80 ? "high" : pct > 50 ? "mid" : "low";
    return `<div class="limit-bar">
      <div class="bar-label"><span>${p}</span><span>${applied}/${max}</span></div>
      <div class="bar"><div class="fill ${cls}" style="width:${pct}%"></div></div>
    </div>`;
  }).join("");
}

// ═══════════════════════════════════════════════════════════════
// Actions
// ═══════════════════════════════════════════════════════════════

function handleJobAction(action, jobId) {
  if (action === "approve") {
    chrome.runtime.sendMessage({ type: "approve_job", jobId }, () => refresh());
  } else if (action === "reject") {
    chrome.runtime.sendMessage({ type: "reject_job", jobId }, () => refresh());
  }
}

// ═══════════════════════════════════════════════════════════════
// Data loading
// ═══════════════════════════════════════════════════════════════

async function refresh() {
  // Backend health
  chrome.runtime.sendMessage({ type: "status" }, (resp) => {
    if (resp?.state === "connected") {
      backendDot.className = "status-dot online";
      wsStatus.textContent = "Connected";
    } else {
      backendDot.className = "status-dot offline";
      wsStatus.textContent = resp?.state || "Offline";
    }
  });

  // Phase status
  chrome.runtime.sendMessage({ type: "get_phases" }, (resp) => {
    renderPhaseStatus(resp?.phases);
  });

  // Job stats
  chrome.runtime.sendMessage({ type: "get_job_stats" }, (resp) => {
    renderStats(resp?.stats);
    renderDailyLimits(resp?.stats);
  });

  // Job queue — load from IndexedDB via background
  // For now render stats-based view; full queue needs direct IndexedDB access
}

// ═══════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════

function esc(str) {
  const d = document.createElement("div");
  d.textContent = str || "";
  return d.innerHTML;
}

// ═══════════════════════════════════════════════════════════════
// Listen for live updates from background
// ═══════════════════════════════════════════════════════════════

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "status") {
    const connected = msg.state === "connected";
    backendDot.className = `status-dot ${connected ? "online" : "offline"}`;
    wsStatus.textContent = msg.state;
  }
  if (msg.type === "scan_complete" || msg.type === "job_applied") {
    refresh();
  }
});

// ═══════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════

document.addEventListener("DOMContentLoaded", () => {
  renderScanControls();
  refresh();
  setInterval(refresh, 10000); // Refresh every 10s
});
