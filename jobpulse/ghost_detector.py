"""Ghost job detection — identifies likely-dead postings before wasting applications.

5 signal analyzers, weighted aggregation, 3 tiers.
Runs as Gate 0.5 via pipeline_hooks (between Gate 0 and Gates 1-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class GhostSignal:
    name: str
    score: float
    confidence: str
    reason: str


@dataclass
class GhostDetectionResult:
    tier: str
    signals: list[GhostSignal] = field(default_factory=list)
    recommendation: str = ""
    should_block: bool = False


_SIGNAL_WEIGHTS = {
    "freshness": 0.30,
    "jd_quality": 0.25,
    "repost": 0.20,
    "url_liveness": 0.15,
    "company": 0.10,
}


def _freshness_signal(listing, jd_text: str) -> GhostSignal:
    posted_at = getattr(listing, "posted_at", None)
    if not posted_at:
        return GhostSignal("freshness", 0.5, "low", "No posting date available")

    try:
        posted = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00")).replace(tzinfo=None)
        age_days = (datetime.utcnow() - posted).days
    except (ValueError, TypeError):
        return GhostSignal("freshness", 0.5, "low", "Could not parse posting date")

    if age_days <= 7:
        return GhostSignal("freshness", 1.0, "high", f"Posted {age_days} days ago")
    if age_days <= 21:
        return GhostSignal("freshness", 0.7, "medium", f"Posted {age_days} days ago")
    if age_days <= 45:
        return GhostSignal("freshness", 0.4, "medium", f"Posted {age_days} days ago — getting stale")
    return GhostSignal("freshness", 0.2, "high", f"Posted {age_days} days ago — likely expired")


def _jd_quality_signal(listing, jd_text: str) -> GhostSignal:
    if len(jd_text) < 100:
        return GhostSignal("jd_quality", 0.2, "high", "JD too short (<100 chars)")

    specificity_markers = [
        r"\d+\+?\s*years?", r"\$[\d,]+|£[\d,]+|€[\d,]+", r"\bsalary\b",
        r"\bpython\b", r"\bsql\b", r"\bdocker\b", r"\baws\b",
        r"\bresponsibilities\b", r"\brequirements\b", r"\bqualifications\b",
    ]
    hits = sum(1 for p in specificity_markers if re.search(p, jd_text, re.IGNORECASE))
    ratio = hits / len(specificity_markers)

    if ratio >= 0.4:
        return GhostSignal("jd_quality", 0.9, "high", f"Specific JD ({hits}/{len(specificity_markers)} markers)")
    if ratio >= 0.2:
        return GhostSignal("jd_quality", 0.6, "medium", f"Moderate JD specificity ({hits} markers)")
    return GhostSignal("jd_quality", 0.3, "medium", "Vague JD — few specificity markers")


def _repost_signal(listing, history: list[dict]) -> GhostSignal:
    if not history:
        return GhostSignal("repost", 0.5, "low", "No historical data")

    company = getattr(listing, "company", "").lower()
    title_words = set(getattr(listing, "title", "").lower().split())
    matches = 0
    for prev in history:
        prev_company = prev.get("company", "").lower()
        prev_title_words = set(prev.get("title", "").lower().split())
        if prev_company == company and len(title_words & prev_title_words) >= len(title_words) * 0.6:
            matches += 1

    if matches >= 2:
        return GhostSignal("repost", 0.2, "high", f"Reposted {matches} times in 90 days")
    if matches == 1:
        return GhostSignal("repost", 0.5, "medium", "Posted once before recently")
    return GhostSignal("repost", 0.8, "medium", "No repost history")


def _url_liveness_signal(listing, jd_text: str) -> GhostSignal:
    return GhostSignal("url_liveness", 0.5, "low", "Liveness check deferred")


def _company_signal(listing, jd_text: str) -> GhostSignal:
    return GhostSignal("company", 0.5, "low", "Company signal deferred")


def detect_ghost_job(listing, jd_text: str, history: list[dict] | None = None) -> GhostDetectionResult:
    """Run all signal analyzers and aggregate into a tier."""
    signals = [
        _freshness_signal(listing, jd_text),
        _jd_quality_signal(listing, jd_text),
        _repost_signal(listing, history or []),
        _url_liveness_signal(listing, jd_text),
        _company_signal(listing, jd_text),
    ]

    weighted_score = sum(
        s.score * _SIGNAL_WEIGHTS.get(s.name, 0.1) for s in signals
    )

    if weighted_score >= 0.6:
        tier = "high_confidence"
        should_block = False
        recommendation = "Legitimate posting — proceed"
    elif weighted_score >= 0.4:
        tier = "proceed_with_caution"
        should_block = False
        recommendation = "Mixed signals — review before applying"
    else:
        tier = "suspicious"
        should_block = True
        recommendation = "Likely ghost job — skip"

    return GhostDetectionResult(
        tier=tier,
        signals=signals,
        recommendation=recommendation,
        should_block=should_block,
    )
