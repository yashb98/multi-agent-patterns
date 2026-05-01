"""Tests for the extracted _verify_action helper used by both _phase_act and auth."""
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, ActionVerification,
)


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


import pytest
from unittest.mock import AsyncMock


@pytest.fixture
def navigator():
    """Build a FormNavigator instance bypassing __init__ for unit tests."""
    from unittest.mock import MagicMock
    nav = FormNavigator.__new__(FormNavigator)
    nav._orch = MagicMock()
    nav._orch.driver = AsyncMock()
    return nav


class TestExpectedOutcomeVerification:
    @pytest.mark.asyncio
    async def test_url_changes_outcome_satisfied(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        pre = {"url": "https://example.com/login", "has_dialog": False,
               "page_text_preview": "login", "fields": [], "buttons": []}
        post = {"url": "https://example.com/dashboard", "has_dialog": False,
                "page_text_preview": "dash", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is True

    @pytest.mark.asyncio
    async def test_url_changes_outcome_violated(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        pre = {"url": "https://example.com/login", "has_dialog": False,
               "page_text_preview": "login", "fields": [], "buttons": []}
        post = {"url": "https://example.com/login", "has_dialog": False,
                "page_text_preview": "login", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is False

    @pytest.mark.asyncio
    async def test_dialog_dismissed_outcome(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="dismiss_overlay", target_text="OK",
            reasoning="t", confidence=0.9, page_type="application_form",
            expected_outcome="dialog_dismissed",
        )
        pre = {"url": "https://example.com/x", "has_dialog": True,
               "page_text_preview": "x", "fields": [], "buttons": []}
        post = {"url": "https://example.com/x", "has_dialog": False,
               "page_text_preview": "x", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is True

    @pytest.mark.asyncio
    async def test_page_unchanged_outcome(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="click_element", target_text="OK",
            reasoning="t", confidence=0.9, page_type="consent_gate",
            expected_outcome="page_unchanged",
        )
        pre = {"url": "https://example.com/x", "has_dialog": False,
               "page_text_preview": "x", "fields": [], "buttons": []}
        post = {"url": "https://example.com/x", "has_dialog": False,
                "page_text_preview": "x", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is True

    @pytest.mark.asyncio
    async def test_unknown_outcome_returns_none(self, navigator):
        from jobpulse.page_analysis.page_reasoner import PageAction
        action = PageAction(
            page_understanding="t", action="click_element", target_text="OK",
            reasoning="t", confidence=0.9, page_type="consent_gate",
            expected_outcome="unknown",
        )
        pre = {"url": "https://example.com/x", "has_dialog": False,
               "page_text_preview": "x", "fields": [], "buttons": []}
        post = {"url": "https://example.com/y", "has_dialog": False,
                "page_text_preview": "y", "fields": [], "buttons": []}
        v = await navigator._verify_action(pre, post, action_kind=action.action)
        v_with_outcome = navigator._check_expected_outcome(action, v)
        assert v_with_outcome.expected_outcome_met is None
