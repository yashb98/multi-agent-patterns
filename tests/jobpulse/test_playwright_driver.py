"""Tests for PlaywrightDriver — pure helpers and protocol compliance.

Per project policy: no mocking of the Playwright bridge. The bridge-driven
tests (navigate, fill, click, check_box, fill_date, upload_file,
get_snapshot, scan_validation_errors, connect-with-retry) used
AsyncMock/MagicMock for the Playwright Page / Browser / Context — Category
B per project policy. Real driver behavior is exercised end-to-end against
real Chrome via CDP in `tests/jobpulse/integration/test_pipeline_live.py`.

What remains: pure-function tests for protocol compliance, init defaults,
fuzzy matching, retry logic, Bezier curves, and timing helpers.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from jobpulse.playwright_driver import PlaywrightDriver
from jobpulse.driver_protocol import DriverProtocol


def test_playwright_driver_has_all_protocol_methods():
    """PlaywrightDriver has all DriverProtocol methods."""
    required = [
        "navigate", "fill", "click", "select_option", "check_box",
        "fill_radio", "fill_date", "fill_autocomplete",
        "fill_contenteditable", "upload_file", "screenshot",
        "get_snapshot", "scan_validation_errors", "close",
    ]
    for method in required:
        assert hasattr(PlaywrightDriver, method), f"Missing method: {method}"


def test_playwright_driver_init():
    """Constructor initializes all fields to None."""
    driver = PlaywrightDriver()
    assert driver._pw is None
    assert driver._browser is None
    assert driver._context is None
    assert driver._page is None


def test_page_property_before_connect():
    """page property returns None before connect()."""
    driver = PlaywrightDriver()
    assert driver.page is None


@pytest.mark.asyncio
async def test_close_when_not_connected():
    """Close is safe when never connected."""
    driver = PlaywrightDriver()
    await driver.close()  # Should not raise


# ── _fuzzy_match (pure string matching) ──


def test_fuzzy_match_exact():
    from jobpulse.playwright_driver import _fuzzy_match
    assert _fuzzy_match("United Kingdom", ["France", "United Kingdom", "Germany"]) == "United Kingdom"

def test_fuzzy_match_startswith():
    from jobpulse.playwright_driver import _fuzzy_match
    assert _fuzzy_match("United", ["France", "United Kingdom", "Germany"]) == "United Kingdom"

def test_fuzzy_match_contains():
    from jobpulse.playwright_driver import _fuzzy_match
    assert _fuzzy_match("Kingdom", ["France", "United Kingdom", "Germany"]) == "United Kingdom"

def test_fuzzy_match_none():
    from jobpulse.playwright_driver import _fuzzy_match
    assert _fuzzy_match("Spain", ["France", "United Kingdom"]) is None


# ── _with_retry (pure async retry wrapper) ──


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_try():
    from jobpulse.playwright_driver import _with_retry
    call_count = 0
    async def fn():
        nonlocal call_count
        call_count += 1
        return {"success": True}
    result = await _with_retry(fn)
    assert result["success"] is True
    assert call_count == 1


@pytest.mark.asyncio
async def test_with_retry_retries_on_failure():
    from jobpulse.playwright_driver import _with_retry
    call_count = 0
    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("fail")
        return {"success": True}
    result = await _with_retry(fn, max_retries=2, delay_ms=10)
    assert result["success"] is True
    assert call_count == 3


# ── _bezier_points / _get_field_gap / _scroll_delay (pure math) ──


def test_bezier_points_short_distance():
    """Very short distance returns just the endpoint."""
    from jobpulse.playwright_driver import _bezier_points
    points = _bezier_points(100, 100, 102, 101)
    assert len(points) == 1
    assert points[0] == (102, 101)


def test_bezier_points_generates_curve():
    """Normal distance generates multiple curve points."""
    from jobpulse.playwright_driver import _bezier_points
    points = _bezier_points(0, 0, 500, 500)
    assert len(points) == 15
    assert abs(points[-1][0] - 500) < 1
    assert abs(points[-1][1] - 500) < 1


def test_field_gap_scales_with_length():
    from jobpulse.playwright_driver import _get_field_gap
    short = _get_field_gap("Name")
    medium = _get_field_gap("Please enter your full legal name")
    long = _get_field_gap("Please provide the complete legal name as it appears on your government-issued identification document")
    assert short < medium < long


def test_scroll_delay_scales_with_distance():
    from jobpulse.playwright_driver import _scroll_delay
    near = _scroll_delay(20)
    mid = _scroll_delay(200)
    far = _scroll_delay(500)
    assert near < mid < far
