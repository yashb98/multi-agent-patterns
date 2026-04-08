"""Tests for NativeFormFiller — Playwright native pipeline."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_filler(page_mock=None, driver_mock=None):
    """Create a NativeFormFiller with mocked dependencies."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = page_mock or MagicMock()
    driver = driver_mock or AsyncMock()
    driver.page = page
    return NativeFormFiller(page=page, driver=driver)


# ── _get_accessible_name ──


@pytest.mark.asyncio
async def test_get_accessible_name_returns_label():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="Email Address")

    result = await filler._get_accessible_name(locator)
    assert result == "Email Address"
    locator.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_get_accessible_name_empty_fallback():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="")

    result = await filler._get_accessible_name(locator)
    assert result == ""


# ── _scan_fields ──


@pytest.mark.asyncio
async def test_scan_fields_text_inputs():
    """Scans textbox role elements and returns field dicts."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    textbox = AsyncMock()
    textbox.input_value = AsyncMock(return_value="")
    textbox.get_attribute = AsyncMock(return_value=None)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[textbox])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role

    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="First Name"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["label"] == "First Name"
    assert fields[0]["type"] == "text"
    assert fields[0]["locator"] is textbox


@pytest.mark.asyncio
async def test_scan_fields_select_with_options():
    """Scans combobox (select) elements and captures options."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    select_el = AsyncMock()
    select_el.input_value = AsyncMock(return_value="")
    option_locator = MagicMock()
    option_locator.all_text_contents = AsyncMock(return_value=["USA", "UK", "Canada"])
    select_el.locator = lambda sel: option_locator

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[select_el])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Country"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "select"
    assert fields[0]["options"] == ["USA", "UK", "Canada"]


@pytest.mark.asyncio
async def test_scan_fields_checkbox():
    """Scans checkbox elements with checked state."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    with patch.object(filler, "_get_accessible_name", return_value="Agree to terms"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox"
    assert fields[0]["checked"] is False


# ── _fill_by_label ──


@pytest.mark.asyncio
async def test_fill_by_label_text_input():
    """Fills a text field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="john@example.com")

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Email", "john@example.com")

    assert result["success"] is True
    el.fill.assert_called_once_with("john@example.com")


@pytest.mark.asyncio
async def test_fill_by_label_select():
    """Fills a select field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(side_effect=["select", "United States"])
    el.get_attribute = AsyncMock(return_value=None)
    el.select_option = AsyncMock()

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country", "United States")

    assert result["success"] is True
    el.select_option.assert_called_once_with(label="United States")


@pytest.mark.asyncio
async def test_fill_by_label_not_found():
    """Returns error when no field matches label or placeholder."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=empty_locator)

    with patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Nonexistent", "value")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_fill_by_label_checkbox():
    """Checks a checkbox found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value="checkbox")
    el.check = AsyncMock()
    el.is_checked = AsyncMock(return_value=True)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("I agree", "yes")

    assert result["success"] is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_by_label_placeholder_fallback():
    """Falls back to placeholder when label locator finds nothing."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="test")

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    placeholder_locator = MagicMock()
    placeholder_locator.count = AsyncMock(return_value=1)
    placeholder_locator.first = el

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=placeholder_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Search", "test")

    assert result["success"] is True
    page.get_by_placeholder.assert_called_once()
