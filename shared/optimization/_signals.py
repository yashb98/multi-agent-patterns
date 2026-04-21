"""LearningSignal and SignalBus — universal event schema for all learning loops."""

import json
import sqlite3
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from shared.logging_config import get_logger

logger = get_logger(__name__)

VALID_SIGNAL_TYPES = frozenset({
    "correction", "failure", "success",
    "adaptation", "score_change", "rollback",
})

VALID_SEVERITIES = frozenset({"info", "warning", "critical"})


@dataclass
class LearningSignal:
    signal_type: str
    source_loop: str
    domain: str
    agent_name: str
    severity: str
    payload: dict
    session_id: str
    timestamp: str = ""
    signal_id: str = ""

    def __post_init__(self):
        if self.signal_type not in VALID_SIGNAL_TYPES:
            raise ValueError(
                f"Invalid signal_type '{self.signal_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_SIGNAL_TYPES))}"
            )
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity '{self.severity}'. "
                f"Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
            )
        if not self.signal_id:
            self.signal_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


class SignalBus:
    """Stores learning signals in SQLite + in-memory deque."""

    def __init__(self, db_path: str, max_recent: int = 1000):
        self._db_path = db_path
        self._recent: deque[LearningSignal] = deque(maxlen=max_recent)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    signal_type TEXT NOT NULL,
                    source_loop TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_domain_ts
                ON signals(domain, timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_source_loop
                ON signals(source_loop)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_signals_session
                ON signals(session_id)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def emit(self, signal: LearningSignal):
        self._recent.append(signal)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO signals
                   (signal_id, signal_type, source_loop, domain, agent_name,
                    severity, payload, session_id, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.signal_id, signal.signal_type, signal.source_loop,
                    signal.domain, signal.agent_name, signal.severity,
                    json.dumps(signal.payload), signal.session_id,
                    signal.timestamp,
                ),
            )

    def query(
        self,
        domain: str = "",
        source_loop: str = "",
        session_id: str = "",
        since: str = "",
        signal_type: str = "",
        limit: int = 500,
    ) -> list[LearningSignal]:
        clauses: list[str] = []
        params: list[str] = []
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if source_loop:
            clauses.append("source_loop = ?")
            params.append(source_loop)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if signal_type:
            clauses.append("signal_type = ?")
            params.append(signal_type)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM signals WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(str(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            LearningSignal(
                signal_type=r["signal_type"],
                source_loop=r["source_loop"],
                domain=r["domain"],
                agent_name=r["agent_name"],
                severity=r["severity"],
                payload=json.loads(r["payload"]),
                session_id=r["session_id"],
                timestamp=r["timestamp"],
                signal_id=r["signal_id"],
            )
            for r in rows
        ]

    def recent(self) -> list[LearningSignal]:
        return list(self._recent)

    def prune(self, max_age_days: int = 90):
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM signals WHERE timestamp < ?", (cutoff,))
        logger.info("Pruned signals older than %d days", max_age_days)

    def count(self, domain: str = "", source_loop: str = "") -> int:
        clauses: list[str] = []
        params: list[str] = []
        if domain:
            clauses.append("domain = ?")
            params.append(domain)
        if source_loop:
            clauses.append("source_loop = ?")
            params.append(source_loop)
        where = " AND ".join(clauses) if clauses else "1=1"
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM signals WHERE {where}", params,
            ).fetchone()
        return row["cnt"]
