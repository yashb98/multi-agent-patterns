// extension/background.js — MV3 Service Worker
//
// Responsibilities:
//   1. Maintain WebSocket connection to Python backend (ws://localhost:8765)
//   2. Relay commands from Python → content script (fill, click, upload, etc.)
//   3. Relay snapshots from content script → Python (navigation, mutation events)
//   4. Handle browser-level actions (navigate, screenshot, close_tab)
//
// MV3 Service Worker Lifecycle:
//   - Service workers go idle after 30s of inactivity
//   - Chrome 116+ keeps workers alive during active WebSocket connections
//   - Heartbeat (20s ping) keeps both the WebSocket and worker alive
//   - Auto-reconnect (3s retry) handles disconnects and bridge restarts

// ═══════════════════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════════════════

let ws = null;
let connectionState = "disconnected"; // "disconnected" | "connecting" | "connected"
const WS_URL = "ws://localhost:8765";

// Track the tab we're actively working on — when an apply link opens a new tab,
// we auto-adopt it so getActiveTab() follows the navigation seamlessly.
let trackedTabId = null;

// ═══════════════════════════════════════════════════════════════
// New-tab follower — catches apply links that open in new tabs
// ═══════════════════════════════════════════════════════════════

chrome.tabs.onCreated.addListener(async (newTab) => {
  // Only follow new tabs when we're connected to Python (i.e. actively automating)
  if (connectionState !== "connected" || !trackedTabId) return;

  // Small delay — pendingUrl may not be set instantly
  await new Promise((r) => setTimeout(r, 300));

  // Re-query to get the updated tab info (pendingUrl, openerTabId)
  try {
    const tab = await chrome.tabs.get(newTab.id);
    const url = tab.pendingUrl || tab.url || "";

    // Only adopt tabs opened by our tracked tab (openerTabId) or if URL looks like an ATS
    const isFromTracked = tab.openerTabId === trackedTabId;
    const isAtsUrl = /greenhouse|lever|workday|ashby|smartrecruiters|icims|successfactors|taleo|bamboohr|jazz|breezy|recruitee|applytojob|jobs\.jobvite/i.test(url);

    if (isFromTracked || isAtsUrl) {
      console.log(`[JobPulse] New tab detected (from tracked=${isFromTracked}, ats=${isAtsUrl}): ${url.substring(0, 80)}`);
      // Switch focus to the new tab and start tracking it
      trackedTabId = tab.id;
      await chrome.tabs.update(tab.id, { active: true });
      // Ensure content script is injected after page loads
      chrome.tabs.onUpdated.addListener(function injectOnLoad(tabId, info) {
        if (tabId === tab.id && info.status === "complete") {
          chrome.tabs.onUpdated.removeListener(injectOnLoad);
          ensureContentScript(tab.id).catch(() => {});
        }
      });
    }
  } catch (e) {
    // Tab may have been closed already
    console.warn("[JobPulse] Failed to inspect new tab:", e.message);
  }
});

// ═══════════════════════════════════════════════════════════════
// Heartbeat — keeps service worker + WebSocket alive
// ═══════════════════════════════════════════════════════════════

let heartbeatInterval = null;

function startHeartbeat() {
  stopHeartbeat();
  heartbeatInterval = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 20_000); // 20s — within MV3's 30s idle timeout
}

function stopHeartbeat() {
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval);
    heartbeatInterval = null;
  }
}

// ═══════════════════════════════════════════════════════════════
// Connection management — connect, disconnect, auto-reconnect
// ═══════════════════════════════════════════════════════════════

let reconnectTimer = null;

/**
 * Schedule a reconnection attempt in 3 seconds.
 * Called automatically on disconnect or error.
 */
function scheduleReconnect() {
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    if (connectionState !== "connected") {
      console.log("[JobPulse] Retrying connection...");
      connect();
    }
  }, 3000);
}

/**
 * Open a WebSocket connection to the Python bridge.
 * Closes any existing connection first. On failure, schedules a retry.
 */
