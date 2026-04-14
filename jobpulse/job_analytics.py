"""Job application analytics — funnel, platform breakdown, gate stats.

Provides Telegram-formatted stats and raw dicts for the API layer.
All functions accept an optional ``db_path`` so tests can point at tmp_path DBs.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)
_DB_PATH = str(DATA_DIR / "jobpulse.db")


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(db_path or _DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Conversion funnel
# ---------------------------------------------------------------------------

def get_conversion_funnel(days: int = 7, db_path: str | None = None) -> dict[str, Any]:
    """Return status counts and conversion rates for the last *days* days."""
    con = _connect(db_path)
    cutoff = _cutoff_iso(days)
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS cnt FROM applications "
            "WHERE created_at >= ? GROUP BY status",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()

    counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}

    found = counts.get("Found", 0)
    applied = counts.get("Applied", 0)
    interview = counts.get("Interview", 0)
    offer = counts.get("Offer", 0)
    rejected = counts.get("Rejected", 0)
    skipped = counts.get("Skipped", 0)
    blocked = counts.get("Blocked", 0)

    found_to_applied = (applied / found * 100) if found else 0.0
    applied_to_interview = (interview / applied * 100) if applied else 0.0

    return {
        "found": found,
        "applied": applied,
        "interview": interview,
        "offer": offer,
        "rejected": rejected,
        "skipped": skipped,
        "blocked": blocked,
        "found_to_applied": found_to_applied,
        "applied_to_interview": applied_to_interview,
    }


# ---------------------------------------------------------------------------
# Platform breakdown
# ---------------------------------------------------------------------------

def get_platform_breakdown(days: int = 7, db_path: str | None = None) -> dict[str, dict[str, int]]:
    """Return per-platform status counts (requires JOIN with job_listings)."""
    con = _connect(db_path)
    cutoff = _cutoff_iso(days)
    try:
        rows = con.execute(
            "SELECT jl.platform, a.status, COUNT(*) AS cnt "
            "FROM applications a "
            "JOIN job_listings jl ON a.job_id = jl.job_id "
            "WHERE a.created_at >= ? "
            "GROUP BY jl.platform, a.status",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()

    breakdown: dict[str, dict[str, int]] = {}
    for r in rows:
        platform = r["platform"]
        status = r["status"].lower()
        breakdown.setdefault(platform, {"found": 0, "applied": 0, "interview": 0})
        breakdown[platform][status] = r["cnt"]
    return breakdown


# ---------------------------------------------------------------------------
# Gate stats
# ---------------------------------------------------------------------------

def get_gate_stats(days: int = 7, db_path: str | None = None) -> dict[str, int]:
    """Return counts of Blocked and Skipped applications."""
    con = _connect(db_path)
    cutoff = _cutoff_iso(days)
    try:
        rows = con.execute(
            "SELECT status, COUNT(*) AS cnt FROM applications "
            "WHERE status IN ('Blocked', 'Skipped') AND created_at >= ? "
            "GROUP BY status",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()

    counts: dict[str, int] = {r["status"]: r["cnt"] for r in rows}
    blocked = counts.get("Blocked", 0)
    skipped = counts.get("Skipped", 0)
    return {"blocked": blocked, "skipped": skipped, "total_screened": blocked + skipped}


# ---------------------------------------------------------------------------
# Formatted Telegram output
# ---------------------------------------------------------------------------

def get_enhanced_job_stats(db_path: str | None = None) -> str:
    """Return a Telegram-friendly summary combining all analytics."""
    funnel = get_conversion_funnel(days=7, db_path=db_path)
    platforms = get_platform_breakdown(days=7, db_path=db_path)
    gates = get_gate_stats(days=7, db_path=db_path)

    lines = [
        "\U0001f4ca Job Application Analytics",
        "",
        "\U0001f4c8 This Week's Funnel:",
        (
            f"Found: {funnel['found']} \u2192 Applied: {funnel['applied']} "
            f"({funnel['found_to_applied']:.0f}%) \u2192 Interview: {funnel['interview']} "
            f"({funnel['applied_to_interview']:.0f}%)"
        ),
        (
            f"Rejected: {funnel['rejected']} | Skipped: {funnel['skipped']} "
            f"| Blocked: {funnel['blocked']}"
        ),
    ]

    if platforms:
        lines.append("")
        lines.append("\U0001f310 By Platform:")
        for plat, counts in sorted(platforms.items()):
            lines.append(
                f"{plat.capitalize()}: {counts.get('found', 0)} found, "
                f"{counts.get('applied', 0)} applied"
            )

    lines.append("")
    lines.append("\U0001f6e1\ufe0f Gate Stats:")
    lines.append(f"Blocked: {gates['blocked']} | Skipped: {gates['skipped']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rejection pattern analysis
# ---------------------------------------------------------------------------

def get_rejection_patterns(days: int = 30, db_path: str | None = None) -> str:
    """Query recent applications and generate a rejection pattern report for Telegram."""
    con = _connect(db_path)
    cutoff = _cutoff_iso(days)
    try:
        rows = con.execute(
            "SELECT job_id, status, match_tier, ats_score, notes, created_at "
            "FROM applications WHERE created_at >= ?",
            (cutoff,),
        ).fetchall()
    finally:
        con.close()

    if not rows:
        return "No applications in the last {days} days to analyze."

    applications = [dict(r) for r in rows]

    try:
        from jobpulse.rejection_analyzer import generate_full_report
        report = generate_full_report(applications)
    except Exception as exc:
        logger.error("get_rejection_patterns: generate_full_report failed: %s", exc)
        return f"Failed to generate rejection analysis: {exc}"

    # Format for Telegram
    lines = ["\U0001f50d Rejection Pattern Analysis\n"]

    funnel = report.get("funnel", {})
    if funnel:
        lines.append("\U0001f4ca Funnel:")
        for stage, count in funnel.items():
            lines.append(f"  {stage}: {count}")
        lines.append("")

    blockers = report.get("blocker_frequency", {})
    if blockers:
        lines.append("\U0001f6a7 Top Blockers:")
        for blocker, count in sorted(blockers.items(), key=lambda x: x[1], reverse=True)[:5]:
            lines.append(f"  {blocker}: {count}")
        lines.append("")

    recs = report.get("recommendations", [])
    if recs:
        lines.append("\U0001f4a1 Recommendations:")
        for rec in recs[:5]:
            lines.append(f"  \u2022 {rec}")

    return "\n".join(lines)
