"""Tests for date_filler."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import pytest


@pytest.mark.asyncio
async def test_fill_native_date():
    from jobpulse.form_engine.date_filler import fill_date

    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "date" if name == "type" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    el.evaluate = AsyncMock(return_value="2026-05-01")
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_date(page, "#start_date", "2026-05-01")
    assert result.success is True
    el.fill.assert_called_once_with("2026-05-01")


@pytest.mark.asyncio
async def test_fill_date_element_not_found():
    from jobpulse.form_engine.date_filler import fill_date

    page = MagicMock()
    page.query_selector = AsyncMock(return_value=None)

    result = await fill_date(page, "#missing", "2026-05-01")
    assert result.success is False


def test_format_date_uk():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "DD/MM/YYYY") == "01/05/2026"


def test_format_date_us():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "MM/DD/YYYY") == "05/01/2026"


def test_format_date_iso():
    from jobpulse.form_engine.date_filler import _format_date

    assert _format_date("2026-05-01", "YYYY-MM-DD") == "2026-05-01"
