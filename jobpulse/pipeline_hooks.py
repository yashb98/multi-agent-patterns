"""Feature-flagged wrappers for the scan pipeline.

All new career-ops features integrate through this module.
Each wrapper checks an env var and either delegates to the new
feature or passes through to the original function unchanged.
"""

import os
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Lazy-loaded references — populated on first use so tests can patch them
# on this module (e.g. patch("jobpulse.pipeline_hooks.detect_ghost_job")).
detect_ghost_job = None


def feature_enabled(env_var: str) -> bool:
    """Check if a feature flag env var is set to true."""
    return os.getenv(env_var, "false").lower() == "true"


# ---------------------------------------------------------------------------
# Ghost Detection wrapper (F2)
# ---------------------------------------------------------------------------


def _ensure_ghost_detector():
    global detect_ghost_job
    if detect_ghost_job is None:
        from jobpulse.ghost_detector import detect_ghost_job as _fn
        detect_ghost_job = _fn


def with_ghost_detection(
    listings: list[Any],
    jd_texts: dict[str, str],
) -> list[Any]:
    """Filter listings through ghost detection when enabled. Pass-through when disabled."""
    if not feature_enabled("JOBPULSE_GHOST_DETECTION"):
        return listings

    _ensure_ghost_detector()

    result = []
    for listing in listings:
        try:
            jd = jd_texts.get(listing.job_id, getattr(listing, "description_raw", ""))
            ghost = detect_ghost_job(listing, jd)
            listing.ghost_tier = ghost.tier
            if not ghost.should_block:
                result.append(listing)
            else:
                logger.info(
                    "pipeline_hooks: ghost blocked %s @ %s — tier=%s",
                    listing.title, listing.company, ghost.tier,
                )
        except Exception as exc:
            logger.warning("pipeline_hooks: ghost detection failed for %s: %s", listing.job_id, exc)
            result.append(listing)
    return result


# ---------------------------------------------------------------------------
# Archetype Detection wrapper (F3)
# ---------------------------------------------------------------------------


def with_archetype_detection(listing: Any) -> None:
    """Detect and attach archetype to listing when enabled. No-op when disabled."""
    if not feature_enabled("JOBPULSE_ARCHETYPE_ENGINE"):
        return

    from jobpulse.archetype_engine import detect_archetype

    try:
        result = detect_archetype(
            getattr(listing, "description_raw", ""),
            getattr(listing, "required_skills", []),
        )
        listing.archetype = result.primary
        listing.archetype_secondary = result.secondary
        listing.archetype_confidence = result.confidence
    except Exception as exc:
        logger.warning("pipeline_hooks: archetype detection failed for %s: %s", listing.job_id, exc)


# ---------------------------------------------------------------------------
# Enhanced generate_materials wrapper (F5, F6)
# ---------------------------------------------------------------------------


def enhanced_generate_materials(
    original_fn: Any,
    listing: Any,
    screen: Any,
    db: Any,
    repos: list[dict],
    notion_failures: list[str],
) -> Any:
    """Wrap generate_materials with archetype framing and ATS normalization."""
    bundle = original_fn(listing, screen, db, repos, notion_failures)

    if feature_enabled("JOBPULSE_ATS_NORMALIZE") and bundle.cv_path:
        try:
            from jobpulse.cv_templates.generate_cv import normalize_text_for_ats

            if bundle.cv_text:
                normalized, counts = normalize_text_for_ats(bundle.cv_text)
                total = sum(counts.values())
                if total > 0:
                    logger.info(
                        "pipeline_hooks: normalized %d chars in CV for %s",
                        total, listing.company,
                    )
                bundle.cv_text = normalized
        except Exception as exc:
            logger.warning("pipeline_hooks: ATS normalize failed: %s", exc)

    return bundle


# ---------------------------------------------------------------------------
# Tone Framework wrapper (F7)
# ---------------------------------------------------------------------------


def with_tone_filter(answer: str, question: str, listing: Any) -> str:
    """Apply tone framework to a screening answer when enabled. Pass-through when disabled."""
    if not feature_enabled("JOBPULSE_TONE_FRAMEWORK"):
        return answer

    from jobpulse.tone_framework import apply_tone

    try:
        return apply_tone(answer, question, listing)
    except Exception as exc:
        logger.warning("pipeline_hooks: tone filter failed: %s", exc)
        return answer
