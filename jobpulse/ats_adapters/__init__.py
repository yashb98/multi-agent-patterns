"""ATS adapter registry — all platforms route through PlaywrightAdapter.

Platform-specific quirks handled by BasePlatformStrategy subclasses
(strategy.py). SmartRecruiters, LinkedIn, etc. register thin strategies
that override only what differs from the universal NativeFormFiller pipeline.
"""
from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the PlaywrightAdapter for all platforms.

    Platform-specific behavior is handled by strategies loaded inside
    NativeFormFiller via ``get_strategy(platform)``.
    """
    import jobpulse.ats_adapters.smartrecruiters  # noqa: F401
    import jobpulse.ats_adapters.generic  # noqa: F401

    from jobpulse.playwright_adapter import PlaywrightAdapter
    return PlaywrightAdapter()


def reset_adapter() -> None:
    """No-op — kept for test compatibility."""


__all__ = ["BaseATSAdapter", "get_adapter", "reset_adapter"]
