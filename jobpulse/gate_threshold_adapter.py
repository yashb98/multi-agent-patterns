"""Per-domain Gate 3 threshold adaptation.

Gate 3 (JD quality) blocks low-effort job listings. Different domains
have different baseline quality. This adapter learns per-domain thresholds
from historical interview rates rather than using a single global value.

Usage:
    adapter = GateThresholdAdapter()
    threshold = adapter.get_threshold_for("quantitative_trading", default=0.65)
    adapter.record_outcome("quantitative_trading", jd_quality=0.72, got_interview=True)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "gate_thresholds.db")

# Domain families that share similar quality baselines
_DOMAIN_FAMILIES: dict[str, list[str]] = {
    "tech": [
        "software_engineer", "backend", "frontend", "fullstack",
        "devops", "sre", "security_engineer", "data_engineer",
    ],
    "ml_ai": [
        "machine_learning", "ml_engineer", "ai_engineer", "data_scientist",
        "research_scientist", "nlp_engineer", "computer_vision",
    ],
    "quant": ["quantitative_trading", "quant_researcher", "quant_developer"],
    "product": ["product_manager", "product_owner", "technical_pm"],
    "design": ["ux_designer", "ui_designer", "product_designer"],
    "default": ["other"],
}


def _resolve_family(domain: str) -> str:
    """Map a specific domain to its family."""
    domain_lower = domain.lower().replace(" ", "_")
    for family, members in _DOMAIN_FAMILIES.items():
        if domain_lower in members:
            return family
    return "default"


@dataclass
class DomainThreshold:
    """Learned threshold for a domain family."""

    family: str
    threshold: float
    samples: int
    interview_rate: float
    confidence: float  # 0-1 based on sample size


class GateThresholdAdapter:
    """Learns and suggests per-domain Gate 3 thresholds."""

    # Minimum samples before trusting learned threshold
    MIN_SAMPLES = 5
    # Max deviation from global default (prevents wild swings with few samples)
    MAX_DEVIATION = 0.25
    GLOBAL_DEFAULT = 0.65

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gate_threshold_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    family TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    jd_quality REAL NOT NULL,
                    got_interview INTEGER DEFAULT 0,
                    recorded_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_gate_family
                ON gate_threshold_outcomes(family, jd_quality)
            """)

    def record_outcome(
        self,
        domain: str,
        jd_quality: float,
        got_interview: bool,
    ) -> None:
        """Record a Gate 3 outcome for learning."""
        from datetime import UTC, datetime

        family = _resolve_family(domain)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO gate_threshold_outcomes
                   (family, domain, jd_quality, got_interview, recorded_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (family, domain, jd_quality, int(got_interview), datetime.now(UTC).isoformat()),
            )

    def get_threshold_for(self, domain: str, default: float = GLOBAL_DEFAULT) -> float:
        """Get the learned threshold for a domain, or default if not enough data."""
        family = _resolve_family(domain)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT jd_quality, got_interview
                   FROM gate_threshold_outcomes WHERE family = ?""",
                (family,),
            ).fetchall()

        if len(rows) < self.MIN_SAMPLES:
            return default

        # Grid search: find threshold that maximizes interview rate among passed jobs
        best_threshold = default
        best_rate = 0.0

        for candidate in [x / 100 for x in range(30, 91)]:
            passed = [r for r in rows if r["jd_quality"] >= candidate]
            if not passed:
                continue
            rate = sum(r["got_interview"] for r in passed) / len(passed)
            if rate > best_rate:
                best_rate = rate
                best_threshold = candidate

        # Clamp deviation from default
        deviation = best_threshold - default
        if abs(deviation) > self.MAX_DEVIATION:
            best_threshold = default + (self.MAX_DEVIATION if deviation > 0 else -self.MAX_DEVIATION)

        return round(best_threshold, 2)

    def get_domain_stats(self, domain: str) -> dict:
        """Return statistics for a domain family."""
        family = _resolve_family(domain)
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM gate_threshold_outcomes WHERE family = ?",
                (family,),
            ).fetchone()[0]
            interviews = conn.execute(
                "SELECT SUM(got_interview) FROM gate_threshold_outcomes WHERE family = ?",
                (family,),
            ).fetchone()[0]

        return {
            "family": family,
            "samples": total,
            "interviews": interviews or 0,
            "interview_rate": round((interviews or 0) / total, 3) if total > 0 else 0.0,
            "threshold": self.get_threshold_for(domain),
        }
