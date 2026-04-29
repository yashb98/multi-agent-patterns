"""End-to-end integration test for the unified FormFillEngine.

Runs against REAL ATS forms (supervised, no auto-submit).

Usage:
    # Run against default Greenhouse test board
    pytest tests/jobpulse/integration/test_unified_engine_live.py -v

    # Run against a specific URL
    pytest tests/jobpulse/integration/test_unified_engine_live.py -v \
        --ats-url "https://boards.greenhouse.io/company/jobs/123"

    # Skip live tests (default in CI)
    pytest tests/jobpulse/ -k "not integration"
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# Skip entire module unless --run-integration is passed
pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not os.environ.get("RUN_INTEGRATION_TESTS"),
        reason="Set RUN_INTEGRATION_TESTS=1 to run live ATS tests",
    ),
]

# Default test target — a well-known Greenhouse board
_DEFAULT_GREENHOUSE_URL = "https://boards.greenhouse.io/twilio/jobs/7652892"

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


@pytest.fixture(scope="module")
def event_loop():
    """Override pytest-asyncio's default loop to allow module-scoped fixtures."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def playwright_page():
    """Launch a real Playwright browser page."""
    pytest.importorskip("playwright", reason="playwright not installed")
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 800},
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
def ats_url(request: Any) -> str:
    """Return the ATS URL to test against."""
    return request.config.getoption("--ats-url") or _DEFAULT_GREENHOUSE_URL


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--ats-url",
        action="store",
        default=None,
        help="ATS form URL to test against (default: Twilio Greenhouse board)",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unified_engine_detects_fields(
    playwright_page: Any,
    ats_url: str,
) -> None:
    """Navigate to a real form and verify UnifiedFieldScanner finds fields."""
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

    page = playwright_page
    await page.goto(ats_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)  # Let JS hydrate

    scanner = UnifiedFieldScanner(page)
    fields = await scanner.scan()

    assert len(fields) > 0, f"No fields detected on {ats_url}"

    # Log what we found for diagnostics
    print(f"\n=== Detected {len(fields)} fields on {ats_url} ===")
    for f in fields[:10]:
        print(f"  [{f.input_type:12}] {f.label[:50]!r} ({f.selector[:60]})")


@pytest.mark.asyncio
async def test_unified_engine_widget_detection(
    playwright_page: Any,
    ats_url: str,
) -> None:
    """Verify WidgetLibraryDetector identifies any custom widgets."""
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner
    from jobpulse.form_engine.widget_detector import WidgetLibraryDetector

    page = playwright_page
    await page.goto(ats_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    scanner = UnifiedFieldScanner(page)
    fields = await scanner.scan()

    detector = WidgetLibraryDetector(page)
    widget_map = await detector.detect_for_page()

    widgets_found = set(widget_map.values())
    if widgets_found:
        print(f"\n=== Widget libraries detected: {widgets_found} ===")
    else:
        print("\n=== No custom widgets detected (standard HTML form) ===")

    # Soft assertion: we just want to know what's there
    assert isinstance(widget_map, dict)


@pytest.mark.asyncio
async def test_unified_engine_builds_mapping(
    playwright_page: Any,
    ats_url: str,
) -> None:
    """Run FormFillEngine._build_mapping against real fields — no page interaction."""
    from jobpulse.form_engine.engine import FormFillEngine
    from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

    page = playwright_page
    await page.goto(ats_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Minimal driver mock
    class _FakeDriver:
        page = page

    engine = FormFillEngine(page=page, driver=_FakeDriver(), application_id="test_integration")

    # Scan fields
    fields = await engine._scanner.scan()
    assert len(fields) > 0, "No fields to map"

    # Build mapping (this exercises field_mapper + LLM calls)
    # Use a dummy strategy to avoid needing real platform strategy
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("generic")

    mapping, llm_calls = await engine._build_mapping(
        fields, _TEST_PROFILE, {}, "generic", strategy
    )

    print(f"\n=== Mapping built: {len(mapping)} fields, {llm_calls} LLM calls ===")
    for label, value in list(mapping.items())[:10]:
        print(f"  {label[:40]!r} → {str(value)[:40]!r}")

    # Core assertion: mapping was produced without exception
    assert isinstance(mapping, dict)
    assert len(mapping) > 0 or len(fields) == 0


@pytest.mark.asyncio
async def test_unified_engine_full_pipeline_no_submit(
    playwright_page: Any,
    ats_url: str,
) -> None:
    """Run the full FormFillEngine.fill() pipeline with dry_run=True.

    This is the most important test: it exercises scanner → mapper → filler
    → navigation, but stops before clicking Submit.
    """
    from jobpulse.form_engine.engine import FormFillEngine

    page = playwright_page
    await page.goto(ats_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    class _FakeDriver:
        page = page

    os.environ["UNIFIED_FORM_ENGINE"] = "true"
    engine = FormFillEngine(page=page, driver=_FakeDriver(), application_id="test_integration")

    result = await engine.fill(
        profile=_TEST_PROFILE,
        custom_answers={},
        platform="greenhouse",
        dry_run=True,
    )

    print(f"\n=== FormFillEngine result ===")
    print(f"  success: {result.success}")
    print(f"  pages_filled: {result.pages_filled}")
    print(f"  fields_filled: {result.total_fields_filled}")
    print(f"  fields_failed: {result.total_fields_failed}")
    print(f"  llm_calls: {result.llm_calls}")
    print(f"  time: {result.time_seconds}s")
    if result.error:
        print(f"  error: {result.error}")

    # Soft assertions: we expect partial success on a real form
    assert result.total_fields_filled > 0, "Engine should fill at least one field"
    assert result.llm_calls >= 0

    # AB tracker should have logged something
    from jobpulse.tracked_driver import ABTracker
    tracker = ABTracker()
    stats = tracker.get_engine_stats("unified_form_engine", days=1)
    print(f"\n=== ABTracker stats: {stats} ===")
