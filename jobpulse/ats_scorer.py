"""Deterministic ATS scoring module.

No LLM calls — pure Python keyword matching + section detection + format checks.
Scores a CV against a list of JD skills and returns an ATSScore breakdown.
"""

from __future__ import annotations

import json
import re

from jobpulse.config import DATA_DIR
from jobpulse.models.application_models import ATSScore
from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Section detection patterns
# ---------------------------------------------------------------------------

_SECTION_PATTERNS: dict[str, list[str]] = {
    "education": [
        r"\beducation\b",
        r"\bdegree\b",
        r"\buniversity\b",
        r"\bcollege\b",
        r"\bbsc\b",
        r"\bmsc\b",
        r"\bphd\b",
        r"\bba\b",
        r"\bqualification",
    ],
    "experience": [
        r"\bexperience\b",
        r"\bwork history\b",
        r"\bemployment\b",
        r"\bcareer\b",
        r"\bprofessional background\b",
        r"\bjob history\b",
    ],
    "skills": [
        r"\bskills?\b",
        r"\btechnical skills?\b",
        r"\bcompetenc",
        r"\bproficienc",
        r"\bexpertise\b",
        r"\btechnologies\b",
        r"\btools?\b",
    ],
    "projects": [
        r"\bprojects?\b",
        r"\bportfolio\b",
        r"\bside projects?\b",
        r"\bopen.?source\b",
        r"\bpersonal projects?\b",
    ],
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_ats(jd_skills: list[str], cv_text: str) -> ATSScore:
    """Score a CV against JD skills. Returns ATSScore with breakdown.

    Scoring:
      - Keyword match: 0-70 (matched/total * 70). Uses synonym matching.
      - Section completeness: 0-20 (5 per required section: education, experience, skills, projects)
      - Format: 0-10 (parseable text, no binary content, has headings)
    """
    if not cv_text.strip():
        return ATSScore(
            total=0.0,
            keyword_score=0.0,
            section_score=0.0,
            format_score=0.0,
            missing_keywords=[],
            matched_keywords=[],
        )

    synonyms = _load_synonyms()
    cv_normalized = _normalize(cv_text)

    # Keyword scoring (0-70)
    matched: list[str] = []
    missing: list[str] = []

    for skill in jd_skills:
        skill_lower = skill.lower().strip()
        if _keyword_in_text(skill_lower, cv_normalized, synonyms):
            matched.append(skill_lower)
        else:
            missing.append(skill_lower)

    keyword_score = len(matched) / len(jd_skills) * 70.0 if jd_skills else 0.0

    # Section scoring (0-20, 5 pts each)
    detected = _detect_sections(cv_text)
    section_score = len(detected) * 5.0

    # Format scoring (0-10)
    format_score = _score_format(cv_text)

    total = keyword_score + section_score + format_score

    return ATSScore(
        total=round(total, 4),
        keyword_score=round(keyword_score, 4),
        section_score=round(section_score, 4),
        format_score=round(format_score, 4),
        missing_keywords=missing,
        matched_keywords=matched,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_synonyms() -> dict[str, list[str]]:
    """Load data/skill_synonyms.json.

    Returns a dict mapping canonical skill name → list of synonym forms.
    Falls back to an empty dict if the file is missing.
    """
    synonyms_path = DATA_DIR / "skill_synonyms.json"
    try:
        with synonyms_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning(
            "ats_scorer: synonym file missing or invalid at %s — "
            "keyword matching will not use synonyms. Error: %s",
            synonyms_path, exc,
        )
        return {}


def _normalize(text: str) -> str:
    """Lowercase, strip, replace hyphens and underscores with spaces."""
    return text.lower().strip().replace("-", " ").replace("_", " ")


def _keyword_in_text(keyword: str, cv_text: str, synonyms: dict[str, list[str]]) -> bool:
    """Check if keyword (or any synonym) appears in CV text.

    Strategy:
    1. Direct substring match first.
    2. Find the synonym group that contains this keyword (as canonical key or as a synonym value).
    3. Check whether any form from that group appears in the CV.
    """
    kw_norm = _normalize(keyword)
    cv_norm = _normalize(cv_text)

    # Direct match
    if _word_present(kw_norm, cv_norm):
        return True

    # Synonym group lookup — canonical key
    if kw_norm in synonyms:
        for form in synonyms[kw_norm]:
            if _word_present(_normalize(form), cv_norm):
                return True

    # Synonym group lookup — keyword may itself be a synonym value for some canonical key
    for canonical, variants in synonyms.items():
        normalized_variants = [_normalize(v) for v in variants]
        if kw_norm in normalized_variants or kw_norm == _normalize(canonical):
            # Check canonical key itself in CV
            if _word_present(_normalize(canonical), cv_norm):
                return True
            # Check all other variants in CV
            for form in normalized_variants:
                if _word_present(form, cv_norm):
                    return True

    return False


def _word_present(keyword: str, text: str) -> bool:
    """Check if keyword appears as a whole word in text."""
    if not keyword:
        return False
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def _detect_sections(cv_text: str) -> set[str]:
    """Detect which CV sections are present: education, experience, skills, projects.

    Uses regex to find section headings and relevant keywords.
    Returns a set of detected section names.
    """
    text_lower = cv_text.lower()
    detected: set[str] = set()

    for section, patterns in _SECTION_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                detected.add(section)
                break

    return detected


def _score_format(cv_text: str) -> float:
    """Assess basic CV format quality.

    Awards up to 10 points:
    - 4 pts: text is parseable (no null bytes / binary-looking content)
    - 3 pts: has at least one heading-like line (ALL CAPS or Title Case line <= 30 chars)
    - 3 pts: reasonable length (at least 100 characters)
    """
    score = 0.0

    # Parseable text: no null bytes or excessive non-printable characters
    null_count = cv_text.count("\x00")
    non_printable = sum(1 for ch in cv_text if not ch.isprintable() and ch not in "\n\r\t ")
    if null_count == 0 and non_printable < 5:
        score += 4.0

    # Has headings: a line that looks like a section header
    lines = [line.strip() for line in cv_text.splitlines() if line.strip()]
    has_heading = any(
        (line.isupper() or (line.istitle() and len(line) <= 30)) and len(line) >= 3
        for line in lines
    )
    if has_heading:
        score += 3.0

    # Reasonable length
    if len(cv_text.strip()) >= 100:
        score += 3.0

    return score