function connect() {
  // Prevent concurrent connect() calls (onInstalled + startup + alarm can race)
  if (connectionState === "connecting" || connectionState === "connected") return;

  if (ws) {
    try { ws.close(); } catch (_) { /* ignore close errors */ }
    ws = null;
  }

  connectionState = "connecting";
  broadcastStatus();
  console.log("[JobPulse] Connecting to", WS_URL);

  const socket = new WebSocket(WS_URL);
  ws = socket;

  socket.onopen = () => {
    if (ws !== socket) return; // Stale — another connect() replaced us
    connectionState = "connected";
    broadcastStatus();
    startHeartbeat();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    socket.send(JSON.stringify({ type: "extension_hello" }));
    console.log("[JobPulse] Connected to Python backend");
  };

  socket.onclose = () => {
    if (ws === socket) ws = null;
    connectionState = "disconnected";
    broadcastStatus();
    stopHeartbeat();
    console.log("[JobPulse] Disconnected — will retry in 3s");
    scheduleReconnect();
  };

  socket.onerror = (err) => {
    console.error("[JobPulse] WebSocket error:", err);
    if (ws === socket) ws = null;
    connectionState = "disconnected";
    broadcastStatus();
    scheduleReconnect();
  };

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (_) {
      console.error("[JobPulse] Invalid JSON from Python:", event.data);
      return;
    }

    if (msg.type === "pong") return; // Heartbeat response
    if (msg.action) handlePythonCommand(msg); // Command dispatch
  };
}

/**
 * Manually disconnect. Cancels auto-reconnect.
 * Only called from popup "Disconnect" button.
 */
function disconnect() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  if (ws) { ws.close(); ws = null; }
  connectionState = "disconnected";
  broadcastStatus();
  stopHeartbeat();
}

// ═══════════════════════════════════════════════════════════════
// Command dispatch — Python → Extension
// ═══════════════════════════════════════════════════════════════

/**
 * Handle a command from the Python backend.
 *
 * Flow: Python sends {id, action, payload} → we ack immediately →
 * execute action → send {id, type:"result", payload} back.
 *
 * Actions handled in background.js (require chrome.* APIs):
 *   navigate, screenshot, get_snapshot, close_tab
 *
 * All other actions (fill, click, upload, select, check, analyze_field)
 * are forwarded to the content script via chrome.tabs.sendMessage.
 */
