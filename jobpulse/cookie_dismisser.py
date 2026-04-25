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
    re.compile(r"(alle\s*)?akzeptieren", re.IGNORECASE),
    re.compile(r"zustimmen", re.IGNORECASE),
    re.compile(r"(tout\s*)?accepter", re.IGNORECASE),
    re.compile(r"j.accepte", re.IGNORECASE),
    re.compile(r"aceptar\s*(todas?)?", re.IGNORECASE),
]

# Secondary: close button when cookie context detected in page text
_COOKIE_CONTEXT = re.compile(
    r"(cookie|gdpr|privacy|consent|tracking"
    r"|datenschutz|privacidad|confidentialit[eé]"
    r"|wir\s*verwenden|utilizamos|ce\s*site\s*utilise)", re.IGNORECASE
)
_CLOSE_PATTERN = re.compile(r"^(close|dismiss|\u00d7|\u2715|x)$", re.IGNORECASE)

# Never click these
_ANTI_PATTERNS = re.compile(
    r"(reject|decline|manage|customize|preferences|settings|policy|learn\s*more"
    r"|user\s*agreement|terms|copyright"
    r"|ablehnen|verwalten|einstellungen|rechazar|gestionar|refuser|param[eè]tres)",
    re.IGNORECASE,
)


class CookieBannerDismisser:
    """Dismiss cookie consent banners via the extension bridge."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    async def dismiss(self, snapshot: Any) -> bool:
        """Try to dismiss a cookie banner. Returns True if a banner was found and clicked."""
        if hasattr(snapshot, "model_dump"):
            snapshot = snapshot.model_dump()
        buttons = snapshot.get("buttons", [])
        page_text = snapshot.get("page_text_preview", "")
        has_cookie_context = bool(_COOKIE_CONTEXT.search(page_text))

        # Try accept/agree buttons first
        for btn in buttons:
            text = btn.get("text", "")
            if not btn.get("enabled", True) or not text:
                continue
            if not has_cookie_context and not _COOKIE_CONTEXT.search(text):
                continue
            if _ANTI_PATTERNS.search(text):
                continue
            for pattern in _ACCEPT_PATTERNS:
                if pattern.search(text):
                    logger.info("Dismissing cookie banner: clicking '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        # If page mentions cookies, try close button
        if has_cookie_context:
            for btn in buttons:
                text = btn.get("text", "")
                if _CLOSE_PATTERN.search(text) and not _ANTI_PATTERNS.search(text):
                    logger.info("Dismissing cookie banner via close: '%s'", text)
                    await self.bridge.click(btn["selector"])
                    return True

        return False


async def dismiss_cookie_banner_playwright(page: Any, timeout_ms: int = 3000) -> bool:
    """Playwright-native cookie dismissal with short visibility timeout.

    Use when the extension bridge isn't available (e.g. direct CDP sessions).
    Checks visibility before clicking to avoid timeouts on invisible elements.
    """
    selectors = [
        "#onetrust-accept-btn-handler",
        "button:has-text('Accept All')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I agree')",
        "button:has-text('Got it')",
        "button:has-text('Allow All')",
        "[data-testid='cookie-accept']",
        ".cookie-accept",
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=timeout_ms):
                await loc.click(timeout=timeout_ms)
                logger.info("cookie_dismisser: clicked %s", sel)
                return True
        except Exception:
            continue
    return False
