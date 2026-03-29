"""Greenhouse form adapter."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class GreenhouseAdapter(BaseATSAdapter):
    name: str = "greenhouse"

    def detect(self, url: str) -> bool:
        return "greenhouse.io" in url or "boards.greenhouse" in url

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
            logger.warning("Playwright not installed — Greenhouse adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("Greenhouse form: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(headless=True) as (browser, page):
                page.goto(url, timeout=30000)

                # Greenhouse standard field selectors
                for selector, value in [
                    ("#first_name", profile.get("first_name", "")),
                    ("#last_name", profile.get("last_name", "")),
                    ("#email", profile.get("email", "")),
                    ("#phone", profile.get("phone", "")),
                    ("#resume_text", ""),  # may not be present
                ]:
                    el = page.query_selector(selector)
                    if el and value:
                        el.fill(value)

                # Upload CV
                resume_input = page.query_selector("input[type='file'][id*='resume']")
                if resume_input and cv_path.exists():
                    resume_input.set_input_files(str(cv_path))

                # Upload cover letter if provided
                if cover_letter_path and cover_letter_path.exists():
                    cl_input = page.query_selector("input[type='file'][id*='cover']")
                    if cl_input:
                        cl_input.set_input_files(str(cover_letter_path))

                # Fill custom answers
                for field_id, answer in custom_answers.items():
                    el = page.query_selector(f"#{field_id}")
                    if el:
                        el.fill(str(answer))

                screenshot_path = cv_path.parent / "greenhouse_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Greenhouse adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
