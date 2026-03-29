"""LinkedIn Easy Apply adapter."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class LinkedInAdapter(BaseATSAdapter):
    name: str = "linkedin"

    def detect(self, url: str) -> bool:
        return "linkedin.com" in url

    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
    ) -> dict:
        try:
            from jobpulse.utils.safe_io import managed_browser
        except ImportError:
            logger.warning("Playwright not installed — LinkedIn adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("LinkedIn Easy Apply: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(headless=True) as (_browser, page):
                page.goto(url, timeout=30000)

                # Click Easy Apply button if present
                easy_apply_btn = page.query_selector("button.jobs-apply-button")
                if easy_apply_btn:
                    easy_apply_btn.click()

                # Fill contact fields if visible
                for field, value in [
                    ("[name='phoneNumber']", profile.get("phone", "")),
                    ("[name='email']", profile.get("email", "")),
                ]:
                    el = page.query_selector(field)
                    if el:
                        el.fill(value)

                # Upload CV if file input present
                cv_input = page.query_selector("input[type='file']")
                if cv_input and cv_path.exists():
                    cv_input.set_input_files(str(cv_path))

                screenshot_path = cv_path.parent / "linkedin_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("LinkedIn adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
