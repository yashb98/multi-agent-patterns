"""Tests for LinkedIn adapter helpers — login wall detection and DOM capture.

All Playwright interactions mocked with MagicMock.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jobpulse.ats_adapters.linkedin import (
    _dump_page_context,
    _fill_location_typeahead,
    _handle_login_wall,
)


@pytest.fixture
def mock_page():
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/4395143521/"
    page.title.return_value = "Gousto | LinkedIn"
    page.query_selector.return_value = None
    page.query_selector_all.return_value = []
    return page


# ---------------------------------------------------------------------------
# _handle_login_wall
# ---------------------------------------------------------------------------


def test_handle_login_wall_no_wall_returns_no_wall(mock_page):
    mock_page.query_selector.return_value = None
    result = _handle_login_wall(mock_page)
    assert result == "no_wall"


def test_handle_login_wall_continue_as_button_clicks_and_returns(mock_page):
    btn = MagicMock()

    def qs(sel):
        if "Continue as" in sel:
            return btn
        return None

    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "clicked_continue"
    btn.click.assert_called_once()


def test_handle_login_wall_sign_in_button_returns_needs_login(mock_page):
    sign_in = MagicMock()

    def qs(sel):
        if "Sign in" in sel and "Continue" not in sel:
            return sign_in
        return None

    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "needs_login"


def test_handle_login_wall_text_scan_fallback_finds_continue(mock_page):
    """When query_selector misses 'Continue as', the text scan of <a>/<button> elements finds it."""
    continue_link = MagicMock()
    continue_link.text_content.return_value = "Continue as Yash"

    # query_selector returns None for everything (selectors don't match)
    mock_page.query_selector.return_value = None
    # query_selector_all returns the link when scanning clickable elements
    mock_page.query_selector_all.return_value = [continue_link]

    result = _handle_login_wall(mock_page)
    assert result == "clicked_continue"
    continue_link.click.assert_called_once()


def test_handle_login_wall_continue_takes_priority_over_signin(mock_page):
    """'Continue as Yash' must be tried before generic Sign-in."""
    continue_btn = MagicMock()
    signin_btn = MagicMock()

    def qs(sel):
        if "Continue as" in sel:
            return continue_btn
        if "Sign in" in sel:
            return signin_btn
        return None

    mock_page.query_selector.side_effect = qs
    result = _handle_login_wall(mock_page)
    assert result == "clicked_continue"
    continue_btn.click.assert_called_once()
    signin_btn.click.assert_not_called()


# ---------------------------------------------------------------------------
# _dump_page_context
# ---------------------------------------------------------------------------


def test_dump_page_context_returns_required_keys(mock_page):
    ctx = _dump_page_context(mock_page)
    for key in ("url", "inputs", "buttons", "modal_text", "selects"):
        assert key in ctx, f"Missing key: {key}"


def test_dump_page_context_captures_url(mock_page):
    mock_page.url = "https://www.linkedin.com/jobs/view/999/"
    ctx = _dump_page_context(mock_page)
    assert ctx["url"] == "https://www.linkedin.com/jobs/view/999/"


def test_dump_page_context_no_modal_gives_empty_modal_text(mock_page):
    mock_page.query_selector.return_value = None
    ctx = _dump_page_context(mock_page)
    assert ctx["modal_text"] == ""


def test_dump_page_context_captures_input_aria_labels(mock_page):
    inp = MagicMock()
    inp.get_attribute.side_effect = lambda a: {
        "type": "text",
        "name": "phoneNumber",
        "id": "ph-1",
        "placeholder": "Mobile phone number",
        "aria-label": "Mobile phone number",
    }.get(a, "")
    inp.input_value.return_value = ""

    def qsa(sel):
        if "input" in sel:
            return [inp]
        return []

    mock_page.query_selector_all.side_effect = qsa
    ctx = _dump_page_context(mock_page)
    assert len(ctx["inputs"]) == 1
    assert ctx["inputs"][0]["aria_label"] == "Mobile phone number"


def test_dump_page_context_caps_inputs_at_20(mock_page):
    inputs = [MagicMock() for _ in range(30)]
    for inp in inputs:
        inp.get_attribute.return_value = ""
        inp.input_value.return_value = ""

    def qsa(sel):
        if "input" in sel:
            return inputs
        return []

    mock_page.query_selector_all.side_effect = qsa
    ctx = _dump_page_context(mock_page)
    assert len(ctx["inputs"]) <= 20


# ---------------------------------------------------------------------------
# _fill_location_typeahead
# ---------------------------------------------------------------------------


def test_fill_location_typeahead_clicks_first_suggestion(mock_page):
    """Location field found by aria-label → types → clicks first suggestion dropdown."""
    location_input = MagicMock()
    suggestion = MagicMock()

    _SUGGESTION_SELS = (
        "[role='option']",
        ".basic-typeahead__selectable",
        "li.search-typeahead-v2__hit",
    )

    def qs(sel):
        # Only return location_input for input[aria-label*=...] selectors
        if sel.startswith("input") and (
            "ocation" in sel or "City" in sel or "typeahead" in sel
        ):
            return location_input
        # Return suggestion only for the exact known suggestion selectors
        if sel in _SUGGESTION_SELS:
            return suggestion
        return None

    mock_page.query_selector.side_effect = qs
    mock_page.keyboard = MagicMock()

    _fill_location_typeahead(mock_page, "Dundee, UK")

    location_input.fill.assert_called_once_with("")
    suggestion.click.assert_called_once()


def test_fill_location_typeahead_presses_enter_when_no_suggestion(mock_page):
    """No suggestion dropdown → fall back to pressing Enter."""
    location_input = MagicMock()

    def qs(sel):
        # Return input only for input[...] selectors — not for suggestion class selectors
        if sel.startswith("input") and (
            "ocation" in sel or "City" in sel or "typeahead" in sel
        ):
            return location_input
        return None

    mock_page.query_selector.side_effect = qs
    mock_page.keyboard = MagicMock()

    _fill_location_typeahead(mock_page, "Dundee, UK")
    mock_page.keyboard.press.assert_called_once_with("Enter")
