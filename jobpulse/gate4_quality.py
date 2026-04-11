"""Gate 4 — Application Quality Check.

Phase A (pre-generation): JD quality, company background.
Phase B (post-generation): Deterministic CV scrutiny, LLM FAANG recruiter review.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass, field
from typing import Any

from shared.logging_config import get_logger
from jobpulse.utils.safe_io import safe_openai_call

logger = get_logger(__name__)

BOILERPLATE_PHRASES: list[str] = [
    "competitive salary",
    "dynamic team",
    "fast-paced environment",
    "great benefits",
    "exciting opportunity",
    "passionate individuals",
    "self-starter",
    "team player wanted",
    "immediate start",
    "no experience necessary",
]

GENERIC_WORDS: set[str] = {
    "tech", "digital", "it", "solutions", "services", "consulting",
    "group", "limited", "ltd", "uk", "global", "systems", "software",
    "data", "cloud", "cyber", "enterprise", "international",
}


@dataclass
class JDQualityResult:
    passed: bool
    reason: str
    boilerplate_count: int
    skill_count: int


@dataclass
class CompanyBackgroundResult:
    is_generic: bool
    previously_applied: bool
    note: str


def check_jd_quality(jd_text: str, extracted_skills: list[str]) -> JDQualityResult:
    """Check whether a job description is worth processing.

    Blocks JDs that are too short, too vague (few skills), or
    boilerplate-heavy with insufficient technical content.
    """
    skill_count = len(extracted_skills)
    jd_lower = jd_text.lower()

    # 1. Length check
    if len(jd_text) < 200:
        logger.info("Gate 4: JD too short (%d chars)", len(jd_text))
        return JDQualityResult(
            passed=False,
            reason="JD too short — fewer than 200 characters",
            boilerplate_count=0,
            skill_count=skill_count,
        )

    # 2. Skill count check
    if skill_count < 5:
        logger.info("Gate 4: JD too vague — only %d skills extracted", skill_count)
        return JDQualityResult(
            passed=False,
            reason=f"JD too vague — only {skill_count} skills extracted",
            boilerplate_count=0,
            skill_count=skill_count,
        )

    # 3. Boilerplate check (only blocks if skills are also low)
    boilerplate_count = sum(1 for phrase in BOILERPLATE_PHRASES if phrase in jd_lower)

    if boilerplate_count >= 3 and skill_count < 8:
        logger.info(
            "Gate 4: boilerplate JD — %d boilerplate phrases, only %d skills",
            boilerplate_count,
            skill_count,
        )
        return JDQualityResult(
            passed=False,
            reason=f"Boilerplate JD — {boilerplate_count} generic phrases with only {skill_count} skills",
            boilerplate_count=boilerplate_count,
            skill_count=skill_count,
        )

    logger.info("Gate 4: JD passed quality check (%d skills, %d boilerplate)", skill_count, boilerplate_count)
    return JDQualityResult(
        passed=True,
        reason="OK",
        boilerplate_count=boilerplate_count,
        skill_count=skill_count,
    )


def check_company_background(
    company: str,
    past_applications: list[dict[str, str]],
) -> CompanyBackgroundResult:
    """Check whether a company name is generic and if we already applied.

    Generic detection: company name has 1-3 words and ALL words are in the
    generic word set (after lowercasing and extracting word tokens).
    """
    # Extract word tokens from company name
    words = re.findall(r"[a-zA-Z]+", company.lower())

    is_generic = False
    if 1 <= len(words) <= 3 and all(w in GENERIC_WORDS for w in words):
        is_generic = True
        logger.info("Gate 4: generic company name — %s", company)

    # Check past applications (case-insensitive)
    previously_applied = False
    note = ""
    company_lower = company.lower()
    for app in past_applications:
        if app.get("company", "").lower() == company_lower:
            previously_applied = True
            date = app.get("date", "unknown")
            role = app.get("role", "unknown role")
            note = f"Previously applied on {date} for {role}"
            logger.info("Gate 4: previously applied to %s on %s", company, date)
            break

    if not note:
        note = "No previous application found" if not is_generic else f"Generic company name: {company}"

    return CompanyBackgroundResult(
        is_generic=is_generic,
        previously_applied=previously_applied,
        note=note,
    )


# ---------------------------------------------------------------------------
# Phase B: Post-generation CV scrutiny
# ---------------------------------------------------------------------------

_CONVERSATIONAL_PATTERNS: list[str] = [
    r"\bI worked\b", r"\bI helped\b", r"\bI was responsible\b",
    r"\bMy role was\b", r"\bI have\b", r"\bI am\b",
]

_INFORMAL_WORDS: list[str] = [
    r"\breally\b", r"\bvery\b", r"\bjust\b",
    r"\bstuff\b", r"\bthings\b", r"\bnice\b",
]

_METRIC_PATTERN = re.compile(
    r"\d+[%xX]|\d+\+|\$[\d,.]+|£[\d,.]+|\d+[kKmM]\b"
    r"|\d+ (?:users|requests|apps|tests|skills|projects|endpoints|agents|bots)"
)

_MAX_CV_CHARS = 4500  # heuristic for 2-page limit


@dataclass
class CVScrutinyResult:
    status: str = "clean"  # "clean" | "acceptable" | "needs_fix"
    has_error: bool = False
    missing_metrics_count: int = 0
    conversational_count: int = 0
    informal_count: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class LLMScrutinyResult:
    score: int = 0
    verdict: str = "reject"
    needs_review: bool = True
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    breakdown: dict[str, int] = field(default_factory=dict)


def scrutinize_cv_deterministic(cv_text: str) -> CVScrutinyResult:
    """B1: FAANG-level deterministic checks on CV text."""
    result = CVScrutinyResult()

    # Page limit heuristic
    if len(cv_text) > _MAX_CV_CHARS:
        result.has_error = True
        result.warnings.append(f"CV too long ({len(cv_text)} chars, max {_MAX_CV_CHARS})")

    # Metrics in bullet lines
    lines = cv_text.split("\n")
    bullet_lines = [
        l for l in lines
        if l.strip().startswith(("•", "-", "–", "·"))
        or (len(l.strip()) > 20 and not l.strip().isupper())
    ]
    lines_without_metrics = 0
    for line in bullet_lines:
        if not _METRIC_PATTERN.search(line):
            lines_without_metrics += 1
    result.missing_metrics_count = lines_without_metrics

    # Conversational text
    for pattern in _CONVERSATIONAL_PATTERNS:
        result.conversational_count += len(re.findall(pattern, cv_text, re.IGNORECASE))
    if result.conversational_count > 0:
        result.warnings.append(f"Conversational text: {result.conversational_count} instances")

    # Informal words
    for pattern in _INFORMAL_WORDS:
        result.informal_count += len(re.findall(pattern, cv_text, re.IGNORECASE))
    if result.informal_count > 0:
        result.warnings.append(f"Informal words: {result.informal_count} instances")

    # Status
    total_warnings = len(result.warnings) + (1 if result.missing_metrics_count > 2 else 0)
    if result.has_error:
        result.status = "needs_fix"
    elif total_warnings == 0:
        result.status = "clean"
    elif total_warnings <= 2:
        result.status = "acceptable"
    else:
        result.status = "needs_fix"

    return result


def scrutinize_cv_llm(
    cv_text: str,
    role: str,
    company: str,
    required_skills: list[str],
    preferred_skills: list[str],
) -> LLMScrutinyResult:
    """B2: GPT-5o-mini as a FAANG senior recruiter reviewing the CV."""
    prompt = (
        f"You are a senior IT recruiter at Google reviewing a CV for: {role} at {company}.\n\n"
        f"Required skills: {', '.join(required_skills[:15])}\n"
        f"Preferred skills: {', '.join(preferred_skills[:10])}\n\n"
        f"CV:\n{cv_text[:3000]}\n\n"
        f"Score 0-10:\n"
        f"1. Relevance (0-3): Does it address requirements?\n"
        f"2. Evidence (0-3): Claims backed by metrics/projects?\n"
        f"3. Presentation (0-2): Professional, clear, no fluff?\n"
        f"4. Standout (0-2): Would you want to interview?\n\n"
        f"Return ONLY valid JSON:\n"
        f'{{"total_score": 0-10, "relevance": 0-3, "evidence": 0-3, '
        f'"presentation": 0-2, "standout": 0-2, '
        f'"strengths": ["..."], "weaknesses": ["..."], '
        f'"verdict": "shortlist"|"maybe"|"reject"}}'
    )

    from shared.agents import get_openai_client, get_model_name
    client = get_openai_client()
    response = safe_openai_call(
        client,
        model=get_model_name(),
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        caller="gate4_llm_scrutiny",
    )

    if not response:
        logger.warning("Gate 4 LLM scrutiny returned None")
        return LLMScrutinyResult(needs_review=True)

    try:
        data = _json.loads(response)
    except _json.JSONDecodeError:
        logger.warning("Gate 4 LLM scrutiny invalid JSON: %s", response[:200])
        return LLMScrutinyResult(needs_review=True)

    score = int(data.get("total_score", 0))
    verdict = data.get("verdict", "reject")

    return LLMScrutinyResult(
        score=score,
        verdict=verdict,
        needs_review=score < 7,
        strengths=data.get("strengths", []),
        weaknesses=data.get("weaknesses", []),
        breakdown={
            "relevance": data.get("relevance", 0),
            "evidence": data.get("evidence", 0),
            "presentation": data.get("presentation", 0),
            "standout": data.get("standout", 0),
        },
    )
