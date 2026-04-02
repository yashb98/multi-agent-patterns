"""Lever form adapter."""

from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)


class LeverAdapter(BaseATSAdapter):
    name: str = "lever"

    def detect(self, url: str) -> bool:
        return "lever.co" in url or "jobs.lever" in url

    def fill_and_submit(
        self,
        url: str,
        cv_path: Path,
        cover_letter_path: Path | None,
        profile: dict,
        custom_answers: dict,
        overrides: dict | None = None,
        dry_run: bool = False,
    ) -> dict:
        try:
            from jobpulse.utils.safe_io import managed_browser
        except ImportError:
            logger.warning("Playwright not installed — Lever adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("Lever form: %s", url)
        try:
            from jobpulse.utils.safe_io import managed_browser

            with managed_browser(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
            ) as (_browser, page):
                page.goto(url, timeout=30000)

                # Lever standard field selectors
                for selector, value in [
                    (
                        "input[name='name']",
                        f"{profile.get('first_name', '')} {profile.get('last_name', '')}",
                    ),
                    ("input[name='email']", profile.get("email", "")),
                    ("input[name='phone']", profile.get("phone", "")),
                    ("input[name='org']", ""),  # current company — leave blank
                    ("input[name='urls[LinkedIn]']", profile.get("linkedin", "")),
                    ("input[name='urls[GitHub]']", profile.get("github", "")),
                    ("input[name='urls[Portfolio]']", profile.get("portfolio", "")),
                ]:
                    el = page.query_selector(selector)
                    if el and value:
                        el.fill(value)

                # Upload resume
                resume_input = page.query_selector("input[type='file'][name='resume']")
                if resume_input and cv_path.exists():
                    resume_input.set_input_files(str(cv_path))

                # Upload cover letter
                if cover_letter_path and cover_letter_path.exists():
                    cl_input = page.query_selector("input[type='file'][name='coverLetter']")
                    if cl_input:
                        cl_input.set_input_files(str(cover_letter_path))

                # Answer screening questions via shared get_answer() engine
                job_context = custom_answers.get("_job_context") if custom_answers else None
                self.answer_screening_questions(page, job_context)

                screenshot_path = cv_path.parent / "lever_screenshot.png"
                page.screenshot(path=str(screenshot_path))

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("Lever adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
