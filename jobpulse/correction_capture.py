"""Capture per-field corrections when users override agent-filled form values.

Records diffs between agent_mapping and final_mapping from dry-run approvals.
Correction rates drive escalation decisions in FormIntelligence.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "field_corrections.db")


def _normalize_label(label: str) -> str:
    return label.strip().lower()


class CorrectionCapture:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    agent_value TEXT NOT NULL,
                    user_value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_field_label "
                "ON field_corrections (field_label)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_corrections_domain "
                "ON field_corrections (domain)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_corrections(
        self,
        domain: str,
        platform: str,
        agent_mapping: dict[str, str],
        final_mapping: dict[str, str],
        *,
        job_id: str = "",
        source: str = "human",
        agent_name: str = "",
    ) -> dict:
        """Diff agent_mapping vs final_mapping and store each correction.

        Args:
            job_id: If provided, also marks matching field_trajectories as corrected.

        Returns:
            {"corrections": [{"field": ..., "agent": ..., "user": ...}, ...],
             "unchanged": int}
        """
        corrections: list[dict[str, str]] = []
        unchanged = 0
        now = datetime.now(UTC).isoformat()

        for field, agent_value in agent_mapping.items():
            user_value = final_mapping.get(field, agent_value)
            if str(agent_value).strip() != str(user_value).strip():
                corrections.append({
                    "field": field,
                    "agent": str(agent_value),
                    "user": str(user_value),
                })
            else:
                unchanged += 1

        if corrections:
            with self._connect() as conn:
                conn.executemany(
                    """INSERT INTO field_corrections
                       (domain, platform, field_label, agent_value, user_value, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        (domain, platform, _normalize_label(c["field"]),
                         c["agent"], c["user"], now)
                        for c in corrections
                    ],
                )

        if corrections:
            logger.info(
                "correction_capture: %d corrections, %d unchanged (domain=%s, source=%s)",
                len(corrections), unchanged, domain, source,
            )

            # Link corrections to field trajectories
            if job_id:
                try:
                    from jobpulse.trajectory_store import get_trajectory_store
                    ts = get_trajectory_store()
                    for c in corrections:
                        ts.mark_corrected(job_id, domain, c["field"], c["user"])
                except Exception as exc:
                    logger.debug("trajectory linkage failed: %s", exc)

            try:
                from shared.optimization import get_optimization_engine
                engine = get_optimization_engine()
                for c in corrections:
                    engine.emit(
                        signal_type="correction",
                        source_loop="correction_capture",
                        domain=domain,
                        agent_name=agent_name or "form_filler",
                        payload={
                            "field": c["field"],
                            "old_value": c["agent"],
                            "new_value": c["user"],
                            "platform": platform,
                            "source": source,
                        },
                        session_id=f"cc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                    )
            except Exception as e:
                logger.debug("Optimization signal failed: %s", e)

            # Teach the Screening V2 pipeline from each correction
            try:
                from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop
                feedback = ScreeningFeedbackLoop()
                for c in corrections:
                    feedback.learn_from_correction(
                        question=c["field"],
                        agent_answer=c["agent"],
                        user_answer=c["user"],
                        platform=platform,
                        domain=domain,
                    )
            except Exception as e:
                logger.debug("Screening V2 feedback loop failed: %s", e)

        return {"corrections": corrections, "unchanged": unchanged}

    def get_domain_accuracy(self, domain: str) -> float | None:
        """Return accuracy (1 - correction_rate) for a domain, or None if insufficient data."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as total FROM field_corrections WHERE domain = ?",
                (domain,),
            ).fetchone()
        total = row["total"] if row else 0
        if total < 5:
            return None
        from jobpulse.form_experience_db import FormExperienceDB
        exp = FormExperienceDB().lookup(domain)
        if not exp or not exp.get("field_types"):
            return None
        import json
        field_count = len(json.loads(exp["field_types"]) if isinstance(exp["field_types"], str) else exp["field_types"])
        total_fills = field_count * exp.get("apply_count", 1)
        if total_fills < 5:
            return None
        return max(0.0, 1.0 - (total / total_fills))

    def get_field_corrections_by_domain(self, domain: str, limit: int = 10) -> list[dict]:
        """Return most frequently corrected fields for a domain."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT field_label, COUNT(*) as cnt FROM field_corrections "
                "WHERE domain = ? GROUP BY field_label ORDER BY cnt DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        return [{"field_label": r["field_label"], "count": r["cnt"]} for r in rows]

    def get_skill_correction_values(self, min_occurrences: int = 2) -> list[str]:
        """Return user_value strings from skill-related corrections seen multiple times.

        Useful for boosting extra_skills in CV generation — if the user keeps
        correcting a skills field to add a value, we should include it.
        """
        skill_labels = ("skill", "technology", "technologies", "tools", "languages",
                        "frameworks", "proficiency", "expertise")
        placeholders = ",".join("?" for _ in skill_labels)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT user_value, COUNT(*) as cnt FROM field_corrections "
                f"WHERE field_label IN ({placeholders}) "
                f"GROUP BY user_value HAVING cnt >= ? ORDER BY cnt DESC LIMIT 20",
                (*skill_labels, min_occurrences),
            ).fetchall()
        return [r["user_value"] for r in rows]
