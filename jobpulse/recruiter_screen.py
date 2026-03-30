"""Gate 0: Title relevance filter — runs BEFORE any LLM or DB calls.

Public API:
  gate0_title_relevance(title, jd_text, config) -> bool
"""

from __future__ import annotations
import re
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _normalize_title(title: str) -> set[str]:
    """Extract meaningful words from a title for fuzzy matching."""
    noise = {"the", "a", "an", "at", "in", "for", "and", "or", "of", "to", "with"}
    words = re.findall(r"[a-zA-Z]+", title.lower())
    return {w for w in words if w not in noise and len(w) > 1}


def gate0_title_relevance(title: str, jd_text: str, config: dict) -> bool:
    """Return True if the job title is relevant based on search config.

    Checks:
    1. No exclude_keywords in title
    2. No exclude_keywords in JD body (catches "5+ years" etc.)
    3. At least one search title has >= 50% word overlap with job title

    Args:
        title: Job title string
        jd_text: Full JD text (can be empty)
        config: dict with "titles" (list[str]) and "exclude_keywords" (list[str])
    """
    title_lower = title.lower()
    jd_lower = jd_text.lower() if jd_text else ""

    # Check exclude keywords in title
    for kw in config.get("exclude_keywords", []):
        if kw.lower() in title_lower:
            logger.debug("gate0: title '%s' killed by exclude keyword '%s'", title, kw)
            return False

    # Check exclude keywords in JD body
    for kw in config.get("exclude_keywords", []):
        if kw.lower() in jd_lower:
            logger.debug("gate0: JD body killed by exclude keyword '%s'", kw)
            return False

    # Fuzzy title matching
    job_words = _normalize_title(title)
    if not job_words:
        return False

    for search_title in config.get("titles", []):
        search_words = _normalize_title(search_title)
        if not search_words:
            continue
        overlap = len(job_words & search_words)
        min_len = min(len(job_words), len(search_words))
        if min_len > 0 and overlap / min_len >= 0.5:
            return True

    return False
