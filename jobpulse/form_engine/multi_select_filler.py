"""Fill multi-select elements — tag inputs, checkbox lists, native <select multiple>."""

from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult
from jobpulse.form_engine.select_filler import _fuzzy_match_option

logger = get_logger(__name__)


async def fill_tag_input(
    page,
    selector: str,
    values: list[str],
    timeout: int = 5000,
) -> FillResult:
    """Fill a tag/chip input by typing each value and pressing Enter."""
    try:
        if not values:
            return FillResult(
                success=True, selector=selector,
                value_attempted="", skipped=True,
            )

        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values), error=f"Element {selector} not found",
            )

        await el.scroll_into_view_if_needed()
        added: list[str] = []

        for val in values:
            await el.fill(val)
            await page.wait_for_timeout(200)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(300)
            added.append(val)

        logger.debug("tag_input: added %d tags to %s", len(added), selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(values), value_set=str(added),
        )

    except Exception as exc:
        logger.error("tag_input: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(values), error=str(exc),
        )


async def fill_native_multi_select(
    page,
    selector: str,
    values: list[str],
    timeout: int = 5000,
) -> FillResult:
    """Fill a native <select multiple> element."""
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values), error=f"Element {selector} not found",
            )

        # Get available options
        options = await page.eval_on_selector_all(
            f"{selector} option",
            "els => els.map(e => e.textContent.trim())",
        )

        # Fuzzy match each value
        matched: list[str] = []
        for val in values:
            match = _fuzzy_match_option(val, options)
            if match:
                matched.append(match)

        if not matched:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(values),
                error=f"No matching options for {values} in {options[:10]}",
            )

        await page.select_option(selector, label=matched)
        logger.debug("multi_select: selected %d options in %s", len(matched), selector)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(values), value_set=str(matched),
        )

    except Exception as exc:
        logger.error("multi_select: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(values), error=str(exc),
        )
