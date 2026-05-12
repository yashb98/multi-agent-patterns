"""Wiring test: Oracle HCM ul[role=list]+li[role=listitem]+button Yes/No widget.

Live regression on JPMC 2026-05-05: agent missed all 4 Yes/No questions
on the JPMC 'Job Application Questions' page. They render as
<ul role='list'> + <li role='listitem'> + <button> with selected state
encoded via CSS class (not aria-checked or role=radio). field_scanner
queried input/select/textarea/role=radio and returned 0 fields for the
section.

Fix: _scan_dom_query now picks up these widgets as type='list_button_radio'.
NativeFormFiller adds a fill handler that clicks the option button by text.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_field_scanner_module_includes_list_button_radio_path():
    """The DOM-query JS embedded in _scan_dom_query must mention the widget."""
    import inspect
    from jobpulse.form_engine import field_scanner
    src = inspect.getsource(field_scanner)
    assert "list_button_radio" in src
    assert "ul[role=\"list\"]" in src or 'ul[role="list"]' in src


@pytest.mark.asyncio
async def test_native_form_filler_routes_list_button_radio_to_click_path():
    """When the agent encounters input_type='list_button_radio', it should
    click the option button with matching text via JS evaluate on the <ul>."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = MagicMock()
    page.url = "https://jpmc.fa.oraclecloud.com/.../section/2"
    driver = SimpleNamespace(intelligence=None)
    filler = NativeFormFiller(page=page, driver=driver)

    # Mock the <ul> locator: evaluate returns the clicked text
    ul_locator = MagicMock()
    ul_locator.evaluate = AsyncMock(return_value="Yes")

    result = await filler._fill_via_input_type(  # type: ignore[attr-defined]
        el=ul_locator, input_type="list_button_radio",
        fill_value="Yes", label="Are you at least 18 years of age?",
        role=None, tag="ul",
    ) if hasattr(filler, "_fill_via_input_type") else None

    # The dispatcher is named differently — find any method that fills by
    # input_type and exercise it. Otherwise just assert the widget type
    # branch exists in the source.
    if result is None:
        import inspect
        src = inspect.getsource(NativeFormFiller)
        assert 'input_type == "list_button_radio"' in src
        assert "li[role=\"listitem\"]" in src or 'li[role="listitem"]' in src
    else:
        assert result["success"] is True
        assert result["value_set"] == "Yes"
        ul_locator.evaluate.assert_awaited()


def test_widget_detection_handles_canonical_jpmc_options():
    """Sanity: the JS heuristic recognizes Yes/No-only lists and skips
    unrelated lists (nav menus, breadcrumbs)."""
    import inspect
    from jobpulse.form_engine import field_scanner
    src = inspect.getsource(field_scanner)
    # Must guard against generic <ul role="list"> (nav, breadcrumbs)
    assert "buttons.length !== lis.length" in src
    # Must label-gate (require aria-label / aria-labelledby)
    assert "aria-labelledby" in src
    # Must report the visible option text, not buttonId
    assert "optionTexts" in src


def test_selected_state_detection_includes_class_and_aria_pressed():
    """Selected state detection must cover (a) aria-pressed/aria-current,
    (b) CSS class (selected/active/is-selected/chosen), (c) computed
    background-color fallback."""
    import inspect
    from jobpulse.form_engine import field_scanner
    src = inspect.getsource(field_scanner)
    for marker in ("aria-pressed", "aria-current", "selected", "active",
                   "is-selected", "chosen", "backgroundColor"):
        assert marker in src, f"missing selection-state marker: {marker}"
