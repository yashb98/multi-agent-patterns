// extension/background.js
// Service worker: WebSocket client to Python backend + message relay.

let ws = null;
let connectionState = "disconnected";  // "disconnected" | "connecting" | "connected"
let wsUrl = "ws://localhost:8765";

// --- Keepalive heartbeat (MV3 requirement) ---
// Chrome 116+ keeps service worker alive during active WebSocket,
// but we still need periodic pings within the 30s idle timeout.
let heartbeatInterval = null;

function startHeartbeat() {
  stopHeartbeat();
  heartbeatInterval = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 20000);  // 20s interval (within 30s service worker timeout)
}

function stopHeartbeat() {
  if (heartbeatInterval) {
    clearInterval(heartbeatInterval);
    heartbeatInterval = null;
  }
}

// --- WebSocket connection management ---

function connect() {
  if (ws && ws.readyState <= WebSocket.OPEN) return;

  connectionState = "connecting";
  broadcastStatus();

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    connectionState = "connected";
    broadcastStatus();
    startHeartbeat();
    console.log("[JobPulse] Connected to Python backend");
  };

  ws.onclose = () => {
    connectionState = "disconnected";
    broadcastStatus();
    stopHeartbeat();
    ws = null;
    console.log("[JobPulse] Disconnected from Python backend");
  };

  ws.onerror = (err) => {
    console.error("[JobPulse] WebSocket error:", err);
    connectionState = "disconnected";
    broadcastStatus();
  };

  ws.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      console.error("[JobPulse] Invalid JSON from Python:", event.data);
      return;
    }

    // Pong response (keepalive)
    if (msg.type === "pong") return;

    // Command from Python — forward to content script
    if (msg.action) {
      handlePythonCommand(msg);
      return;
    }
  };
}

function disconnect() {
  if (ws) {
    ws.close();
    ws = null;
  }
  connectionState = "disconnected";
  broadcastStatus();
  stopHeartbeat();
}

// --- Command handling ---

async function handlePythonCommand(cmd) {
  const { id, action, payload } = cmd;

  // Send ack immediately
  sendToPython({ id, type: "ack", payload: {} });

  try {
    if (action === "navigate") {
      const tab = await getActiveTab();
      await chrome.tabs.update(tab.id, { url: payload.url });
      // Snapshot will be sent by content script after page load
      return;
    }

    if (action === "screenshot") {
      const tab = await getActiveTab();
      const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
      const base64 = dataUrl.replace(/^data:image\/png;base64,/, "");
      sendToPython({ id, type: "result", payload: { success: true, data: base64 } });
      return;
    }

    if (action === "close_tab") {
      const tab = await getActiveTab();
      await chrome.tabs.remove(tab.id);
      sendToPython({ id, type: "result", payload: { success: true } });
      return;
    }

    // All other actions: forward to content script
    const tab = await getActiveTab();
    const response = await chrome.tabs.sendMessage(tab.id, { id, action, payload });
    sendToPython({ id, type: "result", payload: response || { success: false, error: "No response from content script" } });
  } catch (err) {
    sendToPython({ id, type: "error", payload: { success: false, error: err.message } });
  }
}

function sendToPython(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) throw new Error("No active tab");
  return tab;
}

// --- Message relay: content/popup/sidepanel -> background ---

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // Status request from popup/sidepanel
  if (msg.type === "status") {
    sendResponse({ state: connectionState });
    return true;
  }

  // Connect/disconnect from popup
  if (msg.type === "connect") {
    connect();
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "disconnect") {
    disconnect();
    sendResponse({ ok: true });
    return true;
  }

  // Snapshot from content script — forward to Python
  if (msg.type === "snapshot" || msg.type === "mutation" || msg.type === "navigation") {
    sendToPython({ id: msg.id || "", type: msg.type, payload: msg.payload || {} });
    // Also relay to sidepanel
    chrome.runtime.sendMessage({ type: "snapshot_update", payload: msg.payload }).catch(() => {});
    return false;
  }

  return false;
});

// --- Side panel setup ---

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});

// --- Broadcast connection status ---

function broadcastStatus() {
  chrome.runtime.sendMessage({ type: "status", state: connectionState }).catch(() => {});
}
