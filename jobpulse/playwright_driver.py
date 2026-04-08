"""Playwright-based driver — connects to real Chrome via CDP.

Uses Playwright's native API + human-like enhancements for form filling.
Connects to a running Chrome instance (separate profile from extension).
"""
from __future__ import annotations

import asyncio
import base64
import math
import os
import random
from typing import Any

from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from shared.logging_config import get_logger

logger = get_logger(__name__)

CDP_URL = os.environ.get("PLAYWRIGHT_CDP_URL", "http://localhost:9222")


async def _with_retry(fn, max_retries=2, delay_ms=500):
    """Retry an async function on transient errors."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                await asyncio.sleep(delay_ms / 1000)
    return {"success": False, "error": str(last_exc), "retry_count": max_retries}


def _fuzzy_match(value: str, options: list[str]) -> str | None:
    """Match value against options: exact → startswith → contains."""
    v = value.lower().strip()
    for opt in options:
        if opt.lower().strip() == v:
            return opt
    for opt in options:
        if opt.lower().strip().startswith(v):
            return opt
    for opt in options:
        if v in opt.lower().strip():
            return opt
    return None


def _get_field_gap(label_text: str = "") -> float:
    """Return delay in seconds based on label length (simulates reading)."""
    length = len(label_text)
    if length < 10:
        return 0.3 + random.uniform(0, 0.15)
    if length < 30:
        return 0.5 + random.uniform(0, 0.3)
    if length < 60:
        return 0.8 + random.uniform(0, 0.4)
    return 1.2 + random.uniform(0, 0.5)


def _scroll_delay(distance_px: float) -> float:
    """Return delay in seconds proportional to scroll distance."""
    if distance_px < 50:
        return 0.05
    if distance_px < 300:
        return 0.15 + random.uniform(0, 0.1)
    return 0.4 + random.uniform(0, 0.4)


def _bezier_points(x0, y0, x1, y1, steps=15):
    """Generate cubic Bezier curve points with randomized curvature."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.sqrt(dx * dx + dy * dy)
    if dist < 5:
        return [(x1, y1)]

    px, py = -dy / dist, dx / dist
    curve = random.uniform(30, 80) * random.choice([-1, 1])

    cx1 = x0 + dx * 0.3 + px * curve
    cy1 = y0 + dy * 0.3 + py * curve
    cx2 = x0 + dx * 0.7 + px * curve * 0.5
    cy2 = y0 + dy * 0.7 + py * curve * 0.5

    points = []
    for i in range(1, steps + 1):
        t = i / steps
        it = 1 - t
        bx = it**3 * x0 + 3 * it**2 * t * cx1 + 3 * it * t**2 * cx2 + t**3 * x1
        by = it**3 * y0 + 3 * it**2 * t * cy1 + 3 * it * t**2 * cy2 + t**3 * y1
        points.append((bx, by))
    return points


