"""Tests for TrackedDriver — transparent instrumentation wrapper."""
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest
from unittest.mock import AsyncMock
from jobpulse.tracked_driver import TrackedDriver, ABTracker


@pytest.fixture
def mock_driver():
    driver = AsyncMock()
    driver.fill.return_value = {"success": True, "value_set": "test", "value_verified": True}
    driver.click.return_value = {"success": True}
    driver.select_option.return_value = {"success": True, "value_set": "UK", "value_verified": True}
    driver.check_box.return_value = {"success": True, "value_set": "True", "value_verified": True}
    driver.navigate.return_value = {"success": True}
    driver.screenshot.return_value = {"success": True, "data": "base64"}
    driver.get_snapshot.return_value = {"url": "test", "fields": []}
    driver.scan_validation_errors.return_value = {"success": True, "errors": []}
    driver.close.return_value = None
    return driver


@pytest.mark.asyncio
async def test_tracked_fill_logs_event(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="playwright", application_id="app1", db_path=db_path)
    result = await tracked.fill("#email", "test@test.com")

    assert result["success"] is True
    mock_driver.fill.assert_called_once_with("#email", "test@test.com")

    tracker = ABTracker(db_path=db_path)
    stats = tracker.get_engine_stats("playwright", days=1)
    assert stats["total_fields"] == 1
    assert stats["fields_verified"] == 1


@pytest.mark.asyncio
async def test_tracked_click_logs_event(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="extension", application_id="app2", db_path=db_path)
    result = await tracked.click("#submit")
    assert result["success"] is True
    mock_driver.click.assert_called_once_with("#submit")

    tracker = ABTracker(db_path=db_path)
    stats = tracker.get_engine_stats("extension", days=1)
    assert stats["total_fields"] == 1


@pytest.mark.asyncio
async def test_tracked_select_logs_event(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="playwright", application_id="app3", db_path=db_path)
    result = await tracked.select_option("#country", "UK")
    assert result["success"] is True


@pytest.mark.asyncio
async def test_tracked_navigate_passes_through(tmp_path, mock_driver):
    """Navigate passes through without logging."""
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="playwright", application_id="app4", db_path=db_path)
    result = await tracked.navigate("https://example.com")
    assert result["success"] is True
    mock_driver.navigate.assert_called_once()

    tracker = ABTracker(db_path=db_path)
    stats = tracker.get_engine_stats("playwright", days=1)
    assert stats["total_fields"] == 0  # navigate is not a field event


@pytest.mark.asyncio
async def test_set_platform_tags_events(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="playwright", application_id="app5", db_path=db_path)
    tracked.set_platform("greenhouse")
    await tracked.fill("#name", "Yash")
    # Verify platform was set (check via raw SQLite)
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT platform FROM field_events WHERE app_id='app5'").fetchone()
    assert row[0] == "greenhouse"


@pytest.mark.asyncio
async def test_tracked_close_passes_through(tmp_path, mock_driver):
    db_path = str(tmp_path / "ab.db")
    tracked = TrackedDriver(mock_driver, engine="pw", application_id="a1", db_path=db_path)
    await tracked.close()
    mock_driver.close.assert_called_once()
