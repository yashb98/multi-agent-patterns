"""Form filling — multi-page form state machine with two-phase fill.

Handles: state machine page loop, deterministic + LLM fill, gotcha
workarounds, stuck detection, MV3 progress recovery, anti-detection timing.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ext_models import PageType
from jobpulse.state_machines import (
    ApplicationState,
    find_next_button,
    find_submit_button,
    get_state_machine,
    is_page_stuck,
)

if TYPE_CHECKING:
    from jobpulse.application_orchestrator_pkg._executor import ActionExecutor
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    from jobpulse.form_engine.gotchas import GotchasDB

logger = get_logger(__name__)

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
    """Check if a field is critical (missing it = worthless application)."""
    if label:
        label_lower = label.lower()
        if any(p in label_lower for p in _CRITICAL_FIELD_PATTERNS):
            return True
    import re as _re
    cleaned = _re.sub(r"\[.*?\]", "", selector).lower()
    return any(p in cleaned for p in _CRITICAL_FIELD_PATTERNS)


class FormFiller:
    """Multi-page form filling via state machine with two-phase fill."""

    def __init__(self, orch, executor: "ActionExecutor", navigator: "FormNavigator"):
        self._orch = orch
        self.executor = executor
        self.navigator = navigator

    @property
    def driver(self):
        return self._orch.driver

    @property
    def gotchas(self):
        return self._orch.gotchas

    @property
    def engine(self):
        return self._orch.engine

    def _to_page_snapshot(self, snapshot):
        return self._orch._to_page_snapshot(snapshot)

    @staticmethod
    def _as_dict(snapshot: Any) -> dict:
        if hasattr(snapshot, "model_dump"):
            return snapshot.model_dump()
        return snapshot

    async def fill_application(
        self, platform, snapshot, cv_path, cover_letter_path, profile,
        custom_answers, overrides, dry_run, form_intelligence,
    ) -> dict:
        """Multi-page form filling — branches by engine.

        engine='playwright': NativeFormFiller (locators + LLM)
        engine='extension': state machine + snapshots (original path)
        """
        if self.engine == "playwright":
            from jobpulse.native_form_filler import NativeFormFiller
            filler = NativeFormFiller(page=self.driver.page, driver=self.driver)
            return await filler.fill(
                platform=platform,
                cv_path=str(cv_path) if cv_path else None,
                cl_path=str(cover_letter_path) if cover_letter_path else None,
                profile=profile or {},
                custom_answers=custom_answers or {},
                dry_run=dry_run,
            )

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

        # Load known gotchas for this domain (learned from manual fixes)
        from jobpulse.application_orchestrator_pkg._navigator import extract_domain
        domain = extract_domain(current_url) if current_url else platform
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
                apply_snapshot = await self.navigator.click_apply_button(
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
                    action = _apply_gotcha_to_action(action, solution)
                    # Re-read type after gotcha modification
                    atype = getattr(action, "type", None) or (action.get("type", "?") if isinstance(action, dict) else "?")
                    sel = getattr(action, "selector", None) or (action.get("selector", "?") if isinstance(action, dict) else "?")

                logger.info("  Action %d/%d: %s → %s", i + 1, len(actions), atype, str(sel)[:60])
                try:
                    # Pre-action gotcha steps (scroll, wait)
                    if gotcha:
                        await _execute_gotcha_pre_steps(self.driver, gotcha["solution"], str(sel))
                    await self.executor.execute_action_with_retry(action, tg_stream=tg_stream)
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

            # ── Post-fill verification: check that values stuck ──
            if filled_selectors:
                try:
                    await asyncio.sleep(0.5)
                    verify_snap = self._to_page_snapshot(
                        self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                    )
                    retries = await self._verify_filled_fields(filled_selectors, actions, verify_snap)
                    if retries > 0:
                        logger.info("  Post-fill verification: retried %d empty fields", retries)
                except (TimeoutError, ConnectionError):
                    pass  # Non-critical — proceed without verification

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
                    verification = await self._orch._verify_submission()
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
                                    await self.executor.execute_action_with_retry(ra, tg_stream=tg_stream)
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
                await self.executor.execute_action(action)
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

    async def _verify_filled_fields(
        self,
        filled_selectors: set[str],
        actions: list,
        snapshot,
    ) -> int:
        """Verify filled fields have values, retry empty ones. Returns retry count."""
        if not filled_selectors:
            return 0

        retry_count = 0
        action_map = {}
        for a in actions:
            sel = getattr(a, "selector", None) or (a.get("selector") if isinstance(a, dict) else None)
            if sel:
                action_map[sel] = a

        for field in snapshot.fields:
            if field.selector not in filled_selectors:
                continue
            if field.input_type == "file":
                continue
            if field.current_value and field.current_value.strip():
                continue

            original_action = action_map.get(field.selector)
            if not original_action:
                continue

            logger.info("  Verify: %s is empty after fill — retrying", field.selector[:40])
            try:
                await self.executor.execute_action_with_retry(original_action)
                retry_count += 1
            except (TimeoutError, ConnectionError) as exc:
                logger.warning("  Verify retry failed for %s: %s", field.selector[:40], exc)

        return retry_count


# ── Module-level utilities ──

def _apply_gotcha_to_action(action: Any, solution: str) -> Any:
    """Modify an action based on a gotcha solution string."""
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


async def _execute_gotcha_pre_steps(driver, solution: str, selector: str) -> None:
    """Execute pre-action steps from a gotcha solution (scroll, wait)."""
    if "scroll_first" in solution:
        try:
            await driver.scroll_to(selector)
        except (TimeoutError, ConnectionError):
            logger.debug("Gotcha scroll_to failed for %s", selector[:40])
    if solution.startswith("wait_before:"):
        try:
            wait_ms = int(solution.split(":")[1])
            await asyncio.sleep(wait_ms / 1000.0)
        except (ValueError, IndexError):
            await asyncio.sleep(1.0)
