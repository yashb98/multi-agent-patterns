"""Application orchestrator — navigates redirect chains, handles account lifecycle,
and delegates form filling to the state machine.

Flow: URL → cookie dismiss → page stability wait → detect page type (DOM+Vision)
     → navigate (Apply clicks, SSO, login, signup, verify) → application form
     → state machine multi-page fill → submit → save learned sequence

Split into focused modules:
- _navigator.py — redirect chain navigation, apply button detection
- _auth.py — login, signup, email verification
- _form_filler.py — multi-page form filling, two-phase fill, gotchas
- _executor.py — action dispatch to driver with retry

ApplicationOrchestrator is the public facade — same API as before the split.
"""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from jobpulse.perplexity import CompanyResearch

from shared.logging_config import get_logger

from jobpulse.account_manager import AccountManager
from jobpulse.cookie_dismisser import CookieBannerDismisser
from jobpulse.ext_models import ButtonInfo, FieldInfo, PageSnapshot, PageType
from jobpulse.gmail_verify import GmailVerifier
from jobpulse.navigation_learner import NavigationLearner
from jobpulse.page_analyzer import PageAnalyzer
from jobpulse.form_engine.gotchas import GotchasDB
from jobpulse.sso_handler import SSOHandler

from jobpulse.application_orchestrator_pkg._executor import ActionExecutor
from jobpulse.application_orchestrator_pkg._auth import AuthHandler
from jobpulse.application_orchestrator_pkg._navigator import FormNavigator, extract_domain
from jobpulse.application_orchestrator_pkg._form_filler import FormFiller

logger = get_logger(__name__)


