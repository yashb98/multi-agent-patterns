"""Step-by-step form interaction log.

Records every action during form filling — fills, clicks, uploads, corrections,
page transitions. Two tables:

- form_interactions: per-step log (session_id groups one application attempt)
- form_page_structure: per-domain page layout (shared across all jobs on same domain)

Cron agents query page structure to know what fields to expect on each page
BEFORE navigating. Replay logs let agents learn the exact flow.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_interactions.db")


class FormInteractionLog:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    platform TEXT,
                    page_num INTEGER,
                    page_title TEXT,
                    step_order INTEGER,
                    step_type TEXT NOT NULL,
                    target_label TEXT,
                    target_selector TEXT,
                    value TEXT,
                    method TEXT,
                    was_corrected INTEGER DEFAULT 0,
                    original_value TEXT,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_interactions_domain_session
                ON form_interactions (domain, session_id)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS form_page_structure (
                    domain TEXT NOT NULL,
                    platform TEXT,
                    page_num INTEGER NOT NULL,
                    page_title TEXT,
                    field_labels TEXT NOT NULL,
                    field_types TEXT NOT NULL,
                    has_file_upload INTEGER DEFAULT 0,
                    nav_buttons TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain, page_num)
                )
            """)

    @staticmethod
    def _normalize_domain(domain_or_url: str) -> str:
        if "://" in domain_or_url:
            parsed = urlparse(domain_or_url)
            return parsed.netloc.lower().removeprefix("www.")
        return domain_or_url.lower().removeprefix("www.")

    def log_step(
        self,
        session_id: str,
        domain: str,
        platform: str | None = None,
        page_num: int | None = None,
        page_title: str | None = None,
        step_order: int | None = None,
        step_type: str = "fill",
        target_label: str | None = None,
        target_selector: str | None = None,
        value: str | None = None,
        method: str | None = None,
        was_corrected: bool = False,
        original_value: str | None = None,
    ) -> None:
        domain = self._normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO form_interactions
                   (session_id, domain, platform, page_num, page_title, step_order,
                    step_type, target_label, target_selector, value, method,
                    was_corrected, original_value, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, domain, platform, page_num, page_title, step_order,
                 step_type, target_label, target_selector, value, method,
                 int(was_corrected), original_value, now),
            )

    def log_page_structure(
        self,
        domain: str,
        platform: str | None,
        page_num: int,
        page_title: str | None,
        field_labels: list[str],
        field_types: list[str],
        has_file_upload: bool = False,
        nav_buttons: list[str] | None = None,
    ) -> None:
        domain = self._normalize_domain(domain)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO form_page_structure
                   (domain, platform, page_num, page_title, field_labels, field_types,
                    has_file_upload, nav_buttons, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(domain, page_num) DO UPDATE SET
                       platform = excluded.platform,
                       page_title = excluded.page_title,
                       field_labels = excluded.field_labels,
                       field_types = excluded.field_types,
                       has_file_upload = excluded.has_file_upload,
                       nav_buttons = excluded.nav_buttons,
                       updated_at = excluded.updated_at""",
                (domain, platform, page_num, page_title,
                 json.dumps(field_labels), json.dumps(field_types),
                 int(has_file_upload), json.dumps(nav_buttons or []), now),
            )
        logger.info(
            "form_interactions: page %d/%s on %s — %d fields, buttons=%s",
            page_num, page_title or "?", domain, len(field_labels), nav_buttons,
        )

    def get_replay(self, domain_or_url: str) -> list[dict]:
        """Get the most recent session's ordered steps for a domain."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            latest = conn.execute(
                "SELECT session_id FROM form_interactions WHERE domain = ? ORDER BY timestamp DESC LIMIT 1",
                (domain,),
            ).fetchone()
            if not latest:
                return []
            rows = conn.execute(
                """SELECT * FROM form_interactions
                   WHERE domain = ? AND session_id = ?
                   ORDER BY step_order ASC, id ASC""",
                (domain, latest["session_id"]),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_page_structure(self, domain_or_url: str) -> list[dict]:
        """Get all page structures for a domain, ordered by page number."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM form_page_structure WHERE domain = ? ORDER BY page_num",
                (domain,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["field_labels"] = json.loads(d["field_labels"])
            d["field_types"] = json.loads(d["field_types"])
            d["nav_buttons"] = json.loads(d["nav_buttons"])
            result.append(d)
        return result

    def get_form_flow(self, domain_or_url: str) -> list[dict]:
        """Get the page sequence for a domain."""
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT page_num, page_title, nav_buttons FROM form_page_structure WHERE domain = ? ORDER BY page_num",
                (domain,),
            ).fetchall()
        return [{"page_num": r["page_num"], "page_title": r["page_title"],
                 "nav_buttons": json.loads(r["nav_buttons"])} for r in rows]

    def get_stats(self) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            total_steps = conn.execute("SELECT COUNT(*) FROM form_interactions").fetchone()[0]
            total_sessions = conn.execute("SELECT COUNT(DISTINCT session_id) FROM form_interactions").fetchone()[0]
            total_domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM form_interactions").fetchone()[0]
        return {"total_steps": total_steps, "total_sessions": total_sessions, "total_domains": total_domains}
