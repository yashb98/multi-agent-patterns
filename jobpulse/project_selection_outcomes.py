"""Project selection outcome tracking — learns which projects lead to interviews.

Tracks every project's performance per job archetype. Future project selection
ranks candidates by historical interview rate rather than just skill overlap.

Usage:
    tracker = ProjectOutcomeTracker()
    tracker.record_selection(
        project_id="multi-agent-patterns",
        archetype="ml_engineer",
        ats_score=88,
        got_interview=True,
    )
    best = tracker.top_projects_for("ml_engineer", top_n=3)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "project_selection_outcomes.db")


@dataclass
class ProjectOutcome:
    """Outcome stats for a single project + archetype combination."""

    project_id: str
    project_name: str
    archetype: str
    times_selected: int
    interviews: int
    offers: int
    avg_ats_score: float
    last_selected_at: str


class ProjectOutcomeTracker:
    """Tracks and ranks projects by their historical interview performance."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_selection_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    archetype TEXT NOT NULL,
                    times_selected INTEGER DEFAULT 0,
                    interviews INTEGER DEFAULT 0,
                    offers INTEGER DEFAULT 0,
                    total_ats_score REAL DEFAULT 0.0,
                    last_selected_at TEXT,
                    UNIQUE(project_id, archetype)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_project_archetype
                ON project_selection_outcomes(archetype)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_project_interview_rate
                ON project_selection_outcomes(archetype, interviews, times_selected)
            """)

    def record_selection(
        self,
        project_id: str,
        project_name: str,
        archetype: str,
        ats_score: float = 0.0,
        got_interview: bool = False,
        got_offer: bool = False,
    ) -> None:
        """Record a project selection event.

        Called after CV generation. Later updated via update_outcome().
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO project_selection_outcomes
                (project_id, project_name, archetype, times_selected, interviews,
                 offers, total_ats_score, last_selected_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(project_id, archetype) DO UPDATE SET
                    times_selected = times_selected + 1,
                    interviews = interviews + excluded.interviews,
                    offers = offers + excluded.offers,
                    total_ats_score = total_ats_score + excluded.total_ats_score,
                    last_selected_at = excluded.last_selected_at
                """,
                (
                    project_id,
                    project_name,
                    archetype,
                    int(got_interview),
                    int(got_offer),
                    ats_score,
                    now,
                ),
            )

    def update_outcome(
        self,
        project_id: str,
        archetype: str,
        *,
        got_interview: bool | None = None,
        got_offer: bool | None = None,
    ) -> bool:
        """Update the outcome for a previously recorded project selection."""
        updates = []
        params: list = []
        if got_interview is not None:
            updates.append("interviews = interviews + ?")
            params.append(int(got_interview))
        if got_offer is not None:
            updates.append("offers = offers + ?")
            params.append(int(got_offer))

        if not updates:
            return False

        params.extend([project_id, archetype])
        set_clause = ", ".join(updates)
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE project_selection_outcomes SET " + set_clause +
                " WHERE project_id = ? AND archetype = ?",
                params,
            )
            return cur.rowcount > 0

    def top_projects_for(
        self,
        archetype: str,
        top_n: int = 5,
        min_selections: int = 2,
    ) -> list[ProjectOutcome]:
        """Return top-performing projects for an archetype.

        Ranks by interview rate, with a bonus for projects with more samples.
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT project_id, project_name, archetype, times_selected,
                       interviews, offers, total_ats_score, last_selected_at
                FROM project_selection_outcomes
                WHERE archetype = ? AND times_selected >= ?
                ORDER BY (CAST(interviews AS REAL) / times_selected) DESC,
                         times_selected DESC
                LIMIT ?
                """,
                (archetype, min_selections, top_n),
            ).fetchall()

        return [
            ProjectOutcome(
                project_id=r["project_id"],
                project_name=r["project_name"],
                archetype=r["archetype"],
                times_selected=r["times_selected"],
                interviews=r["interviews"],
                offers=r["offers"],
                avg_ats_score=(r["total_ats_score"] / r["times_selected"])
                if r["times_selected"] > 0 else 0.0,
                last_selected_at=r["last_selected_at"],
            )
            for r in rows
        ]

    def get_stats(self, archetype: str | None = None) -> dict:
        """Return aggregate statistics."""
        with sqlite3.connect(self._db_path) as conn:
            if archetype:
                total = conn.execute(
                    "SELECT COUNT(*) FROM project_selection_outcomes WHERE archetype = ?",
                    (archetype,),
                ).fetchone()[0]
                total_selections = conn.execute(
                    "SELECT SUM(times_selected) FROM project_selection_outcomes WHERE archetype = ?",
                    (archetype,),
                ).fetchone()[0]
                total_interviews = conn.execute(
                    "SELECT SUM(interviews) FROM project_selection_outcomes WHERE archetype = ?",
                    (archetype,),
                ).fetchone()[0]
            else:
                total = conn.execute(
                    "SELECT COUNT(*) FROM project_selection_outcomes"
                ).fetchone()[0]
                total_selections = conn.execute(
                    "SELECT SUM(times_selected) FROM project_selection_outcomes"
                ).fetchone()[0]
                total_interviews = conn.execute(
                    "SELECT SUM(interviews) FROM project_selection_outcomes"
                ).fetchone()[0]

        total_selections = total_selections or 0
        total_interviews = total_interviews or 0
        return {
            "distinct_projects": total,
            "total_selections": total_selections,
            "total_interviews": total_interviews,
            "interview_rate": round(total_interviews / total_selections, 3)
            if total_selections > 0 else 0.0,
        }
