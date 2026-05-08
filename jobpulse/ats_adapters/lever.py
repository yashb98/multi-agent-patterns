"""Lever platform strategy.

Lever uses a multi-page React form with smooth transitions.
Key quirks:
- File uploads are standard <input type="file">
- "Next" and "Submit" buttons are styled <button> elements
- Cover letter can be file upload OR text area depending on config
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class LeverStrategy(BasePlatformStrategy):
    name = "lever"
    min_page_time = 4.0

    def detect(self, url: str) -> bool:
        return "lever.co" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "[data-qa='btn-apply']",
            "a:has-text('Apply to this job')",
            "button:has-text('Apply to this job')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button:has-text('Next'):not([disabled])",
            "button:has-text('Continue'):not([disabled])",
            "button:has-text('Review'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button:has-text('Submit Application'):not([disabled])",
            "button:has-text('Apply'):not([disabled])",
        ]

    def wait_for_form_hydrated_ms(self) -> int:
        return 3000

    def normalize_label(self, label: str) -> str:
        # Lever sometimes adds a colon after labels
        return label.rstrip(":*").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "resume": "cv",
            "cover letter": "cover_letter",
            "full name": "full_name",
            "phone": "phone",
            "email": "email",
            "linkedin": "linkedin_url",
            "twitter": "twitter_url",
            "portfolio": "portfolio_url",
            "other website": "website",
            "pronouns": "pronouns",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        return {}
