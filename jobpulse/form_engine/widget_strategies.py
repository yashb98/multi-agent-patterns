"""WidgetFillStrategy — per-widget-library fill strategies.

Registered strategies for React-Select, MUI Autocomplete, Ant Design,
intl-tel-input, SmartRecruiters spl-*, Workday, and Greenhouse.

Each strategy receives a Playwright Page, FieldInfo, and value,
and returns a FillResult.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FieldInfo, FillResult
from jobpulse.form_engine.widget_detector import get_widget_config

logger = get_logger(__name__)

# Registry of widget fill strategies
_WIDGET_STRATEGIES: dict[
    str, Callable[[Any, FieldInfo, str], Coroutine[Any, Any, FillResult]]
] = {}


def register_widget_strategy(library: str):
    """Decorator to register a widget fill strategy."""
    def decorator(
        fn: Callable[[Any, FieldInfo, str], Coroutine[Any, Any, FillResult]]
    ) -> Callable[[Any, FieldInfo, str], Coroutine[Any, Any, FillResult]]:
        _WIDGET_STRATEGIES[library] = fn
        return fn
    return decorator


def get_strategy(library: str | None) -> Callable | None:
    """Get the fill strategy for a widget library."""
    if not library:
        return None
    return _WIDGET_STRATEGIES.get(library)


# ── React-Select ──

@register_widget_strategy("react_select")
async def fill_react_select(page, field: FieldInfo, value: str) -> FillResult:
    """Fill a React-Select dropdown — handles both searchable and non-searchable variants.

    Strategy:
      1. Click to open the dropdown.
      2. Try to find and click a matching option without typing (non-searchable).
      3. If no match, type to filter (searchable) and retry.
      4. Verify the selected value is set.
    """
    cfg = get_widget_config("react_select") or {}
    option_sel = cfg.get("option_selector", ".select__option")
    menu_sel = cfg.get("menu_selector", ".select__menu")

    async def _collect_options() -> list[tuple]:
        """Collect visible (text, element) pairs, excluding intl-tel-input noise."""
        for sel in (option_sel, f"{menu_sel} {option_sel}", "[role='option']"):
            candidates = await page.locator(sel).all()
            candidates = [
                c for c in candidates
                if not await c.evaluate("el => el.closest('.iti__country-list') !== null")
            ]
            if candidates:
                return [((await c.text_content() or "").strip(), c) for c in candidates]
        # Last resort: menu children
        menu_els = await page.locator(f"{menu_sel} > div, {menu_sel} [class*='option']").all()
        menu_els = [
            c for c in menu_els
            if not await c.evaluate("el => el.closest('.iti__country-list') !== null")
        ]
        return [((await c.text_content() or "").strip(), c) for c in menu_els]

    def _best_option(options: list[tuple], target: str) -> tuple | None:
        """Fuzzy match target against option texts. Returns (text, element, score)."""
        norm_target = target.lower().strip()
        best = None
        best_score = -1
        for text, el in options:
            if not text:
                continue
            norm_text = text.lower().strip()
            if norm_text == norm_target:
                return (text, el, 100)
            if norm_target in norm_text:
                score = 50 + len(norm_target) / max(len(norm_text), 1)
            else:
                vtokens = set(norm_target.split())
                ttokens = set(norm_text.split())
                overlap = len(vtokens & ttokens)
                score = overlap / max(len(vtokens), 1) * 30 if vtokens else 0
            if score > best_score:
                best_score = score
                best = (text, el, score)
        return best

    try:
        input_el = page.locator(field.selector).first
        if not await input_el.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="React-Select input not found",
            )

        await input_el.scroll_into_view_if_needed()

        # --- Phase 1: Click to open and see if options appear (non-searchable) ---
        await input_el.click()
        await asyncio.sleep(0.6)
        options = await _collect_options()

        match = _best_option(options, value)
        if match is None or match[2] <= 0:
            # --- Phase 2: Type to filter (searchable variant) ---
            type_value = value
            if "," in value and len(value.split(",")[0]) >= 3:
                type_value = value.split(",")[0].strip()
            await input_el.fill("")
            await asyncio.sleep(0.1)
            await input_el.type(type_value, delay=30)
            await asyncio.sleep(0.8)
            options = await _collect_options()
            match = _best_option(options, value)

        if match is None or match[2] <= 0:
            # Phase 3: if still no match, pick first real option as fallback
            for text, el in options:
                if text and text not in ("No options", "Loading..."):
                    match = (text, el, 1)
                    break

        if match is None:
            await page.keyboard.press("Escape")
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="No React-Select options visible",
            )

        _, opt_el, _ = match
        await opt_el.click()
        await asyncio.sleep(0.3)

        # Verify: some React Select variants store value in data-value instead of single-value
        selected_text = ""
        try:
            selected_text = await page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return "";
                    const control = el.closest('.select__control') || el.closest('[class*="select__"]');
                    if (!control) return "";
                    const vc = control.querySelector('.select__value-container');
                    if (vc) {
                        const sv = vc.querySelector('.select__single-value');
                        if (sv) return sv.textContent.trim();
                    }
                    const sv = control.querySelector('.select__single-value');
                    if (sv) return sv.textContent.trim();
                    // Some variants store value in data-value on input-container
                    const ic = control.querySelector('.select__input-container');
                    if (ic && ic.getAttribute('data-value')) return ic.getAttribute('data-value').trim();
                    return "";
                }""",
                field.selector,
            )
        except Exception:
            pass

        return FillResult(
            success=True, selector=field.selector,
            value_attempted=value, value_set=selected_text or value,
            value_verified=bool(selected_text),
        )

    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── MUI Autocomplete ──

