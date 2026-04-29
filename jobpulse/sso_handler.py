"""SSO (Single Sign-On) detection and handling.

Detects "Sign in with Google", "Continue with LinkedIn" etc. on login/signup pages.
When SSO is available, clicking it is faster and more reliable than creating
a new email+password account.

Also handles the Google account chooser ("Continue as Yash") that appears
after the initial SSO click when the user is already signed into Google.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# SSO button patterns — (regex, provider name)
_SSO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*google", re.IGNORECASE), "google"),
    (re.compile(r"google\s*(sign\s*in|login|sso)", re.IGNORECASE), "google"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*linkedin", re.IGNORECASE), "linkedin"),
    (re.compile(r"linkedin\s*(sign\s*in|login|sso)", re.IGNORECASE), "linkedin"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*microsoft", re.IGNORECASE), "microsoft"),
    (re.compile(r"(sign\s*in|continue|log\s*in)\s*with\s*apple", re.IGNORECASE), "apple"),
]

# Google account chooser patterns — appears AFTER clicking "Sign in with Google"
_GOOGLE_CHOOSER_PATTERNS: list[re.Pattern] = [
    re.compile(r"continue\s+as\s+\w+", re.IGNORECASE),          # "Continue as Yash"
    re.compile(r"continue\s*", re.IGNORECASE),                   # Just "Continue"
    re.compile(r"use\s+another\s+account", re.IGNORECASE),       # "Use another account"
]

# Prefer these providers (we have Google OAuth already)
_PROVIDER_PRIORITY = {"google": 100, "linkedin": 80, "microsoft": 50, "apple": 30}


class SSOHandler:
    """Detect and use SSO buttons on login/signup pages."""

    def __init__(self, bridge: Any):
        self.bridge = bridge

    def detect_sso(self, snapshot: dict) -> dict | None:
        """Detect SSO buttons. Returns {provider, selector} or None."""
        buttons = snapshot.get("buttons", [])
        candidates: list[dict] = []

        for btn in buttons:
            text = btn.get("text", "")
            if not btn.get("enabled", True) or not text:
                continue
            for pattern, provider in _SSO_PATTERNS:
                if pattern.search(text):
                    candidates.append({
                        "provider": provider,
                        "selector": btn["selector"],
                        "text": text,
                        "priority": _PROVIDER_PRIORITY.get(provider, 0),
                    })
                    break

        if not candidates:
            return None

        # Return highest priority SSO option
        candidates.sort(key=lambda x: x["priority"], reverse=True)
        best = candidates[0]
        logger.info("SSO detected: %s ('%s')", best["provider"], best["text"])
        return {"provider": best["provider"], "selector": best["selector"]}

    async def click_sso(self, sso: dict):
        """Click an SSO button and wait for redirect, handling account choosers."""
        import asyncio
        logger.info("Clicking SSO: %s at %s", sso["provider"], sso["selector"])
        await self.bridge.click(sso["selector"])

        for i in range(30):
            await asyncio.sleep(0.5)
            try:
                snap = await self.bridge.get_snapshot()
                if hasattr(snap, "model_dump"):
                    snap = snap.model_dump()
                url = snap.get("url", "").lower()

                # Handle Google account chooser ("Continue as Yash")
                if "accounts.google" in url or "oauth" in url:
                    await self._handle_google_chooser(snap)
                    continue

                # If we're no longer on a login/auth page, we're done
                if "login" not in url and "signin" not in url and "auth" not in url:
                    break

            except Exception:
                continue

        logger.info("SSO flow completed for %s", sso["provider"])

    async def _handle_google_chooser(self, snapshot: dict) -> bool:
        """Handle the Google 'Continue as <Name>' account chooser.

        Returns True if a button was clicked.
        """
        import asyncio
        buttons = snapshot.get("buttons", [])
        page_text = snapshot.get("page_text_preview", "").lower()

        # Look for "Continue as <Name>" or "Continue" buttons
        for btn in buttons:
            text = btn.get("text", "")
            if not text:
                continue
            for pattern in _GOOGLE_CHOOSER_PATTERNS:
                if pattern.search(text):
                    logger.info("Google chooser: clicking '%s'", text)
                    try:
                        await self.bridge.click(btn["selector"])
                        await asyncio.sleep(1.5)
                        return True
                    except Exception as exc:
                        logger.warning("Google chooser click failed: %s", exc)
                        continue

        # Fallback: if page contains "Continue as" but no button matched,
        # try generic button selectors
        if "continue as" in page_text:
            logger.info("Google chooser detected — trying generic button click")
            for btn in buttons:
                if btn.get("enabled") and re.search(r"continue", btn.get("text", ""), re.IGNORECASE):
                    try:
                        await self.bridge.click(btn["selector"])
                        await asyncio.sleep(1.5)
                        return True
                    except Exception:
                        continue

        return False
