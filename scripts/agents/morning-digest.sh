#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Morning Telegram Digest
# Runs: 8:03am daily
#
# Collects ALL sections and sends ONE consolidated Telegram message:
#   Section 1: Recruiter emails (from yesterday's log)
#   Section 2: Today's calendar + tomorrow preview (via Calendar MCP)
#   Section 3: Today's Notion tasks (via Notion MCP)
#   Section 4: Yesterday's GitHub commits (via gh CLI)
#
# Each section is fault-tolerant — if one fails, others still send.
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
TODAY_PRETTY=$(date +"%A, %B %d, %Y")
YESTERDAY=$(date -v-1d +%Y-%m-%d)

source "$PROJECT_DIR/.env"
mkdir -p "$DATA_DIR" "$LOG_DIR"

echo "[$(date)] Morning digest started..." >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# SECTION 1: Recruiter Emails (read from yesterday's log, no MCP needed)
# ════════════════════════════════════════════════════════════════
RECRUITER_LOG="$DATA_DIR/recruiter_emails.log"
SECTION_EMAILS=""

if [ -f "$RECRUITER_LOG" ]; then
    # Filter entries from yesterday
    YESTERDAY_EMAILS=$(grep "^${YESTERDAY}" "$RECRUITER_LOG" 2>/dev/null || true)

    if [ -n "$YESTERDAY_EMAILS" ]; then
        SECTION_EMAILS=$(python3 << PYEOF
lines = """$YESTERDAY_EMAILS""".strip().split("\n")
selected = []
interview = []
rejected = []

for line in lines:
    parts = line.split("|", 3)
    if len(parts) < 4:
        continue
    ts, cat, sender, subject = parts
    sender_short = sender.split("<")[0].strip() if "<" in sender else sender.strip()
    if cat == "SELECTED_NEXT_ROUND":
        selected.append(f'  ✅ SELECTED: {sender_short} — "{subject.strip()}"')
    elif cat == "INTERVIEW_SCHEDULING":
        interview.append(f'  📅 INTERVIEW: {sender_short} — "{subject.strip()}"')
    elif cat == "REJECTED":
        rejected.append(f'  ❌ REJECTED: {sender_short} — "{subject.strip()}"')

result = "\n".join(selected + interview + rejected)
print(result if result else "  No recruiter emails yesterday")
PYEOF
)
    else
        SECTION_EMAILS="  No recruiter emails yesterday"
    fi
else
    SECTION_EMAILS="  No recruiter emails yesterday"
fi

echo "[$(date)] Section 1 (emails) done" >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# SECTION 2: Calendar (run calendar-check.sh, then read its output)
# ════════════════════════════════════════════════════════════════
SECTION_CALENDAR=""
SECTION_TOMORROW=""

bash "$PROJECT_DIR/scripts/agents/calendar-check.sh" 2>> "$LOG_DIR/morning.log" || true

CAL_FILE="$DATA_DIR/calendar-$TODAY.json"
if [ -f "$CAL_FILE" ]; then
    SECTION_CALENDAR=$(python3 << PYEOF
import json
with open("$CAL_FILE") as f:
    cal = json.load(f)

today = cal.get("today_events", [])
tomorrow = cal.get("tomorrow_events", [])

if today:
    lines = []
    for e in today:
        loc = f" ({e['location']})" if e.get("location") else ""
        lines.append(f"  • {e['start']} — {e['title']}{loc}")
    print("\n".join(lines))
else:
    print("  No events today")
PYEOF
)
    SECTION_TOMORROW=$(python3 << PYEOF
import json
with open("$CAL_FILE") as f:
    cal = json.load(f)

tomorrow = cal.get("tomorrow_events", [])
if tomorrow:
    lines = []
    for e in tomorrow:
        loc = f" ({e['location']})" if e.get("location") else ""
        lines.append(f"  • {e['start']} — {e['title']}{loc}")
    print("\n".join(lines))
else:
    print("  Nothing scheduled tomorrow")
PYEOF
)
else
    SECTION_CALENDAR="  No events today"
    SECTION_TOMORROW="  Nothing scheduled tomorrow"
fi

echo "[$(date)] Section 2 (calendar) done" >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# SECTION 3: Notion Tasks (uses claude + Notion MCP)
# ════════════════════════════════════════════════════════════════
SECTION_TASKS=""

