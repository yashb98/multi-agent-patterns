"""Trajectory replay harness — snapshot top runs and diff them deterministically.

The goal is not to re-run live browser/LLM work in CI. Instead we record
high-signal trajectories once, store their normalized structure as fixtures,
and replay those fixtures into a stable digest on every PR. If the digest
changes, the harness returns a unified diff showing exactly what moved.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from shared.optimization._trajectory import Trajectory, TrajectoryStep, TrajectoryStore


@dataclass
class ReplayFixture:
    trajectory_id: str
    pipeline: str
    domain: str
    agent_name: str
    session_id: str
    final_outcome: str
    final_score: float
    total_duration_ms: float
    total_cost: float
    timestamp: str
    steps: list[dict]
    expected_digest: str


def _step_to_dict(step: TrajectoryStep) -> dict:
    return {
        "step_index": step.step_index,
        "action": step.action,
        "target": step.target,
        "input_value": step.input_value,
        "output_value": step.output_value,
        "outcome": step.outcome,
        "duration_ms": step.duration_ms,
        "metadata": step.metadata,
    }


def _fixture_to_trajectory(fixture: ReplayFixture) -> Trajectory:
    return Trajectory(
        trajectory_id=fixture.trajectory_id,
        pipeline=fixture.pipeline,
        domain=fixture.domain,
        agent_name=fixture.agent_name,
        session_id=fixture.session_id,
        steps=[TrajectoryStep(**step) for step in fixture.steps],
        final_outcome=fixture.final_outcome,
        final_score=fixture.final_score,
        total_duration_ms=fixture.total_duration_ms,
        total_cost=fixture.total_cost,
        timestamp=fixture.timestamp,
    )


def render_replay_digest(trajectory: Trajectory) -> str:
    """Render a deterministic human-readable digest for diff-friendly replay."""
    lines = [
        f"trajectory_id={trajectory.trajectory_id}",
        f"pipeline={trajectory.pipeline}",
        f"domain={trajectory.domain}",
        f"agent={trajectory.agent_name}",
        f"session={trajectory.session_id}",
        (
            f"outcome={trajectory.final_outcome} "
            f"score={trajectory.final_score:.3f} "
            f"duration_ms={trajectory.total_duration_ms:.1f} "
            f"cost={trajectory.total_cost:.6f}"
        ),
        "steps:",
    ]
    for step in sorted(trajectory.steps, key=lambda s: s.step_index):
        metadata_keys = ",".join(sorted(step.metadata.keys()))
        lines.append(
            (
                f"  {step.step_index:02d} | {step.action} | {step.target} | {step.outcome} "
                f"| in={step.input_value[:60]!r} | out={step.output_value[:60]!r} "
                f"| meta=[{metadata_keys}]"
            )
        )
    return "\n".join(lines)


def _trajectory_sort_key(traj: Trajectory) -> tuple:
    return (
        -float(traj.final_score),
        0 if traj.final_outcome == "success" else 1,
        float(traj.total_cost),
        float(traj.total_duration_ms),
        traj.timestamp,
    )


def select_top_trajectories(
    trajectories: Iterable[Trajectory],
    limit: int = 20,
) -> list[Trajectory]:
    ranked = sorted(list(trajectories), key=_trajectory_sort_key)
    return ranked[:limit]


def build_replay_fixtures(
    trajectories: Iterable[Trajectory],
    limit: int = 20,
) -> list[ReplayFixture]:
    fixtures: list[ReplayFixture] = []
    for traj in select_top_trajectories(trajectories, limit=limit):
        fixtures.append(
            ReplayFixture(
                trajectory_id=traj.trajectory_id,
                pipeline=traj.pipeline,
                domain=traj.domain,
                agent_name=traj.agent_name,
                session_id=traj.session_id,
                final_outcome=traj.final_outcome,
                final_score=traj.final_score,
                total_duration_ms=traj.total_duration_ms,
                total_cost=traj.total_cost,
                timestamp=traj.timestamp,
                steps=[_step_to_dict(step) for step in traj.steps],
                expected_digest=render_replay_digest(traj),
            )
        )
    return fixtures


def write_replay_fixture(
    store: TrajectoryStore,
    output_path: str | Path,
    *,
    limit: int = 20,
    domain: str = "",
    pipeline: str = "",
) -> list[ReplayFixture]:
    fixtures = build_replay_fixtures(
        store.query(domain=domain, pipeline=pipeline, limit=max(limit * 5, 100)),
        limit=limit,
    )
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([asdict(item) for item in fixtures], indent=2),
        encoding="utf-8",
    )
    return fixtures


def load_replay_fixture(path: str | Path) -> list[ReplayFixture]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [ReplayFixture(**item) for item in raw]


def diff_replay_fixture(path: str | Path) -> str:
    """Replay a stored fixture and diff computed digests against stored ones."""
    diffs: list[str] = []
    for fixture in load_replay_fixture(path):
        trajectory = _fixture_to_trajectory(fixture)
        actual = render_replay_digest(trajectory)
        if actual == fixture.expected_digest:
            continue
        diffs.extend(
            difflib.unified_diff(
                fixture.expected_digest.splitlines(),
                actual.splitlines(),
                fromfile=f"{fixture.trajectory_id}:expected",
                tofile=f"{fixture.trajectory_id}:actual",
                lineterm="",
            )
        )
    return "\n".join(diffs)


def assert_replay_fixture_matches(path: str | Path) -> None:
    diff = diff_replay_fixture(path)
    if diff:
        raise AssertionError(f"Trajectory replay fixture mismatch:\n{diff}")
