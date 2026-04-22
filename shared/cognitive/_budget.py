"""Budget guardrails for cognitive reasoning levels."""

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path

from shared.db import get_pooled_db_conn
from shared.logging_config import get_logger
from shared.paths import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_BUDGET_DB = DATA_DIR / "cognitive_budget.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cognitive_budget_windows (
    scope TEXT NOT NULL,
    window_start TEXT NOT NULL,
    l2_count INTEGER NOT NULL DEFAULT 0,
    l3_count INTEGER NOT NULL DEFAULT 0,
    cost_total REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (scope, window_start)
);

CREATE TABLE IF NOT EXISTS cognitive_budget_state (
    scope TEXT PRIMARY KEY,
    cooldown_until REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);
"""


def _utc_hour_key(now: datetime | None = None) -> str:
    ts = now or datetime.now(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:00:00Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ThinkLevel(IntEnum):
    L0_MEMORY = 0
    L1_SINGLE = 1
    L2_REFLEXION = 2
    L3_TREE_OF_THOUGHT = 3


@dataclass
class CognitiveBudget:
    max_l2_per_hour: int = 20
    max_l3_per_hour: int = 5
    max_cost_per_hour: float = 0.50
    cooldown_minutes: int = 5
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "CognitiveBudget":
        return cls(
            max_l2_per_hour=int(os.getenv("COGNITIVE_MAX_L2_PER_HOUR", "20")),
            max_l3_per_hour=int(os.getenv("COGNITIVE_MAX_L3_PER_HOUR", "5")),
            max_cost_per_hour=float(os.getenv("COGNITIVE_MAX_COST_PER_HOUR", "0.50")),
            cooldown_minutes=int(os.getenv("COGNITIVE_COOLDOWN_MINUTES", "5")),
            enabled=os.getenv("COGNITIVE_ENABLED", "true").lower() == "true",
        )


class BudgetTracker:
    """Tracks cognitive level usage per hour and enforces caps."""

    def __init__(
        self,
        budget: CognitiveBudget,
        db_path: str | Path | None = None,
        scope: str = "cognitive_global",
    ):
        self._budget = budget
        self._scope = scope
        resolved_path = db_path or os.getenv("COGNITIVE_BUDGET_DB", str(_DEFAULT_BUDGET_DB))
        self._db_path = Path(resolved_path)
        self._conn = get_pooled_db_conn(self._db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _load_window(self, window_start: str) -> dict:
        row = self._conn.execute(
            """
            SELECT l2_count, l3_count, cost_total
            FROM cognitive_budget_windows
            WHERE scope = ? AND window_start = ?
            """,
            (self._scope, window_start),
        ).fetchone()
        if row is None:
            return {"l2_count": 0, "l3_count": 0, "cost_total": 0.0}
        return {
            "l2_count": int(row["l2_count"]),
            "l3_count": int(row["l3_count"]),
            "cost_total": float(row["cost_total"]),
        }

    def _save_window(self, window_start: str, l2_count: int, l3_count: int, cost_total: float) -> None:
        self._conn.execute(
            """
            INSERT INTO cognitive_budget_windows
                (scope, window_start, l2_count, l3_count, cost_total, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, window_start) DO UPDATE SET
                l2_count = excluded.l2_count,
                l3_count = excluded.l3_count,
                cost_total = excluded.cost_total,
                updated_at = excluded.updated_at
            """,
            (self._scope, window_start, l2_count, l3_count, cost_total, _now_iso()),
        )
        self._conn.commit()

    def _get_cooldown_until(self) -> float:
        row = self._conn.execute(
            "SELECT cooldown_until FROM cognitive_budget_state WHERE scope = ?",
            (self._scope,),
        ).fetchone()
        if row is None:
            return 0.0
        return float(row["cooldown_until"] or 0.0)

    def _set_cooldown_until(self, cooldown_until: float) -> None:
        self._conn.execute(
            """
            INSERT INTO cognitive_budget_state (scope, cooldown_until, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                cooldown_until = excluded.cooldown_until,
                updated_at = excluded.updated_at
            """,
            (self._scope, cooldown_until, _now_iso()),
        )
        self._conn.commit()

    def record(self, level: ThinkLevel, cost: float):
        window_start = _utc_hour_key()
        stats = self._load_window(window_start)
        l2_count = stats["l2_count"]
        l3_count = stats["l3_count"]
        cost_total = stats["cost_total"]

        if level == ThinkLevel.L2_REFLEXION:
            l2_count += 1
        elif level == ThinkLevel.L3_TREE_OF_THOUGHT:
            l3_count += 1
        cost_total += max(0.0, float(cost))

        self._save_window(window_start, l2_count, l3_count, cost_total)

        if (
            l2_count >= self._budget.max_l2_per_hour
            or l3_count >= self._budget.max_l3_per_hour
            or cost_total >= self._budget.max_cost_per_hour
        ):
            self._set_cooldown_until(time.time() + self._budget.cooldown_minutes * 60)
            logger.warning(
                "Cognitive budget cap reached — cooldown %d min",
                self._budget.cooldown_minutes,
            )

    def allows(self, level: ThinkLevel) -> bool:
        if not self._budget.enabled and level > ThinkLevel.L1_SINGLE:
            return False

        window_start = _utc_hour_key()
        stats = self._load_window(window_start)
        cooldown_until = self._get_cooldown_until()

        if time.time() < cooldown_until:
            return level <= ThinkLevel.L1_SINGLE
        if level <= ThinkLevel.L1_SINGLE:
            return True
        if stats["cost_total"] >= self._budget.max_cost_per_hour:
            return False
        if level == ThinkLevel.L2_REFLEXION:
            return stats["l2_count"] < self._budget.max_l2_per_hour
        if level == ThinkLevel.L3_TREE_OF_THOUGHT:
            return stats["l3_count"] < self._budget.max_l3_per_hour
        return True

    def clamp(self, level: ThinkLevel) -> ThinkLevel:
        if not self._budget.enabled and level > ThinkLevel.L1_SINGLE:
            return ThinkLevel.L1_SINGLE
        while level > ThinkLevel.L0_MEMORY and not self.allows(level):
            level = ThinkLevel(level - 1)
        return level

    def report(self) -> dict:
        window_start = _utc_hour_key()
        stats = self._load_window(window_start)
        cooldown_until = self._get_cooldown_until()
        return {
            "scope": self._scope,
            "window_start": window_start,
            "l2_used": stats["l2_count"],
            "l2_remaining": max(0, self._budget.max_l2_per_hour - stats["l2_count"]),
            "l3_used": stats["l3_count"],
            "l3_remaining": max(0, self._budget.max_l3_per_hour - stats["l3_count"]),
            "cost_used": round(stats["cost_total"], 4),
            "cost_remaining": round(max(0, self._budget.max_cost_per_hour - stats["cost_total"]), 4),
            "enabled": self._budget.enabled,
            "in_cooldown": time.time() < cooldown_until,
        }
