"""Telegram agent — sends messages via Bot API using curl (avoids Python SSL issues)."""

import json
import subprocess
from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from shared.logging_config import get_logger

logger = get_logger(__name__)


def send_message(text: str, chat_id: str = None) -> bool:
    """Send a message to Telegram. Returns True on success."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        logger.warning("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
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
        logger.error("API error: %s", resp)
        return False
    except Exception as e:
        logger.error("Failed: %s", e)
        return False


def get_updates(offset: int = 0, long_poll: bool = False) -> list[dict]:
    """Get new messages from Telegram.

    If long_poll=True, uses 30s timeout on the API side (efficient, blocks until message arrives).
    If long_poll=False, returns immediately (for cron-based polling).
    """
    timeout_param = 30 if long_poll else 1
    curl_timeout = timeout_param + 10  # give curl extra time beyond API timeout

    try:
        result = subprocess.run(
            ["curl", "-s",
             f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
             f"?offset={offset}&timeout={timeout_param}"],
            capture_output=True, text=True, timeout=curl_timeout
        )
        data = json.loads(result.stdout)
        return data.get("result", [])
    except subprocess.TimeoutExpired:
        return []  # normal for long-poll when no messages
    except Exception as e:
        logger.debug("Telegram get_updates error: %s", e)
        return []
