"""Tests for Ralph Loop CLI output formatting."""

from __future__ import annotations

from jobpulse.ralph_loop.cli_output import format_test_result
from jobpulse.ralph_loop.test_runner import TestRunResult


class TestCLIOutput:
    def test_format_success_result(self):
        result = TestRunResult(
            run_id=1, platform="linkedin",
            url="https://linkedin.com/jobs/view/123",
            verdict="success", iterations=2,
            fixes_applied=["f1"], fixes_skipped=[],
            fields_filled=12, fields_failed=0,
            screenshot_dir="/tmp/screenshots",
            duration_ms=5000,
        )
        output = format_test_result(result, iteration_details=[
            {"iteration": 0, "diagnosis": "Location typeahead", "fix_type": "selector_override"},
            {"iteration": 1, "diagnosis": None, "fix_type": None},
        ])
        assert "SUCCESS" in output
        assert "linkedin" in output.lower()
        assert "12" in output

    def test_format_blocked_result(self):
        result = TestRunResult(
            run_id=2, platform="linkedin",
            url="https://linkedin.com/jobs/view/456",
            verdict="blocked", iterations=1,
            fixes_applied=[], fixes_skipped=[],
            fields_filled=0, fields_failed=0,
            screenshot_dir="/tmp/screenshots",
            error_summary="Cloudflare verification detected",
            duration_ms=3000,
        )
        output = format_test_result(result, iteration_details=[])
        assert "BLOCKED" in output

    def test_format_empty_iterations(self):
        result = TestRunResult(
            run_id=3, platform="linkedin",
            url="https://linkedin.com/jobs/view/789",
            verdict="error", iterations=0,
            fixes_applied=[], fixes_skipped=[],
            fields_filled=0, fields_failed=0,
            screenshot_dir="/tmp/screenshots",
            error_summary="Browser launch failed",
            duration_ms=1000,
        )
        output = format_test_result(result, iteration_details=[])
        assert "ERROR" in output

    def test_format_plain_fallback(self):
        """Test plain text output (no Rich dependency needed)."""
        from jobpulse.ralph_loop.cli_output import _format_plain
        result = TestRunResult(
            run_id=4, platform="indeed",
            url="https://indeed.com/job/123",
            verdict="success", iterations=1,
            fixes_applied=[], fixes_skipped=[],
            fields_filled=8, fields_failed=2,
            screenshot_dir="/tmp/ss", duration_ms=2000,
        )
        output = _format_plain(result, [])
        assert "SUCCESS" in output
        assert "Indeed" in output
        assert "8" in output
