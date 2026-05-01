"""Tests for executor verification primitives."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.page_analysis.page_reasoner import PageAction
from jobpulse.navigation.action_executor import (
    NavigationActionExecutor,
    ExecutorResult,
)


def _make_action(**kwargs) -> PageAction:
    defaults = {
        "page_understanding": "test", "action": "fill_and_advance",
        "target_text": "", "reasoning": "test", "confidence": 0.9,
        "page_type": "signup_form", "field_fills": [],
        "advance_button": "", "overlays_to_dismiss": [],
    }
    defaults.update(kwargs)
    return PageAction(**defaults)


class TestExecutorResultShape:
    def test_default_result_is_empty(self):
        r = ExecutorResult()
        assert r.fills_attempted == 0
        assert r.fills_verified == 0
        assert r.fills_failed == []
        assert r.clicks_attempted == 0
        assert r.advance_clicked is False

    def test_result_records_failures(self):
        r = ExecutorResult()
        r.record_fill_failure("Email", expected="a@b.com", actual="")
        assert r.fills_failed == [{"label": "Email", "expected": "a@b.com", "actual": ""}]
