"""SSO (Single Sign-On) detection and handling.

Detects "Sign in with Google", "Continue with LinkedIn" etc. on login/signup pages.
When SSO is available, clicking it is faster and more reliable than creating
a new email+password account.
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
        """Click an SSO button and wait for redirect."""
        logger.info("Clicking SSO: %s at %s", sso["provider"], sso["selector"])
        await self.bridge.click(sso["selector"])
