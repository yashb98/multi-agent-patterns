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
from jobpulse.job_scanner import scan_platforms

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
        logger.error("Ralph test run failed with exception: %r", exc, exc_info=True)
        result = {"success": False, "error": f"{type(exc).__name__}: {exc}"}

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

    # Send results + screenshots to Telegram Jobs bot
    _notify_telegram(test_result, result, screenshot_dir)

    return test_result


def _notify_telegram(
    test_result: TestRunResult,
    raw_result: dict,
    screenshot_dir: str,
) -> None:
    """Send ralph-test verdict + failure screenshots to Telegram Jobs bot."""
    try:
        from jobpulse.telegram_bots import send_jobs, send_jobs_photo
    except Exception:
        return

    verdict_emoji = {
        "success": "\u2705", "partial": "\u26a0\ufe0f",
        "blocked": "\U0001f6ab", "error": "\u274c",
    }
    emoji = verdict_emoji.get(test_result.verdict, "\u2753")

    lines = [
        f"{emoji} Ralph Test: {test_result.verdict.upper()}",
        f"Platform: {test_result.platform}",
        f"URL: {test_result.url}",
        f"Iterations: {test_result.iterations}",
        f"Duration: {test_result.duration_ms / 1000:.1f}s",
    ]
    if test_result.error_summary:
        lines.append(f"Error: {test_result.error_summary[:200]}")
    if test_result.fields_filled:
        lines.append(f"Fields: {test_result.fields_filled} filled, {test_result.fields_failed} failed")

    send_jobs("\n".join(lines))

    # Send screenshots on non-success verdicts for human review
    if test_result.verdict != "success" and screenshot_dir:
        screenshot_path = Path(screenshot_dir)
        # Send the most diagnostic screenshots
        priority_screenshots = [
            "linkedin_03_no_modal.png",
            "linkedin_02_apply_debug.png",
            "linkedin_02_no_apply_button.png",
            "linkedin_02_external_apply.png",
            "linkedin_01_job_page.png",
        ]
        sent = 0
        for name in priority_screenshots:
            img = screenshot_path / name
            if img.exists() and sent < 3:
                send_jobs_photo(str(img), caption=f"Ralph Test [{test_result.platform}] — {name}")
                sent += 1
        # Also send any stuck/error screenshots
        for img in sorted(screenshot_path.glob("linkedin_stuck_*.png")):
            if sent < 4:
                send_jobs_photo(str(img), caption=f"Ralph Test — {img.name}")
                sent += 1
        # Send last iteration screenshot if nothing else was sent
        if sent == 0:
            for img in sorted(screenshot_path.glob("iter_*.png"), reverse=True):
                send_jobs_photo(str(img), caption=f"Ralph Test — {img.name}")
                break


def ralph_live_test(
    platforms: list[str] | None = None,
    count: int = 3,
    max_iterations: int = 5,
    store_db_path: str | None = None,
    pattern_db_path: str | None = None,
    base_dir: Path | None = None,
) -> list[TestRunResult]:
    """Scrape fresh job URLs and test each through Ralph Loop (dry_run=True).

    1. Calls scan_platforms() for fresh URLs
    2. Picks `count` jobs with round-robin platform diversity
    3. Runs each through ralph_test_run(dry_run=True)
    4. Returns list of TestRunResult
    """
    from jobpulse.ext_adapter import _detect_ats_platform
    from jobpulse.config import APPLICATION_ENGINE

    # Start bridge eagerly so the extension can connect during scraping
    if APPLICATION_ENGINE == "extension":
        from jobpulse.ats_adapters import _get_extension_adapter
        logger.info("Starting extension bridge before scraping...")
        _get_extension_adapter()

    jobs = scan_platforms(platforms)
    if not jobs:
        logger.warning("ralph_live_test: no jobs found from scanners")
        return []

    selected = _select_diverse_jobs(jobs, count)
    logger.info("ralph_live_test: selected %d jobs from %d scraped", len(selected), len(jobs))

    results: list[TestRunResult] = []
    for job in selected:
        url = job["url"]
        platform = job.get("platform") or _detect_ats_platform(url)
        logger.info("ralph_live_test: testing %s — %s", platform, url[:60])

        result = ralph_test_run(
            platform=platform,
            url=url,
            max_iterations=max_iterations,
            store_db_path=store_db_path,
            pattern_db_path=pattern_db_path,
            base_dir=base_dir,
        )
        results.append(result)

    return results


def _select_diverse_jobs(jobs: list[dict], count: int) -> list[dict]:
    """Pick up to `count` jobs with round-robin platform diversity."""
    from collections import defaultdict

    by_platform: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        by_platform[job.get("platform", "generic")].append(job)

    selected: list[dict] = []
    seen_urls: set[str] = set()
    platforms = list(by_platform.keys())
    idx = 0

    while len(selected) < count and platforms:
        platform = platforms[idx % len(platforms)]
        bucket = by_platform[platform]
        if bucket:
            job = bucket.pop(0)
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                selected.append(job)
        else:
            platforms.remove(platform)
            if not platforms:
                break
            continue
        idx += 1

    return selected
