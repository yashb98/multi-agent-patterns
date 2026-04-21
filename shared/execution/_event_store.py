"""Append-only event store backed by SQLite WAL mode.

Events are immutable records of state changes. Current state is derived
by replaying events through projectors. SQLite is the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import TypedDict

from ulid import ULID

from shared.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    stream_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    metadata    TEXT NOT NULL,
    schema_v    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS stream_snapshots (
    stream_id       TEXT PRIMARY KEY,
    snapshot_state  TEXT NOT NULL,
    last_event_id   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


class Event(TypedDict):
    event_id: str
    stream_id: str
    event_type: str
    payload: dict
    metadata: dict
    schema_v: int
    created_at: str


class EventStore:
    """Append-only event store. Thread-safe via internal lock."""

    def __init__(self, db_path: str = "data/events.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def emit(
        self,
        stream_id: str,
        event_type: str,
        payload: dict,
        metadata: dict | None = None,
        schema_v: int = 1,
    ) -> str:
        """Append an event. Returns the event_id (ULID)."""
        event_id = str(ULID())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
        meta = metadata.copy() if metadata else {}
        meta.setdefault("timestamp", now)

        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_id, stream_id, event_type, payload, metadata, schema_v, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, stream_id, event_type, json.dumps(payload),
                 json.dumps(meta), schema_v, now),
            )
            self._conn.commit()

        logger.debug("Event emitted: %s %s on %s", event_id[:8], event_type, stream_id)
        return event_id

    def get_stream(
        self,
        stream_id: str,
        event_type: str | None = None,
        after_event_id: str | None = None,
    ) -> list[Event]:
        """Get all events in a stream, ordered by created_at."""
        sql = "SELECT * FROM events WHERE stream_id = ?"
        params: list = [stream_id]
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if after_event_id:
            sql += " AND event_id > ?"
            params.append(after_event_id)
        sql += " ORDER BY created_at ASC"

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query(
        self,
        stream_prefix: str | None = None,
        event_types: list[str] | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events across streams."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if stream_prefix:
            sql += " AND stream_id LIKE ?"
            params.append(stream_prefix + "%")
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def find_incomplete_streams(
        self,
        prefix: str,
        start_event: str,
        end_event: str,
    ) -> list[str]:
        """Find streams that have start_event but no end_event."""
        sql = """
            SELECT DISTINCT e1.stream_id
            FROM events e1
            WHERE e1.stream_id LIKE ?
              AND e1.event_type = ?
              AND NOT EXISTS (
                  SELECT 1 FROM events e2
                  WHERE e2.stream_id = e1.stream_id
                    AND e2.event_type = ?
              )
        """
        with self._lock:
            rows = self._conn.execute(sql, (prefix + "%", start_event, end_event)).fetchall()
        return [r[0] for r in rows]

    def save_snapshot(self, stream_id: str, state: dict, last_event_id: str) -> None:
        """Save a projected state snapshot for a stream."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO stream_snapshots "
                "(stream_id, snapshot_state, last_event_id, created_at) VALUES (?, ?, ?, ?)",
                (stream_id, json.dumps(state), last_event_id, now),
            )
            self._conn.commit()

    def load_snapshot(self, stream_id: str) -> dict | None:
        """Load the latest snapshot for a stream."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM stream_snapshots WHERE stream_id = ?", (stream_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "stream_id": row["stream_id"],
            "snapshot_state": json.loads(row["snapshot_state"]),
            "last_event_id": row["last_event_id"],
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            metadata=json.loads(row["metadata"]),
            schema_v=row["schema_v"],
            created_at=row["created_at"],
        )
