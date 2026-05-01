"""Tests for the 5-phase navigation pipeline."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from jobpulse.application_orchestrator_pkg._navigator import (
    TabState,
    PageFingerprint,
    StepContext,
    TERMINAL_ACTIONS,
)
from jobpulse.form_models import PageType


class TestTabState:
    def test_enum_values(self):
        assert TabState.NORMAL.value == "normal"
        assert TabState.NEW_TAB.value == "new_tab"
        assert TabState.POPUP.value == "popup"
        assert TabState.CLOSED.value == "closed"
        assert TabState.REDIRECTED.value == "redirected"


class TestPageFingerprint:
    def test_creation(self):
        fp = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now", "Save"),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.92,
            url_path_pattern="/jobs/{id}",
        )
        assert fp.field_count == 5
        assert fp.button_texts == ("Apply Now", "Save")
        assert fp.url_path_pattern == "/jobs/{id}"

    def test_to_dict(self):
        fp = PageFingerprint(
            field_count=3,
            button_texts=("Next",),
            content_hash="def456",
            has_dialog=True,
            has_file_inputs=False,
            page_type="login_form",
            dom_confidence=0.85,
            url_path_pattern="/login",
        )
        d = fp.to_dict()
        assert d["field_count"] == 3
        assert d["button_texts"] == ["Next"]
        assert d["page_type"] == "login_form"

    def test_from_dict(self):
        d = {
            "field_count": 2,
            "button_texts": ["Submit"],
            "content_hash": "xyz",
            "has_dialog": False,
            "has_file_inputs": False,
            "page_type": "unknown",
            "dom_confidence": 0.5,
            "url_path_pattern": "/apply",
        }
        fp = PageFingerprint.from_dict(d)
        assert fp.field_count == 2
        assert fp.button_texts == ("Submit",)


class TestStepContext:
    def test_defaults(self):
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        assert ctx.dom_type == PageType.UNKNOWN
        assert ctx.dom_confidence == 0.0
        assert ctx.match_score == 0.0
        assert ctx.planned_action is None
        assert ctx.ghost_click is False
        assert ctx.overlays_detected == []


class TestTerminalActions:
    def test_terminal_actions_frozenset(self):
        assert isinstance(TERMINAL_ACTIONS, frozenset)
        assert "fill_form" in TERMINAL_ACTIONS
        assert "done" in TERMINAL_ACTIONS
        assert "abort" in TERMINAL_ACTIONS
