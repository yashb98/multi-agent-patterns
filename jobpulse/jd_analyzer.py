"""JD Analyzer — parses raw job description text into structured data.

Two-tier extraction:
  1. Rule-based (no LLM): salary, location, seniority, ATS platform, easy apply, job ID.
  2. LLM-based (gpt-5o-mini): required skills, preferred skills, industry, sub_context.

Public API
----------
generate_job_id(url)          -> str
extract_salary(text)          -> tuple[float | None, float | None]
extract_location(text)        -> str
detect_remote(text)           -> bool
detect_seniority(text)        -> str | None
detect_ats_platform(url)      -> str | None
detect_easy_apply(url, text)  -> bool
extract_skills_llm(jd_text)   -> dict
analyze_jd(...)               -> JobListing
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from shared.logging_config import get_logger

from jobpulse.config import OPENAI_API_KEY
from jobpulse.models.application_models import JobListing

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# ATS platform URL patterns
# ---------------------------------------------------------------------------
_ATS_PATTERNS: list[tuple[str, str]] = [
    (r"greenhouse\.io", "greenhouse"),
    (r"lever\.co", "lever"),
    (r"myworkdayjobs\.com", "workday"),
    (r"smartrecruiters\.com", "smartrecruiters"),
    (r"icims\.com", "icims"),
    (r"taleo\.net", "taleo"),
    (r"jobvite\.com", "jobvite"),
    (r"recruitee\.com", "recruitee"),
    (r"ashbyhq\.com", "ashby"),
    (r"bamboohr\.com", "bamboohr"),
]

# ---------------------------------------------------------------------------
# UK city names used for location extraction fallback
# ---------------------------------------------------------------------------
_UK_CITIES: list[str] = [
    "London",
    "Manchester",
    "Birmingham",
    "Leeds",
    "Sheffield",
    "Liverpool",
    "Bristol",
    "Edinburgh",
    "Glasgow",
    "Cardiff",
    "Newcastle",
    "Nottingham",
    "Southampton",
    "Portsmouth",
    "Brighton",
    "Oxford",
    "Cambridge",
    "Leicester",
    "Coventry",
    "Bradford",
    "Reading",
    "Belfast",
    "York",
    "Exeter",
]

# ---------------------------------------------------------------------------
# Salary extraction
# ---------------------------------------------------------------------------

# Matches patterns like:
#   £30,000 - £35,000   30K-35K   £28k per annum   $50,000-$60,000
_SALARY_RE = re.compile(
    r"(?:£|\$|USD|GBP)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[kK]?"
    r"\s*(?:-|to|\u2013)\s*"  # \u2013 = EN dash, common in UK job listings
    r"(?:£|\$|USD|GBP)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[kK]?",
    re.IGNORECASE,
)

# Single salary: £50,000 or 50K
_SINGLE_SALARY_RE = re.compile(
    r"(?:£|\$|USD|GBP)\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*[kK]?",
    re.IGNORECASE,
)


def _parse_amount(raw: str, suffix_char: str) -> float:
    """Convert a raw numeric string (possibly with commas) + K suffix to float."""
    cleaned = raw.replace(",", "")
    value = float(cleaned)
    if suffix_char.upper() == "K":
        value *= 1000
    return value


def generate_job_id(url: str) -> str:
    """Return a 64-char SHA-256 hex digest of the URL.

    Identical URLs always produce the same ID; different URLs produce different IDs.
    """
    return hashlib.sha256(url.encode()).hexdigest()


def extract_salary(text: str) -> tuple[float | None, float | None]:
    """Extract a salary range from free text.

    Handles:
      £30,000 - £35,000
      30K-35K
      £28k - £32k per annum
      $50,000-$60,000
      "Competitive salary" -> (None, None)

    Returns:
        (salary_min, salary_max) as floats, or (None, None) if no range found.
    """
    # Search for a salary range pattern
    match = _SALARY_RE.search(text)
    if match:
        raw_lo, raw_hi = match.group(1), match.group(2)
        # Detect K suffix by looking at the character immediately after each number
        full = match.group(0)
        # Re-extract with explicit K awareness
        lo_end = match.start(1) + len(raw_lo)
        hi_end = match.start(2) + len(raw_hi)
        lo_k = full[lo_end - match.start(0) : lo_end - match.start(0) + 1]
        hi_k = full[hi_end - match.start(0) : hi_end - match.start(0) + 1]

        lo = _parse_amount(raw_lo, lo_k)
        hi = _parse_amount(raw_hi, hi_k)
        return (lo, hi)

    return (None, None)


# ---------------------------------------------------------------------------
# Location extraction
# ---------------------------------------------------------------------------

_LOCATION_PREFIX_RE = re.compile(
    r"Location\s*:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# "Remote, UK" or "Remote" at line start / after whitespace
_REMOTE_LOCATION_RE = re.compile(r"\bRemote(?:,\s*\w+)?\b", re.IGNORECASE)


def extract_location(text: str) -> str:
    """Extract the primary work location from a job description.

    Strategy:
      1. Look for "Location: <value>" label.
      2. Look for "Remote, <country>" pattern.
      3. Scan for known UK city names.
      4. Default to "United Kingdom".
    """
    # 1. Explicit "Location:" prefix
    m = _LOCATION_PREFIX_RE.search(text)
    if m:
        loc = m.group(1).strip()
        # Strip trailing parenthetical like "(Hybrid)" from the label value
        # but keep "(Hybrid)" if it's part of the full value — per spec test
        # "Location: London, UK (Hybrid)" → "London, UK"
        # Strip trailing parenthetical only
        loc = re.sub(r"\s*\([^)]*\)\s*$", "", loc).strip()
        return loc

    # 2. "Remote, UK" or similar at start of text / as a standalone phrase
    m = _REMOTE_LOCATION_RE.search(text)
    if m:
        # Return the matched remote phrase as-is (e.g. "Remote, UK")
        return m.group(0)

    # 3. Known UK cities
    for city in _UK_CITIES:
        if re.search(rf"\b{city}\b", text, re.IGNORECASE):
            return city

    return "United Kingdom"


# ---------------------------------------------------------------------------
# Remote detection
# ---------------------------------------------------------------------------

_REMOTE_KEYWORDS = re.compile(
    r"\b(remote|hybrid|work\s*from\s*home|wfh|flexible\s*(?:location|working))\b",
    re.IGNORECASE,
)


def detect_remote(text: str) -> bool:
    """Return True if the text mentions remote, hybrid, WFH, or flexible working."""
    return bool(_REMOTE_KEYWORDS.search(text))


# ---------------------------------------------------------------------------
# Seniority detection
# ---------------------------------------------------------------------------

_SENIORITY_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bintern\b", re.IGNORECASE), "intern"),
    (re.compile(r"\bgraduate\b", re.IGNORECASE), "graduate"),
    (re.compile(r"\bjunior\b", re.IGNORECASE), "junior"),
    (re.compile(r"\bmid[\s-]?level\b|\bmid\b", re.IGNORECASE), "mid"),
]


def detect_seniority(text: str) -> str | None:
    """Return 'intern', 'graduate', 'junior', 'mid', or None.

    Checks in priority order: intern > graduate > junior > mid.
    Returns None if no keyword is found.
    """
    for pattern, level in _SENIORITY_MAP:
        if pattern.search(text):
            return level
    return None


# ---------------------------------------------------------------------------
# ATS platform detection
# ---------------------------------------------------------------------------


def detect_ats_platform(url: str) -> str | None:
    """Detect the ATS platform from the application URL.

    Returns a lowercase platform name (e.g. 'greenhouse', 'lever', 'workday')
    or None if not recognised.
    """
    for pattern, name in _ATS_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return name
    return None


# ---------------------------------------------------------------------------
# Easy apply detection
# ---------------------------------------------------------------------------

_EASY_APPLY_PLATFORMS = re.compile(r"linkedin\.com", re.IGNORECASE)
_QUICK_APPLY_PLATFORMS = re.compile(r"indeed\.co(?:m|\.uk)", re.IGNORECASE)

_EASY_APPLY_TEXT = re.compile(r"\beasy\s*apply\b", re.IGNORECASE)
_QUICK_APPLY_TEXT = re.compile(r"\bquick\s*apply\b", re.IGNORECASE)


def detect_easy_apply(url: str, text: str) -> bool:
    """Return True only when the platform supports easy apply AND the text confirms it.

    LinkedIn + "Easy Apply" text  → True
    Indeed   + "Quick Apply" text → True
    Any other combination          → False
    """
    linkedin_match = _EASY_APPLY_PLATFORMS.search(url) and _EASY_APPLY_TEXT.search(text)
    indeed_match = _QUICK_APPLY_PLATFORMS.search(url) and _QUICK_APPLY_TEXT.search(text)
    return bool(linkedin_match or indeed_match)


# ---------------------------------------------------------------------------
# LLM-based skill extraction
# ---------------------------------------------------------------------------


def extract_skills_llm(jd_text: str) -> dict:  # type: ignore[return]
    """Use gpt-5o-mini to extract structured skill data from a JD.

    Returns:
        {
            "required_skills": list[str],
            "preferred_skills": list[str],
            "industry": str,
            "sub_context": str,
            "error": None | str,  # None on success, error description or "empty_jd" otherwise
        }

    Falls back to empty lists + empty strings on any error.
    """
    if not jd_text or not jd_text.strip():
        logger.warning("extract_skills_llm: received empty JD text, skipping LLM call")
        return {"required_skills": [], "preferred_skills": [], "industry": "", "sub_context": "", "error": "empty_jd"}

    try:
        import openai  # local import to keep module importable without openai installed

        client = openai.OpenAI(api_key=OPENAI_API_KEY)

        system = (
            "You are a job description parser. Extract skill and context data as JSON. "
            "Return ONLY valid JSON with these keys: "
            "required_skills (list of strings), preferred_skills (list of strings), "
            "industry (string), sub_context (string — 1 sentence describing the domain)."
        )

        user = f"Parse this job description:\n\n{jd_text[:4000]}"

        response = client.chat.completions.create(
            model="gpt-5o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "required_skills": data.get("required_skills", []),
            "preferred_skills": data.get("preferred_skills", []),
            "industry": data.get("industry", ""),
            "sub_context": data.get("sub_context", ""),
            "error": None,
        }

    except Exception as exc:
        logger.warning("extract_skills_llm failed: %s", exc)
        return {
            "required_skills": [],
            "preferred_skills": [],
            "industry": "",
            "sub_context": "",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Recruiter email extraction
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

_DISCARD_PREFIXES = frozenset([
    "noreply", "no-reply", "donotreply", "info", "admin",
    "support", "hello", "contact", "enquiries", "feedback",
])

_GENERIC_HR_PREFIXES = frozenset([
    "jobs", "careers", "hr", "recruitment", "hiring",
    "talent", "apply", "applications",
])


def extract_recruiter_email(jd_text: str) -> str | None:
    """Extract the most useful recruiter/HR email from a job description.

    Priority: personal recruiter email > generic HR email > None.
    Discards noreply/info/support addresses entirely.
    """
    emails = _EMAIL_RE.findall(jd_text)

    recruiter_emails: list[str] = []
    generic_hr_emails: list[str] = []

    for email in emails:
        local_part = email.split("@")[0].lower()
        if any(local_part.startswith(prefix) for prefix in _DISCARD_PREFIXES):
            continue
        if any(local_part.startswith(prefix) for prefix in _GENERIC_HR_PREFIXES):
            generic_hr_emails.append(email)
        else:
            recruiter_emails.append(email)

    if recruiter_emails:
        return recruiter_emails[0]
    if generic_hr_emails:
        return generic_hr_emails[0]
    return None


# ---------------------------------------------------------------------------
# Full analysis orchestrator
# ---------------------------------------------------------------------------


def analyze_jd(
    url: str,
    title: str,
    company: str,
    platform: str,
    jd_text: str,
    apply_url: str = "",
) -> JobListing:
    """Combine rule-based and LLM extraction into a JobListing model.

    Args:
        url:       Canonical URL of the job listing (used for job_id + easy_apply).
        title:     Job title as shown on the listing.
        company:   Company name.
        platform:  Job board ('linkedin', 'indeed', 'reed', 'totaljobs', 'glassdoor').
        jd_text:   Raw job description text.
        apply_url: URL of the application page (used for ATS detection). Defaults to url.

    Returns:
        Fully populated JobListing instance.
    """
    if not apply_url:
        apply_url = url

    job_id = generate_job_id(url)
    salary_min, salary_max = extract_salary(jd_text)
    location = extract_location(jd_text)
    remote = detect_remote(jd_text)
    seniority = detect_seniority(f"{title} {jd_text}")
    ats_platform = detect_ats_platform(apply_url)
    easy_apply = detect_easy_apply(url, jd_text)

    recruiter_email = extract_recruiter_email(jd_text)

    from jobpulse.skill_extractor import extract_skills_hybrid
    llm_data = extract_skills_hybrid(jd_text)

    logger.info(
        "analyze_jd completed job_id=%s title=%r seniority=%s ats=%s",
        job_id[:8],
        title,
        seniority,
        ats_platform,
    )

    return JobListing(
        job_id=job_id,
        title=title,
        company=company,
        platform=platform,  # type: ignore[arg-type]
        url=url,
        salary_min=salary_min,
        salary_max=salary_max,
        location=location,
        remote=remote,
        seniority=seniority,
        required_skills=llm_data["required_skills"],
        preferred_skills=llm_data["preferred_skills"],
        description_raw=jd_text,
        ats_platform=ats_platform,
        found_at=datetime.now(UTC),
        easy_apply=easy_apply,
        recruiter_email=recruiter_email,
    )
