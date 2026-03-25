#!/usr/bin/env python3
"""Claude Code hook: Send bash command approvals to Telegram and wait for response.

This script is called by Claude Code's PreToolUse hook for Bash commands.
It sends the command to Telegram, waits for yes/no, and exits with
code 0 (approve) or 2 (block).

Usage in .claude/settings.json:
  "PreToolUse": [{
    "matcher": "Bash",
    "hooks": [{
      "type": "command",
      "command": "python scripts/telegram_approve.py",
      "timeout": 120
    }]
  }]

The hook receives tool input via CLAUDE_TOOL_INPUT env var (JSON).
Exit codes: 0 = allow, 2 = block
"""

import json
import os
import sys
import time
import subprocess

# Load env
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_DIR, ".env"))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Auto-approve list — don't bother asking for these
AUTO_APPROVE = [
    "ls", "cat ", "head ", "tail ", "wc ", "pwd", "date", "which ",
    "echo ", "python -c", "python -m pytest", "grep ", "git status",
    "git log", "git diff", "git branch",
]

# Always block — don't even ask
ALWAYS_BLOCK = [
    "rm -rf", "sudo", "shutdown", "reboot", "> /dev",
]


def send_telegram(text: str) -> bool:
    """Send a message to Telegram."""
    payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text})
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=10
        )
        return json.loads(result.stdout).get("ok", False)
    except Exception:
        return False


def get_latest_reply(after_id: int) -> str | None:
    """Poll Telegram for a reply after a given update_id."""
    try:
        result = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
             f"?offset={after_id + 1}&timeout=5"],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        for update in data.get("result", []):
            msg = update.get("message", {})
            from_id = str(msg.get("from", {}).get("id", ""))
            text = msg.get("text", "").strip().lower()
            if from_id == TELEGRAM_CHAT_ID and text in ("yes", "y", "approve", "no", "n", "reject", "block"):
                return text
    except Exception:
        pass
    return None


def get_current_update_id() -> int:
    """Get the latest update_id so we only look at replies after our question."""
    try:
        result = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
             f"?offset=-1"],
            capture_output=True, text=True, timeout=10
        )
        data = json.loads(result.stdout)
        results = data.get("result", [])
        if results:
            return results[-1].get("update_id", 0)
    except Exception:
        pass
    return 0


def main():
    # Get tool input from env
    tool_input_raw = os.getenv("CLAUDE_TOOL_INPUT", "{}")
    try:
        tool_input = json.loads(tool_input_raw)
    except json.JSONDecodeError:
        sys.exit(0)  # Can't parse, allow

    command = tool_input.get("command", "")
    if not command:
        sys.exit(0)  # No command, allow

    # Auto-approve safe commands
    cmd_lower = command.lower().strip()
    for prefix in AUTO_APPROVE:
        if cmd_lower.startswith(prefix):
            sys.exit(0)

    # Always block dangerous commands
    for pattern in ALWAYS_BLOCK:
        if pattern in cmd_lower:
            send_telegram(f"🚫 AUTO-BLOCKED: {command[:200]}")
            sys.exit(2)

    # If no Telegram configured, allow (fall back to CLI approval)
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        sys.exit(0)

    # Get current update_id before sending question
    last_id = get_current_update_id()

    # Ask on Telegram
    msg = f"🔐 CLAUDE CODE APPROVAL\n\nCommand:\n{command[:500]}\n\nReply yes or no (1 hour timeout)"
    sent = send_telegram(msg)
    if not sent:
        sys.exit(0)  # Telegram failed, fall back to CLI

    # Poll for reply (max 1 hour)
    deadline = time.time() + 3600
    while time.time() < deadline:
        reply = get_latest_reply(last_id)
        if reply:
            if reply in ("yes", "y", "approve"):
                send_telegram(f"✅ Approved: {command[:100]}")
                sys.exit(0)
            else:
                send_telegram(f"❌ Blocked: {command[:100]}")
                sys.exit(2)
        time.sleep(3)

    # Timeout — block by default
    send_telegram(f"⏰ Timeout — blocked: {command[:100]}")
    sys.exit(2)


if __name__ == "__main__":
    main()
