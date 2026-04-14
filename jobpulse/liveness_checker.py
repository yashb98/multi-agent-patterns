"""Liveness classifier — detects ghost/expired job postings.

Pure-function classifier: no browser dependency.
Takes HTTP response data, returns active/expired/uncertain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse, parse_qs

from shared.logging_config import get_logger

logger = get_logger(__name__)

# --- Patterns ---

_EXPIRED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"job (is )?no longer available",
        r"job.*no longer open",
        r"position has been filled",
        r"this job has expired",
        r"job posting has expired",
        r"no longer accepting applications",
        r"this (position|role|job) (is )?no longer",
        r"this job (listing )?is closed",
        r"job (listing )?not found",
        r"the page you are looking for doesn.t exist",
        r"diese stelle (ist )?(nicht mehr|bereits) besetzt",
        r"offre (expir[eé]e?|n[''']est plus disponible)",
    ]
]

_APPLY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bapply\b",
        r"\bsolicitar\b",
        r"\bbewerben\b",
        r"\bpostuler\b",
        r"submit application",
        r"easy apply",
        r"start application",
        r"ich bewerbe mich",
    ]
]

_LISTING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\d+ jobs? found",
        r"search for jobs page is loaded",
    ]
]


# --- Result type ---

@dataclass
class LivenessResult:
    status: str   # "active" | "expired" | "uncertain"
    reason: str


# --- Classifier ---

def classify_liveness(
    *,
    status_code: int,
    url: str,
    body: str,
    apply_control_text: str = "",
) -> LivenessResult:
    """Classify a job posting as active, expired, or uncertain.

    Args:
        status_code: HTTP response status code.
        url: Final URL after redirects.
        body: Full response body text (HTML or plain text).
        apply_control_text: Text content of apply button/control, if available.
                            Defaults to empty string when not present.

    Returns:
        LivenessResult with status and reason.
    """
    # 1. HTTP 404/410 → expired
    if status_code in (404, 410):
        logger.debug("liveness=expired reason=http_%d url=%s", status_code, url)
        return LivenessResult(status="expired", reason=f"HTTP {status_code}")

    # 2. ?error=true in URL → expired (Greenhouse redirect)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("error", [""])[0].lower() == "true":
        logger.debug("liveness=expired reason=greenhouse_error_redirect url=%s", url)
        return LivenessResult(status="expired", reason="Greenhouse error redirect (?error=true)")

    # 3. Body matches expired pattern → expired
    for pattern in _EXPIRED_PATTERNS:
        match = pattern.search(body)
        if match:
            logger.debug("liveness=expired reason=expired_pattern pattern=%s url=%s", pattern.pattern, url)
            return LivenessResult(status="expired", reason=f"Expired pattern matched: '{match.group(0)}'")

    # 4. Body matches listing page pattern → expired (check BEFORE apply controls)
    for pattern in _LISTING_PATTERNS:
        match = pattern.search(body)
        if match:
            logger.debug("liveness=expired reason=listing_page pattern=%s url=%s", pattern.pattern, url)
            return LivenessResult(status="expired", reason=f"Listing page detected: '{match.group(0)}'")

    # 5. Body < 300 chars → expired
    if len(body) < 300:
        logger.debug("liveness=expired reason=short_body len=%d url=%s", len(body), url)
        return LivenessResult(status="expired", reason=f"Body too short ({len(body)} chars)")

    # 6. Apply control matches apply pattern → active
    for pattern in _APPLY_PATTERNS:
        if pattern.search(apply_control_text):
            logger.debug("liveness=active reason=apply_control url=%s", url)
            return LivenessResult(status="active", reason=f"Apply control found: '{pattern.pattern}'")

    # 7. Default → uncertain
    logger.debug("liveness=uncertain url=%s", url)
    return LivenessResult(status="uncertain", reason="No apply control found and no expiry signals")
