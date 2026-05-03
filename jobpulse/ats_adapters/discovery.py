"""Platform auto-discovery from URL + DOM signals.

Maps URLs and DOM patterns to the correct ``BasePlatformStrategy``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from jobpulse.form_models import PageSnapshot

logger = get_logger(__name__)

# URL-based patterns (fast, primary)
_URL_PATTERNS: dict[str, list[str]] = {
    "greenhouse": ["greenhouse", "boards.greenhouse.io"],
    "lever": ["lever.co", "jobs.lever.co"],
    "workday": ["myworkdayjobs", "workdayjobs"],
    "smartrecruiters": ["smartrecruiters", "jobs.smartrecruiters"],
    "indeed": ["indeed.com", "indeed.co"],
    "ashby": ["ashbyhq.com"],
    "icims": ["icims.com"],
    "linkedin": ["linkedin.com/jobs"],
}

# DOM-based patterns (slower, used when URL is ambiguous).
# Catches white-label / clone instances hosted at customer domains.
_DOM_PATTERNS: dict[str, list[str]] = {
    "greenhouse": [
        "data-mosaic-component-name",
        "greenhouse",
        "boards-greenhouse",
        "powered by greenhouse",
        "greenhouse-app",
    ],
    "workday": [
        "data-automation-id",
        "workday",
        "myworkdayjobs",
        "wd-popup",
    ],
    "smartrecruiters": [
        "spl-",
        "smartrecruiters",
        "spl-application",
        "spl-form",
    ],
    "lever": [
        "lever.co",
        "jobs.lever",
        "powered by lever",
    ],
    "ashby": [
        "ashbyhq",
        "ashby-application",
        "ashby-jobs",
    ],
    "icims": [
        "icims_content",
        "icims-jobs",
        "icims",
    ],
    "linkedin": [
        "jobs-easy-apply",
        "easy-apply-button",
        "linkedin.com/jobs",
    ],
    "indeed": [
        "indeed-apply",
        "icl-AppliedFilter",
        "indeed.com",
    ],
    "reed": [
        "reed-apply",
        "reed.co.uk",
    ],
}


def detect_platform(url: str, snapshot: "PageSnapshot | dict | None" = None) -> str:
    """Detect ATS platform from URL + optional DOM snapshot.

    Args:
        url: The current page URL.
        snapshot: Optional page snapshot for DOM-based detection.

    Returns:
        Platform name (e.g. "greenhouse", "workday") or "generic".
    """
    url_lower = url.lower()

    # 1. Fast URL-based detection
    for platform, patterns in _URL_PATTERNS.items():
        for pattern in patterns:
            if pattern in url_lower:
                logger.debug("Platform detected from URL: %s", platform)
                return platform

    # 2. DOM-based detection (if snapshot provided)
    if snapshot is not None:
        snapshot_dict = snapshot.model_dump() if hasattr(snapshot, "model_dump") else dict(snapshot)

        page_text = (snapshot_dict.get("page_text_preview") or "").lower()
        html = (snapshot_dict.get("html_preview") or "").lower()
        buttons = snapshot_dict.get("buttons", [])

        for platform, patterns in _DOM_PATTERNS.items():
            for pattern in patterns:
                if pattern in page_text or pattern in html:
                    logger.debug("Platform detected from DOM text: %s", platform)
                    return platform

            # Check button texts for platform hints
            for btn in buttons:
                text = (btn.get("text") or "").lower()
                for pattern in patterns:
                    if pattern in text:
                        logger.debug("Platform detected from button text: %s", platform)
                        return platform

    logger.debug("No platform detected — falling back to generic")
    return "generic"


def detect_platform_from_url(url: str) -> str:
    """URL-only platform detection (fast, no DOM required)."""
    return detect_platform(url, snapshot=None)
