"""Generic SSO button discovery for providers not in the hardcoded list."""
from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Defer to SSOHandler's hardcoded handler when these known providers are present
_KNOWN_PROVIDERS = ("google", "linkedin", "microsoft", "apple")

_SSO_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("okta", re.compile(r"\b(continue|sign\s*in|log\s*in)\s*(with\s+|via\s+)?okta\b", re.I)),
    ("auth0", re.compile(r"\b(continue|sign\s*in|log\s*in)\s*(with\s+|via\s+)?auth0\b", re.I)),
    ("workos", re.compile(r"\b(continue|sign\s*in|log\s*in)\s*(with\s+|via\s+)?workos\b", re.I)),
    ("onelogin", re.compile(r"\b(continue|sign\s*in|log\s*in)\s*(with\s+|via\s+)?onelogin\b", re.I)),
    ("ping_identity", re.compile(r"\bping\s*identity\b", re.I)),
    ("generic_sso", re.compile(r"\bsign\s*in\s*with\s*sso\b", re.I)),
    ("generic_sso", re.compile(r"\bcontinue\s*with\s*sso\b", re.I)),
    ("generic_sso", re.compile(r"\b(use|continue\s*with)\s*(your|my)?\s*company\s*(login|sso|account)\b", re.I)),
    ("generic_sso", re.compile(r"\bcorporate\s*(login|sso|sign\s*in|account)\b", re.I)),
    ("generic_sso", re.compile(r"\benterprise\s*(login|sso|sign\s*in|account)\b", re.I)),
]


def detect_sso_button_patterns(buttons: list[dict] | None) -> dict | None:
    """Detect generic SSO buttons not handled by SSOHandler's hardcoded list.

    Returns {"provider": str, "button_text": str, "selector": str} or None.
    Returns None when a known provider (Google/LinkedIn/Microsoft/Apple) is present,
    deferring to SSOHandler's priority-ranked handling.
    """
    if not buttons:
        return None

    # Defer to existing handler if a known provider is present
    for btn in buttons:
        text = (btn.get("text") or "").lower()
        for known in _KNOWN_PROVIDERS:
            if (
                f"with {known}" in text
                or f"via {known}" in text
                or f"continue {known}" in text
            ):
                return None

    for btn in buttons:
        text = btn.get("text") or ""
        for provider, pattern in _SSO_PATTERNS:
            if pattern.search(text):
                logger.info(
                    "Generic SSO detected: provider=%s button=%r", provider, text[:60]
                )
                return {
                    "provider": provider,
                    "button_text": text,
                    "selector": btn.get("selector", ""),
                }

    return None
