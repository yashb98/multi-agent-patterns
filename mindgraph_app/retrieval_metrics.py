"""Retrieval quality metrics for MindGraph / GraphRAG.

Tracks latency, result counts, coverage, and hit rates for each retrieval method.
Stored in SQLite at data/retrieval_metrics.db.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from pathlib import Path

from shared.logging_config import get_logger

logger = get_logger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "retrieval_metrics.db"
_db_lock = threading.Lock()


@dataclass
class RetrievalMetrics:
    query: str
    method: str
    latency_ms: float
    entities_found: int
    relations_found: int
    answer_source: str  # "graph", "llm_fallback", "rlm", "none"
    hit: bool  # True if entities_found > 0
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


def _init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS retrieval_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT,
            method TEXT,
            latency_ms REAL,
            entities_found INTEGER,
            relations_found INTEGER,
            answer_source TEXT,
            hit INTEGER,
            timestamp TEXT
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_method ON retrieval_metrics(method)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_timestamp ON retrieval_metrics(timestamp)"""
    )
    conn.commit()
    return conn


def record_metrics(m: RetrievalMetrics) -> None:
    """Record a retrieval metrics entry."""
    with _db_lock:
        conn = _init_db()
        try:
            conn.execute(
                """INSERT INTO retrieval_metrics
                (query, method, latency_ms, entities_found, relations_found, answer_source, hit, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m.query,
                    m.method,
                    m.latency_ms,
                    m.entities_found,
                    m.relations_found,
                    m.answer_source,
                    1 if m.hit else 0,
                    m.timestamp,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.warning("Failed to record retrieval metrics: %s", e)
        finally:
            conn.close()


def get_metrics_summary(since_hours: int = 24) -> dict:
    """Get aggregate retrieval metrics for the last N hours."""
    with _db_lock:
        conn = _init_db()
        try:
            since = datetime.now(UTC).isoformat()[:10] + "T00:00:00"
            # Count by method
            rows = conn.execute(
                """SELECT method,
                    COUNT(*) as total,
                    AVG(latency_ms) as avg_latency,
                    SUM(hit) as hits,
                    AVG(entities_found) as avg_entities,
                    AVG(relations_found) as avg_relations
                FROM retrieval_metrics
                WHERE timestamp > ?
                GROUP BY method""",
                (since,),
            ).fetchall()

            result = {}
            for row in rows:
                result[row["method"]] = {
                    "total_queries": row["total"],
                    "avg_latency_ms": round(row["avg_latency"], 1) if row["avg_latency"] else 0,
                    "hit_rate": round(row["hits"] / row["total"], 2) if row["total"] else 0,
                    "avg_entities": round(row["avg_entities"], 1) if row["avg_entities"] else 0,
                    "avg_relations": round(row["avg_relations"], 1) if row["avg_relations"] else 0,
                }
            return result
        except Exception as e:
            logger.warning("Failed to get metrics summary: %s", e)
            return {}
        finally:
            conn.close()


class RetrievalTimer:
    """Context manager for timing retrievals and auto-recording metrics."""

    def __init__(self, query: str, method: str):
        self.query = query
        self.method = method
        self.start = 0.0
        self.entities_found = 0
        self.relations_found = 0
        self.answer_source = "none"

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        latency_ms = (time.perf_counter() - self.start) * 1000
        record_metrics(
            RetrievalMetrics(
                query=self.query,
                method=self.method,
                latency_ms=latency_ms,
                entities_found=self.entities_found,
                relations_found=self.relations_found,
                answer_source=self.answer_source,
                hit=self.entities_found > 0,
            )
        )