@register_widget_strategy("mui_autocomplete")
async def fill_mui_autocomplete(page, field: FieldInfo, value: str) -> FillResult:
    """Fill a MUI Autocomplete by typing and selecting from dropdown."""
    cfg = get_widget_config("mui_autocomplete") or {}
    option_sel = cfg.get("option_selector", ".MuiAutocomplete-option")

    try:
        input_el = page.locator(field.selector).first
        if not await input_el.count():
            # Try to find input inside autocomplete
            input_el = page.locator(".MuiAutocomplete-input").first

        if not await input_el.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="MUI Autocomplete input not found",
            )

        await input_el.fill("")
        await input_el.type(value[:5] if len(value) >= 5 else value, delay=50)
        await asyncio.sleep(0.6)

        options = await page.locator(option_sel).all()
        for opt in options:
            text = (await opt.text_content() or "").strip()
            if text and value.lower() in text.lower():
                await opt.click()
                return FillResult(
                    success=True, selector=field.selector,
                    value_attempted=value, value_set=text, value_verified=True,
                )

        await page.keyboard.press("Escape")
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=f"No MUI option matching '{value}'",
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── Ant Design Select ──

@register_widget_strategy("ant_select")
async def fill_ant_select(page, field: FieldInfo, value: str) -> FillResult:
    """Fill an Ant Design Select dropdown."""
    cfg = get_widget_config("ant_select") or {}
    option_sel = cfg.get("option_selector", ".ant-select-item-option-content")

    try:
        trigger = page.locator(field.selector).first
        if not await trigger.count():
            trigger = page.locator(".ant-select").first

        if not await trigger.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="Ant Select trigger not found",
            )

        await trigger.click()
        await asyncio.sleep(0.3)

        options = await page.locator(option_sel).all()
        for opt in options:
            text = (await opt.text_content() or "").strip()
            if text and value.lower() in text.lower():
                await opt.click()
                return FillResult(
                    success=True, selector=field.selector,
                    value_attempted=value, value_set=text, value_verified=True,
                )

        await page.keyboard.press("Escape")
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=f"No Ant Select option matching '{value}'",
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── intl-tel-input ──

