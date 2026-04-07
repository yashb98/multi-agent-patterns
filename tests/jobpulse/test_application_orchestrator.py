"""Comprehensive tests for ApplicationOrchestrator — navigation, form filling, edge cases.

Covers:
- Full navigation flow: JD → Apply click → form detection
- Verification wall abort at navigation and form phases
- Login/SSO/signup/email verification flows
- Multi-page form filling with state machine
- Stuck detection and page exhaustion
- Dry-run mode
- Domain extraction edge cases
- Apply button regex matching
- Signup link detection
- Cookie dismiss integration
- Learned sequence save on success
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.application_orchestrator import (
    MAX_FORM_PAGES,
    MAX_NAVIGATION_STEPS,
    ApplicationOrchestrator,
)
from jobpulse.ext_models import (
    ButtonInfo,
    FieldInfo,
    PageSnapshot,
    PageType,
    VerificationWall,
)


# =========================================================================
# Fixtures
# =========================================================================


def _snap(
    url="https://example.com",
    title="Test",
    fields=None,
    buttons=None,
    wall=None,
    text="",
    has_files=False,
):
    return PageSnapshot(
        url=url,
        title=title,
        fields=fields or [],
        buttons=buttons or [],
        verification_wall=wall,
        page_text_preview=text,
        has_file_inputs=has_files,
        iframe_count=0,
        timestamp=1000,
    )


def _snap_dict(**kwargs):
    return _snap(**kwargs).model_dump()


@pytest.fixture
def bridge():
    b = AsyncMock()
    b.navigate = AsyncMock()
    b.fill = AsyncMock()
    b.click = AsyncMock()
    b.upload = AsyncMock()
    b.get_snapshot = AsyncMock()
    b.screenshot = AsyncMock(return_value=b"screenshot")
    b.select_option = AsyncMock()
    b.check = AsyncMock()
    # v2 form engine methods
    b.fill_radio_group = AsyncMock()
    b.fill_custom_select = AsyncMock()
    b.fill_autocomplete = AsyncMock()
    b.fill_tag_input = AsyncMock()
    b.fill_date = AsyncMock()
    b.scroll_to = AsyncMock()
    b.force_click = AsyncMock()
    b.check_consent_boxes = AsyncMock()
    b.rescan_after_fill = AsyncMock(return_value={"validation_errors": []})
    b.wait_for_apply = AsyncMock(return_value={"waited_ms": 0, "apply_diagnostics": []})
    # MV3 state persistence — return None by default (no saved progress)
    b.get_form_progress = AsyncMock(return_value=None)
    b.save_form_progress = AsyncMock(return_value=True)
    b.clear_form_progress = AsyncMock(return_value=True)
    return b


@pytest.fixture
def orchestrator(bridge, tmp_path):
    from jobpulse.account_manager import AccountManager
    from jobpulse.navigation_learner import NavigationLearner

    orch = ApplicationOrchestrator(
        bridge=bridge,
        account_manager=AccountManager(db_path=str(tmp_path / "acc.db")),
        gmail_verifier=MagicMock(),
        navigation_learner=NavigationLearner(db_path=str(tmp_path / "nav.db")),
    )
    # Mock the page analyzer to avoid real OpenAI API calls.
    # Tests must set orchestrator.analyzer.detect.side_effect to control page type detection.
    orch.analyzer = MagicMock()
    orch.analyzer.detect = AsyncMock()
    # Also mock cookie dismisser to avoid bridge calls
    orch.cookie_dismisser = MagicMock()
    orch.cookie_dismisser.dismiss = AsyncMock()
    # Mock SSO handler
    orch.sso = MagicMock()
    orch.sso.detect_sso = MagicMock(return_value=None)
    # Use temp GotchasDB to avoid touching production data
    from jobpulse.form_engine.gotchas import GotchasDB
    orch.gotchas = GotchasDB(db_path=str(tmp_path / "gotchas.db"))
    return orch


@pytest.fixture
def cv_path(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test cv")
    return cv


# =========================================================================
# Navigation: happy paths
# =========================================================================


class TestNavigationHappyPaths:
    @pytest.mark.asyncio
    async def test_jd_page_to_form_via_apply_click(self, orchestrator, bridge, cv_path):
        """JD page → clicks Apply → reaches APPLICATION_FORM → fills → confirms."""
        jd_snap = _snap_dict(
            url="https://greenhouse.io/job/1",
            text="Software Engineer at Acme. We are looking for...",
            buttons=[
                {"selector": "#apply", "text": "Apply Now", "enabled": True, "type": "button"},
            ],
        )
        form_snap = _snap_dict(
            url="https://greenhouse.io/job/1/apply",
            fields=[
                {"selector": "#first_name", "input_type": "text", "label": "First Name"},
                {"selector": "#email", "input_type": "email", "label": "Email"},
            ],
            has_files=True,
        )
        confirm_snap = _snap_dict(
            url="https://greenhouse.io/job/1/thanks",
            text="Thank you for applying! Your application has been received.",
        )

        bridge.get_snapshot.side_effect = [
            jd_snap, jd_snap,        # after navigate + after cookie dismiss
            form_snap, form_snap,     # after apply click + after cookie dismiss
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.side_effect = [
            PageType.JOB_DESCRIPTION,
            PageType.APPLICATION_FORM,
        ]
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://greenhouse.io/job/1",
            platform="greenhouse",
            cv_path=cv_path,
            profile={"first_name": "Yash", "email": "y@test.com"},
            custom_answers={},
        )
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_direct_application_form(self, orchestrator, bridge, cv_path):
        """Direct link to application form (skips JD page)."""
        form_snap = _snap_dict(
            url="https://lever.co/apply",
            fields=[
                {"selector": "#name", "input_type": "text", "label": "Full Name"},
            ],
        )
        confirm_snap = _snap_dict(
            text="Application submitted successfully!",
        )
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,     # navigate + cookie dismiss
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://lever.co/apply",
            platform="lever",
            cv_path=cv_path,
            profile={"first_name": "Yash"},
        )
        assert result["success"] is True


# =========================================================================
# Navigation: verification wall
# =========================================================================


class TestVerificationWall:
    @pytest.mark.asyncio
    async def test_captcha_during_navigation(self, orchestrator, bridge, cv_path):
        wall_snap = _snap_dict(
            wall={
                "wall_type": "cloudflare",
                "confidence": 0.95,
                "details": "Turnstile challenge",
            },
        )
        bridge.get_snapshot.return_value = wall_snap
        orchestrator.analyzer.detect.return_value = PageType.VERIFICATION_WALL

        result = await orchestrator.apply(
            url="https://example.com/job",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False
        assert "CAPTCHA" in result["error"]

    @pytest.mark.asyncio
    async def test_captcha_during_form_fill(self, orchestrator, bridge, cv_path):
        """CAPTCHA appears mid-form after first page."""
        form_snap = _snap_dict(
            fields=[{"selector": "#q", "input_type": "text", "label": "First Name"}],
            has_files=True,
        )
        wall_snap = _snap_dict(
            wall={
                "wall_type": "recaptcha",
                "confidence": 0.9,
                "details": "",
            },
        )
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,   # navigate + cookie
            wall_snap, wall_snap, wall_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False
        assert "CAPTCHA" in result["error"]


# =========================================================================
# Navigation: unknown page
# =========================================================================


class TestUnknownPage:
    @pytest.mark.asyncio
    async def test_unknown_page_with_no_apply_button(self, orchestrator, bridge, cv_path):
        """UNKNOWN page with no identifiable apply button → abort."""
        unknown_snap = _snap_dict(
            text="Welcome to our company",
            buttons=[{"selector": "#about", "text": "About Us", "enabled": True, "type": "button"}],
        )
        bridge.get_snapshot.return_value = unknown_snap
        orchestrator.analyzer.detect.return_value = PageType.UNKNOWN

        result = await orchestrator.apply(
            url="https://company.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False
        assert "Unknown" in result["error"] or "could not reach" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_page_with_apply_guess(self, orchestrator, bridge, cv_path):
        """UNKNOWN page with a guessable 'Apply' button → clicks it."""
        unknown_snap = _snap_dict(
            text="Job details here",
            buttons=[
                {"selector": "#apply", "text": "Apply for this job", "enabled": True, "type": "button"},
            ],
        )
        form_snap = _snap_dict(
            fields=[{"selector": "#name", "input_type": "text", "label": "Name"}],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")

        bridge.get_snapshot.side_effect = [
            unknown_snap, unknown_snap,   # navigate + cookie
            form_snap, form_snap,         # after guess click + cookie
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.side_effect = [
            PageType.UNKNOWN,
            PageType.APPLICATION_FORM,
        ]
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://company.com/careers/1",
            platform="generic",
            cv_path=cv_path,
            profile={"first_name": "Test"},
        )
        assert result["success"] is True


# =========================================================================
# Navigation: login / SSO
# =========================================================================


class TestLoginFlow:
    @pytest.mark.asyncio
    async def test_login_with_existing_account(self, orchestrator, bridge, cv_path):
        """Login page detected → fills credentials from account manager."""
        # Note: FieldInfo doesn't support 'password' type, use 'text' for password fields
        login_snap = {
            "url": "https://boards.greenhouse.io/login",
            "title": "Login",
            "fields": [
                {"selector": "#email", "input_type": "email", "label": "Email"},
                {"selector": "#pass", "input_type": "text", "label": "Password"},
            ],
            "buttons": [
                {"selector": "#login", "text": "Sign In", "enabled": True, "type": "button"},
            ],
            "verification_wall": None,
            "page_text_preview": "Sign in to continue",
            "has_file_inputs": False,
            "iframe_count": 0,
            "timestamp": 1000,
        }
        form_snap = _snap_dict(
            fields=[{"selector": "#q", "input_type": "text", "label": "First Name"}],
            has_files=True,
        )
        confirm_snap = _snap_dict(text="Application submitted")

        # Pre-create account — patch the config value used by account_manager
        with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "TestPass123!"):
            orchestrator.accounts.create_account("boards.greenhouse.io")
        bridge.get_snapshot.side_effect = [
            login_snap, login_snap,     # navigate + cookie
            form_snap, form_snap,       # after login + cookie
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.side_effect = [
            PageType.LOGIN_FORM,
            PageType.APPLICATION_FORM,
        ]
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://boards.greenhouse.io/login",
            platform="greenhouse",
            cv_path=cv_path,
            profile={"first_name": "Yash"},
        )
        assert result["success"] is True
        assert bridge.fill.call_count >= 2  # email + password


# =========================================================================
# Form filling: stuck detection
# =========================================================================


class TestStuckDetection:
    @pytest.mark.asyncio
    async def test_stuck_page_aborts_after_2_identical(self, orchestrator, bridge, cv_path):
        """Same page content 3 times → stuck_count=2 → abort."""
        stuck_text = "x" * 800
        stuck_snap = _snap_dict(
            fields=[{"selector": "#q1", "input_type": "text", "label": "Question"}],
            text=stuck_text,
        )
        bridge.get_snapshot.side_effect = [
            stuck_snap, stuck_snap,   # navigate + cookie
            stuck_snap, stuck_snap, stuck_snap, stuck_snap,
            stuck_snap, stuck_snap, stuck_snap, stuck_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False
        assert "Stuck" in result.get("error", "")

    @pytest.mark.asyncio
    async def test_short_page_text_not_stuck(self, orchestrator, bridge, cv_path):
        """Short text (<10 chars in slice) should NOT trigger stuck detection."""
        short_snap = _snap_dict(
            fields=[{"selector": "#q1", "input_type": "text", "label": "Name"}],
            text="Hello",
        )
        confirm_snap = _snap_dict(text="Thank you for applying")

        bridge.get_snapshot.side_effect = [
            short_snap, short_snap,     # navigate + cookie
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is True


# =========================================================================
# Form filling: dry-run
# =========================================================================


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_stops_at_submit(self, orchestrator, bridge, cv_path):
        """Dry run stops before clicking submit."""
        submit_snap = _snap_dict(
            fields=[],
            buttons=[
                {"selector": "#submit", "text": "Submit Application", "enabled": True, "type": "button"},
            ],
            text="Review your application",
        )
        bridge.get_snapshot.side_effect = [
            submit_snap, submit_snap,   # navigate + cookie
            submit_snap, submit_snap, submit_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
            dry_run=True,
        )
        assert result.get("dry_run") is True


# =========================================================================
# Form filling: max pages exhaustion
# =========================================================================


class TestMaxPages:
    @pytest.mark.asyncio
    async def test_page_exhaustion(self, orchestrator, bridge, cv_path):
        """Exceeding MAX_FORM_PAGES returns error."""
        # Each page must be unique in chars[200:700] to avoid stuck detection
        def make_page(i):
            # Pad start with 200 chars of wrapper, then unique content in [200:700]
            unique_middle = f"UNIQUE_PAGE_{i}_" * 35  # ~500 chars
            text = ("W" * 200) + unique_middle + ("T" * 100)
            return {
                "url": f"https://example.com/page/{i}",
                "title": "Test",
                "fields": [{"selector": f"#q{i}", "input_type": "text", "label": f"Question {i}"}],
                "buttons": [{"selector": "#next", "text": "Next", "enabled": True, "type": "button"}],
                "verification_wall": None,
                "page_text_preview": text,
                "has_file_inputs": False,
                "iframe_count": 0,
                "timestamp": 1000 + i,
            }

        pages = [make_page(i) for i in range(MAX_FORM_PAGES + 5)]

        # Each get_snapshot call in the form loop gets the NEXT page
        bridge.get_snapshot.side_effect = [
            pages[0], pages[0],  # navigate + cookie
        ] + [pages[i % len(pages)] for i in range(MAX_FORM_PAGES * 2)]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False


# =========================================================================
# Static helpers
# =========================================================================


class TestStaticHelpers:
    def test_extract_domain_standard(self):
        assert ApplicationOrchestrator._extract_domain("https://www.greenhouse.io/apply") == "greenhouse.io"

    def test_extract_domain_no_www(self):
        assert ApplicationOrchestrator._extract_domain("https://lever.co/jobs/1") == "lever.co"

    def test_extract_domain_empty_url(self):
        assert ApplicationOrchestrator._extract_domain("") == ""

    def test_extract_domain_no_scheme(self):
        """URL without scheme — urlparse puts everything in path."""
        result = ApplicationOrchestrator._extract_domain("example.com")
        assert result == "example.com"  # falls to else branch

    def test_find_apply_button_matches(self):
        snap = {
            "buttons": [
                {"selector": "#about", "text": "About", "enabled": True},
                {"selector": "#apply", "text": "Apply Now", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is not None
        assert btn["selector"] == "#apply"

    def test_find_apply_button_disabled(self):
        snap = {
            "buttons": [
                {"selector": "#apply", "text": "Apply Now", "enabled": False},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is None

    def test_find_apply_button_no_match(self):
        snap = {
            "buttons": [
                {"selector": "#login", "text": "Sign In", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_apply_button(snap)
        assert btn is None

    def test_find_signup_link(self):
        snap = {
            "buttons": [
                {"selector": "#signup", "text": "Create Account", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_signup_link(snap)
        assert btn is not None

    def test_find_signup_link_dont_have(self):
        snap = {
            "buttons": [
                {"selector": "#signup", "text": "Don't have an account?", "enabled": True},
            ],
        }
        btn = ApplicationOrchestrator._find_signup_link(snap)
        assert btn is not None

    def test_as_dict_pydantic_model(self):
        snap = _snap(url="https://test.com")
        result = ApplicationOrchestrator._as_dict(snap)
        assert isinstance(result, dict)
        assert result["url"] == "https://test.com"

    def test_as_dict_already_dict(self):
        d = {"url": "https://test.com"}
        result = ApplicationOrchestrator._as_dict(d)
        assert result is d

    def test_to_page_snapshot_from_dict(self):
        raw = {
            "url": "https://test.com",
            "title": "Test",
            "fields": [
                {"selector": "#q", "input_type": "text", "label": "Name"},
            ],
            "buttons": [
                {"selector": "#btn", "text": "Next", "type": "button", "enabled": True},
            ],
            "verification_wall": None,
            "page_text_preview": "Hello",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert isinstance(snap, PageSnapshot)
        assert len(snap.fields) == 1
        assert len(snap.buttons) == 1

    def test_to_page_snapshot_malformed_field_skipped(self):
        """Malformed field dict is silently skipped."""
        raw = {
            "url": "",
            "title": "",
            "fields": [
                {"bad_key": "no selector"},  # will raise on FieldInfo(**f)
                {"selector": "#ok", "input_type": "text", "label": "OK"},
            ],
            "buttons": [],
            "verification_wall": None,
            "page_text_preview": "",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert len(snap.fields) == 1  # malformed one skipped

    def test_to_page_snapshot_malformed_button_skipped(self):
        raw = {
            "url": "",
            "title": "",
            "fields": [],
            "buttons": [
                {"bad": True},  # malformed
                {"selector": "#ok", "text": "OK"},
            ],
            "verification_wall": None,
            "page_text_preview": "",
            "has_file_inputs": False,
        }
        snap = ApplicationOrchestrator._to_page_snapshot(raw)
        assert len(snap.buttons) == 1


# =========================================================================
# Navigation step limit
# =========================================================================


class TestNavigationLimit:
    @pytest.mark.asyncio
    async def test_max_navigation_steps_reached(self, orchestrator, bridge, cv_path):
        """If we never reach APPLICATION_FORM within MAX_NAVIGATION_STEPS, return UNKNOWN."""
        jd_snap = _snap_dict(
            text="Job description content here",
            buttons=[
                {"selector": "#apply", "text": "Apply", "enabled": True, "type": "button"},
            ],
        )
        bridge.get_snapshot.return_value = jd_snap
        # Always return JOB_DESCRIPTION — never progresses to form
        orchestrator.analyzer.detect.return_value = PageType.JOB_DESCRIPTION

        result = await orchestrator.apply(
            url="https://example.com",
            platform="generic",
            cv_path=cv_path,
        )
        assert result["success"] is False


# =========================================================================
# Learned sequence saved on success
# =========================================================================


class TestLearnedSequence:
    @pytest.mark.asyncio
    async def test_successful_apply_saves_sequence(self, orchestrator, bridge, cv_path):
        """Successful application saves navigation steps to learner."""
        form_snap = _snap_dict(
            fields=[{"selector": "#name", "input_type": "text", "label": "Name"}],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")

        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        with patch.object(orchestrator.learner, "save_sequence") as mock_save:
            result = await orchestrator.apply(
                url="https://example.com/job",
                platform="generic",
                cv_path=cv_path,
                profile={"first_name": "Yash"},
            )
            assert result["success"] is True
            mock_save.assert_called_once()


# =========================================================================
# Execute action dispatch
# =========================================================================


class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_fill_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "fill", "selector": "#q", "value": "answer"})
        bridge.fill.assert_called_once_with("#q", "answer")

    @pytest.mark.asyncio
    async def test_click_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "click", "selector": "#btn"})
        bridge.click.assert_called_once_with("#btn")

    @pytest.mark.asyncio
    async def test_select_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "select", "selector": "#dd", "value": "opt1"})
        bridge.select_option.assert_called_once_with("#dd", "opt1")

    @pytest.mark.asyncio
    async def test_check_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "check", "selector": "#cb"})
        bridge.check.assert_called_once_with("#cb", True)

    @pytest.mark.asyncio
    async def test_upload_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "upload", "selector": "#file", "file_path": "/tmp/cv.pdf"})
        bridge.upload.assert_called_once_with("#file", Path("/tmp/cv.pdf"))


# =========================================================================
# Login handler
# =========================================================================


class TestHandleLogin:
    @pytest.mark.asyncio
    async def test_login_fills_email_and_password(self, orchestrator, bridge):
        """With a known account, _handle_login fills both email and password."""
        with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "Secret123!"):
            orchestrator.accounts.create_account("login.example.com")

        login_snap = _snap_dict(
            url="https://login.example.com/sign-in",
            text="Sign in to continue",
            fields=[
                {"selector": "#email", "input_type": "email", "label": "Email"},
                {"selector": "#pass", "input_type": "text", "label": "Password"},
            ],
            buttons=[
                {"selector": "#sign-in", "text": "Sign In", "enabled": True, "type": "button"},
            ],
        )
        post_snap = _snap_dict(
            url="https://login.example.com/dashboard",
            text="Welcome back",
        )
        bridge.get_snapshot.return_value = post_snap

        result = await orchestrator._handle_login(login_snap, "generic")

        fill_calls = [str(c) for c in bridge.fill.call_args_list]
        selectors = [c.args[0] for c in bridge.fill.call_args_list]
        assert "#email" in selectors
        assert "#pass" in selectors

    @pytest.mark.asyncio
    async def test_login_no_account_redirects_to_signup(self, orchestrator, bridge):
        """No account + snapshot has signup button → clicks signup."""
        snap = _snap_dict(
            url="https://noac.example.com/login",
            text="Log in",
            buttons=[
                {"selector": "#signup-link", "text": "Create Account", "enabled": True, "type": "button"},
            ],
        )
        after_click = _snap_dict(url="https://noac.example.com/register", text="Create account")
        bridge.get_snapshot.return_value = after_click

        result = await orchestrator._handle_login(snap, "generic")

        bridge.click.assert_called_once_with("#signup-link")
        assert result["url"] == "https://noac.example.com/register"

    @pytest.mark.asyncio
    async def test_login_no_account_no_signup_returns_snapshot(self, orchestrator, bridge):
        """No account, no signup button → returns original snapshot unchanged."""
        snap = _snap_dict(
            url="https://noac2.example.com/login",
            text="Log in",
            buttons=[
                {"selector": "#about", "text": "About Us", "enabled": True, "type": "button"},
            ],
        )
        result = await orchestrator._handle_login(snap, "generic")

        bridge.click.assert_not_called()
        assert result["url"] == "https://noac2.example.com/login"

    @pytest.mark.asyncio
    async def test_login_verifies_success_before_marking(self, orchestrator, bridge):
        """After clicking sign-in, if post-login page still looks like login, do NOT mark success."""
        with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "Secret123!"):
            orchestrator.accounts.create_account("verify.example.com")

        snap = _snap_dict(
            url="https://verify.example.com/login",
            text="Sign in to continue",
            fields=[
                {"selector": "#email", "input_type": "email", "label": "Email"},
                {"selector": "#pass", "input_type": "text", "label": "Password"},
            ],
            buttons=[
                {"selector": "#btn", "text": "Sign In", "enabled": True, "type": "button"},
            ],
        )
        # Post-login snapshot still looks like a login page
        still_login = _snap_dict(
            url="https://verify.example.com/login",
            text="sign in — invalid password",
        )
        bridge.fill.return_value = MagicMock(success=True)
        bridge.get_snapshot.return_value = still_login

        with patch.object(orchestrator.accounts, "mark_login_success") as mock_mark:
            await orchestrator._handle_login(snap, "generic")
            mock_mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_login_fill_failure_returns_early(self, orchestrator, bridge):
        """TimeoutError on email fill → returns snapshot without clicking sign-in."""
        with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "Secret123!"):
            orchestrator.accounts.create_account("timeout.example.com")

        snap = _snap_dict(
            url="https://timeout.example.com/login",
            text="Sign in",
            fields=[
                {"selector": "#email", "input_type": "email", "label": "Email"},
                {"selector": "#pass", "input_type": "text", "label": "Password"},
            ],
            buttons=[
                {"selector": "#btn", "text": "Log In", "enabled": True, "type": "button"},
            ],
        )
        # email fill raises TimeoutError; password fill is fine
        bridge.fill.side_effect = [TimeoutError("timeout"), MagicMock(success=True)]
        bridge.get_snapshot.return_value = _snap_dict(url="https://timeout.example.com/dashboard")

        result = await orchestrator._handle_login(snap, "generic")

        # Should not have clicked sign-in because email fill failed
        bridge.click.assert_not_called()


# =========================================================================
# Signup handler
# =========================================================================


class TestHandleSignup:
    @pytest.mark.asyncio
    async def test_signup_creates_account_fills_fields(self, orchestrator, bridge):
        """_handle_signup creates an account and fills all profile fields."""
        snap = _snap_dict(
            url="https://signup.example.com/register",
            text="Create your account",
            fields=[
                {"selector": "#email", "input_type": "email", "label": "Email"},
                {"selector": "#pass", "input_type": "text", "label": "Password"},
                {"selector": "#fname", "input_type": "text", "label": "First Name"},
                {"selector": "#lname", "input_type": "text", "label": "Last Name"},
                {"selector": "#phone", "input_type": "tel", "label": "Phone"},
            ],
            buttons=[
                {"selector": "#create", "text": "Create Account", "enabled": True, "type": "button"},
            ],
        )
        after_snap = _snap_dict(url="https://signup.example.com/verify", text="Check your email")
        bridge.get_snapshot.return_value = after_snap

        with patch("jobpulse.config.ATS_ACCOUNT_PASSWORD", "Secret123!"):
            with patch("jobpulse.applicator.PROFILE", {
                "first_name": "Yash",
                "last_name": "Bishnoi",
                "email": "yash@test.com",
                "phone": "+447000000000",
            }):
                result = await orchestrator._handle_signup(snap, "generic")

        filled_selectors = [c.args[0] for c in bridge.fill.call_args_list]
        assert "#email" in filled_selectors
        assert "#fname" in filled_selectors
        assert "#lname" in filled_selectors
        # Account should now exist for this domain
        assert orchestrator.accounts.has_account("signup.example.com")


# =========================================================================
# Pre-submit gate
# =========================================================================


class TestPreSubmitGate:
    @pytest.mark.asyncio
    async def test_gate_passes_on_good_answers(self, orchestrator, bridge, cv_path):
        """Gate passes → result is success=True with gate_score populated."""
        form_snap = _snap_dict(
            fields=[{"selector": "#q", "input_type": "text", "label": "Name"}],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        company_research = MagicMock()
        with patch("jobpulse.pre_submit_gate.PreSubmitGate") as MockGate:
            from jobpulse.pre_submit_gate import GateResult
            MockGate.return_value.review.return_value = GateResult(passed=True, score=8.5)
            result = await orchestrator.apply(
                url="https://example.com",
                platform="generic",
                cv_path=cv_path,
                company_research=company_research,
            )

        assert result["success"] is True
        assert result.get("gate_score") == 8.5

    @pytest.mark.asyncio
    async def test_gate_blocks_on_low_score(self, orchestrator, bridge, cv_path):
        """Gate returns passed=False → result includes needs_human_review."""
        form_snap = _snap_dict(
            fields=[{"selector": "#q", "input_type": "text", "label": "Name"}],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        company_research = MagicMock()
        with patch("jobpulse.pre_submit_gate.PreSubmitGate") as MockGate:
            from jobpulse.pre_submit_gate import GateResult
            MockGate.return_value.review.return_value = GateResult(
                passed=False, score=5.0, weaknesses=["Too generic"]
            )
            result = await orchestrator.apply(
                url="https://example.com",
                platform="generic",
                cv_path=cv_path,
                company_research=company_research,
            )

        assert result["success"] is False
        assert result.get("needs_human_review") is True

    def test_gate_import_error_blocks_submission(self, orchestrator):
        """ImportError on PreSubmitGate → passed=False, fail-closed."""
        company_research = MagicMock()
        with patch.dict("sys.modules", {"jobpulse.pre_submit_gate": None}):
            gate_result = orchestrator._run_pre_submit_gate(
                custom_answers={"q1": "answer"},
                jd_keywords=["python"],
                company_research=company_research,
            )
        assert gate_result.passed is False


# =========================================================================
# MV3 persistence
# =========================================================================


class TestMV3Persistence:
    @pytest.mark.asyncio
    async def test_get_form_progress_recovery(self, orchestrator, bridge, cv_path):
        """Pre-filled fields from saved progress are skipped during form fill."""
        # Saved progress says #q1 is already filled
        bridge.get_form_progress.return_value = {
            "filled_fields": [{"selector": "#q1"}],
            "current_page": 1,
        }
        form_snap = _snap_dict(
            url="https://example.com/apply",
            fields=[
                {"selector": "#q1", "input_type": "text", "label": "Question 1"},
                {"selector": "#q2", "input_type": "text", "label": "Question 2"},
            ],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        await orchestrator.apply(
            url="https://example.com/apply",
            platform="generic",
            cv_path=cv_path,
            profile={"q1": "val1", "q2": "val2"},
        )

        # bridge.fill should NOT have been called for #q1 (it was pre-filled)
        fill_selectors = [c.args[0] for c in bridge.fill.call_args_list]
        assert "#q1" not in fill_selectors

    @pytest.mark.asyncio
    async def test_save_form_progress_after_fill(self, orchestrator, bridge, cv_path):
        """bridge.save_form_progress is called after a successful fill action."""
        bridge.get_form_progress.return_value = None
        # Use an email field — state machine reliably generates a fill action for it
        form_snap = _snap_dict(
            url="https://example.com/apply",
            fields=[
                {"selector": "#email", "input_type": "email", "label": "Email"},
            ],
        )
        confirm_snap = _snap_dict(text="Thank you for applying")
        bridge.get_snapshot.side_effect = [
            form_snap, form_snap,
            confirm_snap, confirm_snap, confirm_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM
        bridge.fill.return_value = MagicMock(success=True)

        await orchestrator.apply(
            url="https://example.com/apply",
            platform="generic",
            cv_path=cv_path,
            profile={"email": "y@test.com"},
        )

        bridge.save_form_progress.assert_called()

    @pytest.mark.asyncio
    async def test_clear_form_progress_after_verified_submit(self, orchestrator, bridge, cv_path):
        """After verified submission, bridge.clear_form_progress is called."""
        bridge.get_form_progress.return_value = None
        submit_snap = _snap_dict(
            url="https://example.com/apply",
            fields=[],
            buttons=[
                {"selector": "#submit", "text": "Submit Application", "enabled": True, "type": "button"},
            ],
            text="Review your answers",
        )
        bridge.get_snapshot.side_effect = [
            submit_snap, submit_snap,
            submit_snap, submit_snap, submit_snap,
        ]
        orchestrator.analyzer.detect.return_value = PageType.APPLICATION_FORM

        # Mock _verify_submission to return verified
        with patch.object(orchestrator, "_verify_submission", AsyncMock(return_value={"verified": True})):
            result = await orchestrator.apply(
                url="https://example.com/apply",
                platform="generic",
                cv_path=cv_path,
            )

        bridge.clear_form_progress.assert_called()


# =========================================================================
# Gotcha application
# =========================================================================


class TestGotchaApplication:
    @pytest.mark.asyncio
    async def test_gotcha_use_force_click_modifies_action(self, orchestrator, bridge):
        """use_force_click solution → bridge.force_click called instead of bridge.fill."""
        orchestrator.gotchas.store(
            "example.com", "#tricky-btn", "click fails silently", "use_force_click"
        )
        # Action with type=fill but gotcha redirects to force_click
        action = {"type": "fill", "selector": "#tricky-btn", "value": "test"}
        modified = orchestrator._apply_gotcha_to_action(action, "use_force_click")
        assert modified["type"] == "force_click"

        await orchestrator._execute_action(modified)
        bridge.force_click.assert_called_once_with("#tricky-btn")

    @pytest.mark.asyncio
    async def test_gotcha_use_selector_swaps_selector(self, orchestrator, bridge):
        """use_selector:#alt solution → new selector is used in action."""
        action = {"type": "fill", "selector": "#old", "value": "hello"}
        modified = orchestrator._apply_gotcha_to_action(action, "use_selector:#alt-input")
        assert modified["selector"] == "#alt-input"
        assert modified["type"] == "fill"

        await orchestrator._execute_action(modified)
        bridge.fill.assert_called_once_with("#alt-input", "hello")

    @pytest.mark.asyncio
    async def test_gotcha_scroll_first_pre_step(self, orchestrator, bridge):
        """scroll_first solution → bridge.scroll_to called as pre-step."""
        await orchestrator._execute_gotcha_pre_steps("scroll_first", "#target-field")
        bridge.scroll_to.assert_called_once_with("#target-field")


