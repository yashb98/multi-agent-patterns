"""Tests for multi_select_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_tag_input():
    from jobpulse.form_engine.multi_select_filler import fill_tag_input

    page = MagicMock()
    el = MagicMock()
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await fill_tag_input(page, "#skills", ["Python", "React", "AWS"])
    assert result.success is True
    assert page.keyboard.press.call_count == 3  # Enter after each


@pytest.mark.asyncio
async def test_fill_tag_input_empty_values():
    from jobpulse.form_engine.multi_select_filler import fill_tag_input

    page = MagicMock()
    el = MagicMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_tag_input(page, "#skills", [])
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_native_multi_select():
    from jobpulse.form_engine.multi_select_filler import fill_native_multi_select

    page = MagicMock()
    el = MagicMock()
    page.query_selector = AsyncMock(return_value=el)
    page.select_option = AsyncMock(return_value=["Python", "React"])
    page.eval_on_selector_all = AsyncMock(return_value=["Python", "React", "Java", "Go"])

    result = await fill_native_multi_select(page, "#languages", ["Python", "React"])
    assert result.success is True
