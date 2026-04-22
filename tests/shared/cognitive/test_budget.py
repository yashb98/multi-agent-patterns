import pytest
from unittest.mock import patch

from shared.cognitive._budget import CognitiveBudget, BudgetTracker, ThinkLevel


class TestBudgetTracker:
    @pytest.fixture
    def tracker_factory(self, tmp_path):
        db_path = tmp_path / "budget.db"
        counter = {"n": 0}

        def _make(budget: CognitiveBudget, scope: str | None = None) -> BudgetTracker:
            counter["n"] += 1
            return BudgetTracker(
                budget,
                db_path=str(db_path),
                scope=scope or f"test_scope_{counter['n']}",
            )

        return _make

    def test_budget_allows_within_limits(self, tracker_factory):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5)
        tracker = tracker_factory(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_blocks_over_l2_limit(self, tracker_factory):
        budget = CognitiveBudget(max_l2_per_hour=3, max_l3_per_hour=5)
        tracker = tracker_factory(budget)
        for _ in range(3):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE

    def test_budget_blocks_over_l3_limit(self, tracker_factory):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=2)
        tracker = tracker_factory(budget)
        for _ in range(2):
            tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        # Cooldown triggers after hitting limit — restricts to L1
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE

    def test_cost_cap_blocks_escalation(self, tracker_factory):
        budget = CognitiveBudget(max_cost_per_hour=0.10)
        tracker = tracker_factory(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.10)
        assert not tracker.allows(ThinkLevel.L3_TREE_OF_THOUGHT)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE

    def test_cooldown_after_cap(self, tracker_factory):
        import time

        budget = CognitiveBudget(max_l2_per_hour=1, cooldown_minutes=5)
        tracker = tracker_factory(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Simulate cooldown passing
        tracker._set_cooldown_until(time.time() - 1)
        # Still blocked by hourly count — cooldown alone doesn't reset count
        assert not tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_budget_resets_hourly(self, tracker_factory):
        budget = CognitiveBudget(max_l2_per_hour=1)
        tracker = tracker_factory(budget)
        with patch("shared.cognitive._budget._utc_hour_key", return_value="2026-01-01T10:00:00Z"):
            tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
            assert not tracker.allows(ThinkLevel.L2_REFLEXION)
        # Next hour should be a fresh bucket.
        tracker._set_cooldown_until(0.0)
        with patch("shared.cognitive._budget._utc_hour_key", return_value="2026-01-01T11:00:00Z"):
            assert tracker.allows(ThinkLevel.L2_REFLEXION)

    def test_disabled_engine(self, tracker_factory):
        budget = CognitiveBudget(enabled=False)
        tracker = tracker_factory(budget)
        assert tracker.clamp(ThinkLevel.L3_TREE_OF_THOUGHT) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L2_REFLEXION) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L1_SINGLE) == ThinkLevel.L1_SINGLE
        assert tracker.clamp(ThinkLevel.L0_MEMORY) == ThinkLevel.L0_MEMORY

    def test_budget_report(self, tracker_factory):
        budget = CognitiveBudget(max_l2_per_hour=20, max_l3_per_hour=5,
                                 max_cost_per_hour=0.50)
        tracker = tracker_factory(budget)
        tracker.record(ThinkLevel.L2_REFLEXION, cost=0.005)
        tracker.record(ThinkLevel.L3_TREE_OF_THOUGHT, cost=0.03)
        report = tracker.report()
        assert report["scope"].startswith("test_scope_")
        assert report["window_start"]
        assert report["l2_used"] == 1
        assert report["l2_remaining"] == 19
        assert report["l3_used"] == 1
        assert report["l3_remaining"] == 4
        assert abs(report["cost_used"] - 0.035) < 0.001
        assert abs(report["cost_remaining"] - 0.465) < 0.001
