"""Fill date picker fields — native <input type=date> and custom calendar widgets."""

from __future__ import annotations

from datetime import datetime

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)


def _format_date(iso_date: str, fmt: str = "YYYY-MM-DD") -> str:
    """Convert ISO date string to the specified format.

    Supported formats: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY.
    """
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date

    if fmt == "DD/MM/YYYY":
        return dt.strftime("%d/%m/%Y")
    if fmt == "MM/DD/YYYY":
        return dt.strftime("%m/%d/%Y")
    return dt.strftime("%Y-%m-%d")


def _detect_date_format(placeholder: str | None) -> str:
    """Detect date format from placeholder text."""
    if not placeholder:
        return "YYYY-MM-DD"
    p = placeholder.lower()
    if "dd/mm" in p:
        return "DD/MM/YYYY"
    if "mm/dd" in p:
        return "MM/DD/YYYY"
    return "YYYY-MM-DD"


async def fill_date(
    page,
    selector: str,
    value: str,
    date_format: str | None = None,
    timeout: int = 5000,
) -> FillResult:
    """Fill a date input field.

    Args:
        page: Playwright page.
        selector: CSS selector for the date field.
        value: Date in ISO format (YYYY-MM-DD).
        date_format: Override format. Auto-detected from placeholder if None.
        timeout: Max wait time in ms.
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

        await el.scroll_into_view_if_needed()

        # Detect if native date input
        input_type = await el.get_attribute("type")
        if input_type == "date":
            # Native date inputs always use YYYY-MM-DD internally
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            logger.debug("date_filler: native date %s = %s", selector, value)
            return FillResult(
                success=True, selector=selector,
                value_attempted=value, value_set=value,
                value_verified=(actual == value),
            )

        # Text-based date field — format according to placeholder or override
        if date_format is None:
            placeholder = await el.get_attribute("placeholder")
            date_format = _detect_date_format(placeholder)

        formatted = _format_date(value, date_format)
        await el.fill(formatted)

        # Press Tab to trigger validation/confirm
        await page.keyboard.press("Tab")

        actual = await el.evaluate("el => el.value || ''")
        logger.debug("date_filler: text date %s = %s (format=%s)", selector, formatted, date_format)
        return FillResult(
            success=True, selector=selector,
            value_attempted=value, value_set=formatted,
            value_verified=(formatted[:4] in actual),
        )

    except Exception as exc:
        logger.error("date_filler: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=value, error=str(exc),
        )
