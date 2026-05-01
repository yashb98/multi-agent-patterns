"""Tests for the extracted _verify_action helper used by both _phase_act and auth."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, ActionVerification,
)


@pytest.fixture
def navigator():
    nav = FormNavigator.__new__(FormNavigator)  # bypass __init__ for unit test
    nav.driver = AsyncMock()
    nav.driver.get_snapshot = AsyncMock(return_value={
        "url": "https://example.com/step2",
        "page_text_preview": "step 2",
        "has_dialog": False,
        "fields": [], "buttons": [],
    })
    return nav


class TestActionVerification:
    def test_default_unverified(self):
        v = ActionVerification(
            pre_url="https://example.com",
            pre_hash="abc",
            pre_dialog=False,
            post_url="https://example.com",
            post_hash="abc",
            post_dialog=False,
        )
        assert v.url_changed is False
        assert v.content_changed is False

    def test_url_change_detected(self):
        v = ActionVerification(
            pre_url="https://example.com/login",
            pre_hash="abc",
            pre_dialog=False,
            post_url="https://example.com/dashboard",
            post_hash="def",
            post_dialog=False,
        )
        assert v.url_changed is True
        assert v.content_changed is True