async function handlePythonCommand(cmd) {
  const { id, action, payload } = cmd;
  sendToPython({ id, type: "ack", payload: {} });

  try {
    // --- Navigate: update active tab URL, wait for content script snapshot ---
    if (action === "navigate") {
      const navTab = await getActiveTab();
      trackedTabId = navTab.id;  // Start tracking this tab for new-tab detection
      await handleNavigate(id, payload.url);
      return;
    }

    // --- Real click via DevTools Protocol (chrome.debugger) ---
    // Moves the mouse along a Bezier curve to (x, y), then clicks.
    // Uses real Input.dispatchMouseEvent for every curve point.
    if (action === "real_click") {
      const tab = await getActiveTab();
      const { x, y, fromX, fromY } = payload;
      try {
        await chrome.debugger.attach({ tabId: tab.id }, "1.3");

        // Starting point: last known position, or random viewport edge
        const startX = fromX ?? (Math.random() * 200 + 50);
        const startY = fromY ?? (Math.random() * 200 + 50);

        // Generate Bezier curve points for human-like trajectory
        const points = _bezierCurve(startX, startY, x, y, 14 + Math.floor(Math.random() * 6));

        // Move along the curve with varying speed (slow start, fast middle, slow end)
        for (let i = 0; i < points.length; i++) {
          await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchMouseEvent", {
            type: "mouseMoved", x: points[i].x, y: points[i].y, button: "none",
          });
          const t = i / points.length;
          const delayMs = 6 + 18 * (1 - Math.abs(2 * t - 1)) + Math.random() * 4;
          await new Promise(r => setTimeout(r, delayMs));
        }

        // Final move to exact target
        await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchMouseEvent", {
          type: "mouseMoved", x, y, button: "none",
        });
        await new Promise(r => setTimeout(r, 30 + Math.random() * 40));

        // Click with human-like press/release timing
        await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchMouseEvent", {
          type: "mousePressed", x, y, button: "left", clickCount: 1,
        });
        await new Promise(r => setTimeout(r, 40 + Math.random() * 60));
        await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchMouseEvent", {
          type: "mouseReleased", x, y, button: "left", clickCount: 1,
        });

        await chrome.debugger.detach({ tabId: tab.id });
        sendToPython({ id, type: "result", payload: { success: true } });
      } catch (err) {
        try { await chrome.debugger.detach({ tabId: tab.id }); } catch (_) {}
        sendToPython({ id, type: "result", payload: { success: false, error: err.message } });
      }
      return;
    }

    // --- Real type via DevTools Protocol ---
    if (action === "real_type") {
      const tab = await getActiveTab();
      const { text } = payload;
      try {
        await chrome.debugger.attach({ tabId: tab.id }, "1.3");
        for (const char of text) {
          await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchKeyEvent", {
            type: "keyDown", text: char, key: char, code: `Key${char.toUpperCase()}`,
          });
          await chrome.debugger.sendCommand({ tabId: tab.id }, "Input.dispatchKeyEvent", {
            type: "keyUp", key: char, code: `Key${char.toUpperCase()}`,
          });
          await new Promise(r => setTimeout(r, 50 + Math.random() * 80));
        }
        await chrome.debugger.detach({ tabId: tab.id });
        sendToPython({ id, type: "result", payload: { success: true } });
      } catch (err) {
        try { await chrome.debugger.detach({ tabId: tab.id }); } catch (_) {}
        sendToPython({ id, type: "result", payload: { success: false, error: err.message } });
      }
      return;
    }

    // --- Screenshot: capture visible tab as PNG ---
    if (action === "screenshot") {
      const tab = await getActiveTab();
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
      const base64 = dataUrl.replace(/^data:image\/png;base64,/, "");
      sendToPython({ id, type: "result", payload: { success: true, data: base64 } });
      return;
    }

    // --- Element screenshot: capture tab + crop to element bounds ---
    if (action === "element_screenshot") {
      const tab = await getActiveTab();
      // Step 1: Get element bounds from content script
      const bounds = await chrome.tabs.sendMessage(tab.id, {
        id, action: "element_bounds", payload: { selector: payload.selector },
      }, { frameId: 0 });
      if (!bounds || !bounds.success) {
        sendToPython({ id, type: "result", payload: { success: false, error: "Element not found: " + payload.selector } });
        return;
      }
      // Step 2: Capture full tab
      const dataUrl2 = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
      const fullBase64 = dataUrl2.replace(/^data:image\/png;base64,/, "");
      // Step 3: Crop using OffscreenCanvas
      const b = bounds.bounds;
      const imgBlob = await fetch(dataUrl2).then(r => r.blob());
      const imgBitmap = await createImageBitmap(imgBlob);
      const canvas = new OffscreenCanvas(b.width, b.height);
      const ctx = canvas.getContext("2d");
      ctx.drawImage(imgBitmap, b.x, b.y, b.width, b.height, 0, 0, b.width, b.height);
      const croppedBlob = await canvas.convertToBlob({ type: "image/png" });
      const arrayBuf = await croppedBlob.arrayBuffer();
      const croppedBase64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuf)));
      sendToPython({ id, type: "result", payload: { success: true, data: croppedBase64 } });
      return;
    }

    // --- Get snapshot: request fresh page scan from content script ---
    if (action === "get_snapshot") {
      const tab = await getActiveTab();
      const response = await chrome.tabs.sendMessage(tab.id, { id, action: "get_snapshot", payload: {} }, { frameId: 0 });
      sendToPython({ id, type: "result", payload: response || { success: false, error: "No response" } });
      return;
    }

    // --- Close tab ---
    if (action === "close_tab") {
      const tab = await getActiveTab();
      await chrome.tabs.remove(tab.id);
      sendToPython({ id, type: "result", payload: { success: true } });
      return;
    }

    // --- All other actions: forward to content script ---
    const tab = await getActiveTab();
    const response = await chrome.tabs.sendMessage(tab.id, { id, action, payload }, { frameId: 0 });
    sendToPython({
      id,
      type: "result",
      payload: response || { success: false, error: "No response from content script" },
    });
  } catch (err) {
    sendToPython({ id, type: "error", payload: { success: false, error: err.message } });
  }
}

/**
 * Navigate to a URL and return a page snapshot.
 *
 * Strategy (3 sources, first one wins):
 *   1. Content script fires a "navigation" event after window.load
 *   2. chrome.tabs.onUpdated fires "complete" → we request get_snapshot
 *   3. Timeout fallback (15s) → send null snapshot
 *
 * The service worker may restart during navigation (MV3 lifecycle).
 * The Python bridge handles this by polling for reconnection.
 */
