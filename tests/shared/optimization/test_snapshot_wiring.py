"""Tests proving optimize() calls snapshot() to populate performance_snapshots."""
import sqlite3

import pytest

from shared.optimization._engine import OptimizationEngine


@pytest.fixture
def opt_engine(tmp_path):
    db_path = str(tmp_path / "optimization.db")
    return OptimizationEngine(db_path=db_path)


def test_optimize_creates_snapshot(opt_engine):
    """optimize() must create at least one performance_snapshot per cycle."""
    opt_engine.emit("success", "form_experience", "greenhouse.io",
                    agent_name="form_filler", payload={"fields": 5})
    opt_engine.emit("correction", "correction_capture", "greenhouse.io",
                    agent_name="form_filler", payload={"field": "salary"})

    opt_engine.optimize()

    conn = sqlite3.connect(opt_engine._db_path)
    conn.row_factory = sqlite3.Row
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM performance_snapshots"
    ).fetchone()["cnt"]
    conn.close()
    assert count >= 1, "optimize() must call snapshot() to record performance_snapshots"


def test_snapshot_exposed_on_facade(opt_engine):
    """OptimizationEngine must expose snapshot() as a public method that works."""
    snap = opt_engine.snapshot("test_loop", "test_domain", {"metric_a": 1.0})
    assert snap is not None, "snapshot() must return a PerformanceSnapshot, not None"
    assert snap.loop_name == "test_loop"
    assert snap.domain == "test_domain"
