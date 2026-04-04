"""Application orchestrator — navigates redirect chains, handles account lifecycle,
and delegates form filling to the state machine.

Flow: URL → cookie dismiss → page stability wait → detect page type (DOM+Vision)
     → navigate (Apply clicks, SSO, login, signup, verify) → application form
     → state machine multi-page fill → submit → save learned sequence
"""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from shared.logging_config import get_logger

from jobpulse.account_manager import AccountManager
from jobpulse.cookie_dismisser import CookieBannerDismisser
from jobpulse.ext_models import ButtonInfo, FieldInfo, PageSnapshot, PageType
from jobpulse.gmail_verify import GmailVerifier
from jobpulse.navigation_learner import NavigationLearner
from jobpulse.page_analyzer import PageAnalyzer
from jobpulse.sso_handler import SSOHandler
from jobpulse.state_machines import (
    ApplicationState,
    find_next_button,
    get_state_machine,
    is_page_stuck,
)

logger = get_logger(__name__)

MAX_NAVIGATION_STEPS = 10
MAX_FORM_PAGES = 20


class ApplicationOrchestrator:
    def __init__(
        self,
        bridge: Any,
        account_manager: AccountManager | None = None,
        gmail_verifier: GmailVerifier | None = None,
        navigation_learner: NavigationLearner | None = None,
    ):
        self.bridge = bridge
        self.accounts = account_manager or AccountManager()
        self.gmail = gmail_verifier or GmailVerifier()
        self.learner = navigation_learner or NavigationLearner()
        self.analyzer = PageAnalyzer(bridge)
        self.cookie_dismisser = CookieBannerDismisser(bridge)
        self.sso = SSOHandler(bridge)

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        """Ensure snapshot is a plain dict (handles both dicts and Pydantic models)."""
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    async def apply(
        self,
        url: str,
        platform: str,
        cv_path: Path,
        cover_letter_path: Path | None = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict | None = None,
        dry_run: bool = False,
        form_intelligence: Any | None = None,
    ) -> dict:
        """Full application flow: navigate → account → verify → fill → submit."""
        profile = profile or {}
        custom_answers = custom_answers or {}
        navigation_steps: list[dict] = []

        # Phase 1: Navigate to application form
        nav_result = await self._navigate_to_form(url, platform, navigation_steps)
        page_type = nav_result["page_type"]

        if page_type == PageType.VERIFICATION_WALL:
            return {"success": False, "error": "CAPTCHA wall", "screenshot": nav_result.get("screenshot")}

        if page_type == PageType.UNKNOWN:
            return {"success": False, "error": "Unknown page — could not reach application form", "screenshot": nav_result.get("screenshot")}

        if page_type != PageType.APPLICATION_FORM:
            return {"success": False, "error": f"Stuck on {page_type}", "screenshot": nav_result.get("screenshot")}

        # Phase 2: Multi-page form filling
        result = await self._fill_application(
            platform=platform,
            snapshot=nav_result["snapshot"],
            cv_path=cv_path,
            cover_letter_path=cover_letter_path,
            profile=profile,
            custom_answers=custom_answers,
            overrides=overrides,
            dry_run=dry_run,
            form_intelligence=form_intelligence,
        )

        # Save successful navigation for future replay
        if result.get("success"):
            domain = self._extract_domain(url)
            self.learner.save_sequence(domain, navigation_steps, success=True)

        return result

    async def _navigate_to_form(
        self, url: str, platform: str, steps: list[dict]
    ) -> dict:
        """Navigate through redirect chain to reach application form."""
        await self.bridge.navigate(url)
        snapshot = self._as_dict(await self.bridge.get_snapshot())

        # Try learned sequence first
        domain = self._extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if learned:
            logger.info("Replaying learned navigation for %s (%d steps)", domain, len(learned))
            self.learner.increment_replay(domain)

        # Dismiss cookie banner
        await self.cookie_dismisser.dismiss(snapshot)
        snapshot = self._as_dict(await self.bridge.get_snapshot())

        for step in range(MAX_NAVIGATION_STEPS):
            page_type = await self.analyzer.detect(snapshot)
            logger.info("Navigation step %d: %s", step + 1, page_type)

            if page_type in (PageType.APPLICATION_FORM, PageType.VERIFICATION_WALL, PageType.CONFIRMATION):
                return {"page_type": page_type, "snapshot": snapshot}

            if page_type == PageType.JOB_DESCRIPTION:
                snapshot = await self._click_apply_button(snapshot)
                steps.append({"page_type": "job_description", "action": "click_apply"})

            elif page_type == PageType.LOGIN_FORM:
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.bridge.get_snapshot())
                    steps.append({"page_type": "login_form", "action": f"sso_{sso['provider']}"})
                else:
                    snapshot = await self._handle_login(snapshot, platform)
                    steps.append({"page_type": "login_form", "action": "fill_login"})

            elif page_type == PageType.SIGNUP_FORM:
                snapshot = await self._handle_signup(snapshot, platform)
                steps.append({"page_type": "signup_form", "action": "fill_signup"})

            elif page_type == PageType.EMAIL_VERIFICATION:
                snapshot = await self._handle_email_verification(snapshot, platform, url)
                steps.append({"page_type": "email_verification", "action": "verify_email"})

            elif page_type == PageType.UNKNOWN:
                apply_btn = self._find_apply_button(snapshot)
                if apply_btn:
                    await self.bridge.click(apply_btn["selector"])
                    snapshot = self._as_dict(await self.bridge.get_snapshot())
                    steps.append({"page_type": "unknown", "action": "click_apply_guess"})
                else:
                    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Dismiss any new cookie banners after navigation
            await self.cookie_dismisser.dismiss(snapshot)
            snapshot = self._as_dict(await self.bridge.get_snapshot())

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def _click_apply_button(self, snapshot: dict) -> dict:
        import re
        apply_pattern = re.compile(
            r"(apply\s*(now|for\s*this)?|start\s*application|apply\s*for\s*(this\s*)?job)",
            re.IGNORECASE,
        )
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and apply_pattern.search(btn.get("text", "")):
                logger.info("Clicking: %s", btn["text"])
                await self.bridge.click(btn["selector"])
                return self._as_dict(await self.bridge.get_snapshot())
        return snapshot

    async def _handle_login(self, snapshot: dict, platform: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))

        if not self.accounts.has_account(domain):
            signup_btn = self._find_signup_link(snapshot)
            if signup_btn:
                await self.bridge.click(signup_btn["selector"])
                return self._as_dict(await self.bridge.get_snapshot())
            return snapshot

        email, password = self.accounts.get_credentials(domain)
        logger.info("Logging into %s", domain)

        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            if ftype == "email" or "email" in label:
                await self.bridge.fill(field["selector"], email)
            elif ftype == "password" or "password" in label:
                await self.bridge.fill(field["selector"], password)

        import re
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(sign\s*in|log\s*in|login)", btn.get("text", ""), re.IGNORECASE):
                await self.bridge.click(btn["selector"])
                break

        self.accounts.mark_login_success(domain)
        return self._as_dict(await self.bridge.get_snapshot())

    async def _handle_signup(self, snapshot: dict, platform: str) -> dict:
        from jobpulse.applicator import PROFILE

        domain = self._extract_domain(snapshot.get("url", ""))
        email, password = self.accounts.create_account(domain)
        logger.info("Creating account on %s", domain)

        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            sel = field.get("selector", "")

            if ftype == "email" or "email" in label:
                await self.bridge.fill(sel, email)
            elif ftype == "password":
                await self.bridge.fill(sel, password)
            elif "first" in label:
                await self.bridge.fill(sel, PROFILE.get("first_name", ""))
            elif "last" in label:
                await self.bridge.fill(sel, PROFILE.get("last_name", ""))
            elif "name" in label and "user" not in label:
                await self.bridge.fill(sel, f"{PROFILE.get('first_name', '')} {PROFILE.get('last_name', '')}".strip())
            elif "phone" in label or ftype == "tel":
                await self.bridge.fill(sel, PROFILE.get("phone", ""))

        import re
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(create|sign\s*up|register|join|submit)", btn.get("text", ""), re.IGNORECASE):
                await self.bridge.click(btn["selector"])
                break

        return self._as_dict(await self.bridge.get_snapshot())

    async def _handle_email_verification(self, snapshot: dict, platform: str, return_url: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))
        logger.info("Waiting for verification email from %s", domain)

        link = self.gmail.wait_for_verification(domain)
        if not link:
            logger.warning("Verification email not received for %s", domain)
            return snapshot

        await self.bridge.navigate(link)
        self._as_dict(await self.bridge.get_snapshot())
        self.accounts.mark_verified(domain)

        logger.info("Returning to application: %s", return_url[:80])
        await self.bridge.navigate(return_url)
        return self._as_dict(await self.bridge.get_snapshot())

    @staticmethod
    def _to_page_snapshot(snapshot: dict) -> PageSnapshot:
        """Convert raw dict snapshot from bridge to a PageSnapshot Pydantic model."""
        raw_fields = snapshot.get("fields", [])
        raw_buttons = snapshot.get("buttons", [])

        fields: list[FieldInfo] = []
        for f in raw_fields:
            with contextlib.suppress(Exception):
                fields.append(FieldInfo(**f) if isinstance(f, dict) else f)

        buttons: list[ButtonInfo] = []
        for b in raw_buttons:
            with contextlib.suppress(Exception):
                buttons.append(ButtonInfo(**b) if isinstance(b, dict) else b)

        vwall = snapshot.get("verification_wall")

        return PageSnapshot(
            url=snapshot.get("url", ""),
            title=snapshot.get("title", ""),
            fields=fields,
            buttons=buttons,
            verification_wall=vwall if isinstance(vwall, dict) or vwall is None else None,
            page_text_preview=snapshot.get("page_text_preview", ""),
            has_file_inputs=snapshot.get("has_file_inputs", False),
        )

    async def _fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        """Multi-page form filling via state machine."""
        machine = get_state_machine(platform)
        prev_snapshot = None
        stuck_count = 0
        last_screenshot = None

        for page_num in range(1, MAX_FORM_PAGES + 1):
            page_snapshot = self._to_page_snapshot(snapshot) if isinstance(snapshot, dict) else snapshot
            state = machine.detect_state(page_snapshot)
            logger.info("Form page %d: state=%s", page_num, state)

            if state == ApplicationState.CONFIRMATION:
                return {"success": True, "screenshot": last_screenshot, "pages_filled": page_num}
            if state == ApplicationState.VERIFICATION_WALL:
                return {"success": False, "error": "CAPTCHA during form", "screenshot": last_screenshot}
            if state == ApplicationState.ERROR:
                return {"success": False, "error": "State machine error", "screenshot": last_screenshot}

            if prev_snapshot and is_page_stuck(prev_snapshot, snapshot):
                stuck_count += 1
                if stuck_count >= 2:
                    return {"success": False, "error": f"Stuck on page {page_num}", "screenshot": last_screenshot}
            else:
                stuck_count = 0

            actions = machine.get_actions(
                state, page_snapshot, profile=profile, custom_answers=custom_answers,
                cv_path=str(cv_path) if cv_path else "",
                cl_path=str(cover_letter_path) if cover_letter_path else None,
                form_intelligence=form_intelligence,
            )

            for action in actions:
                await self._execute_action(action)

            screenshot_bytes = await self.bridge.screenshot()
            if screenshot_bytes:
                last_screenshot = screenshot_bytes

            if state == ApplicationState.SUBMIT:
                if dry_run:
                    return {"success": True, "dry_run": True, "screenshot": last_screenshot, "pages_filled": page_num}
                submit_btn = find_next_button(snapshot.get("buttons", []))
                if submit_btn:
                    await self.bridge.click(submit_btn["selector"])
            else:
                next_btn = find_next_button(snapshot.get("buttons", []))
                if next_btn:
                    await self.bridge.click(next_btn["selector"])

            prev_snapshot = snapshot
            snapshot = self._as_dict(await self.bridge.get_snapshot())

        return {"success": False, "error": f"Exhausted {MAX_FORM_PAGES} pages", "screenshot": last_screenshot}

    async def _execute_action(self, action: Any):
        if hasattr(action, "model_dump"):
            # Pydantic Action model
            atype = getattr(action, "type", "")
            selector = getattr(action, "selector", "")
            value = getattr(action, "value", "")
            file_path = getattr(action, "file_path", None)
        else:
            atype = action.get("type", "")
            selector = action.get("selector", "")
            value = action.get("value", "")
            file_path = action.get("file_path")

        if atype == "fill":
            await self.bridge.fill(selector, value)
        elif atype == "upload":
            await self.bridge.upload(selector, str(file_path))
        elif atype == "click":
            await self.bridge.click(selector)
        elif atype == "select":
            await self.bridge.select_option(selector, value)
        elif atype == "check":
            await self.bridge.check(selector)

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url

    @staticmethod
    def _find_apply_button(snapshot: dict) -> dict | None:
        import re
        pattern = re.compile(r"(apply|start\s*application|begin|submit\s*interest)", re.IGNORECASE)
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and pattern.search(btn.get("text", "")):
                return btn
        return None

    @staticmethod
    def _find_signup_link(snapshot: dict) -> dict | None:
        import re
        pattern = re.compile(r"(create\s*account|sign\s*up|register|don.?t\s*have|new\s*user)", re.IGNORECASE)
        for btn in snapshot.get("buttons", []):
            if pattern.search(btn.get("text", "")):
                return btn
        return None
