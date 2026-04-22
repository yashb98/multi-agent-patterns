"""Draft Queue — SQLite-backed queue for application drafts awaiting human review.

Replaces the broken module-level global approval state with a proper persistent queue.
Each draft tracks its lifecycle: filling → filled → pending_review → submitted/rejected.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from jobpulse.config import DATA_DIR
from shared.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH: Path = DATA_DIR / "application_drafts.db"

_DDL = """
CREATE TABLE IF NOT EXISTS drafts (
    draft_id TEXT PRIMARY KEY,
    job_id TEXT,
    url TEXT NOT NULL,
    platform TEXT,
    company TEXT,
    title TEXT,
    status TEXT NOT NULL DEFAULT 'filling',
    screenshot_path TEXT,
    filled_fields TEXT,           -- JSON: {field_label: value}
    form_pages INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);
CREATE INDEX IF NOT EXISTS idx_drafts_job_id ON drafts(job_id);
CREATE INDEX IF NOT EXISTS idx_drafts_expires ON drafts(expires_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _default_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")


class DraftQueue:
    """Persistent SQLite queue for application drafts."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(_DDL)
            conn.commit()

    def create_draft(
        self,
        job_id: str,
        url: str,
        platform: str,
        company: str,
        title: str,
    ) -> str:
        """Create a new draft entry. Returns draft_id."""
        draft_id = str(uuid.uuid4())[:8]
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO drafts (draft_id, job_id, url, platform, company, title,
                                    status, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'filling', ?, ?, ?)
                """,
                (draft_id, job_id, url, platform, company, title, _now(), _now(), _default_expiry()),
            )
            conn.commit()
        logger.info("DraftQueue: created draft %s for %s @ %s", draft_id, title, company)
        return draft_id

    def update_draft(
        self,
        draft_id: str,
        status: str | None = None,
        screenshot_path: str | None = None,
        filled_fields: dict[str, Any] | None = None,
        form_pages: int | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Update mutable draft fields. Returns True if draft existed."""
        sets: list[str] = ["updated_at = ?"]
        vals: list[Any] = [_now()]

        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        if screenshot_path is not None:
            sets.append("screenshot_path = ?")
            vals.append(screenshot_path)
        if filled_fields is not None:
            sets.append("filled_fields = ?")
            vals.append(json.dumps(filled_fields))
        if form_pages is not None:
            sets.append("form_pages = ?")
            vals.append(form_pages)
        if error_message is not None:
            sets.append("error_message = ?")
            vals.append(error_message)

        vals.append(draft_id)
        with self._conn() as conn:
            cur = conn.execute(
                f"UPDATE drafts SET {', '.join(sets)} WHERE draft_id = ?",
                vals,
            )
            conn.commit()
            updated = cur.rowcount > 0
        if updated:
            logger.info("DraftQueue: updated draft %s → status=%s", draft_id, status)
        return updated

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        """Return draft as dict, or None if not found."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        if not row:
            return None
        draft = dict(row)
        if draft.get("filled_fields"):
            try:
                draft["filled_fields"] = json.loads(draft["filled_fields"])
            except json.JSONDecodeError:
                draft["filled_fields"] = {}
        return draft

    def get_pending_drafts(self) -> list[dict[str, Any]]:
        """Return all drafts awaiting review (not expired)."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM drafts
                WHERE status IN ('filled', 'pending_review')
                  AND expires_at > ?
                ORDER BY created_at DESC
                """,
                (_now(),),
            ).fetchall()
        drafts = []
        for row in rows:
            d = dict(row)
            if d.get("filled_fields"):
                try:
                    d["filled_fields"] = json.loads(d["filled_fields"])
                except json.JSONDecodeError:
                    d["filled_fields"] = {}
            drafts.append(d)
        return drafts

    def get_resumable_drafts(self) -> list[dict[str, Any]]:
        """Return non-terminal drafts that should resume after daemon restart."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM drafts
                WHERE status IN ('filling', 'filled', 'pending_review')
                  AND expires_at > ?
                ORDER BY created_at ASC
                """,
                (_now(),),
            ).fetchall()
        drafts: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            if d.get("filled_fields"):
                try:
                    d["filled_fields"] = json.loads(d["filled_fields"])
                except json.JSONDecodeError:
                    d["filled_fields"] = {}
            drafts.append(d)
        return drafts

    def mark_submitted(self, draft_id: str) -> bool:
        """Mark draft as submitted. Returns True if draft existed."""
        now = _now()
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE drafts SET status = 'submitted', submitted_at = ?, updated_at = ? WHERE draft_id = ?",
                (now, now, draft_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def mark_rejected(self, draft_id: str) -> bool:
        """Mark draft as rejected. Returns True if draft existed."""
        return self.update_draft(draft_id, status="rejected")

    def expire_old_drafts(self, max_age_hours: int = 24) -> int:
        """Mark expired drafts as 'expired'. Returns count."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%dT%H:%M:%S")
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE drafts SET status = 'expired', updated_at = ? WHERE status IN ('filling', 'filled', 'pending_review') AND created_at < ?",
                (_now(), cutoff),
            )
            conn.commit()
            return cur.rowcount

    def get_stats(self) -> dict[str, int]:
        """Return counts by status."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM drafts GROUP BY status"
            ).fetchall()
        return {row[0]: row[1] for row in rows}
