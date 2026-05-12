"""Tests for the new expected_outcome contract on PageAction."""
import json
import pytest
from jobpulse.page_analysis.page_reasoner import PageReasoner, PageAction


VALID_OUTCOMES = {"url_changes", "fields_filled", "dialog_dismissed", "page_unchanged", "unknown"}


class TestPageActionOutcomeField:
    def test_default_is_unknown(self):
        a = PageAction(
            page_understanding="t", action="abort", target_text="",
            reasoning="t", confidence=0.0, page_type="unknown",
        )
        assert a.expected_outcome == "unknown"

    def test_outcome_round_trips(self):
        a = PageAction(
            page_understanding="t", action="fill_and_advance", target_text="",
            reasoning="t", confidence=0.9, page_type="login_form",
            expected_outcome="url_changes",
        )
        assert a.to_dict()["expected_outcome"] == "url_changes"

    def test_parser_extracts_outcome(self):
        text = json.dumps({
            "page_understanding": "login form", "action": "fill_and_advance",
            "target_text": "", "field_fills": [], "advance_button": "Sign in",
            "overlays_to_dismiss": [], "reasoning": "t", "confidence": 0.9,
            "page_type": "login_form", "expected_outcome": "url_changes",
        })
        action = PageReasoner._parse_response(text)
        assert action.expected_outcome == "url_changes"

    def test_parser_normalizes_unknown_outcome(self):
        text = json.dumps({
            "page_understanding": "x", "action": "abort", "target_text": "",
            "reasoning": "t", "confidence": 0.0, "page_type": "unknown",
            "expected_outcome": "rocket_launch",
        })
        action = PageReasoner._parse_response(text)
        assert action.expected_outcome == "unknown"
