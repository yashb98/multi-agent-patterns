"""Baseline tracking — append-only SQLite store with regression detection."""

from __future__ import annotations

import sqlite3
import statistics
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from shared.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_name TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_baselines_suite_metric
    ON baselines(suite_name, metric, timestamp);
"""


@dataclass
class Regression:
    metric: str
    baseline_value: float
    current_value: float
    drop_pct: float
    suite_name: str


class BaselineTracker:
    def __init__(self, db_path: str = "data/eval_baselines.db"):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def record(self, suite_name: str, scores: dict[str, float]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for metric, value in scores.items():
                self._conn.execute(
                    "INSERT INTO baselines (suite_name, metric, value, timestamp) VALUES (?, ?, ?, ?)",
                    (suite_name, metric, value, now),
                )
            self._conn.commit()

    def detect_regressions(
        self,
        suite_name: str,
        current: dict[str, float],
        threshold: float = 0.1,
    ) -> list[Regression]:
        regressions = []
        for metric, current_value in current.items():
            trend = self.get_trend(suite_name, metric, n=3)
            if not trend:
                continue
            baseline_value = statistics.median(trend)
            if baseline_value == 0:
                continue
            drop_pct = (baseline_value - current_value) / baseline_value
            if drop_pct > threshold:
                regressions.append(Regression(
                    metric=metric,
                    baseline_value=baseline_value,
                    current_value=current_value,
                    drop_pct=drop_pct,
                    suite_name=suite_name,
                ))
        return regressions

    def get_trend(self, suite_name: str, metric: str, n: int = 10) -> list[float]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT value FROM baselines WHERE suite_name = ? AND metric = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (suite_name, metric, n),
            ).fetchall()
        return [r[0] for r in reversed(rows)]

    def close(self) -> None:
        self._conn.close()