# =========================================================================
# Execute action — v2 action types
# =========================================================================


class TestExecuteActionV2Types:
    @pytest.mark.asyncio
    async def test_fill_radio_group_action(self, orchestrator, bridge):
        await orchestrator._execute_action(
            {"type": "fill_radio_group", "selector": "#radio", "value": "Yes"}
        )
        bridge.fill_radio_group.assert_called_once_with("#radio", "Yes")

    @pytest.mark.asyncio
    async def test_fill_custom_select_action(self, orchestrator, bridge):
        await orchestrator._execute_action(
            {"type": "fill_custom_select", "selector": "#cust-dd", "value": "Option B"}
        )
        bridge.fill_custom_select.assert_called_once_with("#cust-dd", "Option B")

    @pytest.mark.asyncio
    async def test_fill_autocomplete_action(self, orchestrator, bridge):
        await orchestrator._execute_action(
            {"type": "fill_autocomplete", "selector": "#ac", "value": "London"}
        )
        bridge.fill_autocomplete.assert_called_once_with("#ac", "London")

    @pytest.mark.asyncio
    async def test_fill_tag_input_action(self, orchestrator, bridge):
        """fill_tag_input splits comma-separated value into list."""
        await orchestrator._execute_action(
            {"type": "fill_tag_input", "selector": "#tags", "value": "Python, Django, REST"}
        )
        bridge.fill_tag_input.assert_called_once_with("#tags", ["Python", "Django", "REST"])

    @pytest.mark.asyncio
    async def test_fill_date_action(self, orchestrator, bridge):
        await orchestrator._execute_action(
            {"type": "fill_date", "selector": "#dob", "value": "1995-01-15"}
        )
        bridge.fill_date.assert_called_once_with("#dob", "1995-01-15")
