"""Playwright-based driver — connects to real Chrome via CDP.

Uses Playwright's native API + human-like enhancements for form filling.
Connects to a running Chrome instance (separate profile from extension).
"""
from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from shared.logging_config import get_logger

logger = get_logger(__name__)

CDP_URL = os.environ.get("PLAYWRIGHT_CDP_URL", "http://localhost:9222")


class PlaywrightDriver:
    """Form-filling driver using Playwright connected to real Chrome via CDP."""

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def connect(self, cdp_url: str | None = None) -> None:
        """Connect to Chrome via CDP. Call before any other method."""
        url = cdp_url or CDP_URL
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.connect_over_cdp(url)
        except Exception as exc:
            await self._pw.stop()
            raise ConnectionError(
                f"Cannot connect to Chrome at {url}. "
                "Start Chrome with: python -m jobpulse.runner chrome-pw"
            ) from exc
        self._context = self._browser.contexts[0]
        self._page = await self._context.new_page()
        logger.info("PlaywrightDriver connected to Chrome at %s", url)

    async def close(self) -> None:
        """Close the tab and disconnect."""
        if self._page:
            await self._page.close()
            self._page = None
        if self._pw:
            await self._pw.stop()
            self._pw = None
        self._browser = None
        self._context = None

    async def navigate(self, url: str) -> dict:
        """Navigate to URL, wait for load, return snapshot."""
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._page.wait_for_load_state("networkidle", timeout=15000)
        snapshot = await self.get_snapshot()
        return {"success": True, "snapshot": snapshot}

    async def screenshot(self) -> dict:
        """Capture visible page as base64 PNG."""
        buf = await self._page.screenshot(type="png")
        return {"success": True, "data": base64.b64encode(buf).decode()}

    async def get_snapshot(self, **kwargs) -> dict:
        """Scan DOM for form fields — returns same shape as extension snapshots."""
        return await self._page.evaluate("""() => {
            const fields = [];
            document.querySelectorAll('input, select, textarea, [contenteditable="true"]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;
                fields.push({
                    selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : el.tagName.toLowerCase()),
                    type: el.type || el.tagName.toLowerCase(),
                    value: el.value || '',
                    label: '',
                    required: el.required || el.getAttribute('aria-required') === 'true',
                });
            });
            return { url: location.href, title: document.title, fields };
        }""")

    async def scan_validation_errors(self) -> dict:
        """Scan for validation errors using the validation module."""
        from jobpulse.form_engine.validation import scan_for_errors
        errors = await scan_for_errors(self._page)
        return {
            "success": True,
            "errors": [{"selector": e.field_selector, "message": e.error_message} for e in errors],
        }

    # --- Fill method stubs (implemented in Task 6) ---

    async def fill(self, selector: str, value: str) -> dict:
        """Fill a text input. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.fill — see Task 6")

    async def click(self, selector: str) -> dict:
        """Click an element. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.click — see Task 6")

    async def select_option(self, selector: str, value: str) -> dict:
        """Select a dropdown option. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.select_option — see Task 6")

    async def check_box(self, selector: str, checked: bool) -> dict:
        """Check/uncheck a checkbox. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.check_box — see Task 6")

    async def fill_radio(self, selector: str, value: str) -> dict:
        """Select a radio option. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.fill_radio — see Task 6")

    async def fill_date(self, selector: str, value: str) -> dict:
        """Fill a date field. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.fill_date — see Task 6")

    async def fill_autocomplete(self, selector: str, value: str) -> dict:
        """Fill an autocomplete field. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.fill_autocomplete — see Task 6")

    async def fill_contenteditable(self, selector: str, value: str) -> dict:
        """Fill a contenteditable element. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.fill_contenteditable — see Task 6")

    async def upload_file(self, selector: str, path: str) -> dict:
        """Upload a file. Stub — implemented in Task 6."""
        raise NotImplementedError("PlaywrightDriver.upload_file — see Task 6")
