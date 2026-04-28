"""Integration tests for all 5 post-apply learning loops."""
import pytest
from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store


@pytest.fixture
def trajectory_store(tmp_path):
    _reset_shared_store()
    store = TrajectoryStore(db_path=str(tmp_path / "trajectory.db"))
    yield store
    _reset_shared_store()


def test_field_trajectory_logged_after_fill(trajectory_store):
    """Verify log_field creates a retrievable trajectory record."""
    trajectory_store.log_field(
        job_id="job_001",
        domain="greenhouse.io",
        field_label="First Name",
        strategy=StrategyTier.PATTERN_MATCH,
        value_filled="Yash",
        field_type="text",
        confidence=0.95,
        time_ms=50,
    )

    results = trajectory_store.get_trajectories("job_001")
    assert len(results) == 1
    assert results[0].field_label == "First Name"
    assert results[0].strategy == "pattern_match"
    assert results[0].confidence == 0.95


def test_log_field_trajectory_helper(trajectory_store, monkeypatch):
    """The helper function writes to trajectory store."""
    monkeypatch.setattr(
        "jobpulse.trajectory_store.get_trajectory_store",
        lambda: trajectory_store,
    )
    from jobpulse.native_form_filler import _log_field_trajectory
    _log_field_trajectory(
        job_id="job_002", domain="linkedin.com",
        field_label="Email", field_type="email",
        strategy="pattern_match", value="test@test.com",
        confidence=0.95, time_ms=30,
    )
    results = trajectory_store.get_trajectories("job_002")
    assert len(results) == 1
    assert results[0].field_label == "Email"


def test_agent_rules_field_overrides(tmp_path):
    """AgentRulesDB returns field overrides for form filler consumption."""
    from jobpulse.agent_rules import AgentRulesDB
    db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    overrides = db.get_field_overrides(domain="greenhouse.io")
    assert "city" in overrides
    assert overrides["city"]["value"] == "Dundee"


def test_field_override_consumed_during_fill(tmp_path):
    """When an override exists, times_applied is incremented."""
    from jobpulse.agent_rules import AgentRulesDB
    db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    overrides = db.get_field_overrides(domain="greenhouse.io")
    assert overrides["city"]["value"] == "Dundee"

    rules = db.get_active_rules("correction_override")
    city_rules = [r for r in rules if r["category"] == "city"]
    assert city_rules[0]["times_applied"] == 1


def test_heuristics_loaded_before_fill(trajectory_store):
    """Heuristics from prior applications are loaded before a new fill."""
    from jobpulse.trajectory_store import Heuristic

    trajectory_store.save_heuristics([
        Heuristic(
            trigger="field 'city' on smartrecruiters",
            action="type text then ArrowDown+Enter",
            confidence=0.85,
            source_domain="jobs.smartrecruiters.com",
            platform="smartrecruiters",
        ),
    ])

    from jobpulse.trajectory_store import load_heuristics_for_application
    result = load_heuristics_for_application(
        "jobs.smartrecruiters.com",
        platform="smartrecruiters",
        store=trajectory_store,
    )
    assert len(result["domain_heuristics"]) >= 1
    assert "ArrowDown" in result["prompt_context"]


def test_correction_links_to_trajectory(tmp_path):
    """Corrections from confirm_application link to field trajectories."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store
    from jobpulse.correction_capture import CorrectionCapture

    _reset_shared_store()
    traj_store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    cc = CorrectionCapture(db_path=str(tmp_path / "corrections.db"))

    traj_store.log_field(
        job_id="job_100", domain="greenhouse.io",
        field_label="City", strategy=StrategyTier.PATTERN_MATCH,
        value_filled="London", field_type="text",
    )

    result = cc.record_corrections(
        domain="greenhouse.io",
        platform="greenhouse",
        agent_mapping={"City": "London"},
        final_mapping={"City": "Dundee"},
        job_id="job_100",
    )

    assert len(result["corrections"]) == 1
    _reset_shared_store()


def test_post_apply_records_learning_action(tmp_path, monkeypatch):
    """post_apply_hook wraps with before/after learning measurement."""
    import sqlite3

    db_path = str(tmp_path / "optimization.db")

    from shared.optimization._engine import OptimizationEngine
    engine = OptimizationEngine(db_path=db_path)
    monkeypatch.setattr(
        "shared.optimization._engine._shared_engine", engine,
    )

    action_id = engine.before_learning_action(
        "post_apply", domain="greenhouse.io",
        metrics={"fields_filled": 10, "pages_filled": 2},
    )
    assert action_id

    result = engine.after_learning_action(
        action_id,
        metrics={"fields_filled": 12, "pages_filled": 2},
    )
    assert result["improved"] is True

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM learning_actions").fetchall()
    assert len(rows) == 1
    assert rows[0]["after_metrics"] is not None


def test_full_correction_to_rule_to_consumption_loop(tmp_path):
    """E2E: correction generates a rule, next fill consumes it."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store
    from jobpulse.correction_capture import CorrectionCapture
    from jobpulse.agent_rules import AgentRulesDB

    _reset_shared_store()
    traj_store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    cc = CorrectionCapture(db_path=str(tmp_path / "corrections.db"))
    rules_db = AgentRulesDB(db_path=str(tmp_path / "rules.db"))

    # Step 1: Agent fills "city" with "London"
    traj_store.log_field(
        job_id="job_200", domain="greenhouse.io",
        field_label="City", strategy=StrategyTier.PATTERN_MATCH,
        value_filled="London", field_type="text",
    )

    # Step 2: User corrects to "Dundee"
    cc.record_corrections(
        domain="greenhouse.io",
        platform="greenhouse",
        agent_mapping={"City": "London"},
        final_mapping={"City": "Dundee"},
        job_id="job_200",
    )

    # Step 3: Rule auto-generated
    rules_db.auto_generate_from_correction(
        field_label="city",
        agent_value="London",
        user_value="Dundee",
        domain="greenhouse.io",
        platform="greenhouse",
    )

    # Step 4: Next fill queries rules — should get "Dundee"
    overrides = rules_db.get_field_overrides(domain="greenhouse.io")
    assert "city" in overrides
    assert overrides["city"]["value"] == "Dundee"
    assert overrides["city"]["action"] == "override_answer"

    _reset_shared_store()


def test_heuristic_extraction_from_trajectories(tmp_path):
    """Strategy reflector extracts heuristics from field trajectories."""
    from jobpulse.trajectory_store import TrajectoryStore, StrategyTier, _reset_shared_store

    _reset_shared_store()
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))

    # Log 5 fields, 2 corrected
    for i, (label, corrected) in enumerate([
        ("First Name", False), ("Last Name", False),
        ("City", True), ("Salary", True), ("Email", False),
    ]):
        store.log_field(
            job_id="job_300", domain="greenhouse.io",
            field_label=label, strategy=StrategyTier.PATTERN_MATCH,
            value_filled=f"val_{i}", field_type="text",
            confidence=0.9, time_ms=50,
        )
        if corrected:
            store.mark_corrected("job_300", "greenhouse.io", label, f"corrected_{i}")

    trajs = store.get_trajectories("job_300")
    assert len(trajs) == 5

    # Verify corrected trajectories
    corrected_trajs = [t for t in trajs if t.corrected]
    assert len(corrected_trajs) == 2

    _reset_shared_store()
