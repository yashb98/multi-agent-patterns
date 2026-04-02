"""Test runner — CLI-callable dry-run harness for Ralph Loop.

Orchestrates: create run -> call ralph_apply_sync(dry_run=True) -> record results -> print summary.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.logging_config import get_logger
from jobpulse.ralph_loop.loop import ralph_apply_sync
from jobpulse.ralph_loop.test_store import TestStore
from jobpulse.ralph_loop.pattern_store import PatternStore

logger = get_logger(__name__)

_VERIFICATION_PATTERNS = re.compile(
    r"captcha|cloudflare|recaptcha|hcaptcha|verify|robot|blocked|403|429",
    re.IGNORECASE,
)


@dataclass
class TestRunResult:
    """Result of a single Ralph Loop test run."""

    run_id: int | None = None
    platform: str = ""
    url: str = ""
    verdict: str = ""  # success | partial | blocked | error
    iterations: int = 0
    fixes_applied: list[str] = field(default_factory=list)
    fixes_skipped: list[str] = field(default_factory=list)
    fields_filled: int = 0
    fields_failed: int = 0
    screenshot_dir: str = ""
    error_summary: str | None = None
    duration_ms: int = 0


def ralph_test_run(
    platform: str,
    url: str,
    max_iterations: int = 5,
    store_db_path: str | None = None,
    pattern_db_path: str | None = None,
    base_dir: Path | None = None,
) -> TestRunResult:
    """Run Ralph Loop in dry-run mode and record structured results.

    Always passes dry_run=True. Never submits. Never decrements daily caps.
    """
    store = TestStore(db_path=store_db_path, base_dir=base_dir)
    pattern_store = PatternStore(db_path=pattern_db_path)

    # Prune stale data
    pattern_store.prune_stale_test_fixes()
    store.prune_old_runs()

    # Create run record
    run_id = store.create_run(platform=platform, url=url)
    run = store.get_run(run_id)
    screenshot_dir = run["screenshot_dir"] if run else ""

    start_time = time.monotonic()
    iteration_data: list[dict] = []

    def iteration_callback(
        iteration: int,
        screenshot_bytes: bytes | None,
        diagnosis: dict | None,
        result: dict | None,
    ) -> None:
        fix_type = diagnosis.get("fix_type") if diagnosis else None
        fix_detail = diagnosis.get("fix_payload") if diagnosis else None
        diag_text = diagnosis.get("diagnosis") if diagnosis else None

        store.record_iteration(
            run_id=run_id,
            iteration=iteration,
            screenshot_bytes=screenshot_bytes,
            diagnosis=diag_text,
            fix_type=fix_type,
            fix_detail=fix_detail,
            duration_ms=0,
        )
        iteration_data.append({
            "iteration": iteration,
            "diagnosis": diag_text,
            "fix_type": fix_type,
        })

    # Build minimal CV path for the test
    cv_dir = Path(screenshot_dir) if screenshot_dir else Path("/tmp/ralph_test")
    cv_dir.mkdir(parents=True, exist_ok=True)
    cv_path = cv_dir / "test_cv.pdf"
    if not cv_path.exists():
        cv_path.write_bytes(b"%PDF-1.4 test")

    # Run Ralph Loop in dry-run mode
    try:
        result = ralph_apply_sync(
            url=url,
            ats_platform=platform,
            cv_path=cv_path,
            dry_run=True,
            db_path=pattern_db_path,
            iteration_callback=iteration_callback,
        )
    except Exception as exc:
        logger.error("Ralph test run failed with exception: %s", exc)
        result = {"success": False, "error": str(exc)}

    elapsed_ms = int((time.monotonic() - start_time) * 1000)

    # Determine verdict
    error_msg = result.get("error", "") or ""
    if result.get("success"):
        verdict = "success"
    elif _VERIFICATION_PATTERNS.search(error_msg):
        verdict = "blocked"
    elif result.get("ralph_exhausted"):
        verdict = "partial"
    else:
        verdict = "error"

    # Complete the run record
    store.complete_run(
        run_id=run_id,
        iterations=result.get("ralph_iterations", len(iteration_data)),
        fixes_applied=[],
        fixes_skipped=[],
        fields_filled=result.get("fields_filled", 0),
        fields_failed=result.get("fields_failed", 0),
        verdict=verdict,
        error_summary=error_msg if error_msg else None,
    )

    # Write summary JSON
    store.write_summary_json(run_id)

    test_result = TestRunResult(
        run_id=run_id,
        platform=platform,
        url=url,
        verdict=verdict,
        iterations=result.get("ralph_iterations", len(iteration_data)),
        fixes_applied=[],
        fixes_skipped=[],
        fields_filled=result.get("fields_filled", 0),
        fields_failed=result.get("fields_failed", 0),
        screenshot_dir=screenshot_dir,
        error_summary=error_msg if error_msg else None,
        duration_ms=elapsed_ms,
    )

    logger.info(
        "Ralph test run %d complete: verdict=%s iterations=%d duration=%dms",
        run_id, verdict, test_result.iterations, elapsed_ms,
    )

    return test_result
