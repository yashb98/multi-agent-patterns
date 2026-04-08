"""Fill text inputs, textareas, and search/autocomplete fields."""

from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


async def fill_text(
    page,
    selector: str,
    value: str,
    clear_first: bool = True,
    timeout: int = 5000,
) -> FillResult:
    """Fill a text input field.

    Respects maxlength attribute. Clears pre-filled content if clear_first=True.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        disabled = await el.get_attribute("disabled")
        readonly = await el.get_attribute("readonly")
        if disabled is not None or readonly is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        # Respect maxlength
        maxlength = await el.get_attribute("maxlength")
        fill_value = value
        if maxlength:
            try:
                max_len = int(maxlength)
                fill_value = value[:max_len]
            except ValueError:
                pass

        await el.scroll_into_view_if_needed()
        await el.fill(fill_value)

        actual = await el.evaluate("el => el.value || ''")
        verified = actual == fill_value or fill_value[:10] in actual

        logger.debug("text_filler: filled %s (%d chars)", selector, len(fill_value))
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=fill_value,
            value_verified=verified,
        )

    except Exception as exc:
        logger.error("text_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )


async def fill_textarea(
    page,
    selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a textarea element. Handles maxlength and pre-filled content."""
    return await fill_text(page, selector, value, clear_first=True, timeout=timeout)


async def fill_autocomplete(
    page,
    selector: str,
    value: str,
    suggestion_selector: str = "li, [role='option']",
    timeout: int = 5000,
) -> FillResult:
    """Fill a search/autocomplete field.

    Types the value, waits for suggestion dropdown, clicks matching suggestion.
    Falls back to leaving typed text if freeform input is allowed.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        await el.scroll_into_view_if_needed()

        # Type at least 3 chars to trigger autocomplete
        type_text = value[:3] if len(value) >= 3 else value
        await el.fill("")  # clear first
        await el.type(type_text, delay=100)

        # Wait for suggestions to appear
        await page.wait_for_timeout(1500)

        # Look for matching suggestions
        suggestions = await page.query_selector_all(suggestion_selector)
        for suggestion in suggestions:
            text = await suggestion.text_content()
            if text and value.lower() in text.strip().lower():
                await suggestion.click()
                logger.debug("autocomplete: selected '%s' from suggestions", text.strip())
                return FillResult(
                    success=True, selector=selector,
                    value_attempted=value, value_set=text.strip(),
                )

        # No matching suggestion — type full value and press Escape
        await el.fill(value)
        await page.keyboard.press("Escape")
        logger.debug("autocomplete: no suggestion match, typed '%s' directly", value)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=value,
        )

    except Exception as exc:
        logger.error("autocomplete: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )
