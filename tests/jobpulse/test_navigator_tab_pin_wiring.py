"""Wiring test: navigator respects the explicit tab pick in _phase_observe.

Live regression on 2026-05-05 (JPMC Strategic Testing): three tabs were
open in Chrome (Indeed JD, JPMC HCM JD, JPMC HCM apply form). The session
correctly picked the apply form tab via _find_in_progress_apply_tab and
attached via prefer_url. But the orchestrator's _phase_observe blindly
switched to pages[-1] on the first observe step, clobbering the
attachment and grounding the page reasoner on the wrong tab. The agent
then looped on "wait_human" because it was reading the Indeed JD page
instead of the logged-in apply form.

Fix: _phase_observe now consults two locks before auto-switching:
  1. _should_auto_switch_tab — False if driver._attached_existing_url
     (the attachment was deliberate) OR current page is already on an
     apply-path URL (we're already where we want to be).
  2. _pick_target_tab — when a switch IS warranted, prefer pages on
     apply-path URLs over pages[-1].
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_navigator(*, attached_existing_url: bool = False):
    """Construct a FormNavigator without exercising __init__.

    `driver` is a @property that goes through `self._orch.driver`, so we
    stub a fake orch with a driver child object.
    """
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    nav = FormNavigator.__new__(FormNavigator)
    nav._orch = SimpleNamespace(driver=SimpleNamespace(
        _attached_existing_url=attached_existing_url,
    ))
    return nav


def _page(url: str, *, closed: bool = False) -> Any:  # type: ignore[name-defined]
    p = MagicMock()
    p.url = url
    p.is_closed = MagicMock(return_value=closed)
    return p


def test_is_apply_path_url_matches_canonical_patterns():
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    matches = [
        "https://jpmc.fa.oraclecloud.com/.../job/210707149/apply/section/1",
        "https://boards.greenhouse.io/foo/jobs/12345/apply",
        "https://example.com/candidate/start",
        "https://example.com/application?token=abc",
    ]
    misses = [
        "https://uk.indeed.com/viewjob?jk=abc",
        "https://example.com/career/job/210707149",
        "",
    ]
    for u in matches:
        assert FormNavigator._is_apply_path_url(u), f"should match: {u}"
    for u in misses:
        assert not FormNavigator._is_apply_path_url(u), f"should NOT match: {u}"


def test_should_auto_switch_blocked_when_driver_attached_existing_url():
    """When prefer_url match attached the driver, never auto-switch."""
    nav = _make_navigator(attached_existing_url=True)
    current = _page("https://uk.indeed.com/viewjob?jk=abc")  # not an apply page
    assert nav._should_auto_switch_tab(current) is False


def test_should_auto_switch_blocked_when_current_is_apply_path():
    """If current is already on an apply page, never auto-switch — even if
    other tabs exist (the most likely scenario is stale Indeed JD tab in
    the background)."""
    nav = _make_navigator()
    current = _page("https://jpmc.fa.oraclecloud.com/.../apply/section/1")
    assert nav._should_auto_switch_tab(current) is False


def test_should_auto_switch_allowed_when_current_is_jd_page():
    """If current is on a JD/listing page, allow follow-the-redirect to a
    new ATS tab (the SSO chain we want to capture)."""
    nav = _make_navigator()
    current = _page("https://uk.indeed.com/viewjob?jk=abc")
    assert nav._should_auto_switch_tab(current) is True


def test_pick_target_tab_prefers_apply_path_over_pages_last():
    """The bug: when the apply-form tab is at pages[1] and a stale JD tab
    is at pages[-1], pages[-1] would have been chosen. Fix picks the
    apply-path one."""
    nav = _make_navigator()
    current = _page("https://uk.indeed.com/viewjob?jk=abc")
    apply_tab = _page("https://jpmc.fa.oraclecloud.com/.../apply/section/1")
    indeed_jd = _page("https://uk.indeed.com/viewjob?jk=abc")  # same as current
    extra_jd = _page("https://jpmc.fa.oraclecloud.com/.../job/210707149")

    pages = [current, apply_tab, extra_jd]
    target = nav._pick_target_tab(pages, current)
    assert target is apply_tab


def test_pick_target_tab_skips_closed_pages():
    nav = _make_navigator()
    current = _page("https://uk.indeed.com/viewjob?jk=abc")
    closed_apply = _page("https://example.com/apply/section/1", closed=True)
    open_apply = _page("https://example.com/apply/section/2")
    pages = [current, closed_apply, open_apply]
    target = nav._pick_target_tab(pages, current)
    assert target is open_apply


def test_pick_target_tab_returns_none_when_only_current():
    nav = _make_navigator()
    current = _page("https://example.com/foo")
    target = nav._pick_target_tab([current], current)
    assert target is None


def test_pick_target_tab_falls_back_to_newest_when_no_apply_path():
    """If no candidate is on an apply path, fall back to newest non-current
    (the original heuristic, scoped to non-current/non-closed)."""
    nav = _make_navigator()
    current = _page("https://example.com/foo")
    a = _page("https://example.com/bar")
    b = _page("https://example.com/baz")
    target = nav._pick_target_tab([current, a, b], current)
    assert target is b
