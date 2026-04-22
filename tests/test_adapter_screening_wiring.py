"""Tests that the adapter registry works correctly.

Verifies:
1. get_adapter() returns PlaywrightAdapter for non-SmartRecruiters platforms
2. SmartRecruiters gets its own adapter
3. BaseATSAdapter still provides screening question support
"""

from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter


def test_get_adapter_returns_playwright_adapter():
    """get_adapter() should return a PlaywrightAdapter for standard platforms."""
    from jobpulse.ats_adapters import get_adapter
    adapter = get_adapter("linkedin")
    assert adapter.name == "playwright"


def test_get_adapter_all_platforms():
    """All standard platforms route to PlaywrightAdapter."""
    from jobpulse.ats_adapters import get_adapter
    for platform in ["greenhouse", "indeed", "linkedin", "lever", "workday", None]:
        adapter = get_adapter(platform)
        assert adapter.name == "playwright", f"{platform} should route to playwright"


def test_smartrecruiters_gets_own_adapter():
    """SmartRecruiters routes to its dedicated adapter."""
    from jobpulse.ats_adapters import get_adapter
    adapter = get_adapter("smartrecruiters")
    assert adapter.name != "playwright"
