#!/bin/bash
# Git pre-commit hook: auto-update stats in CLAUDE.md and README.md
# Install: cp scripts/pre-commit-stats.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

cd "$(git rev-parse --show-toplevel)" || exit 0

# Only run if Python files are staged
if git diff --cached --name-only | grep -q '\.py$'; then
    python scripts/update_stats.py 2>/dev/null
    # If stats changed, stage the updated files
    if ! git diff --quiet CLAUDE.md README.md 2>/dev/null; then
        git add CLAUDE.md README.md
        echo "📊 Stats auto-updated in CLAUDE.md + README.md"
    fi
fi