class ApplicationOrchestrator:
    def __init__(
        self,
        bridge=None,
        driver=None,
        engine: str = "extension",
        account_manager: AccountManager | None = None,
        gmail_verifier: GmailVerifier | None = None,
        navigation_learner: NavigationLearner | None = None,
    ):
        # Support both old bridge= and new driver= parameter
        self.driver = driver or bridge
        # Keep self.bridge as alias for backward compat
        self.bridge = self.driver
        self.engine = engine
        self.accounts = account_manager or AccountManager()
        self.gmail = gmail_verifier or GmailVerifier()
        self.learner = navigation_learner or NavigationLearner()
        self.analyzer = PageAnalyzer(self.driver)
        self.cookie_dismisser = CookieBannerDismisser(self.driver)
        self.sso = SSOHandler(self.driver)
        self.gotchas = GotchasDB()

        # Compose focused collaborators (pass self so they access live attrs)
        self._executor = ActionExecutor(self)
        self._auth = AuthHandler(self)
        self._navigator = FormNavigator(self, self._auth)
        self._filler = FormFiller(self, self._executor, self._navigator)
        self._bind_compat_aliases()

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
        cv_path: "Path",
        cover_letter_path: "Path | None" = None,
        profile: dict | None = None,
        custom_answers: dict | None = None,
        overrides: dict | None = None,
        dry_run: bool = False,
        form_intelligence: Any | None = None,
        jd_keywords: list[str] | None = None,
        company_research: "CompanyResearch | None" = None,
        pre_navigated_snapshot: dict | None = None,
    ) -> dict:
        """Full application flow: navigate → account → verify → fill → submit.

        If *pre_navigated_snapshot* is provided, Phase 1 navigation is skipped
        and the snapshot is used directly (avoids double-navigation which kills
        the MV3 service worker connection).
        """
        profile = profile or {}
        custom_answers = custom_answers or {}
        navigation_steps: list[dict] = []

        # Phase 1: Navigate to application form
        if pre_navigated_snapshot is not None:
            if hasattr(self.driver, '_snapshot'):
                self.driver._snapshot = self._to_page_snapshot(pre_navigated_snapshot)
        nav_result = await self._navigator.navigate_to_form(
            url, platform, navigation_steps,
            skip_initial_navigate=pre_navigated_snapshot is not None,
        )
        page_type = nav_result["page_type"]

        if page_type == PageType.VERIFICATION_WALL:
            return {"success": False, "error": "CAPTCHA wall", "screenshot": nav_result.get("screenshot")}

        if page_type == PageType.UNKNOWN:
            return {"success": False, "error": "Unknown page — could not reach application form", "screenshot": nav_result.get("screenshot")}

        if page_type != PageType.APPLICATION_FORM:
            return {"success": False, "error": f"Stuck on {page_type}", "screenshot": nav_result.get("screenshot")}

        # Phase 2: Multi-page form filling
        result = await self._filler.fill_application(
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

        # Phase 3: Pre-submit quality gate — review filled answers before submitting
        if result.get("success") and not dry_run and company_research is not None:
            gate_result = self._run_pre_submit_gate(
                custom_answers=custom_answers,
                jd_keywords=jd_keywords or [],
                company_research=company_research,
            )
            if not gate_result.passed:
                logger.warning(
                    "PreSubmitGate blocked submission (score=%.1f): %s",
                    gate_result.score,
                    gate_result.weaknesses,
                )
                return {
                    "success": False,
                    "needs_human_review": True,
                    "gate_score": gate_result.score,
                    "gate_weaknesses": gate_result.weaknesses,
                    "gate_suggestions": gate_result.suggestions,
                    "screenshot": result.get("screenshot"),
                    "pages_filled": result.get("pages_filled"),
                }
            result["gate_score"] = gate_result.score

        # Save successful navigation for future replay
        if result.get("success"):
            domain = extract_domain(url)
            self.learner.save_sequence(domain, navigation_steps, success=True)

        return result

    @staticmethod
    def _run_pre_submit_gate(
        custom_answers: dict,
        jd_keywords: list[str],
        company_research: "CompanyResearch",
    ):
        """Run PreSubmitGate on the filled answers.

        Fail-closed on import/setup errors (blocks submission).
        Pass-open only on transient runtime errors during review (with score=0).
        """
        try:
            from jobpulse.pre_submit_gate import PreSubmitGate, GateResult
        except ImportError as exc:
            logger.error("PreSubmitGate import failed — blocking submission: %s", exc)
            class _FakeGateResult:
                passed = False
                score = 0.0
                weaknesses = [f"PreSubmitGate unavailable: {exc}"]
                suggestions = ["Fix PreSubmitGate import before running pipeline"]
            return _FakeGateResult()

        try:
            filled = {
                k: str(v)
                for k, v in custom_answers.items()
                if not k.startswith("_") and isinstance(v, (str, int, float, bool))
            }
            gate = PreSubmitGate()
            return gate.review(
                filled_answers=filled,
                jd_keywords=jd_keywords,
                company_research=company_research,
            )
        except Exception as exc:
            logger.warning("PreSubmitGate runtime error — passing with score=0: %s", exc)
            return GateResult(passed=True, score=0.0, weaknesses=[f"Gate error: {exc}"])

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

    def _bind_compat_aliases(self):
        """Bind backward-compat aliases so patch.object works on instances."""
        self._navigate_to_form = self._navigator.navigate_to_form
        self._click_apply_button = self._navigator.click_apply_button
        self._handle_login = self._auth.handle_login
        self._handle_signup = self._auth.handle_signup
        self._handle_email_verification = self._auth.handle_email_verification
        self._fill_application = self._filler.fill_application
        self._two_phase_fill = self._filler._two_phase_fill
        self._execute_action = self._executor.execute_action
        self._execute_action_with_retry = self._executor.execute_action_with_retry
        self._verify_submission = self._navigator.verify_submission

    @staticmethod
    def _apply_gotcha_to_action(action: Any, solution: str) -> Any:
        from jobpulse.application_orchestrator_pkg._form_filler import _apply_gotcha_to_action
        return _apply_gotcha_to_action(action, solution)

    async def _execute_gotcha_pre_steps(self, solution: str, selector: str) -> None:
        from jobpulse.application_orchestrator_pkg._form_filler import _execute_gotcha_pre_steps
        await _execute_gotcha_pre_steps(self.driver, solution, selector)

    @staticmethod
    def _extract_domain(url: str) -> str:
        return extract_domain(url)

    @staticmethod
    def _find_apply_button(snapshot: dict) -> dict | None:
        from jobpulse.application_orchestrator_pkg._navigator import find_apply_button
        return find_apply_button(snapshot)

    @staticmethod
    def _find_signup_link(snapshot: dict) -> dict | None:
        from jobpulse.application_orchestrator_pkg._auth import find_signup_link
        return find_signup_link(snapshot)
