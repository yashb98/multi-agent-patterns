"""ATS adapter registry — extension-only mode.

All job applications route through ExtensionAdapter which uses the
Chrome extension via HTTP API for form filling.
"""

from jobpulse.ats_adapters.base import BaseATSAdapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the ExtensionAdapter (sole adapter).

    The ats_platform parameter is retained for interface compatibility
    but is not used for routing — all platforms go through the extension.
    """
    from jobpulse.ext_adapter import ExtensionAdapter
    from jobpulse.ext_bridge import ExtensionBridge

    if not hasattr(get_adapter, "_instance"):
        bridge = ExtensionBridge()
        get_adapter._instance = ExtensionAdapter(bridge)
    return get_adapter._instance


__all__ = ["BaseATSAdapter", "get_adapter"]
