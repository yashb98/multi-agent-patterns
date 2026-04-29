"""Ashby platform strategy.

Ashby uses a modern React form with minimal custom widgets.
Key quirks:
- Clean, accessible form markup
- Standard file uploads
- Multi-page with "Next" and "Submit"
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class AshbyStrategy(BasePlatformStrategy):
    name = "ashby"
    min_page_time = 4.0

    def detect(self, url: str) -> bool:
        return "ashbyhq.com" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "a:has-text('Apply')",
            "button:has-text('Apply')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button:has-text('Next'):not([disabled])",
            "button:has-text('Continue'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button:has-text('Submit Application'):not([disabled])",
        ]

    def wait_for_form_hydrated_ms(self) -> int:
        return 3000

    def normalize_label(self, label: str) -> str:
        return label.replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "name": "full_name",
            "email": "email",
            "phone": "phone",
            "resume": "cv",
            "cover letter": "cover_letter",
            "linkedin": "linkedin_url",
            "website": "website",
            "portfolio": "portfolio_url",
        }

    def screening_defaults(self) -> dict[str, str]:
        return {
            "are you legally authorized to work": "yes",
            "will you require sponsorship": "no",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        return {}
