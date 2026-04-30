"""Tests for the reasoner-driven navigation loop."""
import json
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.page_analysis.page_reasoner import (
    PageReasoner, PageAction, VALID_ACTIONS,
)


def _fake_llm_response(data: dict) -> MagicMock:
    """Create a mock AIMessage with .content = JSON string."""
    msg = MagicMock()
    msg.content = json.dumps(data)
    return msg


class TestPageReasonerParsing:
    def test_parse_field_fills(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Email entry page for Oracle Cloud",
            "page_type": "signup_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"},
                {"label": "I agree with the terms", "value": "true", "method": "check_label"},
            ],
            "advance_button": "Next",
            "overlays_to_dismiss": ["Agree"],
            "reasoning": "Simple email entry with consent checkbox",
            "confidence": 0.95,
        })
        action = reasoner._parse_response(text)
        assert action.action == "fill_and_advance"
        assert len(action.field_fills) == 2
        assert action.field_fills[0]["label"] == "Email Address"
        assert action.advance_button == "Next"
        assert action.overlays_to_dismiss == ["Agree"]

    def test_parse_click_apply(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Job listing page with Apply button",
            "page_type": "job_description",
            "action": "click_element",
            "target_text": "Apply Now",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": [],
            "reasoning": "Click apply to proceed",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert action.action == "click_element"
        assert action.target_text == "Apply Now"

    def test_parse_dismiss_overlay(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Cookie consent overlay blocking page",
            "page_type": "unknown",
            "action": "dismiss_overlay",
            "target_text": "Accept",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": ["Accept", "Agree"],
            "reasoning": "Cookie consent must be dismissed first",
            "confidence": 0.95,
        })
        action = reasoner._parse_response(text)
        assert action.action == "dismiss_overlay"

    def test_parse_captcha_routes_to_human(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Page with hCaptcha blocking interaction",
            "page_type": "verification_wall",
            "action": "wait_human",
            "target_text": "",
            "field_fills": [],
            "advance_button": "",
            "overlays_to_dismiss": [],
            "reasoning": "CAPTCHA requires human intervention",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert action.action == "wait_human"

    def test_honeypot_skipped(self):
        reasoner = PageReasoner.__new__(PageReasoner)
        text = json.dumps({
            "page_understanding": "Signup with honeypot",
            "page_type": "signup_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email Address", "value": "FROM_PROFILE:email", "method": "fill"},
            ],
            "advance_button": "Next",
            "overlays_to_dismiss": [],
            "reasoning": "Honeypot field skipped",
            "confidence": 0.9,
        })
        action = reasoner._parse_response(text)
        assert len(action.field_fills) == 1
        assert all(f["label"] != "honeypot" for f in action.field_fills)

    def test_valid_actions_includes_new_types(self):
        assert "fill_and_advance" in VALID_ACTIONS
        assert "dismiss_overlay" in VALID_ACTIONS
        assert "fill_form" in VALID_ACTIONS
        assert "wait_human" in VALID_ACTIONS


class TestPageReasonerSync:
    @patch("jobpulse.page_analysis.page_reasoner.smart_llm_call")
    @patch("jobpulse.page_analysis.page_reasoner.get_llm")
    def test_reason_sync_returns_page_action(self, mock_get_llm, mock_smart_call):
        mock_smart_call.return_value = _fake_llm_response({
            "page_understanding": "Login page",
            "page_type": "login_form",
            "action": "fill_and_advance",
            "field_fills": [
                {"label": "Email", "value": "FROM_PROFILE:email", "method": "fill"},
            ],
            "advance_button": "Sign In",
            "overlays_to_dismiss": [],
            "reasoning": "Fill email and sign in",
            "confidence": 0.9,
        })
        reasoner = PageReasoner.__new__(PageReasoner)
        reasoner._db_path = ":memory:"
        reasoner._ensure_db = lambda: None
        reasoner._get_cached = lambda k: None
        reasoner._set_cache = lambda k, a: None
        action = reasoner.reason_sync({
            "url": "https://example.com/login",
            "page_text_preview": "Sign in to your account",
            "buttons": [{"text": "Sign In"}],
            "fields": [{"label": "Email", "input_type": "email"}],
        })
        assert isinstance(action, PageAction)
        assert action.action == "fill_and_advance"
        assert action.confidence == 0.9
