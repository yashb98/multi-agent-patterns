#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Setup GitHub Actions Secrets from .env
#
# Reads your .env file and sets each variable as a GitHub repository
# secret so the backup workflows can use them.
#
# Usage: ./scripts/setup_github_secrets.sh
# Requires: gh CLI authenticated
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
REPO="yashb98/multi-agent-patterns"

echo "══════════════════════════════════════════"
echo "  Setting GitHub Actions Secrets"
echo "══════════════════════════════════════════"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env file not found at $ENV_FILE"
    exit 1
fi

# Secrets to sync
SECRETS=(
    "OPENAI_API_KEY"
    "TELEGRAM_BOT_TOKEN"
    "TELEGRAM_CHAT_ID"
    "NOTION_API_KEY"
    "NOTION_TASKS_DB_ID"
    "NOTION_RESEARCH_DB_ID"
    "NOTION_PARENT_PAGE_ID"
    "GOOGLE_OAUTH_CLIENT_ID"
    "GOOGLE_OAUTH_CLIENT_SECRET"
)

# Read .env
source "$ENV_FILE"

for SECRET_NAME in "${SECRETS[@]}"; do
    VALUE="${!SECRET_NAME}"
    if [ -z "$VALUE" ]; then
        echo "⚠️  $SECRET_NAME is empty — skipping"
        continue
    fi

    echo -n "Setting $SECRET_NAME... "
    echo "$VALUE" | gh secret set "$SECRET_NAME" --repo "$REPO" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "✅"
    else
        echo "❌"
    fi
done

echo ""
echo "══════════════════════════════════════════"
echo "  Done! Verify at:"
echo "  https://github.com/$REPO/settings/secrets/actions"
echo "══════════════════════════════════════════"
