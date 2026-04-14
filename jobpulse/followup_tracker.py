"""Follow-up cadence tracker — SQLite-backed urgency-tiered follow-up tracking.

Tracks follow-up actions for job applications with configurable cadence
per status (applied, responded, interview) and urgency classification.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "followups.db")

# ---------------------------------------------------------------------------
# Cadence constants
# ---------------------------------------------------------------------------

APPLIED_FIRST_DAYS = 7
APPLIED_SUBSEQUENT_DAYS = 7
APPLIED_MAX_FOLLOWUPS = 2

RESPONDED_INITIAL_DAYS = 1
RESPONDED_SUBSEQUENT_DAYS = 3

INTERVIEW_THANKYOU_DAYS = 1


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FollowUpEntry:
    job_id: str
    company: str
    role: str
    status: str
    urgency: str
    next_followup_date: Optional[date]
    days_until_next: Optional[int]
    followup_count: int
    contacts: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

def init_db(db_path: str = _DEFAULT_DB_PATH) -> None:
    """Create followups table if it does not exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                date TEXT NOT NULL,
                channel TEXT,
                contact TEXT,
                notes TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Urgency computation
# ---------------------------------------------------------------------------

def compute_urgency(
    status: str,
    last_action_date: date,
    followup_count: int,
) -> str:
    """Return urgency tier: 'urgent' | 'overdue' | 'waiting' | 'cold'.

    Args:
        status: Application status — 'applied', 'responded', or 'interview'.
        last_action_date: Date of the last relevant action (apply date, response date, etc.).
        followup_count: Number of follow-ups already sent.
    """
    today = date.today()
    days_elapsed = (today - last_action_date).days

    if status == "applied":
        if followup_count >= APPLIED_MAX_FOLLOWUPS:
            return "cold"
        threshold = APPLIED_FIRST_DAYS if followup_count == 0 else APPLIED_SUBSEQUENT_DAYS
        if days_elapsed >= threshold:
            return "overdue"
        return "waiting"

    if status == "responded":
        if days_elapsed <= RESPONDED_INITIAL_DAYS:
            return "urgent"
        if days_elapsed >= RESPONDED_SUBSEQUENT_DAYS:
            return "overdue"
        return "waiting"

    if status == "interview":
        if days_elapsed >= INTERVIEW_THANKYOU_DAYS:
            return "overdue"
        return "waiting"

    # Unknown status — treat as waiting
    logger.warning("compute_urgency: unknown status %r, defaulting to 'waiting'", status)
    return "waiting"


# ---------------------------------------------------------------------------
# Record / query
# ---------------------------------------------------------------------------

def record_followup(
    job_id: str,
    channel: str,
    contact: str,
    notes: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> None:
    """Insert a follow-up record for the given job."""
    today = date.today().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO followups (job_id, date, channel, contact, notes) VALUES (?, ?, ?, ?, ?)",
            (job_id, today, channel, contact, notes),
        )
        conn.commit()
    logger.debug("Recorded follow-up for job_id=%s via %s", job_id, channel)


def get_followup_count(job_id: str, db_path: str = _DEFAULT_DB_PATH) -> int:
    """Return the number of follow-ups recorded for job_id."""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM followups WHERE job_id = ?", (job_id,)
        ).fetchone()
    return row[0] if row else 0


# ---------------------------------------------------------------------------
# Telegram-formatted report
# ---------------------------------------------------------------------------

def format_followup_report(entries: List[FollowUpEntry]) -> str:
    """Return a Telegram-formatted follow-up report.

    Shows urgency counts at the top, then one line per entry sorted by
    urgency (overdue → urgent → waiting → cold).
    """
    if not entries:
        return "No follow-ups to report."

    urgency_order = {"overdue": 0, "urgent": 1, "waiting": 2, "cold": 3}
    sorted_entries = sorted(entries, key=lambda e: urgency_order.get(e.urgency, 99))

    counts: dict[str, int] = {}
    for e in entries:
        counts[e.urgency] = counts.get(e.urgency, 0) + 1

    lines = ["*Follow-up Summary*"]
    for tier in ("overdue", "urgent", "waiting", "cold"):
        if tier in counts:
            lines.append(f"  {tier.capitalize()}: {counts[tier]}")

    lines.append("")
    for e in sorted_entries:
        days_str = (
            f"{e.days_until_next}d" if e.days_until_next is not None else "—"
        )
        date_str = e.next_followup_date.isoformat() if e.next_followup_date else "—"
        lines.append(
            f"[{e.urgency.upper()}] {e.company} — {e.role} "
            f"(#{e.followup_count} followups, next: {date_str}, in {days_str})"
        )

    return "\n".join(lines)