@register_widget_strategy("intl_tel_input")
async def fill_intl_tel_input(page, field: FieldInfo, value: str) -> FillResult:
    """Fill an intl-tel-input phone field.

    Expects the value to be the full phone number.
    Country selection is handled separately if needed.
    """
    try:
        # Find the actual phone input (may be inside the iti container)
        phone_input = page.locator(f"{field.selector} input, input[type='tel']").first
        if not await phone_input.count():
            phone_input = page.locator(field.selector).first

        if not await phone_input.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="intl-tel-input phone input not found",
            )

        await phone_input.fill(value)
        actual = await phone_input.evaluate("el => el.value || ''")
        verified = actual == value or value[:10] in actual

        return FillResult(
            success=True, selector=field.selector,
            value_attempted=value, value_set=actual,
            value_verified=verified,
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── SmartRecruiters spl-* ──

@register_widget_strategy("smartrecruiters_spl")
async def fill_smartrecruiters_spl(page, field: FieldInfo, value: str) -> FillResult:
    """Fill a SmartRecruiters spl-autocomplete combobox.

    Strategy: click → clear → type value → ArrowDown → Enter.
    """
    try:
        combo = page.locator(field.selector).first
        if not await combo.count():
            combo = page.get_by_role("combobox", name=field.label).first

        if not await combo.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="SmartRecruiters combobox not found",
            )

        await combo.click()
        await combo.fill("")
        await combo.fill(value)
        await asyncio.sleep(0.5)

        option = page.get_by_role("option").first
        if await option.count():
            text = (await option.text_content() or "").strip()
            await option.click()
            return FillResult(
                success=True, selector=field.selector,
                value_attempted=value, value_set=text, value_verified=True,
            )

        await combo.press("ArrowDown")
        await asyncio.sleep(0.2)
        await combo.press("Enter")

        actual = await combo.input_value()
        return FillResult(
            success=True, selector=field.selector,
            value_attempted=value, value_set=actual or value,
            value_verified=bool(actual),
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── Workday dropdown ──

@register_widget_strategy("workday_wd")
async def fill_workday_dropdown(page, field: FieldInfo, value: str) -> FillResult:
    """Fill a Workday dropdown (data-automation-id based)."""
    cfg = get_widget_config("workday_wd") or {}
    option_sel = cfg.get("option_selector", "[data-automation-id='menuItem']")

    try:
        trigger = page.locator(field.selector).first
        if not await trigger.count():
            trigger = page.locator("[data-automation-id='dropdownArrow']").first

        if not await trigger.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="Workday dropdown trigger not found",
            )

        await trigger.click()
        await asyncio.sleep(0.4)

        options = await page.locator(option_sel).all()
        for opt in options:
            text = (await opt.text_content() or "").strip()
            if text and value.lower() in text.lower():
                await opt.click()
                return FillResult(
                    success=True, selector=field.selector,
                    value_attempted=value, value_set=text, value_verified=True,
                )

        await page.keyboard.press("Escape")
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=f"No Workday option matching '{value}'",
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )


# ── Greenhouse custom select2 ──

@register_widget_strategy("greenhouse_custom")
async def fill_greenhouse_custom(page, field: FieldInfo, value: str) -> FillResult:
    """Fill a Greenhouse Select2 dropdown."""
    cfg = get_widget_config("greenhouse_custom") or {}
    option_sel = cfg.get("option_selector", ".select2-results__option")

    try:
        trigger = page.locator(field.selector).first
        if not await trigger.count():
            trigger = page.locator(".select2-selection").first

        if not await trigger.count():
            return FillResult(
                success=False, selector=field.selector,
                value_attempted=value, error="Greenhouse Select2 trigger not found",
            )

        await trigger.click()
        await asyncio.sleep(0.3)

        options = await page.locator(option_sel).all()
        for opt in options:
            text = (await opt.text_content() or "").strip()
            if text and value.lower() in text.lower():
                await opt.click()
                return FillResult(
                    success=True, selector=field.selector,
                    value_attempted=value, value_set=text, value_verified=True,
                )

        await page.keyboard.press("Escape")
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=f"No Select2 option matching '{value}'",
        )
    except Exception as exc:
        return FillResult(
            success=False, selector=field.selector,
            value_attempted=value, error=str(exc),
        )
