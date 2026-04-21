"""PerformanceTracker — before/after measurement for every learning action."""

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

_REGRESSION_THRESHOLD = 0.15
_BASELINE_SNAPSHOT_COUNT = 30


@dataclass
class PerformanceSnapshot:
    loop_name: str
    domain: str
    timestamp: str
    metrics: dict


@dataclass
class DomainStats:
    domain: str
    agent_name: str
    sample_size: int
    l0_success_rate: float
    l1_success_rate: float
    l2_success_rate: float
    l3_success_rate: float
    forced_level: Optional[int]
    avg_correction_rate: float
    escalation_frequency: float
    last_updated: str


class PerformanceTracker:
    """Measures before/after impact of every learning action."""

    def __init__(self, db_path: str, memory_manager=None):
        self._db_path = db_path
        self._memory = memory_manager
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS performance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    loop_name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metrics TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS learning_actions (
                    action_id TEXT PRIMARY KEY,
                    loop_name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    before_metrics TEXT NOT NULL,
                    after_metrics TEXT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cognitive_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    success INTEGER NOT NULL,
                    escalated INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snap_loop_domain "
                "ON performance_snapshots(loop_name, domain)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cog_domain "
                "ON cognitive_outcomes(domain, agent_name)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def snapshot(self, loop_name: str, domain: str,
                 metrics: dict) -> PerformanceSnapshot:
        ts = datetime.now(timezone.utc).isoformat()
        snap = PerformanceSnapshot(
            loop_name=loop_name, domain=domain,
            timestamp=ts, metrics=metrics,
        )
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO performance_snapshots
                   (loop_name, domain, timestamp, metrics)
                   VALUES (?, ?, ?, ?)""",
                (loop_name, domain, ts, json.dumps(metrics)),
            )
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM performance_snapshots "
                "WHERE loop_name = ? AND domain = ?",
                (loop_name, domain),
            ).fetchone()["cnt"]

        if count >= _BASELINE_SNAPSHOT_COUNT and self._memory:
            self._store_baseline(loop_name, domain, metrics)

        return snap

    def _store_baseline(self, loop_name: str, domain: str, metrics: dict):
        content = (
            f"Baseline for {loop_name} on {domain}: "
            + ", ".join(f"{k}={v}" for k, v in metrics.items())
        )
        try:
            self._memory.learn_fact(
                domain=f"optimization_baseline_{domain}",
                fact=content,
                run_id=f"baseline_{loop_name}_{domain}",
            )
        except Exception as e:
            logger.warning("Failed to store baseline: %s", e)

    def before_learning_action(self, loop_name: str, domain: str,
                               metrics: dict) -> str:
        action_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO learning_actions
                   (action_id, loop_name, domain, before_metrics, started_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (action_id, loop_name, domain, json.dumps(metrics), ts),
            )
        return action_id

    def after_learning_action(self, action_id: str, metrics: dict) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE learning_actions SET after_metrics = ?, completed_at = ? "
                "WHERE action_id = ?",
                (json.dumps(metrics), ts, action_id),
            )
            row = conn.execute(
                "SELECT * FROM learning_actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()

        before = json.loads(row["before_metrics"])
        after = metrics

        common_keys = set(before.keys()) & set(after.keys())
        if not common_keys:
            return {"improvement": 0, "regression": False, "improved": False}

        key = next(iter(common_keys))
        before_val = float(before[key])
        after_val = float(after[key])
        diff = after_val - before_val

        if before_val == 0:
            pct_change = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
        else:
            pct_change = abs(diff) / abs(before_val)

        is_rate_metric = "rate" in key
        if is_rate_metric:
            regression = after_val > before_val and pct_change > _REGRESSION_THRESHOLD
            improved = after_val < before_val and pct_change > 0.10
        else:
            regression = after_val < before_val and pct_change > _REGRESSION_THRESHOLD
            improved = after_val > before_val and pct_change > 0.10

        return {
            "improvement": diff,
            "regression": regression,
            "improved": improved,
            "before": before,
            "after": after,
            "action_id": action_id,
        }

    def get_snapshots(self, loop_name: str, domain: str,
                      limit: int = 100) -> list[PerformanceSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM performance_snapshots
                   WHERE loop_name = ? AND domain = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (loop_name, domain, limit),
            ).fetchall()
        return [
            PerformanceSnapshot(
                loop_name=r["loop_name"], domain=r["domain"],
                timestamp=r["timestamp"],
                metrics=json.loads(r["metrics"]),
            )
            for r in rows
        ]

    def get_avg_metric(self, loop_name: str, domain: str,
                       metric_name: str) -> Optional[float]:
        snaps = self.get_snapshots(loop_name, domain)
        values = [s.metrics.get(metric_name) for s in snaps
                  if metric_name in s.metrics]
        if not values:
            return None
        return sum(values) / len(values)

    def get_trend(self, loop_name: str, domain: str,
                  metric_name: str) -> str:
        snaps = self.get_snapshots(loop_name, domain, limit=10)
        values = [s.metrics.get(metric_name) for s in reversed(snaps)
                  if metric_name in s.metrics]
        if len(values) < 5:
            return "insufficient_data"
        first_half = sum(values[:len(values)//2]) / (len(values)//2)
        second_half = sum(values[len(values)//2:]) / (len(values) - len(values)//2)
        if second_half > first_half * 1.05:
            return "improving"
        elif second_half < first_half * 0.95:
            return "declining"
        return "stable"

    def record_cognitive_outcome(self, domain: str, agent_name: str,
                                 level: int, success: bool,
                                 escalated: bool = False):
        ts = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO cognitive_outcomes
                   (domain, agent_name, level, success, escalated, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (domain, agent_name, level, int(success), int(escalated), ts),
            )

    def get_domain_stats(self, domain: str, agent_name: str) -> DomainStats:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cognitive_outcomes WHERE domain = ? AND agent_name = ?",
                (domain, agent_name),
            ).fetchall()

        total = len(rows)
        if total == 0:
            return DomainStats(
                domain=domain, agent_name=agent_name, sample_size=0,
                l0_success_rate=0.0, l1_success_rate=0.0,
                l2_success_rate=0.0, l3_success_rate=0.0,
                forced_level=None, avg_correction_rate=0.0,
                escalation_frequency=0.0,
                last_updated=datetime.now(timezone.utc).isoformat(),
            )

        def _rate(lvl: int) -> float:
            at_level = [r for r in rows if r["level"] == lvl]
            if not at_level:
                return 0.0
            return sum(1 for r in at_level if r["success"]) / len(at_level)

        escalated_count = sum(1 for r in rows if r["escalated"])
        forced = None
        if total >= 20:
            l0_rate = _rate(0)
            if l0_rate >= 0.95:
                forced = 0
            elif _rate(1) < 0.50:
                forced = 2

        return DomainStats(
            domain=domain, agent_name=agent_name, sample_size=total,
            l0_success_rate=_rate(0), l1_success_rate=_rate(1),
            l2_success_rate=_rate(2), l3_success_rate=_rate(3),
            forced_level=forced,
            avg_correction_rate=0.0,
            escalation_frequency=escalated_count / total if total else 0.0,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
