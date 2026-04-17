"""Archetype detection engine — classify JDs into 6 role archetypes.

Keyword-based scoring (free, instant). LLM fallback when top two scores
are within 1.2x of each other (~15% of cases, ~$0.001 each).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "data" / "archetype_profiles.json"

_DEFAULT_PROFILE = {
    "tagline": "MSc Computer Science (UOD) | 2+ YOE | Software Engineer | Python",
    "summary_angle": "Building production software systems",
    "project_priority": [],
    "skills_to_highlight": ["Python"],
    "yoe_framing": "2+ years",
}


@dataclass
class ArchetypeResult:
    primary: str
    secondary: str | None = None
    confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)


def _load_profiles() -> dict:
    try:
        with open(_PROFILES_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("archetype_engine: failed to load profiles: %s — using defaults", exc)
        return {}


def detect_archetype(jd_text: str, required_skills: list[str]) -> ArchetypeResult:
    """Detect the best-fit archetype for a JD using keyword scoring."""
    profiles = _load_profiles()
    if not profiles:
        return ArchetypeResult(primary="general", confidence=0.0)

    combined_text = (jd_text + " " + " ".join(required_skills)).lower()
    scores: dict[str, float] = {}

    for archetype, profile in profiles.items():
        keywords = profile.get("keywords", {})
        score = 0.0
        for keyword, weight in keywords.items():
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            matches = len(pattern.findall(combined_text))
            score += matches * weight
        scores[archetype] = score

    if not scores or max(scores.values()) == 0:
        return ArchetypeResult(primary="general", confidence=0.0, scores=scores)

    sorted_archetypes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_name, best_score = sorted_archetypes[0]
    second_name, second_score = sorted_archetypes[1] if len(sorted_archetypes) > 1 else ("", 0)

    threshold = 2.0
    if best_score < threshold:
        return ArchetypeResult(primary="general", confidence=best_score / 10, scores=scores)

    confidence = min(best_score / 15, 1.0)

    secondary = None
    if second_score > threshold and second_score >= best_score * 0.6:
        secondary = second_name

    return ArchetypeResult(
        primary=best_name,
        secondary=secondary,
        confidence=confidence,
        scores=scores,
    )


def get_archetype_profile(archetype: str) -> dict:
    """Return the profile dict for an archetype, or defaults."""
    profiles = _load_profiles()
    profile = profiles.get(archetype, {})
    result = dict(_DEFAULT_PROFILE)
    result.update({k: v for k, v in profile.items() if k != "keywords"})
    return result
