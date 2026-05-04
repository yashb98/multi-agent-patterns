"""Live review applicator — one job at a time, same-page approval before submit.

This replaces the operational role of the old draft queue for the normal apply
path. The invariant remains the same: AI agents can fill the form, but never
submit without explicit human approval.

Flow:
1. `start_live_review(job)` launches exactly one background review session.
2. The session opens Chrome, navigates to the job, fills the form with
   `dry_run=True`, and stops at the submit button.
3. The user reviews the live page in Chrome and replies `yes` / `no` in
   Telegram through the existing approval system.
4. `yes` clicks Submit on the same page and runs `confirm_application()`.
5. `no` closes the live tab and returns the job to the pending-review state.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.daemon_threads import (
    heartbeat_daemon_thread,
    register_daemon_thread,
    stop_daemon_thread,
)
from shared.logging_config import get_logger
from shared.locks import process_lock

from jobpulse.ai_assist_logger import AIAssistLogger, get_ai_assist_logger
from jobpulse.applicator import confirm_application, prepare_application_inputs
from jobpulse.approval import request_approval
from jobpulse.config import APPLICANT_PROFILE as PROFILE, DATA_DIR, TELEGRAM_CHAT_ID
from jobpulse.telegram_agent import send_message as send_telegram

logger = get_logger(__name__)

SCREENSHOT_DIR = DATA_DIR / "live_review_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
_ACTIVE_REVIEW_FILE = DATA_DIR / "live_review_active.json"

_LOOP_THREAD_REGISTRY_KEY = "live_review_applicator.loop"

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()

_active_session: "LiveReviewSession | None" = None
_active_lock = process_lock("jobpulse_live_review_active_session")


def _clear_active_review_file() -> None:
    try:
        _ACTIVE_REVIEW_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("live_review_applicator: failed clearing active review file: %s", exc)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


_STALE_MAX_AGE_SECONDS = 7200  # 2 hours


def _load_persisted_review() -> dict[str, Any] | None:
    if not _ACTIVE_REVIEW_FILE.exists():
        return None
    try:
        data = json.loads(_ACTIVE_REVIEW_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("live_review_applicator: failed loading active review file: %s", exc)
        _clear_active_review_file()
        return None

    owner_pid = data.get("pid")
    started_at = data.get("started_at")

    if not owner_pid and not started_at:
        logger.warning("live_review_applicator: clearing legacy session (no pid/timestamp)")
        _clear_active_review_file()
        return None

    if started_at and (time.time() - started_at) > _STALE_MAX_AGE_SECONDS:
        logger.warning(
            "live_review_applicator: clearing stale session (age=%.0fs, max=%ds)",
            time.time() - started_at, _STALE_MAX_AGE_SECONDS,
        )
        _clear_active_review_file()
        return None

    if owner_pid and not _is_pid_alive(owner_pid):
        logger.warning(
            "live_review_applicator: clearing orphaned session (pid=%d is dead)", owner_pid,
        )
        _clear_active_review_file()
        return None

    return data


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start and return the persistent background event loop."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            heartbeat_daemon_thread(_LOOP_THREAD_REGISTRY_KEY)
            return _loop

        _loop = asyncio.new_event_loop()

        def _run() -> None:
            assert _loop is not None
            register_daemon_thread(
                _LOOP_THREAD_REGISTRY_KEY,
                kind="live_review_event_loop",
                thread_name="live-review-loop",
                metadata={"component": "live_review_applicator"},
            )
            try:
                asyncio.set_event_loop(_loop)
                _loop.run_forever()
            finally:
                stop_daemon_thread(_LOOP_THREAD_REGISTRY_KEY)

        _loop_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="live-review-loop",
        )
        _loop_thread.start()
        return _loop


def _run_async(coro: Any, timeout: float | None = None) -> Any:
    """Schedule *coro* on the background loop and block until it completes."""
    loop = _ensure_loop()
    heartbeat_daemon_thread(_LOOP_THREAD_REGISTRY_KEY)
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


def _bring_chrome_to_front(url: str | None = None) -> None:
    """Focus Chrome (and the job tab if given) via AppleScript. Best effort."""
    try:
        if url:
            script = f'''
            tell application "Google Chrome"
                activate
                repeat with w in windows
                    repeat with t in tabs of w
                        if (t's URL contains "{url}") then
                            set active tab index of w to (index of t)
                            set index of w to 1
                            return
                        end if
                    end repeat
                end repeat
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                return
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to activate'],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        logger.debug("osascript not available — cannot focus Chrome")
    except Exception as exc:
        logger.debug("Failed to focus Chrome: %s", exc)


class LiveReviewSession:
    """One live application session that pauses for yes/no approval."""

    def __init__(self, job: dict[str, Any], *, session_id: str | None = None) -> None:
        self.job = job
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.url: str = job["url"]

        jid = str(job.get("job_id") or "")
        cv_candidate: Path | None = (
            Path(job["cv_path"]) if job.get("cv_path") else None
        )
        if cv_candidate is None or not cv_candidate.is_file():
            if jid:
                from jobpulse.application_materials import ensure_tailored_cv_for_job

                gen_cv = ensure_tailored_cv_for_job(jid)
                if gen_cv:
                    cv_candidate = gen_cv

        cl_gen = None
        if jid:
            from jobpulse.application_materials import build_lazy_cover_letter_generator

            cl_gen = build_lazy_cover_letter_generator(jid)

        prep = prepare_application_inputs(
            url=self.url,
            ats_platform=job.get("ats_platform") or job.get("platform"),
            custom_answers=job.get("custom_answers"),
            job_context={
                "job_id": jid,
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "url": self.url,
            },
            cover_letter_path=(
                Path(job["cover_letter_path"]) if job.get("cover_letter_path") else None
            ),
            cl_generator=cl_gen,
        )
        self.ats_platform: str | None = prep["ats_platform"]
        self.platform_key: str = prep["platform_key"]
        self.merged_answers: dict = prep["merged_answers"]
        self.cover_letter_path: Path | None = prep["cover_letter_path"]
        self.cv_path: Path | None = cv_candidate

        self._driver: Any | None = None
        self._page: Any | None = None
        self._fill_result: dict[str, Any] = {}
        self._agent_mapping: dict[str, str] = {}
        self._final_mapping: dict[str, str] = {}
        self._ai_assist_session_id: str | None = None

        self._action: str | None = None
        self._action_event = threading.Event()

    def _persist_state(self, status: str, **extra: Any) -> None:
        """Persist the current review state so approval can survive restarts."""
        page_url = self.url
        try:
            if self._page is not None and getattr(self._page, "url", ""):
                page_url = self._page.url
        except Exception:
            page_url = self.url

        payload = {
            "status": status,
            "session_id": self.session_id,
            "pid": os.getpid(),
            "started_at": time.time(),
            "job": self.job,
            "url": self.url,
            "approval_page_url": page_url,
            "ats_platform": self.ats_platform,
            "platform_key": self.platform_key,
            "agent_mapping": self._agent_mapping,
            "fill_result": self._fill_result,
        }
        payload.update(extra)
        try:
            _ACTIVE_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
            _ACTIVE_REVIEW_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("live_review_applicator: failed persisting active review: %s", exc)

    @classmethod
    def from_persisted(cls, payload: dict[str, Any]) -> "LiveReviewSession":
        session = cls(payload["job"], session_id=str(payload.get("session_id") or "restored"))
        session._fill_result = dict(payload.get("fill_result") or {})
        session._agent_mapping = dict(payload.get("agent_mapping") or {})
        return session

    def set_action(self, action: str) -> None:
        if self._action is None:
            self._action = action
            self._action_event.set()

    def reset_action(self) -> None:
        self._action = None
        self._action_event.clear()

    def wait_for_action(self, timeout: float = 24 * 3600) -> str:
        self._action_event.wait(timeout=timeout)
        return self._action or "cancel"

    def _failed_fill_labels(self) -> list[str]:
        stats = self._fill_result.get("agent_fill_stats") or {}
        labels = stats.get("failed_labels") or []
        return [str(label).strip() for label in labels if str(label).strip()]

    def start_ai_assist(self, agent_name: str) -> str:
        """Start an AI assist session linked to this live review.

        Returns the session_id that the AI assistant should use for
        record_fix() / record_strategy() calls.
        """
        try:
            logger.info(
                "live_review_applicator: starting AI assist (%s) for %s @ %s",
                agent_name,
                self.job.get("title"),
                self.job.get("company"),
            )
            domain = self._domain_from_url(self.url)
            logger_instance = get_ai_assist_logger()
            session = logger_instance.start_session(
                agent_name=agent_name,
                job_id=str(self.job.get("job_id") or ""),
                domain=domain,
                platform=self.platform_key,
                original_mapping=dict(self._agent_mapping),
            )
            self._ai_assist_session_id = session.session_id
            return session.session_id
        except Exception as exc:
            logger.warning("live_review_applicator: AI assist start failed: %s", exc)
            return ""

    def pull_ai_assist_data(self) -> dict[str, Any]:
        """Read any AI assist fixes for this job and merge into final_mapping.

        Called automatically before submit so AI-corrected values are included
        in the final application mapping and learning pipeline.
        """
        session_id = getattr(self, "_ai_assist_session_id", None)
        if not session_id:
            return {"fixes": 0, "strategies": 0}
        try:
            logger_instance = get_ai_assist_logger()
            fixes = logger_instance.get_fixes(session_id)
            strategies = logger_instance.get_strategies(session_id)
            merged = 0
            for fix in fixes:
                label = fix.get("field_label", "")
                new_val = fix.get("new_value", "")
                if label and new_val:
                    self._final_mapping[label] = new_val
                    merged += 1
            return {"fixes": len(fixes), "strategies": len(strategies), "merged": merged}
        except Exception as exc:
            logger.warning("live_review_applicator: AI assist pull failed: %s", exc)
            return {"fixes": 0, "strategies": 0}

    @staticmethod
    def _domain_from_url(url: str) -> str:
        from urllib.parse import urlparse
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def resolve_approval(self, approved: bool) -> str:
        """Approval callback invoked by the Telegram listener."""
        if approved:
            self.set_action("submit")
            return (
                f"Submitting {self.job.get('title')} @ {self.job.get('company')} now. "
                "I'll message you again when the submission finishes."
            )

        self.set_action("cancel")
        return (
            f"Cancelled submission for {self.job.get('title')} @ {self.job.get('company')}. "
            "The job is back in the pending review list."
        )

    async def _fill_async(self) -> dict:
        from jobpulse.application_orchestrator import ApplicationOrchestrator
        from jobpulse.playwright_driver import PlaywrightDriver

        if self.cv_path is None or not self.cv_path.exists():
            raise FileNotFoundError(f"CV not found: {self.cv_path}")

        driver = PlaywrightDriver()
        await driver.connect()
        # Clear stale state from previous runs — navigate to blank page first
        try:
            await driver.page.goto("about:blank", wait_until="load", timeout=5000)
        except Exception:
            pass
        self._driver = driver
        self._page = driver.page

        orchestrator = ApplicationOrchestrator(driver=driver, engine="playwright")
        return await orchestrator.apply(
            url=self.url,
            platform=self.platform_key,
            cv_path=self.cv_path,
            cover_letter_path=self.cover_letter_path,
            profile=PROFILE,
            custom_answers=self.merged_answers,
            overrides=None,
            dry_run=True,
            job=self.job,
        )

    def fill_and_request_approval(self) -> None:
        logger.info(
            "live_review_applicator: filling live review %s (%s @ %s)",
            self.session_id,
            self.job.get("title"),
            self.job.get("company"),
        )
        try:
            result = _run_async(self._fill_async(), timeout=15 * 60)
        except Exception as exc:
            logger.error("live_review_applicator: fill failed: %s", exc)
            self._restore_pending_status()
            self._record_failure_learning(str(exc))
            _clear_active_review_file()
            send_telegram(
                f"❌ Failed to fill {self.job.get('title')} @ {self.job.get('company')}:\n{exc}",
                chat_id=TELEGRAM_CHAT_ID,
            )
            self.set_action("error")
            return

        self._fill_result = result or {}
        self._agent_mapping = dict(self._fill_result.get("agent_mapping") or {})

        if not self._fill_result.get("success"):
            err = self._fill_result.get("error", "fill returned success=False")
            is_expired = self._fill_result.get("expired", False)
            logger.warning("live_review_applicator: fill did not reach submit page: %s", err)

            if is_expired:
                self._mark_expired()
            else:
                self._restore_pending_status()

            self._record_failure_learning(err, expired=is_expired)
            _clear_active_review_file()

            status_emoji = "💀" if is_expired else "❌"
            status_label = "Job expired" if is_expired else "Could not reach the submit page for"
            send_telegram(
                f"{status_emoji} {status_label} "
                f"{self.job.get('title')} @ {self.job.get('company')}:\n{err}",
                chat_id=TELEGRAM_CHAT_ID,
            )
            self.set_action("error")
            return

        screenshot_path = self._capture_screenshot()
        unresolved_labels = self._failed_fill_labels()
        self._persist_state(
            "awaiting_approval",
            screenshot_path=screenshot_path,
            unresolved_labels=unresolved_labels,
        )
        _bring_chrome_to_front(url=self.url)
        self._send_review_notification(
            screenshot_path,
            unresolved_labels=unresolved_labels or None,
        )
        request_approval(
            question=self._review_question(unresolved_labels or None),
            timeout_seconds=24 * 3600,
            callback=self.resolve_approval,
            persistent_context={"kind": "live_review", "session_id": self.session_id},
        )

    def _capture_screenshot(self) -> str | None:
        async def _shot() -> str | None:
            if self._page is None:
                return None
            path = SCREENSHOT_DIR / (
                f"review_{self.session_id}_"
                f"{datetime.now(timezone.utc).strftime('%H%M%S')}.png"
            )
            try:
                await self._page.screenshot(path=str(path), full_page=True)
                return str(path)
            except Exception as exc:
                logger.warning("live_review_applicator: screenshot failed: %s", exc)
                return None

        try:
            return _run_async(_shot(), timeout=30)
        except Exception as exc:
            logger.warning("live_review_applicator: screenshot dispatch failed: %s", exc)
            return None

    def _review_question(self, unresolved_labels: list[str] | None = None) -> str:
        if unresolved_labels:
            joined = ", ".join(unresolved_labels)
            return (
                f"Human help needed before submit for {self.job.get('title')} @ "
                f"{self.job.get('company')}. The live form is open in Chrome. "
                f"Please review/fix: {joined}. Reply yes when the page is ready to submit, "
                "or no to keep it pending."
            )
        return (
            f"Submit application for {self.job.get('title')} @ "
            f"{self.job.get('company')}? The live form is open in Chrome."
        )

    def _send_review_notification(
        self,
        screenshot_path: str | None,
        *,
        unresolved_labels: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        lines = [
            "🧭 Application Ready for Review",
            "",
            f"Job: {self.job.get('title', 'unknown')}",
            f"Company: {self.job.get('company', 'unknown')}",
            f"Platform: {self.job.get('platform', 'generic')}",
        ]
        if self.job.get("ats_score"):
            lines.append(f"ATS Score: {self.job['ats_score']:.1f}%")
        if reason:
            lines.extend(["", f"Reason: {reason}"])
        if unresolved_labels:
            lines.extend(
                [
                    "",
                    "AI still needs human help on these fields:",
                    *[f"  - {label}" for label in unresolved_labels],
                    "",
                    "Edit them directly in Chrome. Your final edits will be learned for future runs.",
                ]
            )
        lines.extend(
            [
                "",
                "Chrome is focused on the live form.",
                "Review the answers, make any edits you want, then reply:",
                "  yes  — submit the application",
                "  no   — cancel and return it to pending review",
            ]
        )
        caption = "\n".join(lines)

        doc_pairs: list[tuple[str, str]] = []
        if self.cv_path and self.cv_path.exists():
            doc_pairs.append((str(self.cv_path), f"CV for review: {self.job.get('title')} @ {self.job.get('company')}"))
        if self.cover_letter_path and self.cover_letter_path.exists():
            doc_pairs.append(
                (
                    str(self.cover_letter_path),
                    f"Cover letter for review: {self.job.get('title')} @ {self.job.get('company')}",
                )
            )

        try:
            from jobpulse.telegram_bots import send_jobs_document

            for doc_path, doc_caption in doc_pairs:
                send_jobs_document(doc_path, caption=doc_caption)
        except Exception as exc:
            logger.debug("live_review_applicator: send_jobs_document failed: %s", exc)

        sent_photo = False
        if screenshot_path:
            try:
                from jobpulse.telegram_bots import send_jobs_photo

                sent_photo = send_jobs_photo(screenshot_path, caption=caption)
            except Exception as exc:
                logger.debug("live_review_applicator: send_jobs_photo failed: %s", exc)
        if not sent_photo:
            if screenshot_path:
                caption += f"\n\nScreenshot: {screenshot_path}"
            send_telegram(caption, chat_id=TELEGRAM_CHAT_ID)

    def _request_manual_help(
        self,
        unresolved_labels: list[str],
        *,
        reason: str,
    ) -> None:
        screenshot_path = self._capture_screenshot()
        self._persist_state(
            "awaiting_approval",
            screenshot_path=screenshot_path,
            unresolved_labels=unresolved_labels,
            help_reason=reason,
        )
        _bring_chrome_to_front(url=self.url)
        self._send_review_notification(
            screenshot_path,
            unresolved_labels=unresolved_labels,
            reason=reason,
        )
        request_approval(
            question=self._review_question(unresolved_labels),
            timeout_seconds=24 * 3600,
            callback=self.resolve_approval,
            persistent_context={"kind": "live_review", "session_id": self.session_id},
        )

    async def _capture_final_mapping_async(self, filler: Any) -> dict[str, str]:
        """Read live page values right before click-submit."""
        page = self._page
        if page is None:
            return dict(self._agent_mapping)

        # Pull any AI assist fixes into the mapping before reading the page
        self.pull_ai_assist_data()

        final: dict[str, str] = {}

        async def _read(loc: Any, label: str, kind: str) -> None:
            if not label:
                return
            try:
                if kind in ("text", "textarea"):
                    final[label] = (await loc.input_value()) or ""
                elif kind == "select":
                    final[label] = await loc.evaluate(
                        "el => el.options[el.selectedIndex]?.text?.trim() || ''"
                    )
                elif kind == "combobox":
                    final[label] = await loc.evaluate(
                        """el => {
                            const own = (el.value || '').trim();
                            if (own) return own;
                            let node = el.parentElement;
                            for (let i = 0; node && i < 5; i += 1, node = node.parentElement) {
                                const display = node.querySelector('.select__single-value, [class*="singleValue"]');
                                const text = display?.textContent?.trim();
                                if (text) return text;
                            }
                            return '';
                        }"""
                    )
                elif kind == "checkbox":
                    final[label] = "true" if await loc.is_checked() else "false"
                elif kind == "radio_group":
                    selected = ""
                    for radio in await loc.get_by_role("radio").all():
                        try:
                            if await radio.is_checked():
                                selected = await filler._get_accessible_name(radio)
                                break
                        except Exception:
                            continue
                    final[label] = selected
            except Exception as exc:
                logger.debug(
                    "live_review_applicator: final-mapping read failed for %r: %s",
                    label,
                    exc,
                )

        try:
            for loc in await page.get_by_role("textbox").all():
                label = await filler._get_accessible_name(loc)
                await _read(loc, label, "text")

            for loc in await page.get_by_role("combobox").all():
                label = await filler._get_accessible_name(loc)
                try:
                    tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                except Exception:
                    tag = "combobox"
                await _read(loc, label, "select" if tag == "select" else "combobox")

            for loc in await page.get_by_role("radiogroup").all():
                label = await filler._get_accessible_name(loc)
                await _read(loc, label, "radio_group")

            for loc in await page.get_by_role("checkbox").all():
                label = await filler._get_accessible_name(loc)
                await _read(loc, label, "checkbox")

            for loc in await page.locator("textarea:visible").all():
                label = await filler._get_accessible_name(loc)
                await _read(loc, label, "textarea")
        except Exception as exc:
            logger.warning(
                "live_review_applicator: final-mapping capture crashed: %s",
                exc,
            )
            return dict(self._agent_mapping)

        return final

    async def _attach_to_existing_page_async(self, page_url: str | None = None) -> None:
        """Reconnect to the existing live application tab after a daemon restart."""
        from jobpulse.playwright_driver import PlaywrightDriver

        driver = PlaywrightDriver()
        await driver.connect()
        context = driver._context
        assert context is not None, "live review resume has no browser context"

        target = None
        desired = page_url or self.url
        pages = list(context.pages)
        for candidate in reversed(pages):
            current_url = getattr(candidate, "url", "")
            if current_url == desired:
                target = candidate
                break
        if target is None:
            for candidate in reversed(pages):
                current_url = getattr(candidate, "url", "")
                if desired and desired in current_url:
                    target = candidate
                    break
        if target is None:
            for candidate in reversed(pages):
                current_url = getattr(candidate, "url", "")
                if "job-boards.greenhouse.io" in current_url or self.job.get("company", "").lower() in current_url.lower():
                    target = candidate
                    break
        if target is None:
            await driver.close()
            raise RuntimeError("Could not find the live application tab to resume.")

        blank_page = driver.page
        if blank_page is not None and blank_page is not target:
            try:
                if getattr(blank_page, "url", "") in {"about:blank", "chrome://new-tab-page/", "data:,"}:
                    await blank_page.close()
            except Exception:
                pass

        driver._page = target
        self._driver = driver
        self._page = target

    async def _click_submit_async(self) -> dict[str, Any]:
        from jobpulse.native_form_filler import NativeFormFiller

        assert self._page is not None, "live review session has no live page"
        filler = NativeFormFiller(page=self._page, driver=self._driver)

        try:
            self._final_mapping = await self._capture_final_mapping_async(filler)
        except Exception as exc:
            logger.warning(
                "live_review_applicator: final-mapping capture failed, falling back: %s",
                exc,
            )
            self._final_mapping = dict(self._agent_mapping)

        clicked = await filler._click_navigation(dry_run=False)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        await asyncio.sleep(3)

        final_url = self._page.url
        page_text = ""
        try:
            body = self._page.locator("body")
            if await body.count():
                page_text = (await body.first.text_content() or "").lower()
        except Exception:
            page_text = ""

        success_markers = (
            "thank",
            "success",
            "received",
            "confirmation",
            "application sent",
            "application submitted",
            "we've received",
            "we have received",
        )
        error_markers = ("error", "failed", "please correct", "required field")

        saw_success = any(marker in final_url.lower() or marker in page_text for marker in success_markers)
        saw_error = any(marker in page_text for marker in error_markers) and not saw_success

        return {
            "clicked": clicked,
            "final_url": final_url,
            "saw_success": saw_success,
            "saw_error": saw_error,
        }

    def _remaining_manual_help_labels(self) -> list[str]:
        unresolved: list[str] = []
        for label in self._failed_fill_labels():
            value = str(self._final_mapping.get(label, "")).strip().lower()
            if not value:
                unresolved.append(label)
                continue
            if value in {"select", "select...", "select country", "choose", "choose..."}:
                unresolved.append(label)
                continue
            if "consent" in label.lower() and value in {"false", "no", "0"}:
                unresolved.append(label)
        return unresolved

    def run_submit_and_confirm(self) -> str:
        """Click Submit on the live page, then run the post-submit pipeline."""
        logger.info(
            "live_review_applicator: submitting %s @ %s",
            self.job.get("title"),
            self.job.get("company"),
        )

        result: dict[str, Any]
        try:
            click = _run_async(self._click_submit_async(), timeout=120)

            # Incorporate AI assist metrics into agent_performance
            ai_meta = self._ai_assist_meta()

            unresolved_labels = self._remaining_manual_help_labels()
            if unresolved_labels:
                self.reset_action()
                self._request_manual_help(
                    unresolved_labels,
                    reason="Some fields still need human fixes before submit.",
                )
                return "awaiting_human"
            if click.get("clicked") not in ("submitted", "next"):
                self.reset_action()
                self._request_manual_help(
                    self._failed_fill_labels() or ["Submit button / navigation"],
                    reason="I could not find or activate the final submit step safely.",
                )
                return "awaiting_human"
            elif click.get("saw_error"):
                self.reset_action()
                self._request_manual_help(
                    self._failed_fill_labels() or ["Form validation errors"],
                    reason="Form validation still failed after submit. Please fix the page in Chrome.",
                )
                return "awaiting_human"
            else:
                result = {"success": True, "final_url": click.get("final_url")}

            if result["success"]:
                self._run_confirm_application(ai_meta=ai_meta)
            else:
                self._restore_pending_status()
        except Exception as exc:
            logger.exception("live_review_applicator: submit crashed: %s", exc)
            self.reset_action()
            self._request_manual_help(
                self._failed_fill_labels() or ["Unexpected submit error"],
                reason=f"Submit attempt crashed: {exc}",
            )
            return "awaiting_human"

        self._send_post_submit_notification(result)
        return "completed"

    def _ai_assist_meta(self) -> dict[str, Any]:
        """Gather AI assist metadata for this session."""
        session_id = getattr(self, "_ai_assist_session_id", None)
        if not session_id:
            return {}
        try:
            logger_instance = get_ai_assist_logger()
            session = logger_instance.get_session(session_id)
            fixes = logger_instance.get_fixes(session_id)
            strategies = logger_instance.get_strategies(session_id)
            if not session:
                return {}
            return {
                "ai_agent_name": session.get("agent_name", ""),
                "ai_fixes_count": len(fixes),
                "ai_strategies_count": len(strategies),
                "ai_reasoning_summary": self._summarize_ai_reasoning(fixes),
            }
        except Exception as exc:
            logger.debug("ai_assist_meta failed: %s", exc)
            return {}

    @staticmethod
    def _summarize_ai_reasoning(fixes: list[dict[str, Any]]) -> str:
        reasons = [f.get("reasoning", "") for f in fixes if f.get("reasoning")]
        if not reasons:
            return ""
        return " | ".join(reasons[:5])

    def _run_confirm_application(self, ai_meta: dict[str, Any] | None = None) -> None:
        try:
            confirm_application(
                dry_run_result=dict(self._fill_result),
                url=self.url,
                cv_path=self.cv_path or Path("/dev/null"),
                cover_letter_path=self.cover_letter_path,
                job_context={
                    "job_id": self.job.get("job_id", ""),
                    "company": self.job.get("company", ""),
                    "title": self.job.get("title", ""),
                    "notion_page_id": self.job.get("notion_page_id"),
                    "match_tier": self.job.get("match_tier"),
                    "ats_score": self.job.get("ats_score"),
                    "matched_projects": self.job.get("matched_projects"),
                    "platform": self.platform_key,
                },
                ats_platform=self.ats_platform,
                agent_mapping=self._agent_mapping,
                final_mapping=self._final_mapping or self._agent_mapping,
                ai_meta=ai_meta,
            )
        except Exception as exc:
            logger.warning("live_review_applicator: confirm_application failed: %s", exc)

    def _send_post_submit_notification(self, result: dict[str, Any]) -> None:
        if result.get("success"):
            text = (
                f"✅ Submitted: {self.job.get('title')} @ {self.job.get('company')}\n"
                f"Final URL: {result.get('final_url', 'N/A')}"
            )
        else:
            text = (
                f"❌ Submit failed for {self.job.get('title')} @ {self.job.get('company')}:\n"
                f"{result.get('error', 'unknown error')}\n"
                "The job was returned to pending review."
            )
        send_telegram(text, chat_id=TELEGRAM_CHAT_ID)

    def run_reject(self) -> None:
        self._restore_pending_status()
        send_telegram(
            f"⏭ Kept pending: {self.job.get('title')} @ {self.job.get('company')}",
            chat_id=TELEGRAM_CHAT_ID,
        )

    def _restore_pending_status(self) -> None:
        from jobpulse.job_db import JobDB

        job_id = self.job.get("job_id")
        if not job_id:
            return
        try:
            JobDB().update_status(job_id, "Pending Approval")
        except Exception as exc:
            logger.warning("live_review_applicator: failed to restore Pending Approval: %s", exc)

    def _mark_expired(self) -> None:
        """Mark job as Expired in both SQLite and Notion so it never re-enters the queue."""
        from jobpulse.job_db import JobDB

        job_id = self.job.get("job_id")
        if job_id:
            try:
                JobDB().update_status(job_id, "Expired")
            except Exception as exc:
                logger.warning("_mark_expired: SQLite update failed: %s", exc)

        notion_page_id = self.job.get("notion_page_id") or self.job.get("_notion_page_id")
        if notion_page_id:
            try:
                from jobpulse.job_notion_sync import update_application_page
                # Notion's Status property only accepts the canonical lifecycle
                # values (Found / Analyzing / Ready / Pending Approval / Applied
                # / Interview / Offer / Rejected / Withdrawn). "Expired" is NOT
                # in the schema — sending it triggers a 400 that the retry loop
                # strips, leaving the job at Status=Found and re-pulled forever.
                # Map to "Withdrawn" (closest semantic — we're withdrawing the
                # job from the queue, not the recruiter rejecting us). The note
                # preserves the actual reason.
                update_application_page(
                    notion_page_id,
                    status="Withdrawn",
                    notes="Job expired / no longer available (auto-withdrawn)",
                )
            except Exception as exc:
                logger.warning("_mark_expired: Notion update failed: %s", exc)

    def _record_failure_learning(self, error: str, *, expired: bool = False) -> None:
        """OPRAL Learn phase — emit failure signals to learning systems."""
        from urllib.parse import urlparse
        domain = urlparse(self.url).netloc.lower().removeprefix("www.") if self.url else ""
        company = self.job.get("company", "")
        title = self.job.get("title", "")
        platform = self.job.get("platform", "")

        # 1. GotchasDB — record domain-specific failure
        try:
            from jobpulse.form_engine.gotchas import GotchasDB
            gotchas = GotchasDB()
            problem = "expired_job" if expired else "navigation_failure"
            gotchas.store(
                domain=domain,
                selector_pattern=f"_failure:{problem}",
                problem=f"{error} | {title} @ {company}",
                solution="Mark as expired" if expired else "Investigate page structure",
            )
        except Exception as exc:
            logger.debug("_record_failure_learning: GotchasDB failed: %s", exc)

        # 2. OptimizationEngine — emit failure signal
        try:
            from shared.optimization import get_optimization_engine
            engine = get_optimization_engine()
            signal_type = "failure"
            engine.emit(
                signal_type=signal_type,
                source_loop="live_review_applicator",
                domain=domain,
                agent_name="application_orchestrator",
                payload={
                    "category": "expired_job" if expired else "navigation_failure",
                    "company": company,
                    "title": title,
                    "platform": platform,
                    "error": error,
                    "url": self.url,
                },
            )
        except Exception as exc:
            logger.debug("_record_failure_learning: OptimizationEngine failed: %s", exc)

        # 3. AgentPerformanceDB — record failed attempt
        try:
            from jobpulse.agent_performance import AgentPerformanceDB
            perf = AgentPerformanceDB()
            perf.record_session(
                company=company,
                role=title,
                platform=platform,
                url=self.url,
                success=False,
                notes=f"{'expired' if expired else 'failure'}: {error}",
            )
        except Exception as exc:
            logger.debug("_record_failure_learning: AgentPerformanceDB failed: %s", exc)

        logger.info(
            "OPRAL Learn: recorded failure for %s @ %s (expired=%s, domain=%s)",
            title, company, expired, domain,
        )

    def release(self) -> None:
        """Detach from Playwright while leaving the Chrome tab open."""
        driver = self._driver
        self._driver = None
        self._page = None
        if driver is None:
            return

        async def _detach() -> None:
            try:
                pw = getattr(driver, "_pw", None)
                if pw is not None:
                    await pw.stop()
            except Exception as exc:
                logger.debug("live_review_applicator: driver detach error: %s", exc)

        try:
            _run_async(_detach(), timeout=30)
        except Exception as exc:
            logger.debug("live_review_applicator: release dispatch failed: %s", exc)


def _run_session(session: LiveReviewSession) -> None:
    """Worker entrypoint for a single live review session."""
    registry_key = f"live_review_applicator.session.{session.session_id}"
    register_daemon_thread(
        registry_key,
        kind="live_review_session",
        thread_name=f"live-review-{session.session_id}",
        metadata={
            "component": "live_review_applicator",
            "job_id": session.job.get("job_id", ""),
        },
    )
    try:
        session.fill_and_request_approval()
        while True:
            action = session.wait_for_action()
            if action == "submit":
                outcome = session.run_submit_and_confirm()
                if outcome == "awaiting_human":
                    continue
                break
            if action == "cancel":
                session.run_reject()
                break
            break
    finally:
        session.release()
        with _active_lock:
            global _active_session
            if _active_session is session:
                _active_session = None
        _clear_active_review_file()
        stop_daemon_thread(registry_key)


def start_live_review(job: dict[str, Any], *, foreground: bool = False) -> dict[str, Any]:
    """Start a single live review session if none is already active.

    When ``foreground=True`` the session runs in the calling thread (blocks
    until complete).  Use this from Claude Code / one-shot scripts where a
    daemon thread would die with the process.
    """
    global _active_session
    with _active_lock:
        if _active_session is not None:
            current = _active_session
            return {
                "started": False,
                "message": (
                    "Another application is already open for review: "
                    f"{current.job.get('title')} @ {current.job.get('company')}. "
                    "Reply yes/no there before starting the next one."
                ),
            }

        session = LiveReviewSession(job)
        _active_session = session
        session._persist_state("starting")

    from jobpulse.job_db import JobDB

    try:
        JobDB().update_status(job.get("job_id", ""), "Reviewing")
    except Exception as exc:
        logger.warning("live_review_applicator: failed to mark Reviewing: %s", exc)

    if foreground:
        _run_session(session)
        return {
            "started": True,
            "session_id": session.session_id,
            "message": (
                f"Completed live application review for {job.get('title')} @ "
                f"{job.get('company')} (foreground mode)."
            ),
        }

    thread = threading.Thread(
        target=_run_session,
        args=(session,),
        daemon=True,
        name=f"live-review-{session.session_id}",
    )
    thread.start()
    return {
        "started": True,
        "session_id": session.session_id,
        "message": (
            f"Starting live application review for {job.get('title')} @ "
            f"{job.get('company')}. I'll stop at submit and wait for your yes/no."
        ),
    }


def get_active_review() -> dict[str, Any] | None:
    """Return a summary of the currently active live review session, if any."""
    with _active_lock:
        session = _active_session
        if session is None:
            persisted = _load_persisted_review()
            if not persisted:
                return None
            job = persisted.get("job") or {}
            return {
                "session_id": str(persisted.get("session_id") or ""),
                "job_id": job.get("job_id", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "platform": job.get("platform", "generic"),
                "url": persisted.get("url") or job.get("url", ""),
            }
        return {
            "session_id": session.session_id,
            "job_id": session.job.get("job_id", ""),
            "title": session.job.get("title", ""),
            "company": session.job.get("company", ""),
            "platform": session.job.get("platform", "generic"),
            "url": session.url,
        }


def _run_resumed_action(session: LiveReviewSession, action: str, page_url: str | None) -> None:
    """Resume a persisted live-review action in a fresh background thread."""
    registry_key = f"live_review_applicator.resume.{session.session_id}"
    register_daemon_thread(
        registry_key,
        kind="live_review_resume",
        thread_name=f"live-review-resume-{session.session_id}",
        metadata={
            "component": "live_review_applicator",
            "job_id": session.job.get("job_id", ""),
            "action": action,
        },
    )
    try:
        if action == "submit":
            _run_async(session._attach_to_existing_page_async(page_url), timeout=60)
            session.run_submit_and_confirm()
        else:
            session.run_reject()
    except Exception as exc:
        logger.error("live_review_applicator: resume action failed: %s", exc)
        session._restore_pending_status()
        send_telegram(
            f"❌ Could not resume live review for {session.job.get('title')} @ "
            f"{session.job.get('company')}:\n{exc}",
            chat_id=TELEGRAM_CHAT_ID,
        )
    finally:
        session.release()
        with _active_lock:
            global _active_session
            if _active_session is session:
                _active_session = None
        _clear_active_review_file()
        stop_daemon_thread(registry_key)


def resume_persisted_review_action(context: dict[str, Any], approved: bool) -> str:
    """Resume a live-review approval after a daemon restart."""
    payload = _load_persisted_review()
    if not payload:
        return "The previous live review session is no longer available."

    expected_session_id = str(context.get("session_id") or "")
    persisted_session_id = str(payload.get("session_id") or "")
    if expected_session_id and persisted_session_id and expected_session_id != persisted_session_id:
        return "The pending approval no longer matches the active live review."

    session = LiveReviewSession.from_persisted(payload)
    page_url = str(payload.get("approval_page_url") or payload.get("url") or session.url)

    with _active_lock:
        global _active_session
        _active_session = session

    if approved:
        thread = threading.Thread(
            target=_run_resumed_action,
            args=(session, "submit", page_url),
            daemon=True,
            name=f"live-review-resume-{session.session_id}",
        )
        thread.start()
        return (
            f"Submitting {session.job.get('title')} @ {session.job.get('company')} now. "
            "I'll message you again when the submission finishes."
        )

    thread = threading.Thread(
        target=_run_resumed_action,
        args=(session, "cancel", page_url),
        daemon=True,
        name=f"live-review-cancel-{session.session_id}",
    )
    thread.start()
    return (
        f"Cancelled submission for {session.job.get('title')} @ {session.job.get('company')}. "
        "The job is back in the pending review list."
    )
