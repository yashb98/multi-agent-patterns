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
from jobpulse.form_models import ButtonInfo, FieldInfo, PageSnapshot, PageType
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
        self.analyzer = PageAnalyzer(self.driver, form_experience=self._get_form_experience())
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
    def _get_form_experience():
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            return FormExperienceDB()
        except Exception:
            return None

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
        job: dict | None = None,
    ) -> dict:
        """Full application flow: navigate → account → verify → fill → submit.

        If *pre_navigated_snapshot* is provided, Phase 1 navigation is skipped
        and the snapshot is used directly (avoids double-navigation which kills
        the MV3 service worker connection).
        """
        import time as _time

        profile = profile or {}
        custom_answers = custom_answers or {}
        navigation_steps: list[dict] = []

        # Start trajectory logging
        _tid = ""
        _step_idx = 0
        _t0 = _time.monotonic()
        try:
            from shared.optimization import get_optimization_engine
            from shared.optimization._trajectory import TrajectoryStep
            _opt_engine = get_optimization_engine()
            _domain = extract_domain(url)
            _tid = _opt_engine.start_trajectory(
                pipeline="job_application", domain=_domain,
                agent_name="orchestrator", session_id=f"apply_{_domain}_{platform}",
            )
        except Exception:
            _opt_engine = None

        # Phase 1: Navigate to application form
        if pre_navigated_snapshot is not None:
            if hasattr(self.driver, '_snapshot'):
                self.driver._snapshot = self._to_page_snapshot(pre_navigated_snapshot)
        _nav_t0 = _time.monotonic()
        _job_for_bypass = job
        if not _job_for_bypass and company_research:
            _job_for_bypass = {"company": company_research.company, "title": "", "url": url, "platform": platform}
        if _job_for_bypass and _job_for_bypass.get("direct_url"):
            try:
                from jobpulse.platform_bypass import get_platform_bypass
                pb = get_platform_bypass()
                pb._store_cached(
                    _job_for_bypass["company"],
                    _job_for_bypass["direct_url"],
                    ats_platform="", strategy="scan_time",
                )
                logger.info("Pre-seeded bypass cache: %s → %s",
                            _job_for_bypass["company"], _job_for_bypass["direct_url"][:60])
            except Exception as exc:
                logger.debug("Could not pre-seed bypass cache: %s", exc)
        nav_result = await self._navigator.navigate_to_form(
            url, platform, navigation_steps,
            skip_initial_navigate=pre_navigated_snapshot is not None,
            job=_job_for_bypass,
        )
        page_type = nav_result["page_type"]

        # DOM-pattern platform discovery — catches white-label clones at unknown
        # URLs (e.g. Greenhouse hosted at careers.acme.com). Augments the URL-only
        # path that ran in prepare_application_inputs.
        if platform in (None, "", "generic"):
            try:
                from jobpulse.ats_adapters.discovery import detect_platform
                detected = detect_platform(url, snapshot=nav_result.get("snapshot"))
                if detected and detected != "generic":
                    logger.info(
                        "DOM platform discovery: %s detected on %s",
                        detected, url[:60],
                    )
                    platform = detected
            except Exception as exc:
                logger.debug("DOM platform discovery failed: %s", exc)

        try:
            if _tid and _opt_engine:
                _opt_engine.log_step(_tid, TrajectoryStep(
                    step_index=_step_idx, action="navigate_to_form",
                    target=url, input_value=platform,
                    output_value=str(page_type),
                    outcome="success" if page_type == PageType.APPLICATION_FORM else "failure",
                    duration_ms=(_time.monotonic() - _nav_t0) * 1000, metadata={},
                ))
                _step_idx += 1
        except Exception:
            pass

        if page_type == PageType.VERIFICATION_WALL:
            self._complete_trajectory(_tid, _opt_engine, "failure_captcha", 0.0, _t0)
            return {"success": False, "error": "CAPTCHA wall", "screenshot": nav_result.get("screenshot")}

        if page_type == PageType.UNKNOWN:
            outcome = "failure_expired" if nav_result.get("expired") else "failure_unknown_page"
            self._complete_trajectory(_tid, _opt_engine, outcome, 0.0, _t0)
            error_msg = nav_result.get("error", "Unknown page — could not reach application form")
            return {
                "success": False,
                "error": error_msg,
                "expired": nav_result.get("expired", False),
                "screenshot": nav_result.get("screenshot"),
            }

        if page_type != PageType.APPLICATION_FORM:
            self._complete_trajectory(_tid, _opt_engine, f"failure_stuck_{page_type}", 0.0, _t0)
            return {"success": False, "error": f"Stuck on {page_type}", "screenshot": nav_result.get("screenshot")}

        # Phase 2: Multi-page form filling
        _fill_t0 = _time.monotonic()
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

        # Re-auth retry on session expiry during form fill
        if result.get("error") == "session_expired" and not result.get("_reauth_attempted"):
            logger.info("Session expired during form fill — re-authenticating")
            reauth = await self._navigator.navigate_to_form(url, platform, navigation_steps, job=_job_for_bypass)
            if reauth["page_type"] == PageType.APPLICATION_FORM:
                result = await self._filler.fill_application(
                    platform=platform, snapshot=reauth["snapshot"],
                    cv_path=cv_path, cover_letter_path=cover_letter_path,
                    profile=profile, custom_answers=custom_answers,
                    overrides=overrides, dry_run=dry_run,
                    form_intelligence=form_intelligence,
                )
                result["_reauth_attempted"] = True

        try:
            if _tid and _opt_engine:
                _opt_engine.log_step(_tid, TrajectoryStep(
                    step_index=_step_idx, action="form_fill",
                    target=url, input_value=f"pages={result.get('pages_filled', 0)}",
                    output_value="success" if result.get("success") else result.get("error", "unknown"),
                    outcome="success" if result.get("success") else "failure",
                    duration_ms=(_time.monotonic() - _fill_t0) * 1000, metadata={},
                ))
                _step_idx += 1
        except Exception:
            pass

        # Phase 3: Pre-submit quality gate — review filled answers before submitting
        # Run on every successful non-dry-run application. If company_research is missing,
        # synthesize a minimal stub so the gate still evaluates JD keyword coverage and
        # answer quality (the company-context check just degrades gracefully).
        if result.get("success") and not dry_run:
            _gate_t0 = _time.monotonic()
            _company_research = company_research
            if _company_research is None:
                from jobpulse.perplexity import CompanyResearch as _CR
                _company_name = (job or {}).get("company", "") if job else ""
                _company_research = _CR(
                    company=_company_name,
                    description="",
                    tech_stack=[],
                    size="unknown",
                    industry="unknown",
                )
            gate_result = self._run_pre_submit_gate(
                custom_answers=custom_answers,
                jd_keywords=jd_keywords or [],
                company_research=_company_research,
            )

            # Semantic-correctness check (LLM-as-judge + deterministic checks).
            # Different from gate_result above which is the recruiter-quality
            # score. This catches values that passed read-back but are
            # semantically wrong (e.g. visa/sponsor contradiction, name/email
            # mismatching the actual profile, placeholder values).
            sem_result = self._run_semantic_correctness_check(
                custom_answers=custom_answers,
                jd_keywords=jd_keywords or [],
                profile=profile,
            )

            try:
                if _tid and _opt_engine:
                    _opt_engine.log_step(_tid, TrajectoryStep(
                        step_index=_step_idx, action="pre_submit_gate",
                        target=url,
                        input_value=f"recruiter={gate_result.score:.1f} semantic={sem_result.score:.1f}",
                        output_value=(
                            "passed" if gate_result.passed and sem_result.passed
                            else "blocked"
                        ),
                        outcome=(
                            "success" if gate_result.passed and sem_result.passed
                            else "failure"
                        ),
                        duration_ms=(_time.monotonic() - _gate_t0) * 1000, metadata={},
                    ))
                    _step_idx += 1
            except Exception:
                pass

            # Block if EITHER gate fails — both must pass to submit
            if not gate_result.passed or not sem_result.passed:
                combined_weaknesses = list(gate_result.weaknesses) + list(sem_result.weaknesses)
                combined_suggestions = list(gate_result.suggestions)
                logger.warning(
                    "PreSubmitGate blocked submission "
                    "(recruiter=%.1f, semantic=%.1f): %s",
                    gate_result.score, sem_result.score, combined_weaknesses,
                )
                self._complete_trajectory(
                    _tid, _opt_engine, "failure_gate_blocked",
                    min(gate_result.score, sem_result.score), _t0,
                )
                return {
                    "success": False,
                    "needs_human_review": True,
                    "gate_score": gate_result.score,
                    "semantic_score": sem_result.score,
                    "gate_weaknesses": combined_weaknesses,
                    "gate_suggestions": combined_suggestions,
                    "screenshot": result.get("screenshot"),
                    "pages_filled": result.get("pages_filled"),
                }
            result["gate_score"] = gate_result.score
            result["semantic_score"] = sem_result.score

        # Save successful navigation for future replay
        if result.get("success"):
            domain = extract_domain(url)
            self.learner.save_sequence(domain, navigation_steps, success=True, platform=platform)

        # Complete trajectory
        _outcome = "success" if result.get("success") else "failure"
        _score = result.get("gate_score", 8.0 if result.get("success") else 0.0)
        self._complete_trajectory(_tid, _opt_engine, _outcome, _score, _t0)

        return result

    @staticmethod
    def _complete_trajectory(tid: str, engine, outcome: str, score: float, t0: float):
        """Safely complete a trajectory, suppressing all errors."""
        try:
            import time as _time
            if tid and engine:
                engine.complete_trajectory(
                    tid, final_outcome=outcome, final_score=score,
                    total_duration_ms=(_time.monotonic() - t0) * 1000,
                )
        except Exception:
            pass

    @staticmethod
    def _run_pre_submit_gate(
        custom_answers: dict,
        jd_keywords: list[str],
        company_research: "CompanyResearch",
    ):
        """Run PreSubmitGate on the filled answers.

        Fail-closed on every error path: import failures, constructor errors,
        filled-dict comprehension errors, and any exception escaping
        gate.review() all return passed=False so the application is held for
        human review rather than silently submitted with no quality check.
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
            logger.warning("PreSubmitGate setup error — blocking for human review: %s", exc)
            return GateResult(passed=False, score=0.0, weaknesses=[f"Gate setup error: {exc}"])

    @staticmethod
    def _run_semantic_correctness_check(
        custom_answers: dict,
        jd_keywords: list[str],
        profile: dict | None,
    ):
        """Run PreSubmitGate.check_semantic_correctness.

        Verifies values that PASSED read-back are also semantically correct:
        cross-field consistency (visa/sponsor), profile alignment (name/email),
        placeholder detection, plus optional LLM-as-judge for JD relevance.

        Fail-open on import/runtime errors (passing-with-warning) — semantic
        check is additive to the existing recruiter gate; blocking on its
        own failures would be too aggressive while it's unproven in production.
        """
        try:
            from jobpulse.pre_submit_gate import PreSubmitGate, GateResult
        except ImportError as exc:
            logger.warning("Semantic correctness check unavailable: %s", exc)
            class _PassResult:
                passed = True
                score = 10.0
                weaknesses: list[str] = []
                suggestions: list[str] = []
            return _PassResult()

        try:
            filled = {
                k: str(v)
                for k, v in custom_answers.items()
                if not k.startswith("_") and isinstance(v, (str, int, float, bool))
            }
            gate = PreSubmitGate()
            return gate.check_semantic_correctness(
                filled_answers=filled,
                jd_keywords=jd_keywords,
                profile=profile or {},
                run_llm_judge=True,
            )
        except Exception as exc:
            logger.warning("Semantic correctness check error — passing with score=10: %s", exc)
            return GateResult(passed=True, score=10.0, weaknesses=[f"Semantic check error: {exc}"])

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
        self._execute_action = self._executor.execute_action
        self._execute_action_with_retry = self._executor.execute_action_with_retry
        self._verify_submission = self._navigator.verify_submission

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
