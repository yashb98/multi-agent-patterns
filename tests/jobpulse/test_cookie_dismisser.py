"""Tests for CookieBannerDismisser."""

import pytest
from unittest.mock import AsyncMock
from jobpulse.cookie_dismisser import CookieBannerDismisser


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.click = AsyncMock(return_value=True)
    b.get_snapshot = AsyncMock()
    return b


@pytest.fixture
def dismisser(bridge):
    return CookieBannerDismisser(bridge)


@pytest.mark.asyncio
async def test_dismiss_accept_all(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Accept All Cookies", "enabled": True, "selector": "#accept-all"},
            {"text": "Manage Preferences", "enabled": True, "selector": "#manage"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with("#accept-all")


@pytest.mark.asyncio
async def test_dismiss_i_agree(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "I Agree", "enabled": True, "selector": "#agree"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with("#agree")


@pytest.mark.asyncio
async def test_dismiss_accept_cookies(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Accept cookies", "enabled": True, "selector": ".cookie-btn"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True


@pytest.mark.asyncio
async def test_dismiss_got_it(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Got it!", "enabled": True, "selector": "#gotit"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True


@pytest.mark.asyncio
async def test_no_banner_returns_false(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Submit Application", "enabled": True, "selector": "#submit"},
        ],
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is False
    bridge.click.assert_not_called()


@pytest.mark.asyncio
async def test_dismiss_close_x_button(dismisser, bridge):
    snapshot = {
        "buttons": [
            {"text": "Close", "enabled": True, "selector": ".cookie-close"},
            {"text": "Cookie Policy", "enabled": True, "selector": "#policy"},
        ],
        "page_text_preview": "We use cookies to improve your experience",
    }
    dismissed = await dismisser.dismiss(snapshot)
    assert dismissed is True
    bridge.click.assert_called_once_with(".cookie-close")
