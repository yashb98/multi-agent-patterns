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
    r"|data\s*privacy\s*statement|read\s*and\s*understood|confirm\s*that\s*you"
    r"|ablehnen|verwalten|einstellungen|rechazar|gestionar|refuser|param[eè]tres)",
    re.IGNORECASE,
)


class CookieBannerDismisser:
    """Dismiss cookie consent banners via the extension bridge."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    @staticmethod
    def _has_cookie_sibling_buttons(buttons: list[dict]) -> bool:
        """Detect cookie banner by sibling buttons (Reject All, Manage Cookies, etc.)."""
        texts = [b.get("text", "").lower() for b in buttons if b.get("text")]
        cookie_siblings = (
            "reject all", "manage cookies", "cookie settings",
            "cookie preferences", "customize cookies",
        )
        return any(sib in t for t in texts for sib in cookie_siblings)

    async def dismiss(self, snapshot: Any) -> bool:
        """Try to dismiss a cookie banner. Returns True if a banner was found and clicked."""
        if hasattr(snapshot, "model_dump"):
            snapshot = snapshot.model_dump()
        buttons = snapshot.get("buttons", [])
        page_text = snapshot.get("page_text_preview", "")
        has_cookie_context = bool(_COOKIE_CONTEXT.search(page_text))

        # Also detect cookie banners by sibling buttons (e.g. "Reject All" next to "Allow All")
        if not has_cookie_context:
            has_cookie_context = self._has_cookie_sibling_buttons(buttons)

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

    IMPORTANT: Generic selectors like "I agree" / "Got it" are scoped to
    known cookie-banner containers to avoid clicking form consent elements
    (e.g. data privacy acknowledgements inside application forms).
    """
    # Highly specific — safe to match page-wide
    specific_selectors = [
        "#onetrust-accept-btn-handler",
        '[data-test-global-toast] button:has-text("Accept")',
        'section.artdeco-toast-item button:has-text("Accept")',
        "button:has-text('Accept All')",
        "button:has-text('Accept Cookies')",
        "button:has-text('Allow All')",
        "button:has-text('Allow all cookies')",
        "[data-testid='cookie-accept']",
        ".cookie-accept",
    ]
    for sel in specific_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible(timeout=timeout_ms):
                await loc.click(timeout=timeout_ms)
                logger.info("cookie_dismisser: clicked %s", sel)
                return True
        except Exception:
            continue

    # Generic "I agree" / "Got it" — only inside cookie banner containers
    _BANNER_SCOPES = [
        "#onetrust-banner-sdk",
        "[class*='cookie']",
        "[id*='cookie']",
        "[class*='consent-banner']",
        "[id*='consent']",
        "[role='dialog'][aria-label*='cookie' i]",
        "[role='dialog'][aria-label*='consent' i]",
    ]
    for scope in _BANNER_SCOPES:
        for btn_text in ("I agree", "Got it"):
            sel = f"{scope} button:has-text('{btn_text}')"
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(timeout=timeout_ms)
                    logger.info("cookie_dismisser: clicked scoped %s", sel)
                    return True
            except Exception:
                continue

    return False
