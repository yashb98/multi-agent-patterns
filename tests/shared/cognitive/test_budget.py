import time
import pytest
from unittest.mock import patch

from shared.cognitive._budget import CognitiveBudget, BudgetTracker, ThinkLevel


class TestBudgetTracker:

    def test_budget_allows_within_limits(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5)
        tracker = BudgetTracker(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_blocks_over_l2_limit(self):
        budget = CognitiveBudget(max_l2_per_hour=3, max_l3_per_hour=5)
        tracker = BudgetTracker(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE

    def test_budget_blocks_over_l3_limit(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=2)
        tracker = BudgetTracker(budget)
        for _ in range(2):
            tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L2_REFLEXION

    def test_cost_cap_blocks_escalation(self):
        budget = CognitiveBudget(max_cost_per_hour=0.10)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.10)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE

    def test_cooldown_after_cap(self):
        budget = CognitiveBudget(max_l2_per_hour=1, cooldown_minutes=5)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Simulate cooldown passing
        tracker._cooldown_until = time.monotonic() - 1
        # Still blocked by hourly count — cooldown alone doesn't reset count
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_resets_hourly(self):
        budget = CognitiveBudget(max_l2_per_hour=1)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Simulate 61 minutes passing by backdating the window start
        tracker._window_start = time.monotonic() - 3700
        assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_disabled_engine(self):
        budget = CognitiveBudget(enabled=False)
        tracker = BudgetTracker(budget)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L1_SINGLE) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L0_MEMORY) == ThinkLevel.L0_MEMORY

    def test_budget_report(self):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5,
                                 max_cost_per_hour=0.50)
        tracker = BudgetTracker(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        report = tracker.report()
        assert report["l2_used"] == 1
        assert report["l2_remaining"] == 19
        assert report["l3_used"] == 1
        assert report["l3_remaining"] == 4
        assert abs(report["cost_used"] - 0.035) < 0.001
        assert abs(report["cost_remaining"] - 0.465) < 0.001
