"""Application orchestrator — navigates redirect chains, handles account lifecycle,
and delegates form filling to the state machine.

Flow: URL → cookie dismiss → page stability wait → detect page type (DOM+Vision)
     → navigate (Apply clicks, SSO, login, signup, verify) → application form
     → state machine multi-page fill → submit → save learned sequence
"""
from __future__ import annotations

import asyncio
import contextlib
import random
import re
import time
from pathlib import Path
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
from jobpulse.state_machines import (
    ApplicationState,
    find_next_button,
    find_submit_button,
    get_state_machine,
    is_page_stuck,
)

logger = get_logger(__name__)

MAX_NAVIGATION_STEPS = 10
MAX_FORM_PAGES = 20

# Per-platform minimum page times (seconds) — from anti-detection research
# Workday tracks client-side timing and flags <2min total
_PLATFORM_MIN_PAGE_TIME: dict[str, float] = {
    "workday": 45.0,
    "linkedin": 8.0,
    "greenhouse": 5.0,
    "lever": 5.0,
    "indeed": 10.0,
    "generic": 5.0,
}

# Fields that MUST succeed or the application is incomplete
_CRITICAL_FIELD_PATTERNS = ("email", "name", "first", "last", "resume", "cv", "phone")


def _is_critical_field(selector: str, label: str = "") -> bool:
    """Check if a field is critical (missing it = worthless application).

    Checks the field label first, then falls back to a cleaned selector
    that strips CSS attribute syntax (e.g., ``[name='...']``) to avoid
    false positives where ``name`` in ``[name=...]`` triggers a match.
    """
    # Prefer the semantic label (set by form analyzer)
    if label:
        label_lower = label.lower()
        if any(p in label_lower for p in _CRITICAL_FIELD_PATTERNS):
            return True
    # Strip CSS attribute selectors like [name='...'] to avoid false positives
    import re as _re
    cleaned = _re.sub(r"\[.*?\]", "", selector).lower()
    return any(p in cleaned for p in _CRITICAL_FIELD_PATTERNS)


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
        # If caller already navigated (pre_navigated_snapshot), inject the snapshot
        # into the bridge cache so _navigate_to_form skips the initial URL load but
        # still handles JD→form transitions (click apply, login, SSO, etc.).
        if pre_navigated_snapshot is not None:
            if hasattr(self.driver, '_snapshot'):
                self.driver._snapshot = self._to_page_snapshot(pre_navigated_snapshot)
        nav_result = await self._navigate_to_form(
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
            domain = self._extract_domain(url)
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
        # Import outside try block — import failure = hard stop
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

    async def _navigate_to_form(
        self, url: str, platform: str, steps: list[dict],
        skip_initial_navigate: bool = False,
    ) -> dict:
        """Navigate through redirect chain to reach application form.

        If *skip_initial_navigate* is True, the caller has already loaded the
        page and injected the snapshot into the bridge cache — we skip the
        initial ``bridge.navigate(url)`` to avoid a redundant MV3 restart.
        """
        if not skip_initial_navigate:
            try:
                await self.driver.navigate(url)
            except (TimeoutError, ConnectionError):
                logger.info("Navigate lost (MV3 restart) — waiting for extension to reconnect")
                await asyncio.sleep(5)
        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        if not snapshot or not snapshot.get("url"):
            # Still no snapshot — wait longer
            await asyncio.sleep(5)
            snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        # Try learned sequence first
        domain = self._extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if learned:
            logger.info("Replaying learned navigation for %s (%d steps)", domain, len(learned))
            self.learner.increment_replay(domain)
            replay_ok = True
            for learned_step in learned:
                action = learned_step.get("action", "")
                step_page_type = learned_step.get("page_type", "")
                try:
                    if action == "click_apply" or action == "click_apply_guess":
                        snapshot = await self._click_apply_button(snapshot)
                    elif action == "fill_login":
                        snapshot = await self._handle_login(snapshot, platform)
                    elif action.startswith("sso_"):
                        provider = action[len("sso_"):]
                        sso = self.sso.detect_sso(snapshot)
                        if sso and sso.get("provider") == provider:
                            await self.sso.click_sso(sso)
                            snapshot = self._as_dict(await self.driver.get_snapshot())
                        else:
                            logger.warning("Replay: SSO provider %s not found, falling through", provider)
                            replay_ok = False
                            break
                    elif action == "fill_signup":
                        snapshot = await self._handle_signup(snapshot, platform)
                    elif action == "verify_email":
                        snapshot = await self._handle_email_verification(snapshot, platform, url)
                    else:
                        logger.warning("Replay: unknown action %r in step, falling through", action)
                        replay_ok = False
                        break
                    # Dismiss any new cookie banners after each replay step
                    await self.cookie_dismisser.dismiss(snapshot)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                except Exception as replay_exc:
                    logger.warning("Replay step failed (action=%s): %s — falling through to fresh detection", action, replay_exc)
                    replay_ok = False
                    break

            if replay_ok:
                # Check if we reached the application form after replay
                page_type_after = await self.analyzer.detect(snapshot)
                if page_type_after == PageType.APPLICATION_FORM:
                    logger.info("Replay succeeded: reached APPLICATION_FORM for %s", domain)
                    return {"page_type": page_type_after, "snapshot": snapshot}
                logger.info("Replay completed but page_type=%s — continuing with fresh detection", page_type_after)

        # Dismiss cookie banner
        await self.cookie_dismisser.dismiss(snapshot)
        snapshot = self._as_dict(await self.driver.get_snapshot())

        apply_attempts = 0
        for step in range(MAX_NAVIGATION_STEPS):
            page_type = await self.analyzer.detect(snapshot)
            logger.info("Navigation step %d: %s", step + 1, page_type)

            if page_type in (PageType.APPLICATION_FORM, PageType.VERIFICATION_WALL, PageType.CONFIRMATION):
                return {"page_type": page_type, "snapshot": snapshot}

            if page_type == PageType.JOB_DESCRIPTION:
                apply_attempts += 1
                if apply_attempts > 3:
                    logger.warning("Apply button clicked %d times without modal — aborting", apply_attempts - 1)
                    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}
                import asyncio
                current_url = snapshot.get("url", "") if isinstance(snapshot, dict) else ""

                # LinkedIn shortcut: navigate directly to /apply/ URL (avoids modal click issues)
                if "linkedin.com/jobs/view/" in current_url and "/apply" not in current_url:
                    apply_url = current_url.split("?")[0].rstrip("/") + "/apply/"
                    logger.info("LinkedIn shortcut: navigating directly to %s", apply_url)
                    await self.driver.navigate(apply_url)
                    await asyncio.sleep(5)
                    snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    steps.append({"page_type": "job_description", "action": "linkedin_direct_apply"})
                else:
                    # Use wait_for_apply: polls DOM for up to 10s for apply button to render
                    try:
                        result = await self.driver.wait_for_apply(timeout_ms=10000)
                        if isinstance(result, dict):
                            snapshot = self._as_dict(await self.driver.get_snapshot())
                            waited = result.get("waited_ms", 0)
                            diag = result.get("apply_diagnostics", [])
                            if diag and isinstance(diag, list):
                                logger.info(
                                    "wait_for_apply: %dms, %d elements with 'apply' text: %s",
                                    waited, len(diag),
                                    [d.get("text", "")[:40] for d in diag[:5]],
                                )
                            else:
                                logger.warning("wait_for_apply: %dms, NO elements with 'apply' text found", waited)
                    except (TimeoutError, ConnectionError, TypeError, AttributeError):
                        logger.warning("wait_for_apply unavailable — using cached snapshot")

                    snapshot = await self._click_apply_button(snapshot)
                    steps.append({"page_type": "job_description", "action": "click_apply"})

            elif page_type == PageType.LOGIN_FORM:
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
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
                    await self.driver.click(apply_btn["selector"])
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                    steps.append({"page_type": "unknown", "action": "click_apply_guess"})
                else:
                    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Dismiss any new cookie banners after navigation
            await self.cookie_dismisser.dismiss(snapshot)
            snapshot = self._as_dict(await self.driver.get_snapshot())

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def _click_apply_button(self, snapshot: dict) -> dict:
        import re
        import asyncio
        apply_pattern = re.compile(
            r"(easy\s*apply|apply\W*(now|for\s*this|on\s*company)?"
            r"|start\s*application"
            r"|i.?m\s*interested|submit\s*interest)",
            re.IGNORECASE,
        )
        # "Submit Application" is the FORM submit — never click it during navigation
        submit_pattern = re.compile(r"submit\s*(my\s*)?application", re.IGNORECASE)

        buttons = snapshot.get("buttons", [])
        button_texts = [b.get("text", "")[:60] for b in buttons]
        logger.info("Apply button search: %d buttons found — %s", len(buttons), button_texts[:10])

        # Find apply buttons — collect all matches, prefer ones with href
        # Reject long text (>50 chars) — those are info labels, not buttons
        # Never match "Submit Application" — that's the form submit, not apply start
        apply_matches = []
        for btn in buttons:
            text = btn.get("text", "")
            if btn.get("enabled") is False:
                continue
            if text.strip().lower() == "save":
                continue
            if len(text) > 50:
                continue
            if submit_pattern.search(text):
                continue
            if apply_pattern.search(text):
                apply_matches.append(btn)

        if not apply_matches:
            logger.warning("No apply button found in snapshot")
            return snapshot

        # Rank matches: "Easy Apply" / "Apply Now" are strongest signals,
        # weaker matches like "I'm interested" only used as last resort.
        strong_pattern = re.compile(r"easy\s*apply|apply\s*(now|for)", re.IGNORECASE)
        strong = [b for b in apply_matches if strong_pattern.search(b.get("text", ""))]
        ranked = strong if strong else apply_matches

        # Strategy: if the match is a link with href, navigate directly (most reliable)
        # This avoids target="_blank" new-tab issues entirely
        for btn in ranked:
            href = btn.get("href", "")
            if href and href.startswith("http"):
                logger.info("Apply link found: '%s' → navigating to %s", btn["text"][:40], href[:100])
                await self.driver.navigate(href)
                await asyncio.sleep(3)
                return self._as_dict(await self.driver.get_snapshot())

        # Fallback: click the button directly (Easy Apply modals, non-link buttons)
        btn = ranked[0]
        logger.info("Clicking apply button: '%s' via %s", btn["text"][:60], btn["selector"])
        try:
            await self.driver.click(btn["selector"])
        except (TimeoutError, Exception) as exc:
            logger.warning("Click timed out (%s) — trying force_click", exc)
            try:
                await self.driver.force_click(btn["selector"])
            except Exception:
                pass
        await asyncio.sleep(3)
        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    async def _handle_login(self, snapshot: dict, platform: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))

        if not self.accounts.has_account(domain):
            signup_btn = self._find_signup_link(snapshot)
            if signup_btn:
                await self.driver.click(signup_btn["selector"])
                return self._as_dict(await self.driver.get_snapshot())
            return snapshot

        email, password = self.accounts.get_credentials(domain)
        logger.info("Logging into %s", domain)

        filled_email = False
        filled_password = False
        for field in snapshot.get("fields", []):
            label = field.get("label", "").lower()
            ftype = field.get("type", "")
            try:
                if ftype == "email" or "email" in label:
                    await self.driver.fill(field["selector"], email)
                    filled_email = True
                elif ftype == "password" or "password" in label:
                    await self.driver.fill(field["selector"], password)
                    filled_password = True
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("Login fill failed for %s: %s", field.get("selector"), exc)

        if not filled_email or not filled_password:
            logger.warning("Login: could not fill email=%s password=%s for %s", filled_email, filled_password, domain)
            return snapshot

        import re as _re
        clicked = False
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and _re.search(r"(sign\s*in|log\s*in|login)", btn.get("text", ""), _re.IGNORECASE):
                await self.driver.click(btn["selector"])
                clicked = True
                break

        if not clicked:
            logger.warning("Login: no sign-in button found for %s", domain)
            return snapshot

        # Wait for page transition after login click
        await asyncio.sleep(2.0)
        post_login = self._as_dict(await self.driver.get_snapshot())

        # Verify login succeeded — if we're still on the login page, don't mark success
        post_url = post_login.get("url", "").lower()
        post_text = post_login.get("page_text_preview", "").lower()
        still_login = any(
            kw in post_text for kw in ("sign in", "log in", "invalid", "incorrect", "wrong password")
        ) and "login" in post_url
        if still_login:
            logger.warning("Login appears to have failed for %s — not marking success", domain)
            return post_login

        self.accounts.mark_login_success(domain)
        return post_login

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
                await self.driver.fill(sel, email)
            elif ftype == "password":
                await self.driver.fill(sel, password)
            elif "first" in label:
                await self.driver.fill(sel, PROFILE.get("first_name", ""))
            elif "last" in label:
                await self.driver.fill(sel, PROFILE.get("last_name", ""))
            elif "name" in label and "user" not in label:
                await self.driver.fill(sel, f"{PROFILE.get('first_name', '')} {PROFILE.get('last_name', '')}".strip())
            elif "phone" in label or ftype == "tel":
                await self.driver.fill(sel, PROFILE.get("phone", ""))

        import re
        for btn in snapshot.get("buttons", []):
            if btn.get("enabled") and re.search(r"(create|sign\s*up|register|join|submit)", btn.get("text", ""), re.IGNORECASE):
                await self.driver.click(btn["selector"])
                break

        return self._as_dict(await self.driver.get_snapshot())

    async def _handle_email_verification(self, snapshot: dict, platform: str, return_url: str) -> dict:
        domain = self._extract_domain(snapshot.get("url", ""))
        logger.info("Waiting for verification email from %s", domain)

        link = self.gmail.wait_for_verification(domain)
        if not link:
            logger.warning("Verification email not received for %s", domain)
            return snapshot

        await self.driver.navigate(link)
        self._as_dict(await self.driver.get_snapshot())
        self.accounts.mark_verified(domain)

        logger.info("Returning to application: %s", return_url[:80])
        await self.driver.navigate(return_url)
        return self._as_dict(await self.driver.get_snapshot())

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

        # Extract Telegram progress stream if provided (injected by applicator.py)
        tg_stream = custom_answers.pop("_stream", None) if custom_answers else None

        # MV3 recovery: check if we have saved progress from a service worker restart
        current_url = snapshot.get("url", "") if isinstance(snapshot, dict) else getattr(snapshot, "url", "")
        filled_selectors: set[str] = set()
        if current_url:
            try:
                saved_progress = await self.driver.get_form_progress(current_url)
                if saved_progress:
                    filled_selectors = {f["selector"] for f in saved_progress.get("filled_fields", [])}
                    logger.info("MV3 recovery: resuming with %d pre-filled fields", len(filled_selectors))
            except (TimeoutError, ConnectionError):
                pass  # filled_selectors already initialized as empty set

        # Load known gotchas for this domain (learned from Ralph Loop + manual fixes)
        domain = self._extract_domain(current_url) if current_url else platform
        domain_gotchas = {g["selector_pattern"]: g for g in self.gotchas.lookup_domain(domain, engine=self.engine)}
        if domain_gotchas:
            logger.info("Loaded %d gotchas for domain %s", len(domain_gotchas), domain)

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

            # ── Two-phase fill for screening questions ──
            if state == ApplicationState.SCREENING_QUESTIONS:
                actions = await self._two_phase_fill(
                    page_snapshot, machine, profile, custom_answers,
                    cv_path=str(cv_path) if cv_path else "",
                    cl_path=str(cover_letter_path) if cover_letter_path else None,
                    form_intelligence=form_intelligence,
                )
            else:
                actions = machine.get_actions(
                    state, page_snapshot, profile=profile, custom_answers=custom_answers,
                    cv_path=str(cv_path) if cv_path else "",
                    cl_path=str(cover_letter_path) if cover_letter_path else None,
                    form_intelligence=form_intelligence,
                )

            # If LLM returned no actions (page has fields but they're navigation/search),
            # try clicking the apply button — we may still be on the job listing page
            if not actions and state == ApplicationState.SCREENING_QUESTIONS:
                logger.info("  No fill actions — page may not be an application form, trying apply button")
                apply_snapshot = await self._click_apply_button(
                    snapshot if isinstance(snapshot, dict) else snapshot.model_dump()
                )
                if apply_snapshot != snapshot:
                    snapshot = apply_snapshot
                    prev_snapshot = None  # Reset stuck detection
                    continue

            page_start = time.monotonic()

            for i, action in enumerate(actions):
                atype = getattr(action, "type", None) or (action.get("type", "?") if isinstance(action, dict) else "?")
                sel = getattr(action, "selector", None) or (action.get("selector", "?") if isinstance(action, dict) else "?")
                # Skip fields already filled in a previous MV3 session
                if sel and sel in filled_selectors:
                    logger.debug("  Skipping pre-filled field %s (MV3 recovery)", str(sel)[:60])
                    continue
                # Apply known gotchas — modify action based on learned workaround
                gotcha = domain_gotchas.get(str(sel))
                if gotcha:
                    solution = gotcha["solution"]
                    logger.info("  Applying gotcha for %s: %s", str(sel)[:40], solution[:60])
                    self.gotchas.record_usage(domain, str(sel))
                    action = self._apply_gotcha_to_action(action, solution)
                    # Re-read type after gotcha modification
                    atype = getattr(action, "type", None) or (action.get("type", "?") if isinstance(action, dict) else "?")
                    sel = getattr(action, "selector", None) or (action.get("selector", "?") if isinstance(action, dict) else "?")

                logger.info("  Action %d/%d: %s → %s", i + 1, len(actions), atype, str(sel)[:60])
                try:
                    # Pre-action gotcha steps (scroll, wait)
                    if gotcha:
                        await self._execute_gotcha_pre_steps(gotcha["solution"], str(sel))
                    await self._execute_action_with_retry(action, tg_stream=tg_stream)
                    # Track filled field for MV3 persistence
                    if sel and atype in ("fill", "select", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date", "fill_combobox", "fill_contenteditable", "check"):
                        filled_selectors.add(sel)
                        try:
                            await self.driver.save_form_progress(current_url, {
                                "filled_fields": [{"selector": s} for s in filled_selectors],
                                "current_page": page_num,
                            })
                        except (TimeoutError, ConnectionError):
                            pass  # Non-critical — best effort
                except (TimeoutError, ConnectionError) as exc:
                    field_label = ""
                    if isinstance(action, dict):
                        field_label = action.get("label", "")
                    elif hasattr(action, "label"):
                        field_label = getattr(action, "label", "")

                    # Fallback: if fill_combobox/fill_date failed, retry as plain fill
                    if atype in ("fill_combobox", "fill_date", "fill_custom_select"):
                        fallback_val = action.get("value", "") if isinstance(action, dict) else getattr(action, "value", "")
                        logger.info("  Fallback: retrying %s → plain fill for %s", atype, str(sel)[:60])
                        try:
                            await self.driver.fill(str(sel), fallback_val)
                            filled_selectors.add(sel)
                            continue  # Fallback succeeded
                        except Exception:
                            logger.warning("  Fallback fill also failed for %s", str(sel)[:40])

                    if _is_critical_field(str(sel), label=str(field_label)):
                        logger.error("  Critical field %s failed — aborting page", sel)
                        return {"success": False, "error": f"Critical field failed: {sel}", "screenshot": last_screenshot}
                    logger.warning("  Action %d/%d failed: %s — %r", i + 1, len(actions), atype, exc)

            try:
                screenshot_bytes = await self.driver.screenshot()
            except (TimeoutError, ConnectionError):
                screenshot_bytes = None
                logger.warning("Screenshot failed after form page %d", page_num)
            if screenshot_bytes:
                last_screenshot = screenshot_bytes

            # Enforce minimum page timing (anti-detection)
            min_page_time = _PLATFORM_MIN_PAGE_TIME.get(platform, 5.0)
            elapsed = time.monotonic() - page_start
            if elapsed < min_page_time:
                remaining = min_page_time - elapsed
                jitter = random.gauss(remaining * 0.3, remaining * 0.1)
                await asyncio.sleep(max(0.5, remaining + jitter))

            # Auto-check consent boxes before any navigation
            try:
                await self.driver.check_consent_boxes()
            except (TimeoutError, ConnectionError):
                pass  # Non-critical — proceed without

            if state == ApplicationState.SUBMIT:
                if dry_run:
                    return {"success": True, "dry_run": True, "screenshot": last_screenshot, "pages_filled": page_num}
                # ── Pre-submit validation gate ──
                try:
                    validation = await self.driver.scan_validation_errors()
                    if isinstance(validation, dict) and validation.get("has_errors"):
                        errors = validation.get("errors", [])
                        logger.warning(
                            "Pre-submit validation errors (%d): %s",
                            len(errors),
                            [e.get("error_message", "")[:60] for e in errors[:5]],
                        )
                        return {
                            "status": "validation_errors",
                            "errorCategory": "validation",
                            "errors": errors,
                            "message": f"{len(errors)} validation error(s) before submit",
                            "isRetryable": True,
                            "agentName": "application_orchestrator",
                            "attemptedAction": "pre_submit_validation",
                        }
                except Exception as exc:
                    logger.warning("Validation scan failed (non-blocking): %s", exc)
                # Use CURRENT page_snapshot (not stale snapshot variable)
                current_buttons = page_snapshot.buttons if hasattr(page_snapshot, 'buttons') else snapshot.get("buttons", [])
                submit_btn = find_submit_button(
                    [b.model_dump() if hasattr(b, 'model_dump') else b for b in current_buttons]
                )
                if submit_btn:
                    await self.driver.click(submit_btn["selector"])
                    # Verify submission actually went through
                    verification = await self._verify_submission()
                    if verification.get("verified"):
                        logger.info("Submission verified: %s", verification)
                        # Clear MV3 progress — application complete
                        if current_url:
                            try:
                                await self.driver.clear_form_progress(current_url)
                            except (TimeoutError, ConnectionError):
                                pass
                        return {"success": True, "verified": True, "screenshot": last_screenshot, "pages_filled": page_num}
                    elif verification.get("reason") == "form_error":
                        logger.warning("Submit rejected: %s", verification)
                        # Don't return — let the loop continue to re-detect state
            else:
                # Pre-navigation check: scan for validation errors / unfilled required fields
                try:
                    validation = await self.driver.scan_validation_errors()
                    if isinstance(validation, dict) and validation.get("has_errors"):
                        errors = validation.get("errors", [])
                        logger.warning(
                            "Validation errors before Next (%d): %s",
                            len(errors),
                            [e.get("error_message", "")[:60] for e in errors[:5]],
                        )
                        # Re-scan fields and re-fill missing ones
                        retry_snapshot = self._to_page_snapshot(
                            self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                        )
                        empty_required = [
                            f for f in retry_snapshot.fields
                            if f.required and not f.current_value and f.input_type != "file"
                        ]
                        if empty_required:
                            logger.info("Re-filling %d empty required fields", len(empty_required))
                            retry_actions = machine.get_actions(
                                state, retry_snapshot, profile=profile,
                                custom_answers=custom_answers,
                                cv_path=str(cv_path) if cv_path else "",
                                cl_path=str(cover_letter_path) if cover_letter_path else None,
                                form_intelligence=form_intelligence,
                            )
                            for ra in retry_actions:
                                ra_sel = getattr(ra, "selector", "")
                                if ra_sel in filled_selectors:
                                    continue
                                try:
                                    await self._execute_action_with_retry(ra, tg_stream=tg_stream)
                                    filled_selectors.add(ra_sel)
                                except (TimeoutError, ConnectionError):
                                    pass
                except (TimeoutError, ConnectionError):
                    pass  # Non-critical — try clicking Next anyway

                # Use CURRENT page_snapshot for next button
                current_buttons = page_snapshot.buttons if hasattr(page_snapshot, 'buttons') else snapshot.get("buttons", [])
                next_btn = find_next_button(
                    [b.model_dump() if hasattr(b, 'model_dump') else b for b in current_buttons]
                )
                if next_btn:
                    await self.driver.click(next_btn["selector"])

            prev_snapshot = snapshot
            snapshot = self._as_dict(await self.driver.get_snapshot())

        return {"success": False, "error": f"Exhausted {MAX_FORM_PAGES} pages", "screenshot": last_screenshot}

    @staticmethod
    def _apply_gotcha_to_action(action: Any, solution: str) -> Any:
        """Modify an action based on a gotcha solution string.

        Solution formats:
            use_force_click              — change action type to force_click
            scroll_first                 — handled in pre-steps (no action change)
            wait_before:<ms>             — handled in pre-steps (no action change)
            use_selector:<new_selector>  — swap selector
        """
        if solution.startswith("use_selector:"):
            new_selector = solution[len("use_selector:"):]
            if hasattr(action, "model_copy"):
                return action.model_copy(update={"selector": new_selector})
            elif isinstance(action, dict):
                return {**action, "selector": new_selector}
        elif solution == "use_force_click":
            if hasattr(action, "model_copy"):
                return action.model_copy(update={"type": "force_click"})
            elif isinstance(action, dict):
                return {**action, "type": "force_click"}
        # scroll_first, wait_before — handled in _execute_gotcha_pre_steps
        return action

    async def _execute_gotcha_pre_steps(self, solution: str, selector: str) -> None:
        """Execute pre-action steps from a gotcha solution (scroll, wait)."""
        if "scroll_first" in solution:
            try:
                await self.driver.scroll_to(selector)
            except (TimeoutError, ConnectionError):
                logger.debug("Gotcha scroll_to failed for %s", selector[:40])
        if solution.startswith("wait_before:"):
            try:
                wait_ms = int(solution.split(":")[1])
                await asyncio.sleep(wait_ms / 1000.0)
            except (ValueError, IndexError):
                await asyncio.sleep(1.0)

    async def _two_phase_fill(
        self,
        page_snapshot,
        machine,
        profile: dict,
        custom_answers: dict,
        cv_path: str = "",
        cl_path: str | None = None,
        form_intelligence: object | None = None,
    ) -> list:
        """Two-phase form fill: deterministic + click-to-reveal + LLM.

        Phase 1: Pattern-match known fields (name, email, phone, etc.) — instant, free
        Phase 2: Click comboboxes to reveal real options, then LLM for remaining fields
        Phase 3: Append file uploads (state machine handles these)
        """
        from jobpulse.form_analyzer import (
            deterministic_fill,
            analyze_remaining_fields,
            _PLACEHOLDER_VALUES,
        )
        from jobpulse.ext_models import Action

        job_context = custom_answers.get("_job_context")
        context_dict = job_context if isinstance(job_context, dict) else None

        # Strip placeholder values
        for f in page_snapshot.fields:
            if f.current_value and f.current_value.strip().lower() in _PLACEHOLDER_VALUES:
                f.current_value = ""

        # ── Phase 1: Deterministic fill ──
        det_actions = deterministic_fill(
            page_snapshot, job_context=context_dict, platform=machine.platform,
        )

        # Sort deterministic actions in ascending DOM order (top-to-bottom)
        field_order_map = {f.selector: idx for idx, f in enumerate(page_snapshot.fields)}
        det_actions.sort(key=lambda a: field_order_map.get(a.selector, 9999))
        det_selectors = {a.selector for a in det_actions}

        # Execute deterministic actions immediately (in DOM order)
        for i, action in enumerate(det_actions):
            sel = action.selector
            logger.info("  Phase1 %d/%d: %s → %s", i + 1, len(det_actions), action.type, sel[:40])
            try:
                await self._execute_action(action)
            except Exception as exc:
                logger.warning("  Phase1 action failed: %s — %s", sel[:40], exc)
            await asyncio.sleep(0.15)

        # ── Phase 2: Click-to-reveal for remaining comboboxes ──
        remaining = [
            f for f in page_snapshot.fields
            if f.selector not in det_selectors
            and f.input_type != "file"
            and (not f.current_value or f.current_value.strip().lower() in _PLACEHOLDER_VALUES)
        ]

        # Click each combobox to reveal real options
        combobox_fields = [
            f for f in remaining
            if f.input_type in ("search_autocomplete", "combobox", "custom_select")
            or f.attributes.get("role") == "combobox"
        ]
        for f in combobox_fields:
            try:
                options = await self.driver.reveal_options(f.selector, timeout_ms=8000)
                if options:
                    f.options = options
                    logger.info("  Revealed %d options for %s: %s",
                                len(options), f.selector[:40], options[:5])
            except Exception as exc:
                logger.debug("  reveal_options failed for %s: %s", f.selector[:40], exc)

        # ── Phase 2b: LLM for remaining fields (now with real options) ──
        llm_actions = []
        if remaining:
            llm_actions = analyze_remaining_fields(
                page_snapshot, remaining,
                job_context=context_dict, platform=machine.platform,
            )

        # ── Phase 3: Append file uploads (deduplicated — one CV, one CL max) ──
        all_fill_selectors = det_selectors | {a.selector for a in llm_actions}
        upload_actions = []
        cv_uploaded = False
        cl_uploaded = False
        for field in page_snapshot.fields:
            if field.input_type == "file" and field.selector not in all_fill_selectors:
                label = field.label.lower()
                if "autofill" in label or "drag and drop" in label or "easyresume" in field.selector:
                    continue
                if "cover" in label and cl_path and not cl_uploaded:
                    upload_actions.append(Action(type="upload", selector=field.selector, file_path=cl_path))
                    cl_uploaded = True
                elif cv_path and not cv_uploaded and "cover" not in label:
                    upload_actions.append(Action(type="upload", selector=field.selector, file_path=cv_path))
                    cv_uploaded = True

        # Combine: deterministic already executed, return LLM + uploads for orchestrator to execute
        combined = llm_actions + upload_actions

        # Sort in DOM order
        field_order = {f.selector: idx for idx, f in enumerate(page_snapshot.fields)}
        combined.sort(key=lambda a: field_order.get(a.selector, 9999))

        logger.info("Two-phase fill: %d det (done) + %d llm + %d uploads = %d remaining",
                     len(det_actions), len(llm_actions), len(upload_actions), len(combined))
        return combined

    async def _execute_action(self, action: Any, tg_stream: Any = None):
        if hasattr(action, "model_dump"):
            # Pydantic Action model
            atype = getattr(action, "type", "")
            selector = getattr(action, "selector", "")
            value = getattr(action, "value", "")
            file_path = getattr(action, "file_path", None)
            label = getattr(action, "label", selector)
            tier = getattr(action, "tier", 1)
            confidence = getattr(action, "confidence", 1.0)
        else:
            atype = action.get("type", "")
            selector = action.get("selector", "")
            value = action.get("value", "")
            file_path = action.get("file_path")
            label = action.get("label", selector)
            tier = action.get("tier", 1)
            confidence = action.get("confidence", 1.0)

        if atype == "fill":
            await self.driver.fill(selector, value)
        elif atype == "upload":
            await self.driver.upload(selector, Path(file_path) if file_path else file_path)
        elif atype == "click":
            await self.driver.click(selector)
        elif atype == "select":
            await self.driver.select_option(selector, value)
        elif atype == "check":
            await self.driver.check(selector, value.lower() in ("true", "yes", "1", "checked") if value else True)
        # v2 action types
        elif atype == "fill_radio_group":
            await self.driver.fill_radio_group(selector, value)
        elif atype == "fill_custom_select":
            await self.driver.fill_custom_select(selector, value)
        elif atype == "fill_autocomplete":
            await self.driver.fill_autocomplete(selector, value)
        elif atype == "fill_tag_input":
            values = [v.strip() for v in value.split(",") if v.strip()] if value else []
            await self.driver.fill_tag_input(selector, values)
        elif atype == "fill_date":
            await self.driver.fill_date(selector, value)
        elif atype == "fill_combobox":
            await self.driver.fill_combobox(selector, value)
        elif atype == "fill_contenteditable":
            await self.driver.fill_contenteditable(selector, value)
        elif atype == "scroll_to":
            await self.driver.scroll_to(selector)
        elif atype == "force_click":
            await self.driver.force_click(selector)
        elif atype == "check_consent_boxes":
            await self.driver.check_consent_boxes(selector or None)

        # Stream field progress to Telegram in real-time
        if tg_stream is not None and atype in ("fill", "select", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date"):
            try:
                await tg_stream.stream_field(
                    label=str(label),
                    value=str(value),
                    tier=int(tier),
                    confident=float(confidence) >= 0.7,
                )
            except Exception as _se:
                logger.debug("stream_field failed: %s", _se)

    async def _execute_action_with_retry(
        self, action: Any, tg_stream: Any = None, max_retries: int = 2
    ):
        """Execute action with retry for critical fields and post-fill validation."""
        selector = getattr(action, "selector", "") or (
            action.get("selector", "") if isinstance(action, dict) else ""
        )
        atype = getattr(action, "type", "") or (
            action.get("type", "") if isinstance(action, dict) else ""
        )

        for attempt in range(max_retries + 1):
            try:
                await self._execute_action(action, tg_stream=tg_stream)

                # Post-fill validation for fill actions
                if atype in ("fill", "fill_radio_group", "fill_custom_select", "fill_autocomplete", "fill_date", "fill_combobox") and selector:
                    try:
                        rescan = await self.driver.rescan_after_fill(selector)
                        errors = rescan.get("validation_errors", [])
                        if errors:
                            logger.warning("Validation error after %s: %s", atype, errors)
                            if attempt < max_retries:
                                await asyncio.sleep(1.5 * (attempt + 1))
                                continue
                    except (TimeoutError, ConnectionError):
                        pass  # Rescan failed — don't block the fill
                return  # Success
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("Action %s attempt %d/%d failed: %r", atype, attempt + 1, max_retries + 1, exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    raise  # Let caller handle

    async def _verify_submission(self) -> dict:
        """Wait for and verify the confirmation page after submit click."""
        await asyncio.sleep(3.0)
        snapshot = await self.driver.get_snapshot(force_refresh=True)
        if not snapshot:
            return {"verified": False, "reason": "no_snapshot"}

        text = (snapshot.page_text_preview or "").lower()

        # Success indicators
        success_patterns = [
            r"application.*(?:submitted|received|complete|sent)",
            r"thank\s*you\s*for\s*(?:applying|your\s*application)",
            r"we.ll\s*(?:be\s*in\s*touch|review|get\s*back)",
            r"application\s*(?:reference|confirmation|id)\s*[\w-]+",
            r"successfully\s*(?:applied|submitted)",
            r"you\s*(?:have\s*)?applied",
        ]
        for pat in success_patterns:
            if re.search(pat, text):
                return {"verified": True, "pattern": pat}

        # URL-based confirmation
        url = (snapshot.url or "").lower()
        for path in ("/confirmation", "/thank-you", "/success", "/applied", "/complete"):
            if path in url:
                return {"verified": True, "url_match": path}

        # Error indicators (form rejected submission)
        error_patterns = [
            r"please\s*(?:fix|correct|review)\s*(?:the\s*)?(?:errors|fields)",
            r"required\s*field",
            r"there\s*(?:was|were)\s*(?:an?\s*)?error",
            r"submission\s*failed",
        ]
        for pat in error_patterns:
            if re.search(pat, text):
                return {"verified": False, "reason": "form_error", "pattern": pat}

        return {"verified": False, "reason": "unknown_state"}

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
