"""Tests for form validation detection."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_ROOT = Path(__file__).parent.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest


@pytest.mark.asyncio
async def test_scan_for_errors_finds_aria_invalid():
    from jobpulse.form_engine.validation import scan_for_errors

    error_el = MagicMock()
    error_el.get_attribute = AsyncMock(side_effect=lambda name: {
        "id": "email",
        "aria-invalid": "true",
    }.get(name))
    error_el.text_content = AsyncMock(return_value="")
    error_el.evaluate = AsyncMock(return_value="Please enter a valid email")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[error_el])

    errors = await scan_for_errors(page)
    assert len(errors) >= 1


@pytest.mark.asyncio
async def test_scan_for_errors_empty_page():
    from jobpulse.form_engine.validation import scan_for_errors

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[])

    errors = await scan_for_errors(page)
    assert errors == []


@pytest.mark.asyncio
async def test_find_required_unfilled():
    from jobpulse.form_engine.validation import find_required_unfilled

    el = MagicMock()
    el.get_attribute = AsyncMock(side_effect=lambda name: {
        "required": "",
        "id": "email",
        "value": "",
    }.get(name))
    el.evaluate = AsyncMock(return_value="")

    page = MagicMock()
    page.query_selector_all = AsyncMock(return_value=[el])

    unfilled = await find_required_unfilled(page)
    assert len(unfilled) >= 1
