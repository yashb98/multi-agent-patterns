# Task 5: PlaywrightDriver — Core (Connect, Navigate, Screenshot, Snapshot)

**Files:**
- Create: `jobpulse/playwright_driver.py`
- Test: `tests/jobpulse/test_playwright_driver.py`

**Why:** The core driver that connects to real Chrome via CDP and implements navigation, screenshots, and snapshots. Fill methods are added in Tasks 6-7.

**Dependencies:** Task 1 (DriverProtocol)

---

- [ ] **Step 1: Create PlaywrightDriver with connect + close**

```python
"""Playwright-based driver — connects to real Chrome via CDP.

Uses Playwright's native API + human-like enhancements for form filling.
Connects to a running Chrome instance (separate profile from extension).
"""
from __future__ import annotations

import asyncio
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
```

- [ ] **Step 2: Add navigate, screenshot, get_snapshot**

```python
    async def navigate(self, url: str) -> dict:
        """Navigate to URL, wait for load, return snapshot."""
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self._page.wait_for_load_state("networkidle", timeout=15000)
        snapshot = await self.get_snapshot()
        return {"success": True, "snapshot": snapshot}

    async def screenshot(self) -> dict:
        """Capture visible page as base64 PNG."""
        import base64
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
```

- [ ] **Step 3: Write test**

```python
"""tests/jobpulse/test_playwright_driver.py"""
import pytest
from jobpulse.playwright_driver import PlaywrightDriver
from jobpulse.driver_protocol import DriverProtocol

def test_playwright_driver_is_protocol_compatible():
    """PlaywrightDriver has all DriverProtocol methods."""
    required = [
        "navigate", "fill", "click", "select_option", "check_box",
        "fill_radio", "fill_date", "fill_autocomplete",
        "fill_contenteditable", "upload_file", "screenshot",
        "get_snapshot", "scan_validation_errors", "close",
    ]
    for method in required:
        assert hasattr(PlaywrightDriver, method), f"Missing method: {method}"
```

Note: This test will fail until Tasks 6-7 add fill methods. For now commit with the core methods and a partial test.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/playwright_driver.py tests/jobpulse/test_playwright_driver.py
git commit -m "feat: PlaywrightDriver core — CDP connect, navigate, screenshot, snapshot"
```
