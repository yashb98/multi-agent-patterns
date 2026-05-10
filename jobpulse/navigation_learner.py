"""Per-domain navigation sequence learning.

After a successful application, saves the sequence of page types and actions
taken to reach the application form. On repeat visits to the same domain,
replays the learned path (zero LLM cost).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "navigation_learning.db")
_SEQUENCE_TTL_DAYS = 30
_MAX_CONSECUTIVE_FAILURES = 3


class NavigationLearner:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with a busy_timeout so concurrent writers wait
        instead of immediately raising 'database is locked'. WAL alone isn't
        enough — without busy_timeout, any contention fails fast."""
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self):
        with self._connect() as conn:
            # WAL mode is a file-level setting that persists; tolerate
            # contention from another thread also initialising the DB.
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                logger.debug("navigation_learner: WAL pragma contention: %s", exc)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sequences (
                    domain TEXT PRIMARY KEY,
                    steps TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    replay_count INTEGER DEFAULT 0,
                    fail_count INTEGER DEFAULT 0,
                    platform TEXT DEFAULT ''
                )
            """)
            # Migration: add platform column if missing
            try:
                conn.execute("SELECT platform FROM sequences LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE sequences ADD COLUMN platform TEXT DEFAULT ''")
            # Migration: add content_hash column if missing
            try:
                conn.execute("SELECT content_hash FROM sequences LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE sequences ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sequences_content_hash
                ON sequences (content_hash)
            """)

    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            db_path = getattr(self, "_transfer_db_path", None)
            self._te = PlatformTransferEngine(db_path=db_path)
        return self._te

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def get_sequence(self, domain_or_url: str) -> list[dict] | None:
        """Get a successful navigation sequence for a domain. Returns None if none exists or expired."""
        domain = self._normalize_domain(domain_or_url)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT steps, updated_at FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
        if row:
            try:
                updated = datetime.fromisoformat(row[1])
                if (datetime.now(UTC) - updated).days > _SEQUENCE_TTL_DAYS:
                    logger.info("Navigation sequence for %s expired (%s)", domain, row[1])
                    row = None
            except (ValueError, TypeError):
                pass
        if row:
            return json.loads(row[0])
        transfer = self._transfer_engine.get_transfer_data(domain, "navigation_flow")
        if transfer:
            with self._connect() as conn:
                donor_row = conn.execute(
                    "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                return json.loads(donor_row[0])
        return None

    def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool, platform: str = "", content_hash: str = ""):
        """Save a navigation sequence for a domain."""
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()

        # Don't overwrite a non-empty successful sequence with empty steps
        if not steps and success:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                    (domain,),
                ).fetchone()
            if row and json.loads(row[0]):
                logger.debug("Skipping empty-steps save for %s — non-empty sequence already exists", domain)
                return

        steps_json = json.dumps(steps)

        with self._connect() as conn:
            conn.execute(
                """INSERT INTO sequences (domain, steps, success, created_at, updated_at, platform, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       steps = excluded.steps,
                       success = excluded.success,
                       updated_at = excluded.updated_at,
                       platform = CASE WHEN excluded.platform != '' THEN excluded.platform ELSE platform END,
                       content_hash = CASE WHEN excluded.content_hash != '' THEN excluded.content_hash ELSE content_hash END""",
                (domain, steps_json, int(success), now, now, platform, content_hash),
            )
        logger.info("Saved navigation sequence for %s (success=%s, %d steps)", domain, success, len(steps))
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="adaptation",
                source_loop="navigation_learner",
                domain=domain,
                agent_name="navigator",
                payload={"param": "navigation_path", "old_value": "", "new_value": f"{len(steps)}_steps", "reason": "learned_navigation"},
                session_id=f"nl_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)

    def get_platform_pattern(self, platform: str, exclude_domain: str = "", min_observations: int = 2) -> list[dict] | None:
        """Return the most common action pattern for a platform across all domains.

        Requires at least `min_observations` domains sharing the same action sequence.
        """
        from collections import Counter

        exclude = self._normalize_domain(exclude_domain) if exclude_domain else ""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT steps FROM sequences WHERE platform = ? AND success = 1 AND domain != ?",
                (platform, exclude),
            ).fetchall()

        if not rows:
            return None

        # Build a fingerprint from the ordered list of action values only (ignore selectors)
        def _action_key(steps_json: str) -> tuple:
            steps = json.loads(steps_json)
            return tuple(s.get("action", "") for s in steps)

        counts: Counter = Counter(_action_key(row[0]) for row in rows)
        most_common_key, count = counts.most_common(1)[0]

        if count < min_observations:
            return None

        # Return the full steps from the first row matching the most common pattern
        for row in rows:
            if _action_key(row[0]) == most_common_key:
                return json.loads(row[0])

        return None  # pragma: no cover

    def get_sequence_by_content_hash(
        self, content_hash: str, exclude_domain: str = "",
    ) -> list[dict] | None:
        """Return a successful sequence from any domain sharing the same content_hash."""
        if not content_hash:
            return None
        exclude = self._normalize_domain(exclude_domain) if exclude_domain else ""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT steps FROM sequences
                   WHERE content_hash = ? AND domain != ? AND success = 1
                   ORDER BY updated_at DESC LIMIT 1""",
                (content_hash, exclude),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None

    def get_failed_sequences(self, domain_or_url: str) -> list[dict]:
        """Return all failed sequences for a domain."""
        domain = self._normalize_domain(domain_or_url)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT steps, updated_at, content_hash FROM sequences WHERE domain = ? AND success = 0",
                (domain,),
            ).fetchall()
        return [
            {"steps": json.loads(r[0]), "updated_at": r[1], "content_hash": r[2]}
            for r in rows
        ]

    def mark_failed(self, domain_or_url: str):
        """Mark a learned sequence as failed. Purges after 3 consecutive failures."""
        domain = self._normalize_domain(domain_or_url)
        with self._connect() as conn:
            conn.execute(
                "UPDATE sequences SET success = 0, fail_count = fail_count + 1 WHERE domain = ?",
                (domain,),
            )
            row = conn.execute("SELECT fail_count FROM sequences WHERE domain = ?", (domain,)).fetchone()
            if row and row[0] >= _MAX_CONSECUTIVE_FAILURES:
                conn.execute("DELETE FROM sequences WHERE domain = ?", (domain,))
                logger.info("Purged navigation sequence for %s after %d failures", domain, row[0])
            else:
                logger.info("Invalidated navigation for %s (fail_count=%d)", domain, row[0] if row else 0)
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="failure",
                source_loop="navigation_learner",
                domain=domain,
                agent_name="navigator",
                payload={"param": "navigation_path", "reason": "replay_failed"},
                session_id=f"nl_fail_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)

    def increment_replay(self, domain_or_url: str):
        """Track that a sequence was replayed."""
        domain = self._normalize_domain(domain_or_url)
        with self._connect() as conn:
            conn.execute(
                "UPDATE sequences SET replay_count = replay_count + 1 WHERE domain = ?",
                (domain,),
            )

    def get_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM sequences WHERE success = 1").fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
