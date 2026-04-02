"""Integration test: full Ralph Loop dry-run pipeline.

Tests the complete flow: test_runner -> ralph_apply_sync(dry_run=True) -> PatternStore(mode=test) -> TestStore.
All mocked at the apply_job level -- no real Playwright.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jobpulse.ralph_loop.test_runner import ralph_test_run
from jobpulse.ralph_loop.test_store import TestStore


@pytest.fixture
def test_env(tmp_path):
    return {
        "store_db": str(tmp_path / "test_store.db"),
        "pattern_db": str(tmp_path / "patterns.db"),
        "base_dir": tmp_path / "ralph_tests",
    }


class TestFullDryRunPipeline:
    @patch("jobpulse.applicator.apply_job")
    def test_success_flow_records_everything(self, mock_apply, test_env):
        """Full success: apply returns success, test store records run + verdict."""
        mock_apply.return_value = {
            "success": True, "screenshot": None, "error": None,
            "ralph_iterations": 1,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/999",
            store_db_path=test_env["store_db"],
            pattern_db_path=test_env["pattern_db"],
            base_dir=test_env["base_dir"],
        )

        assert result.verdict == "success"
        assert result.run_id is not None

        store = TestStore(db_path=test_env["store_db"], base_dir=test_env["base_dir"])
        run = store.get_run(result.run_id)
        assert run is not None
        assert run["final_verdict"] == "success"
        assert run["platform"] == "linkedin"

        # Verify dry_run was passed
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is True

    @patch("jobpulse.applicator.apply_job")
    def test_verification_wall_returns_blocked(self, mock_apply, test_env):
        mock_apply.return_value = {
            "success": False, "screenshot": None,
            "error": "Cloudflare verification wall detected",
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/777",
            store_db_path=test_env["store_db"],
            pattern_db_path=test_env["pattern_db"],
            base_dir=test_env["base_dir"],
        )

        assert result.verdict == "blocked"

    @patch("jobpulse.applicator.apply_job")
    def test_exhausted_returns_partial(self, mock_apply, test_env):
        mock_apply.return_value = {
            "success": False, "screenshot": None,
            "error": "Field not found after retries",
            "ralph_iterations": 5, "ralph_exhausted": True,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/555",
            store_db_path=test_env["store_db"],
            pattern_db_path=test_env["pattern_db"],
            base_dir=test_env["base_dir"],
        )

        assert result.verdict == "partial"

    @patch("jobpulse.applicator.apply_job")
    def test_summary_json_written(self, mock_apply, test_env):
        mock_apply.return_value = {
            "success": True, "screenshot": None, "error": None,
            "ralph_iterations": 1,
        }

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/666",
            store_db_path=test_env["store_db"],
            pattern_db_path=test_env["pattern_db"],
            base_dir=test_env["base_dir"],
        )

        if result.screenshot_dir:
            summary_path = Path(result.screenshot_dir) / "summary.json"
            assert summary_path.exists()
            data = json.loads(summary_path.read_text())
            assert data["verdict"] == "success"

    @patch("jobpulse.applicator.apply_job")
    def test_exception_returns_error_verdict(self, mock_apply, test_env):
        mock_apply.side_effect = RuntimeError("Browser crashed")

        result = ralph_test_run(
            platform="linkedin",
            url="https://linkedin.com/jobs/view/crash",
            store_db_path=test_env["store_db"],
            pattern_db_path=test_env["pattern_db"],
            base_dir=test_env["base_dir"],
        )

        assert result.verdict == "error"
        assert "Browser crashed" in (result.error_summary or "")
