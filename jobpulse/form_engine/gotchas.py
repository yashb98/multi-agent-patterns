"""Runtime gotchas DB — learn and remember form-filling quirks per domain.

When the form engine encounters a problem and figures out the fix, it stores
that knowledge here so the daemon never hits the same wall twice.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

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
                    engine TEXT NOT NULL DEFAULT 'extension',
                    times_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT,
                    PRIMARY KEY (domain, selector_pattern, engine)
                )"""
            )
            # Migration: add engine column if missing (existing DBs)
            try:
                conn.execute("ALTER TABLE gotchas ADD COLUMN engine TEXT NOT NULL DEFAULT 'extension'")
            except sqlite3.OperationalError:
                pass  # Already exists

            # Migration: if table was created with old PK (domain, selector_pattern)
            # without engine, recreate with correct PK
            try:
                conn.execute(
                    """INSERT INTO gotchas (domain, selector_pattern, problem, solution, engine, times_used, created_at)
                       VALUES ('__pk_check__', '__pk_check__', '', '', 'extension', 0, '')
                       ON CONFLICT(domain, selector_pattern, engine) DO NOTHING"""
                )
            except sqlite3.OperationalError:
                # Old PK doesn't include engine — need to recreate
                conn.execute("ALTER TABLE gotchas RENAME TO gotchas_old")
                conn.execute(
                    """CREATE TABLE gotchas (
                        domain TEXT NOT NULL,
                        selector_pattern TEXT NOT NULL,
                        problem TEXT NOT NULL,
                        solution TEXT NOT NULL,
                        engine TEXT NOT NULL DEFAULT 'extension',
                        times_used INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        last_used_at TEXT,
                        PRIMARY KEY (domain, selector_pattern, engine)
                    )"""
                )
                conn.execute(
                    """INSERT INTO gotchas (domain, selector_pattern, problem, solution, engine, times_used, created_at, last_used_at)
                       SELECT domain, selector_pattern, problem, solution,
                              COALESCE(engine, 'extension') AS engine,
                              COALESCE(times_used, 0) AS times_used,
                              created_at, last_used_at
                       FROM gotchas_old"""
                )
                conn.execute("DROP TABLE gotchas_old")

            # Per-domain learned widget patterns — captured from corrections,
            # consulted by the field scanner as Strategy 0 on subsequent
            # visits. fix_count increments on duplicate (domain,label,selector).
            conn.executescript(
                """CREATE TABLE IF NOT EXISTS widget_patterns (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain          TEXT NOT NULL,
                    label           TEXT NOT NULL,
                    selector        TEXT NOT NULL,
                    widget_type     TEXT NOT NULL,
                    ancestor_classes TEXT NOT NULL DEFAULT '',
                    aria_label      TEXT NOT NULL DEFAULT '',
                    captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    fix_count       INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(domain, label, selector)
                );
                CREATE INDEX IF NOT EXISTS idx_widget_patterns_domain
                    ON widget_patterns(domain);
                """
            )
            conn.commit()

    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            db_path = getattr(self, "_transfer_db_path", None)
            self._te = PlatformTransferEngine(db_path=db_path)
        return self._te

    def store(self, domain: str, selector_pattern: str, problem: str, solution: str, engine: str = "extension") -> None:
        """Store or update a gotcha. Overwrites if same domain+selector+engine exists."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO gotchas (domain, selector_pattern, problem, solution, engine, times_used, created_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(domain, selector_pattern, engine) DO UPDATE SET
                       problem = excluded.problem,
                       solution = excluded.solution,
                       created_at = excluded.created_at,
                       times_used = 0""",
                (domain, selector_pattern, problem, solution, engine, now),
            )
            conn.commit()
        logger.info("gotchas: stored %s/%s [%s] -> %s", domain, selector_pattern, engine, solution)

    def lookup(self, domain: str, selector_pattern: str, engine: str = "extension") -> dict | None:
        """Look up a gotcha by exact domain + selector + engine. Returns dict or None."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND selector_pattern = ? AND engine = ?",
                (domain, selector_pattern, engine),
            ).fetchone()
            return dict(row) if row else None

    def lookup_domain(self, domain: str, engine: str = "extension") -> list[dict]:
        """Get all gotchas for a domain filtered by engine."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND engine = ? ORDER BY times_used DESC",
                (domain, engine),
            ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        transfer = self._transfer_engine.get_transfer_data(domain, "failure_patterns")
        if transfer:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_rows = conn.execute(
                    "SELECT * FROM gotchas WHERE domain = ? AND engine = ? ORDER BY times_used DESC",
                    (transfer["donor_domain"], engine),
                ).fetchall()
            if donor_rows:
                return [dict(r) for r in donor_rows]
        return []

    def record_usage(self, domain: str, selector_pattern: str, engine: str = "extension") -> None:
        """Increment times_used and update last_used_at."""
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """UPDATE gotchas SET times_used = times_used + 1, last_used_at = ?
                   WHERE domain = ? AND selector_pattern = ? AND engine = ?""",
                (now, domain, selector_pattern, engine),
            )
            conn.commit()

    def get_skip_domains(self) -> list[str]:
        """Get domains that should always route to manual review."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT domain FROM gotchas WHERE selector_pattern = '*' AND solution = 'skip_manual_review'"
            ).fetchall()
            return [r[0] for r in rows]

    def record_widget_pattern(
        self,
        *,
        domain: str,
        label: str,
        selector: str,
        widget_type: str,
        ancestor_classes: str = "",
        aria_label: str = "",
    ) -> None:
        """Insert or increment a widget pattern.

        The (domain, label, selector) triple is unique — repeat calls bump
        fix_count instead of duplicating rows. Higher fix_count = more
        confident pattern.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO widget_patterns
                     (domain, label, selector, widget_type, ancestor_classes, aria_label)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain, label, selector) DO UPDATE SET
                     fix_count = fix_count + 1,
                     captured_at = datetime('now')""",
                (domain, label, selector, widget_type, ancestor_classes, aria_label),
            )
            conn.commit()

    def get_widget_patterns(self, domain: str) -> list[dict]:
        """Return all stored patterns for a domain, ordered by fix_count desc."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """SELECT label, selector, widget_type, ancestor_classes,
                          aria_label, fix_count
                   FROM widget_patterns
                   WHERE domain = ?
                   ORDER BY fix_count DESC, captured_at DESC""",
                (domain,),
            ).fetchall()
        return [
            {"label": r[0], "selector": r[1], "widget_type": r[2],
             "ancestor_classes": r[3], "aria_label": r[4], "fix_count": r[5]}
            for r in rows
        ]
