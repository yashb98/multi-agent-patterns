#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# GitHub Commits Check
# Runs: as part of morning-digest, or standalone
#
# Uses the Commits API per-repo (NOT the Events API) because the
# Events API strips commit arrays from older PushEvents.
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
YESTERDAY=$(date -v-1d +%Y-%m-%d)
TODAY=$(date +%Y-%m-%d)
RESULTS_FILE="$DATA_DIR/github-$TODAY.json"
USERNAME="yashb98"

mkdir -p "$DATA_DIR" "$LOG_DIR"

echo "[$(date)] Checking GitHub commits for $YESTERDAY..." >> "$LOG_DIR/github.log"

python3 << PYEOF
import json
import subprocess

username = "$USERNAME"
yesterday = "$YESTERDAY"
today = "$TODAY"

def gh_api(endpoint):
    try:
        r = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, timeout=15)
        return json.loads(r.stdout) if r.stdout else []
    except:
        return []

# Get recently pushed repos
repos_data = gh_api(f"/users/{username}/repos?sort=pushed&per_page=15")

commits = []
repos = set()

for repo_obj in repos_data:
    name = repo_obj.get("name", "")
    pushed = repo_obj.get("pushed_at", "")[:10]
    if pushed != yesterday:
        continue

    # Fetch actual commits for this repo
    repo_commits = gh_api(
        f"/repos/{username}/{name}/commits?since={yesterday}T00:00:00Z&until={today}T00:00:00Z&per_page=50"
    )
    for c in repo_commits:
        msg = c.get("commit", {}).get("message", "").split("\n")[0][:100]
        sha = c.get("sha", "")[:7]
        commits.append({"repo": name, "message": msg, "sha": sha})
        repos.add(name)

result = {
    "date": yesterday,
    "total_commits": len(commits),
    "repos": sorted(list(repos)),
    "commits": commits
}

with open("$RESULTS_FILE", "w") as f:
    json.dump(result, f, indent=2)

if commits:
    repo_list = ", ".join(sorted(repos))
    print(f"{len(commits)} commit(s) across {repo_list}")
    for c in commits[:10]:
        print(f"  • {c['repo']}: {c['message']}")
else:
    print("No commits yesterday — let's fix that today!")
PYEOF

echo "[$(date)] GitHub check done." >> "$LOG_DIR/github.log"
