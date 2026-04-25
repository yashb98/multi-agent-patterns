"""Navigation — traverse redirect chains to reach the application form.

Handles: learned sequence replay, cookie dismissal, apply button detection,
LinkedIn direct-apply shortcut, and page-type-based routing.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from shared.logging_config import get_logger

from jobpulse.form_models import PageType

logger = get_logger(__name__)

MAX_NAVIGATION_STEPS = 10


class FormNavigator:
    """Navigates through redirect chains to reach the application form."""

    def __init__(self, orch, auth_handler):
        self._orch = orch
        self.auth = auth_handler

    @property
    def driver(self):
        return self._orch.driver

    @property
    def analyzer(self):
        return self._orch.analyzer

    @property
    def cookie_dismisser(self):
        return self._orch.cookie_dismisser

    @property
    def sso(self):
        return self._orch.sso

    @property
    def learner(self):
        return self._orch.learner

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    async def navigate_to_form(
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
        domain = extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if not learned and platform:
            learned = self.learner.get_platform_pattern(platform, exclude_domain=domain)
            if learned:
                logger.info("Using PLATFORM pattern for %s (%s, no domain-specific data)", domain, platform)
        if learned:
            logger.info("Replaying learned navigation for %s (%d steps)", domain, len(learned))
            self.learner.increment_replay(domain)
            replay_ok = True
            for learned_step in learned:
                action = learned_step.get("action", "")
                step_page_type = learned_step.get("page_type", "")
                try:
                    if action in {"click_apply", "click_apply_guess", "linkedin_direct_apply"}:
                        snapshot = await self.click_apply_button(snapshot)
                    elif action == "fill_login":
                        snapshot = await self.auth.handle_login(snapshot, platform)
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
                        snapshot = await self.auth.handle_signup(snapshot, platform)
                    elif action == "verify_email":
                        snapshot = await self.auth.handle_email_verification(snapshot, platform, url)
                    else:
                        logger.warning("Replay: unknown action %r in step, falling through", action)
                        replay_ok = False
                        break
                    # Dismiss any new cookie banners after each replay step
                    await self.cookie_dismisser.dismiss(snapshot)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                except Exception as replay_exc:
                    logger.warning("Replay step failed (action=%s): %s — falling through to fresh detection", action, replay_exc)
                    self.learner.mark_failed(domain)
                    replay_ok = False
                    break

            if replay_ok:
                # Check if we reached the application form after replay
                page_type_after = await self.analyzer.detect(snapshot)
                if page_type_after == PageType.APPLICATION_FORM:
                    logger.info("Replay succeeded: reached APPLICATION_FORM for %s", domain)
                    return {"page_type": page_type_after, "snapshot": snapshot}
                logger.info("Replay completed but page_type=%s — continuing with fresh detection", page_type_after)
                self.learner.mark_failed(domain)

        # Dismiss cookie banner
        await self.cookie_dismisser.dismiss(snapshot)
        snapshot = self._as_dict(await self.driver.get_snapshot())

        apply_attempts = 0
        visited_states: dict[tuple[str, str], int] = {}
        for step in range(MAX_NAVIGATION_STEPS):
            page_type = await self.analyzer.detect(snapshot)
            logger.info("Navigation step %d: %s", step + 1, page_type)

            current_url = snapshot.get("url", "") if isinstance(snapshot, dict) else ""
            _loop_key = (_extract_loop_domain(current_url), str(page_type))
            visited_states[_loop_key] = visited_states.get(_loop_key, 0) + 1
            if visited_states[_loop_key] >= 3:
                logger.warning("Redirect loop: %s × %d — aborting", _loop_key, visited_states[_loop_key])
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            if page_type in (PageType.APPLICATION_FORM, PageType.VERIFICATION_WALL, PageType.CONFIRMATION):
                return {"page_type": page_type, "snapshot": snapshot}

            if page_type == PageType.JOB_DESCRIPTION:
                apply_attempts += 1
                if apply_attempts > 3:
                    logger.warning("Apply button clicked %d times without modal — aborting", apply_attempts - 1)
                    return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}
                current_url = snapshot.get("url", "") if isinstance(snapshot, dict) else ""

                # Click the real visible apply control on the live page.
                # Some LinkedIn job pages render an external "Apply" button where
                # the old `/apply/` URL shortcut lands on a 404 page.
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

                snapshot = await self.click_apply_button(snapshot)
                steps.append({"page_type": "job_description", "action": "click_apply"})

            elif page_type == PageType.LOGIN_FORM:
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                    steps.append({"page_type": "login_form", "action": f"sso_{sso['provider']}"})
                else:
                    snapshot = await self.auth.handle_login(snapshot, platform)
                    steps.append({"page_type": "login_form", "action": "fill_login"})

            elif page_type == PageType.SIGNUP_FORM:
                snapshot = await self.auth.handle_signup(snapshot, platform)
                steps.append({"page_type": "signup_form", "action": "fill_signup"})

            elif page_type == PageType.EMAIL_VERIFICATION:
                snapshot = await self.auth.handle_email_verification(snapshot, platform, url)
                steps.append({"page_type": "email_verification", "action": "verify_email"})

            elif page_type == PageType.UNKNOWN:
                apply_btn = find_apply_button(snapshot)
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

    async def click_apply_button(self, snapshot: dict) -> dict:
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

        current_page = getattr(self.driver, "page", None)
        before_pages = []
        if current_page is not None:
            with_pages = getattr(current_page, "context", None)
            before_pages = list(with_pages.pages) if with_pages is not None else []

        # Strategy: if the match is a normal link with href, navigate directly.
        # But LinkedIn outbound apply links (`/safety/go`) must be clicked on-page
        # so LinkedIn can open the external ATS tab correctly.
        for btn in ranked:
            href = btn.get("href", "")
            if href and href.startswith("http") and "linkedin.com/safety/go" not in href:
                logger.info("Apply link found: '%s' → navigating to %s", btn["text"][:40], href[:100])
                await self.driver.navigate(href)
                await asyncio.sleep(3)
                return self._as_dict(await self.driver.get_snapshot())

        # Fallback: click the button directly (Easy Apply modals, non-link buttons)
        btn = ranked[0]
        logger.info("Clicking apply button: '%s' via %s", btn["text"][:60], btn["selector"])
        button_text = (btn.get("text") or "").strip()
        try:
            clicked = False
            if current_page is not None and button_text:
                for role in ("link", "button"):
                    locator = current_page.get_by_role(role, name=button_text).first
                    try:
                        if await locator.count():
                            await locator.click()
                            clicked = True
                            break
                    except Exception:
                        continue
            if not clicked:
                await self.driver.click(btn["selector"])
        except (TimeoutError, Exception) as exc:
            logger.warning("Click timed out (%s) — trying force_click", exc)
            try:
                if current_page is not None and button_text:
                    forced = False
                    for role in ("link", "button"):
                        locator = current_page.get_by_role(role, name=button_text).first
                        try:
                            if await locator.count():
                                await locator.click(force=True)
                                forced = True
                                break
                        except Exception:
                            continue
                    if not forced:
                        await self.driver.force_click(btn["selector"])
                else:
                    await self.driver.force_click(btn["selector"])
            except Exception as e:
                logger.debug("Force click also failed: %s", e)

        # Wait for modal or new form fields (max 8s, 0.5s intervals)
        modal_found = False
        for _ in range(16):
            try:
                dialog = self.driver.page.locator('[role="dialog"], [aria-modal="true"]')
                if await dialog.count():
                    modal_found = True
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if not modal_found:
            await asyncio.sleep(1)  # brief fallback wait

        # Follow external applications that open in a new tab/window.
        if current_page is not None:
            context = getattr(current_page, "context", None)
            if context is not None:
                new_pages = [page for page in context.pages if page not in before_pages]
                if new_pages:
                    newest = new_pages[-1]
                    try:
                        await newest.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    logger.info("Apply click opened a new page: %s", newest.url)
                    self.driver._page = newest

        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    async def verify_submission(self) -> dict:
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


# ── Module-level utilities ──

def extract_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url


def _extract_loop_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url


def find_apply_button(snapshot: dict) -> dict | None:
    pattern = re.compile(r"(apply|start\s*application|begin|submit\s*interest)", re.IGNORECASE)
    for btn in snapshot.get("buttons", []):
        if btn.get("enabled") and pattern.search(btn.get("text", "")):
            return btn
    return None
