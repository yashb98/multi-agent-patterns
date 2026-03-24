#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Telegram Listener — Polls for replies and acts on them
# Runs: every 5 minutes via cron, or as a one-shot after todo prompt
#
# Checks for new Telegram messages from Yash and:
#   - If tasks are listed → creates them in Notion via MCP
#   - If "skip" → does nothing
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
LAST_UPDATE_FILE="$DATA_DIR/telegram_last_update_id.txt"

source "$PROJECT_DIR/.env"
mkdir -p "$DATA_DIR" "$LOG_DIR"

# Get the last processed update ID (to avoid reprocessing)
LAST_UPDATE_ID=$(cat "$LAST_UPDATE_FILE" 2>/dev/null || echo "0")

echo "[$(date)] Telegram listener checking (after update_id: $LAST_UPDATE_ID)..." >> "$LOG_DIR/telegram-listener.log"

# Fetch new messages from Telegram
UPDATES=$(curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates?offset=$((LAST_UPDATE_ID + 1))&timeout=5")

# Parse and process with Python
python3 << PYEOF
import json
import subprocess
import sys
import os

updates_raw = '''$UPDATES'''
chat_id = "$TELEGRAM_CHAT_ID"
bot_token = "$TELEGRAM_BOT_TOKEN"
last_update_file = "$LAST_UPDATE_FILE"
log_file = "$LOG_DIR/telegram-listener.log"
project_dir = "$PROJECT_DIR"

def log(msg):
    with open(log_file, "a") as f:
        f.write(f"  {msg}\n")

def send_telegram(text):
    payload = json.dumps({"chat_id": chat_id, "text": text})
    subprocess.run([
        "curl", "-s", "-X", "POST",
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        "-H", "Content-Type: application/json",
        "-d", payload
    ], capture_output=True, timeout=15)

try:
    data = json.loads(updates_raw)
except:
    log("Failed to parse Telegram updates")
    sys.exit(0)

results = data.get("result", [])
if not results:
    log("No new messages")
    sys.exit(0)

max_update_id = 0

for update in results:
    update_id = update.get("update_id", 0)
    max_update_id = max(max_update_id, update_id)

    msg = update.get("message", {})
    from_id = str(msg.get("from", {}).get("id", ""))
    text = msg.get("text", "").strip()

    # Only process messages from Yash
    if from_id != chat_id:
        continue

    if not text:
        continue

    log(f"Got message: {text[:100]}")

    # Check if it's a "skip" response
    if text.lower() in ("skip", "no", "nah", "not today", "pass"):
        send_telegram("👍 Got it — no todo list for today. Enjoy your day!")
        log("User skipped todo list")
        continue

    # Otherwise, treat it as a task list — create in Notion via claude
    tasks = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip common prefixes
        for prefix in ["- ", "• ", "□ ", "* ", "✅ ", "☐ "]:
            if line.startswith(prefix):
                line = line[len(prefix):]
        # Strip numbered prefixes like "1. " or "1) "
        if len(line) > 2 and line[0].isdigit() and line[1] in ".) ":
            line = line[2:].strip()
        elif len(line) > 3 and line[:2].isdigit() and line[2] in ".) ":
            line = line[3:].strip()
        if line:
            tasks.append(line)

    if not tasks:
        send_telegram("Hmm, I couldn't parse any tasks from that. Try sending them one per line:\n\nFix CORS bug\nApply to 5 roles\nPrepare for interview")
        continue

    task_list = "\n".join(f"  □ {t}" for t in tasks)
    log(f"Creating {len(tasks)} tasks in Notion")

    # Use claude + Notion MCP to create the tasks
    task_text = "\\n".join(tasks)
    prompt = f'''You have access to Notion via MCP.

Create today's todo list in Notion:

1. Find a database called "Daily Tasks" or "Tasks" or "Todo". If none exists, create a new page at the workspace top level titled "Daily Tasks — {os.popen("date +%Y-%m-%d").read().strip()}".

2. Add these tasks (each as a separate item if using a database, or as a checklist if creating a page):
{chr(10).join(f"- {t}" for t in tasks)}

3. Set the Date property to today if the database has one.
4. Set Status to "Not started" or "To Do" if available.

Confirm by printing: NOTION_TASKS_CREATED|{{number of tasks}}'''

    result = subprocess.run(
        ["/Users/yashbishnoi/.local/bin/claude", "-p", prompt, "--dangerously-skip-permissions"],
        capture_output=True, text=True, timeout=120
    )

    if "NOTION_TASKS_CREATED" in result.stdout:
        send_telegram(f"✅ Done! Created {len(tasks)} tasks in Notion:\n\n{task_list}\n\nGet after it! 💪")
        log(f"Created {len(tasks)} tasks in Notion")
    else:
        # Still confirm to user even if we can't verify
        send_telegram(f"📝 Sent {len(tasks)} tasks to Notion:\n\n{task_list}\n\nCheck Notion to confirm they're there!")
        log(f"Sent {len(tasks)} tasks to Notion (unconfirmed)")

# Save the last processed update ID
if max_update_id > 0:
    with open(last_update_file, "w") as f:
        f.write(str(max_update_id))
    log(f"Updated last_update_id to {max_update_id}")
PYEOF

echo "[$(date)] Telegram listener done." >> "$LOG_DIR/telegram-listener.log"
