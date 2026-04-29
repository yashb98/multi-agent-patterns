"""CV scrutiny calibration — learns which LLM reviewer scores predict success.

Tracks the correlation between Gate 4B LLM scores / deterministic warnings
and actual hiring outcomes (interview, offer, rejection, ghosted).
Over time, this calibrates the scrutiny threshold to maximize interview rate.

Usage:
    calibrator = ScrutinyCalibrator()
    calibrator.calibrate(
        llm_score=8, b1_warnings=[],
        got_interview=True, user_overrode=False,
        job_id="abc123",
    )
    threshold = calibrator.adjusted_threshold()  # e.g., 6.5 instead of 7.0
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "cv_scrutiny_calibration.db")

# Default hardcoded threshold from gate4_quality.py
_DEFAULT_THRESHOLD = 7.0
_MIN_SAMPLES_FOR_CALIBRATION = 10


@dataclass
class CalibrationInsight:
    """Result of a calibration analysis."""

    current_threshold: float
    suggested_threshold: float
    sample_size: int
    interview_rate_above: float
    interview_rate_below: float
    confidence: float


class ScrutinyCalibrator:
    """Calibrates CV scrutiny scores against actual outcomes."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cv_scrutiny_calibration (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    llm_score INTEGER NOT NULL,
                    b1_warning_count INTEGER DEFAULT 0,
                    b1_warnings TEXT,  -- JSON array
                    got_interview INTEGER DEFAULT 0,
                    got_offer INTEGER DEFAULT 0,
                    user_overrode INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scrutiny_job_id
                ON cv_scrutiny_calibration(job_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_scrutiny_score
                ON cv_scrutiny_calibration(llm_score)
            """)

    def calibrate(
        self,
        llm_score: int,
        b1_warnings: list[str],
        *,
        got_interview: bool = False,
        got_offer: bool = False,
        user_overrode: bool = False,
        job_id: str = "",
    ) -> None:
        """Record a data point for future calibration.

        Args:
            llm_score: The LLM scrutiny score (0-10)
            b1_warnings: List of deterministic B1 warnings
            got_interview: Whether the application resulted in an interview
            got_offer: Whether the application resulted in an offer
            user_overrode: Whether the user manually overrode the scrutiny decision
            job_id: Optional job ID for traceability
        """
        import json
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO cv_scrutiny_calibration
                (job_id, llm_score, b1_warning_count, b1_warnings,
                 got_interview, got_offer, user_overrode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    llm_score,
                    len(b1_warnings),
                    json.dumps(b1_warnings),
                    int(got_interview),
                    int(got_offer),
                    int(user_overrode),
                    now,
                ),
            )

        logger.debug(
            "ScrutinyCalibrator: recorded score=%d, interview=%s, offer=%s, job=%s",
            llm_score, got_interview, got_offer, job_id[:8] if job_id else "?",
        )

    def adjusted_threshold(self) -> float:
        """Return the LLM score threshold that maximizes expected interview rate.

        Uses a simple grid search over thresholds 4.0-9.0.
        Returns the hardcoded default if insufficient data.
        """
        import json

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT llm_score, got_interview, user_overrode FROM cv_scrutiny_calibration"
            ).fetchall()

        if len(rows) < _MIN_SAMPLES_FOR_CALIBRATION:
            logger.debug(
                "ScrutinyCalibrator: insufficient data (%d < %d), using default %.1f",
                len(rows), _MIN_SAMPLES_FOR_CALIBRATION, _DEFAULT_THRESHOLD,
            )
            return _DEFAULT_THRESHOLD

        # Grid search: find threshold that maximizes precision * recall proxy
        best_threshold = _DEFAULT_THRESHOLD
        best_score = 0.0

        for threshold in [i * 0.5 for i in range(8, 19)]:  # 4.0 to 9.0
            above = [r for r in rows if r["llm_score"] >= threshold]
            below = [r for r in rows if r["llm_score"] < threshold]

            if not above or not below:
                continue

            interview_rate_above = sum(r["got_interview"] for r in above) / len(above)
            interview_rate_below = sum(r["got_interview"] for r in below) / len(below)

            # Score: reward high interview rate above threshold,
            # penalize if below-threshold also has high rate (threshold too high)
            separation = interview_rate_above - interview_rate_below
            coverage = len(above) / len(rows)

            # Favour thresholds that capture most interviews while maintaining separation
            score = separation * coverage + interview_rate_above * 0.3

            if score > best_score:
                best_score = score
                best_threshold = threshold

        logger.info(
            "ScrutinyCalibrator: adjusted threshold %.1f → %.1f (n=%d, score=%.3f)",
            _DEFAULT_THRESHOLD, best_threshold, len(rows), best_score,
        )
        return best_threshold

    def get_insight(self) -> CalibrationInsight:
        """Return a detailed calibration analysis."""
        import json

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT llm_score, got_interview FROM cv_scrutiny_calibration"
            ).fetchall()

        threshold = self.adjusted_threshold()

        above = [r for r in rows if r["llm_score"] >= threshold]
        below = [r for r in rows if r["llm_score"] < threshold]

        interview_rate_above = (
            sum(r["got_interview"] for r in above) / len(above) if above else 0.0
        )
        interview_rate_below = (
            sum(r["got_interview"] for r in below) / len(below) if below else 0.0
        )

        confidence = min(len(rows) / 100.0, 1.0)  # Max confidence at 100 samples

        return CalibrationInsight(
            current_threshold=_DEFAULT_THRESHOLD,
            suggested_threshold=threshold,
            sample_size=len(rows),
            interview_rate_above=interview_rate_above,
            interview_rate_below=interview_rate_below,
            confidence=confidence,
        )

    def update_outcome(
        self,
        job_id: str,
        *,
        got_interview: bool | None = None,
        got_offer: bool | None = None,
    ) -> bool:
        """Update the outcome fields for a previously recorded job.

        Returns True if a record was updated.
        """
        if not job_id:
            return False

        updates = []
        params: list = []
        if got_interview is not None:
            updates.append("got_interview = ?")
            params.append(int(got_interview))
        if got_offer is not None:
            updates.append("got_offer = ?")
            params.append(int(got_offer))

        if not updates:
            return False

        params.append(job_id)
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute(
                f"UPDATE cv_scrutiny_calibration SET {', '.join(updates)} WHERE job_id = ?",
                params,
            )
            return cur.rowcount > 0

    def get_stats(self) -> dict:
        """Return summary statistics."""
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM cv_scrutiny_calibration"
            ).fetchone()[0]
            interviews = conn.execute(
                "SELECT COUNT(*) FROM cv_scrutiny_calibration WHERE got_interview = 1"
            ).fetchone()[0]
            offers = conn.execute(
                "SELECT COUNT(*) FROM cv_scrutiny_calibration WHERE got_offer = 1"
            ).fetchone()[0]
            overrode = conn.execute(
                "SELECT COUNT(*) FROM cv_scrutiny_calibration WHERE user_overrode = 1"
            ).fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(llm_score) FROM cv_scrutiny_calibration"
            ).fetchone()[0]

        return {
            "total_recorded": total,
            "interviews": interviews,
            "offers": offers,
            "user_overrides": overrode,
            "avg_llm_score": round(avg_score or 0.0, 2),
            "adjusted_threshold": round(self.adjusted_threshold(), 2),
        }
