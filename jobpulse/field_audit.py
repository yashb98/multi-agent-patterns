"""Per-field fill audit log — tracks method, model, and confidence for every form fill.

Append-only SQLite table used by FormIntelligence to record resolution metadata
and by CorrectionCapture to compute correction rates.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "field_audit.db")


def _normalize_domain(url: str) -> str:
    if "://" in url:
        return urlparse(url).netloc.lower().removeprefix("www.")
    return url.lower().removeprefix("www.")


class FieldAuditDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    application_url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    value TEXT NOT NULL,
                    method TEXT NOT NULL,
                    tier INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_fills_label
                ON field_fills (field_label)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_fills_url
                ON field_fills (application_url)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_fill(
        self,
        application_url: str,
        domain: str,
        platform: str,
        field_label: str,
        value: str,
        method: str,
        tier: int,
        confidence: float,
        model: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        normalized_domain = _normalize_domain(domain) if domain else _normalize_domain(application_url)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO field_fills
                   (application_url, domain, platform, field_label, value,
                    method, tier, confidence, model, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (application_url, normalized_domain, platform,
                 field_label.strip().lower(), value, method, tier,
                 confidence, model, now),
            )

    def get_field_stats(self, field_label: str) -> dict:
        """Return fill statistics for a field label.

        Returns:
            {"total": int, "by_method": {"pattern": n, ...}, "avg_confidence": float}
        """
        label = field_label.strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT method, COUNT(*) as cnt, AVG(confidence) as avg_conf
                   FROM field_fills WHERE field_label = ?
                   GROUP BY method""",
                (label,),
            ).fetchall()

        if not rows:
            return {"total": 0, "by_method": {}, "avg_confidence": 0.0}

        by_method = {r["method"]: r["cnt"] for r in rows}
        total = sum(by_method.values())
        weighted_conf = sum(r["avg_conf"] * r["cnt"] for r in rows) / total
        return {"total": total, "by_method": by_method, "avg_confidence": weighted_conf}

    def get_field_fill_count(self, field_label: str) -> int:
        """Return total fill count for a field label."""
        label = field_label.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM field_fills WHERE field_label = ?",
                (label,),
            ).fetchone()
        return row["cnt"] if row else 0

    def get_application_audit(self, application_url: str) -> list[dict]:
        """Return all fill records for a specific application."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM field_fills WHERE application_url = ? ORDER BY id",
                (application_url,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_method_distribution(self, days: int = 30) -> dict[str, int]:
        """Return aggregate method counts across all fields within a time window."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT method, COUNT(*) as cnt FROM field_fills
                   WHERE created_at >= ?
                   GROUP BY method""",
                (cutoff,),
            ).fetchall()
        return {r["method"]: r["cnt"] for r in rows}

    def get_all_field_fill_counts(self) -> dict[str, int]:
        """Return {field_label: total_count} for all fields."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT field_label, COUNT(*) as cnt FROM field_fills GROUP BY field_label"
            ).fetchall()
        return {r["field_label"]: r["cnt"] for r in rows}
