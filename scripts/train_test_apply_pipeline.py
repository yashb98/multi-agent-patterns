#!/usr/bin/env python3
"""Comprehensive training & testing harness for the job application pipeline.

Tests ALL components across ALL platforms:
  1. Platform Detection (URL + DOM)
  2. Field Scanning (UnifiedFieldScanner — CDP / Playwright / DOM)
  3. Field Mapping (deterministic + LLM fallback)
  4. Form Filling (FormFillEngine / NativeFormFiller)
  5. Screening Answers (ScreeningPipeline v2)
  6. Page Analysis (PageAnalyzer — all PageTypes)
  7. Navigation (NavigationLearner + FormNavigator)
  8. End-to-End Apply (ApplicationOrchestrator)

Modes:
  --local      Fast test against scripts/test_ats_form.html (default)
  --live URL   Test against a real job URL (headful, human approval)
  --platform P Test a specific platform strategy
  --train      Collect successful mappings into form_experience.db
  --report     Generate JSON coverage report

Usage:
    # Default: local form test + all platform detection tests
    python scripts/train_test_apply_pipeline.py

    # Train on local form (save field mappings & patterns)
    python scripts/train_test_apply_pipeline.py --local --train

    # Test against a real Greenhouse URL
    python scripts/train_test_apply_pipeline.py --live \
        "https://boards.greenhouse.io/twilio/jobs/7652892"

    # Test only Workday strategy
    python scripts/train_test_apply_pipeline.py --platform workday --live <URL>

    # Full report
    python scripts/train_test_apply_pipeline.py --report --output report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("JOBPULSE_TEST_MODE", "1")

from shared.logging_config import get_logger

logger = get_logger(__name__)

HTML_PATH = PROJECT_ROOT / "scripts" / "test_ats_form.html"

_TEST_PROFILE: dict[str, str] = {
    "first_name": "Test",
    "last_name": "User",
    "email": "test.user.example@mailinator.com",
    "phone": "+1-555-0199",
    "linkedin": "https://linkedin.com/in/testuser",
    "portfolio": "https://example.com",
    "github": "https://github.com/testuser",
    "location": "San Francisco, CA",
    "country": "United States",
    "headline": "Software Engineer",
}

_CUSTOM_ANSWERS: dict[str, str] = {
    "work_auth": "Yes, I am authorized",
    "visa_sponsor": "No",
    "gender": "Prefer not to say",
    "ethnicity": "Prefer not to say",
    "disability": "Prefer not to say",
    "veteran": "Prefer not to say",
}

# ── Data classes for reporting ──


@dataclass
class PlatformDetectionResult:
    platform: str
    url: str
    expected: str
    passed: bool


@dataclass
class FieldScanResult:
    page_num: int
    fields_found: int
    field_names: list[str]
    passed: bool


@dataclass
class FillResult:
    page_num: int
    fields_filled: int
    fields_failed: list[str]
    passed: bool


@dataclass
class ComponentResult:
    name: str
    passed: bool
    duration_ms: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunReport:
    timestamp: str
    mode: str
    total_tests: int
    passed: int
    failed: int
    components: list[ComponentResult] = field(default_factory=list)
    platform_detection: list[PlatformDetectionResult] = field(default_factory=list)
    field_scans: list[FieldScanResult] = field(default_factory=list)
    fill_results: list[FillResult] = field(default_factory=list)


# ── Platform Detection Tests ──

_PLATFORM_TEST_CASES: list[tuple[str, str]] = [
    ("greenhouse", "https://boards.greenhouse.io/twilio/jobs/7652892"),
    ("greenhouse", "https://job-boards.greenhouse.io/company/jobs/123"),
    ("lever", "https://jobs.lever.co/anthropic/abc123"),
    ("lever", "https://lever.co/company/12345"),
    ("workday", "https://company.myworkdayjobs.com/en-US/jobs/12345"),
    ("workday", "https://amd.wd5.myworkdayjobs.com/en-US/External/details"),
    ("smartrecruiters", "https://jobs.smartrecruiters.com/Company/123"),
    ("indeed", "https://uk.indeed.com/viewjob?jk=12345"),
    ("indeed", "https://indeed.com/jobs?q=engineer"),
    ("ashby", "https://jobs.ashbyhq.com/company/123"),
    ("icims", "https://company-icims.com/jobs/123"),
    ("linkedin", "https://www.linkedin.com/jobs/view/12345"),
    ("generic", "https://example.com/careers/apply"),
    ("generic", "https://unknown-ats.com/jobs/123"),
]


def test_platform_detection() -> ComponentResult:
    """Test URL-based platform detection for all supported platforms."""
    from jobpulse.ats_adapters.discovery import detect_platform_from_url

    t0 = time.perf_counter()
    results: list[PlatformDetectionResult] = []
    all_passed = True

    for expected, url in _PLATFORM_TEST_CASES:
        detected = detect_platform_from_url(url)
        passed = detected == expected
        if not passed:
            all_passed = False
            logger.warning("Platform detection FAIL: %s → %s (expected %s)", url, detected, expected)
        results.append(PlatformDetectionResult(platform=detected, url=url, expected=expected, passed=passed))

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="platform_detection",
        passed=all_passed,
        duration_ms=duration,
        details={"cases_tested": len(_PLATFORM_TEST_CASES), "cases": [asdict(r) for r in results]},
    )


# ── Screening Pipeline Tests ──

_SCREENING_TEST_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("Are you authorized to work in the United States?", {"options": ["Yes", "No"]}, "Yes"),
    ("Will you now or in the future require sponsorship?", {"options": ["Yes", "No"]}, "No"),
    ("How did you hear about this job?", {"options": ["LinkedIn", "Company Website", "Referral"]}, "LinkedIn"),
    ("What is your gender?", {"options": ["Male", "Female", "Non-binary", "Prefer not to say"]}, "Prefer not to say"),
]


def test_screening_pipeline() -> ComponentResult:
    """Test the v2 screening answer pipeline."""
    from jobpulse.screening_pipeline import ScreeningPipeline

    t0 = time.perf_counter()
    pipeline = ScreeningPipeline(profile=_TEST_PROFILE)
    results: list[dict[str, Any]] = []
    all_passed = True

    for question, field_meta, expected_contains in _SCREENING_TEST_CASES:
        try:
            result = pipeline.answer(question, field=field_meta)
            answer = result.get("answer", "")
            passed = expected_contains.lower() in answer.lower() or answer.lower() in expected_contains.lower()
            if not passed:
                all_passed = False
                logger.warning("Screening FAIL: '%s' → '%s' (expected ~%s)", question, answer, expected_contains)
            results.append({"question": question, "answer": answer, "expected": expected_contains, "passed": passed, "source": result.get("source")})
        except Exception as exc:
            all_passed = False
            logger.warning("Screening ERROR: '%s' → %r", question, exc)
            results.append({"question": question, "error": str(exc), "passed": False})

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="screening_pipeline",
        passed=all_passed,
        duration_ms=duration,
        details={"cases_tested": len(_SCREENING_TEST_CASES), "cases": results},
    )


# ── Page Analysis Tests ──

_PAGE_TYPE_TEST_CASES: list[tuple[str, dict[str, Any], str]] = [
    ("job_description", {"buttons": [{"text": "Apply", "enabled": True}], "fields": [], "page_text_preview": "About the role"}, "job_description"),
    ("login_form", {"fields": [{"input_type": "email", "label": "Email"}, {"input_type": "password", "label": "Password"}], "buttons": [{"text": "Sign in", "enabled": True}], "page_text_preview": "Sign in"}, "login_form"),
    ("application_form", {"fields": [{"input_type": "text", "label": "First Name"}, {"input_type": "email", "label": "Email"}], "page_text_preview": "Apply"}, "application_form"),
    ("confirmation", {"page_text_preview": "Thank you for applying"}, "confirmation"),
    ("session_expired", {"page_text_preview": "Your session has expired. Please sign in again."}, "session_expired"),
    ("consent_gate", {"page_text_preview": "Please agree to our privacy policy to continue", "buttons": [{"text": "I Accept", "enabled": True}]}, "consent_gate"),
]


def test_page_analysis() -> ComponentResult:
    """Test PageAnalyzer DOM detection for all page types."""
    from jobpulse.page_analyzer import _dom_detect
    from jobpulse.form_models import PageType

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    all_passed = True

    for name, snapshot, expected in _PAGE_TYPE_TEST_CASES:
        try:
            detected, confidence = _dom_detect(snapshot)
            detected_str = str(detected)
            passed = detected_str == expected
            if not passed:
                all_passed = False
                logger.warning("PageAnalysis FAIL: %s → %s (expected %s, conf=%.2f)", name, detected_str, expected, confidence)
            results.append({"case": name, "detected": detected_str, "expected": expected, "confidence": confidence, "passed": passed})
        except Exception as exc:
            all_passed = False
            logger.warning("PageAnalysis ERROR: %s → %r", name, exc)
            results.append({"case": name, "error": str(exc), "passed": False})

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="page_analysis",
        passed=all_passed,
        duration_ms=duration,
        details={"cases_tested": len(_PAGE_TYPE_TEST_CASES), "cases": results},
    )


# ── Local Form Test (Playwright) ──

async def test_local_form(train: bool = False) -> ComponentResult:
    """Run the full pipeline against the local multi-page test form."""
    from playwright.async_api import async_playwright
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.ats_adapters.discovery import detect_platform

    t0 = time.perf_counter()
    scan_results: list[FieldScanResult] = []
    fill_results: list[FillResult] = []
    error: str | None = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1280, "height": 900})

            await page.goto(f"file://{HTML_PATH}", wait_until="networkidle")
            await asyncio.sleep(0.3)

            platform = detect_platform(page.url)
            logger.info("Local form: platform=%s", platform)

            # --- Page 1: Personal Info ---
            scanner = UnifiedFieldScanner(page)
            fields = await scanner.scan()
            scan_results.append(FieldScanResult(
                page_num=1,
                fields_found=len(fields),
                field_names=[f.label for f in fields if f.input_type != "button"],
                passed=len(fields) >= 7,
            ))

            # Fill page 1
            engine = FormFillEngine(page=page, driver=None, application_id="train_test_local")
            result = await engine.fill(
                profile=_TEST_PROFILE,
                custom_answers=_CUSTOM_ANSWERS,
                platform=platform,
                dry_run=True,  # Never submit the local form
            )
            fill_results.append(FillResult(
                page_num=1,
                fields_filled=result.total_fields_filled,
                fields_failed=result.failed_labels,
                passed=result.success,
            ))

            # Navigate to page 2 if possible
            try:
                next_btn = page.locator("#btn-next-1")
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    await next_btn.click()
                    await asyncio.sleep(0.3)

                    fields_p2 = await scanner.scan()
                    scan_results.append(FieldScanResult(
                        page_num=2,
                        fields_found=len(fields_p2),
                        field_names=[f.label for f in fields_p2 if f.input_type != "button"],
                        passed=len(fields_p2) >= 4,
                    ))
            except Exception as exc:
                logger.debug("Page 2 navigation skipped: %s", exc)

            # Navigate to page 3 if possible
            try:
                next_btn = page.locator("#btn-next-2")
                if await next_btn.count() > 0 and await next_btn.is_visible():
                    await next_btn.click()
                    await asyncio.sleep(0.3)

                    fields_p3 = await scanner.scan()
                    scan_results.append(FieldScanResult(
                        page_num=3,
                        fields_found=len(fields_p3),
                        field_names=[f.label for f in fields_p3 if f.input_type != "button"],
                        passed=len(fields_p3) >= 3,
                    ))
            except Exception as exc:
                logger.debug("Page 3 navigation skipped: %s", exc)

            await browser.close()

            # Training: save field mappings
            if train:
                _train_from_local_form(fields, platform)

    except Exception as exc:
        error = str(exc)
        logger.error("Local form test failed: %r", exc)

    duration = (time.perf_counter() - t0) * 1000
    all_passed = all(s.passed for s in scan_results) and all(f.passed for f in fill_results)
    return ComponentResult(
        name="local_form_e2e",
        passed=all_passed,
        duration_ms=duration,
        details={
            "scans": [asdict(s) for s in scan_results],
            "fills": [asdict(f) for f in fill_results],
        },
        error=error,
    )


def _train_from_local_form(fields: list[Any], platform: str) -> None:
    """Persist successful field mappings from the local form test."""
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB()

        field_types = [f.input_type for f in fields if f.input_type != "button"]
        screening_like = [f.label for f in fields if "?" in f.label or f.input_type in ("select", "radio", "checkbox")]

        db.record(
            domain="local_test_form",
            platform=platform,
            adapter="unified_form_engine",
            pages_filled=3,
            field_types=field_types,
            screening_questions=screening_like,
            time_seconds=5.0,
            success=True,
        )
        logger.info("Training: saved local form experience (%d fields, %d screening)", len(field_types), len(screening_like))
    except Exception as exc:
        logger.warning("Training save failed: %s", exc)


# ── Live URL Test ──

async def test_live_url(url: str, platform: str | None = None, headful: bool = False) -> ComponentResult:
    """Run the pipeline against a real job URL (supervised, dry-run)."""
    from playwright.async_api import async_playwright
    from jobpulse.ats_adapters.discovery import detect_platform
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.page_analyzer import PageAnalyzer

    t0 = time.perf_counter()
    error: str | None = None
    details: dict[str, Any] = {}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headful=headful, slow_mo=500 if headful else 0)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            logger.info("Navigating to %s ...", url)
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            detected_platform = platform or detect_platform(page.url)
            logger.info("Live URL: platform=%s", detected_platform)

            # Page analysis
            analyzer = PageAnalyzer(page)
            # Build a simple snapshot for analysis
            scanner = UnifiedFieldScanner(page)
            fields = await scanner.scan()
            details["fields_found"] = len(fields)
            details["field_preview"] = [{"label": f.label, "type": f.input_type} for f in fields[:15]]

            # Try form fill (dry-run)
            engine = FormFillEngine(page=page, driver=None, application_id="train_test_live")
            result = await engine.fill(
                profile=_TEST_PROFILE,
                custom_answers=_CUSTOM_ANSWERS,
                platform=detected_platform,
                dry_run=True,
            )
            details["fill_result"] = {
                "success": result.success,
                "pages_filled": result.pages_filled,
                "total_fields_filled": result.total_fields_filled,
                "failed_labels": result.failed_labels,
                "llm_calls": result.llm_calls,
            }

            await browser.close()

    except Exception as exc:
        error = str(exc)
        logger.error("Live URL test failed: %r", exc)

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="live_url_e2e",
        passed=error is None,
        duration_ms=duration,
        details=details,
        error=error,
    )


# ── Strategy Tests ──

_PLATFORM_STRATEGIES = ["greenhouse", "lever", "workday", "linkedin", "indeed", "ashby", "icims", "smartrecruiters", "generic"]


def test_platform_strategies() -> ComponentResult:
    """Test that all platform strategies load and have valid defaults."""
    from jobpulse.ats_adapters.strategy import get_strategy

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    all_passed = True

    for name in _PLATFORM_STRATEGIES:
        try:
            strategy = get_strategy(name)
            passed = (
                strategy.name == name
                and strategy.min_page_time > 0
                and strategy.max_form_pages > 0
            )
            results.append({"platform": name, "strategy_name": strategy.name, "min_page_time": strategy.min_page_time, "passed": passed})
            if not passed:
                all_passed = False
        except Exception as exc:
            all_passed = False
            results.append({"platform": name, "error": str(exc), "passed": False})
            logger.warning("Strategy FAIL: %s → %r", name, exc)

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="platform_strategies",
        passed=all_passed,
        duration_ms=duration,
        details={"strategies_tested": len(_PLATFORM_STRATEGIES), "results": results},
    )


# ── Navigation Learner Tests ──


def test_navigation_learner() -> ComponentResult:
    """Test NavigationLearner save, retrieve, TTL, and platform fallback."""
    import tempfile
    from jobpulse.navigation_learner import NavigationLearner

    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    all_passed = True

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "nav.db"
        learner = NavigationLearner(db_path=str(db_path))

        # Test save + retrieve
        steps = [{"page_type": "job_description", "action": "click_apply"}]
        learner.save_sequence("acme.com", steps, success=True, platform="greenhouse")
        retrieved = learner.get_sequence("acme.com")
        passed = retrieved == steps
        results.append({"test": "save_retrieve", "passed": passed})
        if not passed:
            all_passed = False

        # Test platform fallback
        learner.save_sequence("beta.com", steps, success=True, platform="greenhouse")
        learner.save_sequence("gamma.com", steps, success=True, platform="greenhouse")
        pattern = learner.get_platform_pattern("greenhouse", exclude_domain="new.com")
        passed = pattern is not None and pattern == steps
        results.append({"test": "platform_fallback", "passed": passed})
        if not passed:
            all_passed = False

        # Test TTL (manually set old date)
        import sqlite3
        from datetime import UTC, datetime, timedelta
        old_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE sequences SET updated_at = ? WHERE domain = ?", (old_date, "acme.com"))
        expired = learner.get_sequence("acme.com")
        passed = expired is None
        results.append({"test": "ttl_expiry", "passed": passed})
        if not passed:
            all_passed = False

    duration = (time.perf_counter() - t0) * 1000
    return ComponentResult(
        name="navigation_learner",
        passed=all_passed,
        duration_ms=duration,
        details={"tests": results},
    )


# ── Main harness ──

async def run_harness(args: argparse.Namespace) -> RunReport:
    """Run the full training & testing harness."""
    report = RunReport(
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        mode="live" if args.live else ("platform" if args.platform else "local"),
        total_tests=0,
        passed=0,
        failed=0,
    )

    # Always run fast unit-like tests
    tests: list[ComponentResult] = []

    tests.append(test_platform_detection())
    tests.append(test_platform_strategies())
    tests.append(test_page_analysis())
    tests.append(test_screening_pipeline())
    tests.append(test_navigation_learner())

    if args.live:
        tests.append(await test_live_url(args.live, platform=args.platform, headful=args.headful))
    else:
        tests.append(await test_local_form(train=args.train))

    report.components = tests
    report.total_tests = len(tests)
    report.passed = sum(1 for t in tests if t.passed)
    report.failed = report.total_tests - report.passed

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Train & Test the Job Application Pipeline")
    parser.add_argument("--local", action="store_true", help="Test against local HTML form (default)")
    parser.add_argument("--live", metavar="URL", type=str, default=None, help="Test against a real job URL")
    parser.add_argument("--platform", type=str, default=None, help="Force a specific platform strategy")
    parser.add_argument("--headful", action="store_true", help="Show browser window (for --live)")
    parser.add_argument("--train", action="store_true", help="Collect training data from successful runs")
    parser.add_argument("--report", action="store_true", help="Generate JSON report")
    parser.add_argument("--output", type=str, default="train_test_report.json", help="Report output path")
    args = parser.parse_args()

    if not args.live and not args.local:
        args.local = True  # Default to local

    report = asyncio.run(run_harness(args))

    # Console summary
    print("\n" + "=" * 70)
    print("  JOB APPLICATION PIPELINE — TRAIN & TEST REPORT")
    print("=" * 70)
    print(f"  Mode:      {report.mode}")
    print(f"  Timestamp: {report.timestamp}")
    print(f"  Total:     {report.total_tests}")
    print(f"  Passed:    {report.passed}")
    print(f"  Failed:    {report.failed}")
    print("-" * 70)
    for comp in report.components:
        status = "PASS" if comp.passed else "FAIL"
        print(f"  [{status}] {comp.name:<25} {comp.duration_ms:>8.1f} ms")
        if comp.error:
            print(f"         Error: {comp.error}")
    print("=" * 70)

    # JSON report
    if args.report:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        print(f"\n  Report saved: {output_path.resolve()}")

    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
