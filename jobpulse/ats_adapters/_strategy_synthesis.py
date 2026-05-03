"""Synthesize a LearnedStrategy from FormExperienceDB when a domain has enough history.

Returns None below the apply-count threshold so callers fall back to GenericStrategy.
"""
from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.ats_adapters.learned_strategy import LearnedStrategy, _normalize_domain

logger = get_logger(__name__)

# Minimum successful applications before synthesis trusts the FE data.
# Below this, fall back to GenericStrategy.
_MIN_APPLY_COUNT = 3


def _get_fe_db():
    """Lazy accessor — patchable in tests."""
    from jobpulse.form_experience_db import FormExperienceDB
    return FormExperienceDB()


def synthesize_strategy_for_domain(domain_or_url: str | None) -> LearnedStrategy | None:
    """Return a LearnedStrategy if the domain has ≥3 successful applies in FE.

    Returns None if the domain is unknown to FE or has too few applies.
    """
    domain = _normalize_domain(domain_or_url)
    if not domain:
        return None

    try:
        record = _get_fe_db().lookup(domain)
    except Exception as exc:
        logger.debug("synthesize_strategy_for_domain: lookup failed: %s", exc)
        return None

    if not record:
        return None

    apply_count = record.get("apply_count", 0) or 0
    if apply_count < _MIN_APPLY_COUNT:
        return None

    logger.info(
        "Synthesized LearnedStrategy for %s (apply_count=%d)",
        domain, apply_count,
    )
    return LearnedStrategy(domain=domain, apply_count=apply_count)
