"""SQLite source-of-truth store for the memory layer.

All writes go to SQLite first (synchronous). Qdrant/Neo4j sync is async.
Embeddings are NOT stored here — they live in Qdrant.
"""

import json
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from shared.memory_layer._entries import Lifecycle, MemoryEntry, MemoryTier

_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS memories (
    memory_id    TEXT PRIMARY KEY,
    tier         TEXT NOT NULL,
    lifecycle    TEXT NOT NULL,
    domain       TEXT NOT NULL,
    content      TEXT NOT NULL,
    payload      TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count INTEGER NOT NULL DEFAULT 0,
    decay_score  REAL NOT NULL DEFAULT 1.0,
    score        REAL NOT NULL DEFAULT 0.0,
    confidence   REAL NOT NULL DEFAULT 0.7,
    is_tombstoned INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mem_tier       ON memories (tier);
CREATE INDEX IF NOT EXISTS idx_mem_domain     ON memories (domain);
CREATE INDEX IF NOT EXISTS idx_mem_decay      ON memories (decay_score);
CREATE INDEX IF NOT EXISTS idx_mem_lifecycle  ON memories (lifecycle);

CREATE VIEW IF NOT EXISTS episodic_memories AS
    SELECT * FROM memories WHERE tier = 'episodic' AND is_tombstoned = 0;

CREATE VIEW IF NOT EXISTS semantic_facts AS
    SELECT * FROM memories WHERE tier = 'semantic' AND is_tombstoned = 0;

CREATE VIEW IF NOT EXISTS procedures AS
    SELECT * FROM memories WHERE tier = 'procedural' AND is_tombstoned = 0;
"""


class SQLiteStore:
    """Thread-safe SQLite source-of-truth for MemoryEntry objects."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection management (thread-local)
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # Row conversion
    # ------------------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row["memory_id"],
            tier=MemoryTier(row["tier"]),
            lifecycle=Lifecycle(row["lifecycle"]),
            domain=row["domain"],
            content=row["content"],
            embedding=[],  # vectors live in Qdrant
            created_at=datetime.strptime(row["created_at"], _TS_FMT),
            last_accessed=datetime.strptime(row["last_accessed"], _TS_FMT),
            access_count=row["access_count"],
            decay_score=row["decay_score"],
            score=row["score"],
            confidence=row["confidence"],
            payload=json.loads(row["payload"]),
            is_tombstoned=bool(row["is_tombstoned"]),
        )

    # ------------------------------------------------------------------
    # Write operations (all guarded by write_lock)
    # ------------------------------------------------------------------

    def insert(self, entry: MemoryEntry) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO memories
                    (memory_id, tier, lifecycle, domain, content, payload,
                     created_at, last_accessed, access_count, decay_score,
                     score, confidence, is_tombstoned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.memory_id,
                    entry.tier.value,
                    entry.lifecycle.value,
                    entry.domain,
                    entry.content,
                    json.dumps(entry.payload),
                    entry.created_at.strftime(_TS_FMT),
                    entry.last_accessed.strftime(_TS_FMT),
                    entry.access_count,
                    entry.decay_score,
                    entry.score,
                    entry.confidence,
                    int(entry.is_tombstoned),
                ),
            )
            conn.commit()

    def touch(self, memory_id: str) -> None:
        now = datetime.now().strftime(_TS_FMT)
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                """
                UPDATE memories
                SET last_accessed = ?, access_count = access_count + 1
                WHERE memory_id = ?
                """,
                (now, memory_id),
            )
            conn.commit()

    def update_decay(self, memory_id: str, decay_score: float) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memories SET decay_score = ? WHERE memory_id = ?",
                (decay_score, memory_id),
            )
            conn.commit()

    def update_lifecycle(self, memory_id: str, lifecycle: Lifecycle) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memories SET lifecycle = ? WHERE memory_id = ?",
                (lifecycle.value, memory_id),
            )
            conn.commit()

    def update_confidence(self, memory_id: str, confidence: float) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memories SET confidence = ? WHERE memory_id = ?",
                (confidence, memory_id),
            )
            conn.commit()

    def tombstone(self, memory_id: str) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memories SET is_tombstoned = 1 WHERE memory_id = ?",
                (memory_id,),
            )
            conn.commit()

    def revive(self, memory_id: str) -> None:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE memories SET is_tombstoned = 0 WHERE memory_id = ?",
                (memory_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_by_id(self, memory_id: str) -> Optional[MemoryEntry]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM memories WHERE memory_id = ? AND is_tombstoned = 0",
            (memory_id,),
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def query_by_tier(self, tier: MemoryTier, limit: int = 100) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE tier = ? AND is_tombstoned = 0 LIMIT ?",
            (tier.value, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_domain(self, domain: str, limit: int = 100) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE domain = ? AND is_tombstoned = 0 LIMIT ?",
            (domain, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_lifecycle(self, lifecycle: Lifecycle, limit: int = 100) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE lifecycle = ? AND is_tombstoned = 0 LIMIT ?",
            (lifecycle.value, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_decay_desc(self, limit: int = 50) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE is_tombstoned = 0 ORDER BY decay_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_active(self, min_decay: float = 0.0) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE is_tombstoned = 0 AND decay_score >= ?",
            (min_decay,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_tombstoned_recent(self, domain: str, days: int = 30) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM memories
            WHERE domain = ? AND is_tombstoned = 1
              AND julianday('now') - julianday(last_accessed) <= ?
            ORDER BY last_accessed DESC
            """,
            (domain, days),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def count(self, include_tombstoned: bool = False) -> int:
        conn = self._get_conn()
        if include_tombstoned:
            return conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM memories WHERE is_tombstoned = 0"
        ).fetchone()[0]

    def all_memory_ids(self) -> list[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT memory_id FROM memories WHERE is_tombstoned = 0"
        ).fetchall()
        return [r[0] for r in rows]
