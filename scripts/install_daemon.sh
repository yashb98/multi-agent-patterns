#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Install/Uninstall the JobPulse Telegram daemon.
# Cross-platform: auto-detects macOS (launchctl) vs Linux (systemd).
#
# Usage:
#   ./scripts/install_daemon.sh install    # Install and start
#   ./scripts/install_daemon.sh uninstall  # Stop and remove
#   ./scripts/install_daemon.sh status     # Check if running
#   ./scripts/install_daemon.sh restart    # Restart daemon
#   ./scripts/install_daemon.sh logs       # Tail daemon logs
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OS="$(uname -s)"

# ── Auto-detect Python ──────────────────────────────────────────
if [ -f "$PROJECT_DIR/.python_path" ]; then
    PYTHON="$(cat "$PROJECT_DIR/.python_path")"
elif command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
else
    PYTHON="python"
fi
PYTHON_BIN="$(dirname "$PYTHON")"

# ── macOS (launchctl) ───────────────────────────────────────────
if [ "$OS" = "Darwin" ]; then

PLIST_NAME="com.jobpulse.daemon"
PLIST_SRC="$PROJECT_DIR/scripts/com.jobpulse.daemon.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

_macos_generate_plist() {
    # Generate plist with correct paths for THIS machine
    cat > "$PLIST_DST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>jobpulse.runner</string>
        <string>multi-bot</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>$PROJECT_DIR/logs/daemon-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$PROJECT_DIR/logs/daemon-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$PYTHON_BIN:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF
}

case "${1:-status}" in
    install)
        echo "Installing JobPulse daemon (macOS)..."
        mkdir -p "$PROJECT_DIR/logs"
        launchctl bootout gui/$(id -u) "$PLIST_DST" 2>/dev/null || true
        _macos_generate_plist
        launchctl bootstrap gui/$(id -u) "$PLIST_DST"
        echo "  Python: $PYTHON"
        echo "  Project: $PROJECT_DIR"
        echo "  Logs: $PROJECT_DIR/logs/daemon-stdout.log"
        echo "  Auto-starts on login, restarts on crash."
        ;;
    uninstall)
        echo "Uninstalling JobPulse daemon (macOS)..."
        launchctl bootout gui/$(id -u) "$PLIST_DST" 2>/dev/null || true
        rm -f "$PLIST_DST"
        echo "  Daemon stopped and removed."
        ;;
    restart)
        echo "Restarting JobPulse daemon (macOS)..."
        launchctl kickstart -k gui/$(id -u)/$PLIST_NAME 2>/dev/null
        echo "  Daemon restarted."
        ;;
    status)
        if launchctl print gui/$(id -u)/$PLIST_NAME 2>/dev/null | grep -q "state"; then
            echo "  JobPulse daemon is running (macOS launchd)"
            launchctl print gui/$(id -u)/$PLIST_NAME 2>/dev/null | grep -E "state|pid|last exit"
        else
            echo "  JobPulse daemon is NOT running"
            echo "  Install with: ./scripts/install_daemon.sh install"
        fi
        ;;
    logs)
        echo "=== stdout ===" && tail -30 "$PROJECT_DIR/logs/daemon-stdout.log" 2>/dev/null
        echo "" && echo "=== stderr ===" && tail -30 "$PROJECT_DIR/logs/daemon-stderr.log" 2>/dev/null
        ;;
    *) echo "Usage: $0 {install|uninstall|restart|status|logs}"; exit 1 ;;
esac

# ── Linux (systemd) ────────────────────────────────────────────
else

SERVICE_NAME="jobpulse"
SERVICE_SRC="$PROJECT_DIR/scripts/jobpulse.service"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_DST="$SERVICE_DIR/$SERVICE_NAME.service"

_linux_install_service() {
    mkdir -p "$SERVICE_DIR" "$PROJECT_DIR/logs"
    # Generate service file with correct paths
    sed \
        -e "s|__PYTHON__|$PYTHON|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
        "$SERVICE_SRC" > "$SERVICE_DST"
    systemctl --user daemon-reload
}

case "${1:-status}" in
    install)
        echo "Installing JobPulse daemon (Linux systemd)..."
        _linux_install_service
        systemctl --user enable "$SERVICE_NAME"
        systemctl --user start "$SERVICE_NAME"
        echo "  Python: $PYTHON"
        echo "  Project: $PROJECT_DIR"
        echo "  Service: $SERVICE_DST"
        echo "  Logs: journalctl --user -u $SERVICE_NAME -f"
        echo "  Auto-starts on login, restarts on crash."
        # Enable lingering so service runs even when not logged in
        if command -v loginctl &>/dev/null; then
            loginctl enable-linger "$(whoami)" 2>/dev/null || true
            echo "  Lingering enabled (runs without active login session)."
        fi
        ;;
    uninstall)
        echo "Uninstalling JobPulse daemon (Linux systemd)..."
        systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
        systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
        rm -f "$SERVICE_DST"
        systemctl --user daemon-reload
        echo "  Daemon stopped and removed."
        ;;
    restart)
        echo "Restarting JobPulse daemon (Linux systemd)..."
        systemctl --user restart "$SERVICE_NAME"
        echo "  Daemon restarted."
        ;;
    status)
        if systemctl --user is-active "$SERVICE_NAME" &>/dev/null; then
            echo "  JobPulse daemon is running (Linux systemd)"
            systemctl --user status "$SERVICE_NAME" --no-pager | head -10
        else
            echo "  JobPulse daemon is NOT running"
            echo "  Install with: ./scripts/install_daemon.sh install"
            # Show last failure if any
            systemctl --user status "$SERVICE_NAME" --no-pager 2>/dev/null | tail -5 || true
        fi
        ;;
    logs)
        if command -v journalctl &>/dev/null; then
            journalctl --user -u "$SERVICE_NAME" --no-pager -n 30
        else
            echo "=== stdout ===" && tail -30 "$PROJECT_DIR/logs/daemon-stdout.log" 2>/dev/null
            echo "" && echo "=== stderr ===" && tail -30 "$PROJECT_DIR/logs/daemon-stderr.log" 2>/dev/null
        fi
        ;;
    *) echo "Usage: $0 {install|uninstall|restart|status|logs}"; exit 1 ;;
esac

fi
