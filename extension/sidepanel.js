// extension/sidepanel.js

const connStatus = document.getElementById("conn-status");
const logEntries = document.getElementById("log-entries");
const appCompany = document.getElementById("app-company");
const appRole = document.getElementById("app-role");
const appState = document.getElementById("app-state");
const progressFill = document.getElementById("progress-fill");
const intelBody = document.getElementById("intel-body");

function setConnectionStatus(state) {
  connStatus.className = "badge " + state;
  connStatus.textContent = state === "connected" ? "Connected" :
                           state === "connecting" ? "Connecting..." : "Disconnected";
}

function addLogEntry(label, value, tier, confident) {
  const entry = document.createElement("div");
  entry.className = "log-entry" + (confident ? "" : " uncertain");
  const tierLabels = { 1: "Pattern", 2: "Nano", 3: "LLM", 4: "Vision" };
  entry.innerHTML = `
    <span class="entry-icon">${confident ? "+" : "?"}</span>
    <span class="entry-label">${escapeHtml(label)}</span>
    <span class="entry-value">${escapeHtml(value.substring(0, 60))}</span>
    <span class="entry-tier">${tierLabels[tier] || "?"}</span>
  `;
  logEntries.prepend(entry);
}

function showCompanyIntel(research) {
  document.getElementById("company-intel").classList.remove("hidden");
  const tech = (research.tech_stack || []).join(", ") || "N/A";
  const flags = (research.red_flags || []).join(", ") || "None";
  intelBody.innerHTML = `
    <p><strong>${escapeHtml(research.company)}</strong></p>
    <p>${escapeHtml(research.description || "")}</p>
    <p><em>${escapeHtml(research.industry || "")} | ${escapeHtml(research.size || "")}</em></p>
    <p>Tech: ${escapeHtml(tech)}</p>
    <p>Red flags: ${escapeHtml(flags)}</p>
  `;
}

function setApplicationState(state, progress) {
  document.getElementById("current-app").classList.remove("hidden");
  appState.textContent = state;
  if (progress !== undefined) {
    progressFill.style.width = progress + "%";
  }
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Listen for updates from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "status") {
    setConnectionStatus(msg.state);
  }
  if (msg.type === "snapshot_update") {
    // Could update field count, etc.
  }
  if (msg.type === "field_filled") {
    addLogEntry(msg.label, msg.value, msg.tier, msg.confident);
  }
  if (msg.type === "application_start") {
    appCompany.textContent = msg.company || "";
    appRole.textContent = msg.role || "";
    logEntries.innerHTML = "";
    if (msg.company_research) showCompanyIntel(msg.company_research);
    setApplicationState("Starting", 0);
  }
  if (msg.type === "application_complete") {
    setApplicationState(msg.success ? "Complete" : "Failed", 100);
  }
});

// Get initial status
chrome.runtime.sendMessage({ type: "status" }, (resp) => {
  if (resp && resp.state) setConnectionStatus(resp.state);
});
