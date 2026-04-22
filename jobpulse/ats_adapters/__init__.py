"""ATS adapter registry — Playwright-only mode.

All job applications route through PlaywrightAdapter which uses
Playwright CDP for form filling. SmartRecruiters uses its own
dedicated Playwright CDP adapter (shadow DOM web components).
"""

from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the appropriate adapter for the ATS platform."""
    if ats_platform == "smartrecruiters":
        from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersAdapter
        return SmartRecruitersAdapter()
    from jobpulse.playwright_adapter import PlaywrightAdapter
    return PlaywrightAdapter()


def reset_adapter() -> None:
    """No-op — kept for test compatibility."""


__all__ = ["BaseATSAdapter", "get_adapter", "reset_adapter"]
