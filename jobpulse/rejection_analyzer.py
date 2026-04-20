"""Rejection pattern analysis — classifies outcomes, blockers, and generates recommendations."""

from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

_POSITIVE_STATUSES = {"interview", "offer", "responded"}
_NEGATIVE_STATUSES = {"rejected", "discarded"}
_SELF_FILTERED_STATUSES = {"skipped", "blocked"}


def classify_outcome(status: str) -> str:
    """Classify an application status into a broad outcome category.

    Returns one of: "positive", "negative", "self_filtered", "pending".
    """
    s = status.lower().strip()
    if s in _POSITIVE_STATUSES:
        return "positive"
    if s in _NEGATIVE_STATUSES:
        return "negative"
    if s in _SELF_FILTERED_STATUSES:
        return "self_filtered"
    return "pending"


# ---------------------------------------------------------------------------
# Blocker classification
# ---------------------------------------------------------------------------

_BLOCKER_PATTERNS: list[tuple[str, str]] = [
    ("geo-restriction", r"us\.only|visa|residency|right.to.work|work.authoriz"),
    ("seniority-mismatch", r"staff.engineer|principal|director|vp.of|head.of"),
    ("onsite-requirement", r"on.?site|hybrid|relocat|in.office|in.person"),
    ("stack-mismatch", r"java\b|c\+\+|ruby|swift|kotlin|react.native|flutter|\.net|php\b|rust\b"),
]


def classify_blocker(reason: str) -> str:
    """Classify a rejection/block reason into a category.

    Returns one of: "geo-restriction", "seniority-mismatch",
    "onsite-requirement", "stack-mismatch", "other".
    """
    r = reason.lower()
    for category, pattern in _BLOCKER_PATTERNS:
        if re.search(pattern, r):
            return category
    return "other"


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def compute_funnel(applications: list[dict[str, Any]]) -> dict[str, int]:
    """Count applications by status."""
    counts: dict[str, int] = {}
    for app in applications:
        status = app.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def compute_score_by_outcome(
    applications: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Group ATS/match scores by outcome category.

    Returns {outcome: {avg, min, max, count}}.
    Only includes applications that have a numeric "score" field.
    """
    buckets: dict[str, list[float]] = {}
    for app in applications:
        score = app.get("score")
        if score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        outcome = classify_outcome(app.get("status", ""))
        buckets.setdefault(outcome, []).append(score)

    result: dict[str, dict[str, Any]] = {}
    for outcome, scores in buckets.items():
        result[outcome] = {
            "avg": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores),
            "count": len(scores),
        }
    return result


def compute_blocker_frequency(
    applications: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Count blocker categories from the "block_reason" field.

    Returns {category: {count, pct}}.
    Only applications with a non-empty "block_reason" are counted.
    """
    counts: dict[str, int] = {}
    total = 0
    for app in applications:
        reason = app.get("block_reason") or ""
        if not reason.strip():
            continue
        category = classify_blocker(reason)
        counts[category] = counts.get(category, 0) + 1
        total += 1

    if total == 0:
        return {}

    return {
        cat: {"count": cnt, "pct": cnt / total * 100}
        for cat, cnt in counts.items()
    }


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def generate_recommendations(
    applications: list[dict[str, Any]],
    max_recs: int = 5,
) -> list[dict[str, str]]:
    """Generate actionable recommendations based on blocker patterns and scores.

    Returns list of {action, impact}.
    """
    recs: list[dict[str, str]] = []

    blocker_freq = compute_blocker_frequency(applications)
    score_by_outcome = compute_score_by_outcome(applications)

    def _pct(category: str) -> float:
        return blocker_freq.get(category, {}).get("pct", 0.0)

    if _pct("geo-restriction") >= 20:
        recs.append({
            "action": "Tighten location filters",
            "impact": "Reduce wasted applications on roles with geo-restrictions",
        })

    if _pct("stack-mismatch") >= 15:
        recs.append({
            "action": "Filter out mismatched tech stacks",
            "impact": "Stop applying to roles requiring Java/C++/Ruby/etc.",
        })

    if _pct("seniority-mismatch") >= 10:
        recs.append({
            "action": "Exclude senior/staff/director roles",
            "impact": "Avoid seniority-based rejections",
        })

    if _pct("onsite-requirement") >= 15:
        recs.append({
            "action": "Filter for remote-only",
            "impact": "Eliminate onsite/hybrid requirement mismatches",
        })

    # Score-based recommendation
    positive_data = score_by_outcome.get("positive")
    negative_data = score_by_outcome.get("negative")
    if positive_data and negative_data:
        threshold = positive_data["avg"]
        if threshold > negative_data["avg"]:
            recs.append({
                "action": f"Raise minimum ATS score threshold to {threshold:.0f}",
                "impact": "Target roles where your profile scores above the positive-outcome average",
            })

    return recs[:max_recs]


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def generate_full_report(applications: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine all analysis into a single report dict."""
    funnel = compute_funnel(applications)
    score_by_outcome = compute_score_by_outcome(applications)
    blocker_frequency = compute_blocker_frequency(applications)
    recommendations = generate_recommendations(applications)

    total = len(applications)
    outcome_counts: dict[str, int] = {}
    for app in applications:
        outcome = classify_outcome(app.get("status", ""))
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    logger.info(
        "rejection_analyzer: report generated total=%d positive=%d negative=%d",
        total,
        outcome_counts.get("positive", 0),
        outcome_counts.get("negative", 0),
    )

    return {
        "total": total,
        "funnel": funnel,
        "outcome_counts": outcome_counts,
        "score_by_outcome": score_by_outcome,
        "blocker_frequency": blocker_frequency,
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# Avoidance rule generation
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "geo-restriction": ["visa", "right to work", "work authorization", "residency"],
    "seniority-mismatch": ["staff", "principal", "director", "vp", "head of"],
    "onsite-requirement": ["onsite", "on-site", "hybrid", "relocate", "in office"],
    "stack-mismatch": ["java", "c++", "ruby", ".net", "php", "rust", "swift", "kotlin"],
}

_CATEGORY_THRESHOLDS: dict[str, float] = {
    "geo-restriction": 20.0,
    "stack-mismatch": 15.0,
    "seniority-mismatch": 10.0,
    "onsite-requirement": 15.0,
}


def generate_avoidance_rules(
    applications: list[dict[str, Any]],
    *,
    rules_db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Auto-generate avoidance rules from blocker frequency.

    For each blocker category that exceeds its threshold, writes an
    exclude_keyword rule to AgentRulesDB.

    Returns:
        List of generated rule dicts.
    """
    blocker_freq = compute_blocker_frequency(applications)
    if not blocker_freq:
        return []

    total_blocked = sum(b["count"] for b in blocker_freq.values())
    if total_blocked == 0:
        return []

    try:
        from jobpulse.agent_rules import AgentRulesDB
    except ImportError:
        logger.warning("agent_rules module not available — skipping rule generation")
        return []

    rules_db = AgentRulesDB(db_path=rules_db_path)
    generated: list[dict[str, Any]] = []

    for category, data in blocker_freq.items():
        threshold = _CATEGORY_THRESHOLDS.get(category)
        if threshold is None or data["pct"] < threshold:
            continue

        keywords = _CATEGORY_KEYWORDS.get(category, [])
        for keyword in keywords:
            rule = rules_db.auto_generate_from_blocker(
                category=category,
                pattern=keyword,
                count=data["count"],
                total=total_blocked,
            )
            generated.append(rule)

    if generated:
        logger.info(
            "rejection_analyzer: generated %d avoidance rules from %d blocked apps",
            len(generated), total_blocked,
        )

    return generated
