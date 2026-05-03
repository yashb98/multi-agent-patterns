"""Wiring tests: intent_healing integrated into NativeFormFiller._fill_by_label.

This file is an intentional exception to the project's no-Playwright-bridge-mock
policy. It exists solely to verify that _fill_by_label routes through heal_locator
when all built-in locator strategies return 0 elements, and that heal_locator is
NOT called when the initial locator resolves successfully.

DOM-behavioural tests (real fills) live in
tests/jobpulse/integration/test_pipeline_live.py.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _async_return(value):
    """Return a coroutine that yields *value* — compatible with AsyncMock."""
    async def _coro(*_args, **_kwargs):
        return value
    return _coro


def _make_locator(count: int, tag: str = "input"):
    """Build a minimal Playwright Locator-like mock returning *count* elements."""
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    loc.nth = MagicMock(return_value=loc)
    loc.first = loc
    loc.evaluate = AsyncMock(return_value=tag)
    loc.get_attribute = AsyncMock(return_value=None)
    loc.fill = AsyncMock()
    loc.type = AsyncMock()
    loc.click = AsyncMock()
    loc.inner_text = AsyncMock(return_value="")
    loc.is_visible = AsyncMock(return_value=True)
    loc.input_value = AsyncMock(return_value="hello")
    loc.all_text_contents = AsyncMock(return_value=[])
    loc.locator = MagicMock(return_value=loc)
    loc.select_option = AsyncMock()
    return loc


def _make_page(locator_for_label, locator_for_placeholder=None, locator_for_role=None):
    """Build a minimal Playwright Page-like mock."""
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.get_by_label = MagicMock(return_value=locator_for_label)
    page.get_by_placeholder = MagicMock(
        return_value=locator_for_placeholder or _make_locator(0)
    )
    page.get_by_role = MagicMock(
        return_value=locator_for_role or _make_locator(0)
    )
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.locator = MagicMock(return_value=_make_locator(0))
    return page


def _make_filler(page):
    """Construct a minimal NativeFormFiller with only the attributes _fill_by_label needs."""
    from jobpulse.native_form_filler import NativeFormFiller

    # NativeFormFiller.__init__ needs a real page + profile_store; we only need
    # _fill_by_label so we build a bare instance without calling __init__.
    filler = object.__new__(NativeFormFiller)
    filler._page = page
    filler._strategy = None
    filler._fe_db = None
    filler._container_selector = None
    filler._profile_store = MagicMock()
    filler._profile_store.get = MagicMock(return_value=None)
    # Disable special-widget and scroll helpers to keep the test focused
    filler._fill_special_widget = AsyncMock(return_value=None)
    filler._smart_scroll = AsyncMock()
    filler._move_mouse_to = AsyncMock()
    return filler


# ---------------------------------------------------------------------------
# Test 1 — heal_locator called when initial locator returns 0 elements
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_by_label_calls_heal_when_initial_locator_empty(tmp_path):
    """When get_by_label/placeholder/role all return 0 elements, _fill_by_label
    must call heal_locator. If healing succeeds the fill should succeed."""

    # Page always returns empty locators for all built-in strategies
    empty_loc = _make_locator(0)
    page = _make_page(
        locator_for_label=empty_loc,
        locator_for_placeholder=empty_loc,
        locator_for_role=empty_loc,
    )

    # Healed locator is a real-looking input element
    healed_loc = _make_locator(count=1, tag="input")

    filler = _make_filler(page)

    with (
        patch(
            "jobpulse.form_engine.field_scanner.scan_fields",
            new=AsyncMock(return_value=[{"label": "First name", "role": "textbox"}]),
        ),
        patch(
            "jobpulse.form_engine.intent_healing.heal_locator",
            new=AsyncMock(return_value=healed_loc),
        ) as mock_heal,
    ):
        result = await filler._fill_by_label("First name", "Alice")

    mock_heal.assert_called_once()
    call_kwargs = mock_heal.call_args
    assert call_kwargs.kwargs["intent"].label == "First name"
    assert call_kwargs.kwargs["stored_selector"] is None
    assert result.get("success") is True


# ---------------------------------------------------------------------------
# Test 2 — heal_locator NOT called when initial locator finds the element
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_by_label_skips_heal_when_locator_found():
    """heal_locator must NOT be invoked when the initial get_by_label call
    already returns 1 or more elements."""

    found_loc = _make_locator(count=1, tag="input")
    page = _make_page(locator_for_label=found_loc)
    filler = _make_filler(page)

    with patch(
        "jobpulse.form_engine.intent_healing.heal_locator",
        new=AsyncMock(return_value=None),
    ) as mock_heal:
        await filler._fill_by_label("Email", "test@example.com")

    mock_heal.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3 — returns failure when heal_locator also returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fill_by_label_returns_failure_when_heal_also_fails():
    """When all built-in strategies AND heal_locator return 0 / None, the result
    must indicate failure without raising."""

    empty_loc = _make_locator(0)
    page = _make_page(
        locator_for_label=empty_loc,
        locator_for_placeholder=empty_loc,
        locator_for_role=empty_loc,
    )
    filler = _make_filler(page)

    with (
        patch(
            "jobpulse.form_engine.field_scanner.scan_fields",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "jobpulse.form_engine.intent_healing.heal_locator",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await filler._fill_by_label("Nonexistent Field", "value")

    assert result.get("success") is False
    assert "Nonexistent Field" in result.get("error", "")
