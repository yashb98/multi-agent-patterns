#!/bin/bash
# Auto-restart daemon every 3 hours to prevent degradation.
# Uses launchd (com.jobpulse.daemon) which runs multi-bot.
# Called by cron. Logs to logs/restart.log.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Daemon restart triggered"

# Kill any stale 'multi' processes (not multi-bot) — these are orphans from old restart script
STALE_PIDS=$(pgrep -f "jobpulse\.runner multi$" 2>/dev/null || true)
if [ -n "$STALE_PIDS" ]; then
    echo "  Cleaning up stale 'multi' orphans: $STALE_PIDS"
    echo "$STALE_PIDS" | xargs kill 2>/dev/null || true
    sleep 2
    echo "$STALE_PIDS" | xargs kill -9 2>/dev/null || true
fi

# Restart via launchd — it manages multi-bot with KeepAlive
OLD_PID=$(pgrep -f "jobpulse\.runner multi-bot" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    PS_INFO=$(ps -p "$OLD_PID" -o etime=,pcpu=,rss= 2>/dev/null || echo "unknown")
    echo "  Current multi-bot PID $OLD_PID (uptime/cpu/mem: $PS_INFO)"
fi

# bootout + bootstrap = clean restart via launchd
launchctl bootout gui/$(id -u) /Users/yashbishnoi/Library/LaunchAgents/com.jobpulse.daemon.plist 2>/dev/null || true
sleep 3
launchctl bootstrap gui/$(id -u) /Users/yashbishnoi/Library/LaunchAgents/com.jobpulse.daemon.plist 2>/dev/null || true

sleep 3

# Verify it started
NEW_PID=$(pgrep -f "jobpulse\.runner multi-bot" 2>/dev/null || true)
if [ -n "$NEW_PID" ]; then
    echo "  Started new multi-bot PID $NEW_PID"
    echo "  $(date '+%Y-%m-%d %H:%M:%S') — Restart complete"
else
    echo "  ERROR: multi-bot failed to start via launchd!"
fi