async function handleNavigate(cmdId, url) {
  const tab = await getActiveTab();

  // Respond BEFORE navigating — chrome.tabs.update() can restart the
  // MV3 service worker, killing the WebSocket and dropping any response
  // sent after navigation starts.
  sendToPython({ id: cmdId, type: "result", payload: { success: true, snapshot: null } });

  // Now trigger navigation (may restart service worker)
  await chrome.tabs.update(tab.id, { url });

  // Best-effort: push snapshot to Python once page loads (passive update)
  let pushed = false;

  const navListener = (msg) => {
    if (!pushed && msg.type === "navigation" && msg.payload?.snapshot) {
      pushed = true;
      chrome.runtime.onMessage.removeListener(navListener);
      // Send as a passive navigation event — not tied to cmdId
      sendToPython({ id: "", type: "navigation", payload: msg.payload });
    }
  };
  chrome.runtime.onMessage.addListener(navListener);

  const tabListener = async (tabId, changeInfo) => {
    if (tabId === tab.id && changeInfo.status === "complete" && !pushed) {
      chrome.tabs.onUpdated.removeListener(tabListener);
      await new Promise((r) => setTimeout(r, 1500)); // Let DOM settle
      if (pushed) return;
      try {
        await ensureContentScript(tab.id);
        const snapshot = await chrome.tabs.sendMessage(tab.id, {
          id: cmdId, action: "get_snapshot", payload: {},
        }, { frameId: 0 });
        if (!pushed && snapshot) {
          pushed = true;
          chrome.runtime.onMessage.removeListener(navListener);
          sendToPython({ id: "", type: "navigation", payload: { snapshot } });
        }
      } catch (_) {
        // Content script not ready — Python will poll via get_snapshot
      }
    }
  };
  chrome.tabs.onUpdated.addListener(tabListener);

  // Cleanup listeners after 20s to prevent memory leaks
  setTimeout(() => {
    if (!pushed) {
      chrome.runtime.onMessage.removeListener(navListener);
      chrome.tabs.onUpdated.removeListener(tabListener);
    }
  }, 20_000);
}

// ═══════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════

/** Send a JSON message to the Python backend. */
function sendToPython(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

/** Get the currently active tab. Prefers the tracked tab if it still exists. */
async function getActiveTab() {
  // If we're tracking a specific tab (e.g. after a new-tab-follow), use it
  if (trackedTabId) {
    try {
      const tab = await chrome.tabs.get(trackedTabId);
      if (tab) return tab;
    } catch (_) {
      // Tab was closed — fall through to query
      trackedTabId = null;
    }
  }
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("No active tab");
  trackedTabId = tab.id;
  return tab;
}

/**
 * Ensure the content script is injected in the active tab.
 * After extension reload, existing tabs lose their content scripts.
 * This re-injects content.js if it's not already running.
 */
async function ensureContentScript(tabId) {
  try {
    // Lightweight ping — do NOT use get_snapshot (too slow on complex pages).
    // Just check if the content script's message listener is alive.
    await Promise.race([
      chrome.tabs.sendMessage(tabId, { action: "ping", payload: {} }, { frameId: 0 }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("ping timeout")), 3000)),
    ]);
  } catch (_) {
    // Content script not loaded or stale — inject fresh copy
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ["content.js"],
      });
      // Wait for it to initialize
      await new Promise((r) => setTimeout(r, 500));
      console.log("[JobPulse] Content script injected into tab", tabId);
    } catch (e) {
      console.error("[JobPulse] Cannot inject content script:", e.message);
    }
  }
}

/** Broadcast connection state to popup and sidepanel. */
function broadcastStatus() {
  chrome.runtime.sendMessage({ type: "status", state: connectionState }).catch(() => {});
}

// ═══════════════════════════════════════════════════════════════
// Internal message relay — content script / popup / sidepanel
// ═══════════════════════════════════════════════════════════════

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Popup: status query
  if (msg.type === "status") {
    sendResponse({ state: connectionState });
    return true;
  }

  // Popup: manual connect/disconnect
  if (msg.type === "connect") { connect(); sendResponse({ ok: true }); return true; }
  if (msg.type === "disconnect") { disconnect(); sendResponse({ ok: true }); return true; }

  // Content script: forward page events to Python
  if (msg.type === "snapshot" || msg.type === "mutation" || msg.type === "navigation") {
    sendToPython({ id: msg.id || "", type: msg.type, payload: msg.payload || {} });
    chrome.runtime.sendMessage({ type: "snapshot_update", payload: msg.payload }).catch(() => {});
    return false;
  }

  return false;
});

