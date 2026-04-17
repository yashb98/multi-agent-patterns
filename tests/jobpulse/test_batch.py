"""Tests for batch processing — state tracking and orchestrator."""
import pytest
from pathlib import Path


class TestBatchState:
    def test_create_state_file(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_started("job2")
        assert state.get_status("job1") == "started"
        assert state.get_status("job2") == "started"

    def test_mark_completed(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_completed("job1", score=8.5)
        assert state.get_status("job1") == "completed"

    def test_mark_failed(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_failed("job1", error="timeout")
        assert state.get_status("job1") == "failed"

    def test_get_pending(self, tmp_path):
        from jobpulse.batch.state import BatchState

        state = BatchState(tmp_path / "batch.tsv")
        state.mark_started("job1")
        state.mark_completed("job1", score=8.0)
        state.mark_started("job2")
        state.mark_started("job3")
        state.mark_failed("job3", error="err")
        pending = state.get_pending(["job1", "job2", "job3", "job4"])
        assert "job1" not in pending
        assert "job2" not in pending
        assert "job3" in pending
        assert "job4" in pending

    def test_persistence(self, tmp_path):
        from jobpulse.batch.state import BatchState

        path = tmp_path / "batch.tsv"
        state1 = BatchState(path)
        state1.mark_started("job1")
        state1.mark_completed("job1", score=9.0)

        state2 = BatchState(path)
        assert state2.get_status("job1") == "completed"
