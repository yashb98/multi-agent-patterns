"""Tests for the 5-phase navigation pipeline.

Phase-method tests (TestPhaseObserve/Analyze/Match/Plan/Act/Integration) were
removed 2026-05-03 — they required a `mock_navigator` fixture that fully
mocked the orchestrator + Playwright driver + page + browser context, which
violates the project's no-mock-of-the-bridge policy. Real navigation is
covered by `tests/jobpulse/integration/test_pipeline_live.py`.

What remains: pure-function unit tests for fingerprinting, match scoring,
ghost-click detection, snapshot hashing, and result construction. These
use real Python data (dicts, dataclass instances) — no mocks.
"""
import pytest

from jobpulse.application_orchestrator_pkg._navigator import (
    TabState,
    PageFingerprint,
    StepContext,
    TERMINAL_ACTIONS,
    build_page_fingerprint,
    score_fingerprint_match,
    FormNavigator,
)
from jobpulse.form_models import PageType
from jobpulse.page_analysis.page_reasoner import PageAction


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


class TestGhostClickDetection:
    def test_nothing_changed_is_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="aaa",
            post_dialog=False,
        ) is True

    def test_url_changed_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/apply/1",
            post_content_hash="aaa",
            post_dialog=False,
        ) is False

    def test_content_changed_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="bbb",
            post_dialog=False,
        ) is False

    def test_dialog_appeared_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="aaa",
            post_dialog=True,
        ) is False


class TestSnapshotContentHash:
    def test_basic(self):
        snapshot = {
            "page_text_preview": "Hello world",
            "fields": [{"label": "Name"}],
            "buttons": [{"text": "Submit"}],
        }
        h = FormNavigator._snapshot_content_hash(snapshot)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_different_content_different_hash(self):
        s1 = {"page_text_preview": "Page A", "fields": [], "buttons": []}
        s2 = {"page_text_preview": "Page B", "fields": [], "buttons": []}
        assert FormNavigator._snapshot_content_hash(s1) != FormNavigator._snapshot_content_hash(s2)

    def test_same_content_same_hash(self):
        s = {"page_text_preview": "Same", "fields": [{"x": 1}], "buttons": []}
        assert FormNavigator._snapshot_content_hash(s) == FormNavigator._snapshot_content_hash(s)


class TestMakeResult:
    def test_fill_form_returns_application_form(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Form ready",
            action="fill_form",
            target_text="",
            reasoning="ready",
            confidence=0.9,
            page_type="application_form",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.APPLICATION_FORM
        assert result["snapshot"] == ctx.snapshot

    def test_done_returns_confirmation(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Submitted",
            action="done",
            target_text="",
            reasoning="confirmed",
            confidence=0.95,
            page_type="confirmation",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.CONFIRMATION

    def test_abort_returns_unknown(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Can't proceed",
            action="abort",
            target_text="",
            reasoning="blocked",
            confidence=0.8,
            page_type="unknown",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.UNKNOWN

    def test_expired_job_sets_expired_flag(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com/job/closed"},
            url="https://example.com/job/closed",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Job no longer available",
            action="abort",
            target_text="",
            reasoning="expired",
            confidence=0.9,
            page_type="expired_job",
        )
        result = FormNavigator._make_result(ctx)
        assert result["expired"] is True
        assert "error" in result



# ---------------------------------------------------------------------------
# Phase methods (TestPhaseObserve, TestPhaseAnalyze, TestPhaseMatch,
# TestPhasePlan, TestPhaseAct, TestNavigateToFormIntegration)
#
# Removed 2026-05-03: 30 tests built on a `mock_navigator` fixture that
# fully mocked the orchestrator + Playwright driver + page + context
# (Category B — bridge/Playwright mock). Each test asserted on phase
# behavior against synthetic snapshots returned by an AsyncMock driver.
#
# End-to-end phase behavior is exercised by:
#   tests/jobpulse/integration/test_pipeline_live.py — real Playwright
#   tests/jobpulse/test_navigation_learner_real.py    — real DB + real driver
#
# The pure-function fingerprint/match/ghost-click logic above is the
# unit-testable surface; everything below required mocking real Playwright
# and was producing false-positives after the 5-phase pipeline rewrite.
# ---------------------------------------------------------------------------
