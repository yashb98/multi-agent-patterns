#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Setup Always-On Mode for JobPulse
#
# Configures Mac to maintain network connectivity during sleep
# so the Telegram daemon keeps running even with the lid closed.
#
# Run with sudo: sudo ./scripts/setup_always_on.sh
# ─────────────────────────────────────────────────────────────────

echo "══════════════════════════════════════════"
echo "  JobPulse — Always-On Setup"
echo "══════════════════════════════════════════"

# 1. Keep TCP connections alive during sleep
echo "Setting tcpkeepalive=1..."
sudo pmset -a tcpkeepalive 1

# 2. Enable Power Nap (background tasks during sleep)
echo "Setting powernap=1..."
sudo pmset -a powernap 1

# 3. Keep network active during sleep
echo "Setting networkoversleep=1..."
sudo pmset -a networkoversleep 1

# 4. Wake on network access (Wake-on-LAN)
echo "Setting womp=1..."
sudo pmset -a womp 1

# 5. Prevent idle sleep when on charger (AC power only)
echo "Setting sleep=0 on charger (AC)..."
sudo pmset -c sleep 0

# 6. Keep display sleep separate (screen off but Mac awake on charger)
echo "Setting displaysleep=10 on charger..."
sudo pmset -c displaysleep 10

# 7. On battery: allow sleep after 15 min (save battery)
echo "Setting sleep=15 on battery..."
sudo pmset -b sleep 15

echo ""
echo "══════════════════════════════════════════"
echo "  Current Power Settings"
echo "══════════════════════════════════════════"
pmset -g | grep -E "tcpkeepalive|powernap|sleep|displaysleep|networkoversleep|womp"

echo ""
echo "✅ Always-On configured!"
echo ""
echo "Behavior:"
echo "  On charger: Mac never sleeps, display off after 10min"
echo "  On battery: Mac sleeps after 15min (saves battery)"
echo "  During sleep: network stays alive (TCP keepalive + Power Nap)"
echo ""
echo "The Telegram daemon will keep running as long as your Mac is plugged in."
