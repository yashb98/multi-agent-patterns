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

    def validate_against_live(
        self,
        domain_or_url: str,
        live_field_types: list[str],
        live_page_count: int | None = None,
        *,
        match_threshold: float = 0.8,
    ) -> dict:
        """Compare stored experience against live DOM scan.

        Args:
            domain_or_url: The URL or domain to look up.
            live_field_types: Field types discovered from the current page DOM.
            live_page_count: Page count from current DOM (if detectable).
            match_threshold: Minimum overlap ratio to trust stored experience.

        Returns:
            {"trusted": bool, "match_ratio": float, "stored": dict|None,
             "diverged_fields": list[str]}
        """
        stored = self.lookup(domain_or_url)
        if not stored or not stored.get("success"):
            return {"trusted": False, "match_ratio": 0.0, "stored": None,
                    "diverged_fields": []}

        stored_types = json.loads(stored["field_types"]) if isinstance(stored["field_types"], str) else stored["field_types"]

        if not stored_types and not live_field_types:
            return {"trusted": True, "match_ratio": 1.0, "stored": stored,
                    "diverged_fields": []}

        stored_set = set(stored_types)
        live_set = set(live_field_types)
        union = stored_set | live_set
        intersection = stored_set & live_set
        match_ratio = len(intersection) / len(union) if union else 1.0

        diverged = sorted(stored_set.symmetric_difference(live_set))

        trusted = match_ratio >= match_threshold
        if not trusted:
            logger.info(
                "form_experience: DIVERGENCE on %s — match %.0f%% (threshold %.0f%%), "
                "diverged fields: %s. Falling back to LLM detection.",
                self.normalize_domain(domain_or_url),
                match_ratio * 100, match_threshold * 100,
                diverged[:10],
            )

        if live_page_count is not None and stored.get("pages_filled"):
            if abs(live_page_count - stored["pages_filled"]) > 1:
                logger.info(
                    "form_experience: page count mismatch on %s — stored=%d, live=%d",
                    self.normalize_domain(domain_or_url),
                    stored["pages_filled"], live_page_count,
                )
                trusted = False

        return {"trusted": trusted, "match_ratio": match_ratio,
                "stored": stored, "diverged_fields": diverged}

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM form_experience").fetchone()[0]
            successful = conn.execute(
                "SELECT COUNT(*) FROM form_experience WHERE success = 1"
            ).fetchone()[0]
        return {"total_domains": total, "successful_domains": successful}
