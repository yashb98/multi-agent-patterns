"""Telegram agent — sends messages via Bot API using curl (avoids Python SSL issues)."""

import json
import random
import subprocess
import time
from jobpulse.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from shared.logging_config import get_logger
from shared.telegram_client import telegram_url

logger = get_logger(__name__)


def _send_single(text: str, chat_id: str, max_retries: int = 3) -> bool:
    """Send a single message chunk to Telegram with exponential backoff retry."""
    payload = json.dumps({"chat_id": chat_id, "text": text})
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST",
                 telegram_url(TELEGRAM_BOT_TOKEN, "sendMessage"),
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=15
            )
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                return True
            desc = str(resp.get("description", "")).lower()
            retry_after = resp.get("parameters", {}).get("retry_after", 0)
            if retry_after and attempt < max_retries:
                logger.warning("Telegram rate limit, retrying after %ds...", retry_after)
                time.sleep(retry_after + 1)
                continue
            if any(k in desc for k in ("timeout", "connection", "network", "temporary", "bad gateway")):
                if attempt < max_retries:
                    delay = min(2 ** attempt, 30) * (0.5 + random.random())
                    logger.warning("Telegram transient error, retrying in %.1fs...", delay)
                    time.sleep(delay)
                    continue
            logger.error("API error: %s", resp)
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 30) * (0.5 + random.random())
                logger.warning("Telegram send failed (attempt %d/%d): %s. Retrying in %.1fs...",
                               attempt + 1, max_retries + 1, e, delay)
                time.sleep(delay)
            else:
                logger.error("Failed after %d attempts: %s", max_retries + 1, e)
                return False
    return False


MAX_MSG_LEN = 4096


def send_message(text: str, chat_id: str = None) -> bool:
    """Send a message to Telegram. Splits at section boundaries if >4096 chars."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        logger.warning("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
        return False

    if len(text) <= MAX_MSG_LEN:
        return _send_single(text, cid)

    # Split on section separator lines, keeping them as part of each chunk
    chunks = []
    current = ""
    for line in text.split("\n"):
        candidate = current + line + "\n" if current else line + "\n"
        if len(candidate) > MAX_MSG_LEN:
            if current:
                chunks.append(current.rstrip("\n"))
            current = line + "\n"
        else:
            current = candidate
    if current.strip():
        chunks.append(current.rstrip("\n"))

    success = True
    for chunk in chunks:
        if not _send_single(chunk, cid):
            success = False
    return success


def send_chat_action(action: str = "typing", chat_id: str = None, max_retries: int = 2) -> bool:
    """Send a chat action (e.g. 'typing') to show the bot is processing."""
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        return False
    payload = json.dumps({"chat_id": cid, "action": action})
    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s", "-X", "POST",
                 telegram_url(TELEGRAM_BOT_TOKEN, "sendChatAction"),
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, text=True, timeout=5
            )
            resp = json.loads(result.stdout)
            if resp.get("ok"):
                return True
            desc = str(resp.get("description", "")).lower()
            retry_after = resp.get("parameters", {}).get("retry_after", 0)
            if retry_after and attempt < max_retries:
                time.sleep(retry_after + 1)
                continue
            if any(k in desc for k in ("timeout", "connection", "network", "temporary")):
                if attempt < max_retries:
                    delay = min(2 ** attempt, 10) * (0.5 + random.random())
                    time.sleep(delay)
                    continue
            return False
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 10) * (0.5 + random.random())
                time.sleep(delay)
            else:
                logger.debug("send_chat_action failed: %s", e)
                return False
    return False


def get_updates(offset: int = 0, long_poll: bool = False, max_retries: int = 2) -> list[dict]:
    """Get new messages from Telegram with retry.

    If long_poll=True, uses 30s timeout on the API side (efficient, blocks until message arrives).
    If long_poll=False, returns immediately (for cron-based polling).
    """
    timeout_param = 30 if long_poll else 1
    curl_timeout = timeout_param + 10  # give curl extra time beyond API timeout

    for attempt in range(max_retries + 1):
        try:
            result = subprocess.run(
                ["curl", "-s",
                 f"{telegram_url(TELEGRAM_BOT_TOKEN, 'getUpdates')}"
                 f"?offset={offset}&timeout={timeout_param}"],
                capture_output=True, text=True, timeout=curl_timeout
            )
            data = json.loads(result.stdout)
            return data.get("result", [])
        except subprocess.TimeoutExpired:
            return []  # normal for long-poll when no messages
        except Exception as e:
            if attempt < max_retries:
                delay = min(2 ** attempt, 10) * (0.5 + random.random())
                logger.debug("Telegram get_updates error (attempt %d): %s. Retrying in %.1fs...",
                             attempt + 1, e, delay)
                time.sleep(delay)
            else:
                logger.debug("Telegram get_updates error after %d attempts: %s", max_retries + 1, e)
                return []
    return []
