"""Telegram listener — polls for replies, creates Notion tasks. No claude -p dependency."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DATA_DIR, LOGS_DIR
from jobpulse import telegram_agent, notion_agent


LAST_UPDATE_FILE = DATA_DIR / "telegram_last_update_id.txt"


def _get_last_update_id() -> int:
    try:
        return int(LAST_UPDATE_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _save_last_update_id(uid: int):
    LAST_UPDATE_FILE.write_text(str(uid))


def _log(msg: str):
    log_file = LOGS_DIR / "telegram-listener.log"
    with open(log_file, "a") as f:
        f.write(f"[{datetime.now().isoformat()}] {msg}\n")


def _parse_tasks(text: str) -> list[str]:
    """Parse a message into individual task strings."""
    tasks = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip common prefixes
        for prefix in ["- ", "• ", "□ ", "* ", "✅ ", "☐ ", "And ", "and "]:
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        # Strip numbered prefixes like "1. " or "1) "
        if len(line) > 2 and line[0].isdigit() and line[1] in ".) ":
            line = line[2:].strip()
        elif len(line) > 3 and line[:2].isdigit() and line[2] in ".) ":
            line = line[3:].strip()
        if line and len(line) > 2:
            tasks.append(line)
    return tasks


def poll_and_process():
    """Main entry: check for new Telegram messages, create tasks if found."""
    last_id = _get_last_update_id()
    _log(f"Checking (after update_id: {last_id})")

    updates = telegram_agent.get_updates(offset=last_id + 1)

    if not updates:
        _log("No new messages")
        return

    max_id = last_id

    for update in updates:
        uid = update.get("update_id", 0)
        max_id = max(max_id, uid)

        msg = update.get("message", {})
        from_id = str(msg.get("from", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # Only process messages from Yash
        if from_id != TELEGRAM_CHAT_ID or not text:
            continue

        _log(f"Got message: {text[:100]}")

        # Skip if it's a simple greeting
        if text.lower() in ("hi", "hello", "hey"):
            continue

        # Skip response
        if text.lower() in ("skip", "no", "nah", "not today", "pass"):
            telegram_agent.send_message("👍 Got it — no tasks for today. Enjoy your day!")
            _log("User skipped")
            continue

        # Parse as tasks
        tasks = _parse_tasks(text)

        if not tasks:
            _log(f"Could not parse tasks from: {text[:50]}")
            continue

        # Create in Notion using direct API
        today = datetime.now().strftime("%Y-%m-%d")
        created = 0
        for task in tasks:
            if notion_agent.create_task(task, today):
                created += 1

        task_list = "\n".join(f"  □ {t}" for t in tasks)

        if created > 0:
            telegram_agent.send_message(
                f"✅ Created {created} tasks in Notion:\n\n{task_list}\n\nGet after it! 💪"
            )
            _log(f"Created {created} tasks in Notion")
        else:
            telegram_agent.send_message(
                f"📝 Tried to create {len(tasks)} tasks but Notion API failed. Check your integration."
            )
            _log(f"Failed to create tasks in Notion")

    # Save checkpoint
    if max_id > last_id:
        _save_last_update_id(max_id)
        _log(f"Updated checkpoint to {max_id}")


if __name__ == "__main__":
    poll_and_process()
