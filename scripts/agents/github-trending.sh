#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# GitHub Trending Repos
# Runs: daily as part of morning digest
#
# 1. Scrapes https://github.com/trending for today's top repos
# 2. Picks top 5
# 3. Outputs formatted text to stdout (for morning-digest)
# 4. Saves to data/github-trending-YYYY-MM-DD.json
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
RESULTS_FILE="$DATA_DIR/github-trending-$TODAY.json"

source "$PROJECT_DIR/.env"
mkdir -p "$DATA_DIR" "$LOG_DIR"

echo "[$(date)] Fetching GitHub trending repos..." >> "$LOG_DIR/github.log"

/Users/yashbishnoi/.local/bin/claude -p "
Use WebFetch to get https://github.com/trending

Extract the TOP 5 trending repositories. For each repo extract:
- repo: owner/name
- description: one-line description
- language: primary language
- stars_today: stars gained today (e.g. '234 stars today')
- url: https://github.com/owner/name

Output ONLY valid JSON (no markdown, no explanation):
[
  {\"repo\": \"owner/name\", \"description\": \"...\", \"language\": \"Python\", \"stars_today\": \"234\", \"url\": \"https://github.com/owner/name\"}
]

IMPORTANT: Output ONLY the JSON array. No markdown fences.
" --dangerously-skip-permissions 2>> "$LOG_DIR/github.log" | python3 << 'PYEOF' - "$RESULTS_FILE"
import json
import sys

raw = sys.stdin.read().strip()
results_file = sys.argv[1]

# Parse JSON
try:
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    repos = json.loads(raw)
except (json.JSONDecodeError, ValueError):
    repos = []

# Save JSON
with open(results_file, "w") as f:
    json.dump({"date": "$TODAY", "repos": repos}, f, indent=2)

# Output formatted text to stdout
if repos:
    for i, r in enumerate(repos[:5], 1):
        lang = f" [{r.get('language', '')}]" if r.get('language') else ""
        stars = f" ⭐ {r.get('stars_today', '')} today" if r.get('stars_today') else ""
        url = r.get('url', f"https://github.com/{r.get('repo', '')}")
        print(f"  {i}. {r.get('repo', '?')}{lang}{stars}")
        desc = r.get('description', '')
        if desc:
            print(f"     {desc[:80]}")
        print(f"     {url}")
else:
    print("  Could not fetch trending repos")
PYEOF

echo "[$(date)] GitHub trending done." >> "$LOG_DIR/github.log"
