"""LinkedIn Easy Apply adapter — with anti-detection measures."""

import random
import time
from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.ats_adapters.base import BaseATSAdapter

logger = get_logger(__name__)

# Anti-detection user agents
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _human_delay(min_s: float = 0.5, max_s: float = 2.0) -> None:
    """Random delay to mimic human interaction speed."""
    time.sleep(random.uniform(min_s, max_s))


def _human_type(page, selector: str, text: str) -> None:
    """Type text character by character with random delays (mimics human typing)."""
    el = page.query_selector(selector)
    if not el:
        return
    el.click()
    _human_delay(0.3, 0.8)
    el.fill("")  # clear first
    for char in text:
        page.keyboard.type(char, delay=random.randint(50, 150))
    _human_delay(0.5, 1.0)


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
        overrides: dict | None = None,
    ) -> dict:
        try:
            from jobpulse.utils.safe_io import managed_persistent_browser
        except ImportError:
            logger.warning("Playwright not installed — LinkedIn adapter unavailable")
            return {"success": False, "screenshot": None, "error": "Playwright not installed"}

        logger.info("LinkedIn Easy Apply: %s", url)
        try:
            from jobpulse.config import DATA_DIR

            chrome_profile = str(DATA_DIR / "chrome_profile")

            with managed_persistent_browser(
                user_data_dir=chrome_profile,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
                user_agent=random.choice(_USER_AGENTS),
                viewport={"width": 1280, "height": 800},
            ) as (_browser, page):
                page.goto(url, timeout=45000, wait_until="domcontentloaded")
                _human_delay(2, 4)  # let page fully render

                # Click Easy Apply button if present
                easy_apply_btn = page.query_selector(
                    "button.jobs-apply-button, button[aria-label*='Easy Apply']"
                )
                if easy_apply_btn:
                    easy_apply_btn.scroll_into_view_if_needed()
                    _human_delay(0.5, 1.5)
                    easy_apply_btn.click()
                    _human_delay(1.5, 3.0)  # wait for modal

                # Fill contact fields with human-like typing
                for selector, value in [
                    ("[name='phoneNumber']", profile.get("phone", "")),
                    ("[name='email']", profile.get("email", "")),
                ]:
                    if value:
                        el = page.query_selector(selector)
                        if el:
                            _human_type(page, selector, value)
                            _human_delay(0.5, 1.0)

                # Upload CV if file input present
                cv_input = page.query_selector("input[type='file']")
                if cv_input and cv_path.exists():
                    cv_input.set_input_files(str(cv_path))
                    _human_delay(1.0, 2.0)

                # Screenshot before any submission
                screenshot_path = cv_path.parent / "linkedin_screenshot.png"
                page.screenshot(path=str(screenshot_path))

                _human_delay(1.0, 2.0)

            return {"success": True, "screenshot": screenshot_path, "error": None}
        except Exception as exc:
            logger.error("LinkedIn adapter error: %s", exc)
            return {"success": False, "screenshot": None, "error": str(exc)}
