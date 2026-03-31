"""Workday wizard adapter."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class WorkdayAdapter(BaseATSAdapter):
    name: str = "workday"

    def detect(self, url: str) -> bool:
        return "myworkdayjobs.com" in url or "workday" in url

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
            logger.warning("Playwright not installed — Workday adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("Workday wizard: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            ) as (_browser, page):
                page.goto(url, timeout=30000)

                # Workday uses React with data-automation-id attributes
                for automation_id, value in [
                    ("legalNameSection_firstName", profile.get("first_name", "")),
                    ("legalNameSection_lastName", profile.get("last_name", "")),
                    ("email", profile.get("email", "")),
                    ("phone-number", profile.get("phone", "")),
                    ("addressSection_addressLine1", profile.get("location", "")),
                ]:
                    el = page.query_selector(f"[data-automation-id='{automation_id}'] input")
                    if el and value:
                        el.fill(value)

                # Upload resume via Workday file upload widget
                resume_btn = page.query_selector("[data-automation-id='file-upload-drop-zone']")
                if resume_btn and cv_path.exists():
                    # Trigger file input hidden inside Workday's upload zone
                    file_input = page.query_selector("input[type='file']")
                    if file_input:
                        file_input.set_input_files(str(cv_path))

                screenshot_path = cv_path.parent / "workday_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Workday adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
