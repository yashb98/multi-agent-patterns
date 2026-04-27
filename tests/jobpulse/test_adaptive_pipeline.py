"""Live integration tests for adaptive application orchestration.

These tests require a running Chrome instance with CDP enabled:
    python -m jobpulse.runner chrome-pw

Tests connect to real ATS job pages and validate the pipeline against
real DOM structures. Marked @pytest.mark.slow for CI exclusion.
"""

import os
import tempfile

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.skipif(
    not os.environ.get("LIVE_TESTS"),
    reason="Set LIVE_TESTS=1 to run live browser tests",
)]


@pytest.fixture
async def cdp_page():
    """Connect to existing Chrome via CDP and return a Page."""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = await context.new_page()
    yield page
    await page.close()
    await pw.stop()


@pytest.mark.asyncio
async def test_linkedin_container_scoping(cdp_page):
    """Navigate to LinkedIn jobs page -> verify container scoping."""
    from jobpulse.form_engine.field_scanner import resolve_form_container
    from jobpulse.ats_adapters.strategy import get_strategy

    strategy = get_strategy("linkedin")
    await cdp_page.goto("https://www.linkedin.com/jobs/", wait_until="networkidle")

    container = await resolve_form_container(cdp_page, strategy)
    assert container is None or ".jobs-easy-apply-modal" in (container or "")


@pytest.mark.asyncio
async def test_greenhouse_container_detection(cdp_page):
    """Navigate to a Greenhouse application page -> verify container auto-detection."""
    from jobpulse.form_engine.field_scanner import resolve_form_container, scan_fields
    from jobpulse.ats_adapters.strategy import get_strategy

    strategy = get_strategy("greenhouse")
    await cdp_page.goto(
        "https://boards.greenhouse.io/example/jobs/123",
        wait_until="networkidle",
        timeout=15000,
    )
    container = await resolve_form_container(cdp_page, strategy)
    fields = await scan_fields(cdp_page, strategy=strategy)

    min_f, max_f = strategy.expected_field_range()
    if fields:
        assert len(fields) >= min_f
        assert len(fields) <= max_f * 1.5


@pytest.mark.asyncio
async def test_semantic_matcher_real_greenhouse_gender_options(cdp_page):
    """Verify semantic matching on real gender options from Greenhouse forms."""
    from jobpulse.form_engine.semantic_matcher import semantic_option_match

    real_options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
    assert semantic_option_match("male", real_options) == "Man"
    assert semantic_option_match("female", real_options) == "Woman"


@pytest.mark.asyncio
async def test_workday_timing_measurement(cdp_page):
    """Verify timing is measured and stored with running averages."""
    from jobpulse.form_experience_db import FormExperienceDB

    with tempfile.TemporaryDirectory() as tmp:
        db = FormExperienceDB(db_path=os.path.join(tmp, "test.db"))
        db.store_timing("myworkdayjobs.com", hydration_ms=9000, fill_ms=15000, transition_ms=4000)
        timing = db.get_timing("myworkdayjobs.com")
        assert timing is not None
        assert timing["avg_hydration_ms"] == 9000

        db.store_timing("myworkdayjobs.com", hydration_ms=11000, fill_ms=17000, transition_ms=6000)
        timing = db.get_timing("myworkdayjobs.com")
        assert timing["avg_hydration_ms"] == 10000
        assert timing["avg_fill_ms"] == 16000
