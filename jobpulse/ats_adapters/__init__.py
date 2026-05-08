"""ATS adapter registry — all platforms route through PlaywrightAdapter.

Platform-specific quirks handled by BasePlatformStrategy subclasses
(strategy.py). Each strategy registers itself via @register_strategy.
"""
from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter

# Import all strategies to trigger self-registration
# noqa: F401 — imported for side effects (registry population)
import jobpulse.ats_adapters.generic
import jobpulse.ats_adapters.greenhouse
import jobpulse.ats_adapters.lever
import jobpulse.ats_adapters.workday
import jobpulse.ats_adapters.linkedin
import jobpulse.ats_adapters.indeed
import jobpulse.ats_adapters.ashby
import jobpulse.ats_adapters.icims
import jobpulse.ats_adapters.smartrecruiters


def get_adapter() -> BaseATSAdapter:
    """Return the PlaywrightAdapter — the only ATS adapter post-2026-04 unification.

    Platform-specific behavior is handled by ``BasePlatformStrategy`` subclasses
    loaded inside the form engine via ``get_strategy(platform, url)``.
    """
    from jobpulse.playwright_adapter import PlaywrightAdapter

    return PlaywrightAdapter()


def reset_adapter() -> None:
    """No-op — kept for test compatibility."""


__all__ = ["BaseATSAdapter", "get_adapter", "reset_adapter"]
