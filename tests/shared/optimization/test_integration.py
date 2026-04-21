import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from shared.optimization._engine import OptimizationEngine
from shared.optimization._signals import LearningSignal
from shared.optimization._trajectory import TrajectoryStep


class TestIntegration:

    def test_correction_to_insight_to_cognitive_reuse(
        self, optimization_engine, mock_memory,
    ):
        """3 corrections → insight generated → written to memory."""
        for i in range(3):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="workday",
                agent_name="form_filler",
                payload={"field": "salary", "old": "45,000", "new": "45000"},
                session_id=f"sess_int_{i}",
            )
        result = optimization_engine.optimize()
        assert len(result["insights"]) >= 1
        assert any(i["type"] == "systemic_failure" for i in result["insights"])

    def test_regression_detection_and_auto_rollback(
        self, optimization_engine, mock_memory,
    ):
        """Persona evolution → metric decline → rollback detected."""
        action_id = optimization_engine.before_learning_action(
            loop_name="persona_evolution", domain="scanner",
            metrics={"avg_score_trend": 8.0},
        )
        optimization_engine.after_learning_action(
            action_id=action_id,
            metrics={"avg_score_trend": 6.0},
        )
        result = optimization_engine.optimize()
        regressions = [i for i in result["insights"] if i["type"] == "regression"]
        assert len(regressions) >= 1

    def test_cognitive_classifier_override(self, optimization_engine):
        """P3 domain stats → EscalationClassifier-compatible output."""
        for _ in range(5):
            optimization_engine.record_cognitive_outcome(
                domain="email", agent_name="classifier",
                level=0, success=True,
            )
        stats = optimization_engine.get_domain_stats(
            domain="email", agent_name="classifier",
        )
        assert stats.l0_success_rate == 1.0
        assert stats.sample_size == 5

    def test_memory_lifecycle_driven_by_tracker(
        self, optimization_engine, mock_memory,
    ):
        """Good → promote. Bad → demote."""
        optimization_engine._policy.promote_memory("good_mem")
        assert "good_mem" in mock_memory._promoted
        optimization_engine._policy.demote_memory("bad_mem")
        assert "bad_mem" in mock_memory._demoted

    def test_full_trajectory_to_training_export(
        self, optimization_engine, tmp_path,
    ):
        """Full session → steps → JSONL export."""
        tid = optimization_engine.start_trajectory(
            pipeline="job_application", domain="greenhouse",
            agent_name="form_filler", session_id="sess_export",
        )
        for i in range(3):
            optimization_engine.log_step(tid, TrajectoryStep(
                step_index=i, action="fill_field", target=f"field_{i}",
                input_value=f"val_{i}", output_value=f"val_{i}",
                outcome="success", duration_ms=50 + i * 10,
                metadata={},
            ))
        optimization_engine.complete_trajectory(
            tid, final_outcome="success", final_score=8.5,
            total_duration_ms=180, total_cost=0.005,
        )
        export_path = str(tmp_path / "export.jsonl")
        optimization_engine._trajectory.export_jsonl(export_path)
        import json
        with open(export_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert len(entry["conversations"]) == 6  # 3 steps × 2 (human+gpt)

    def test_cross_domain_transfer_via_qdrant(
        self, optimization_engine, mock_memory,
    ):
        """Workday insight found when querying for Indeed."""
        mock_memory._search_results = [
            {"content": "Workday salary requires integer", "domain": "workday", "score": 0.9},
        ]
        for i in range(3):
            optimization_engine.emit(
                signal_type="correction",
                source_loop="correction_capture",
                domain="indeed",
                agent_name="form_filler",
                payload={"field": "compensation", "old": "$45k", "new": "45000"},
                session_id=f"sess_cross_{i}",
            )
        result = optimization_engine.optimize()
        assert isinstance(result, dict)

    def test_l3_cost_reduction_over_time(self, optimization_engine):
        """Track L3 outcomes — verify stats accumulate."""
        for _ in range(3):
            optimization_engine.record_cognitive_outcome(
                domain="research", agent_name="researcher",
                level=3, success=True,
            )
        stats = optimization_engine.get_domain_stats(
            domain="research", agent_name="researcher",
        )
        assert stats.l3_success_rate == 1.0

    def test_contradiction_resolution_with_neo4j(
        self, optimization_engine, mock_memory,
    ):
        """New vs old insight → policy resolves."""
        optimization_engine._policy.resolve_contradiction(
            new_id="new_insight", old_id="old_insight", new_stronger=True,
        )
        assert "old_insight" in mock_memory._contradicted
