"""Tests for jobpulse/job_autopilot.py — real data, no mocks."""

import json
import pytest
from datetime import date
from pathlib import Path


class TestDetermineMatchTier:
    def test_auto_tier(self):
        from jobpulse.job_autopilot import determine_match_tier

        assert determine_match_tier(95.0) == "auto"
        assert determine_match_tier(90.0) == "auto"

    def test_review_tier(self):
        from jobpulse.job_autopilot import determine_match_tier

        assert determine_match_tier(85.0) == "review"
        assert determine_match_tier(82.0) == "review"

    def test_skip_tier(self):
        from jobpulse.job_autopilot import determine_match_tier

        assert determine_match_tier(81.9) == "skip"
        assert determine_match_tier(50.0) == "skip"
        assert determine_match_tier(0.0) == "skip"

    def test_boundary_90(self):
        from jobpulse.job_autopilot import determine_match_tier

        assert determine_match_tier(90.0) == "auto"
        assert determine_match_tier(89.9) == "review"

    def test_boundary_82(self):
        from jobpulse.job_autopilot import determine_match_tier

        assert determine_match_tier(82.0) == "review"
        assert determine_match_tier(81.9) == "skip"


class TestParseJobApplyNextCli:
    def test_default_args(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli

        idx, found_on = parse_job_apply_next_cli([])
        assert idx == "1"
        assert found_on is None

    def test_with_index(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli

        idx, _ = parse_job_apply_next_cli(["runner", "job-apply-next", "5"])
        assert idx == "5"

    def test_with_date(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli

        _, found_on = parse_job_apply_next_cli(["runner", "job-apply-next", "2026-04-30"])
        assert found_on == date(2026, 4, 30)

    def test_with_index_and_date(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli

        idx, found_on = parse_job_apply_next_cli(
            ["runner", "job-apply-next", "3", "2026-04-30"]
        )
        assert idx == "3"
        assert found_on == date(2026, 4, 30)

    def test_short_argv(self):
        from jobpulse.job_autopilot import parse_job_apply_next_cli

        idx, found_on = parse_job_apply_next_cli(["runner"])
        assert idx == "1"
        assert found_on is None


class TestPendingJobQueue:
    def test_save_and_load_pending(self, tmp_path, monkeypatch):
        import jobpulse.job_autopilot as mod

        pending_file = tmp_path / "pending_review_jobs.json"
        monkeypatch.setattr(mod, "PENDING_REVIEW_FILE", pending_file)

        jobs = [{"job_id": "j1", "title": "Data Analyst", "company": "Acme"}]
        mod._save_pending(jobs)

        loaded = mod._load_pending()
        assert len(loaded) == 1
        assert loaded[0]["title"] == "Data Analyst"

    def test_load_missing_file(self, tmp_path, monkeypatch):
        import jobpulse.job_autopilot as mod

        monkeypatch.setattr(mod, "PENDING_REVIEW_FILE", tmp_path / "nonexistent.json")
        assert mod._load_pending() == []

    def test_load_corrupt_file(self, tmp_path, monkeypatch):
        import jobpulse.job_autopilot as mod

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json{{{", encoding="utf-8")
        monkeypatch.setattr(mod, "PENDING_REVIEW_FILE", bad_file)
        assert mod._load_pending() == []

    def test_pending_from_db_rows(self):
        from jobpulse.job_autopilot import _pending_jobs_dicts_from_db_rows

        rows = [
            {
                "job_id": "j1",
                "title": "Engineer",
                "company": "X Corp",
                "platform": "linkedin",
                "location": "London",
                "ats_score": 85.123,
                "updated_at": "2026-04-30",
                "created_at": "2026-04-29",
            }
        ]
        result = _pending_jobs_dicts_from_db_rows(rows)
        assert len(result) == 1
        assert result[0]["job_id"] == "j1"
        assert result[0]["ats_score"] == 85.1

    def test_pending_from_db_rows_sorted_desc(self):
        from jobpulse.job_autopilot import _pending_jobs_dicts_from_db_rows

        rows = [
            {"job_id": "old", "title": "A", "company": "A", "updated_at": "2026-04-01"},
            {"job_id": "new", "title": "B", "company": "B", "updated_at": "2026-04-30"},
        ]
        result = _pending_jobs_dicts_from_db_rows(rows)
        assert result[0]["job_id"] == "new"


class TestPauseControl:
    def test_pause_and_unpause(self, tmp_path, monkeypatch):
        import jobpulse.job_autopilot as mod

        pause_file = tmp_path / "autopilot_paused.txt"
        monkeypatch.setattr(mod, "PAUSE_FILE", pause_file)

        assert mod.is_paused() is False
        mod.set_autopilot_paused(True)
        assert mod.is_paused() is True
        assert pause_file.exists()
        mod.set_autopilot_paused(False)
        assert mod.is_paused() is False
        assert not pause_file.exists()

    def test_unpause_when_not_paused(self, tmp_path, monkeypatch):
        import jobpulse.job_autopilot as mod

        monkeypatch.setattr(mod, "PAUSE_FILE", tmp_path / "paused.txt")
        mod.set_autopilot_paused(False)
        assert mod.is_paused() is False
