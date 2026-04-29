"""Live pipeline integration test — runs the REAL application workflow.

This tests the pipeline the way it actually works:
  1. Fetch a job from the local DB (or Notion)
  2. Navigate to the job page (LinkedIn, Indeed, etc.)
  3. Click apply → navigate to the actual ATS form
  4. Run UnifiedFieldScanner + FormFillEngine
  5. Report what happened

Usage:
    # Test the first job in the queue
    RUN_INTEGRATION_TESTS=1 pytest tests/jobpulse/integration/test_pipeline_live.py -v -s

    # Test a specific job by URL
    RUN_INTEGRATION_TESTS=1 pytest tests/jobpulse/integration/test_pipeline_live.py -v -s \
        --job-url "https://uk.linkedin.com/jobs/view/..."
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Set RUN_INTEGRATION_TESTS=1 to run live pipeline tests",
    ),
]

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


@pytest_asyncio.fixture(scope="module")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="module")
async def playwright_page():
    """Launch a real Playwright browser page."""
    pytest.importorskip("playwright", reason="playwright not installed")
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=False,  # HEADFUL so you can watch it work
        slow_mo=500,     # Slow down for human observation
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    yield page

    await context.close()
    await browser.close()
    await playwright.stop()


@pytest.fixture
def job_url(request: Any) -> str | None:
    try:
        return request.config.getoption("--job-url") or None
    except ValueError:
        return None


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--job-url",
        action="store",
        default=None,
        help="Specific job URL to test (default: auto-pick from DB)",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_job_from_db() -> dict[str, str] | None:
    """Pick a recent job from the DB that has a URL."""
    from jobpulse.job_db import JobDB

    db = JobDB()
    # Prefer jobs that have an ATS platform detected, then any recent job
    rows = db._conn.execute(
        """
        SELECT job_id, title, company, url, platform, ats_platform
        FROM job_listings
        WHERE url IS NOT NULL AND url != ''
        ORDER BY found_at DESC
        LIMIT 20
        """
    ).fetchall()

    # Pick first job with a real URL
    for r in rows:
        url = r[3]
        if url and url.startswith("http"):
            return {
                "job_id": r[0],
                "title": r[1],
                "company": r[2],
                "url": url,
                "platform": r[4] or "generic",
                "ats_platform": r[5],
            }
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_navigate_and_detect(
    playwright_page: Any,
    job_url: str | None,
) -> None:
    """Run the REAL pipeline: navigate → click apply → scan form fields.

    This is the full workflow test. It:
      1. Navigates to the job page (LinkedIn/Indeed/etc)
      2. Uses FormNavigator to click apply and reach the form
      3. Runs UnifiedFieldScanner on whatever ATS appears
      4. Reports fields found WITHOUT filling or submitting
    """
    from jobpulse.application_orchestrator_pkg import ApplicationOrchestrator
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.ats_adapters.discovery import detect_platform
    from jobpulse.page_analysis.classifier import PageTypeClassifier

    page = playwright_page

    # ── 1. Get job URL ──
    if job_url:
        job = {"url": job_url, "platform": "generic", "title": "Manual Test", "company": "TestCo"}
    else:
        job = _pick_job_from_db()

    if not job:
        pytest.skip("No jobs in DB — run a job scan first")

    url = job["url"]
    print(f"\n{'='*60}")
    print(f"🚀 PIPELINE TEST: {job['title']} @ {job['company']}")
    print(f"🔗 URL: {url[:80]}")
    print(f"📍 Platform: {job['platform']}")
    print(f"{'='*60}\n")

    # ── 2. Navigate to job page ──
    print("📡 Step 1: Navigating to job page...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    current_url = page.url
    print(f"   → Landed on: {current_url[:80]}")

    # ── 3. Detect page type ──
    print("\n🔍 Step 2: Detecting page type...")
    # Build a minimal snapshot for the classifier
    body_text = await page.locator("body").text_content() or ""
    classifier = PageTypeClassifier()
    page_type, confidence = classifier.classify({
        "url": current_url,
        "page_text_preview": body_text[:2000],
        "fields": [],
        "buttons": [],
    })
    print(f"   → Detected: {page_type.value} (confidence={confidence:.2f})")

    # ── 4. Find and click apply button ──
    print("\n👆 Step 3: Looking for apply button...")
    from jobpulse.navigation.wait_conditions import wait_for_page_stable
    from jobpulse.application_orchestrator_pkg._navigator import score_apply_button

    # Get all buttons on the page
    buttons = await page.locator("button, a[role='button']").all()
    scored: list[tuple[float, Any]] = []
    for btn in buttons:
        try:
            text = await btn.text_content() or ""
            text = text.strip()
            if not text:
                continue
            score = score_apply_button(text)
            if score > 0:
                scored.append((score, btn))
                print(f"   → Button: '{text[:40]}' score={score:.1f}")
        except Exception:
            continue

    if not scored:
        print("   ⚠️ No apply button found — taking screenshot for diagnosis")
        await page.screenshot(path="/tmp/pipeline_no_apply.png")
        pytest.skip("No apply button detected on this page")

    scored.sort(key=lambda x: x[0], reverse=True)
    best_btn = scored[0][1]
    print(f"\n   🎯 Clicking best apply button...")
    await best_btn.click()
    await asyncio.sleep(5)  # Wait for modal/redirect

    # ── 5. Check where we landed ──
    new_url = page.url
    print(f"\n🌐 Step 4: Post-click URL: {new_url[:80]}")

    detected_ats = detect_platform(new_url)
    print(f"   → Detected ATS: {detected_ats}")

    # ── 6. Scan form fields ──
    print(f"\n🔎 Step 5: Scanning form fields with UnifiedFieldScanner...")
    scanner = UnifiedFieldScanner(page)
    fields = await scanner.scan()

    print(f"\n{'='*60}")
    print(f"📊 RESULTS: {len(fields)} fields detected")
    print(f"{'='*60}")

    for i, f in enumerate(fields[:15], 1):
        widget = f.attributes.get("widget_library", "")
        widget_tag = f" [{widget}]" if widget else ""
        print(f"  {i:2}. [{f.input_type:12}]{widget_tag} {f.label[:45]!r}")

    if len(fields) > 15:
        print(f"  ... and {len(fields) - 15} more")

    # Take screenshot for human review
    await page.screenshot(path="/tmp/pipeline_form_detected.png")
    print(f"\n📸 Screenshot saved: /tmp/pipeline_form_detected.png")

    assert len(fields) >= 0  # Soft assertion — just report


@pytest.mark.asyncio
async def test_pipeline_full_fill_dry_run(
    playwright_page: Any,
    job_url: str | None,
) -> None:
    """Full pipeline: navigate → apply click → FormFillEngine with dry_run=True.

    This runs the COMPLETE pipeline but stops before submitting.
    It exercises the unified engine end-to-end on a real form.
    """
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.ats_adapters.discovery import detect_platform

    page = playwright_page

    # Pick job
    if job_url:
        job = {"url": job_url, "platform": "generic"}
    else:
        job = _pick_job_from_db()
    if not job:
        pytest.skip("No jobs in DB")

    url = job["url"]
    print(f"\n{'='*60}")
    print(f"🚀 FULL PIPELINE TEST: {url[:60]}")
    print(f"{'='*60}\n")

    # Navigate
    print("📡 Navigating...")
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Click apply (simple heuristic)
    print("👆 Clicking apply...")
    from jobpulse.application_orchestrator_pkg._navigator import score_apply_button
    buttons = await page.locator("button, a[role='button']").all()
    for btn in buttons:
        try:
            text = await btn.text_content() or ""
            if score_apply_button(text.strip()) >= 0.7:
                await btn.click()
                await asyncio.sleep(5)
                break
        except Exception:
            continue

    new_url = page.url
    detected_ats = detect_platform(new_url)
    print(f"🌐 ATS detected: {detected_ats}")

    # Run FormFillEngine (dry_run=True = no submit)
    print("\n⚙️ Running FormFillEngine (dry_run=True)...")
    os.environ["UNIFIED_FORM_ENGINE"] = "true"

    class _FakeDriver:
        page = page

    engine = FormFillEngine(page=page, driver=_FakeDriver(), application_id="pipeline_test")

    result = await engine.fill(
        profile=_TEST_PROFILE,
        custom_answers={},
        platform=detected_ats,
        dry_run=True,
    )

    print(f"\n{'='*60}")
    print(f"📊 FILL RESULT")
    print(f"{'='*60}")
    print(f"  success:         {result.success}")
    print(f"  pages_filled:    {result.pages_filled}")
    print(f"  fields_filled:   {result.total_fields_filled}")
    print(f"  fields_failed:   {result.total_fields_failed}")
    print(f"  llm_calls:       {result.llm_calls}")
    print(f"  time_seconds:    {result.time_seconds}")
    if result.error:
        print(f"  error:           {result.error}")
    print(f"{'='*60}")

    await page.screenshot(path="/tmp/pipeline_fill_result.png")
    print(f"\n📸 Screenshot saved: /tmp/pipeline_fill_result.png")

    # Soft assertions
    assert result.llm_calls >= 0
    assert result.time_seconds >= 0
