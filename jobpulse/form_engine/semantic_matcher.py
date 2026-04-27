"""Semantic option matching — 5-tier cascade for form field values.

Matches a desired value to available dropdown/radio/combobox options
without relying on exact string matching. Built from real application
data across Greenhouse, Workday, SmartRecruiters, LinkedIn, and iCIMS.
"""
from __future__ import annotations

import re

from shared.logging_config import get_logger

logger = get_logger(__name__)

CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    # Gender
    "male": ("man", "m", "he/him", "he/him/his", "masculine"),
    "female": ("woman", "f", "she/her", "she/her/hers", "feminine"),
    "man": ("male", "m", "he/him"),
    "woman": ("female", "f", "she/her"),
    # Boolean
    "yes": ("true", "authorized", "i am", "i do", "i have", "y",
            "yes, i am authorized", "yes i am", "yes, i do", "yes, i have"),
    "no": ("false", "not authorized", "i am not", "i do not", "n",
           "no, i am not", "no i do not"),
    # Ethnicity
    "indian": ("asian or asian british - indian", "south asian", "asian - indian",
               "asian or asian british: indian"),
    "asian": ("asian or asian british", "east asian", "southeast asian"),
    "white": ("white british", "white - british", "white english",
              "white - english/welsh/scottish/northern irish"),
    # Visa / work authorization
    "graduate visa": ("tier 4 graduate visa", "post-study work visa",
                      "graduate route", "graduate route visa"),
    # Notice period
    "1 month": ("4 weeks", "one month", "30 days", "less than 30 days",
                "less than 1 month", "1 month or less"),
    "2 weeks": ("14 days", "two weeks", "less than 2 weeks"),
    "immediately": ("available immediately", "0 days", "now", "none"),
    # Experience years
    "2 years": ("2+ years", "2-3 years", "over 2 years", "2 to 3 years"),
    "3 years": ("3+ years", "3-5 years", "over 3 years", "3 to 5 years"),
    "1 year": ("1+ years", "1-2 years", "over 1 year", "0-1 years"),
}

_RANGE_PAT = re.compile(r"[£$€]?\s*([\d,]+)\s*[-–—]\s*[£$€]?\s*([\d,]+)")

_CONSENT_WORDS = frozenset({"privacy", "consent", "terms", "agree", "acknowledge", "confirm", "gdpr", "data protection"})
_MARKETING_WORDS = frozenset({"marketing", "newsletter", "promotional", "offers", "opt in", "subscribe", "communications"})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def semantic_option_match(
    desired_value: str,
    available_options: list[str],
    *,
    field_label: str = "",
    aliases: dict[str, tuple[str, ...]] | None = None,
    numeric_value: float | None = None,
) -> str | None:
    """Match a desired value to available options via 5-tier cascade.

    Tiers:
    1. Exact match (case-insensitive, whitespace-normalized)
    2. Canonical alias lookup (CANONICAL_ALIASES + caller aliases)
    3. Numeric range match (salary, age, experience years)
    4. Token overlap (Jaccard similarity, threshold >= 2 shared tokens)
    5. Substring containment (for values >= 4 chars)

    Returns the exact option text to use, or None if no match.
    """
    if not available_options or not desired_value:
        return None

    desired_norm = _normalize(desired_value)
    opts_norm = {_normalize(o): o for o in available_options}

    # Tier 1: Exact match
    if desired_norm in opts_norm:
        return opts_norm[desired_norm]

    # Tier 2: Canonical aliases
    all_aliases = dict(CANONICAL_ALIASES)
    if aliases:
        all_aliases.update(aliases)

    for alias in all_aliases.get(desired_norm, ()):
        alias_norm = _normalize(alias)
        if alias_norm in opts_norm:
            return opts_norm[alias_norm]
        for opt_norm, opt_original in opts_norm.items():
            if alias_norm in opt_norm or opt_norm in alias_norm:
                return opt_original

    # Also check if desired_value is itself an alias of something
    for canonical, alias_tuple in all_aliases.items():
        if desired_norm in (_normalize(a) for a in alias_tuple):
            canonical_norm = _normalize(canonical)
            if canonical_norm in opts_norm:
                return opts_norm[canonical_norm]

    # Tier 3: Numeric range
    numeric = numeric_value
    if numeric is None:
        try:
            numeric = float(desired_value.replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
        except (ValueError, AttributeError):
            numeric = None

    if numeric is not None:
        for opt in available_options:
            m = _RANGE_PAT.search(opt)
            if m:
                low = float(m.group(1).replace(",", ""))
                high = float(m.group(2).replace(",", ""))
                if low <= numeric <= high:
                    return opt

    # Tier 4: Token overlap
    stop_words = {"and", "for", "the", "with", "from", "valid", "not", "or", "a", "an", "to", "of", "in", "i", "am", "is"}
    desired_tokens = {t for t in desired_norm.split() if len(t) > 1 and t not in stop_words}

    if desired_tokens:
        best_opt = None
        best_score = 0
        for opt_norm, opt_original in opts_norm.items():
            opt_tokens = {t for t in opt_norm.split() if len(t) > 1 and t not in stop_words}
            overlap = len(desired_tokens & opt_tokens)
            if overlap > best_score:
                best_score = overlap
                best_opt = opt_original
        if best_opt is not None and best_score >= 2:
            return best_opt

    # Tier 5: Substring containment (for values >= 4 chars)
    if len(desired_norm) >= 4:
        for opt_norm, opt_original in opts_norm.items():
            if desired_norm in opt_norm:
                return opt_original

    return None


def checkbox_intent(label: str, *, required: bool = False) -> bool | None:
    """Determine whether to check a checkbox based on its label.

    Returns True (check), False (don't check), or None (ambiguous).
    """
    label_lower = label.lower().strip()

    if any(w in label_lower for w in _CONSENT_WORDS):
        return True

    if any(w in label_lower for w in _MARKETING_WORDS):
        return False

    if required:
        return True

    return None
