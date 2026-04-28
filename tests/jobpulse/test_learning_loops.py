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
