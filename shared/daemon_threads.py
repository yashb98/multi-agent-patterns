"""SQLite-backed registry for long-lived daemon threads."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from shared.db import get_pooled_db_conn
from shared.paths import DATA_DIR

_DB_PATH = DATA_DIR / "daemon_threads.db"

_DDL = """
CREATE TABLE IF NOT EXISTS daemon_threads (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    pid INTEGER NOT NULL,
    thread_name TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    stopped_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_daemon_threads_status ON daemon_threads(status);
CREATE INDEX IF NOT EXISTS idx_daemon_threads_kind ON daemon_threads(kind);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn():
    conn = get_pooled_db_conn(_DB_PATH)
    conn.executescript(_DDL)
    conn.commit()
    return conn


def register_daemon_thread(name: str, kind: str, thread_name: str, metadata: dict | None = None) -> None:
    """Insert/update a daemon-thread entry as active."""
    conn = _conn()
    now = _now()
    conn.execute(
        """
        INSERT INTO daemon_threads
            (name, kind, pid, thread_name, status, metadata, started_at, heartbeat_at, stopped_at)
        VALUES (?, ?, ?, ?, 'running', ?, ?, ?, NULL)
        ON CONFLICT(name) DO UPDATE SET
            kind = excluded.kind,
            pid = excluded.pid,
            thread_name = excluded.thread_name,
            status = 'running',
            metadata = excluded.metadata,
            heartbeat_at = excluded.heartbeat_at,
            stopped_at = NULL
        """,
        (
            name,
            kind,
            os.getpid(),
            thread_name,
            json.dumps(metadata or {}, ensure_ascii=True),
            now,
            now,
        ),
    )
    conn.commit()


def heartbeat_daemon_thread(name: str) -> None:
    """Refresh heartbeat timestamp for a running daemon thread."""
    conn = _conn()
    conn.execute(
        "UPDATE daemon_threads SET heartbeat_at = ? WHERE name = ?",
        (_now(), name),
    )
    conn.commit()


def stop_daemon_thread(name: str) -> None:
    """Mark a daemon thread as stopped."""
    conn = _conn()
    now = _now()
    conn.execute(
        """
        UPDATE daemon_threads
        SET status = 'stopped', heartbeat_at = ?, stopped_at = ?
        WHERE name = ?
        """,
        (now, now, name),
    )
    conn.commit()


def list_daemon_threads(status: str | None = None) -> list[dict]:
    """Return daemon-thread rows for observability/debugging."""
    conn = _conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM daemon_threads WHERE status = ? ORDER BY heartbeat_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM daemon_threads ORDER BY heartbeat_at DESC",
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata") or "{}")
        except json.JSONDecodeError:
            item["metadata"] = {}
        out.append(item)
    return out

