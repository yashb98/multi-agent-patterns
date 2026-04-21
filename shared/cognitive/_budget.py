"""Budget guardrails for cognitive reasoning levels."""

import os
import time
from dataclasses import dataclass
from enum import IntEnum

from shared.logging_config import get_logger

logger = get_logger(__name__)


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

    def __init__(self, budget: CognitiveBudget):
        self._budget = budget
        self._window_start = time.monotonic()
        self._l2_count = 0
        self._l3_count = 0
        self._cost_total = 0.0
        self._cooldown_until = 0.0

    def _maybe_reset_window(self):
        elapsed = time.monotonic() - self._window_start
        if elapsed >= 3600:
            self._window_start = time.monotonic()
            self._l2_count = 0
            self._l3_count = 0
            self._cost_total = 0.0
            self._cooldown_until = 0.0

    def record(self, level: ThinkLevel, cost: float):
        self._maybe_reset_window()
        if level == ThinkLevel.L2_REFLEXION:
            self._l2_count += 1
        elif level == ThinkLevel.L3_TREE_OF_THOUGHT:
            self._l3_count += 1
        self._cost_total += cost

        if self._l2_count >= self._budget.max_l2_per_hour or \
           self._l3_count >= self._budget.max_l3_per_hour or \
           self._cost_total >= self._budget.max_cost_per_hour:
            self._cooldown_until = time.monotonic() + self._budget.cooldown_minutes * 60
            logger.warning("Cognitive budget cap reached — cooldown %d min",
                           self._budget.cooldown_minutes)

    def allows(self, level: ThinkLevel) -> bool:
        self._maybe_reset_window()
        if time.monotonic() < self._cooldown_until:
            return level <= ThinkLevel.L1_SINGLE
        if level <= ThinkLevel.L1_SINGLE:
            return True
        if self._cost_total >= self._budget.max_cost_per_hour:
            return False
        if level == ThinkLevel.L2_REFLEXION:
            return self._l2_count < self._budget.max_l2_per_hour
        if level == ThinkLevel.L3_TREE_OF_THOUGHT:
            return self._l3_count < self._budget.max_l3_per_hour
        return True

    def clamp(self, level: ThinkLevel) -> ThinkLevel:
        if not self._budget.enabled and level > ThinkLevel.L1_SINGLE:
            return ThinkLevel.L1_SINGLE
        while level > ThinkLevel.L0_MEMORY and not self.allows(level):
            level = ThinkLevel(level - 1)
        return level

    def report(self) -> dict:
        self._maybe_reset_window()
        return {
            "l2_used": self._l2_count,
            "l2_remaining": max(0, self._budget.max_l2_per_hour - self._l2_count),
            "l3_used": self._l3_count,
            "l3_remaining": max(0, self._budget.max_l3_per_hour - self._l3_count),
            "cost_used": round(self._cost_total, 4),
            "cost_remaining": round(max(0, self._budget.max_cost_per_hour - self._cost_total), 4),
            "enabled": self._budget.enabled,
            "in_cooldown": time.monotonic() < self._cooldown_until,
        }