TASKS_RAW=$(/Users/yashbishnoi/.local/bin/claude -p "
You have access to Notion via MCP.

Search for a database or page called 'Daily Tasks' or 'Tasks' or 'Todo' or 'To Do'.
Filter for items where:
  - Date property = today ($TODAY) AND Status != 'Done'
  OR if no date filter is possible, just get items where Status != 'Done'

Output ONLY a simple text list (no JSON, no markdown fences):
□ Task description 1
□ Task description 2
□ Task description 3

If no database found or no tasks, output exactly: NO_TASKS_FOUND
If tasks exist but all are done, output exactly: ALL_DONE

IMPORTANT: Output ONLY the task list or NO_TASKS_FOUND or ALL_DONE. Nothing else.
" --dangerously-skip-permissions 2>> "$LOG_DIR/morning.log" || echo "NO_TASKS_FOUND")

if echo "$TASKS_RAW" | grep -q "NO_TASKS_FOUND"; then
    SECTION_TASKS="  No tasks set for today. Add some in Notion!"
elif echo "$TASKS_RAW" | grep -q "ALL_DONE"; then
    SECTION_TASKS="  All tasks done! 🎉"
else
    # Indent each line with 2 spaces
    SECTION_TASKS=$(echo "$TASKS_RAW" | grep -E "^□|^-|^•|^\*" | sed 's/^/  /' || echo "  No tasks set for today. Add some in Notion!")
fi

echo "[$(date)] Section 3 (notion tasks) done" >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# SECTION 4: GitHub Commits (run github-commits.sh, read output)
# ════════════════════════════════════════════════════════════════
SECTION_GITHUB=""

GITHUB_OUTPUT=$(bash "$PROJECT_DIR/scripts/agents/github-commits.sh" 2>> "$LOG_DIR/morning.log" || echo "Could not fetch GitHub data")
SECTION_GITHUB=$(echo "$GITHUB_OUTPUT" | sed 's/^/  /')

echo "[$(date)] Section 4 (github) done" >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# SECTION 5: GitHub Trending Repos (run github-trending.sh)
# ════════════════════════════════════════════════════════════════
SECTION_TRENDING=""

TRENDING_OUTPUT=$(bash "$PROJECT_DIR/scripts/agents/github-trending.sh" 2>> "$LOG_DIR/morning.log" || echo "  Could not fetch trending repos")
SECTION_TRENDING="$TRENDING_OUTPUT"

echo "[$(date)] Section 5 (trending) done" >> "$LOG_DIR/morning.log"

# ════════════════════════════════════════════════════════════════
# BUILD AND SEND THE MESSAGE
# ════════════════════════════════════════════════════════════════
MESSAGE="☀️ Good Morning Yash! Here's your briefing for ${TODAY_PRETTY}:

━━━━━━━━━━━━━━━━━━━━

📧 RECRUITER EMAILS (yesterday):
${SECTION_EMAILS}

━━━━━━━━━━━━━━━━━━━━

📅 TODAY'S CALENDAR:
${SECTION_CALENDAR}

📅 TOMORROW PREVIEW:
${SECTION_TOMORROW}

━━━━━━━━━━━━━━━━━━━━

📝 TODAY'S TASKS (from Notion):
${SECTION_TASKS}

━━━━━━━━━━━━━━━━━━━━

💻 YESTERDAY'S GITHUB:
${SECTION_GITHUB}

━━━━━━━━━━━━━━━━━━━━

🔥 TRENDING ON GITHUB:
${SECTION_TRENDING}

━━━━━━━━━━━━━━━━━━━━

Have a productive day! 🚀"

# Send via Telegram (using curl to avoid Python SSL issues)
python3 << PYEOF
import json
import subprocess

message = """$MESSAGE"""
payload = json.dumps({"chat_id": "$TELEGRAM_CHAT_ID", "text": message})
result = subprocess.run([
    "curl", "-s", "-X", "POST",
    "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage",
    "-H", "Content-Type: application/json",
    "-d", payload
], capture_output=True, text=True, timeout=15)

try:
    resp = json.loads(result.stdout)
    if resp.get("ok"):
        print("Morning digest sent successfully!")
    else:
        print(f"Telegram error: {resp}")
except:
    print(f"Failed to parse response: {result.stdout[:200]}")
PYEOF

# ════════════════════════════════════════════════════════════════
# SEPARATE MESSAGE: Notion Todo Prompt
# Always sent after the digest as its own message
# ════════════════════════════════════════════════════════════════
if echo "$SECTION_TASKS" | grep -qi "no tasks\|add some"; then
    TODO_MSG="📝 Hey Yash! Quick check on your day:

I didn't find a todo list for today in Notion.

How does your day look? Would you like me to create a todo list for you?

Just reply with your tasks and I'll add them to Notion. For example:
  • Fix NexusMind CORS bug
  • Apply to 5 roles
  • Prepare for interview

Or reply 'skip' if you're good for today."

    python3 << PYEOF2
import json, subprocess
payload = json.dumps({"chat_id": "$TELEGRAM_CHAT_ID", "text": """$TODO_MSG"""})
subprocess.run([
    "curl", "-s", "-X", "POST",
    "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendMessage",
    "-H", "Content-Type: application/json",
    "-d", payload
], capture_output=True, timeout=15)
print("Notion todo prompt sent")
PYEOF2
fi

echo "[$(date)] Morning digest sent." >> "$LOG_DIR/morning.log"
