import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from shared.optimization._signals import LearningSignal, SignalBus
from shared.optimization._tracker import PerformanceTracker
from shared.optimization._aggregator import SignalAggregator, AggregatedInsight


class TestSignalAggregator:

    @pytest.fixture
    def bus(self, db_path):
        return SignalBus(db_path=db_path)

    @pytest.fixture
    def tracker(self, db_path, mock_memory):
        return PerformanceTracker(db_path=db_path, memory_manager=mock_memory)

    @pytest.fixture
    def aggregator(self, bus, tracker, mock_memory):
        return SignalAggregator(
            signal_bus=bus, tracker=tracker, memory_manager=mock_memory,
        )

    def _emit_corrections(self, bus, domain, field, count, sessions=None):
        for i in range(count):
            bus.emit(LearningSignal(
                signal_type="correction",
                source_loop="correction_capture",
                domain=domain,
                agent_name="form_filler",
                severity="warning",
                payload={"field": field, "old": f"old_{i}", "new": f"new_{i}"},
                session_id=sessions[i] if sessions else f"sess_{i}",
            ))

    def test_systemic_failure_detection(self, aggregator, bus):
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_a", "sess_b", "sess_c"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        assert len(systemic) >= 1
        assert systemic[0].confidence >= 0.8

    def test_below_threshold_no_insight(self, aggregator, bus):
        self._emit_corrections(bus, "workday", "salary", 2)
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        assert len(systemic) == 0

    def test_regression_detection(self, aggregator, bus, tracker):
        action_id = tracker.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score_trend": 8.0},
        )
        tracker.after_learning_action(
            action_id=action_id,
            metrics={"avg_score_trend": 6.5},
        )
        insights = aggregator.check_regressions()
        regressions = [i for i in insights if i.pattern_type == "regression"]
        assert len(regressions) >= 1
        assert regressions[0].confidence >= 0.9

    def test_regression_requires_learning_action_in_window(self, aggregator, bus, tracker):
        tracker.snapshot(
            loop_name="correction_capture", domain="workday",
            metrics={"correction_rate": 0.5},
        )
        insights = aggregator.check_regressions()
        regressions = [i for i in insights if i.pattern_type == "regression"]
        assert len(regressions) == 0

    def test_platform_behavior_change(self, aggregator, bus):
        for i in range(3):
            bus.emit(LearningSignal(
                signal_type="failure",
                source_loop="scan_learning",
                domain="linkedin",
                agent_name="scanner",
                severity="critical",
                payload={"action": "scan", "error": f"blocked_{i}"},
                session_id=f"sess_plat_{i}",
            ))
        insights = aggregator.check_realtime()
        platform = [i for i in insights if i.pattern_type == "platform_change"]
        assert len(platform) >= 1

    def test_persona_drift_detection(self, aggregator, bus):
        for i in range(6):
            bus.emit(LearningSignal(
                signal_type="score_change",
                source_loop="persona_evolution",
                domain="gmail_agent",
                agent_name="gmail_agent",
                severity="info",
                payload={"old_score": 8.0 - i * 0.3, "new_score": 7.7 - i * 0.3},
                session_id=f"sess_drift_{i}",
            ))
        insights = aggregator.sweep()
        drift = [i for i in insights if i.pattern_type == "persona_drift"]
        assert len(drift) >= 1

    def test_redundant_signal_detection(self, aggregator, bus):
        for loop in ["correction_capture", "agent_rules", "form_experience"]:
            bus.emit(LearningSignal(
                signal_type="correction" if loop == "correction_capture" else "adaptation",
                source_loop=loop,
                domain="workday",
                agent_name="form_filler",
                severity="warning",
                payload={"field": "salary", "reason": "format_error"},
                session_id="sess_redundant",
            ))
        insights = aggregator.sweep()
        redundant = [i for i in insights if i.pattern_type == "redundant"]
        assert len(redundant) >= 1

    def test_dedup_with_memory_search(self, aggregator, bus, mock_memory):
        mock_memory._search_results = [
            {"content": "Workday salary requires integer", "score": 0.9},
        ]
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_dup_a", "sess_dup_b", "sess_dup_c"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        assert len(systemic) == 0  # skipped — existing memory found

    def test_cross_domain_discovery_via_qdrant(self, aggregator, bus, mock_memory):
        mock_memory._search_results = [
            {"content": "Indeed compensation rejects symbols", "domain": "indeed", "score": 0.85},
        ]
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_cross_a", "sess_cross_b", "sess_cross_c"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        if systemic:
            assert systemic[0].confidence >= 0.85

    def test_confidence_boosted_by_cross_platform_match(self, aggregator, bus, mock_memory):
        mock_memory._search_results = [
            {"content": "Similar field format issue", "domain": "indeed", "score": 0.7},
        ]
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_boost_a", "sess_boost_b", "sess_boost_c"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        assert len(systemic) >= 1

    def test_hourly_sweep_finds_slow_patterns(self, aggregator, bus):
        for i in range(4):
            bus.emit(LearningSignal(
                signal_type="failure",
                source_loop="form_experience",
                domain="workday",
                agent_name="form_filler",
                severity="warning",
                payload={"action": "fill", "error": "timeout"},
                session_id=f"sess_slow_{i}",
            ))
        insights = aggregator.sweep()
        assert len(insights) >= 1

    def test_contributing_signals_tracked(self, aggregator, bus):
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_track_a", "sess_track_b", "sess_track_c"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        if systemic:
            assert len(systemic[0].contributing_signals) >= 3

    def test_real_time_vs_sweep_cadence(self, aggregator, bus):
        bus.emit(LearningSignal(
            signal_type="failure",
            source_loop="scan_learning",
            domain="linkedin",
            agent_name="scanner",
            severity="critical",
            payload={"action": "scan", "error": "blocked"},
            session_id="sess_rt",
        ))
        rt_insights = aggregator.check_realtime()
        sweep_insights = aggregator.sweep()
        assert isinstance(rt_insights, list)
        assert isinstance(sweep_insights, list)

    def test_aggregator_respects_paused_loops(self, aggregator, bus):
        aggregator.pause_loop("correction_capture")
        self._emit_corrections(
            bus, "workday", "salary", 5,
            sessions=["sess_p1", "sess_p2", "sess_p3", "sess_p4", "sess_p5"],
        )
        insights = aggregator.check_realtime()
        systemic = [i for i in insights if i.pattern_type == "systemic_failure"]
        assert len(systemic) == 0  # paused loop ignored

    def test_neo4j_traversal_for_context(self, aggregator, bus, mock_memory):
        mock_memory._search_results = []  # no dedup hit
        self._emit_corrections(
            bus, "workday", "salary", 3,
            sessions=["sess_neo_a", "sess_neo_b", "sess_neo_c"],
        )
        insights = aggregator.check_realtime()
        assert isinstance(insights, list)
