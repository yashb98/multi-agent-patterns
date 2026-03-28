"""ATS adapter registry."""

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


def get_adapter(ats_platform: str | None) -> BaseATSAdapter:
    """Return the adapter for the given platform, or the generic fallback."""
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
