"""Greenhouse platform strategy.

Greenhouse uses standard HTML forms with minimal widget libraries.
Key quirks:
- Cover letter is often a rich-text textarea (not a file upload)
- "Next" button advances through multi-page applications
- EEOC questions appear on a separate page
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class GreenhouseStrategy(BasePlatformStrategy):
    name = "greenhouse"
    min_page_time = 4.0

    def detect(self, url: str) -> bool:
        return "greenhouse" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "#apply_button",
            "[data-mosaic-component-name='ApplyButton']",
            "a:has-text('Apply Now')",
            "button:has-text('Apply Now')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button:has-text('Next'):not([disabled])",
            "input[type='submit'][value*='Next']",
            "button:has-text('Continue'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button:has-text('Submit Application'):not([disabled])",
            "input[type='submit'][value*='Submit Application']",
        ]

    def form_container_hint(self) -> str | None:
        return "#application"

    def expected_field_range(self) -> tuple[int, int]:
        return (3, 15)

    def wait_for_form_hydrated_ms(self) -> int:
        # Greenhouse forms hydrate quickly
        return 3000

    def normalize_label(self, label: str) -> str:
        # Strip Greenhouse's required-field asterisk marker
        return label.replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "first name": "first_name",
            "last name": "last_name",
            "phone": "phone",
            "email": "email",
            "resume/cv": "cv",
            "cover letter": "cover_letter",
            "linkedin profile": "linkedin_url",
            "website": "website",
            "how did you hear about this job?": "referral_source",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        # Greenhouse sometimes has a "Paste" mode for cover letter
        # — we let the generic engine handle it as a textarea
        return {}
