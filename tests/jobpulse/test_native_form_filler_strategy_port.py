"""Pattern test for the S10 strategy-selector port.

Pre-fix: NativeFormFiller._click_navigation hardcoded
    button[data-automation-id='bottom-navigation-next-button']  (Workday)
    button[type='submit'], input[type='submit'], ...             (generic)

…all of which are duplicated inside platform strategy methods. Post-fix
NativeFormFiller calls `get_platform_strategy(self._platform).submit_selectors()`
and `next_page_selectors()` BEFORE the generic CSS fallback, so each
ATS adapter's selectors become the source of truth.

Test catches regressions where the platform-strategy block is removed,
re-disabled, or accidentally pushed below the generic CSS fallback.

Live verification (real Greenhouse URL) lives separately — this is the
fast unit-level regression guard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.native_form_filler import NativeFormFiller


def _stub_native_form_filler(platform: str, planned_action: dict | None = None):
    """Build a NativeFormFiller with the minimum surface to exercise
    `_click_navigation`. We only stub what the method touches."""
    nff = NativeFormFiller.__new__(NativeFormFiller)
    nff._page = MagicMock()
    nff._driver = MagicMock()
    nff._planned_action = planned_action or {}
    nff._platform = platform
    nff._move_mouse_to = AsyncMock()
    nff._record_final_state_before_submit = AsyncMock()
    return nff


def _make_locator(*, count: int = 1, visible: bool = True, disabled: bool = False) -> MagicMock:
    loc = MagicMock()
    loc.count = AsyncMock(return_value=count)
    loc.is_visible = AsyncMock(return_value=visible)
    loc.is_disabled = AsyncMock(return_value=disabled)
    loc.click = AsyncMock()
    loc.first = loc  # `.first` returns self for our spy purposes
    loc.nth = MagicMock(return_value=loc)
    return loc


@pytest.mark.asyncio
async def test_click_navigation_consults_strategy_submit_selectors_before_css_fallback():
    """Acceptance: a unique selector returned by the platform strategy gets
    queried (and clicked) BEFORE the hardcoded `button[type='submit']`
    list. Pre-S10 this assertion fails because the strategy is never
    consulted."""
    nff = _stub_native_form_filler(
        platform="greenhouse",
        planned_action={"action": "done"},  # is_submit = True
    )

    # The strategy returns a sentinel selector; if the post-fix block runs,
    # it will query this exact selector via page.locator(...).
    sentinel = "button[data-strategy-port-sentinel='submit']"
    queried: list[str] = []

    def _locator(selector: str):
        queried.append(selector)
        # Sentinel is the only one that "matches"; everything else is empty
        # so the search keeps walking.
        return _make_locator(count=1 if selector == sentinel else 0)

    nff._page.locator = MagicMock(side_effect=_locator)
    nff._page.wait_for_load_state = AsyncMock()
    nff._page.evaluate = AsyncMock(return_value=[])

    # Mock the planned-action lookup path so it falls through to strategy.
    # The reasoner fallback would also fall through (we don't mock it,
    # so it raises and the code logs and continues).
    fake_strategy = MagicMock()
    fake_strategy.submit_selectors.return_value = [sentinel]
    fake_strategy.next_page_selectors.return_value = []

    with patch(
        "jobpulse.ats_adapters.strategy.get_strategy",
        return_value=fake_strategy,
    ):
        result = await nff._click_navigation(dry_run=False)

    assert result == "submitted", f"Expected click via strategy selector, got {result!r}"
    assert sentinel in queried, (
        f"Strategy selector {sentinel!r} was never queried. "
        f"Queried: {queried}. S10 port regressed — strategy.submit_selectors() "
        "must be consulted before the generic CSS fallback."
    )
    # CSS fallback's first selector should NOT have been reached.
    assert "button[type='submit']" not in queried, (
        f"CSS fallback fired even though strategy selector matched. "
        f"Queried order: {queried}"
    )


@pytest.mark.asyncio
async def test_click_navigation_uses_next_selectors_when_not_submit():
    """When planned_action.action != 'done', the next_page_selectors list
    is tried first, not submit_selectors. Guards against the
    'click Submit on a multi-page intermediate page' regression."""
    nff = _stub_native_form_filler(
        platform="workday",
        planned_action={"action": "next"},  # is_submit = False
    )

    next_sentinel = "button[data-strategy-port-sentinel='next']"
    submit_sentinel = "button[data-strategy-port-sentinel='submit']"
    queried: list[str] = []

    def _locator(selector: str):
        queried.append(selector)
        # Both sentinels would "match" in isolation; but the order in
        # `queried` will tell us which the code tried first.
        return _make_locator(count=1 if selector == next_sentinel else 0)

    nff._page.locator = MagicMock(side_effect=_locator)
    nff._page.wait_for_load_state = AsyncMock()
    nff._page.evaluate = AsyncMock(return_value=[])

    fake_strategy = MagicMock()
    fake_strategy.next_page_selectors.return_value = [next_sentinel]
    fake_strategy.submit_selectors.return_value = [submit_sentinel]

    with patch(
        "jobpulse.ats_adapters.strategy.get_strategy",
        return_value=fake_strategy,
    ):
        result = await nff._click_navigation(dry_run=False)

    assert result == "next"
    # next_sentinel must appear before submit_sentinel in the query order.
    next_idx = queried.index(next_sentinel) if next_sentinel in queried else 1_000_000
    submit_idx = queried.index(submit_sentinel) if submit_sentinel in queried else -1
    assert next_idx < submit_idx if submit_idx >= 0 else True, (
        f"next_page_selectors should be tried before submit_selectors when "
        f"is_submit=False. Order: {queried}"
    )


@pytest.mark.asyncio
async def test_click_navigation_falls_through_to_css_when_strategy_returns_empty():
    """Backwards compat: generic platform strategies return [] for both
    selector lists. The CSS-fallback block must still fire — same
    behavior as pre-S10 for unknown platforms."""
    nff = _stub_native_form_filler(platform="generic", planned_action={})

    queried: list[str] = []

    def _locator(selector: str):
        queried.append(selector)
        return _make_locator(count=0)  # nothing matches

    nff._page.locator = MagicMock(side_effect=_locator)
    nff._page.wait_for_load_state = AsyncMock()
    nff._page.evaluate = AsyncMock(return_value=[])

    fake_strategy = MagicMock()
    fake_strategy.submit_selectors.return_value = []
    fake_strategy.next_page_selectors.return_value = []

    with patch(
        "jobpulse.ats_adapters.strategy.get_strategy",
        return_value=fake_strategy,
    ):
        result = await nff._click_navigation(dry_run=False)

    # Nothing matched anywhere → method returns falsy.
    assert not result
    # CSS fallback selectors must have been queried.
    assert "button[type='submit']" in queried, (
        "CSS fallback didn't fire — generic platforms with empty strategy "
        "selectors lost the legacy fallback path."
    )
