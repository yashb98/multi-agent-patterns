"""Generic fallback ATS adapter."""

import contextlib
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class GenericAdapter(BaseATSAdapter):
    name: str = "generic"

    def detect(self, url: str) -> bool:
        """Generic adapter accepts any URL as fallback."""
        return True

    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict | None = None,
    ) -> dict:
        try:
            from jobpulse.utils.safe_io import managed_browser
        except ImportError:
            logger.warning("Playwright not installed — Generic adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("Generic form fill: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            ) as (_browser, page):
                page.goto(url, timeout=30000)

                # Best-effort: fill common input patterns by name/placeholder/label
                _fill_by_pattern(page, "email", profile.get("email", ""))
                _fill_by_pattern(page, "phone", profile.get("phone", ""))
                _fill_by_pattern(page, "first", profile.get("first_name", ""))
                _fill_by_pattern(page, "last", profile.get("last_name", ""))
                _fill_by_pattern(
                    page,
                    "name",
                    f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
                )

                # Upload CV to first file input found
                file_input = page.query_selector("input[type='file']")
                if file_input and cv_path.exists():
                    file_input.set_input_files(str(cv_path))

                # Fill custom answers by field name or id
                for field_key, answer in custom_answers.items():
                    if field_key.startswith("_"):
                        continue  # Skip internal keys like _job_context
                    el = page.query_selector(f"[name='{field_key}'], [id='{field_key}']")
                    if el:
                        el.fill(str(answer))

                # Answer screening questions via shared get_answer() engine
                job_context = custom_answers.get("_job_context") if custom_answers else None
                self.answer_screening_questions(page, job_context)

                screenshot_path = cv_path.parent / "generic_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Generic adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}


def _fill_by_pattern(page: object, keyword: str, value: str) -> None:
    """Attempt to fill an input field matching a keyword in name, id, or placeholder."""
    if not value:
        return
    selectors = [
        f"input[name*='{keyword}']",
        f"input[id*='{keyword}']",
        f"input[placeholder*='{keyword}']",
    ]
    for selector in selectors:
        el = page.query_selector(selector)  # type: ignore[attr-defined]
        if el:
            with contextlib.suppress(Exception):
                el.fill(value)
            break
