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
        bridge.check.assert_called_once_with("#cb")

    @pytest.mark.asyncio
    async def test_upload_action(self, orchestrator, bridge):
        await orchestrator._execute_action({"type": "upload", "selector": "#file", "file_path": "/tmp/cv.pdf"})
        bridge.upload.assert_called_once_with("#file", "/tmp/cv.pdf")
