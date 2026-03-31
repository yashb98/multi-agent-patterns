"""Gate 4 Phase A — JD quality and company background checks.

Pre-LLM filters that block low-quality or suspicious job postings
before they consume API budget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.logging_config import get_logger

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
