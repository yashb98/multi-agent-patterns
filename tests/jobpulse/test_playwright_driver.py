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


@pytest.mark.asyncio
async def test_close_when_not_connected():
    """Close is safe when never connected."""
    driver = PlaywrightDriver()
    await driver.close()  # Should not raise


@pytest.mark.asyncio
async def test_fill_stubs_raise():
    """Fill method stubs raise NotImplementedError."""
    driver = PlaywrightDriver()
    stub_methods = [
        ("fill", ("sel", "val")),
        ("click", ("sel",)),
        ("select_option", ("sel", "val")),
        ("check_box", ("sel", True)),
        ("fill_radio", ("sel", "val")),
        ("fill_date", ("sel", "val")),
        ("fill_autocomplete", ("sel", "val")),
        ("fill_contenteditable", ("sel", "val")),
        ("upload_file", ("sel", "path")),
    ]
    for method_name, args in stub_methods:
        with pytest.raises(NotImplementedError):
            await getattr(driver, method_name)(*args)


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
    assert result["success"] is True
    assert isinstance(result["data"], str)


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
