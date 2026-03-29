"""Shared Playwright browser manager with human-like action primitives.

All ATS adapters should use this module instead of calling async_playwright directly.
Playwright is imported lazily inside methods to avoid import errors when not installed.
"""

import asyncio
import random
import string
from pathlib import Path

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

CHROME_PROFILE_DIR = DATA_DIR / "chrome_profile"
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800


class BrowserManager:
    """Async context manager that owns a persistent-context Chromium browser."""

    def __init__(self) -> None:
        self._playwright: object | None = None
        self._browser: object | None = None

    async def __aenter__(self) -> "BrowserManager":
        await self.launch()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()

    async def launch(self) -> None:
        """Start Chromium with a persistent user-data dir (keeps login cookies)."""
        from playwright.async_api import async_playwright  # lazy import

        CHROME_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()

        # Use real Chrome instead of Playwright's Chromium to bypass Cloudflare
        # Real Chrome has a clean fingerprint that bot detectors don't flag
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        self._browser = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE_DIR),
            headless=False,
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            executable_path=chrome_path,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        logger.info(
            "Browser launched (profile=%s, viewport=%dx%d)",
            CHROME_PROFILE_DIR,
            VIEWPORT_WIDTH,
            VIEWPORT_HEIGHT,
        )

    async def new_page(self):
        """Return a new Page from the persistent browser context."""
        if self._browser is None:
            raise RuntimeError("BrowserManager not launched — call launch() or use as async context manager")
        page = await self._browser.new_page()
        return page

    async def close(self) -> None:
        """Gracefully close browser and Playwright."""
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
            logger.info("Browser context closed")
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def screenshot_error(self, page, job_id: str, step: str) -> Path:
        """Save an error screenshot and return the file path.

        Screenshots are saved to ``data/applications/{job_id}/error_{step}.png``.
        """
        dest_dir = DATA_DIR / "applications" / job_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"error_{step}.png"
        await page.screenshot(path=str(dest))
        logger.info("Error screenshot saved: %s", dest)
        return dest


# ---------------------------------------------------------------------------
# Human-like action primitives (standalone async functions)
# ---------------------------------------------------------------------------

async def human_type(page, selector: str, text: str) -> None:
    """Type *text* into *selector* one character at a time with human-like delays.

    Each keystroke has a random 50-150 ms pause.  There is a 5 % chance per
    character of making a typo (random letter) followed by an immediate
    backspace correction.
    """
    await page.wait_for_selector(selector)
    await page.focus(selector)

    for ch in text:
        # 5 % typo chance
        if random.random() < 0.05:
            typo = random.choice(string.ascii_lowercase)
            await page.type(selector, typo, delay=0)
            await asyncio.sleep(random.uniform(0.05, 0.15))
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.05, 0.10))

        await page.type(selector, ch, delay=0)
        await asyncio.sleep(random.uniform(0.05, 0.15))


async def human_click(page, selector: str) -> None:
    """Wait for *selector*, scroll it into view, pause, then click."""
    element = await page.wait_for_selector(selector)
    if element is not None:
        await element.scroll_into_view_if_needed()
    await asyncio.sleep(random.uniform(0.5, 1.5))
    await page.click(selector)


async def random_delay(min_s: float = 2.0, max_s: float = 8.0) -> None:
    """Async sleep for a random duration between *min_s* and *max_s* seconds."""
    duration = random.uniform(min_s, max_s)
    await asyncio.sleep(duration)


async def human_scroll(page, direction: str = "down") -> None:
    """Scroll the page by a random 200-500 px with smooth behavior."""
    pixels = random.randint(200, 500)
    delta = pixels if direction == "down" else -pixels
    await page.evaluate(
        f"window.scrollBy({{top: {delta}, behavior: 'smooth'}})"
    )
    await asyncio.sleep(random.uniform(0.3, 0.8))
