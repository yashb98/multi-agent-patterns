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
    FormNavigator,
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


@pytest.fixture
def mock_navigator():
    """Build a FormNavigator with fully mocked orchestrator."""
    orch = MagicMock()
    page = AsyncMock()
    page.url = "https://example.com/jobs/123"
    page.is_closed = MagicMock(return_value=False)
    context = MagicMock()
    context.pages = [page]
    page.context = context

    driver = MagicMock()
    driver.page = page
    driver._page = page
    driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/jobs/123", "buttons": [], "fields": []})
    driver.intelligence = None
    orch.driver = driver
    orch.analyzer = MagicMock()
    orch.cookie_dismisser = MagicMock()
    orch.cookie_dismisser.dismiss = AsyncMock()
    orch.sso = MagicMock()
    orch.learner = MagicMock()

    auth = MagicMock()
    nav = FormNavigator(orch, auth)
    return nav, driver, page, context


class TestPhaseObserve:
    @pytest.mark.asyncio
    async def test_normal_state_single_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.NORMAL
        assert result.tab_recovered is False

    @pytest.mark.asyncio
    async def test_detects_new_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        new_page = AsyncMock()
        new_page.url = "https://ats.example.com/apply"
        new_page.is_closed = MagicMock(return_value=False)
        new_page.wait_for_load_state = AsyncMock()
        context.pages = [page, new_page]
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.example.com/apply", "buttons": [], "fields": []})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.NEW_TAB
        assert result.tab_recovered is True
        assert driver._page == new_page

    @pytest.mark.asyncio
    async def test_detects_redirect(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        page.url = "https://example.com/redirected"
        driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/redirected", "buttons": [], "fields": []})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.REDIRECTED
        assert result.snapshot["url"] == "https://example.com/redirected"

    @pytest.mark.asyncio
    async def test_detects_closed_page(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        page.is_closed = MagicMock(return_value=True)

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.CLOSED

    @pytest.mark.asyncio
    async def test_reinjects_browser_intelligence_on_new_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        intelligence = AsyncMock()
        driver.intelligence = intelligence
        new_page = AsyncMock()
        new_page.url = "https://ats.example.com/apply"
        new_page.is_closed = MagicMock(return_value=False)
        new_page.wait_for_load_state = AsyncMock()
        context.pages = [page, new_page]
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.example.com/apply"})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        await nav._phase_observe(ctx)
        intelligence.clear.assert_called_once()
        intelligence.inject_on_new_page.assert_awaited_once()


class TestPhaseAnalyze:
    @pytest.mark.asyncio
    async def test_classifies_page_and_builds_fingerprint(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123",
            "page_text_preview": "Apply for Software Engineer",
            "buttons": [{"text": "Apply Now"}],
            "fields": [{"label": "Name", "input_type": "text"}],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.JOB_DESCRIPTION, 0.85)
            result = await nav._phase_analyze(ctx)

        assert result.dom_type == PageType.JOB_DESCRIPTION
        assert result.dom_confidence == 0.85
        assert result.page_fingerprint is not None
        assert result.page_fingerprint.page_type == "job_description"
        assert result.page_fingerprint.field_count == 1

    @pytest.mark.asyncio
    async def test_detects_verification_wall(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "Checking your browser",
            "buttons": [],
            "fields": [],
            "verification_wall": {"type": "cloudflare"},
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.VERIFICATION_WALL, 0.95)
            result = await nav._phase_analyze(ctx)

        assert result.wall_detected == {"type": "cloudflare"}

    @pytest.mark.asyncio
    async def test_dismisses_cookies_and_resnapshots(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot_before = {
            "url": "https://example.com",
            "page_text_preview": "Cookie consent dialog here",
            "buttons": [{"text": "Accept Cookies"}],
            "fields": [],
            "has_dialog": True,
            "dialog_text": "We use cookies. Accept?",
        }
        snapshot_after = {
            "url": "https://example.com",
            "page_text_preview": "Welcome to our site",
            "buttons": [{"text": "Apply"}],
            "fields": [],
            "has_dialog": False,
        }
        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            return snapshot_after if call_count[0] > 1 else snapshot_before
        driver.get_snapshot = _get_snap

        ctx = StepContext(snapshot=snapshot_before, url=snapshot_before["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf, \
             patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock) as mock_cookie:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.JOB_DESCRIPTION, 0.7)
            result = await nav._phase_analyze(ctx)

        nav.cookie_dismisser.dismiss.assert_awaited()

    @pytest.mark.asyncio
    async def test_reads_browser_signals(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        mock_signal = MagicMock()
        mock_signal.source = "console"
        mock_signal.level = "error"
        mock_signal.text = "validation failed"
        mock_signal.timestamp_ms = 1000.0
        mock_signal.url = "https://example.com"
        mock_signal.metadata = {}
        intelligence = MagicMock()
        intelligence.get_signals.return_value = [mock_signal]
        driver.intelligence = intelligence

        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "Form",
            "buttons": [],
            "fields": [],
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.APPLICATION_FORM, 0.9)
            result = await nav._phase_analyze(ctx)

        assert result.browser_signals is not None
        assert len(result.browser_signals) == 1


class TestPhaseMatch:
    def test_matches_learned_sequence_above_threshold(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        fp = PageFingerprint(
            field_count=0,
            button_texts=("Apply Now",),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        learned_steps = [
            {
                "page_type": "job_description",
                "action": "click_apply",
                "fingerprint": fp.to_dict(),
            }
        ]
        nav.learner.get_sequence.return_value = learned_steps
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score >= 0.7
        assert result.learned_step is not None
        assert result.learned_step["action"] == "click_apply"
        assert result.match_source == "domain"

    def test_no_match_below_threshold(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        current_fp = PageFingerprint(
            field_count=10,
            button_texts=("Submit",),
            content_hash="xyz",
            has_dialog=True,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.8,
            url_path_pattern="/apply",
        )
        learned_steps = [
            {
                "page_type": "job_description",
                "action": "click_apply",
                "fingerprint": {
                    "field_count": 0,
                    "button_texts": ["Apply Now"],
                    "content_hash": "other",
                    "page_type": "job_description",
                    "url_path_pattern": "/jobs/{id}",
                },
            }
        ]
        nav.learner.get_sequence.return_value = learned_steps
        ctx = StepContext(
            snapshot={"url": "https://example.com/apply"},
            url="https://example.com/apply",
            tab_state=TabState.NORMAL,
            page_fingerprint=current_fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score < 0.7
        assert result.learned_step is None
        assert result.match_source == "none"

    def test_no_learned_sequence(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.learner.get_sequence.return_value = None
        nav.learner.get_platform_pattern.return_value = None
        nav.learner.get_sequence_by_content_hash.return_value = None
        fp = PageFingerprint(
            field_count=0, button_texts=(), content_hash="x",
            has_dialog=False, has_file_inputs=False,
            page_type="unknown", dom_confidence=0.5,
            url_path_pattern="/",
        )
        ctx = StepContext(
            snapshot={"url": "https://new-site.com"},
            url="https://new-site.com",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "new-site.com", "", step_index=0)
        assert result.match_source == "none"
        assert result.learned_step is None

    def test_step_index_exceeds_sequence(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        learned_steps = [{"page_type": "job_description", "action": "click_apply", "fingerprint": {}}]
        nav.learner.get_sequence.return_value = learned_steps
        fp = PageFingerprint(
            field_count=5, button_texts=("Next",), content_hash="abc",
            has_dialog=False, has_file_inputs=False,
            page_type="application_form", dom_confidence=0.9,
            url_path_pattern="/apply",
        )
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=5)
        assert result.match_source == "none"

    def test_old_format_caps_at_04(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        learned_steps = [{"page_type": "job_description", "action": "click_apply"}]
        nav.learner.get_sequence.return_value = learned_steps
        fp = PageFingerprint(
            field_count=0, button_texts=("Apply Now",), content_hash="abc",
            has_dialog=False, has_file_inputs=False,
            page_type="job_description", dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score <= 0.4
        assert result.learned_step is None

    def test_falls_back_to_platform_pattern(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.learner.get_sequence.return_value = None
        fp_dict = {
            "field_count": 0,
            "button_texts": ["Apply Now"],
            "content_hash": "abc123",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        nav.learner.get_platform_pattern.return_value = [
            {"page_type": "job_description", "action": "click_apply", "fingerprint": fp_dict}
        ]
        fp = PageFingerprint(
            field_count=0, button_texts=("Apply Now",), content_hash="abc123",
            has_dialog=False, has_file_inputs=False,
            page_type="job_description", dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        ctx = StepContext(
            snapshot={"url": "https://new-greenhouse.io/jobs/456"},
            url="https://new-greenhouse.io/jobs/456",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "new-greenhouse.io", "greenhouse", step_index=0)
        assert result.match_score >= 0.7
        assert result.match_source == "platform"
