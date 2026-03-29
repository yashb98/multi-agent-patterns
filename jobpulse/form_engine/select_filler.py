"""Fill dropdown/select elements — native <select> and custom React widgets."""

from __future__ import annotations

from shared.logging_config import get_logger
from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)

# Common abbreviation→full mappings for fuzzy matching
_ABBREVIATIONS: dict[str, str] = {
    "uk": "united kingdom",
    "us": "united states",
    "usa": "united states of america",
}


def _normalize(text: str) -> str:
    """Lowercase, strip whitespace and punctuation for comparison."""
    return text.lower().strip().strip(".,;:!?")


def _fuzzy_match_option(value: str, options: list[str]) -> str | None:
    """Find the best matching option for a value.

    Priority: exact → abbreviation → startswith → contains → None.
    """
    norm_value = _normalize(value)

    # Check abbreviation expansion
    expanded = _ABBREVIATIONS.get(norm_value, norm_value)

    for opt in options:
        if _normalize(opt) == expanded:
            return opt

    for opt in options:
        if _normalize(opt).startswith(expanded):
            return opt

    for opt in options:
        if expanded in _normalize(opt):
            return opt

    return None


async def fill_select(
    page,
    selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a native <select> element by matching visible option text.

    Tries exact match first, then fuzzy match (abbreviations, startswith, contains).
    """
    try:
        element = await page.query_selector(selector)
        if element is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error=f"Element {selector} not found",
            )

        # Check if disabled/readonly
        disabled = await element.get_attribute("disabled")
        if disabled is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, skipped=True,
            )

        # Get available options
        options = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(e => e.textContent.trim())",
        )

        if not options:
            # Might be async-loaded — wait and retry
            await page.wait_for_timeout(2000)
            options = await page.eval_on_selector_all(
                f"{selector} option",
                "els => els.map(e => e.textContent.trim())",
            )

        if not options:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value, error="No options found in select",
            )

        # Find the best match
        match = _fuzzy_match_option(value, options)
        if match is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {options[:5]}",
            )

        await page.select_option(selector, label=match)
        logger.debug("select_filler: filled %s with '%s'", selector, match)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=match,
        )

    except Exception as exc:
        logger.error("select_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )


async def fill_custom_select(
    page,
    trigger_selector: str,
    value: str,
    options_selector: str = "[role='option'], li",
    timeout: int = 5000,
) -> FillResult:
    """Fill a custom React/JS dropdown widget.

    Flow: click trigger → wait for options panel → fuzzy match → click option.
    """
    try:
        trigger = await page.query_selector(trigger_selector)
        if trigger is None:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error=f"Trigger {trigger_selector} not found",
            )

        # Click to open the dropdown
        await trigger.scroll_into_view_if_needed()
        await trigger.click()
        await page.wait_for_timeout(500)

        # Try typing to filter if there's a search input inside
        search_input = await page.query_selector(
            f"{trigger_selector} input, [role='combobox'] input"
        )
        if search_input:
            await search_input.fill(value)
            await page.wait_for_timeout(1000)  # wait for debounce

        # Get visible options
        option_els = await page.query_selector_all(options_selector)
        option_texts = []
        for el in option_els:
            text = await el.text_content()
            if text and text.strip():
                option_texts.append((text.strip(), el))

        if not option_texts:
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value, error="No options visible after opening dropdown",
            )

        # Fuzzy match
        texts_only = [t for t, _ in option_texts]
        match = _fuzzy_match_option(value, texts_only)
        if match is None:
            # Press Escape to close and report failure
            await page.keyboard.press("Escape")
            return FillResult(
                success=False, selector=trigger_selector,
                value_attempted=value,
                error=f"No matching option for '{value}' in {texts_only[:5]}",
            )

        # Click the matching option
        for text, el in option_texts:
            if text == match:
                await el.click()
                break

        logger.debug("custom_select: filled %s with '%s'", trigger_selector, match)
        return FillResult(
            success=True, selector=trigger_selector,
            value_attempted=value, value_set=match,
        )

    except Exception as exc:
        logger.error("custom_select: error filling %s: %s", trigger_selector, exc)
        return FillResult(
            success=False, selector=trigger_selector,
            value_attempted=value, error=str(exc),
        )
