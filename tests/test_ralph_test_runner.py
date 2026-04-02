"""Tests for Ralph Loop test runner orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jobpulse.ralph_loop.test_runner import ralph_test_run, TestRunResult


@pytest.fixture
def tmp_paths(tmp_path):
    return {
        "store_db": str(tmp_path / "test_runner.db"),
        "pattern_db": str(tmp_path / "patterns.db"),
        "base_dir": tmp_path / "ralph_tests",
    }


class TestRalphTestRun:
    @patch("jobpulse.ralph_loop.test_runner.ralph_apply_sync")
    def test_basic_success(self, mock_ralph, tmp_paths):
        mock_ralph.return_value = {
            "success": True, "screenshot": None, "error": None,
            "ralph_iterations": 1,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/123",
            store_db_path=tmp_paths["store_db"],
            pattern_db_path=tmp_paths["pattern_db"],
            base_dir=tmp_paths["base_dir"],
        )

        assert isinstance(result, TestRunResult)
        assert result.verdict == "success"
        assert result.run_id is not None
        mock_ralph.assert_called_once()

    @patch("jobpulse.ralph_loop.test_runner.ralph_apply_sync")
    def test_failure_returns_error_verdict(self, mock_ralph, tmp_paths):
        mock_ralph.return_value = {
            "success": False, "screenshot": None,
            "error": "Timeout waiting for modal",
            "ralph_iterations": 5, "ralph_exhausted": True,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/456",
            store_db_path=tmp_paths["store_db"],
            pattern_db_path=tmp_paths["pattern_db"],
            base_dir=tmp_paths["base_dir"],
        )

        assert result.verdict == "partial"

    @patch("jobpulse.ralph_loop.test_runner.ralph_apply_sync")
    def test_blocked_verdict_on_verification_wall(self, mock_ralph, tmp_paths):
        mock_ralph.return_value = {
            "success": False, "screenshot": None,
            "error": "Cloudflare verification detected",
            "ralph_iterations": 1,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/789",
            store_db_path=tmp_paths["store_db"],
            pattern_db_path=tmp_paths["pattern_db"],
            base_dir=tmp_paths["base_dir"],
        )

        assert result.verdict == "blocked"

    @patch("jobpulse.ralph_loop.test_runner.ralph_apply_sync")
    def test_dry_run_always_true(self, mock_ralph, tmp_paths):
        mock_ralph.return_value = {
            "success": True, "screenshot": None, "error": None,
            "ralph_iterations": 1,
        }

        ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/123",
            store_db_path=tmp_paths["store_db"],
            pattern_db_path=tmp_paths["pattern_db"],
            base_dir=tmp_paths["base_dir"],
        )

        call_kwargs = mock_ralph.call_args.kwargs
        assert call_kwargs["dry_run"] is True

    @patch("jobpulse.ralph_loop.test_runner.ralph_apply_sync")
    def test_results_stored_in_sqlite(self, mock_ralph, tmp_paths):
        mock_ralph.return_value = {
            "success": True, "screenshot": None, "error": None,
            "ralph_iterations": 1,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/123",
            store_db_path=tmp_paths["store_db"],
            pattern_db_path=tmp_paths["pattern_db"],
            base_dir=tmp_paths["base_dir"],
        )

        assert result.run_id is not None
        from jobpulse.ralph_loop.test_store import TestStore
        store = TestStore(db_path=tmp_paths["store_db"], base_dir=tmp_paths["base_dir"])
        run = store.get_run(result.run_id)
        assert run is not None
        assert run["final_verdict"] == "success"
