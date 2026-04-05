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
