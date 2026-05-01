"""Tests for the 5-phase navigation pipeline."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from jobpulse.application_orchestrator_pkg._navigator import (
    TabState,
    PageFingerprint,
    StepContext,
    TERMINAL_ACTIONS,
    build_page_fingerprint,
    score_fingerprint_match,
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


class TestBuildPageFingerprint:
    def test_basic_snapshot(self):
        snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/12345",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [
                {"text": "Apply Now"},
                {"text": "Save"},
                {"text": "Apply Now"},  # duplicate
            ],
            "fields": [
                {"label": "First Name", "input_type": "text"},
                {"label": "Last Name", "input_type": "text"},
            ],
            "has_dialog": False,
            "has_file_inputs": True,
        }
        fp = build_page_fingerprint(snapshot, page_type="application_form", dom_confidence=0.9)
        assert fp.field_count == 2
        assert fp.button_texts == ("Apply Now", "Save")  # sorted, deduplicated
        assert fp.has_dialog is False
        assert fp.has_file_inputs is True
        assert fp.page_type == "application_form"
        assert fp.dom_confidence == 0.9
        assert fp.url_path_pattern == "/company/jobs/{id}"
        assert len(fp.content_hash) == 16  # 16-char hex

    def test_url_id_replacement(self):
        snapshot = {
            "url": "https://example.com/apply/98765/form",
            "page_text_preview": "",
            "buttons": [],
            "fields": [],
        }
        fp = build_page_fingerprint(snapshot, page_type="unknown", dom_confidence=0.5)
        assert fp.url_path_pattern == "/apply/{id}/form"

    def test_button_truncation(self):
        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "",
            "buttons": [{"text": "A" * 50}],
            "fields": [],
        }
        fp = build_page_fingerprint(snapshot, page_type="unknown", dom_confidence=0.5)
        assert len(fp.button_texts[0]) == 20

    def test_empty_snapshot(self):
        fp = build_page_fingerprint({}, page_type="unknown", dom_confidence=0.0)
        assert fp.field_count == 0
        assert fp.button_texts == ()
        assert fp.url_path_pattern == ""


class TestScoreFingerprintMatch:
    def test_identical_fingerprints(self):
        fp = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now", "Save"),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        score = score_fingerprint_match(fp, fp.to_dict())
        assert score == 1.0

    def test_completely_different(self):
        current = PageFingerprint(
            field_count=10,
            button_texts=("Submit",),
            content_hash="aaa",
            has_dialog=True,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.9,
            url_path_pattern="/apply",
        )
        learned = {
            "field_count": 0,
            "button_texts": ["Save"],
            "content_hash": "zzz",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        score = score_fingerprint_match(current, learned)
        assert score < 0.3

    def test_same_page_type_different_content(self):
        current = PageFingerprint(
            field_count=5,
            button_texts=("Next", "Back"),
            content_hash="aaa",
            has_dialog=False,
            has_file_inputs=False,
            page_type="application_form",
            dom_confidence=0.8,
            url_path_pattern="/apply/{id}",
        )
        learned = {
            "field_count": 7,
            "button_texts": ["Next", "Back", "Save"],
            "content_hash": "bbb",
            "page_type": "application_form",
            "url_path_pattern": "/apply/{id}",
        }
        score = score_fingerprint_match(current, learned)
        assert 0.5 < score < 0.8

    def test_old_format_no_fingerprint(self):
        current = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now",),
            content_hash="abc",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        learned_step = {"page_type": "job_description", "action": "click_apply"}
        score = score_fingerprint_match(current, learned_step.get("fingerprint"))
        assert score == 0.0

    def test_threshold_boundary(self):
        current = PageFingerprint(
            field_count=3,
            button_texts=("Apply Now",),
            content_hash="same_hash",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        learned = {
            "field_count": 3,
            "button_texts": ["Apply Now"],
            "content_hash": "same_hash",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        score = score_fingerprint_match(current, learned)
        assert score >= 0.7
