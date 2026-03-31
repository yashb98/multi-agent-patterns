"""Indeed Quick Apply adapter."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class IndeedAdapter(BaseATSAdapter):
    name: str = "indeed"

    def detect(self, url: str) -> bool:
        return "indeed" in url

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
            logger.warning("Playwright not installed — Indeed adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("Indeed Quick Apply: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            ) as (_browser, page):
                page.goto(url, timeout=30000)

                # Fill common Indeed form fields
                for selector, value in [
                    (
                        "input[name='applicant.name']",
                        f"{profile.get('first_name', '')} {profile.get('last_name', '')}",
                    ),
                    ("input[name='applicant.emailAddress']", profile.get("email", "")),
                    ("input[name='applicant.phoneNumber']", profile.get("phone", "")),
                ]:
                    el = page.query_selector(selector)
                    if el:
                        el.fill(value)

                # Upload resume
                resume_input = page.query_selector("input[type='file']")
                if resume_input and cv_path.exists():
                    resume_input.set_input_files(str(cv_path))

                screenshot_path = cv_path.parent / "indeed_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Indeed adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
