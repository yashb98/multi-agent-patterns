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
  if (ws) {
    try { ws.close(); } catch (_) { /* ignore close errors */ }
    ws = null;
  }

  connectionState = "connecting";
  broadcastStatus();
  console.log("[JobPulse] Connecting to", WS_URL);

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    connectionState = "connected";
    broadcastStatus();
    startHeartbeat();
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    console.log("[JobPulse] Connected to Python backend");
  };

  ws.onclose = () => {
    connectionState = "disconnected";
    broadcastStatus();
    stopHeartbeat();
    ws = null;
    console.log("[JobPulse] Disconnected — will retry in 3s");
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    console.error("[JobPulse] WebSocket error:", err);
    connectionState = "disconnected";
    broadcastStatus();
    ws = null;
    // onclose usually fires after onerror, but schedule just in case
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
      await handleNavigate(id, payload.url);
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

    // --- Get snapshot: request fresh page scan from content script ---
    if (action === "get_snapshot") {
      const tab = await getActiveTab();
      await ensureContentScript(tab.id);
      const response = await chrome.tabs.sendMessage(tab.id, { id, action: "get_snapshot", payload: {} });
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
    await ensureContentScript(tab.id);
    const response = await chrome.tabs.sendMessage(tab.id, { id, action, payload });
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
  let resolved = false;

  // Source 1: content script sends navigation snapshot after page load
  const navListener = (msg) => {
    if (!resolved && msg.type === "navigation" && msg.payload?.snapshot) {
      resolved = true;
      chrome.runtime.onMessage.removeListener(navListener);
      sendToPython({ id: cmdId, type: "result", payload: { success: true, snapshot: msg.payload.snapshot } });
    }
  };
  chrome.runtime.onMessage.addListener(navListener);

  // Source 2: tab reports "complete" → request snapshot from content script
  const tabListener = async (tabId, changeInfo) => {
    if (tabId === tab.id && changeInfo.status === "complete" && !resolved) {
      chrome.tabs.onUpdated.removeListener(tabListener);
      await new Promise((r) => setTimeout(r, 2000)); // Let DOM settle
      if (resolved) return;
      try {
        const snapshot = await chrome.tabs.sendMessage(tab.id, {
          id: cmdId, action: "get_snapshot", payload: {},
        });
        if (!resolved && snapshot) {
          resolved = true;
          chrome.runtime.onMessage.removeListener(navListener);
          sendToPython({ id: cmdId, type: "result", payload: { success: true, snapshot } });
        }
      } catch (_) {
        // Content script not ready yet — timeout will catch it
      }
    }
  };
  chrome.tabs.onUpdated.addListener(tabListener);

  // Source 3: timeout fallback
  setTimeout(() => {
    if (!resolved) {
      resolved = true;
      chrome.runtime.onMessage.removeListener(navListener);
      chrome.tabs.onUpdated.removeListener(tabListener);
      sendToPython({ id: cmdId, type: "result", payload: { success: true, snapshot: null } });
    }
  }, 15_000);

  // Trigger the navigation
  await chrome.tabs.update(tab.id, { url });
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

/** Get the currently active tab. Throws if no tab is focused. */
async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("No active tab");
  return tab;
}

/**
 * Ensure the content script is injected in the active tab.
 * After extension reload, existing tabs lose their content scripts.
 * This re-injects content.js if it's not already running.
 */
async function ensureContentScript(tabId) {
  try {
    // Ping the content script — if it responds, it's already loaded
    await chrome.tabs.sendMessage(tabId, { action: "get_snapshot", payload: {} });
  } catch (_) {
    // Content script not loaded — inject it
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
// Startup — auto-connect on install, reload, and service worker wake
// ═══════════════════════════════════════════════════════════════

chrome.runtime.onInstalled.addListener(() => {
  console.log("[JobPulse] Extension installed/reloaded");
  connect();
});

// Service worker woke up (may be fresh start or after idle timeout)
connect();

// Side panel: don't auto-open on action click
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
