"""Executes PageAction instructions on the live page.

Translates the reasoner's structured actions into Playwright calls:
overlay dismissal → field fills → checkbox checks → advance button click.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, TypedDict

from dataclasses import dataclass, field as dc_field

from shared.logging_config import get_logger

from jobpulse.page_analysis.page_reasoner import PageAction

logger = get_logger(__name__)


class FillFailure(TypedDict):
    label: str
    expected: str
    actual: str


@dataclass
class ExecutorResult:
    """Structured outcome of a NavigationActionExecutor.execute() call.

    Returned to callers (FormNavigator._phase_act, AuthHandler.handle_login/signup)
    so they can act on per-fill failures without reverse-engineering from snapshots.
    """
    fills_attempted: int = 0
    fills_verified: int = 0
    fills_failed: list[FillFailure] = dc_field(default_factory=list)
    clicks_attempted: int = 0
    advance_clicked: bool = False

    def record_fill_failure(self, label: str, expected: str, actual: str) -> None:
        entry: FillFailure = {"label": label, "expected": expected, "actual": actual}
        self.fills_failed.append(entry)

    @property
    def has_failures(self) -> bool:
        return bool(self.fills_failed)


_PROFILE_REF = re.compile(r"^FROM_PROFILE:(\w+)$")


class NavigationActionExecutor:
    """Executes a PageAction's instructions on a Playwright page."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def execute(
        self, action: PageAction, profile: dict[str, str]
    ) -> ExecutorResult:
        """Execute the full action and return a structured outcome."""
        result = ExecutorResult()

        if action.action == "click_element":
            result.clicks_attempted += 1
            if await self._try_click_by_text(action.target_text):
                return result
            if action.overlays_to_dismiss:
                await self._dismiss_overlays(action.overlays_to_dismiss)
                if await self._try_click_by_text(action.target_text):
                    return result
            logger.warning("Could not find clickable element: '%s'",
                           (action.target_text or "")[:40])
            return result

        if action.overlays_to_dismiss:
            await self._dismiss_overlays(action.overlays_to_dismiss)

        if action.action == "dismiss_overlay":
            if action.target_text:
                result.clicks_attempted += 1
                await self._click_by_text(action.target_text)
            return result

        if action.action in ("fill_and_advance", "login", "signup"):
            for fill in action.field_fills:
                await self._execute_fill(fill, profile, result)
            if action.advance_button:
                await asyncio.sleep(0.3)
                await self._click_by_text(action.advance_button)
                result.advance_clicked = True
                result.clicks_attempted += 1

        return result

    _PROMO_WORDS = {"premium", "upgrade", "subscribe", "buy", "purchase", "reactivate", "activate", "trial", "pro ", "pricing"}

    async def _dismiss_overlays(self, overlay_buttons: list[str]) -> None:
        url_before = self._page.url
        # Try standard close buttons first, regardless of LLM suggestion
        for close_text in ("Not now", "No thanks", "Dismiss", "Close", "Got it", "Maybe later", "Skip"):
            try:
                for role in ("button", "link"):
                    loc = self._page.get_by_role(role, name=close_text, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Dismissed overlay via standard close: '%s'", close_text)
                        await asyncio.sleep(0.5)
                        return
            except Exception:
                continue
        # Try aria-label close/dismiss button (X icon)
        try:
            loc = self._page.locator("[aria-label*=close i], [aria-label*=dismiss i]").first
            if await loc.is_visible():
                await loc.click()
                logger.info("Dismissed overlay via aria-label close button")
                await asyncio.sleep(0.5)
                return
        except Exception:
            pass
        # Fall back to LLM-suggested buttons, but skip promotional links
        for text in overlay_buttons:
            if any(w in text.lower() for w in self._PROMO_WORDS):
                logger.debug("Skipping promotional overlay text: '%s'", text[:40])
                continue
            try:
                for role in ("button", "link"):
                    loc = self._page.get_by_role(role, name=text, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Dismissed overlay: '%s'", text)
                        await asyncio.sleep(0.5)
                        if self._page.url != url_before:
                            logger.warning("Overlay click navigated away — going back")
                            await self._page.goto(url_before, wait_until="domcontentloaded")
                            await asyncio.sleep(1)
                        return
            except Exception as exc:
                logger.debug("Overlay dismiss failed for '%s': %s", text, exc)

    async def _execute_fill(
        self, fill: dict[str, str], profile: dict[str, str], result: ExecutorResult,
    ) -> None:
        label = fill.get("label", "")
        value = fill.get("value", "")
        method = fill.get("method", "fill")

        if method == "skip":
            logger.debug("Skipping field: %s", label)
            return

        result.fills_attempted += 1
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
                if not await loc.count():
                    loc = self._page.get_by_placeholder(label, exact=False)
                if await loc.count():
                    await loc.first.fill(value)
                    if await self._verify_fill(loc.first, value):
                        result.fills_verified += 1
                        logger.info("Filled %s (verified)", label[:30])
                    else:
                        # one retry with a small wait — covers React controlled
                        # inputs that revert and autocompletes that need time
                        await asyncio.sleep(0.2)
                        await loc.first.fill(value)
                        if await self._verify_fill(loc.first, value):
                            result.fills_verified += 1
                            logger.info("Filled %s (verified after retry)", label[:30])
                        else:
                            actual = await self._safe_input_value(loc.first)
                            result.record_fill_failure(label, value, actual)
                            logger.warning(
                                "Fill mismatch for '%s': expected=%r actual=%r",
                                label[:30], value[:40], actual[:40],
                            )
                else:
                    logger.warning("No locator for fill: %s", label[:40])

        except Exception as exc:
            logger.warning("Fill failed for '%s' (%s): %s", label[:30], method, exc)

    @staticmethod
    async def _safe_input_value(locator: Any) -> str:
        try:
            return (await locator.input_value()) or ""
        except Exception:
            return ""

    async def _verify_fill(self, locator: Any, expected: str) -> bool:
        actual = await self._safe_input_value(locator)
        if not expected:
            return True
        # Three-way match — same pattern NativeFormFiller uses (line 879-883)
        norm_e = expected.strip().lower()
        norm_a = actual.strip().lower()
        return bool(norm_a) and (
            norm_e == norm_a or norm_e in norm_a or norm_a in norm_e
        )

    async def _try_click_by_text(self, text: str) -> bool:
        """Try to click an element by text, return True if clicked."""
        if not text:
            return False
        candidates = [text]
        tl = text.lower().strip()
        if tl == "apply":
            candidates.extend(["Apply on company website", "Apply now", "Apply on"])
        for candidate in candidates:
            for role in ("button", "link"):
                try:
                    loc = self._page.get_by_role(role, name=candidate, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Clicked %s: '%s'", role, candidate[:40])
                        await asyncio.sleep(1.0)
                        return True
                except Exception:
                    continue
        return False

    async def _click_by_text(self, text: str) -> None:
        if not text:
            return
        candidates = [text]
        tl = text.lower().strip()
        if tl == "apply":
            candidates.extend(["Apply on company website", "Apply now", "Apply on"])
        for candidate in candidates:
            for role in ("button", "link"):
                try:
                    loc = self._page.get_by_role(role, name=candidate, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click()
                        logger.info("Clicked %s: '%s'", role, candidate[:40])
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
