#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Setup Always-On Mode for JobPulse
# Cross-platform: macOS (pmset) and Linux (systemd-sleep).
#
# Configures the machine to maintain network connectivity so the
# Telegram daemon keeps running even with the lid closed / idle.
#
# macOS: Run with sudo: sudo ./scripts/setup_always_on.sh
# Linux: Run with sudo: sudo ./scripts/setup_always_on.sh
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

OS="$(uname -s)"

echo "══════════════════════════════════════════"
echo "  JobPulse — Always-On Setup ($OS)"
echo "══════════════════════════════════════════"

if [ "$OS" = "Darwin" ]; then
    # ── macOS ─────────────────────────────────────────────────────

    echo "Setting tcpkeepalive=1..."
    sudo pmset -a tcpkeepalive 1

    echo "Setting powernap=1..."
    sudo pmset -a powernap 1

    echo "Setting networkoversleep=1..."
    sudo pmset -a networkoversleep 1

    echo "Setting womp=1 (Wake-on-LAN)..."
    sudo pmset -a womp 1

    echo "Setting sleep=0 on charger (AC)..."
    sudo pmset -c sleep 0

    echo "Setting displaysleep=10 on charger..."
    sudo pmset -c displaysleep 10

    echo "Setting sleep=15 on battery..."
    sudo pmset -b sleep 15

    echo ""
    echo "Current Power Settings:"
    pmset -g | grep -E "tcpkeepalive|powernap|sleep|displaysleep|networkoversleep|womp"

    echo ""
    echo "Behavior:"
    echo "  On charger: Mac never sleeps, display off after 10min"
    echo "  On battery: Mac sleeps after 15min (saves battery)"
    echo "  During sleep: network stays alive (TCP keepalive + Power Nap)"

else
    # ── Linux ─────────────────────────────────────────────────────

    echo "Disabling sleep/suspend targets..."
    sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target 2>/dev/null || true

    # Prevent lid-close suspend (if laptop)
    LOGIND_CONF="/etc/systemd/logind.conf"
    if [ -f "$LOGIND_CONF" ]; then
        echo "Configuring lid-close action to ignore..."
        # Set HandleLidSwitch=ignore if not already set
        if grep -q "^HandleLidSwitch=" "$LOGIND_CONF"; then
            sudo sed -i 's/^HandleLidSwitch=.*/HandleLidSwitch=ignore/' "$LOGIND_CONF"
        elif grep -q "^#HandleLidSwitch=" "$LOGIND_CONF"; then
            sudo sed -i 's/^#HandleLidSwitch=.*/HandleLidSwitch=ignore/' "$LOGIND_CONF"
        else
            echo "HandleLidSwitch=ignore" | sudo tee -a "$LOGIND_CONF" > /dev/null
        fi

        if grep -q "^HandleLidSwitchExternalPower=" "$LOGIND_CONF"; then
            sudo sed -i 's/^HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' "$LOGIND_CONF"
        elif grep -q "^#HandleLidSwitchExternalPower=" "$LOGIND_CONF"; then
            sudo sed -i 's/^#HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' "$LOGIND_CONF"
        else
            echo "HandleLidSwitchExternalPower=ignore" | sudo tee -a "$LOGIND_CONF" > /dev/null
        fi

        echo "Restarting systemd-logind..."
        sudo systemctl restart systemd-logind 2>/dev/null || true
    fi

    # Enable Wake-on-LAN if ethtool is available
    if command -v ethtool &>/dev/null; then
        # Find the primary network interface
        IFACE=$(ip route | grep default | awk '{print $5}' | head -1)
        if [ -n "$IFACE" ]; then
            echo "Enabling Wake-on-LAN on $IFACE..."
            sudo ethtool -s "$IFACE" wol g 2>/dev/null || echo "  (WoL not supported on $IFACE)"
        fi
    fi

    echo ""
    echo "Current sleep status:"
    systemctl status sleep.target --no-pager 2>/dev/null | head -3 || echo "  sleep.target masked (good)"

    echo ""
    echo "Behavior:"
    echo "  Sleep/suspend/hibernate: disabled"
    echo "  Lid close: ignored (machine stays awake)"
    echo "  Wake-on-LAN: enabled (if supported)"
    echo "  The daemon runs continuously via systemd."
fi

echo ""
echo "  The Telegram daemon will keep running as long as the machine has power."
