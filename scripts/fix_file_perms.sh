#!/usr/bin/env bash
# Fix permissions on sensitive data files — should be owner-only (600).
# Run after first setup and periodically via cron.
set -euo pipefail

SENSITIVE_FILES=(
    "data/google_token.json"
    "data/ats_accounts.db"
    "data/audit.db"
)

for f in "${SENSITIVE_FILES[@]}"; do
    if [ -f "$f" ]; then
        chmod 600 "$f"
        echo "Fixed: $f → 600"
    fi
done
