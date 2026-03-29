#!/bin/bash
# Auto-restart daemon every 3 hours to prevent degradation.
# Called by cron. Logs to logs/restart.log.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="/opt/homebrew/anaconda3/bin/python"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$PROJECT_DIR/data/daemon.pid"

mkdir -p "$LOG_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') — Daemon restart triggered"

# Find and kill existing daemon
OLD_PID=$(pgrep -f "jobpulse.runner multi" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    # Get uptime info before killing
    PS_INFO=$(ps -p "$OLD_PID" -o etime=,pcpu=,rss= 2>/dev/null || echo "unknown")
    echo "  Stopping PID $OLD_PID (uptime/cpu/mem: $PS_INFO)"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 3
    # Force kill if still alive
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "  Force killing PID $OLD_PID"
        kill -9 "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
else
    echo "  No running daemon found"
fi

# Start fresh daemon
cd "$PROJECT_DIR"
nohup "$PYTHON" -m jobpulse.runner multi >> "$LOG_DIR/telegram-listener.log" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

sleep 3

# Verify it started
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "  Started new daemon PID $NEW_PID"
    echo "  $(date '+%Y-%m-%d %H:%M:%S') — Restart complete"
else
    echo "  ERROR: Daemon failed to start!"
fi
