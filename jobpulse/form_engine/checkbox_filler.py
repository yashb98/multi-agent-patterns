"""Fill checkboxes, toggles, and consent boxes."""

from __future__ import annotations

import re

from shared.logging_config import get_logger

from jobpulse.form_engine.models import FillResult

logger = get_logger(__name__)

_CONSENT_KEYWORDS = re.compile(
    r"agree|consent|terms|privacy|gdpr|accept|acknowledge|policy|conditions",
    re.IGNORECASE,
)


def _is_consent_checkbox(label_text: str) -> bool:
    """Return True if the label indicates a consent/terms checkbox."""
    return bool(_CONSENT_KEYWORDS.search(label_text))


async def fill_checkbox(
    page,
    selector: str,
    should_check: bool = True,
    timeout: int = 5000,
) -> FillResult:
    """Check or uncheck a checkbox element.

    Args:
        page: Playwright page.
        selector: CSS selector for the checkbox.
        should_check: True to check, False to uncheck.
        timeout: Max wait time in ms.
    """
    try:
        el = await page.query_selector(selector)
        if el is None:
            return FillResult(
                success=False, selector=selector,
                value_attempted=str(should_check), error=f"Element {selector} not found",
            )

        disabled = await el.get_attribute("disabled")
        if disabled is not None:
            return FillResult(
                success=True, selector=selector,
                value_attempted=str(should_check), skipped=True,
            )

        current = await el.is_checked()
        if current == should_check:
            logger.debug("checkbox: %s already %s", selector, "checked" if current else "unchecked")
            return FillResult(
                success=True, selector=selector,
                value_attempted=str(should_check),
                value_set=str(should_check), skipped=True,
            )

        await el.scroll_into_view_if_needed()
        if should_check:
            await el.check()
        else:
            await el.uncheck()

        logger.debug("checkbox: %s set to %s", selector, should_check)
        return FillResult(
            success=True, selector=selector,
            value_attempted=str(should_check), value_set=str(should_check),
        )

    except Exception as exc:
        logger.error("checkbox: error filling %s: %s", selector, exc)
        return FillResult(
            success=False, selector=selector,
            value_attempted=str(should_check), error=str(exc),
        )


async def auto_check_consent_boxes(page) -> list[FillResult]:
    """Find and check all consent/terms/privacy checkboxes on the page."""
    results: list[FillResult] = []
    checkboxes = await page.query_selector_all("input[type='checkbox']")

    for cb in checkboxes:
        # Get label text
        cb_id = await cb.get_attribute("id")
        label_text = ""
        if cb_id:
            label_el = await page.query_selector(f"label[for='{cb_id}']")
            if label_el:
                label_text = await label_el.text_content() or ""

        if not label_text:
            label_text = await cb.evaluate(
                "el => el.parentElement ? el.parentElement.textContent.trim() : ''"
            ) or ""

        if _is_consent_checkbox(label_text):
            selector = f"#{cb_id}" if cb_id else "input[type='checkbox']"
            result = await fill_checkbox(page, selector, should_check=True)
            results.append(result)

    return results
