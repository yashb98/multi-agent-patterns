"""Tests for PlaywrightDriver.rescan_after_fill()."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobpulse.playwright_driver import PlaywrightDriver


def _make_driver(page=None):
    """Create a PlaywrightDriver with a mocked page."""
    driver = PlaywrightDriver()
    driver._page = page or AsyncMock()
    return driver


@pytest.mark.asyncio
async def test_rescan_after_fill_reads_value():
    """current_value is read back from the element."""
    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="test@example.com")

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    driver = _make_driver(page)
    with patch.object(driver, "scan_validation_errors", AsyncMock(return_value={"has_errors": False, "errors": []})):
        result = await driver.rescan_after_fill("#email")

    assert result["success"] is True
    assert result["current_value"] == "test@example.com"
    page.query_selector.assert_called_once_with("#email")


@pytest.mark.asyncio
async def test_rescan_after_fill_no_element():
    """Returns current_value=None without raising when element not found."""
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)

    driver = _make_driver(page)
    with patch.object(driver, "scan_validation_errors", AsyncMock(return_value={"has_errors": False, "errors": []})):
        result = await driver.rescan_after_fill("#missing")

    assert result["success"] is True
    assert result["current_value"] is None
    assert result["validation_errors"] == []


@pytest.mark.asyncio
async def test_rescan_after_fill_with_validation_errors():
    """Validation errors from scan_validation_errors appear in result."""
    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="bad-value")

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    error_scan = {
        "has_errors": True,
        "errors": [
            {"field_selector": "#email", "error_message": "Invalid email format"},
        ],
    }

    driver = _make_driver(page)
    with patch.object(driver, "scan_validation_errors", AsyncMock(return_value=error_scan)):
        result = await driver.rescan_after_fill("#email")

    assert result["success"] is True
    assert len(result["validation_errors"]) == 1
    assert result["validation_errors"][0]["error_message"] == "Invalid email format"


@pytest.mark.asyncio
async def test_rescan_after_fill_filters_errors_by_selector():
    """Errors for other selectors are excluded; unscoped errors are included."""
    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="some value")

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    error_scan = {
        "has_errors": True,
        "errors": [
            {"field_selector": "#email", "error_message": "This field is required"},
            {"field_selector": "#phone", "error_message": "Invalid phone number"},
            {"field_selector": "", "error_message": "Form has errors"},
        ],
    }

    driver = _make_driver(page)
    with patch.object(driver, "scan_validation_errors", AsyncMock(return_value=error_scan)):
        result = await driver.rescan_after_fill("#email")

    selectors_in_result = [e["field_selector"] for e in result["validation_errors"]]
    assert "#email" in selectors_in_result
    assert "#phone" not in selectors_in_result
    assert "" in selectors_in_result  # unscoped errors included
