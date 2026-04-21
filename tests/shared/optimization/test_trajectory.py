import json
import pytest
from datetime import datetime, timedelta, timezone

from shared.optimization._trajectory import TrajectoryStore, Trajectory, TrajectoryStep


class TestTrajectoryStore:

    @pytest.fixture
    def store(self, db_path):
        return TrajectoryStore(db_path=db_path)

    def test_create_trajectory_and_add_steps(self, store):
        tid = store.start(
            pipeline="job_application", domain="greenhouse",
            agent_name="form_filler", session_id="sess_001",
        )
        store.log_step(tid, TrajectoryStep(
            step_index=0, action="fill_field", target="name",
            input_value="Yash", output_value="Yash",
            outcome="success", duration_ms=50, metadata={},
        ))
        store.log_step(tid, TrajectoryStep(
            step_index=1, action="fill_field", target="salary",
            input_value="45,000", output_value="45000",
            outcome="corrected", duration_ms=150, metadata={},
        ))
        traj = store.complete(
            tid, final_outcome="success", final_score=8.5,
            total_duration_ms=200, total_cost=0.003,
        )
        assert traj.pipeline == "job_application"
        assert len(traj.steps) == 2
        assert traj.final_outcome == "success"

    def test_step_ordering_preserved(self, store):
        tid = store.start(
            pipeline="research", domain="physics",
            agent_name="researcher", session_id="sess_002",
        )
        for i in range(5):
            store.log_step(tid, TrajectoryStep(
                step_index=i, action="llm_call", target=f"model_{i}",
                input_value=f"prompt_{i}", output_value=f"answer_{i}",
                outcome="success", duration_ms=100, metadata={},
            ))
        traj = store.complete(tid, final_outcome="success", final_score=9.0)
        assert [s.step_index for s in traj.steps] == [0, 1, 2, 3, 4]

    def test_trajectory_links_to_session_id(self, store):
        tid = store.start(
            pipeline="job_application", domain="workday",
            agent_name="form_filler", session_id="target_session",
        )
        store.complete(tid, final_outcome="success", final_score=7.0)
        results = store.query(session_id="target_session")
        assert len(results) == 1

    def test_jsonl_export_sharegpt_format(self, store, tmp_path):
        tid = store.start(
            pipeline="job_application", domain="greenhouse",
            agent_name="form_filler", session_id="sess_export",
        )
        store.log_step(tid, TrajectoryStep(
            step_index=0, action="fill_field", target="name",
            input_value="Yash", output_value="Yash",
            outcome="success", duration_ms=50, metadata={},
        ))
        store.complete(tid, final_outcome="success", final_score=8.0)
        out_path = str(tmp_path / "export.jsonl")
        store.export_jsonl(out_path)
        with open(out_path) as f:
            lines = f.readlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert "conversations" in entry

    def test_csv_export_for_analytics(self, store, tmp_path):
        tid = store.start(
            pipeline="email_classification", domain="gmail",
            agent_name="classifier", session_id="sess_csv",
        )
        store.complete(tid, final_outcome="success", final_score=9.0)
        out_path = str(tmp_path / "export.csv")
        store.export_csv(out_path)
        with open(out_path) as f:
            lines = f.readlines()
        assert len(lines) == 2  # header + 1 row
        assert "pipeline" in lines[0]

    def test_pruning_removes_old_trajectories(self, store, db_path):
        tid = store.start(
            pipeline="test", domain="test",
            agent_name="test", session_id="sess_prune",
        )
        store.complete(tid, final_outcome="success", final_score=5.0)
        # Backdate the timestamp
        import sqlite3
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE trajectories SET timestamp = ?", (old_ts,))
        store.prune(max_age_days=90)
        assert len(store.query(domain="test")) == 0

    def test_query_by_pipeline_and_domain(self, store):
        for pipeline, domain in [("job_application", "greenhouse"),
                                  ("job_application", "workday"),
                                  ("research", "physics")]:
            tid = store.start(
                pipeline=pipeline, domain=domain,
                agent_name="test", session_id="sess_q",
            )
            store.complete(tid, final_outcome="success", final_score=7.0)
        results = store.query(pipeline="job_application")
        assert len(results) == 2

    def test_query_by_outcome(self, store):
        for outcome in ["success", "failure", "success"]:
            tid = store.start(
                pipeline="test", domain="test",
                agent_name="test", session_id="sess_outcome",
            )
            store.complete(tid, final_outcome=outcome, final_score=5.0)
        results = store.query(final_outcome="failure")
        assert len(results) == 1

    def test_trajectory_step_metadata_round_trips(self, store):
        tid = store.start(
            pipeline="test", domain="test",
            agent_name="test", session_id="sess_meta",
        )
        meta = {"selector": "#salary", "confidence": 0.95, "model": "gpt-4.1-mini"}
        store.log_step(tid, TrajectoryStep(
            step_index=0, action="fill_field", target="salary",
            input_value="45000", output_value="45000",
            outcome="success", duration_ms=100, metadata=meta,
        ))
        traj = store.complete(tid, final_outcome="success", final_score=8.0)
        assert traj.steps[0].metadata == meta

    def test_cost_and_duration_aggregation(self, store):
        tid = store.start(
            pipeline="test", domain="test",
            agent_name="test", session_id="sess_agg",
        )
        store.log_step(tid, TrajectoryStep(
            step_index=0, action="llm_call", target="model",
            input_value="prompt", output_value="answer",
            outcome="success", duration_ms=100,
            metadata={"cost": 0.001},
        ))
        store.log_step(tid, TrajectoryStep(
            step_index=1, action="llm_call", target="model",
            input_value="prompt2", output_value="answer2",
            outcome="success", duration_ms=200,
            metadata={"cost": 0.002},
        ))
        traj = store.complete(
            tid, final_outcome="success", final_score=8.0,
            total_duration_ms=300, total_cost=0.003,
        )
        assert traj.total_duration_ms == 300
        assert traj.total_cost == 0.003

    def test_signal_linkage(self, store, db_path):
        from shared.optimization._signals import SignalBus, LearningSignal
        bus = SignalBus(db_path=db_path)
        sig = LearningSignal(
            signal_type="correction",
            source_loop="correction_capture",
            domain="workday",
            agent_name="form_filler",
            severity="warning",
            payload={"field": "salary"},
            session_id="sess_link",
        )
        bus.emit(sig)
        tid = store.start(
            pipeline="job_application", domain="workday",
            agent_name="form_filler", session_id="sess_link",
        )
        store.log_step(tid, TrajectoryStep(
            step_index=0, action="fill_field", target="salary",
            input_value="45,000", output_value="45000",
            outcome="corrected", duration_ms=150,
            metadata={"signal_id": sig.signal_id},
        ))
        traj = store.complete(tid, final_outcome="success", final_score=8.0)
        assert traj.steps[0].metadata["signal_id"] == sig.signal_id
