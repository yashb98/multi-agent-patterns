"""OverlayDismisser — LinkedIn 'Save this application?' overlay handler.

The cookie-banner / generic-modal / promo-popup helpers were removed in
`pipeline-bugs-S4` because the production navigator delegates to
`cookie_dismisser.dismiss` for cookie banners and never invoked the other
two helpers. Only `dismiss_linkedin_discard` is reachable from the apply
path (see `_navigator._dismiss_linkedin_discard`).
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


class OverlayDismisser:
    """Dismiss all known blocking overlays and dialogs."""

    def __init__(self, page: "Page") -> None:
        self._page = page

    # ── LinkedIn ──

    async def dismiss_linkedin_discard(self) -> bool:
        """Dismiss LinkedIn 'Save this application?' discard confirmation.""

        Public entry point — called by navigator and other modules.
        """
        page = self._page

        selectors = [
            '[data-test-easy-apply-discard-confirmation]',
            '[data-test-modal-container]',
            '.jobs-easy-apply-modal',
        ]

        for overlay_sel in selectors:
            try:
                overlay = page.locator(overlay_sel).first
                if not await overlay.is_visible():
                    continue

                # Try Discard button inside overlay
                discard_btn = overlay.locator('button:has-text("Discard")').first
                if await discard_btn.is_visible():
                    await discard_btn.click(force=True)
                    await asyncio.sleep(0.5)
                    logger.info("Dismissed LinkedIn discard overlay via Discard button")
                    return True

                # Fallback: last button in overlay (Discard is typically 2nd)
                any_btn = overlay.locator('button').last
                if await any_btn.is_visible():
                    await any_btn.click(force=True)
                    await asyncio.sleep(0.5)
                    logger.info("Dismissed LinkedIn discard overlay via last button")
                    return True
            except Exception:
                continue

        # Even broader fallback: any visible Discard button on page
        try:
            discard = page.locator('button:has-text("Discard")').first
            if await discard.is_visible():
                await discard.click(force=True)
                await asyncio.sleep(0.5)
                logger.info("Dismissed visible Discard button")
                return True
        except Exception:
            pass

        return False

