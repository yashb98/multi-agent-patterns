"""Tests for jobpulse.browser_manager — no real browser required."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.browser_manager import (
    BrowserManager,
    human_type,
    random_delay,
)


# ---------------------------------------------------------------------------
# screenshot_error
# ---------------------------------------------------------------------------

def test_screenshot_error_creates_directory(tmp_path: Path):
    """screenshot_error should create the job directory and save the file."""

    async def _run():
        manager = BrowserManager()
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock()

        with patch("jobpulse.browser_manager.DATA_DIR", tmp_path):
            result = await manager.screenshot_error(mock_page, "job-42", "apply")

        expected = tmp_path / "applications" / "job-42" / "error_apply.png"
        assert result == expected
        assert expected.parent.is_dir()
        mock_page.screenshot.assert_awaited_once_with(path=str(expected))

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# human_type
# ---------------------------------------------------------------------------

def test_human_type_includes_delays():
    """human_type should call page.type once per character (at minimum)."""

    async def _run():
        mock_page = AsyncMock()
        mock_page.wait_for_selector = AsyncMock()
        mock_page.focus = AsyncMock()
        mock_page.type = AsyncMock()
        mock_page.keyboard = AsyncMock()
        mock_page.keyboard.press = AsyncMock()

        text = "hello"

        # 0.5 > 0.05 so no typos will fire
        with patch("jobpulse.browser_manager.random.random", return_value=0.5):
            with patch("jobpulse.browser_manager.asyncio.sleep", new_callable=AsyncMock):
                await human_type(mock_page, "#input", text)

        # page.type should be called once per character
        assert mock_page.type.await_count == len(text)
        # Verify each character was typed
        typed_chars = [call.args[1] for call in mock_page.type.call_args_list]
        assert typed_chars == list(text)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# random_delay
# ---------------------------------------------------------------------------

def test_random_delay_within_bounds():
    """random_delay should sleep between min_s and max_s."""

    async def _run():
        min_s, max_s = 0.1, 0.3  # short bounds for fast test

        start = time.monotonic()
        await random_delay(min_s=min_s, max_s=max_s)
        elapsed = time.monotonic() - start

        # Allow small timing tolerance
        assert elapsed >= min_s - 0.05
        assert elapsed <= max_s + 0.15

    asyncio.run(_run())
