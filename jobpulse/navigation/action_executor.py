"""Executes PageAction instructions on the live page.

Translates the reasoner's structured actions into Playwright calls:
overlay dismissal → field fills → checkbox checks → advance button click.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.page_analysis.page_reasoner import PageAction

logger = get_logger(__name__)

_PROFILE_REF = re.compile(r"^FROM_PROFILE:(\w+)$")


class NavigationActionExecutor:
    """Executes a PageAction's instructions on a Playwright page."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def execute(self, action: PageAction, profile: dict[str, str]) -> None:
        """Execute the full action: dismiss overlays → fill fields → click advance."""
        if action.overlays_to_dismiss:
            await self._dismiss_overlays(action.overlays_to_dismiss)

        if action.action == "click_element":
            await self._click_by_text(action.target_text)
            return

        if action.action == "dismiss_overlay":
            if action.target_text:
                await self._click_by_text(action.target_text)
            return

        if action.action in ("fill_and_advance", "login", "signup"):
            for fill in action.field_fills:
                await self._execute_fill(fill, profile)
            if action.advance_button:
                await asyncio.sleep(0.3)
                await self._click_by_text(action.advance_button)

    async def _dismiss_overlays(self, overlay_buttons: list[str]) -> None:
        for text in overlay_buttons:
            try:
                for role in ("button", "link"):
                    loc = self._page.get_by_role(role, name=text, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Dismissed overlay: '%s'", text)
                        await asyncio.sleep(0.5)
                        break
            except Exception as exc:
                logger.debug("Overlay dismiss failed for '%s': %s", text, exc)

    async def _execute_fill(self, fill: dict[str, str], profile: dict[str, str]) -> None:
        label = fill.get("label", "")
        value = fill.get("value", "")
        method = fill.get("method", "fill")

        if method == "skip":
            logger.debug("Skipping field: %s", label)
            return

        value = self._resolve_value(value, profile)

        try:
            if method == "check_label":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    checked = await loc.first.is_checked()
                    if not checked:
                        await loc.first.check()
                        logger.info("Checked: %s", label[:50])
                else:
                    loc = self._page.get_by_text(label, exact=False)
                    if await loc.count():
                        await loc.first.click()
                        logger.info("Clicked label text: %s", label[:50])

            elif method == "check_input":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.check()
                    logger.info("Checked input: %s", label[:50])

            elif method == "select":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.select_option(value)
                    logger.info("Selected %s = %s", label[:30], value[:30])

            elif method == "fill":
                loc = self._page.get_by_label(label, exact=False)
                if await loc.count():
                    await loc.first.fill(value)
                    logger.info("Filled %s", label[:30])
                else:
                    loc = self._page.get_by_placeholder(label, exact=False)
                    if await loc.count():
                        await loc.first.fill(value)
                        logger.info("Filled (placeholder) %s", label[:30])

        except Exception as exc:
            logger.warning("Fill failed for '%s' (%s): %s", label[:30], method, exc)

    async def _click_by_text(self, text: str) -> None:
        if not text:
            return
        for role in ("button", "link"):
            try:
                loc = self._page.get_by_role(role, name=text, exact=False)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    logger.info("Clicked %s: '%s'", role, text[:40])
                    await asyncio.sleep(1.0)
                    return
            except Exception:
                continue
        logger.warning("Could not find clickable element: '%s'", text[:40])

    @staticmethod
    def _resolve_value(value: str, profile: dict[str, str]) -> str:
        m = _PROFILE_REF.match(value)
        if m:
            key = m.group(1)
            return profile.get(key, "")
        return value
