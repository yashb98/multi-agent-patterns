"""cv_tailor.py — Dataclasses and validation for dynamic CV tailoring.

Validates LLM-generated CV sections before they reach the PDF renderer.
All personal data comes from the profile DB at runtime — never hardcoded here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.logging_config import get_logger
from shared.profile_store import ExperienceEntry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TailoredHeader:
    tagline: str
    summary: str


@dataclass
class TailoredCoverLetter:
    intro: str
    hook: str
    closing: str


@dataclass
class TailoredCV:
    tagline: str | None = None
    summary: str | None = None
    experience: list[ExperienceEntry] | None = None
    projects: list[dict] | None = None
    cover_letter: TailoredCoverLetter | None = None


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_SOFT_SKILL_WORDS = {
    "communication", "teamwork", "leadership", "problem solving", "time management",
    "adaptability", "collaboration", "analytical thinking", "critical thinking",
    "stakeholder management", "mentoring", "coaching", "prioritization",
    "attention to detail", "self motivated", "fast learner", "customer focus",
    "decision making", "interviewing", "okrs", "presentation skills",
    "project management", "strategic thinking", "negotiation",
}

# Regex is used here only for structural format validation of numeric/percentage
# patterns — not for semantic classification (allowed per codebase rules).
_METRIC_RE = re.compile(r"\d+[%$£]|\d{2,}")


def validate_summary(summary: str) -> str | None:
    """Returns error string or None if valid."""
    if len(summary) < 100 or len(summary) > 500:
        return f"Summary length {len(summary)} outside 100-500 range"
    summary_lower = summary.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in summary_lower:
            return f"Soft skill word found: '{word}'"
    if "<b>" not in summary:
        return "Summary must contain at least one <b> tag"
    return None


def validate_experience(original: list[ExperienceEntry], tailored: list[ExperienceEntry]) -> str | None:
    """Returns error string or None if valid."""
    if len(tailored) != len(original):
        return f"Entry count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, entry in enumerate(tailored):
        for j, bullet in enumerate(entry.bullets):
            if len(bullet) > 200:
                return f"Entry {i} bullet {j} exceeds 200 chars ({len(bullet)})"
            if not _METRIC_RE.search(bullet):
                return f"Entry {i} bullet {j} missing quantified metric"
    return None


def validate_projects(original: list[dict], tailored: list[dict]) -> str | None:
    """Returns error string or None if valid."""
    if len(tailored) != len(original):
        return f"Project count mismatch: expected {len(original)}, got {len(tailored)}"
    for i, (orig, tail) in enumerate(zip(original, tailored)):
        orig_numbers = set(re.findall(r"\d+", " ".join(orig.get("bullets", []))))
        tail_numbers = set(re.findall(r"\d+", " ".join(tail.get("bullets", []))))
        missing = orig_numbers - tail_numbers
        if missing:
            return f"Project {i} missing metrics: {missing}"
        bullet_count = len(tail.get("bullets", []))
        if bullet_count < 3 or bullet_count > 4:
            return f"Project {i} has {bullet_count} bullets (expected 3-4)"
    return None


def validate_cover_letter(cl: TailoredCoverLetter, company: str) -> str | None:
    """Returns error string or None if valid."""
    for section_name, text in [("intro", cl.intro), ("hook", cl.hook), ("closing", cl.closing)]:
        if len(text) < 50:
            return f"CL {section_name} too short ({len(text)} chars, min 50)"
        if len(text) > 300:
            return f"CL {section_name} too long ({len(text)} chars, max 300)"
    if company.lower() not in cl.intro.lower():
        return f"CL intro does not mention company name '{company}'"
    hook_lower = cl.hook.lower()
    for word in _SOFT_SKILL_WORDS:
        if word in hook_lower:
            return f"CL hook contains soft skill word: '{word}'"
    return None


# ---------------------------------------------------------------------------
# Telegram alert helper
# ---------------------------------------------------------------------------

def _send_validation_alert(section: str, company: str, reason: str, text: str) -> None:
    """Send Telegram alert for validation failure. Non-blocking."""
    try:
        from jobpulse.telegram_bots import send_jobs
        msg = f"CV Tailoring: {section} failed validation for {company} — {reason}. Generated text: {text[:200]}"
        send_jobs(msg)
    except Exception as exc:
        logger.debug("cv_tailor: Telegram alert failed: %s", exc)
