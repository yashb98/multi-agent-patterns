"""Indeed platform strategy.

Indeed has two apply flows:
1. One-click apply (minimal form, often pre-filled)
2. Full apply (multi-page with resume upload and questions)

Key quirks:
- "Apply Now" vs "Apply with Indeed Resume" paths
- File upload may be skipped if Indeed resume is used
- Screening questions appear after resume step
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class IndeedStrategy(BasePlatformStrategy):
    name = "indeed"
    min_page_time = 4.0

    def detect(self, url: str) -> bool:
        return "indeed.com" in url.lower()

    def apply_button_selectors(self) -> list[str]:
        return [
            "#applyButton",
            "button:has-text('Apply Now')",
            "button:has-text('Apply with Indeed Resume')",
        ]

    def next_page_selectors(self) -> list[str]:
        return [
            "button:has-text('Continue'):not([disabled])",
            "button:has-text('Next'):not([disabled])",
        ]

    def submit_selectors(self) -> list[str]:
        return [
            "button:has-text('Submit your application'):not([disabled])",
            "button:has-text('Submit Application'):not([disabled])",
        ]

    def wait_for_form_hydrated_ms(self) -> int:
        return 4000

    def normalize_label(self, label: str) -> str:
        return label.replace("*", "").strip()

    def extra_label_mappings(self) -> dict[str, str]:
        return {
            "full name": "full_name",
            "first and last name": "full_name",
            "phone number": "phone",
            "email address": "email",
            "resume": "cv",
            "cover letter": "cover_letter",
        }

    async def pre_fill(
        self,
        page: "Page",
        cv_path: str | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict[str, Any]:
        # Check if "Apply with Indeed Resume" is available
        try:
            indeed_resume = page.locator("button:has-text('Apply with Indeed Resume')")
            if await indeed_resume.count():
                logger.info("Indeed: 'Apply with Indeed Resume' button detected")
                return {"indeed_resume_available": True}
        except Exception as exc:
            logger.debug("Indeed resume check: %s", exc)
        return {}
