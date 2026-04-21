"""Per-domain form experience store.

Records what the form looked like (adapter, pages, field types, screening questions,
time) after each successful application. Cron jobs query this to skip LLM page
detection and pre-load the right expectations for known domains.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_experience.db")


class FormExperienceDB:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_experience (
                    domain TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    pages_filled INTEGER NOT NULL,
                    field_types TEXT NOT NULL,
                    screening_questions TEXT NOT NULL,
                    time_seconds REAL NOT NULL,
                    success INTEGER NOT NULL,
                    apply_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    @staticmethod
    def normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def record(
        self,
        domain: str,
        platform: str,
        adapter: str,
        pages_filled: int,
        field_types: list[str],
        screening_questions: list[str],
        time_seconds: float,
        success: bool,
    ) -> None:
        domain = self.normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        ft_json = json.dumps(field_types)
        sq_json = json.dumps(screening_questions)

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT success FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()

            if existing and existing[0] == 1 and not success:
                conn.execute(
                    "UPDATE form_experience SET apply_count = apply_count + 1, updated_at = ? WHERE domain = ?",
                    (now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO form_experience
                       (domain, platform, adapter, pages_filled, field_types,
                        screening_questions, time_seconds, success, apply_count,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                       ON CONFLICT(domain) DO UPDATE SET
                           platform = excluded.platform,
                           adapter = excluded.adapter,
                           pages_filled = excluded.pages_filled,
                           field_types = excluded.field_types,
                           screening_questions = excluded.screening_questions,
                           time_seconds = excluded.time_seconds,
                           success = excluded.success,
                           apply_count = apply_count + 1,
                           updated_at = excluded.updated_at""",
                    (domain, platform, adapter, pages_filled, ft_json, sq_json,
                     time_seconds, int(success), now, now),
                )
        logger.info(
            "form_experience: recorded %s (platform=%s, pages=%d, success=%s, fields=%d)",
            domain, platform, pages_filled, success, len(field_types),
        )
        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="success" if success else "failure",
                source_loop="form_experience",
                domain=domain,
                agent_name="form_filler",
                payload={"action": "record_experience", "adapter": adapter, "pages": pages_filled},
                session_id=f"fe_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
            )
        except Exception as e:
            logger.debug("Optimization signal failed: %s", e)

    def lookup(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM form_experience WHERE domain = ?", (domain,)
            ).fetchone()
        return dict(row) if row else None

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
            successful = conn.execute(
                "SELECT COUNT(*) FROM form_experience WHERE success = 1"
            ).fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
