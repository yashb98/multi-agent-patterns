"""LinkedIn Easy Apply platform strategy.

LinkedIn uses a modal-based Easy Apply flow.
Key quirks:
- Application opens in a modal dialog, not a new page
- "Next" and "Review" buttons advance through steps
- External apply opens a new tab (handled by navigator)
- Discard dialog appears on modal close
- File upload uses standard input but inside modal
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
class LinkedInStrategy(BasePlatformStrategy):
    name = "linkedin"
    min_page_time = 3.0
    max_form_pages = 10

    def detect(self, url: str) -> bool:
        return "linkedin.com" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "button.jobs-apply-button",
            "[data-control-name='jobdetails_topcard_inapply']",
            "button:has-text('Easy Apply')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button[aria-label='Continue to next step']",
            "button:has-text('Next'):not([disabled])",
            "button:has-text('Review'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button[aria-label='Submit application']",
            "button:has-text('Submit application'):not([disabled])",
        ]

    def form_container_hint(self) -> str | None:
        return ".jobs-easy-apply-modal"

    def expected_field_range(self) -> tuple[int, int]:
        return (3, 10)

    def wait_for_form_hydrated_ms(self) -> int:
        # Easy Apply modal renders quickly
        return 2000

    def known_widget_libraries(self) -> list[str]:
        return []

    def normalize_label(self, label: str) -> str:
        return label.replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "email address": "email",
            "phone number": "phone",
            "phone country code": "phone_country",
            "resume": "cv",
            "cover letter": "cover_letter",
            "linkedin profile": "linkedin_url",
            "website": "website",
            "how did you hear about this job": "referral_source",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        # Ensure modal is open before scanning
        try:
            modal = page.locator('[role="dialog"][aria-modal="true"], .jobs-easy-apply-modal')
            if not await modal.count():
                logger.warning("LinkedIn: Easy Apply modal not open yet")
        except Exception as exc:
            logger.debug("LinkedIn modal check: %s", exc)
        return {}

    async def post_page(
        self,
        page: "Page",
        page_num: int,
        result: dict,
    ) -> None:
        # LinkedIn sometimes shows "Follow [Company]" checkbox
        # after submission — we intentionally do NOT interact with it
        pass
