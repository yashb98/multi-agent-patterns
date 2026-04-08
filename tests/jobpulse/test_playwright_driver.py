"""Tests for PlaywrightDriver — protocol compliance and unit tests."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
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


@pytest.mark.asyncio
async def test_navigate_calls_page_goto():
    """Navigate uses page.goto + networkidle."""
    driver = PlaywrightDriver()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_load_state = AsyncMock()
    mock_page.evaluate = AsyncMock(return_value={"url": "https://example.com", "title": "Test", "fields": []})
    driver._page = mock_page

    result = await driver.navigate("https://example.com")
    assert result["success"] is True
    assert "snapshot" in result
    mock_page.goto.assert_called_once()


@pytest.mark.asyncio
async def test_screenshot_returns_base64():
    """Screenshot returns base64-encoded PNG data."""
    driver = PlaywrightDriver()
    mock_page = MagicMock()
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    driver._page = mock_page

    result = await driver.screenshot()
    assert isinstance(result, bytes)
    assert result == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_get_snapshot_evaluates_js():
    """get_snapshot runs JS to scan form fields."""
    driver = PlaywrightDriver()
    mock_page = MagicMock()
    mock_page.evaluate = AsyncMock(return_value={
        "url": "https://example.com",
        "title": "Test",
        "fields": [{"selector": "#name", "type": "text", "value": "", "label": "", "required": True}],
    })
    driver._page = mock_page

    result = await driver.get_snapshot()
    assert result["url"] == "https://example.com"
    assert len(result["fields"]) == 1


@pytest.mark.asyncio
async def test_scan_validation_errors():
    """scan_validation_errors delegates to validation module."""
    driver = PlaywrightDriver()
    mock_page = MagicMock()
    mock_page.query_selector_all = AsyncMock(return_value=[])
    driver._page = mock_page

    result = await driver.scan_validation_errors()
    assert result["success"] is True
    assert result["errors"] == []


@pytest.mark.asyncio
async def test_fill_returns_verified():
    """fill() reads back value and verifies."""
    driver = PlaywrightDriver()
    mock_el = MagicMock()
    mock_el.scroll_into_view_if_needed = AsyncMock()
    mock_el.bounding_box = AsyncMock(return_value={"x": 100, "y": 200, "width": 200, "height": 40})
    mock_el.fill = AsyncMock()
    mock_el.evaluate = AsyncMock(return_value="John Doe")
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_el)
    mock_page.viewport_size = {"width": 1280, "height": 720}
    mock_page.mouse = MagicMock()
    mock_page.mouse.move = AsyncMock()
    driver._page = mock_page

    result = await driver.fill("#name", "John Doe")
    assert result["success"] is True
    assert result["value_verified"] is True


@pytest.mark.asyncio
async def test_fill_element_not_found():
    driver = PlaywrightDriver()
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=None)
    driver._page = mock_page
    result = await driver.fill("#missing", "val")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_click_success():
    driver = PlaywrightDriver()
    mock_el = MagicMock()
    mock_el.scroll_into_view_if_needed = AsyncMock()
    mock_el.bounding_box = AsyncMock(return_value={"x": 50, "y": 100, "width": 120, "height": 36})
    mock_el.click = AsyncMock()
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_el)
    mock_page.viewport_size = {"width": 1280, "height": 720}
    mock_page.mouse = MagicMock()
    mock_page.mouse.move = AsyncMock()
    driver._page = mock_page
    result = await driver.click("#btn")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_check_box_verifies():
    driver = PlaywrightDriver()
    mock_el = MagicMock()
    mock_el.check = AsyncMock()
    mock_el.is_checked = AsyncMock(return_value=True)
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_el)
    driver._page = mock_page
    result = await driver.check_box("#cb", True)
    assert result["success"] is True
    assert result["value_verified"] is True


@pytest.mark.asyncio
async def test_fill_date_verifies():
    driver = PlaywrightDriver()
    mock_el = MagicMock()
    mock_el.scroll_into_view_if_needed = AsyncMock()
    mock_el.fill = AsyncMock()
    mock_el.evaluate = AsyncMock(return_value="2026-04-07")
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_el)
    driver._page = mock_page
    result = await driver.fill_date("#dob", "2026-04-07")
    assert result["success"] is True
    assert result["value_verified"] is True


@pytest.mark.asyncio
async def test_upload_file():
    driver = PlaywrightDriver()
    mock_el = MagicMock()
    mock_el.set_input_files = AsyncMock()
    mock_page = MagicMock()
    mock_page.query_selector = AsyncMock(return_value=mock_el)
    driver._page = mock_page
    result = await driver.upload_file("#file", "/tmp/cv.pdf")
    assert result["success"] is True
    assert result["value_set"] == "/tmp/cv.pdf"


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
    # End point should be close to target
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
