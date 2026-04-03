"""ATS adapter registry.

When APPLICATION_ENGINE=extension, get_adapter() returns an ExtensionAdapter
that uses the Chrome extension via WebSocket instead of Playwright.
"""

from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter
from jobpulse.ats_adapters.generic import GenericAdapter
from jobpulse.ats_adapters.greenhouse import GreenhouseAdapter
from jobpulse.ats_adapters.indeed import IndeedAdapter
from jobpulse.ats_adapters.lever import LeverAdapter
from jobpulse.ats_adapters.linkedin import LinkedInAdapter
from jobpulse.ats_adapters.workday import WorkdayAdapter

ADAPTERS: dict[str, BaseATSAdapter] = {
    "linkedin": LinkedInAdapter(),
    "indeed": IndeedAdapter(),
    "greenhouse": GreenhouseAdapter(),
    "lever": LeverAdapter(),
    "workday": WorkdayAdapter(),
    "generic": GenericAdapter(),
}

# Singleton extension adapter — created lazily when APPLICATION_ENGINE=extension
_ext_adapter: BaseATSAdapter | None = None


def _get_extension_adapter() -> BaseATSAdapter:
    """Lazily create and return the shared ExtensionAdapter."""
    global _ext_adapter
    if _ext_adapter is None:
        from jobpulse.ext_adapter import ExtensionAdapter
        from jobpulse.ext_bridge import ExtensionBridge

        bridge = ExtensionBridge()
        _ext_adapter = ExtensionAdapter(bridge)
    return _ext_adapter


def get_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Return the adapter for the given platform, or the generic fallback.

    When APPLICATION_ENGINE=extension, always returns the ExtensionAdapter
    regardless of platform (the extension handles all platforms via state machines).
    """
    from jobpulse.config import APPLICATION_ENGINE

    if APPLICATION_ENGINE == "extension":
        return _get_extension_adapter()

    if ats_platform and ats_platform in ADAPTERS:
        return ADAPTERS[ats_platform]
    return ADAPTERS["generic"]


__all__ = [
    "ADAPTERS",
    "BaseATSAdapter",
    "GenericAdapter",
    "GreenhouseAdapter",
    "IndeedAdapter",
    "LeverAdapter",
    "LinkedInAdapter",
    "WorkdayAdapter",
    "get_adapter",
]
