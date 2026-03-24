"""Telegram agent — sends messages via Bot API using curl (avoids Python SSL issues)."""

import json
import subprocess
from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_message(text: str, chat_id: str = None) -> bool:
    """Send a message to Telegram. Returns True on success."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        print("[Telegram] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    payload = json.dumps({"chat_id": cid, "text": text})
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=15
        )
        resp = json.loads(result.stdout)
        if resp.get("ok"):
            return True
        print(f"[Telegram] API error: {resp}")
        return False
    except Exception as e:
        print(f"[Telegram] Failed: {e}")
        return False


def get_updates(offset: int = 0) -> list[dict]:
    """Get new messages from Telegram."""
    try:
        result = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=5"],
            capture_output=True, text=True, timeout=15
        )
        data = json.loads(result.stdout)
        return data.get("result", [])
    except Exception:
        return []
