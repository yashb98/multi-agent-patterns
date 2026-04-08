"""Tests for text_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_text_basic():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.evaluate = AsyncMock(return_value="test@example.com")
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#email", "test@example.com")
    assert result.success is True
    el.fill.assert_called_once_with("test@example.com")


@pytest.mark.asyncio
async def test_fill_text_respects_maxlength():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "10" if name == "maxlength" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.evaluate = AsyncMock(return_value="This is a ")
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#short", "This is a very long text that exceeds the limit")
    assert result.success is True
    el.fill.assert_called_once_with("This is a ")


@pytest.mark.asyncio
async def test_fill_text_clears_prefilled():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "old value" if name == "value" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.evaluate = AsyncMock(return_value="New Value")
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_text(page, "#name", "New Value", clear_first=True)
    assert result.success is True


@pytest.mark.asyncio
async def test_fill_text_element_not_found():
    from jobpulse.form_engine.text_filler import fill_text

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_text(page, "#missing", "value")
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_fill_textarea_basic():
    from jobpulse.form_engine.text_filler import fill_textarea

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.evaluate = AsyncMock(return_value="My cover letter text here")
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_textarea(page, "#cover", "My cover letter text here")
    assert result.success is True
