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

# NOTE: tagline is built dynamically from ProfileStore at runtime
# (see _build_default_tagline below) so user's education + YOE + role
# come from the DB, never hardcoded.
_DEFAULT_PROFILE_TEMPLATE = {
    "tagline": "",  # filled at access time via _build_default_tagline()
    "summary_angle": "Building production software systems",
    "project_priority": [],
    "skills_to_highlight": ["Python"],
    "yoe_framing": "2+ years",
}


def _build_default_tagline() -> str:
    """Assemble a tagline from ProfileStore: degree + YOE + role + top-skill.

    Falls back to a generic skill-based tagline if ProfileStore is unavailable.
    Never embeds a specific institution or degree in source code.
    """
    try:
        from shared.profile_store import get_profile_store
        store = get_profile_store()
        edu = store.education()
        top = edu[0] if edu else None
        degree_part = ""
        if top:
            degree_part = top.degree or ""
            inst = (top.institution or "").strip()
            # Use first letters of institution as abbreviation
            if inst:
                abbr = "".join(w[0] for w in inst.split()[:3] if w[0].isupper()) or inst.split()[0]
                degree_part = f"{degree_part} ({abbr})"
        # YOE: take from sensitive_fields if set, else default
        yoe = (store.sensitive("years_of_experience") or "2+ ").strip()
        if yoe and not yoe.endswith("YOE"):
            yoe = f"{yoe} YOE"
        # Role + skill come from JD context, not profile — leave placeholder
        return f"{degree_part} | {yoe} | Software Engineer | Python".strip(" |")
    except Exception:
        return "Software Engineer | Python"


def _build_default_profile() -> dict:
    """Return a default archetype profile with tagline built from DB."""
    out = dict(_DEFAULT_PROFILE_TEMPLATE)
    out["tagline"] = _build_default_tagline()
    return out


# Lazy cache for the DB-derived default profile. Populated on first access via
# `_get_default_profile()`. Building it eagerly at module import opens
# user_profile.db (ProfileStore.__init__ → _connect → SQLite open + WAL pragma
# + schema migration) just to load the module, violating Principle 1
# (no module-level DB reads — same shape as S7 audit B-2 in skill_gap_tracker).
_DEFAULT_PROFILE_CACHE: dict | None = None


def _get_default_profile() -> dict:
    """Lazy accessor for the DB-derived default profile.

    Returns a cached dict on subsequent calls so the cost is paid once per
    process. Tests that need a fresh DB read can reset the cache by setting
    ``_DEFAULT_PROFILE_CACHE = None``.
    """
    global _DEFAULT_PROFILE_CACHE
    if _DEFAULT_PROFILE_CACHE is None:
        _DEFAULT_PROFILE_CACHE = _build_default_profile()
    return _DEFAULT_PROFILE_CACHE


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
    result = dict(_get_default_profile())
    result.update({k: v for k, v in profile.items() if k != "keywords"})
    return result


_archetype_summaries_cache: dict[str, str] | None = None


def _build_archetype_summaries() -> dict[str, str]:
    global _archetype_summaries_cache
    if _archetype_summaries_cache is not None:
        return _archetype_summaries_cache
    from jobpulse.cv_templates import get_project_stats
    s = get_project_stats()
    loc = s.get("loc_display", "142,500+")
    tests = s.get("tests_display", "3,350+")
    dbs = s.get("databases", 57)
    _archetype_summaries_cache = {
        "agentic": (
            f'<b>AI Engineer</b> who built a <b>{loc} LOC</b> production multi-agent system with '
            f'<b>4 LangGraph orchestration patterns</b>, <b>GRPO experiential learning</b>, and '
            f'<b>{tests} tests</b>. Shipped <b>10+ autonomous agents</b> with human-in-the-loop flows, '
            f'fact-checking, and Swarm-based routing. Specialises in <b>agentic architectures</b>, '
            f'<b>tool-use</b>, and <b>production agent deployment</b>.'
        ),
        "data_platform": (
            f'<b>ML Engineer</b> who built a <b>{loc} LOC</b> production AI system with '
            f'<b>{tests} tests</b> and <b>MLOps</b> pipelines. Designed multi-agent orchestration with '
            f'<b>experiential learning</b> and <b>model evaluation</b>. Deployed '
            f'<b>10+ autonomous agents</b> running 24/7 with rate-limited automation '
            f'and <b>Docker</b>-based sandboxing.'
        ),
        "data_analyst": (
            '<b>Data Analyst</b> with experience building dashboards, automating ETL workflows, '
            'and delivering actionable insights. Built <b>Power BI</b> dashboards with <b>DAX</b> '
            'for real-time sales and supplier analysis. Automated <b>SQL</b> and <b>Python</b> '
            'data pipelines, cutting report prep time by <b>35%</b>. Specialises in '
            '<b>statistical testing</b>, <b>forecasting</b>, and <b>data-driven decision making</b>.'
        ),
        "data_scientist": (
            f'<b>Data Scientist</b> with hands-on experience building production ML systems, '
            f'statistical models, and data pipelines. Built a <b>{loc} LOC</b> autonomous system '
            f'with <b>{tests} tests</b> integrating ML-based classification, NLP pipelines, and '
            f'experiential learning (GRPO). Specialises in <b>Python</b>, <b>SQL</b>, '
            f'<b>machine learning</b>, and translating complex data into <b>actionable business insights</b>.'
        ),
        "ai_ml": (
            f'<b>AI/ML Engineer</b> who built a <b>{loc} LOC</b> production AI system with '
            f'<b>{tests} tests</b>, custom encoder-decoders in <b>PyTorch</b>, and NLP pipelines. '
            f'Designed multi-agent orchestration with <b>experiential learning</b> and '
            f'<b>model evaluation</b>. Specialises in <b>deep learning</b>, <b>model deployment</b>, '
            f'and <b>full-stack ML infrastructure</b>.'
        ),
        "data_engineer": (
            f'<b>Data Engineer</b> with hands-on experience building data pipelines, ETL workflows, '
            f'and database systems. Built a <b>{loc} LOC</b> autonomous system with '
            f'<b>{dbs} SQLite databases</b>, automated data ingestion, and scheduled processing. '
            f'Specialises in <b>Python</b>, <b>SQL</b>, <b>pipeline orchestration</b>, '
            f'and <b>scalable data infrastructure</b>.'
        ),
    }
    return _archetype_summaries_cache


def get_archetype_framing(
    archetype: str,
    required_skills: list[str] | None = None,
    preferred_skills: list[str] | None = None,
) -> dict[str, str]:
    """Return tagline + summary for an archetype, matching get_role_profile() interface.

    Falls back to generic profile if archetype is unknown.
    """
    profile = get_archetype_profile(archetype)
    summaries = _build_archetype_summaries()
    summary = summaries.get(archetype, summaries.get("data_scientist", ""))
    return {
        "tagline": profile.get("tagline", _get_default_profile()["tagline"]),
        "summary": summary,
        "project_priority": profile.get("project_priority", []),
        "skills_to_highlight": profile.get("skills_to_highlight", []),
    }
