#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Google Calendar Check
# Runs: as part of morning-digest, and standalone at 9am/12pm/3pm
#
# Mode 1 (from morning-digest): Fetches today + tomorrow events → saves JSON
# Mode 2 (standalone cron): Also sends Telegram reminders for events starting within 2 hours
#
# Pass --remind flag to enable Telegram reminders for upcoming events
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
TOMORROW=$(date -v+1d +%Y-%m-%d)
RESULTS_FILE="$DATA_DIR/calendar-$TODAY.json"
REMIND_MODE="${1:-}"

source "$PROJECT_DIR/.env"
mkdir -p "$DATA_DIR" "$LOG_DIR"

echo "[$(date)] Calendar check started (remind=$REMIND_MODE)..." >> "$LOG_DIR/calendar.log"

# ── Fetch today's and tomorrow's events via claude + Calendar MCP ──
/Users/yashbishnoi/.local/bin/claude -p "
You have access to Google Calendar via MCP. Do the following:

1. Fetch ALL events for today ($TODAY) — from start of day to end of day.
2. Fetch ALL events for tomorrow ($TOMORROW) — from start of day to end of day.
3. For each event extract: title, start time (HH:MM AM/PM format), end time, location (if any).

4. Output ONLY valid JSON (no markdown, no explanation):
{
  \"date\": \"$TODAY\",
  \"today_events\": [
    {\"title\": \"...\", \"start\": \"10:00 AM\", \"end\": \"11:00 AM\", \"location\": \"Zoom\"}
  ],
  \"tomorrow_events\": [
    {\"title\": \"...\", \"start\": \"9:00 AM\", \"end\": \"10:00 AM\", \"location\": \"\"}
  ]
}

If no events for a day, use empty array.
IMPORTANT: Output ONLY the JSON. No markdown fences. No explanation.
" --dangerously-skip-permissions 2>> "$LOG_DIR/calendar.log" | python3 << 'PYEOF' - "$RESULTS_FILE" "$REMIND_MODE" "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_CHAT_ID" "$LOG_DIR/calendar.log"
import json
import sys
import os
from datetime import datetime
# Read claude output from stdin
raw = sys.stdin.read().strip()
results_file = sys.argv[1]
remind_mode = sys.argv[2]
bot_token = sys.argv[3]
chat_id = sys.argv[4]
log_file = sys.argv[5]

def log(msg):
    with open(log_file, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")

def send_telegram(text):
    import subprocess
    try:
        payload = json.dumps({"chat_id": chat_id, "text": text})
        subprocess.run([
            "curl", "-s", "-X", "POST",
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            "-H", "Content-Type: application/json",
            "-d", payload
        ], timeout=15, capture_output=True)
    except Exception as e:
        log(f"Telegram failed: {e}")

# Parse JSON
try:
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        raw = raw.rsplit("```", 1)[0]
    cal = json.loads(raw)
except (json.JSONDecodeError, ValueError) as e:
    log(f"Failed to parse calendar JSON: {e}")
    cal = {"date": datetime.now().strftime("%Y-%m-%d"), "today_events": [], "tomorrow_events": []}

# Save to file for morning-digest
with open(results_file, "w") as f:
    json.dump(cal, f, indent=2)

today_events = cal.get("today_events", [])
tomorrow_events = cal.get("tomorrow_events", [])

log(f"Calendar: {len(today_events)} today, {len(tomorrow_events)} tomorrow")

# ── Reminder mode: alert for events starting within 2 hours ──
if remind_mode == "--remind":
    now = datetime.now()
    for event in today_events:
        start_str = event.get("start", "")
        title = event.get("title", "Unknown")
        location = event.get("location", "")
        try:
            # Parse time like "10:00 AM"
            event_time = datetime.strptime(f"{now.strftime('%Y-%m-%d')} {start_str}", "%Y-%m-%d %I:%M %p")
            diff_minutes = (event_time - now).total_seconds() / 60
            if 0 < diff_minutes <= 120:
                hours = int(diff_minutes // 60)
                mins = int(diff_minutes % 60)
                time_str = f"{hours}h {mins}m" if hours > 0 else f"{mins} minutes"
                loc_str = f" ({location})" if location else ""
                send_telegram(f"⏰ REMINDER: \"{title}\"{loc_str} starts in {time_str} — {start_str}")
                log(f"Sent reminder for: {title} at {start_str}")
        except (ValueError, TypeError):
            pass  # skip unparseable times

# Output formatted text to stdout (for morning-digest)
print("TODAY:")
if today_events:
    for e in today_events:
        loc = f" ({e['location']})" if e.get("location") else ""
        print(f"  • {e['start']} — {e['title']}{loc}")
else:
    print("  No events today")

print("TOMORROW:")
if tomorrow_events:
    for e in tomorrow_events:
        loc = f" ({e['location']})" if e.get("location") else ""
        print(f"  • {e['start']} — {e['title']}{loc}")
else:
    print("  Nothing scheduled tomorrow")
PYEOF

echo "[$(date)] Calendar check done." >> "$LOG_DIR/calendar.log"