// ═══════════════════════════════════════════════════════════════
// Bezier curve — shared by real_click for human-like mouse movement
// ═══════════════════════════════════════════════════════════════

/**
 * Generate points along a cubic Bezier curve with randomized control
 * points. Creates natural-looking mouse trajectories with curvature
 * and slight overshoot — identical algorithm to content.js visual cursor.
 */
function _bezierCurve(x0, y0, x1, y1, steps = 18) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const distance = Math.sqrt(dx * dx + dy * dy);

  const perpX = -dy / (distance || 1);
  const perpY = dx / (distance || 1);
  const curvature = (Math.random() - 0.5) * distance * 0.3;
  const overshoot = 1.0 + (Math.random() * 0.08 - 0.02);

  const cp1x = x0 + dx * 0.3 + perpX * curvature;
  const cp1y = y0 + dy * 0.3 + perpY * curvature;
  const cp2x = x0 + dx * 0.7 * overshoot + perpX * curvature * 0.3;
  const cp2y = y0 + dy * 0.7 * overshoot + perpY * curvature * 0.3;

  const points = [];
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const u = 1 - t;
    const x = u*u*u*x0 + 3*u*u*t*cp1x + 3*u*t*t*cp2x + t*t*t*x1;
    const y = u*u*u*y0 + 3*u*u*t*cp1y + 3*u*t*t*cp2y + t*t*t*y1;
    points.push({ x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 });
  }
  return points;
}

// ═══════════════════════════════════════════════════════════════
// Startup — auto-connect on install, reload, and service worker wake
// ═══════════════════════════════════════════════════════════════

chrome.runtime.onInstalled.addListener(() => {
  console.log("[JobPulse] Extension installed/reloaded");
  // Create a persistent alarm to keep service worker alive (MV3 kills idle workers after 30s)
  chrome.alarms.create("ws-keepalive", { periodInMinutes: 0.4 }); // ~24s — well within 30s idle limit
  connect();
});

// Alarm handler: reconnect WS if dead, send keepalive if alive
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "ws-keepalive") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      console.log("[JobPulse] Alarm: WS dead, reconnecting...");
      connect();
    } else {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }
});

// Service worker woke up (may be fresh start or after idle timeout)
// Also ensure alarm exists (survives service worker restarts)
chrome.alarms.get("ws-keepalive", (existing) => {
  if (!existing) {
    chrome.alarms.create("ws-keepalive", { periodInMinutes: 0.4 });
  }
});
connect();

// Side panel: open on action click for dashboard access
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

// ═══════════════════════════════════════════════════════════════
// Extension-Driven Pipeline — Scanning, Alarms, Phase Engine
// ═══════════════════════════════════════════════════════════════

// Static imports — MV3 service workers forbid dynamic import()
import * as _scannerModule from './scanner.js';
import * as _jobQueueModule from './job_queue.js';
import * as _phaseModule from './phase_engine.js';
import * as _bridgeModule from './native_bridge.js';

function getScanner() {
  return _scannerModule;
}

function getJobQueue() {
  return _jobQueueModule;
}

function getPhaseEngine() {
  return _phaseModule;
}

function getNativeBridge() {
  return _bridgeModule;
}

// Initialize extension pipeline on install/startup
async function initPipeline() {
  try {
    const jq = await getJobQueue();
    await jq.initDB();
    console.log("[JobPulse] IndexedDB initialized");

    const pe = await getPhaseEngine();
    await pe.initPhases();
    console.log("[JobPulse] Phase engine initialized");

    const scanner = await getScanner();
    await scanner.registerScanAlarms();
    console.log("[JobPulse] Scan alarms registered");

    // Try to wake up Python backend
    const bridge = await getNativeBridge();
    const healthy = await bridge.isBackendHealthy();
    if (healthy) {
      console.log("[JobPulse] Python backend is running");
    } else {
      console.log("[JobPulse] Python backend not reachable — will bootstrap on demand");
    }
  } catch (e) {
    console.error("[JobPulse] Pipeline init error:", e.message);
  }
}

