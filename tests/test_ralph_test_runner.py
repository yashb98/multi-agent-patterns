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


class TestRalphLiveTest:
    def test_scrapes_and_tests_each_url(self, tmp_path):
        """ralph_live_test scrapes fresh URLs and runs each through ralph_test_run."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        fake_jobs = [
            {"url": "https://linkedin.com/jobs/view/111", "platform": "linkedin", "title": "ML Engineer"},
            {"url": "https://uk.indeed.com/viewjob?jk=abc", "platform": "indeed", "title": "Data Scientist"},
            {"url": "https://www.reed.co.uk/jobs/analyst/222", "platform": "reed", "title": "Analyst"},
        ]

        mock_result = TestRunResult(
            run_id=1, platform="linkedin", url="https://linkedin.com/jobs/view/111",
            verdict="success", iterations=1, duration_ms=500,
        )

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=fake_jobs) as mock_scan, \
             patch("jobpulse.ralph_loop.test_runner.ralph_test_run", return_value=mock_result) as mock_run:
            results = ralph_live_test(
                platforms=["linkedin", "indeed", "reed"],
                count=3,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )

        mock_scan.assert_called_once_with(["linkedin", "indeed", "reed"])
        assert mock_run.call_count == 3
        assert len(results) == 3

    def test_round_robin_platform_diversity(self, tmp_path):
        """With count=2, picks 1 from each platform rather than 2 from the first."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        fake_jobs = [
            {"url": "https://linkedin.com/jobs/view/1", "platform": "linkedin", "title": "A"},
            {"url": "https://linkedin.com/jobs/view/2", "platform": "linkedin", "title": "B"},
            {"url": "https://uk.indeed.com/viewjob?jk=x", "platform": "indeed", "title": "C"},
        ]

        mock_result = TestRunResult(run_id=1, platform="test", url="x", verdict="success", iterations=1, duration_ms=100)

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=fake_jobs), \
             patch("jobpulse.ralph_loop.test_runner.ralph_test_run", return_value=mock_result) as mock_run:
            results = ralph_live_test(
                platforms=["linkedin", "indeed"],
                count=2,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )

        urls_tested = [c.kwargs["url"] for c in mock_run.call_args_list]
        platforms_tested = set()
        for u in urls_tested:
            if "linkedin" in u:
                platforms_tested.add("linkedin")
            elif "indeed" in u:
                platforms_tested.add("indeed")
        assert len(platforms_tested) == 2

    def test_no_jobs_found_returns_empty(self, tmp_path):
        """When scanners return nothing, ralph_live_test returns empty list."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=[]):
            results = ralph_live_test(
                platforms=["linkedin"],
                count=3,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )
        assert results == []
