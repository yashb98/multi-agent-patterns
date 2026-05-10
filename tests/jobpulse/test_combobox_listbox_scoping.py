"""Test _resolve_listbox_scope avoids cross-combobox option hijacks.

Live evidence (2026-05-09 Anthropic Greenhouse run): the visa-sponsorship
React Select combobox was open at the same time as an intl-tel-input
phone country picker. The legacy fill code did
``page.locator(".select__option, [role='option']").filter(has_text="No")``
which matched ``[role='option']`` nodes inside the country picker iframe
container and selected ``Norway`` instead of ``No``.

The new ``_resolve_listbox_scope`` reads ``aria-controls`` / ``aria-owns``
off the combobox input and returns a Playwright Locator scoped to that
listbox so option clicks can never escape into a sibling component.

These tests use Playwright's `page.set_content` to assemble a minimal
DOM that reproduces the dual-listbox scenario.
"""

from __future__ import annotations

import pytest

playwright = pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from jobpulse.native_form_filler import _resolve_listbox_scope  # noqa: E402

_HTML = """
<!doctype html>
<html><body>
<!-- Visa Yes/No combobox (target field) -->
<div class="select__control">
  <input id="visa-input" type="text" role="combobox" aria-controls="visa-listbox" />
</div>
<div id="visa-listbox" class="select__menu" role="listbox">
  <div class="select__option" role="option">Yes</div>
  <div class="select__option" role="option">No</div>
</div>

<!-- Phone country picker — different listbox, same role='option' shape -->
<ul id="iti-0__country-listbox" class="iti__country-list" role="listbox">
  <li role="option" class="iti__country" data-country-code="lb">No</li>
  <li role="option" class="iti__country" data-country-code="no">Norway</li>
  <li role="option" class="iti__country" data-country-code="se">Sweden</li>
</ul>

<!-- Spec-noncompliant React Select (no aria-controls) -->
<div class="select__control">
  <input id="hispanic-input" type="text" role="combobox" />
</div>
<div class="select__menu">
  <div class="select__option" role="option">Yes</div>
  <div class="select__option" role="option">No</div>
  <div class="select__option" role="option">Decline to answer</div>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_aria_controls_scoping_avoids_cross_listbox():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.set_content(_HTML)
            visa_input = page.locator("#visa-input")
            scope = await _resolve_listbox_scope(page, visa_input)

            no_options = scope.locator(
                ".select__option, [role='option']"
            ).filter(has_text="No")
            count = await no_options.count()
            assert count == 1, (
                f"Expected exactly 1 'No' option scoped to visa listbox, got {count}"
            )

            text = await no_options.first.text_content()
            assert text and text.strip() == "No", (
                f"Expected 'No', got {text!r} (likely 'Norway' from phone picker)"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_react_select_sibling_fallback():
    """When aria-controls is missing, fall back to the React Select
    .select__menu sibling. Verifies the second resolution tier works."""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            await page.set_content(_HTML)
            hispanic_input = page.locator("#hispanic-input")
            scope = await _resolve_listbox_scope(page, hispanic_input)

            options = scope.locator(".select__option, [role='option']")
            count = await options.count()
            assert count == 3, (
                f"Expected 3 options in hispanic listbox, got {count} "
                f"(if higher, the scope leaked into the phone listbox)"
            )

            no_match = scope.locator(
                ".select__option, [role='option']"
            ).filter(has_text="No")
            no_count = await no_match.count()
            assert no_count == 1, (
                f"Expected exactly 1 'No' in scoped Hispanic listbox, got {no_count}"
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_global_fallback_when_no_listbox():
    """When no listbox can be resolved, the helper returns the page itself
    (last-resort path). This tier is the legacy behavior."""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            # Element with no associated listbox at all.
            await page.set_content(
                """<input id="orphan" type="text" role="combobox" />"""
            )
            orphan = page.locator("#orphan")
            scope = await _resolve_listbox_scope(page, orphan)
            # When falling back to page, scope IS the page.
            assert scope is page
        finally:
            await browser.close()
