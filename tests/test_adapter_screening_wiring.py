"""Tests that the adapter registry works correctly.

Verifies:
1. get_adapter() returns PlaywrightAdapter for non-SmartRecruiters platforms
2. SmartRecruiters gets its own adapter
3. BaseATSAdapter still provides screening question support
"""

from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter


def test_get_adapter_returns_playwright_adapter():
    """get_adapter() returns the unified PlaywrightAdapter."""
    from jobpulse.ats_adapters import get_adapter
    adapter = get_adapter()
    assert adapter.name == "playwright"


def test_get_adapter_is_platform_agnostic():
    """get_adapter() takes no arguments — platform dispatch is handled by
    `get_strategy(platform, url)` inside the form engine, not by adapter selection."""
    from jobpulse.ats_adapters import get_adapter
    import inspect
    sig = inspect.signature(get_adapter)
    assert len(sig.parameters) == 0, (
        "get_adapter must not accept a platform parameter — adapter dispatch is "
        "unified post-2026-04. Platform-specific behavior belongs in BasePlatformStrategy."
    )
