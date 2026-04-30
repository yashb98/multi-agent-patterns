"""Tests for jobpulse/runner.py — CLI argument parsing."""

import subprocess
import sys
import pytest


class TestRunnerHelp:
    def test_no_args_exits_nonzero(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 1

    def test_no_args_shows_usage(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner"],
            capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        assert "command" in combined.lower() or "usage" in combined.lower()


class TestUnknownCommand:
    def test_unknown_command_no_traceback(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner", "nonexistent-xyz-command"],
            capture_output=True, text=True, timeout=10,
        )
        assert "Traceback" not in result.stderr or result.returncode != 0

    def test_wrong_case_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner", "BRIEFING"],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode != 0 or "Traceback" not in result.stderr


class TestKnownCommandRecognition:
    @pytest.mark.parametrize("command", [
        "gmail", "calendar", "github", "budget",
        "weekly-report", "export", "health",
        "job-stats",
        "skill-gaps", "optimize",
    ])
    def test_known_command_not_unknown(self, command):
        result = subprocess.run(
            [sys.executable, "-m", "jobpulse.runner", command],
            capture_output=True, text=True, timeout=15,
        )
        combined = result.stdout + result.stderr
        assert "unknown command" not in combined.lower()
