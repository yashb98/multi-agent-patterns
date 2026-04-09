"""Centralized Notion API client — single implementation with retry + error handling.

All Notion API calls in the codebase should go through ``notion_api()`` from this
module.  Previously 5 copies existed (3 degraded, missing retry/401 handling).

Usage::

    from jobpulse.notion_client import notion_api

    result = notion_api("POST", "/databases/{db_id}/query", {"filter": {...}})
"""

from __future__ import annotations

import json
import subprocess
import time

from shared.logging_config import get_logger

from jobpulse.config import NOTION_API_KEY

logger = get_logger(__name__)

_NOTION_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_MAX_RETRIES = 3


def notion_api(method: str, endpoint: str, data: dict | None = None) -> dict:
    """Call Notion API via curl with retry and error handling.

    Features (previously only in notion_agent.py, missing from 3 other copies):
    - 401 detection (bad API key) — immediate return, no retry
    - 429 rate-limit retry — up to 3 attempts with backoff
    - Timeout retry — up to 3 attempts
    - JSON parse error handling

    Returns empty dict on failure — callers must check for expected keys.
    """
    if not NOTION_API_KEY:
        logger.error("NOTION_API_KEY not set — skipping Notion API call to %s", endpoint)
        return {}

    cmd = [
        "curl", "-s", "-X", method,
        f"{_NOTION_BASE}{endpoint}",
        "-H", f"Authorization: Bearer {NOTION_API_KEY}",
        "-H", "Content-Type: application/json",
        "-H", f"Notion-Version: {_NOTION_VERSION}",
    ]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    for attempt in range(_MAX_RETRIES):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if not result.stdout:
                logger.warning("Notion API: empty response for %s %s", method, endpoint)
                return {}

            parsed = json.loads(result.stdout)

            if parsed.get("object") == "error":
                status = parsed.get("status", 0)
                msg = parsed.get("message", "unknown error")

                if status == 401:
                    logger.error("Notion API 401: unauthorized — check NOTION_API_KEY")
                    return {}

                if status == 429:
                    retry_after = int(parsed.get("retry_after", 2))
                    wait = max(retry_after, 2 * (attempt + 1))
                    logger.warning(
                        "Notion API 429: rate limited, retrying in %ds (attempt %d/%d)",
                        wait, attempt + 1, _MAX_RETRIES,
                    )
                    time.sleep(wait)
                    continue

                logger.error("Notion API error %d: %s (endpoint: %s)", status, msg, endpoint)
                return {}

            return parsed

        except json.JSONDecodeError as e:
            logger.error("Notion API: invalid JSON response for %s: %s", endpoint, e)
            return {}
        except subprocess.TimeoutExpired:
            logger.warning(
                "Notion API timeout for %s %s (attempt %d/%d)",
                method, endpoint, attempt + 1, _MAX_RETRIES,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(2)
                continue
            return {}
        except Exception as e:
            logger.error("Notion API error: %s", e)
            return {}

    logger.error("Notion API: failed after %d attempts for %s %s", _MAX_RETRIES, method, endpoint)
    return {}
