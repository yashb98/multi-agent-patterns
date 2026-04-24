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
                "correction_capture: %d corrections, %d unchanged (domain=%s)",
                len(corrections), unchanged, domain,
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
                        agent_name="form_filler",
                        payload={"field": c["field"], "old_value": c["agent"], "new_value": c["user"], "platform": platform},
                        session_id=f"cc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                    )
            except Exception as e:
                logger.debug("Optimization signal failed: %s", e)

        return {"corrections": corrections, "unchanged": unchanged}

    def get_correction_count(self, field_label: str) -> int:
        """Count total corrections for a normalized field label."""
        label = _normalize_label(field_label)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM field_corrections WHERE field_label = ?",
                (label,),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_correction_rate(
        self,
        field_label: str,
        total_fills: int,
        *,
        min_samples: int = 5,
    ) -> float | None:
        """Compute correction rate for a field.

        Args:
            field_label: The field label to check.
            total_fills: Total times this field was filled (from FieldAuditDB).
            min_samples: Minimum total fills required to compute a rate.

        Returns:
            Float 0.0-1.0, or None if total_fills < min_samples.
        """
        if total_fills < min_samples:
            return None
        corrections = self.get_correction_count(field_label)
        return corrections / total_fills

    def get_high_correction_fields(
        self,
        total_fills_by_field: dict[str, int],
        *,
        threshold: float = 0.5,
        min_samples: int = 5,
    ) -> list[dict]:
        """Return fields with correction rate above threshold.

        Args:
            total_fills_by_field: {field_label: total_fill_count} from FieldAuditDB.
            threshold: Minimum correction rate to flag (default 0.5 = 50%).
            min_samples: Minimum fills required.

        Returns:
            List of {"field": str, "rate": float, "corrections": int, "total": int}.
        """
        results = []
        for field_label, total in total_fills_by_field.items():
            rate = self.get_correction_rate(field_label, total, min_samples=min_samples)
            if rate is not None and rate >= threshold:
                results.append({
                    "field": field_label,
                    "rate": rate,
                    "corrections": self.get_correction_count(field_label),
                    "total": total,
                })
        results.sort(key=lambda r: r["rate"], reverse=True)
        return results