class PlaywrightDriver:
    """Form-filling driver using Playwright connected to real Chrome via CDP."""

    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    @property
    def page(self) -> "Page | None":
        """Expose the Playwright Page for native locator access."""
        return self._page

    async def _move_mouse_to(self, el) -> None:
        """Move mouse to element along a Bezier curve."""
        box = await el.bounding_box()
        if not box:
            return
        target_x = box["x"] + box["width"] / 2 + random.uniform(-3, 3)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-2, 2)

        vp = self._page.viewport_size or {"width": 1280, "height": 720}
        start_x = getattr(self, "_mouse_x", vp["width"] / 2)
        start_y = getattr(self, "_mouse_y", vp["height"] / 2)

        points = _bezier_points(start_x, start_y, target_x, target_y)
        for px, py in points:
            await self._page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.008, 0.025))

        self._mouse_x = target_x
        self._mouse_y = target_y

    async def _smart_scroll(self, el) -> None:
        """Scroll element into view and wait proportionally to distance."""
        box_before = await el.bounding_box()
        await el.scroll_into_view_if_needed()
        box_after = await el.bounding_box()
        if box_before and box_after:
            dist = abs(box_after["y"] - box_before["y"])
            delay = _scroll_delay(dist)
            await asyncio.sleep(delay)

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
        """Scan for validation errors using the 5-strategy scanner."""
        from jobpulse.form_engine.validation import scan_for_errors
        errors = await scan_for_errors(self._page)
        return {
            "success": True,
            "errors": [{"field_selector": e.field_selector, "error_message": e.error_message} for e in errors],
            "has_errors": len(errors) > 0,
            "count": len(errors),
        }

    async def fill(self, selector: str, value: str, label: str = "") -> dict:
        """Fill a text input with human-like timing and verification."""
        async def _do():
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element {selector} not found"}
            await self._smart_scroll(el)
            await asyncio.sleep(_get_field_gap(label))
            await self._move_mouse_to(el)
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            verified = actual == value or value[:10] in actual
            return {"success": True, "value_set": value, "value_verified": verified}
        return await _with_retry(_do)

    async def click(self, selector: str) -> dict:
        """Click an element with human-like mouse movement."""
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await self._smart_scroll(el)
        await self._move_mouse_to(el)
        await el.click()
        return {"success": True}

    async def select_option(self, selector: str, value: str) -> dict:
        """Select a dropdown option with fuzzy matching and verification."""
        async def _do():
            options = await self._page.eval_on_selector_all(
                f"{selector} option", "els => els.map(e => e.textContent.trim())"
            )
            match = _fuzzy_match(value, options)
            if not match:
                return {"success": False, "error": f"No match for '{value}' in {options[:5]}"}
            await self._page.select_option(selector, label=match)
            actual = await self._page.eval_on_selector(
                selector, "el => el.options[el.selectedIndex]?.text?.trim() || ''"
            )
            return {"success": True, "value_set": match, "value_verified": match.lower() == actual.lower()}
        return await _with_retry(_do)

    async def check_box(self, selector: str, checked: bool) -> dict:
        """Check or uncheck a checkbox with verification."""
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        if checked:
            await el.check()
        else:
            await el.uncheck()
        actual = await el.is_checked()
        return {"success": True, "value_set": str(checked), "value_verified": actual == checked}

    async def fill_radio(self, selector: str, value: str) -> dict:
        """Select a radio button matching the value text."""
        radios = await self._page.query_selector_all(selector)
        if not radios:
            return {"success": False, "error": "No radio elements found"}
        for radio in radios:
            label = await radio.evaluate(
                "el => el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || el.parentElement?.textContent?.trim() || ''"
            )
            if value.lower() in label.lower():
                await radio.click()
                checked = await radio.is_checked()
                return {"success": True, "value_set": label, "value_verified": checked}
        return {"success": False, "error": f"No radio matching '{value}'"}

    async def fill_date(self, selector: str, value: str) -> dict:
        """Fill a date input field."""
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.scroll_into_view_if_needed()
        await el.fill(value)
        actual = await el.evaluate("el => el.value || ''")
        return {"success": True, "value_set": value, "value_verified": value[:4] in actual}

    async def fill_autocomplete(self, selector: str, value: str) -> dict:
        """Fill an autocomplete field — type prefix, wait for suggestions, click match."""
        async def _do():
            el = await self._page.query_selector(selector)
            if not el:
                return {"success": False, "error": f"Element {selector} not found"}
            await el.scroll_into_view_if_needed()
            await el.fill("")
            await el.type(value[:5] if len(value) >= 5 else value, delay=80)
            await self._page.wait_for_timeout(1500)
            suggestions = await self._page.query_selector_all("li, [role='option']")
            for sug in suggestions:
                text = await sug.text_content()
                if text and value.lower() in text.strip().lower():
                    await sug.click()
                    return {"success": True, "value_set": text.strip(), "value_verified": True}
            await el.fill(value)
            actual = await el.evaluate("el => el.value || ''")
            return {"success": True, "value_set": value, "value_verified": actual == value, "no_suggestions": True}
        return await _with_retry(_do)

    async def fill_contenteditable(self, selector: str, value: str) -> dict:
        """Fill a contenteditable element character by character."""
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.click()
        await self._page.evaluate("document.execCommand('selectAll', false, null)")
        await self._page.evaluate("document.execCommand('delete', false, null)")
        for char in value:
            await self._page.evaluate(f"document.execCommand('insertText', false, {repr(char)})")
            await asyncio.sleep(0.03 + 0.05 * __import__('random').random())
        actual = await el.text_content() or ""
        return {"success": True, "value_set": value, "value_verified": value[:10] in actual}

    async def upload_file(self, selector: str, path: str) -> dict:
        """Upload a file to a file input."""
        el = await self._page.query_selector(selector)
        if not el:
            return {"success": False, "error": f"Element {selector} not found"}
        await el.set_input_files(path)
        return {"success": True, "value_set": path}
