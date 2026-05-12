"""SQLite source-of-truth store for the memory layer.

All writes go to SQLite first (synchronous). Qdrant/Neo4j sync is async.
Embeddings are NOT stored here — they live in Qdrant.
"""

import json
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from shared.logging_config import get_trajectory_id
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

CREATE TABLE IF NOT EXISTS memory_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    action TEXT NOT NULL,
    trajectory_id TEXT NOT NULL,
    accessed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mem_access_traj ON memory_access_log (trajectory_id);
CREATE INDEX IF NOT EXISTS idx_mem_access_memory ON memory_access_log (memory_id);
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

    def _record_read(self, memory_ids: list[str], action: str) -> None:
        trajectory_id = get_trajectory_id()
        if trajectory_id == "no_trajectory" or not memory_ids:
            return
        with self._write_lock:
            conn = self._get_conn()
            now = datetime.now().strftime(_TS_FMT)
            conn.executemany(
                """
                INSERT INTO memory_access_log
                    (memory_id, action, trajectory_id, accessed_at)
                VALUES (?, ?, ?, ?)
                """,
                [(memory_id, action, trajectory_id, now) for memory_id in memory_ids],
            )
            conn.commit()

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

    def count_by_lifecycle(self, lifecycle: Lifecycle) -> int:
        with self._write_lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE lifecycle = ? AND is_tombstoned = 0",
                (lifecycle.value,),
            ).fetchone()
            return row[0] if row else 0

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
        if not row:
            return None
        self._record_read([memory_id], "get_by_id")
        return self._row_to_entry(row)

    def get_by_ids(self, memory_ids: list[str]) -> list[MemoryEntry]:
        """Batch retrieve memories by ID — eliminates N+1 queries."""
        if not memory_ids:
            return []
        conn = self._get_conn()
        placeholders = ",".join("?" * len(memory_ids))
        rows = conn.execute(
            f"SELECT * FROM memories WHERE memory_id IN ({placeholders}) AND is_tombstoned = 0",
            memory_ids,
        ).fetchall()
        self._record_read([r["memory_id"] for r in rows], "get_by_ids")
        return [self._row_to_entry(r) for r in rows]

    def query_active(self, min_decay: float = 0.0) -> list[MemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM memories WHERE is_tombstoned = 0 AND decay_score >= ?",
            (min_decay,),
        ).fetchall()
        self._record_read([r["memory_id"] for r in rows], "query_active")
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

    def query_by_tier_and_domain(
        self,
        tier: MemoryTier,
        domain: str,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """Active entries for a tier+domain, ordered by score then recency.

        Used by ``MemoryManager.get_episodic_entries`` /
        ``get_semantic_entries`` to read SQLite as the source of truth instead
        of the legacy JSON-capped stores (pipeline-bugs M-11.C).
        """
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM memories
            WHERE tier = ? AND domain = ? AND is_tombstoned = 0
            ORDER BY score DESC, last_accessed DESC
            LIMIT ?
            """,
            (tier.value, domain, limit),
        ).fetchall()
        self._record_read([r["memory_id"] for r in rows], "query_by_tier_and_domain")
        return [self._row_to_entry(r) for r in rows]

    def aggregate_procedural_by_strategy(
        self,
        domain: str,
        limit: int = 100,
        prefix_len: int = 50,
    ) -> list[dict]:
        """Group procedural rows by ``content[:prefix_len]`` (mirrors the
        50-char dedup that ``ProceduralMemory.store`` uses) and return one
        representative row per group plus aggregated stats.

        Production has 19 789 procedural rows (99.97 % from the
        ``optimization_success_streak`` write-amplified producer); the
        write path doesn't dedup so cognitive's read-side has to. A single
        windowed query returns the highest-scoring representative row of
        each group together with ``times_used`` (count), ``avg_score``,
        ``avg_success_rate``, eliminating the need for an N+1 lookup.

        Each result dict has keys: ``memory_id, content, payload (str),
        score, created_at, times_used, avg_score, avg_success_rate``.
        """
        conn = self._get_conn()
        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    memory_id, content, payload, score, domain,
                    created_at, last_accessed,
                    SUBSTR(content, 1, {prefix_len}) AS strat_prefix,
                    COUNT(*) OVER (PARTITION BY SUBSTR(content, 1, {prefix_len})) AS times_used,
                    AVG(score) OVER (PARTITION BY SUBSTR(content, 1, {prefix_len})) AS avg_score,
                    AVG(CAST(json_extract(payload, '$.success_rate') AS REAL))
                        OVER (PARTITION BY SUBSTR(content, 1, {prefix_len})) AS avg_success_rate,
                    ROW_NUMBER() OVER (
                        PARTITION BY SUBSTR(content, 1, {prefix_len})
                        ORDER BY score DESC, created_at DESC
                    ) AS rn
                FROM memories
                WHERE tier = 'procedural' AND is_tombstoned = 0 AND domain = ?
            )
            SELECT memory_id, content, payload, score, domain,
                   created_at, last_accessed,
                   times_used, avg_score, avg_success_rate
            FROM ranked
            WHERE rn = 1
            ORDER BY avg_score DESC, times_used DESC
            LIMIT ?
            """,
            (domain, limit),
        ).fetchall()
        return [dict(r) for r in rows]
