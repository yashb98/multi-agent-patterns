"""Tests for select_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
    page.eval_on_selector = AsyncMock(return_value="United Kingdom")
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
async def test_fuzzy_match_prefers_united_kingdom_for_plus44():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Ukraine (+380)", "United Kingdom (+44)", "United States (+1)"]
    match = _fuzzy_match_option("+44", options)
    assert match == "United Kingdom (+44)"


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


def test_token_overlap_matching():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["3-5 years", "6-10 years", "10+ years"]
    assert _fuzzy_match_option("3 to 5 years", options) == "3-5 years"


def test_numeric_range_matching():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["3-5 years experience", "6-10 years experience", "10+ years experience"]
    assert _fuzzy_match_option("3+ years", options) == "3-5 years experience"


def test_token_overlap_reordered_words():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Bachelor's degree", "Master's degree", "High school diploma"]
    assert _fuzzy_match_option("degree bachelor", options) == "Bachelor's degree"


def test_token_overlap_no_false_positive():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Red", "Green", "Blue"]
    assert _fuzzy_match_option("Purple", options) is None


def test_numeric_range_years_format_variants():
    from jobpulse.form_engine.select_filler import _fuzzy_match_option

    options = ["Less than 1 year", "1-2 years", "3-5 years", "5+ years"]
    assert _fuzzy_match_option("1 to 2 years", options) == "1-2 years"
    assert _fuzzy_match_option("1 year", options) == "1-2 years"
