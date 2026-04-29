"""Deterministic Playwright wait conditions — replace all hardcoded asyncio.sleep().

Every wait function returns True on success, False on timeout.
All timeouts are configurable via parameters with sensible defaults.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


async def wait_for_page_stable(page: "Page", timeout_ms: int = 10000) -> bool:
    """Wait until network idle AND no significant DOM mutations for 300ms.

    Uses Playwright's built-in networkidle first, then polls for DOM stability.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        logger.debug("wait_for_page_stable: networkidle timeout, continuing anyway")

    # Poll for DOM stability: no new elements matching interactive selectors
    poll_interval_ms = 300
    stable_for_ms = 0
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    prev_counts: dict[str, int] | None = None
    stability_threshold_ms = 500

    while asyncio.get_running_loop().time() < deadline:
        try:
            counts = await page.evaluate("""() => {
                return {
                    inputs: document.querySelectorAll('input, select, textarea').length,
                    buttons: document.querySelectorAll('button, [role="button"]').length,
                    dialogs: document.querySelectorAll('[role="dialog"], [aria-modal="true"]').length,
                };
            }""")
        except Exception:
            return False

        if prev_counts is not None:
            changed = any(counts[k] != prev_counts[k] for k in counts)
            if not changed:
                stable_for_ms += poll_interval_ms
                if stable_for_ms >= stability_threshold_ms:
                    logger.debug("wait_for_page_stable: DOM stable after %dms", stable_for_ms)
                    return True
            else:
                stable_for_ms = 0

        prev_counts = counts
        await asyncio.sleep(poll_interval_ms / 1000)

    logger.debug("wait_for_page_stable: timeout after %dms", timeout_ms)
    return False


async def wait_for_modal_open(page: "Page", timeout_ms: int = 8000) -> bool:
    """Wait for a modal/dialog to appear."""
    try:
        await page.wait_for_selector(
            '[role="dialog"]:not([aria-hidden="true"]), [aria-modal="true"]:not([aria-hidden="true"])',
            state="visible",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def wait_for_form_hydrated(
    page: "Page", min_fields: int = 2, timeout_ms: int = 15000
) -> bool:
    """Poll until at least *min_fields* interactive form fields are visible.

    Critical for Workday and other SPAs that lazy-load form fields.
    """
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    poll_interval = 0.5

    while asyncio.get_running_loop().time() < deadline:
        try:
            count = await page.evaluate(
                """() => document.querySelectorAll(
                    'input:visible, select:visible, textarea:visible, [contenteditable="true"]:visible'
                ).length"""
            )
            if count >= min_fields:
                logger.debug("wait_for_form_hydrated: %d fields found", count)
                return True
        except Exception:
            pass
        await asyncio.sleep(poll_interval)

    logger.debug("wait_for_form_hydrated: timeout, < %d fields", min_fields)
    return False


async def wait_for_navigation_complete(
    page: "Page", expected_url_pattern: str | None = None, timeout_ms: int = 15000
) -> bool:
    """Wait for URL change (if expected) + networkidle + DOM stable."""
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    if expected_url_pattern:
        while asyncio.get_running_loop().time() < deadline:
            if expected_url_pattern in page.url:
                break
            await asyncio.sleep(0.1)
        else:
            logger.debug("wait_for_navigation_complete: URL pattern timeout")
            return False

    # Now wait for page stable
    remaining_ms = int((deadline - asyncio.get_running_loop().time()) * 1000)
    if remaining_ms <= 0:
        return False

    return await wait_for_page_stable(page, timeout_ms=remaining_ms)


async def wait_for_element_stable(
    page: "Page", selector: str, timeout_ms: int = 5000
) -> bool:
    """Wait until element exists, is visible, and bounding box is stable."""
    try:
        loc = page.locator(selector).first
        await loc.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return False

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    stable_for = 0
    prev_bbox = None

    while asyncio.get_running_loop().time() < deadline:
        try:
            bbox = await loc.bounding_box()
        except Exception:
            return False

        if bbox and prev_bbox:
            moved = (
                abs(bbox["x"] - prev_bbox["x"]) > 2
                or abs(bbox["y"] - prev_bbox["y"]) > 2
                or abs(bbox["width"] - prev_bbox["width"]) > 2
                or abs(bbox["height"] - prev_bbox["height"]) > 2
            )
            if not moved:
                stable_for += 200
                if stable_for >= 400:
                    return True
            else:
                stable_for = 0

        prev_bbox = bbox
        await asyncio.sleep(0.2)

    return False


async def wait_for_any_selector(
    page: "Page", selectors: list[str], timeout_ms: int = 8000
) -> str | None:
    """Wait for any of the CSS selectors to match a visible element.

    Returns the matched selector, or None on timeout.
    """
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    while asyncio.get_running_loop().time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible():
                    return sel
            except Exception:
                continue
        await asyncio.sleep(0.2)

    return None


async def wait_for_url_change(
    page: "Page", from_url: str | None = None, timeout_ms: int = 10000
) -> bool:
    """Wait until the page URL differs from *from_url*."""
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    while asyncio.get_running_loop().time() < deadline:
        if from_url is None or page.url != from_url:
            return True
        await asyncio.sleep(0.1)

    return False


async def wait_for_apply_button_visible(
    page: "Page", timeout_ms: int = 10000
) -> bool:
    """Poll until an apply-like button is visible on the page."""
    from jobpulse.application_orchestrator_pkg._navigator import ApplyButtonPatterns

    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    patterns = ApplyButtonPatterns()

    while asyncio.get_running_loop().time() < deadline:
        try:
            found = await page.evaluate(
                f"""(patterns) => {{
                    const all = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    for (const el of all) {{
                        const text = (el.textContent || el.value || '').trim().toLowerCase();
                        if (!text) continue;
                        for (const p of patterns) {{
                            if (text.includes(p.toLowerCase())) return true;
                        }}
                    }}
                    return false;
                }}""",
                patterns.primary + patterns.secondary,
            )
            if found:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.25)

    return False
