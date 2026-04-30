"""Navigation — traverse redirect chains to reach the application form.

Handles: learned sequence replay, cookie dismissal, apply button detection,
LinkedIn direct-apply shortcut, and page-type-based routing.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from dataclasses import dataclass

from shared.logging_config import get_logger

from jobpulse.form_models import PageType
from jobpulse.cookie_dismisser import dismiss_cookie_banner_playwright
from jobpulse.navigation.overlay_dismisser import OverlayDismisser
from jobpulse.navigation.wait_conditions import wait_for_modal_open, wait_for_page_stable

logger = get_logger(__name__)

MAX_NAVIGATION_STEPS = 10


@dataclass
class ApplyButtonPatterns:
    """Single source of truth for apply-button text patterns."""

    primary: tuple[str, ...] = (
        "easy apply", "apply now", "apply for this job", "start application",
        "apply on company website", "apply for this",
    )
    secondary: tuple[str, ...] = (
        "i'm interested", "submit interest", "begin application", "apply",
    )
    exclude: tuple[str, ...] = (
        "submit application", "submit my application", "save",
    )


def score_apply_button(text: str) -> float:
    """Score a button text for how likely it is an apply button.

    Returns 0.0-1.0. Higher = stronger apply signal.
    """
    lower = text.lower().strip()
    patterns = ApplyButtonPatterns()

    for pat in patterns.exclude:
        if pat in lower:
            return 0.0

    for pat in patterns.primary:
        if pat in lower:
            return 1.0

    for pat in patterns.secondary:
        if pat in lower:
            return 0.7

    if "apply" in lower:
        return 0.4

    return 0.0


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

    @staticmethod
    async def _dismiss_linkedin_discard(page) -> bool:
        """Dismiss LinkedIn 'Save this application?' overlay — delegates to OverlayDismisser."""
        dismisser = OverlayDismisser(page)
        return await dismisser.dismiss_linkedin_discard()

    async def navigate_to_form(
        self, url: str, platform: str, steps: list[dict],
        skip_initial_navigate: bool = False,
        job: dict | None = None,
    ) -> dict:
        """Navigate through redirect chain to reach application form.

        If *skip_initial_navigate* is True, the caller has already loaded the
        page and injected the snapshot into the bridge cache — we skip the
        initial ``bridge.navigate(url)`` to avoid a redundant MV3 restart.
        """
        # If LinkedIn Easy Apply modal is already open, skip ALL navigation to avoid
        # triggering LinkedIn's "Save this application?" dialog.
        # Only check on LinkedIn pages — generic dialog selectors cause false positives.
        current_page = getattr(self.driver, "page", None)
        if current_page is not None:
            try:
                page_url = current_page.url or ""
                if "linkedin.com" in page_url:
                    modal = current_page.locator('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')
                    if await modal.count():
                        logger.info("Easy Apply modal already open — skipping initial navigation")
                        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                        return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            except Exception:
                pass

        if not skip_initial_navigate:
            try:
                await self.driver.navigate(url)
            except (TimeoutError, ConnectionError):
                logger.info("Navigate lost (MV3 restart) — waiting for extension to reconnect")
                await wait_for_page_stable(self.driver.page, timeout_ms=8000)
        snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        if not snapshot or not snapshot.get("url"):
            # Still no snapshot — wait longer
            await wait_for_page_stable(self.driver.page, timeout_ms=8000)
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
                        snapshot = await self._reasoner_step(snapshot, platform, steps)
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
                        snapshot = await self._reasoner_step(snapshot, platform, steps)
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

        # Dismiss cookie banner (snapshot-based + Playwright-native belt-and-suspenders)
        await self.cookie_dismisser.dismiss(snapshot)
        current_page = getattr(self.driver, "page", None)
        if current_page is not None:
            await dismiss_cookie_banner_playwright(current_page)
        snapshot = self._as_dict(await self.driver.get_snapshot())

        # Dismiss site prompts/overlays before entering the navigation loop
        snapshot = await self._dismiss_site_prompt_if_present(snapshot)

        # ── Reasoner-driven navigation loop ──
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        reasoner = get_page_reasoner()

        visited_states: dict[str, int] = {}
        for step in range(MAX_NAVIGATION_STEPS):
            # Fast-path: DOM classifier for high-confidence terminal states
            dom_type, dom_confidence = self._dom_classify(snapshot)
            if dom_confidence >= 0.85 and dom_type == PageType.APPLICATION_FORM:
                logger.info("Fast-path: APPLICATION_FORM (confidence=%.2f)", dom_confidence)
                return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            if dom_confidence >= 0.85 and dom_type == PageType.CONFIRMATION:
                logger.info("Fast-path: CONFIRMATION (confidence=%.2f)", dom_confidence)
                return {"page_type": PageType.CONFIRMATION, "snapshot": snapshot}

            # Reasoner decides what to do
            action = reasoner.reason_sync(snapshot)
            logger.info(
                "Step %d: reasoner → %s (type=%s, conf=%.2f) — %s",
                step + 1, action.action, action.page_type, action.confidence,
                action.page_understanding[:80],
            )

            # Loop detection
            state_key = f"{action.page_type}:{action.action}"
            visited_states[state_key] = visited_states.get(state_key, 0) + 1
            if visited_states[state_key] >= 3:
                logger.warning("Reasoner loop: %s × %d — aborting", state_key, visited_states[state_key])
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Expired job — abort immediately, don't re-queue
            if action.page_type == "expired_job":
                logger.warning("Job expired/closed: %s", action.page_understanding)
                return {
                    "page_type": PageType.UNKNOWN,
                    "snapshot": snapshot,
                    "expired": True,
                    "error": action.page_understanding or "Job is no longer available",
                }

            # Terminal actions
            if action.action == "fill_form":
                return {"page_type": PageType.APPLICATION_FORM, "snapshot": snapshot}
            if action.action == "done":
                return {"page_type": PageType.CONFIRMATION, "snapshot": snapshot}
            if action.action == "abort":
                logger.warning("Reasoner says abort: %s", action.reasoning)
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

            # Verification wall / CAPTCHA — use existing bypass pipeline
            if action.action == "wait_human":
                wall_info = snapshot.get("verification_wall") or {"type": "unknown"}
                bypass_result = await self._bypass_verification_wall(snapshot, wall_info)
                if bypass_result["solved"]:
                    snapshot = bypass_result["snapshot"]
                    visited_states.clear()
                    continue
                if job:
                    pb_result = await self._try_platform_bypass(snapshot, job, steps)
                    if pb_result is not None:
                        snapshot = pb_result
                        visited_states.clear()
                        continue
                return {"page_type": PageType.VERIFICATION_WALL, "snapshot": bypass_result["snapshot"]}

            # SSO detection — check before executing generic fills
            if action.page_type in ("login_form", "signup_form", "session_expired"):
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                    steps.append({"page_type": action.page_type, "action": f"sso_{sso['provider']}"})
                    continue

            # Email verification — delegate to existing handler
            if action.page_type == "email_verification":
                snapshot = await self.auth.handle_email_verification(snapshot, platform, url)
                steps.append({"page_type": "email_verification", "action": "verify_email"})
                continue

            # Execute the reasoner's action on the page
            page = getattr(self.driver, "page", None)
            if page is not None:
                from jobpulse.applicator import PROFILE
                nav_executor = NavigationActionExecutor(page)
                await nav_executor.execute(action, profile=PROFILE)

            steps.append({"page_type": action.page_type, "action": action.action})

            # Post-action: dismiss cookies, get fresh snapshot
            await asyncio.sleep(1.0)
            await self.cookie_dismisser.dismiss(snapshot)
            if page is not None:
                await dismiss_cookie_banner_playwright(page)
                snapshot = await self._handle_new_tabs(page, snapshot)
            else:
                snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}

    async def click_apply_button(self, snapshot: dict) -> dict:
        buttons = snapshot.get("buttons", [])
        button_texts = [b.get("text", "")[:60] for b in buttons]
        logger.info("Apply button search: %d buttons found — %s", len(buttons), button_texts[:10])

        # Score all buttons using unified scoring
        scored: list[tuple[float, dict]] = []
        for btn in buttons:
            text = btn.get("text", "")
            if btn.get("enabled") is False:
                continue
            if len(text) > 50:
                continue
            score = score_apply_button(text)
            if score > 0:
                scored.append((score, btn))

        if not scored:
            logger.warning("No apply button found in snapshot — trying Playwright locator fallback")
            current_page = getattr(self.driver, "page", None)
            if current_page is not None:
                for text_pattern in ("Apply now", "Apply for this job", "Start application", "Apply"):
                    try:
                        loc = current_page.get_by_role("link", name=text_pattern, exact=False).first
                        if await loc.count() and await loc.is_visible():
                            logger.info("Playwright fallback: clicking link '%s'", text_pattern)
                            await loc.click()
                            await wait_for_page_stable(current_page, timeout_ms=8000)
                            return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    except Exception:
                        pass
                    try:
                        loc = current_page.get_by_role("button", name=text_pattern, exact=False).first
                        if await loc.count() and await loc.is_visible():
                            logger.info("Playwright fallback: clicking button '%s'", text_pattern)
                            await loc.click()
                            await wait_for_page_stable(current_page, timeout_ms=8000)
                            return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    except Exception:
                        pass
            return snapshot

        # Rank by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [btn for _, btn in scored]

        current_page = getattr(self.driver, "page", None)

        # If LinkedIn Easy Apply modal is already open (from a previous attempt),
        # skip navigation — going to a URL while the modal is open triggers
        # LinkedIn's "Save this application?" dialog.
        # Only check on LinkedIn pages — generic [role="dialog"] matches cookie
        # consent dialogs on external ATS sites, causing false positives.
        if current_page is not None:
            try:
                page_url = current_page.url or ""
                if "linkedin.com" in page_url:
                    modal = current_page.locator('.jobs-easy-apply-modal, [data-test-modal-id="easy-apply-modal"]')
                    if await modal.count():
                        logger.info("Easy Apply modal already open — skipping navigation")
                        return self._as_dict(await self.driver.get_snapshot())
            except Exception:
                pass

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
                await wait_for_page_stable(current_page or self.driver.page, timeout_ms=8000)
                if current_page is not None:
                    # LinkedIn draft dialog may take time to render — try twice
                    dismissed = await self._dismiss_linkedin_discard(current_page)
                    if not dismissed:
                        await wait_for_modal_open(current_page, timeout_ms=2000)
                        await self._dismiss_linkedin_discard(current_page)
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

        # Wait for modal or new form fields
        modal_found = await wait_for_modal_open(self.driver.page, timeout_ms=8000)
        if not modal_found:
            await wait_for_page_stable(self.driver.page, timeout_ms=3000)

        if current_page is not None:
            await self._dismiss_linkedin_discard(current_page)

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

    async def _bypass_verification_wall(self, snapshot: dict, wall_info: dict) -> dict:
        """Multi-stage Cloudflare/CAPTCHA bypass using full Playwright capabilities.

        Stages:
        1. Auto-wait — Cloudflare JS challenges auto-resolve in 3-10s
        2. Human interaction simulation — mouse movement, scroll, click
        3. Page reload — clears transient challenges
        4. Turnstile checkbox click — Cloudflare's interactive challenge
        5. Human fallback (MANDATORY) — Telegram alert, wait 120s
        """
        page = getattr(self.driver, "page", None)
        wall_type = wall_info.get("type", "unknown")
        wall_url = snapshot.get("url", "?")

        async def _check_cleared() -> dict | None:
            try:
                snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                await asyncio.sleep(2)
                try:
                    snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                except Exception:
                    return None
            re_type = await self.analyzer.detect(snap)
            if re_type != PageType.VERIFICATION_WALL:
                return snap
            return None

        # ── Stage 1: Auto-wait (Cloudflare JS challenge typically resolves in 3-10s) ──
        logger.info("Bypass stage 1: waiting for JS challenge auto-resolve (up to 15s)")
        for _poll in range(5):
            await asyncio.sleep(3)
            cleared = await _check_cleared()
            if cleared:
                logger.info("Bypass stage 1 succeeded: wall cleared after %ds", (_poll + 1) * 3)
                return {"solved": True, "snapshot": cleared}

        if page is None:
            logger.warning("Bypass: no page object — skipping interactive stages")
            return {"solved": False, "snapshot": snapshot}

        # ── Stage 2: Simulate human interaction ──
        logger.info("Bypass stage 2: simulating human interaction")
        try:
            import random
            await page.mouse.move(random.randint(100, 600), random.randint(100, 400))
            await asyncio.sleep(0.3)
            await page.mouse.move(random.randint(200, 700), random.randint(200, 500))
            await asyncio.sleep(0.5)
            await page.evaluate("window.scrollBy(0, 100)")
            await asyncio.sleep(1)
            await page.evaluate("window.scrollBy(0, -50)")
            await asyncio.sleep(1)
        except Exception as exc:
            logger.debug("Stage 2 interaction failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 2 succeeded: wall cleared after human simulation")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 3: Turnstile/checkbox click ──
        logger.info("Bypass stage 3: attempting Turnstile/checkbox click")
        try:
            for selector in (
                "iframe[src*='challenges.cloudflare.com']",
                "iframe[src*='turnstile']",
                ".cf-turnstile iframe",
            ):
                frame_el = page.locator(selector)
                if await frame_el.count():
                    frame = await frame_el.first.content_frame()
                    if frame:
                        checkbox = frame.locator("input[type='checkbox'], .cb-i, #challenge-stage")
                        if await checkbox.count():
                            await checkbox.first.click()
                            logger.info("Clicked Turnstile checkbox")
                            await asyncio.sleep(5)
                            cleared = await _check_cleared()
                            if cleared:
                                logger.info("Bypass stage 3 succeeded: Turnstile cleared")
                                return {"solved": True, "snapshot": cleared}
        except Exception as exc:
            logger.debug("Stage 3 Turnstile click failed: %s", exc)

        # ── Stage 4: Page reload ──
        logger.info("Bypass stage 4: reloading page")
        try:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
        except Exception as exc:
            logger.debug("Stage 4 reload failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 4 succeeded: wall cleared after reload")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 5: Second reload with networkidle ──
        logger.info("Bypass stage 5: second reload with networkidle wait")
        try:
            await page.reload(wait_until="networkidle", timeout=20000)
            await asyncio.sleep(5)
        except Exception as exc:
            logger.debug("Stage 5 reload failed: %s", exc)

        cleared = await _check_cleared()
        if cleared:
            logger.info("Bypass stage 5 succeeded: wall cleared after second reload")
            return {"solved": True, "snapshot": cleared}

        # ── Stage 6: MANDATORY human fallback ──
        logger.warning("All auto-bypass stages failed — requesting human intervention (MANDATORY)")
        try:
            from jobpulse.telegram_agent import send_message as _send_tg
            from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id
            _send_tg(
                f"🔒 Security wall ({wall_type}) on:\n{wall_url}\n\n"
                "Auto-bypass failed after 5 attempts.\n"
                "Please solve the challenge manually in Chrome — I'll wait up to 120 seconds.",
                chat_id=_chat_id,
            )
        except Exception:
            pass

        for _poll in range(24):
            await asyncio.sleep(5)
            cleared = await _check_cleared()
            if cleared:
                logger.info("Human solved the wall after %ds", (_poll + 1) * 5)
                try:
                    from jobpulse.telegram_agent import send_message as _send_tg2
                    from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id2
                    _send_tg2("✅ Security wall cleared — continuing application.", chat_id=_chat_id2)
                except Exception:
                    pass
                return {"solved": True, "snapshot": cleared}

        logger.error("Verification wall not cleared after all bypass stages + 120s human wait")
        try:
            from jobpulse.telegram_agent import send_message as _send_tg3
            from jobpulse.config import TELEGRAM_CHAT_ID as _chat_id3
            _send_tg3(
                f"❌ Could not bypass security wall on {wall_url}. Skipping this job.",
                chat_id=_chat_id3,
            )
        except Exception:
            pass
        try:
            snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        except Exception:
            snap = snapshot
        return {"solved": False, "snapshot": snap}

    async def _dismiss_site_prompt_if_present(self, snapshot: dict) -> dict:
        """Detect and dismiss non-application dialogs (site prompts, surveys, alerts)."""
        if not snapshot.get("has_dialog"):
            return snapshot

        dialog_text = snapshot.get("dialog_text", "").lower()
        if not dialog_text:
            return snapshot

        prompt_signals = (
            "are you interested", "not interested", "maybe later",
            "save application", "rate your experience", "take a survey",
            "subscribe", "newsletter", "job alert", "similar jobs",
            "how did you hear", "recommended for you",
        )
        is_prompt = any(sig in dialog_text for sig in prompt_signals)
        if not is_prompt:
            return snapshot

        logger.info("Site prompt dialog detected — attempting to dismiss: %s", dialog_text[:80])
        page = getattr(self.driver, "page", None)
        if page is None:
            return snapshot

        dismiss_texts = ("Close", "No thanks", "Not now", "Dismiss", "Skip", "Maybe later", "Not interested")
        for text in dismiss_texts:
            try:
                btn = page.get_by_role("button", name=text, exact=False)
                if await btn.count() and await btn.first.is_visible():
                    await btn.first.click()
                    logger.info("Dismissed site prompt via '%s'", text)
                    await asyncio.sleep(0.5)
                    return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                continue

        for selector in ('[aria-label="Close"]', '[aria-label="Dismiss"]', 'button.close', '[data-dismiss]'):
            try:
                loc = page.locator(selector)
                if await loc.count() and await loc.first.is_visible():
                    await loc.first.click()
                    logger.info("Dismissed site prompt via selector %s", selector)
                    await asyncio.sleep(0.5)
                    return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
            except Exception:
                continue

        logger.warning("Could not dismiss site prompt dialog — proceeding anyway")
        return snapshot

    async def _reasoner_step(self, snapshot: dict, platform: str, steps: list[dict]) -> dict:
        """Single reasoner-driven step — used during learned sequence replay fallback."""
        from jobpulse.page_analysis.page_reasoner import get_page_reasoner
        from jobpulse.navigation.action_executor import NavigationActionExecutor
        reasoner = get_page_reasoner()
        action = reasoner.reason_sync(snapshot)
        page = getattr(self.driver, "page", None)
        if page is not None:
            from jobpulse.applicator import PROFILE
            nav_executor = NavigationActionExecutor(page)
            await nav_executor.execute(action, profile=PROFILE)
        steps.append({"page_type": action.page_type, "action": action.action})
        await asyncio.sleep(1.0)
        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    @staticmethod
    def _dom_classify(snapshot: dict) -> tuple:
        from jobpulse.page_analysis.classifier import PageTypeClassifier
        clf = PageTypeClassifier()
        return clf.classify(snapshot)

    async def _handle_new_tabs(self, page, snapshot: dict) -> dict:
        """Check for new tabs after a click and switch to them."""
        context = getattr(page, "context", None)
        if context is None:
            return self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        pages = context.pages
        if len(pages) > 1:
            newest = pages[-1]
            try:
                await newest.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            if newest.url and newest.url != page.url:
                logger.info("Switched to new tab: %s", newest.url[:80])
                self.driver._page = newest
        return self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    async def _try_platform_bypass(self, snapshot: dict, job: dict, steps: list[dict]) -> dict | None:
        """Try platform bypass for aggregator walls. Returns new snapshot or None."""
        wall_url = snapshot.get("url", "")
        try:
            from jobpulse.platform_bypass import is_aggregator_domain, get_platform_bypass
            if not is_aggregator_domain(wall_url):
                return None
            logger.info("Aggregator wall on %s — attempting platform bypass", wall_url)
            page = getattr(self.driver, "page", None)
            pb = get_platform_bypass()
            pb_result = await pb.resolve_direct_url(job, wall_url, page)
            if pb_result.resolved:
                logger.info("Platform bypass: %s → %s", wall_url[:40], pb_result.direct_url[:60])
                await self.driver.page.goto(pb_result.direct_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
                new_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                steps.append({
                    "page_type": "platform_bypass",
                    "action": "redirect_to_ats",
                    "from_url": wall_url,
                    "to_url": pb_result.direct_url,
                    "strategy": pb_result.strategy_used,
                })
                return new_snap
        except Exception as exc:
            logger.debug("Platform bypass failed: %s", exc)
        return None

    async def verify_submission(self) -> dict:
        """Wait for and verify the confirmation page after submit click."""
        await wait_for_page_stable(self.driver.page, timeout_ms=5000)
        snapshot = await self.driver.get_snapshot(force_refresh=True)
        if not snapshot:
            return {"verified": False, "reason": "no_snapshot"}
        snapshot = self._as_dict(snapshot)
        text = (snapshot.get("page_text_preview") or "").lower()

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
        url = (snapshot.get("url") or "").lower()
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


def find_apply_button(snapshot: dict) -> dict | None:
    """Find the best apply button in a snapshot using unified scoring."""
    best: dict | None = None
    best_score = 0.0
    for btn in snapshot.get("buttons", []):
        if not btn.get("enabled"):
            continue
        score = score_apply_button(btn.get("text", ""))
        if score > best_score:
            best_score = score
            best = btn
    return best if best_score >= 0.4 else None
