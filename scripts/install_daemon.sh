#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Install/Uninstall the JobPulse Telegram daemon as a macOS Launch Agent.
#
# Usage:
#   ./scripts/install_daemon.sh install    # Install and start
#   ./scripts/install_daemon.sh uninstall  # Stop and remove
#   ./scripts/install_daemon.sh status     # Check if running
#   ./scripts/install_daemon.sh restart    # Restart daemon
#   ./scripts/install_daemon.sh logs       # Tail daemon logs
# ─────────────────────────────────────────────────────────────────

PLIST_NAME="com.jobpulse.daemon"
PLIST_SRC="$(dirname "$0")/com.jobpulse.daemon.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

case "${1:-status}" in
    install)
        echo "Installing JobPulse daemon..."
        # Stop if already running
        launchctl bootout gui/$(id -u) "$PLIST_DST" 2>/dev/null
        # Copy plist
        cp "$PLIST_SRC" "$PLIST_DST"
        # Load and start
        launchctl bootstrap gui/$(id -u) "$PLIST_DST"
        echo "✅ Daemon installed and started"
        echo "   It will auto-start on login and restart if it crashes."
        echo "   Logs: $PROJECT_DIR/logs/daemon-stdout.log"
        launchctl print gui/$(id -u)/$PLIST_NAME 2>/dev/null | head -5
        ;;

    uninstall)
        echo "Uninstalling JobPulse daemon..."
        launchctl bootout gui/$(id -u) "$PLIST_DST" 2>/dev/null
        rm -f "$PLIST_DST"
        echo "✅ Daemon stopped and removed"
        ;;

    restart)
        echo "Restarting JobPulse daemon..."
        launchctl kickstart -k gui/$(id -u)/$PLIST_NAME 2>/dev/null
        echo "✅ Daemon restarted"
        ;;

    status)
        if launchctl print gui/$(id -u)/$PLIST_NAME 2>/dev/null | grep -q "state"; then
            echo "✅ JobPulse daemon is running"
            launchctl print gui/$(id -u)/$PLIST_NAME 2>/dev/null | grep -E "state|pid|last exit"
        else
            echo "❌ JobPulse daemon is not running"
            echo "   Install with: ./scripts/install_daemon.sh install"
        fi
        ;;

    logs)
        echo "=== stdout ===" && tail -20 "$PROJECT_DIR/logs/daemon-stdout.log" 2>/dev/null
        echo "" && echo "=== stderr ===" && tail -20 "$PROJECT_DIR/logs/daemon-stderr.log" 2>/dev/null
        ;;

    *)
        echo "Usage: $0 {install|uninstall|restart|status|logs}"
        exit 1
        ;;
esac
