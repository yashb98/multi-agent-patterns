"""Tests for checkbox_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_is_consent_checkbox_detects_terms():
    from jobpulse.form_engine.checkbox_filler import _is_consent_checkbox
    assert _is_consent_checkbox("I agree to the terms and conditions") is True


@pytest.mark.asyncio
async def test_is_consent_checkbox_rejects_normal():
    from jobpulse.form_engine.checkbox_filler import _is_consent_checkbox
    assert _is_consent_checkbox("I have a disability") is False


@pytest.mark.asyncio
async def test_fill_checkbox_checks_when_should_be_true():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=False)
    el.check = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#terms", should_check=True)
    assert result.success is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_checkbox_skips_when_already_correct():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=True)
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#terms", should_check=True)
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_checkbox_unchecks_when_should_be_false():
    from jobpulse.form_engine.checkbox_filler import fill_checkbox

    page = MagicMock()
    el = MagicMock()
    el.is_checked = AsyncMock(return_value=True)
    el.uncheck = AsyncMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_checkbox(page, "#sponsor", should_check=False)
    assert result.success is True
    el.uncheck.assert_called_once()
