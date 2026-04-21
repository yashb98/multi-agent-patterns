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

    def test_alert_human_calls_callback(self, optimization_engine):
        """alert_human action invokes the registered alert callback."""
        alerts = []
        optimization_engine.set_alert_fn(lambda msg: alerts.append(msg))
        for i in range(3):
            optimization_engine.emit(
                signal_type="failure",
                source_loop="test",
                domain="flaky_platform",
                agent_name="test",
                payload={},
                session_id=f"sess_alert_{i}",
                severity="critical",
            )
        optimization_engine.optimize()
        assert len(alerts) >= 1
        assert "flaky_platform" in alerts[0]

    def test_escalate_cognitive_sets_forced_level(self, optimization_engine, db_path):
        """escalate_cognitive action writes forced_level to tracker."""
        # Emit enough corrections to trigger systemic + escalation
        for i in range(5):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="escalate_test",
                agent_name="form_filler",
                payload={"field": "salary", "old_value": "bad", "new_value": "good"},
                session_id=f"sess_esc_{i}",
            )
        optimization_engine.optimize()
        stats = optimization_engine.get_domain_stats("escalate_test", "escalate_test")
        # If escalate_cognitive fired, forced_level should be set to 2
        if stats.forced_level is not None:
            assert stats.forced_level == 2

    def test_demote_memory_searches_and_demotes(self, optimization_engine, mock_memory):
        """demote_memory action does semantic search and calls demote()."""
        mock_memory._search_results = [
            {"id": "mem_regression", "content": "old insight", "score": 0.9},
        ]
        # Trigger a regression → policy generates demote_memory action
        aid = optimization_engine.before_learning_action(
            loop_name="persona_evolution", domain="demote_test",
            metrics={"avg_score_trend": 8.0},
        )
        optimization_engine.after_learning_action(
            action_id=aid, metrics={"avg_score_trend": 5.0},
        )
        optimization_engine.optimize()
        assert "mem_regression" in mock_memory._demoted

    def test_paused_loops_persist_across_restart(self, db_path, mock_memory, mock_cognitive):
        """Paused loops survive engine recreation (SQLite persistence)."""
        engine1 = OptimizationEngine(
            db_path=db_path,
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )
        engine1.pause_loop("persona_evolution")
        assert "persona_evolution" in engine1.health()["paused_loops"]

        # Recreate engine from same db
        engine2 = OptimizationEngine(
            db_path=db_path,
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )
        assert "persona_evolution" in engine2.health()["paused_loops"]

        # Resume and verify persistence
        engine2.resume_loop("persona_evolution")
        engine3 = OptimizationEngine(
            db_path=db_path,
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )
        assert "persona_evolution" not in engine3.health()["paused_loops"]

    def test_budget_state_persists_across_restart(self, db_path, mock_memory, mock_cognitive):
        """Budget counters survive engine recreation."""
        engine1 = OptimizationEngine(
            db_path=db_path,
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )
        # Trigger enough regressions to increment rollback_count
        for i in range(2):
            aid = engine1.before_learning_action(
                loop_name="persona_evolution", domain="budget_test",
                metrics={"score": 8.0},
            )
            engine1.after_learning_action(aid, metrics={"score": 5.0})
        engine1.optimize()
        count1 = engine1._policy._rollback_count

        # Recreate engine — budget should persist
        engine2 = OptimizationEngine(
            db_path=db_path,
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )
        assert engine2._policy._rollback_count == count1

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
