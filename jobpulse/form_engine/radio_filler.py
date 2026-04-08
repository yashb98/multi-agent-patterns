"""Fill radio button groups by matching label text to desired value."""

from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult
from jobpulse.form_engine.select_filler import _fuzzy_match_option

logger = get_logger(__name__)


async def _get_radio_label(page, radio_el) -> str:
    """Extract the label text for a radio button.

    Tries: <label for="id">, sibling text, parent text, aria-label.
    """
    # Try <label for="id">
    radio_id = await radio_el.get_attribute("id")
    if radio_id:
        label_el = await page.query_selector(f"label[for='{radio_id}']")
        if label_el:
            text = await label_el.text_content()
            if text and text.strip():
                return text.strip()

    # Try aria-label
    aria = await radio_el.get_attribute("aria-label")
    if aria:
        return aria.strip()

    # Try parent element text
    parent_text = await radio_el.evaluate(
        "el => el.parentElement ? el.parentElement.textContent.trim() : ''"
    )
    if parent_text:
        return parent_text

    return ""


async def fill_radio_group(
    page,
    group_selector: str,
    value: str,
    timeout: int = 5000,
) -> FillResult:
    """Fill a radio button group by selecting the option matching value.

    Args:
        page: Playwright page.
        group_selector: CSS selector for the radio inputs (e.g. "input[name='sponsor']").
        value: The desired answer text (e.g. "No", "Yes", "Prefer not to say").
        timeout: Max wait time in ms.

    Returns:
        FillResult with success status.
    """
    try:
        radios = await page.query_selector_all(group_selector)
        if not radios:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value, error="No radio elements found",
            )

        # Build label→element mapping
        label_map: list[tuple[str, object]] = []
        for radio in radios:
            label = await _get_radio_label(page, radio)
            if label:
                label_map.append((label, radio))

        if not label_map:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value, error="No labels found for radio buttons",
            )

        # Fuzzy match
        labels = [lbl for lbl, _ in label_map]
        match = _fuzzy_match_option(value, labels)
        if match is None:
            return FillResult(
                success=False, selector=group_selector,
                value_attempted=value,
                error=f"No matching radio option for '{value}' in {labels}",
            )

        # Click the matching radio
        for label, radio in label_map:
            if label == match:
                await radio.scroll_into_view_if_needed()
                await radio.click()
                actual_checked = await radio.is_checked()
                logger.debug("radio_filler: selected '%s' in %s", match, group_selector)
                return FillResult(
                    success=True, selector=group_selector,
                    value_attempted=value, value_set=match,
                    value_verified=actual_checked,
                )

        return FillResult(
            success=False, selector=group_selector,
            value_attempted=value, error="Match found but click failed",
        )

    except Exception as exc:
        logger.error("radio_filler: error filling %s: %s", group_selector, exc)
        return FillResult(
            success=False, selector=group_selector,
            value_attempted=value, error=str(exc),
        )
