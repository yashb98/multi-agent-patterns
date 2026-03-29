"""Tests for page_filler orchestrator."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_fill_field_by_type_text():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import FieldInfo, InputType

    field = FieldInfo(
        selector="#email", input_type=InputType.TEXT,
        label="Email", required=True,
    )
    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_field_by_type(page, field, "test@example.com")
    assert result.success is True


@pytest.mark.asyncio
async def test_fill_field_by_type_skips_readonly():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import FieldInfo, InputType

    field = FieldInfo(
        selector="#readonly", input_type=InputType.READONLY,
        label="ID", required=False,
    )
    page = MagicMock()

    result = await fill_field_by_type(page, field, "anything")
    assert result.success is True
    assert result.skipped is True


@pytest.mark.asyncio
async def test_fill_field_by_type_unknown():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import FieldInfo, InputType

    field = FieldInfo(
        selector="#weird", input_type=InputType.UNKNOWN,
        label="Unknown", required=False,
    )
    page = MagicMock()

    result = await fill_field_by_type(page, field, "anything")
    assert result.success is False
    assert "unsupported" in result.error.lower()


@pytest.mark.asyncio
async def test_fill_field_by_type_checkbox():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import FieldInfo, InputType

    field = FieldInfo(
        selector="#terms", input_type=InputType.CHECKBOX,
        label="Terms", required=True,
    )
    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(return_value=None)
    el.is_checked = AsyncMock(return_value=False)
    el.check = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_field_by_type(page, field, "yes")
    assert result.success is True


@pytest.mark.asyncio
async def test_fill_field_by_type_date():
    from jobpulse.form_engine.page_filler import fill_field_by_type
    from jobpulse.form_engine.models import FieldInfo, InputType

    field = FieldInfo(
        selector="#start_date", input_type=InputType.DATE_NATIVE,
        label="Start Date",
    )
    page = MagicMock()
    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: "date" if name == "type" else None)
    el.fill = AsyncMock()
    el.scroll_into_view_if_needed = AsyncMock()
    page.query_selector = AsyncMock(return_value=el)

    result = await fill_field_by_type(page, field, "2026-05-01")
    assert result.success is True
