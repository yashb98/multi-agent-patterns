"""iCIMS platform strategy.

iCIMS forms are often embedded in iframes with heavy JavaScript.
Key quirks:
- Main form lives inside "icims_content_iframe"
- Slow hydration after iframe load
- Custom dropdowns (not native <select>)
- Multi-page with "Next" and "Submit"
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
class ICIMSStrategy(BasePlatformStrategy):
    name = "icims"
    min_page_time = 6.0
    max_form_pages = 20

    def detect(self, url: str) -> bool:
        return "icims.com" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "button:has-text('Apply Now')",
            "a:has-text('Apply Now')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button:has-text('Next'):not([disabled])",
            "input[type='submit'][value*='Next']",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button:has-text('Submit'):not([disabled])",
            "input[type='submit'][value*='Submit']",
        ]

    def wait_for_form_hydrated_ms(self) -> int:
        # iCIMS iframe takes time to load
        return 10000

    def iframe_names(self) -> list[str]:
        return ["icims_content_iframe"]

    def known_widget_libraries(self) -> list[str]:
        # iCIMS uses custom dropdowns
        return ["icims_dropdown"]

    def normalize_label(self, label: str) -> str:
        return label.replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "first name": "first_name",
            "last name": "last_name",
            "phone number": "phone",
            "email address": "email",
            "resume": "cv",
            "cover letter": "cover_letter",
        }

    def screening_defaults(self) -> dict[str, str]:
        return {
            "are you legally authorized to work": "yes",
            "will you now or in the future require sponsorship": "no",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        # Wait for iframe to load
        try:
            iframe = page.frame_locator("iframe[name='icims_content_iframe']")
            if await iframe.locator("body").count():
                logger.info("iCIMS: content iframe detected")
        except Exception as exc:
            logger.debug("iCIMS iframe check: %s", exc)
        return {}

    async def custom_field_scan(self, page: "Page") -> list[dict] | None:
        """Scan inside iCIMS iframe if present."""
        try:
            iframe = page.frame_locator("iframe[name='icims_content_iframe']")
            if not await iframe.locator("body").count():
                return None

            # Let the unified scanner run inside the iframe
            # by returning None — the engine handles iframe traversal
            return None
        except Exception as exc:
            logger.debug("iCIMS custom scan failed: %s", exc)
            return None

    async def fill_combobox(
        self,
        page: "Page",
        locator: Any,
        value: str,
        label: str,
    ) -> str | None:
        """iCIMS dropdown: click → wait → click option."""
        try:
            await locator.click()
            await asyncio.sleep(0.5)

            # iCIMS options often use role="option"
            option = page.get_by_role("option").filter(has_text=value).first
            if await option.count():
                text = (await option.text_content() or "").strip()
                await option.click()
                return text

            return None
        except Exception as exc:
            logger.debug("iCIMS combobox fill failed for %s: %s", label[:40], exc)
            return None
