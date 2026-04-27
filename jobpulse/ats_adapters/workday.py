"""Workday platform strategy.

Workday forms are heavy SPAs with slow hydration and complex widgets.
Key quirks:
- 10–20 s hydration time after navigation
- Uses custom comboboxes (not native <select>)
- Multi-page flow with "Next" / "Submit" buttons
- Progress bar indicates current step
- File upload may open a modal dialog
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
class WorkdayStrategy(BasePlatformStrategy):
    name = "workday"
    min_page_time = 8.0
    max_form_pages = 25

    def detect(self, url: str) -> bool:
        return "myworkdayjobs" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "button[data-automation-id='applyButton']",
            "button:has-text('Apply'):not([disabled])",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button[data-automation-id='bottom-navigation-next-button']",
            "button:has-text('Next'):not([disabled])",
            "button:has-text('Continue'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button[data-automation-id='bottom-navigation-submit-button']",
            "button:has-text('Submit'):not([disabled])",
        ]

    def expected_field_range(self) -> tuple[int, int]:
        return (3, 20)

    def wait_for_form_hydrated_ms(self) -> int:
        # Workday is notoriously slow
        return 15000

    def known_widget_libraries(self) -> list[str]:
        # Workday uses its own custom combobox component
        return ["workday_combobox"]

    def normalize_label(self, label: str) -> str:
        # Workday sometimes appends "(Required)"
        return label.replace("(Required)", "").replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "first name": "first_name",
            "last name": "last_name",
            "phone number": "phone",
            "email address": "email",
            "resume": "cv",
            "cover letter": "cover_letter",
            "linkedin profile url": "linkedin_url",
            "website / portfolio": "website",
        }

    def screening_defaults(self) -> dict[str, str]:
        return {
            "are you legally authorized to work": "yes",
            "will you now or in the future require sponsorship": "no",
            "have you previously worked for": "no",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        # Workday sometimes shows a "Start" button before the form
        try:
            start_btn = page.locator("button[data-automation-id='jobPostingStartButton']")
            if await start_btn.count():
                await start_btn.click()
                await asyncio.sleep(2)
                logger.info("Workday: clicked Start button")
        except Exception as exc:
            logger.debug("Workday start button check: %s", exc)
        return {}

    async def fill_combobox(
        self,
        page: "Page",
        locator: Any,
        value: str,
        label: str,
    ) -> str | None:
        """Workday combobox: click → type → wait for popup → click option."""
        try:
            await locator.click()
            await locator.fill("")
            await locator.fill(value)
            await asyncio.sleep(0.8)

            # Workday options appear in a floating listbox
            option = page.locator(
                f"[data-automation-id='promptOption']:has-text('{value}')"
            ).first
            if await option.count():
                await option.click()
                return value

            # Fallback: generic option role
            option = page.get_by_role("option").filter(has_text=value).first
            if await option.count():
                text = (await option.text_content() or "").strip()
                await option.click()
                return text

            return None
        except Exception as exc:
            logger.debug("Workday combobox fill failed for %s: %s", label[:40], exc)
            return None
