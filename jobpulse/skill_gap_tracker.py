"""Skill Gap Tracker — records missing skills from pre-screened jobs.

Accumulates which JD-required skills the user lacks across all scanned jobs.
Exports a ranked CSV showing the most common skill gaps for upskilling.

Public API:
  record_gap(job_id, title, company, missing_skills, matched_skills, gate3_score)
  get_top_gaps(min_count=5) -> list[dict]
  export_gap_report(output_path) -> Path
  get_gap_stats() -> dict
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DB_PATH = DATA_DIR / "skill_gaps.db"


def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS skill_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill TEXT NOT NULL,
            job_id TEXT NOT NULL,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            gate3_score REAL DEFAULT 0,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skill_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skill TEXT NOT NULL,
            job_id TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_gap_skill ON skill_gaps(skill);
        CREATE INDEX IF NOT EXISTS idx_match_skill ON skill_matches(skill);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gap_unique ON skill_gaps(skill, job_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_match_unique ON skill_matches(skill, job_id);
    """)
    conn.commit()
    conn.close()


# Initialize on import
_init_db()


def record_gap(
    job_id: str,
    title: str,
    company: str,
    missing_skills: list[str],
    matched_skills: list[str],
    gate3_score: float = 0.0,
) -> None:
    """Record missing and matched skills for a pre-screened job.

    Uses INSERT OR IGNORE to prevent duplicates (same skill+job_id).
    """
    now = datetime.now(UTC).isoformat()
    conn = _get_conn()

    for skill in missing_skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_gaps (skill, job_id, job_title, company, gate3_score, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (skill.lower().strip(), job_id, title, company, gate3_score, now),
        )

    for skill in matched_skills:
        conn.execute(
            "INSERT OR IGNORE INTO skill_matches (skill, job_id, recorded_at) "
            "VALUES (?, ?, ?)",
            (skill.lower().strip(), job_id, now),
        )

    conn.commit()
    conn.close()
    logger.debug("skill_gap_tracker: recorded %d gaps, %d matches for %s",
                 len(missing_skills), len(matched_skills), job_id[:8])


def get_top_gaps(min_count: int = 5) -> list[dict]:
    """Return skills that appear as gaps in >= min_count jobs, ranked by frequency.

    Returns list of dicts:
        {"skill": str, "gap_count": int, "match_count": int, "have_it": bool,
         "sample_companies": list[str], "first_seen": str, "last_seen": str}
    """
    conn = _get_conn()

    gaps = conn.execute("""
        SELECT
            skill,
            COUNT(DISTINCT job_id) as gap_count,
            MIN(recorded_at) as first_seen,
            MAX(recorded_at) as last_seen,
            GROUP_CONCAT(DISTINCT company) as companies
        FROM skill_gaps
        GROUP BY skill
        HAVING gap_count >= ?
        ORDER BY gap_count DESC
    """, (min_count,)).fetchall()

    results = []
    for row in gaps:
        # Check if this skill also appears in matches (user has it sometimes via synonyms)
        match_row = conn.execute(
            "SELECT COUNT(DISTINCT job_id) as c FROM skill_matches WHERE skill = ?",
            (row["skill"],),
        ).fetchone()
        match_count = match_row["c"] if match_row else 0

        companies = row["companies"].split(",") if row["companies"] else []
        # Deduplicate and take top 5
        seen: set[str] = set()
        unique_companies: list[str] = []
        for c in companies:
            c = c.strip()
            if c not in seen:
                seen.add(c)
                unique_companies.append(c)

        results.append({
            "skill": row["skill"],
            "gap_count": row["gap_count"],
            "match_count": match_count,
            "have_it": match_count > 0,
            "sample_companies": unique_companies[:5],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
        })

    conn.close()
    return results


def export_gap_report(output_path: str | Path | None = None) -> Path:
    """Export skill gap report as CSV for Google Drive.

    Columns: Rank, Skill, Times Missing, Times Matched, Have It?,
             Action, Sample Companies, First Seen, Last Seen

    Returns the path to the exported CSV.
    """
    if output_path is None:
        output_path = DATA_DIR / "exports" / f"skill_gap_report_{datetime.now(UTC).strftime('%Y%m%d')}.csv"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Get ALL gaps (min_count=1 for full report)
    gaps = get_top_gaps(min_count=1)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Rank", "Skill", "Times Missing", "Times Matched", "Have It?",
            "Action", "Sample Companies", "First Seen", "Last Seen",
        ])

        for i, gap in enumerate(gaps, 1):
            action = "You have this" if gap["have_it"] else "LEARN THIS"
            if not gap["have_it"] and gap["gap_count"] >= 10:
                action = "HIGH PRIORITY - LEARN THIS"
            elif not gap["have_it"] and gap["gap_count"] >= 5:
                action = "LEARN THIS"

            writer.writerow([
                i,
                gap["skill"],
                gap["gap_count"],
                gap["match_count"],
                "Yes" if gap["have_it"] else "No",
                action,
                " | ".join(gap["sample_companies"]),
                gap["first_seen"][:10] if gap["first_seen"] else "",
                gap["last_seen"][:10] if gap["last_seen"] else "",
            ])

    logger.info("skill_gap_tracker: exported %d skills to %s", len(gaps), output_path)
    return output_path


def get_gap_stats() -> dict:
    """Summary stats for the skill gap database."""
    conn = _get_conn()

    total_gaps = conn.execute("SELECT COUNT(DISTINCT skill) FROM skill_gaps").fetchone()[0]
    total_jobs = conn.execute("SELECT COUNT(DISTINCT job_id) FROM skill_gaps").fetchone()[0]
    total_entries = conn.execute("SELECT COUNT(*) FROM skill_gaps").fetchone()[0]

    # Top 5 most common gaps
    top5 = conn.execute("""
        SELECT skill, COUNT(DISTINCT job_id) as c
        FROM skill_gaps
        GROUP BY skill
        ORDER BY c DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    return {
        "unique_gap_skills": total_gaps,
        "jobs_tracked": total_jobs,
        "total_gap_entries": total_entries,
        "top5_gaps": [{"skill": r["skill"], "count": r["c"]} for r in top5],
    }
