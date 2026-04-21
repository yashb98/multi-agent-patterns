import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from shared.optimization._tracker import PerformanceTracker, PerformanceSnapshot, DomainStats


class TestPerformanceTracker:

    @pytest.fixture
    def tracker(self, db_path, mock_memory):
        return PerformanceTracker(db_path=db_path, memory_manager=mock_memory)

    def test_snapshot_creation(self, tracker):
        snap = tracker.snapshot(
            loop_name="correction_capture",
            domain="workday",
            metrics={"correction_rate": 0.18, "fields_overridden_pct": 0.12},
        )
        assert snap.loop_name == "correction_capture"
        assert snap.metrics["correction_rate"] == 0.18

    def test_before_after_tagging(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score_trend": 7.5},
        )
        assert action_id  # non-empty string
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"avg_score_trend": 8.0},
        )
        assert delta["improvement"] == pytest.approx(0.5, abs=0.01)

    def test_regression_detected_on_decline(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score_trend": 8.0},
        )
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"avg_score_trend": 6.5},
        )
        assert delta["regression"] is True

    def test_no_regression_on_normal_variance(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score_trend": 8.0},
        )
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"avg_score_trend": 7.5},
        )
        assert delta["regression"] is False

    def test_improvement_detected(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="correction_capture", domain="workday",
            metrics={"correction_rate": 0.18},
        )
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"correction_rate": 0.05},
        )
        assert delta["improved"] is True

    def test_per_loop_metrics_correct(self, tracker):
        tracker.snapshot(
            loop_name="scan_learning", domain="linkedin",
            metrics={"block_rate": 0.1, "cooldown_triggers": 2},
        )
        snaps = tracker.get_snapshots(loop_name="scan_learning", domain="linkedin")
        assert len(snaps) == 1
        assert snaps[0].metrics["block_rate"] == 0.1

    def test_period_aggregation(self, tracker):
        for i in range(5):
            tracker.snapshot(
                loop_name="correction_capture", domain="workday",
                metrics={"correction_rate": 0.1 + i * 0.02},
            )
        avg = tracker.get_avg_metric(
            loop_name="correction_capture", domain="workday",
            metric_name="correction_rate",
        )
        assert avg is not None
        assert 0.1 <= avg <= 0.2

    def test_baseline_stored_to_memory_as_pinned(self, tracker, mock_memory):
        for i in range(31):
            tracker.snapshot(
                loop_name="correction_capture", domain="workday",
                metrics={"correction_rate": 0.1},
            )
        # MockMemoryManager should have received a store call
        assert any("baseline" in str(s.get("content", "")).lower()
                    for s in mock_memory._stored)

    def test_trend_calculation(self, tracker):
        for i in range(6):
            tracker.snapshot(
                loop_name="persona_evolution", domain="scanner",
                metrics={"avg_score_trend": 7.0 + i * 0.3},
            )
        trend = tracker.get_trend(
            loop_name="persona_evolution", domain="scanner",
            metric_name="avg_score_trend",
        )
        assert trend == "improving"

    def test_cognitive_level_tracking(self, tracker):
        tracker.record_cognitive_outcome(
            domain="workday", agent_name="form_filler",
            level=0, success=True,
        )
        tracker.record_cognitive_outcome(
            domain="workday", agent_name="form_filler",
            level=0, success=True,
        )
        tracker.record_cognitive_outcome(
            domain="workday", agent_name="form_filler",
            level=0, success=False,
        )
        stats = tracker.get_domain_stats(domain="workday", agent_name="form_filler")
        assert isinstance(stats, DomainStats)
        assert stats.l0_success_rate == pytest.approx(2 / 3, abs=0.01)
        assert stats.sample_size == 3

    def test_strategy_template_effectiveness(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="strategy_composer", domain="email",
            metrics={"score": 7.0},
        )
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"score": 8.5},
        )
        assert delta["improved"] is True

    def test_failure_pattern_effectiveness(self, tracker):
        action_id = tracker.before_learning_action(
            loop_name="reflexion_failure", domain="workday",
            metrics={"failure_repeat_rate": 0.4},
        )
        delta = tracker.after_learning_action(
            action_id=action_id,
            metrics={"failure_repeat_rate": 0.1},
        )
        assert delta["improved"] is True

    def test_escalation_frequency_tracking(self, tracker):
        for _ in range(5):
            tracker.record_cognitive_outcome(
                domain="workday", agent_name="form_filler",
                level=1, success=True,
            )
        for _ in range(3):
            tracker.record_cognitive_outcome(
                domain="workday", agent_name="form_filler",
                level=1, success=False, escalated=True,
            )
        stats = tracker.get_domain_stats(domain="workday", agent_name="form_filler")
        assert stats.escalation_frequency == pytest.approx(3 / 8, abs=0.01)

    def test_budget_utilization_monitoring(self, tracker):
        tracker.record_cognitive_outcome(
            domain="workday", agent_name="form_filler",
            level=2, success=True,
        )
        stats = tracker.get_domain_stats(domain="workday", agent_name="form_filler")
        assert stats.l2_success_rate == 1.0
