"""TestStore — SQLite storage for Ralph Loop dry-run test results.

Stores test run metadata + per-iteration screenshots/diagnoses.
File layout: {base_dir}/{platform}/{YYYY-MM-DD_HHMMSS}/iter_N.png
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB_PATH = str(DATA_DIR / "scan_learning.db")
_DEFAULT_BASE_DIR = DATA_DIR / "ralph_tests"


class TestStore:
    """SQLite + filesystem store for Ralph Loop test results."""

    def __init__(
        self,
        db_path: str | None = None,
        base_dir: Path | None = None,
    ) -> None:
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.base_dir = base_dir or _DEFAULT_BASE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ralph_test_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                url TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                iterations INTEGER DEFAULT 0,
                fixes_applied TEXT,
                fixes_skipped TEXT,
                fields_filled INTEGER DEFAULT 0,
                fields_failed INTEGER DEFAULT 0,
                final_verdict TEXT,
                error_summary TEXT,
                screenshot_dir TEXT,
                dry_run BOOLEAN DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS ralph_test_iterations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES ralph_test_runs(id),
                iteration INTEGER NOT NULL,
                screenshot_path TEXT,
                diagnosis TEXT,
                fix_type TEXT,
                fix_detail TEXT,
                duration_ms INTEGER
            );
            """
        )
        conn.close()

    def create_run(self, platform: str, url: str) -> int:
        """Create a new test run. Returns run_id."""
        now_iso = datetime.now(timezone.utc).isoformat()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        screenshot_dir = self.base_dir / platform / timestamp
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """INSERT INTO ralph_test_runs (platform, url, started_at, screenshot_dir, dry_run)
               VALUES (?, ?, ?, ?, 1)""",
            (platform, url, now_iso, str(screenshot_dir)),
        )
        run_id = cursor.lastrowid
        conn.commit()
        conn.close()

        logger.info("Created test run %d for %s: %s", run_id, platform, url[:80])
        return run_id

    def complete_run(
        self,
        run_id: int,
        iterations: int,
        fixes_applied: list[str],
        fixes_skipped: list[str],
        fields_filled: int,
        fields_failed: int,
        verdict: str,
        error_summary: str | None = None,
    ) -> None:
        """Mark a test run as complete with results."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """UPDATE ralph_test_runs SET
                completed_at = ?, iterations = ?,
                fixes_applied = ?, fixes_skipped = ?,
                fields_filled = ?, fields_failed = ?,
                final_verdict = ?, error_summary = ?
               WHERE id = ?""",
            (now_iso, iterations, json.dumps(fixes_applied),
             json.dumps(fixes_skipped), fields_filled, fields_failed,
             verdict, error_summary, run_id),
        )
        conn.commit()
        conn.close()

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        """Get a test run by ID."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM ralph_test_runs WHERE id = ?", (run_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_recent_runs(
        self, platform: str | None = None, limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent test runs, optionally filtered by platform."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if platform:
            rows = conn.execute(
                "SELECT * FROM ralph_test_runs WHERE platform = ? ORDER BY id DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ralph_test_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def record_iteration(
        self,
        run_id: int,
        iteration: int,
        screenshot_bytes: bytes | None,
        diagnosis: str | None,
        fix_type: str | None,
        fix_detail: dict | None,
        duration_ms: int,
    ) -> None:
        """Record a single iteration with optional screenshot."""
        screenshot_path = ""
        if screenshot_bytes:
            run = self.get_run(run_id)
            if run and run["screenshot_dir"]:
                ss_dir = Path(run["screenshot_dir"])
                ss_dir.mkdir(parents=True, exist_ok=True)
                ss_path = ss_dir / f"iter_{iteration}.png"
                ss_path.write_bytes(screenshot_bytes)
                screenshot_path = str(ss_path)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO ralph_test_iterations
               (run_id, iteration, screenshot_path, diagnosis, fix_type, fix_detail, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, iteration, screenshot_path, diagnosis, fix_type,
             json.dumps(fix_detail) if fix_detail else None, duration_ms),
        )
        conn.commit()
        conn.close()

    def get_iterations(self, run_id: int) -> list[dict[str, Any]]:
        """Get all iterations for a test run."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM ralph_test_iterations WHERE run_id = ? ORDER BY iteration",
            (run_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_summary(self, run_id: int) -> dict[str, Any]:
        """Get a complete summary of a test run with all iterations."""
        run = self.get_run(run_id)
        if not run:
            return {}
        iterations = self.get_iterations(run_id)
        return {
            "run_id": run_id,
            "platform": run["platform"],
            "url": run["url"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "iterations": run["iterations"],
            "verdict": run["final_verdict"],
            "fields_filled": run["fields_filled"],
            "fields_failed": run["fields_failed"],
            "fixes_applied": json.loads(run["fixes_applied"]) if run["fixes_applied"] else [],
            "fixes_skipped": json.loads(run["fixes_skipped"]) if run["fixes_skipped"] else [],
            "screenshot_dir": run["screenshot_dir"],
            "iteration_details": iterations,
        }

    def prune_old_runs(self, max_age_days: int = 90) -> int:
        """Delete test runs older than max_age_days. Removes SQLite rows + screenshot dirs."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        old_runs = conn.execute(
            "SELECT id, screenshot_dir FROM ralph_test_runs WHERE started_at < ?",
            (cutoff,),
        ).fetchall()

        if not old_runs:
            conn.close()
            return 0

        run_ids = [r["id"] for r in old_runs]
        placeholders = ",".join("?" * len(run_ids))
        conn.execute(
            f"DELETE FROM ralph_test_iterations WHERE run_id IN ({placeholders})",
            run_ids,
        )
        conn.execute(
            f"DELETE FROM ralph_test_runs WHERE id IN ({placeholders})",
            run_ids,
        )
        conn.commit()
        conn.close()

        for run in old_runs:
            ss_dir = run["screenshot_dir"]
            if ss_dir and Path(ss_dir).exists():
                shutil.rmtree(ss_dir, ignore_errors=True)

        logger.info("Pruned %d old test runs (older than %d days)", len(run_ids), max_age_days)
        return len(run_ids)

    def write_summary_json(self, run_id: int) -> Path | None:
        """Write a summary.json file to the run's screenshot directory."""
        summary = self.get_summary(run_id)
        if not summary or not summary.get("screenshot_dir"):
            return None
        ss_dir = Path(summary["screenshot_dir"])
        ss_dir.mkdir(parents=True, exist_ok=True)
        summary_path = ss_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        return summary_path
