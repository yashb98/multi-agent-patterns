import json

from shared.optimization import (
    TrajectoryStep,
    TrajectoryStore,
    assert_replay_fixture_matches,
    diff_replay_fixture,
    load_replay_fixture,
    write_replay_fixture,
)


def _seed_store(store: TrajectoryStore, total: int = 25) -> None:
    for index in range(total):
        tid = store.start(
            pipeline="job_application" if index % 2 == 0 else "research",
            domain=f"domain_{index % 3}",
            agent_name="agent",
            session_id=f"sess_{index:02d}",
        )
        store.log_step(
            tid,
            TrajectoryStep(
                step_index=0,
                action="act",
                target=f"target_{index}",
                input_value=f"input_{index}",
                output_value=f"output_{index}",
                outcome="success",
                duration_ms=10 + index,
                metadata={"rank": index},
            ),
        )
        store.complete(
            tid,
            final_outcome="success" if index % 4 else "failure",
            final_score=float(index),
            total_duration_ms=100 + index,
            total_cost=0.001 * index,
        )


def test_write_replay_fixture_records_top_20_runs(tmp_path):
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    _seed_store(store)

    fixture_path = tmp_path / "top20.json"
    fixtures = write_replay_fixture(store, fixture_path)

    assert fixture_path.exists()
    assert len(fixtures) == 20
    assert fixtures[0].final_score == 24.0
    assert fixtures[-1].final_score == 5.0


def test_replay_fixture_diff_is_empty_for_stable_fixture(tmp_path):
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    _seed_store(store)

    fixture_path = tmp_path / "top20.json"
    write_replay_fixture(store, fixture_path)

    assert diff_replay_fixture(fixture_path) == ""
    assert_replay_fixture_matches(fixture_path)


def test_replay_fixture_diff_surfaces_digest_changes(tmp_path):
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    _seed_store(store)

    fixture_path = tmp_path / "top20.json"
    write_replay_fixture(store, fixture_path)

    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload[0]["expected_digest"] = payload[0]["expected_digest"].replace("steps:", "steps changed:")
    fixture_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    diff = diff_replay_fixture(fixture_path)
    assert "expected" in diff
    assert "actual" in diff
    assert "steps changed:" in diff


def test_load_replay_fixture_round_trips(tmp_path):
    store = TrajectoryStore(db_path=str(tmp_path / "traj.db"))
    _seed_store(store, total=20)

    fixture_path = tmp_path / "top20.json"
    write_replay_fixture(store, fixture_path)

    fixtures = load_replay_fixture(fixture_path)
    assert len(fixtures) == 20
    assert fixtures[0].steps
