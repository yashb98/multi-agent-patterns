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
                    fail_count INTEGER DEFAULT 0
                )
            """)

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def get_sequence(self, domain_or_url: str) -> list[dict] | None:
        """Get a successful navigation sequence for a domain. Returns None if none exists."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
        if not row:
            return None
        return json.loads(row[0])

    def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool):
        """Save a navigation sequence for a domain."""
        domain = self._normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        steps_json = json.dumps(steps)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO sequences (domain, steps, success, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       steps = excluded.steps,
                       success = excluded.success,
                       updated_at = excluded.updated_at""",
                (domain, steps_json, int(success), now, now),
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

    def mark_failed(self, domain_or_url: str):
        """Mark a learned sequence as failed (invalidate it)."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "UPDATE sequences SET success = 0, fail_count = fail_count + 1 WHERE domain = ?",
                (domain,),
            )
        logger.info("Invalidated navigation sequence for %s", domain)

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
