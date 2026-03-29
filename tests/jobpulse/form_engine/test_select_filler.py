"""Tests for select_filler."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_native_select_exact_match():
    from jobpulse.form_engine.select_filler import fill_select

    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)

    page = MagicMock()
    page.select_option = AsyncMock(return_value=["United Kingdom"])
    page.query_selector = AsyncMock(return_value=el)
    page.eval_on_selector_all = AsyncMock(return_value=["United Kingdom", "United States"])
    page.wait_for_timeout = AsyncMock()

    result = await fill_select(page, "#country", "United Kingdom")
    assert result.success is True
    page.select_option.assert_called_once()


@pytest.mark.asyncio
async def test_fill_native_select_element_not_found():
    from jobpulse.form_engine.select_filler import fill_select

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_select(page, "#missing", "United Kingdom")
    assert result.success is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_fuzzy_match_finds_close_option():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["United Kingdom", "United States", "Canada", "Germany"]
    match = _fuzzy_match_option("UK", options)
    assert match == "United Kingdom"


@pytest.mark.asyncio
async def test_fuzzy_match_exact():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Yes", "No", "Prefer not to say"]
    match = _fuzzy_match_option("Yes", options)
    assert match == "Yes"


@pytest.mark.asyncio
async def test_fuzzy_match_no_match():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Red", "Green", "Blue"]
    match = _fuzzy_match_option("Purple", options)
    assert match is None
