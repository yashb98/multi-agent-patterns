"""Tests for executor verification primitives.

The fill-readback / retry / verification logic in NavigationActionExecutor
operates against a real Playwright Page. Tests that mocked the page surface
(`mock_page = AsyncMock()`) were Category B mocks per project policy and
were removed 2026-05-03 — that behavior is exercised end-to-end by
`tests/jobpulse/integration/test_pipeline_live.py` against a real Chrome
via CDP.

What remains: pure-function tests on the `ExecutorResult` dataclass and
real-DB verification of `emit_fill_failures` against a real
OptimizationEngine on tmp_path.
"""
import sqlite3
import pytest

from jobpulse.navigation.action_executor import (
    ExecutorResult,
    emit_fill_failures,
)


# ---------------------------------------------------------------------------
# ExecutorResult — pure dataclass over real Python data
# ---------------------------------------------------------------------------


class TestExecutorResultShape:
    def test_default_result_is_empty(self):
        r = ExecutorResult()
        assert r.fills_attempted == 0
        assert r.fills_verified == 0
        assert r.fills_failed == []
        assert r.clicks_attempted == 0
        assert r.advance_clicked is False

    def test_result_records_failures(self):
        r = ExecutorResult()
        r.record_fill_failure("Email", expected="a@b.com", actual="")
        assert r.fills_failed == [{"label": "Email", "expected": "a@b.com", "actual": ""}]

    def test_has_failures_reflects_fill_failures(self):
        r = ExecutorResult()
        assert r.has_failures is False
        r.record_fill_failure("Name", expected="Alice", actual="")
        assert r.has_failures is True


# ---------------------------------------------------------------------------
# Failure-signal emission against a real OptimizationEngine on tmp_path
# ---------------------------------------------------------------------------


class TestFailureSignalEmission:
    def test_emit_writes_real_signal_row(self, tmp_path, monkeypatch):
        """emit_fill_failures must write a real signal into the optimization DB."""
        from shared.optimization._engine import OptimizationEngine

        # Real OptimizationEngine on a tmp_path SQLite (no Fake/Mock).
        real_engine = OptimizationEngine(db_path=str(tmp_path / "opt.db"))
        monkeypatch.setattr(
            "shared.optimization.get_optimization_engine",
            lambda: real_engine,
        )

        result = ExecutorResult()
        result.record_fill_failure("Email", "a@b.com", "")
        emit_fill_failures(result, domain="example.com", source="executor_test")

        # Verify the signal landed in the real signals table.
        with sqlite3.connect(real_engine._db_path) as conn:
            rows = conn.execute(
                "SELECT signal_type, payload FROM signals "
                "WHERE source_loop = ?", ("executor_test",),
            ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == "failure"
        assert "Email" in rows[0][1]  # payload JSON contains the field label

    def test_emit_no_op_when_no_failures(self, tmp_path, monkeypatch):
        """emit_fill_failures must NOT write a signal if the result has no failures."""
        from shared.optimization._engine import OptimizationEngine

        real_engine = OptimizationEngine(db_path=str(tmp_path / "opt.db"))
        monkeypatch.setattr(
            "shared.optimization.get_optimization_engine",
            lambda: real_engine,
        )

        # Successful result — no failures recorded
        result = ExecutorResult()
        result.fills_verified = 3
        emit_fill_failures(result, domain="example.com", source="executor_test")

        with sqlite3.connect(real_engine._db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM signals WHERE source_loop = ?",
                ("executor_test",),
            ).fetchone()[0]

        assert count == 0
