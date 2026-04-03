"""Auto-dismiss cookie consent banners before page detection.

Runs before every page type detection to clear overlays that would
interfere with form detection and field scanning.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Buttons to click (priority order — most specific first)
_ACCEPT_PATTERNS = [
    re.compile(r"accept\s*(all)?\s*(cookies?)?", re.IGNORECASE),
    re.compile(r"agree\s*(to\s*all|\s*&\s*continue)?", re.IGNORECASE),
    re.compile(r"i\s*agree", re.IGNORECASE),
    re.compile(r"(got\s*it|okay|ok)(!|\.)?$", re.IGNORECASE),
    re.compile(r"allow\s*(all\s*)?(cookies?)?", re.IGNORECASE),
    re.compile(r"consent", re.IGNORECASE),
]

# Secondary: close button when cookie context detected in page text
_COOKIE_CONTEXT = re.compile(
    r"(cookie|gdpr|privacy|consent|tracking)", re.IGNORECASE
)
_CLOSE_PATTERN = re.compile(r"^(close|dismiss|\u00d7|\u2715|x)$", re.IGNORECASE)

# Never click these
_ANTI_PATTERNS = re.compile(
    r"(reject|decline|manage|customize|preferences|settings|policy|learn\s*more)",
    re.IGNORECASE,
)


class CookieBannerDismisser:
    """Dismiss cookie consent banners via the extension bridge."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def dismiss(self, snapshot: dict) -> bool:
        """Try to dismiss a cookie banner. Returns True if a banner was found and clicked."""
        buttons = snapshot.get("buttons", [])
        page_text = snapshot.get("page_text_preview", "")

        # Try accept/agree buttons first
        for btn in buttons:
            text = btn.get("text", "")
            if not btn.get("enabled", True) or not text:
                continue
            if _ANTI_PATTERNS.search(text):
                continue
            for pattern in _ACCEPT_PATTERNS:
                if pattern.search(text):
                    logger.info("Dismissing cookie banner: clicking '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        # If page mentions cookies, try close button
        if _COOKIE_CONTEXT.search(page_text):
            for btn in buttons:
                text = btn.get("text", "")
                if _CLOSE_PATTERN.search(text) and not _ANTI_PATTERNS.search(text):
                    logger.info("Dismissing cookie banner via close: '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        return False
