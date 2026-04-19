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


_TITLE_ARCHETYPE_MAP: dict[str, str] = {
    "data scientist": "data_scientist",
    "data analyst": "data_analyst",
    "data engineer": "data_engineer",
    "ml engineer": "ai_ml",
    "machine learning engineer": "ai_ml",
    "ai engineer": "agentic",
    "software engineer": "data_platform",
    "mlops": "data_platform",
}


def detect_archetype(
    jd_text: str, required_skills: list[str], title: str = "",
) -> ArchetypeResult:
    """Detect the best-fit archetype for a JD using keyword scoring.

    Title matching provides a boost to prevent mismatches where the JD
    content skews toward a different archetype than the role title implies.
    """
    profiles = _load_profiles()
    if not profiles:
        return ArchetypeResult(primary="general", confidence=0.0)

    combined_text = (jd_text + " " + " ".join(required_skills)).lower()
    title_lower = title.lower()
    scores: dict[str, float] = {}

    for archetype, profile in profiles.items():
        keywords = profile.get("keywords", {})
        score = 0.0
        for keyword, weight in keywords.items():
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            matches = len(pattern.findall(combined_text))
            score += matches * weight
        scores[archetype] = score

    for title_kw, arch in _TITLE_ARCHETYPE_MAP.items():
        if title_kw in title_lower and arch in scores:
            top_content = max(scores.values()) if scores else 0
            scores[arch] += max(15.0, top_content * 0.6)

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


_ARCHETYPE_SUMMARIES: dict[str, str] = {
    "agentic": (
        '<b>AI Engineer</b> who built a <b>88,500+ LOC</b> production multi-agent system with '
        '<b>4 LangGraph orchestration patterns</b>, <b>GRPO experiential learning</b>, and '
        '<b>2,350 tests</b>. Shipped <b>10+ autonomous agents</b> with human-in-the-loop flows, '
        'fact-checking, and Swarm-based routing. Specialises in <b>agentic architectures</b>, '
        '<b>tool-use</b>, and <b>production agent deployment</b>.'
    ),
    "data_platform": (
        '<b>ML Engineer</b> who built a <b>88,500+ LOC</b> production AI system with '
        '<b>2,350 tests</b> and <b>MLOps</b> pipelines. Designed multi-agent orchestration with '
        '<b>experiential learning</b> and <b>model evaluation</b>. Deployed '
        '<b>10+ autonomous agents</b> running 24/7 with rate-limited automation '
        'and <b>Docker</b>-based sandboxing.'
    ),
    "data_analyst": (
        '<b>Data Analyst</b> with experience building dashboards, automating ETL workflows, '
        'and delivering actionable insights. Built <b>Power BI</b> dashboards with <b>DAX</b> '
        'for real-time sales and supplier analysis. Automated <b>SQL</b> and <b>Python</b> '
        'data pipelines, cutting report prep time by <b>35%</b>. Specialises in '
        '<b>statistical testing</b>, <b>forecasting</b>, and <b>data-driven decision making</b>.'
    ),
    "data_scientist": (
        '<b>Data Scientist</b> with hands-on experience building production ML systems, '
        'statistical models, and data pipelines. Built a <b>88,500+ LOC</b> autonomous system '
        'with <b>2,350 tests</b> integrating ML-based classification, NLP pipelines, and '
        'experiential learning (GRPO). Specialises in <b>Python</b>, <b>SQL</b>, '
        '<b>machine learning</b>, and translating complex data into <b>actionable business insights</b>.'
    ),
    "ai_ml": (
        '<b>AI/ML Engineer</b> who built a <b>88,500+ LOC</b> production AI system with '
        '<b>2,350 tests</b>, custom encoder-decoders in <b>PyTorch</b>, and NLP pipelines. '
        'Designed multi-agent orchestration with <b>experiential learning</b> and '
        '<b>model evaluation</b>. Specialises in <b>deep learning</b>, <b>model deployment</b>, '
        'and <b>full-stack ML infrastructure</b>.'
    ),
    "data_engineer": (
        '<b>Data Engineer</b> with hands-on experience building data pipelines, ETL workflows, '
        'and database systems. Built a <b>88,500+ LOC</b> autonomous system with '
        '<b>21 SQLite databases</b>, automated data ingestion, and scheduled processing. '
        'Specialises in <b>Python</b>, <b>SQL</b>, <b>pipeline orchestration</b>, '
        'and <b>scalable data infrastructure</b>.'
    ),
}


def get_archetype_framing(
    archetype: str,
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
) -> dict[str, str]:
    """Return tagline + summary for an archetype, matching get_role_profile() interface.

    Falls back to generic profile if archetype is unknown.
    """
    profile = get_archetype_profile(archetype)
    summary = _ARCHETYPE_SUMMARIES.get(archetype, _ARCHETYPE_SUMMARIES.get("data_scientist", ""))
    return {
        "tagline": profile.get("tagline", _DEFAULT_PROFILE["tagline"]),
        "summary": summary,
        "project_priority": profile.get("project_priority", []),
        "skills_to_highlight": profile.get("skills_to_highlight", []),
    }
