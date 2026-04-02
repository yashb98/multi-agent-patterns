"""Tests for Ralph Loop test result storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from jobpulse.ralph_loop.test_store import TestStore


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    return str(tmp_path / "ralph_test_store.db")


@pytest.fixture
def store(db_path: str, tmp_path: Path) -> TestStore:
    return TestStore(db_path=db_path, base_dir=tmp_path / "ralph_tests")


class TestTestStoreRuns:
    def test_create_run(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/123")
        assert run_id > 0

    def test_complete_run(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/123")
        store.complete_run(
            run_id=run_id, iterations=3,
            fixes_applied=["fix1", "fix2"], fixes_skipped=["fix3"],
            fields_filled=12, fields_failed=0, verdict="success",
        )
        run = store.get_run(run_id)
        assert run["iterations"] == 3
        assert run["final_verdict"] == "success"
        assert json.loads(run["fixes_applied"]) == ["fix1", "fix2"]

    def test_get_recent_runs(self, store):
        store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/1")
        store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/2")
        runs = store.get_recent_runs(platform="linkedin", limit=10)
        assert len(runs) == 2


class TestTestStoreIterations:
    def test_record_iteration(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/123")
        store.record_iteration(
            run_id=run_id, iteration=1,
            screenshot_bytes=b"fake png data",
            diagnosis="Location typeahead failed",
            fix_type="selector_override",
            fix_detail={"original_selector": "a", "new_selector": "b"},
            duration_ms=1200,
        )
        iters = store.get_iterations(run_id)
        assert len(iters) == 1
        assert iters[0]["iteration"] == 1
        assert iters[0]["diagnosis"] == "Location typeahead failed"
        assert Path(iters[0]["screenshot_path"]).name == "iter_1.png"

    def test_screenshot_file_created(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/123")
        store.record_iteration(
            run_id=run_id, iteration=0,
            screenshot_bytes=b"PNG_DATA",
            diagnosis=None, fix_type=None, fix_detail=None, duration_ms=500,
        )
        iters = store.get_iterations(run_id)
        assert Path(iters[0]["screenshot_path"]).exists()


class TestTestStoreCleanup:
    def test_prune_old_runs(self, store):
        old_date = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
        conn = sqlite3.connect(store.db_path)
        conn.execute(
            """INSERT INTO ralph_test_runs
               (platform, url, started_at, screenshot_dir, dry_run)
               VALUES (?, ?, ?, ?, ?)""",
            ("linkedin", "https://old.com", old_date, "/tmp/old", 1),
        )
        conn.commit()
        conn.close()

        pruned = store.prune_old_runs(max_age_days=90)
        assert pruned >= 1


class TestTestStoreSummary:
    def test_get_summary_json(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/123")
        store.record_iteration(
            run_id=run_id, iteration=0,
            screenshot_bytes=b"PNG", diagnosis="test", fix_type="selector_override",
            fix_detail={"a": "b"}, duration_ms=100,
        )
        store.complete_run(
            run_id=run_id, iterations=1,
            fixes_applied=["f1"], fixes_skipped=[],
            fields_filled=5, fields_failed=1, verdict="partial",
        )
        summary = store.get_summary(run_id)
        assert summary["verdict"] == "partial"
        assert summary["iterations"] == 1
        assert len(summary["iteration_details"]) == 1

    def test_write_summary_json(self, store):
        run_id = store.create_run(platform="linkedin", url="https://linkedin.com/jobs/view/456")
        store.complete_run(
            run_id=run_id, iterations=0,
            fixes_applied=[], fixes_skipped=[],
            fields_filled=0, fields_failed=0, verdict="error",
        )
        path = store.write_summary_json(run_id)
        assert path is not None
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["verdict"] == "error"
