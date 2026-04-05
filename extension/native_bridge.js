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
    if (resp.ok) {
      backendReady = true;
      return;
    }
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
