"""OverlayDismisser — single source of truth for dismissing blocking overlays.

Consolidates LinkedIn discard dialogs, cookie banners, and generic modals
that were previously copy-pasted across _navigator.py and native_form_filler.py.
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

    async def dismiss_all(self) -> int:
        """Run all dismissal strategies. Returns count of overlays dismissed."""
        dismissed = 0
        if await self.dismiss_linkedin_discard():
            dismissed += 1
        if await self._dismiss_cookie_banner():
            dismissed += 1
        if await self._dismiss_generic_modal():
            dismissed += 1
        if await self._dismiss_promo_popup():
            dismissed += 1
        return dismissed

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

    # ── Cookie Banners ──

    async def _dismiss_cookie_banner(self) -> bool:
        """Dismiss common cookie consent banners."""
        page = self._page

        # Ordered by specificity: platform-specific → generic
        strategies = [
            # LinkedIn
            {
                "container": '[data-test-global-toast]',
                "accept": 'button:has-text("Accept")',
            },
            # Generic OneTrust / Cookiebot
            {
                "container": '#onetrust-banner-sdk, .cookie-banner, #cookiebanner',
                "accept": 'button:has-text("Accept"), button:has-text("Allow"), button:has-text("Agree"), button:has-text("Continue")',
            },
            # Workday
            {
                "container": '[data-automation-id="cookieBanner"]',
                "accept": 'button',
            },
            # Greenhouse
            {
                "container": '.cookie-notice',
                "accept": 'a, button',
            },
            # Very generic
            {
                "container": '[aria-label*="cookie" i], [class*="cookie" i], [id*="cookie" i]',
                "accept": 'button, a[role="button"]',
            },
        ]

        for strat in strategies:
            try:
                container = page.locator(strat["container"]).first
                if not await container.is_visible():
                    continue

                btn = container.locator(strat["accept"]).first
                if await btn.is_visible():
                    text = (await btn.text_content() or "").lower()
                    # Only click if text looks like an accept/allow/agree/continue action
                    if any(w in text for w in ("accept", "allow", "agree", "continue", "ok", "got it", "dismiss")):
                        await btn.click(force=True)
                        await asyncio.sleep(0.3)
                        logger.info("Dismissed cookie banner: '%s'", text[:40])
                        return True
            except Exception:
                continue

        return False

    # ── Generic Modals ──

    async def _dismiss_generic_modal(self) -> bool:
        """Dismiss unexpected modal dialogs (not LinkedIn-specific)."""
        page = self._page

        # Look for visible modal dialogs without expected application content
        try:
            modals = await page.locator('[role="dialog"]:not([aria-hidden="true"])').all()
        except Exception:
            return False

        for modal in modals:
            try:
                if not await modal.is_visible():
                    continue

                # Try to find close / dismiss / cancel / skip button
                dismissers = [
                    'button:has-text("Close")',
                    'button:has-text("Dismiss")',
                    'button:has-text("Cancel")',
                    'button:has-text("Skip")',
                    'button:has-text("Not now")',
                    '[aria-label="Close"]',
                    '[aria-label="Dismiss"]',
                ]
                for sel in dismissers:
                    try:
                        btn = modal.locator(sel).first
                        if await btn.is_visible():
                            await btn.click(force=True)
                            await asyncio.sleep(0.3)
                            logger.info("Dismissed generic modal via %s", sel)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        return False

    # ── Promo / Sign-up Popups ──

    async def _dismiss_promo_popup(self) -> bool:
        """Dismiss promo, newsletter, or sign-up popups."""
        page = self._page

        selectors = [
            '[class*="popup"]:not([role="dialog"])',
            '[class*="modal"]:not([role="dialog"])',
            '[class*="overlay"]',
            '[class*="newsletter"]',
        ]

        for sel in selectors:
            try:
                popup = page.locator(sel).first
                if not await popup.is_visible():
                    continue

                # Check if it has a close button
                close_btn = popup.locator(
                    'button[class*="close"], [class*="close"], [aria-label="Close"]'
                ).first
                if await close_btn.is_visible():
                    await close_btn.click(force=True)
                    await asyncio.sleep(0.3)
                    logger.info("Dismissed promo popup")
                    return True
            except Exception:
                continue

        return False
