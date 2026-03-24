#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# Gmail Recruiter Check
# Runs: 1:02pm, 3:02pm, 5:02pm daily
#
# 1. Uses claude CLI + Gmail MCP to fetch new emails since last check
# 2. Classifies each into: SELECTED_NEXT_ROUND / INTERVIEW_SCHEDULING / REJECTED / OTHER
# 3. Deduplicates via data/processed_emails.txt
# 4. Logs all recruiter emails to data/recruiter_emails.log
# 5. Sends Telegram alert for categories 1-3 only (silent for OTHER)
# ─────────────────────────────────────────────────────────────────

PROJECT_DIR="/Users/yashbishnoi/Downloads/multi_agent_patterns"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
PROCESSED_FILE="$DATA_DIR/processed_emails.txt"
RECRUITER_LOG="$DATA_DIR/recruiter_emails.log"
LAST_CHECK_FILE="$DATA_DIR/last_gmail_check.txt"

source "$PROJECT_DIR/.env"

# Ensure data files exist
mkdir -p "$DATA_DIR" "$LOG_DIR"
touch "$PROCESSED_FILE" "$RECRUITER_LOG" "$LAST_CHECK_FILE"

# Get timestamp of last check (default: 24 hours ago)
LAST_CHECK=$(cat "$LAST_CHECK_FILE" 2>/dev/null || date -v-1d +%Y-%m-%dT%H:%M:%S)
NOW=$(date +%Y-%m-%dT%H:%M:%S)
TODAY=$(date +%Y-%m-%d)

echo "[$(date)] Gmail recruiter check started (since: $LAST_CHECK)" >> "$LOG_DIR/gmail.log"

# ── Use claude CLI with Gmail MCP to fetch and classify emails ──
RESULT=$(/Users/yashbishnoi/.local/bin/claude -p "
You have access to Gmail via MCP. Follow these steps EXACTLY:

STEP 1: Search Gmail for ALL emails received after $LAST_CHECK. Use this search:
- 'after:$TODAY'
This gets ALL emails — not just ones labeled as recruiter. Any email could contain job-related content.

STEP 2: For EACH email found, extract: message_id, sender name, sender email, subject, first 500 characters of body text.

STEP 3: Check each email's message_id against this list of ALREADY PROCESSED IDs (skip any that appear here):
$(cat "$PROCESSED_FILE" 2>/dev/null | tail -200)

STEP 4: For each NEW (not already processed) email, READ the subject + body carefully and classify into EXACTLY ONE category:
- SELECTED_NEXT_ROUND: The email says you've been selected, congratulations, moving forward, pleased to inform, progressed to next stage, shortlisted, we'd like to invite you to the next round
- INTERVIEW_SCHEDULING: The email asks about your availability, wants to schedule an interview, provides a booking link, asks you to pick a time slot, mentions calendar, asks when you're free
- REJECTED: The email says unfortunately, we regret to inform, you have not been selected, decided to proceed with other candidates, not moving forward, wish you the best in your future, position has been filled
- OTHER: Newsletters, promotions, social media, receipts, shipping, anything NOT related to job applications or interviews

STEP 5: Output ONLY a valid JSON array (no markdown, no explanation) of the NEW non-OTHER emails:
[{\"id\":\"msg_id\",\"sender\":\"name <email>\",\"subject\":\"...\",\"category\":\"SELECTED_NEXT_ROUND|INTERVIEW_SCHEDULING|REJECTED\",\"snippet\":\"first 100 chars of body\"}]

If no job-related emails found, output: []
If all emails are OTHER category, output: []

IMPORTANT: Output ONLY the JSON array. Nothing else. No markdown fences. Scan EVERY email, not just ones that look like recruiter emails from the subject.
" --dangerously-skip-permissions 2>> "$LOG_DIR/gmail.log")

# Update last check timestamp
echo "$NOW" > "$LAST_CHECK_FILE"

# ── Parse results and send notifications ──
python3 << 'PYEOF' "$RESULT" "$PROCESSED_FILE" "$RECRUITER_LOG" "$TELEGRAM_BOT_TOKEN" "$TELEGRAM_CHAT_ID" "$LOG_DIR/gmail.log"
import json
import sys
import os
from datetime import datetime
raw_result = sys.argv[1]
processed_file = sys.argv[2]
recruiter_log = sys.argv[3]
bot_token = sys.argv[4]
chat_id = sys.argv[5]
log_file = sys.argv[6]

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
        log(f"Telegram sent: {text[:80]}...")
    except Exception as e:
        log(f"Telegram failed: {e}")

# Parse the JSON result from claude
try:
    # Strip any markdown fences if claude added them
    cleaned = raw_result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0]
    emails = json.loads(cleaned)
except (json.JSONDecodeError, ValueError) as e:
    log(f"Failed to parse claude output: {e}")
    log(f"Raw output: {raw_result[:500]}")
    emails = []

if not emails:
    log("No new recruiter emails found")
    sys.exit(0)

# Read already processed IDs
with open(processed_file, "r") as f:
    processed_ids = set(f.read().strip().split("\n"))

now = datetime.now().isoformat()
new_count = 0

for email in emails:
    email_id = email.get("id", "")
    if not email_id or email_id in processed_ids:
        continue

    category = email.get("category", "OTHER")
    sender = email.get("sender", "Unknown")
    subject = email.get("subject", "No subject")
    snippet = email.get("snippet", "")

    # Skip OTHER
    if category == "OTHER":
        continue

    new_count += 1

    # Log to recruiter_emails.log (append-only)
    with open(recruiter_log, "a") as f:
        f.write(f"{now}|{category}|{sender}|{subject}\n")

    # Mark as processed (dedup)
    with open(processed_file, "a") as f:
        f.write(f"{email_id}\n")

    # Send Telegram notification based on category
    if category == "SELECTED_NEXT_ROUND":
        emoji = "✅"
        label = "SELECTED"
        send_telegram(f"📧 RECRUITER UPDATE\n\n{emoji} {label}: {sender}\n\"{subject}\"\n\n🎉 Congratulations! Check your email for details.")
    elif category == "INTERVIEW_SCHEDULING":
        emoji = "📅"
        label = "INTERVIEW"
        send_telegram(f"📧 RECRUITER UPDATE\n\n{emoji} {label}: {sender}\n\"{subject}\"\n\n🚨 Action needed — reply to schedule your interview!")
    elif category == "REJECTED":
        emoji = "❌"
        label = "REJECTED"
        send_telegram(f"📧 RECRUITER UPDATE\n\n{emoji} {label}: {sender}\n\"{subject}\"\n\nOnward to the next one 💪")

log(f"Processed {new_count} new recruiter email(s)")
PYEOF

echo "[$(date)] Gmail check done." >> "$LOG_DIR/gmail.log"
