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

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
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

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def get_sequence(self, domain_or_url: str) -> list[dict] | None:
        """Get a successful navigation sequence for a domain. Returns None if none exists or expired."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT steps, updated_at FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
        if not row:
            return None
        try:
            updated = datetime.fromisoformat(row[1])
            if (datetime.now(UTC) - updated).days > _SEQUENCE_TTL_DAYS:
                logger.info("Navigation sequence for %s expired (%s)", domain, row[1])
                return None
        except (ValueError, TypeError):
            pass
        return json.loads(row[0])

    def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool, platform: str = ""):
        """Save a navigation sequence for a domain."""
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()

        # Don't overwrite a non-empty successful sequence with empty steps
        if not steps and success:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                    (domain,),
                ).fetchone()
            if row and json.loads(row[0]):
                logger.debug("Skipping empty-steps save for %s — non-empty sequence already exists", domain)
                return

        steps_json = json.dumps(steps)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO sequences (domain, steps, success, created_at, updated_at, platform)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       steps = excluded.steps,
                       success = excluded.success,
                       updated_at = excluded.updated_at,
                       platform = CASE WHEN excluded.platform != '' THEN excluded.platform ELSE platform END""",
                (domain, steps_json, int(success), now, now, platform),
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
        with sqlite3.connect(self._db_path) as conn:
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

    def mark_failed(self, domain_or_url: str):
        """Mark a learned sequence as failed. Purges after 3 consecutive failures."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
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
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE sequences SET replay_count = replay_count + 1 WHERE domain = ?",
                (domain,),
            )

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM sequences WHERE success = 1").fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
