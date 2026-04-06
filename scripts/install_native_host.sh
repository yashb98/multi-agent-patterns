#!/bin/bash
# Install JobPulse Native Messaging host manifest for Chrome.
# Run from project root: bash scripts/install_native_host.sh

set -e

DIR="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts"
MANIFEST="com.jobpulse.brain.json"
HOST="jobpulse/native_host.py"

mkdir -p "$DIR"
cp "$MANIFEST" "$DIR/"
chmod +x "$HOST"

echo "Native Messaging host installed:"
echo "  Manifest: $DIR/$MANIFEST"
echo "  Host:     $(pwd)/$HOST"
echo "Restart Chrome for changes to take effect."