// Run pipeline init after WebSocket connect attempt
setTimeout(initPipeline, 2000);

// Chrome Alarms — scheduled scan triggers
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (!alarm.name.startsWith("jobpulse_scan:")) return;
  console.log("[JobPulse] Alarm fired:", alarm.name);
  try {
    const scanner = await getScanner();
    await scanner.handleScanAlarm(alarm.name);
  } catch (e) {
    console.error("[JobPulse] Scan alarm error:", e.message);
  }
});

// Side panel + popup message handler (extend existing listener)
const _origListener = chrome.runtime.onMessage.hasListeners;
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Scan Now — triggered from side panel
  if (msg.type === "scan_now") {
    (async () => {
      try {
        const scanner = await getScanner();
        const stats = await scanner.runScanCycle(msg.platform);
        sendResponse({ started: true, stats });
      } catch (e) {
        sendResponse({ started: false, error: e.message });
      }
    })();
    return true;
  }

  // Approve job — from side panel: check phase, mark approved, trigger apply
  if (msg.type === "approve_job") {
    (async () => {
      try {
        const jq = await getJobQueue();
        await jq.updateJob(msg.jobId, { apply_status: "approved" });

        // Retrieve job data for apply call
        const job = await jq.getJob(msg.jobId);
        if (!job || !job.url) {
          sendResponse({ ok: true, applied: false, error: "No URL for job" });
          return;
        }

        const platform = job.platform || "generic";

        // Phase engine gate: check if this platform can fill/submit
        const pe = await getPhaseEngine();
        const phase = await pe.getPhase(platform);
        const fillOk = await pe.canFill(platform);
        const atsScore = job.gate_results?.score ?? 0;
        const submitOk = await pe.canSubmit(platform, atsScore);

        if (!fillOk) {
          // observation phase — log only, don't apply
          console.log(`[JobPulse] Phase ${phase}: observation only for ${platform}, skipping apply`);
          sendResponse({ ok: true, applied: false, phase, error: `Phase ${phase}: observation only` });
          return;
        }

        // dry_run phase: fill forms but don't submit
        const dryRun = !submitOk;
        if (dryRun) {
          console.log(`[JobPulse] Phase ${phase}: dry_run mode for ${platform}`);
        }

        // Trigger application via Python backend HTTP API
        const bridge = await getNativeBridge();
        const result = await bridge.applyJob(
          job.url,
          platform,
          job.company || "",
          job.title || "",
          dryRun,
        );

        if (result.success) {
          await jq.markApplied(msg.jobId);
          // Record success in phase engine for graduation tracking
          await pe.recordCorrectMapping(platform);
          if (dryRun) {
            await pe.recordCleanDryRun(platform);
          }
          console.log(`[JobPulse] Applied to ${job.title} @ ${job.company} (phase=${phase}, dryRun=${dryRun})`);
        } else {
          await jq.markError(msg.jobId, result.error || "Apply failed");
          await pe.recordSubmissionError(platform);
          console.warn(`[JobPulse] Apply failed for ${job.title}: ${result.error}`);
        }

        // Check if platform should graduate after this attempt
        await pe.checkGraduation(platform);

        sendResponse({ ok: true, applied: result.success, phase, dryRun, error: result.error });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  // Reject job — from side panel
  if (msg.type === "reject_job") {
    (async () => {
      try {
        const jq = await getJobQueue();
        await jq.updateJob(msg.jobId, { apply_status: "rejected" });
        sendResponse({ ok: true });
      } catch (e) {
        sendResponse({ ok: false, error: e.message });
      }
    })();
    return true;
  }

  // Get phase status — from side panel
  if (msg.type === "get_phases") {
    (async () => {
      try {
        const pe = await getPhaseEngine();
        const phases = await pe.getAllPhases();
        sendResponse({ phases });
      } catch (e) {
        sendResponse({ phases: null, error: e.message });
      }
    })();
    return true;
  }

  // Get job queue stats — from side panel
  if (msg.type === "get_job_stats") {
    (async () => {
      try {
        const jq = await getJobQueue();
        const stats = await jq.getStats();
        sendResponse({ stats });
      } catch (e) {
        sendResponse({ stats: null, error: e.message });
      }
    })();
    return true;
  }

  return false;
});
