"""Runtime gotchas DB — learn and remember form-filling quirks per domain.

When the form engine encounters a problem and figures out the fix, it stores
that knowledge here so the daemon never hits the same wall twice.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "form_gotchas.db")


class GotchasDB:
    """SQLite-backed store for form-filling gotchas."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS gotchas (
                    domain TEXT NOT NULL,
                    selector_pattern TEXT NOT NULL,
                    problem TEXT NOT NULL,
                    solution TEXT NOT NULL,
                    times_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    PRIMARY KEY (domain, selector_pattern)
                )"""
            )
            conn.commit()

    def store(self, domain: str, selector_pattern: str, problem: str, solution: str) -> None:
        """Store or update a gotcha. Overwrites if same domain+selector exists."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO gotchas (domain, selector_pattern, problem, solution, times_used, created_at)
                   VALUES (?, ?, ?, ?, 0, ?)
                   ON CONFLICT(domain, selector_pattern) DO UPDATE SET
                       problem = excluded.problem,
                       solution = excluded.solution,
                       created_at = excluded.created_at,
                       times_used = 0""",
                (domain, selector_pattern, problem, solution, now),
            )
            conn.commit()
        logger.info("gotchas: stored %s/%s -> %s", domain, selector_pattern, solution)

    def lookup(self, domain: str, selector_pattern: str) -> dict | None:
        """Look up a gotcha by exact domain + selector. Returns dict or None."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND selector_pattern = ?",
                (domain, selector_pattern),
            ).fetchone()
            return dict(row) if row else None

    def lookup_domain(self, domain: str) -> list[dict]:
        """Get all gotchas for a domain."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? ORDER BY times_used DESC",
                (domain,),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_usage(self, domain: str, selector_pattern: str) -> None:
        """Increment times_used and update last_used_at."""
        now = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE gotchas SET times_used = times_used + 1, last_used_at = ?
                   WHERE domain = ? AND selector_pattern = ?""",
                (now, domain, selector_pattern),
            )
            conn.commit()

    def get_skip_domains(self) -> list[str]:
        """Get domains that should always route to manual review."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT domain FROM gotchas WHERE selector_pattern = '*' AND solution = 'skip_manual_review'"
            ).fetchall()
            return [r[0] for r in rows]
