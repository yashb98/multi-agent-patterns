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
        "page_text_preview": "We use cookies to improve your experience",
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
        "page_text_preview": "This site uses cookies and tracking technologies",
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


@pytest.mark.asyncio
async def test_dismiss_german_akzeptieren(dismisser, bridge):
    snapshot = {"buttons": [{"text": "Alle akzeptieren", "enabled": True, "selector": "#de"}],
                "page_text_preview": "Wir verwenden Cookies"}
    assert await dismisser.dismiss(snapshot) is True
    bridge.click.assert_called_with("#de")

@pytest.mark.asyncio
async def test_dismiss_french_accepter(dismisser, bridge):
    snapshot = {"buttons": [{"text": "Tout accepter", "enabled": True, "selector": "#fr"}],
                "page_text_preview": "Ce site utilise des cookies"}
    assert await dismisser.dismiss(snapshot) is True
    bridge.click.assert_called_with("#fr")

@pytest.mark.asyncio
async def test_dismiss_spanish_aceptar(dismisser, bridge):
    snapshot = {"buttons": [{"text": "Aceptar todas", "enabled": True, "selector": "#es"}],
                "page_text_preview": "Utilizamos cookies"}
    assert await dismisser.dismiss(snapshot) is True
    bridge.click.assert_called_with("#es")


class TestCookieSiblingDetection:
    """Verify cookie banner detection via sibling buttons (Reject All, Manage Cookies)."""

    @pytest.mark.asyncio
    async def test_allow_all_with_reject_sibling(self, dismisser, bridge):
        """LinkedIn-style: Allow All + Reject All + Manage Cookies, no cookie text in page."""
        snapshot = {
            "buttons": [
                {"text": "Manage Cookies", "enabled": True, "selector": "#manage"},
                {"text": "Reject All", "enabled": True, "selector": "#reject"},
                {"text": "Allow All", "enabled": True, "selector": "#allow"},
            ],
            "page_text_preview": "Machine Learning Engineer at Intact Insurance UK",
        }
        dismissed = await dismisser.dismiss(snapshot)
        assert dismissed is True
        bridge.click.assert_called_once_with("#allow")

    @pytest.mark.asyncio
    async def test_no_sibling_no_context_skips(self, dismisser, bridge):
        """Buttons without cookie siblings or page context should be skipped."""
        snapshot = {
            "buttons": [
                {"text": "Allow All", "enabled": True, "selector": "#allow"},
                {"text": "Submit", "enabled": True, "selector": "#submit"},
            ],
            "page_text_preview": "Job application form",
        }
        dismissed = await dismisser.dismiss(snapshot)
        assert dismissed is False
        bridge.click.assert_not_called()

    def test_sibling_detection_method(self):
        buttons = [
            {"text": "Reject All"},
            {"text": "Allow All"},
            {"text": "Manage Cookies"},
        ]
        assert CookieBannerDismisser._has_cookie_sibling_buttons(buttons) is True

    def test_no_sibling_detection(self):
        buttons = [
            {"text": "Submit Application"},
            {"text": "Next"},
        ]
        assert CookieBannerDismisser._has_cookie_sibling_buttons(buttons) is False


class TestFormConsentFalsePositive:
    """Form-level data privacy consent must NOT be treated as cookie banner."""

    @pytest.mark.asyncio
    async def test_data_privacy_statement_not_dismissed(self, dismisser, bridge):
        """Intact Insurance 'Data Privacy Statement' consent is a form element, not a cookie banner."""
        snapshot = {
            "buttons": [
                {
                    "text": "By selecting 'I agree' you confirm that you have read and understood the Data Privacy Statement",
                    "selector": "button.privacy-consent",
                    "enabled": True,
                },
                {"text": "Submit Application", "selector": "button.submit", "enabled": True},
            ],
            "page_text_preview": "Machine Learning Engineer application\nPersonal Information\nTitle\nFirst Name",
        }
        result = await dismisser.dismiss(snapshot)
        assert result is False, "Form privacy consent should NOT be dismissed as cookie banner"
        bridge.click.assert_not_called()

    @pytest.mark.asyncio
    async def test_real_cookie_banner_still_dismissed(self, dismisser, bridge):
        """Actual cookie banners with 'I agree' should still be dismissed."""
        snapshot = {
            "buttons": [
                {
                    "text": "I agree",
                    "selector": "#cookie-agree",
                    "enabled": True,
                },
            ],
            "page_text_preview": "This site uses cookies to improve your experience.",
        }
        result = await dismisser.dismiss(snapshot)
        assert result is True
        bridge.click.assert_called_once()
