"""Tests for SSOHandler — SSO button detection and delegation."""

import pytest
from unittest.mock import AsyncMock
from jobpulse.sso_handler import SSOHandler


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.click = AsyncMock()
    b.get_snapshot = AsyncMock(return_value={})
    return b


@pytest.fixture
def handler(bridge):
    return SSOHandler(bridge)


def test_detect_google_sso():
    snapshot = {
        "buttons": [
            {"text": "Sign in with Google", "enabled": True, "selector": "#google-sso"},
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is not None
    assert sso["provider"] == "google"
    assert sso["selector"] == "#google-sso"


def test_detect_linkedin_sso():
    snapshot = {
        "buttons": [
            {"text": "Continue with LinkedIn", "enabled": True, "selector": ".linkedin-btn"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is not None
    assert sso["provider"] == "linkedin"


def test_detect_no_sso():
    snapshot = {
        "buttons": [
            {"text": "Sign in", "enabled": True, "selector": "#signin"},
            {"text": "Create Account", "enabled": True, "selector": "#create"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso is None


def test_detect_google_continue():
    snapshot = {
        "buttons": [
            {"text": "Continue with Google", "enabled": True, "selector": ".google-oauth"},
        ],
    }
    handler = SSOHandler(AsyncMock())
    sso = handler.detect_sso(snapshot)
    assert sso["provider"] == "google"


@pytest.mark.asyncio
async def test_click_sso_button(handler, bridge):
    sso = {"provider": "google", "selector": "#google-sso"}
    await handler.click_sso(sso)
    bridge.click.assert_called_once_with("#google-sso")
