"""A/B engine tracking — SQLite storage for per-field and per-application metrics."""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "ab_engine_tracking.db")


class ABTracker:
    """Tracks per-field events and per-application outcomes for engine comparison."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS field_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT NOT NULL, engine TEXT NOT NULL,
                    platform TEXT, action TEXT NOT NULL, selector TEXT,
                    success BOOLEAN NOT NULL, value_verified BOOLEAN,
                    retry_count INTEGER DEFAULT 0, duration_ms INTEGER,
                    error TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS application_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_id TEXT NOT NULL UNIQUE, engine TEXT NOT NULL,
                    platform TEXT, domain TEXT,
                    total_fields INTEGER DEFAULT 0, fields_filled INTEGER DEFAULT 0,
                    fields_verified INTEGER DEFAULT 0, validation_errors INTEGER DEFAULT 0,
                    outcome TEXT, total_duration_s REAL,
                    pages_navigated INTEGER DEFAULT 0,
                    fixes_applied INTEGER DEFAULT 0, fixes_learned INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS engine_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    engine TEXT NOT NULL, date TEXT NOT NULL,
                    applications INTEGER DEFAULT 0,
                    first_try_success INTEGER DEFAULT 0,
                    total_fixes INTEGER DEFAULT 0, fix_success_rate REAL,
                    gotcha_count INTEGER DEFAULT 0,
                    UNIQUE(engine, date)
                );
            """)

    def log_field(self, *, application_id, engine, action, selector=None,
                  success, value_verified=None, duration_ms=None, error=None,
                  retry_count=0, platform=None):
        """Record a single field fill/click/select event."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO field_events (app_id,engine,platform,action,selector,success,value_verified,retry_count,duration_ms,error,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (application_id, engine, platform, action, selector, success, value_verified, retry_count, duration_ms, error, now),
            )

    def log_outcome(self, *, app_id, engine, platform, domain, total_fields,
                    fields_filled, fields_verified, validation_errors, outcome,
                    total_duration_s, pages_navigated, fixes_applied, fixes_learned):
        """Record the final outcome of a complete application attempt."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO application_outcomes
                   (app_id,engine,platform,domain,total_fields,fields_filled,fields_verified,
                    validation_errors,outcome,total_duration_s,pages_navigated,fixes_applied,fixes_learned,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (app_id, engine, platform, domain, total_fields, fields_filled, fields_verified,
                 validation_errors, outcome, total_duration_s, pages_navigated, fixes_applied, fixes_learned, now),
            )

    def get_engine_stats(self, engine: str, days: int = 7) -> dict:
        """Get aggregated stats for an engine over the given time window."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            r = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN success THEN 1 ELSE 0 END), SUM(CASE WHEN value_verified THEN 1 ELSE 0 END) FROM field_events WHERE engine=? AND created_at>?",
                (engine, cutoff),
            ).fetchone()
            o = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN outcome='submitted' THEN 1 ELSE 0 END) FROM application_outcomes WHERE engine=? AND created_at>?",
                (engine, cutoff),
            ).fetchone()
            return {
                "total_fields": r[0] or 0, "fields_filled": r[1] or 0,
                "fields_verified": r[2] or 0, "applications": o[0] or 0,
                "submit_success": o[1] or 0,
            }
