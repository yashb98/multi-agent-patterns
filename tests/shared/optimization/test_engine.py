import os
import pytest
from unittest.mock import patch, MagicMock

from shared.optimization._engine import OptimizationEngine


class TestOptimizationEngine:

    def test_emit_delegates_to_signal_bus(self, optimization_engine):
        optimization_engine.emit(
            signal_type="correction",
            source_loop="correction_capture",
            domain="workday",
            agent_name="form_filler",
            payload={"field": "salary"},
            session_id="sess_001",
        )
        results = optimization_engine._bus.query(domain="workday")
        assert len(results) == 1

    def test_before_after_learning_action_flow(self, optimization_engine):
        action_id = optimization_engine.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score": 7.5},
        )
        assert action_id
        delta = optimization_engine.after_learning_action(
            action_id=action_id,
            metrics={"avg_score": 8.5},
        )
        assert "improvement" in delta

    def test_start_and_complete_trajectory(self, optimization_engine):
        from shared.optimization._trajectory import TrajectoryStep
        tid = optimization_engine.start_trajectory(
            pipeline="job_application", domain="greenhouse",
            agent_name="form_filler", session_id="sess_traj",
        )
        optimization_engine.log_step(tid, TrajectoryStep(
            step_index=0, action="fill_field", target="name",
            input_value="Yash", output_value="Yash",
            outcome="success", duration_ms=50, metadata={},
        ))
        traj = optimization_engine.complete_trajectory(
            tid, final_outcome="success", final_score=8.5,
        )
        assert traj.final_outcome == "success"

    def test_optimize_runs_aggregation_and_policy(self, optimization_engine):
        for i in range(3):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="workday",
                agent_name="form_filler",
                payload={"field": "salary", "old": "45,000", "new": "45000"},
                session_id=f"sess_opt_{i}",
            )
        result = optimization_engine.optimize()
        assert isinstance(result, dict)
        assert "insights" in result

    def test_get_domain_stats_for_cognitive(self, optimization_engine):
        optimization_engine.record_cognitive_outcome(
            domain="workday", agent_name="form_filler",
            level=0, success=True,
        )
        stats = optimization_engine.get_domain_stats(
            domain="workday", agent_name="form_filler",
        )
        assert stats.sample_size == 1

    def test_get_report_returns_formatted_summary(self, optimization_engine):
        optimization_engine.emit(
            signal_type="success",
            source_loop="experience_memory",
            domain="physics",
            agent_name="researcher",
            payload={"score": 9.0},
            session_id="sess_report",
        )
        report = optimization_engine.get_report(domain="physics")
        assert isinstance(report, dict)
        assert "domain" in report

    def test_flush_delegates(self, optimization_engine, mock_cognitive):
        optimization_engine.flush_sync()
        assert mock_cognitive.flush_called

    def test_daily_report_includes_trends(self, optimization_engine):
        for i in range(6):
            optimization_engine._tracker.snapshot(
                loop_name="correction_capture", domain="workday",
                metrics={"correction_rate": 0.1 + i * 0.01},
            )
        report = optimization_engine.daily_report()
        assert isinstance(report, dict)
        assert "by_signal_type" in report
        assert "active_domains" in report
        assert "action_counts" in report

    def test_weekly_maintenance_prunes_and_exports(self, optimization_engine, tmp_path):
        optimization_engine.emit(
            signal_type="success",
            source_loop="experience_memory",
            domain="test",
            agent_name="test",
            payload={},
            session_id="sess_maint",
        )
        result = optimization_engine.weekly_maintenance(
            export_dir=str(tmp_path),
        )
        assert isinstance(result, dict)

    def test_optimize_executes_actions(self, optimization_engine, mock_memory):
        """optimize() executes policy actions, not just returns them."""
        for i in range(3):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="workday",
                agent_name="form_filler",
                payload={"field": "salary", "old_value": "45k", "new_value": "45000"},
                session_id=f"sess_exec_{i}",
            )
        result = optimization_engine.optimize()
        if result["insights"]:
            executed = [a for a in result["actions"] if a.get("executed")]
            assert len(executed) >= 1

    def test_health_returns_engine_state(self, optimization_engine):
        h = optimization_engine.health()
        assert h["enabled"] is True
        assert h["signal_count"] == 0
        assert h["paused_loops"] == []

    def test_memory_failure_degrades_gracefully(self, optimization_engine, mock_memory):
        """Engine doesn't crash when memory fails."""
        mock_memory._should_fail = True
        for i in range(3):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="test",
                agent_name="test",
                payload={"field": "f", "old_value": "a", "new_value": "b"},
                session_id=f"sess_fail_{i}",
            )
        result = optimization_engine.optimize()
        assert isinstance(result, dict)

    def test_promote_demote_via_facade(self, optimization_engine, mock_memory):
        optimization_engine.promote_memory("mem_a")
        assert "mem_a" in mock_memory._promoted
        optimization_engine.demote_memory("mem_b")
        assert "mem_b" in mock_memory._demoted

    def test_disabled_via_env_var(self, db_path, mock_memory, mock_cognitive):
        with patch.dict(os.environ, {"OPTIMIZATION_ENABLED": "false"}):
            engine = OptimizationEngine(
                db_path=db_path,
                memory_manager=mock_memory,
                cognitive_engine=mock_cognitive,
            )
        engine.emit(
            signal_type="correction",
            source_loop="test",
            domain="test",
            agent_name="test",
            payload={},
            session_id="sess_disabled",
        )
        assert engine._bus.count() == 0
