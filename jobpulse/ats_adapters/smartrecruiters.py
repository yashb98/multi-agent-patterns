"""SmartRecruiters platform strategy — thin override for spl-autocomplete comboboxes.

Field scanning, label extraction, answer resolution, and filling all handled
by NativeFormFiller + form_scanner.py.  This strategy only overrides the
combobox interaction pattern for SmartRecruiters' shadow DOM web components.
"""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class SmartRecruitersStrategy(BasePlatformStrategy):
    name = "smartrecruiters"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "smartrecruiters.com" in url

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        """Upload CV for auto-parse before form scanning."""
        if not cv_path:
            return {}
        try:
            file_inputs = page.locator("input[type='file']")
            if await file_inputs.count():
                await file_inputs.first.set_input_files(cv_path)
                await asyncio.sleep(3)
                logger.info("SR: uploaded CV for auto-parse")
                return {"cv_uploaded": True}
        except Exception as exc:
            logger.debug("SR: CV auto-parse upload failed: %s", exc)
        return {}

    async def fill_combobox(
        self, page: "Page", locator: Any, value: str, label: str,
    ) -> str | None:
        """SmartRecruiters spl-autocomplete: type -> ArrowDown -> Enter."""
        try:
            await locator.click()
            await locator.fill("")
            await locator.fill(value)
            await asyncio.sleep(0.5)

            option = page.get_by_role("option").first
            if await option.count():
                text = (await option.text_content() or "").strip()
                await option.click()
                return text

            await locator.press("ArrowDown")
            await asyncio.sleep(0.2)
            await locator.press("Enter")
            return value
        except Exception as exc:
            logger.debug("SR combobox fill failed for %s: %s", label[:40], exc)
            return None
