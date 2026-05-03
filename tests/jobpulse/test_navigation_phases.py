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
    with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier"):
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
        driver.get_snapshot = AsyncMock(return_value=snapshot)
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        nav._classifier = MagicMock()
        nav._classifier.classify.return_value = (PageType.JOB_DESCRIPTION, 0.85)
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
        driver.get_snapshot = AsyncMock(return_value=snapshot)
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        nav._classifier = MagicMock()
        nav._classifier.classify.return_value = (PageType.VERIFICATION_WALL, 0.95)
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

        nav._classifier = MagicMock()
        nav._classifier.classify.return_value = (PageType.JOB_DESCRIPTION, 0.7)
        with patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock) as mock_cookie:
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
        driver.get_snapshot = AsyncMock(return_value=snapshot)
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        nav._classifier = MagicMock()
        nav._classifier.classify.return_value = (PageType.APPLICATION_FORM, 0.9)
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


class TestPhasePlan:
    def test_wall_detected_returns_wait_human(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
            wall_detected={"type": "cloudflare"},
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action is not None
        assert result.planned_action.action == "wait_human"
        assert result.plan_source == "fast_path"

    def test_confirmation_with_high_confidence_returns_done(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
            dom_type=PageType.CONFIRMATION,
            dom_confidence=0.85,
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action.action == "done"
        assert result.plan_source == "fast_path"

    def test_confirmation_low_confidence_falls_to_reasoner(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
            dom_type=PageType.CONFIRMATION,
            dom_confidence=0.5,
        )
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Confirmation page", action="done",
                target_text="", reasoning="confirmed", confidence=0.9,
                page_type="confirmation",
            )
            result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "reasoner"

    def test_learned_step_verified_click_apply(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "buttons": [{"text": "Apply Now", "enabled": True}],
                "fields": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            learned_step={"page_type": "job_description", "action": "click_apply"},
            match_score=0.85,
            match_source="domain",
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "learned_verified"
        assert result.planned_action.action == "click_apply"

    def test_learned_step_verification_fails_falls_to_reasoner(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "buttons": [],  # No apply button
                "fields": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            learned_step={"page_type": "job_description", "action": "click_apply"},
            match_score=0.85,
            match_source="domain",
        )
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Job page", action="click_element",
                target_text="Apply", reasoning="found apply link", confidence=0.7,
                page_type="job_description",
            )
            result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "reasoner"

    def test_loop_detection_aborts(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com", "buttons": [], "fields": []},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        visited = {"unknown:click_element": 2}
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Stuck", action="click_element",
                target_text="Something", reasoning="trying", confidence=0.5,
                page_type="unknown",
            )
            result = nav._phase_plan(ctx, visited_states=visited, wall_bypass_attempts=0)
        assert result.planned_action.action == "abort"

    def test_application_form_high_confidence_returns_fill_form(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/apply", "buttons": [], "fields": [{"label": "Name"}]},
            url="https://example.com/apply",
            tab_state=TabState.NORMAL,
            dom_type=PageType.APPLICATION_FORM,
            dom_confidence=0.9,
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action.action == "fill_form"
        assert result.plan_source == "fast_path"


class TestPhaseAct:
    @pytest.mark.asyncio
    async def test_click_apply_dispatches(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.click_apply_button = AsyncMock(return_value={"url": "https://ats.com/apply", "buttons": [], "fields": []})
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.com/apply", "buttons": [], "fields": []})
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/1", "buttons": [], "fields": []},
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            planned_action=PageAction(
                page_understanding="JD page", action="click_apply",
                target_text="", reasoning="apply", confidence=0.9,
                page_type="job_description",
            ),
            plan_source="learned_verified",
            page_fingerprint=PageFingerprint(
                field_count=0, button_texts=("Apply Now",), content_hash="abc",
                has_dialog=False, has_file_inputs=False,
                page_type="job_description", dom_confidence=0.9,
                url_path_pattern="/jobs/{id}",
            ),
        )
        result = await nav._phase_act(ctx, "greenhouse", [], 0)
        nav.click_apply_button.assert_awaited_once()
        assert result.action_executed is True
        assert result.post_snapshot is not None

    @pytest.mark.asyncio
    async def test_sso_action_dispatches(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.sso.detect_sso.return_value = {"provider": "google", "selector": "#google-sso"}
        nav.sso.click_sso = AsyncMock()
        driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/sso-done", "buttons": [], "fields": []})
        ctx = StepContext(
            snapshot={"url": "https://example.com/login", "buttons": [], "fields": []},
            url="https://example.com/login",
            tab_state=TabState.NORMAL,
            planned_action=PageAction(
                page_understanding="Login", action="sso_google",
                target_text="", reasoning="sso", confidence=0.9,
                page_type="login_form",
            ),
            plan_source="learned_verified",
            page_fingerprint=PageFingerprint(
                field_count=2, button_texts=("Sign In",), content_hash="xyz",
                has_dialog=False, has_file_inputs=False,
                page_type="login_form", dom_confidence=0.8,
                url_path_pattern="/login",
            ),
        )
        result = await nav._phase_act(ctx, "greenhouse", [], 0)
        nav.sso.click_sso.assert_awaited_once()
        assert result.action_executed is True

    @pytest.mark.asyncio
    async def test_ghost_click_detected_and_retried(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        same_snapshot = {"url": "https://example.com/jobs/1", "page_text_preview": "Same content", "buttons": [{"text": "Apply Now"}], "fields": [], "has_dialog": False}
        driver.get_snapshot = AsyncMock(return_value=same_snapshot)

        with patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:
            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            ctx = StepContext(
                snapshot=same_snapshot,
                url="https://example.com/jobs/1",
                tab_state=TabState.NORMAL,
                planned_action=PageAction(
                    page_understanding="Click element", action="click_element",
                    target_text="Apply Now", reasoning="click it", confidence=0.8,
                    page_type="job_description",
                ),
                plan_source="reasoner",
                page_fingerprint=PageFingerprint(
                    field_count=0, button_texts=("Apply Now",), content_hash="abc",
                    has_dialog=False, has_file_inputs=False,
                    page_type="job_description", dom_confidence=0.8,
                    url_path_pattern="/jobs/{id}",
                ),
            )
            result = await nav._phase_act(ctx, "greenhouse", [], 0)

        assert result.ghost_click is True

    @pytest.mark.asyncio
    async def test_ghost_click_recovery_fires_with_empty_target_text(self, mock_navigator):
        """Learned-replay actions hardcode target_text='' (see _phase_plan).
        When such an action ghost-clicks, the for/else recovery used to be
        nested inside `if action.target_text:` and silently skipped — emitting
        no failure signal, no cache invalidation, no reflection. Regression
        test for bug_008: recovery must fire when target_text is empty.
        """
        nav, driver, page, context = mock_navigator
        same_snapshot = {
            "url": "https://example.com/jobs/1",
            "page_text_preview": "Same content",
            "buttons": [{"text": "Apply Now"}],
            "fields": [],
            "has_dialog": False,
        }
        driver.get_snapshot = AsyncMock(return_value=same_snapshot)

        with patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec, \
             patch("shared.optimization.get_optimization_engine") as mock_get_opt, \
             patch("jobpulse.page_analysis.page_reasoner.get_page_reasoner") as mock_get_reasoner:
            MockExec.return_value.execute = AsyncMock()
            mock_engine = MagicMock()
            mock_engine.emit = MagicMock()
            mock_get_opt.return_value = mock_engine
            mock_reasoner = MagicMock()
            mock_reasoner.invalidate = MagicMock(return_value=True)
            mock_reasoner.reason_with_failure = MagicMock(
                return_value=PageAction(
                    page_understanding="recover", action="click_apply",
                    target_text="", reasoning="reflected", confidence=0.6,
                    page_type="job_description",
                )
            )
            mock_get_reasoner.return_value = mock_reasoner

            ctx = StepContext(
                snapshot=same_snapshot,
                url="https://example.com/jobs/1",
                tab_state=TabState.NORMAL,
                planned_action=PageAction(
                    page_understanding="Replay learned",
                    action="click_element",
                    target_text="",
                    reasoning="learned",
                    confidence=0.9,
                    page_type="job_description",
                ),
                plan_source="learned_verified",
                page_fingerprint=PageFingerprint(
                    field_count=0, button_texts=("Apply Now",), content_hash="abc",
                    has_dialog=False, has_file_inputs=False,
                    page_type="job_description", dom_confidence=0.8,
                    url_path_pattern="/jobs/{id}",
                ),
            )
            result = await nav._phase_act(ctx, "greenhouse", [], 0)

        assert result.ghost_click is True, "ctx.ghost_click must be set for learned-replay ghost clicks"
        assert mock_engine.emit.called, "OptimizationEngine.emit must fire on ghost-click recovery"
        emit_kwargs = mock_engine.emit.call_args.kwargs
        assert emit_kwargs.get("signal_type") == "failure"
        assert emit_kwargs.get("payload", {}).get("param") == "ghost_click"
        assert mock_reasoner.invalidate.called, "PageReasoner.invalidate must run on ghost-click recovery"
        assert mock_reasoner.reason_with_failure.called, "reason_with_failure must run on ghost-click recovery"

    @pytest.mark.asyncio
    async def test_step_appended_with_fingerprint(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.com/apply", "page_text_preview": "New page", "buttons": [], "fields": [{"label": "Name"}], "has_dialog": False})

        with patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:
            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            steps_list: list[dict] = []
            fp = PageFingerprint(
                field_count=0, button_texts=("Apply Now",), content_hash="abc",
                has_dialog=False, has_file_inputs=False,
                page_type="job_description", dom_confidence=0.9,
                url_path_pattern="/jobs/{id}",
            )
            ctx = StepContext(
                snapshot={"url": "https://example.com/jobs/1", "page_text_preview": "Old page", "buttons": [{"text": "Apply Now"}], "fields": [], "has_dialog": False},
                url="https://example.com/jobs/1",
                tab_state=TabState.NORMAL,
                planned_action=PageAction(
                    page_understanding="JD", action="click_element",
                    target_text="Apply Now", reasoning="click", confidence=0.8,
                    page_type="job_description",
                ),
                plan_source="reasoner",
                page_fingerprint=fp,
            )
            result = await nav._phase_act(ctx, "greenhouse", steps_list, 0)

        assert len(steps_list) == 1
        assert "fingerprint" in steps_list[0]
        assert steps_list[0]["fingerprint"]["page_type"] == "job_description"


class TestNavigateToFormIntegration:
    @pytest.mark.asyncio
    async def test_simple_job_description_to_form(self, mock_navigator):
        """JD page -> click apply -> application form. 2 steps."""
        nav, driver, page, context = mock_navigator
        jd_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
            "fields": [],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        form_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123/apply",
            "page_text_preview": "Application Form - First Name Last Name",
            "buttons": [{"text": "Submit"}],
            "fields": [
                {"label": "First Name", "input_type": "text"},
                {"label": "Last Name", "input_type": "text"},
                {"label": "Resume", "input_type": "file"},
            ],
            "has_dialog": False,
            "has_file_inputs": True,
            "verification_wall": None,
        }

        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            # Calls: 1=initial nav, 2=OBSERVE, 3=ANALYZE re-snapshot → all JD
            # After ACT navigates away: 4+=form_snapshot
            return jd_snapshot if call_count[0] <= 3 else form_snapshot
        driver.get_snapshot = _get_snap
        driver.navigate = AsyncMock()
        nav.learner.get_sequence.return_value = None
        nav.learner.get_platform_pattern.return_value = None

        mock_clf = MagicMock()
        # classify called: 1=initial ANALYZE, 2=re-classify after dismiss (same JD),
        # 3=second loop ANALYZE → form
        clf_returns = iter([
            (PageType.JOB_DESCRIPTION, 0.9),
            (PageType.JOB_DESCRIPTION, 0.9),
            (PageType.APPLICATION_FORM, 0.92),
        ])
        mock_clf.classify.side_effect = lambda s: next(clf_returns, (PageType.APPLICATION_FORM, 0.92))
        nav._classifier = mock_clf

        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as MockReasoner, \
             patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock), \
             patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:

            reasoner_instance = MockReasoner.return_value
            reasoner_instance.reason_sync.return_value = PageAction(
                page_understanding="JD with apply button",
                action="click_element",
                target_text="Apply Now",
                reasoning="click to apply",
                confidence=0.9,
                page_type="job_description",
            )

            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            steps: list[dict] = []
            result = await nav.navigate_to_form(
                url="https://boards.greenhouse.io/company/jobs/123",
                platform="greenhouse",
                steps=steps,
            )

        assert result["page_type"] == PageType.APPLICATION_FORM
        assert len(steps) >= 1
        assert "fingerprint" in steps[0]

    @pytest.mark.asyncio
    async def test_learned_replay_with_verification(self, mock_navigator):
        """Learned sequence matches -> verified -> executed without LLM."""
        nav, driver, page, context = mock_navigator
        fp_dict = {
            "field_count": 0,
            "button_texts": ["Apply Now"],
            "content_hash": "abc123",
            "page_type": "job_description",
            "dom_confidence": 0.9,
            "url_path_pattern": "/company/jobs/{id}",
            "has_dialog": False,
            "has_file_inputs": False,
        }
        nav.learner.get_sequence.return_value = [
            {"page_type": "job_description", "action": "click_apply", "fingerprint": fp_dict}
        ]
        nav.learner.increment_replay = MagicMock()

        jd_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/456",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
            "fields": [],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        form_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/456/apply",
            "page_text_preview": "Application Form - First Name",
            "buttons": [{"text": "Submit"}],
            "fields": [{"label": "First Name", "input_type": "text"}],
            "has_dialog": False,
            "has_file_inputs": True,
            "verification_wall": None,
        }
        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            # Calls: 1=initial nav, 2=OBSERVE, 3=ANALYZE re-snapshot → JD
            # After click_apply: 4+=form_snapshot
            return jd_snapshot if call_count[0] <= 3 else form_snapshot
        driver.get_snapshot = _get_snap
        driver.navigate = AsyncMock()
        nav.click_apply_button = AsyncMock(return_value=form_snapshot)

        mock_clf = MagicMock()
        clf_returns = iter([
            (PageType.JOB_DESCRIPTION, 0.9),
            (PageType.JOB_DESCRIPTION, 0.9),
            (PageType.APPLICATION_FORM, 0.92),
        ])
        mock_clf.classify.side_effect = lambda s: next(clf_returns, (PageType.APPLICATION_FORM, 0.92))
        nav._classifier = mock_clf

        with patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock):
            steps: list[dict] = []
            result = await nav.navigate_to_form(
                url="https://boards.greenhouse.io/company/jobs/456",
                platform="greenhouse",
                steps=steps,
            )

        assert result["page_type"] == PageType.APPLICATION_FORM
        assert any(s.get("action") == "click_apply" for s in steps)
