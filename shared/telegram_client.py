"""Centralized Telegram Bot API client — single URL construction and request helpers.

Replaces 12+ inline ``f"https://api.telegram.org/bot{token}/..."`` constructions
scattered across jobpulse and scripts.

Usage::

    from shared.telegram_client import telegram_url, telegram_post

    # Just the URL (for curl or custom HTTP)
    url = telegram_url(token, "sendMessage")

    # Full POST with httpx
    resp = telegram_post(token, "sendMessage", {"chat_id": cid, "text": msg})
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.telegram.org/bot{token}/{method}"
FILE_URL = "https://api.telegram.org/file/bot{token}/{file_path}"


def telegram_url(token: str, method: str) -> str:
    """Build a Telegram Bot API URL."""
    return BASE_URL.format(token=token, method=method)


def telegram_file_url(token: str, file_path: str) -> str:
    """Build a Telegram file download URL."""
    return FILE_URL.format(token=token, file_path=file_path)


def telegram_post(token: str, method: str, data: dict[str, Any] | None = None,
                  *, timeout: int = 15) -> dict:
    """POST to Telegram Bot API via curl subprocess (no extra deps).

    Returns the parsed JSON response, or ``{"ok": False}`` on failure.
    """
    url = telegram_url(token, method)
    payload = json.dumps(data or {})
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=timeout,
        )
        resp = json.loads(result.stdout)
        if not resp.get("ok"):
            logger.warning("Telegram %s error: %s", method, resp.get("description", "unknown"))
        return resp
    except Exception as e:
        logger.warning("Telegram %s failed: %s", method, e)
        return {"ok": False, "description": str(e)}


def telegram_get(token: str, method: str, params: dict[str, Any] | None = None,
                 *, timeout: int = 15) -> dict:
    """GET from Telegram Bot API via curl subprocess.

    Returns the parsed JSON response, or ``{"ok": False}`` on failure.
    """
    url = telegram_url(token, method)
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    try:
        result = subprocess.run(
            ["curl", "-s", url],
            capture_output=True, text=True, timeout=timeout,
        )
        return json.loads(result.stdout)
    except Exception as e:
        logger.warning("Telegram %s failed: %s", method, e)
        return {"ok": False, "description": str(e)}
