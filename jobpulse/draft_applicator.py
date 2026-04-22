"""Draft Applicator — human-in-the-loop job application flow.

Mandatory invariant: AI agents NEVER submit a job application without explicit
human approval. Every draft is filled in `dry_run=True` mode only. The
NativeFormFiller stops at the Submit button; the tab is left live so the user
can inspect the filled form in Chrome. Submission happens only when the user
replies `submit <draft_id>` via Telegram.

Architecture (replaces the earlier thread-per-job + disconnected-submit design):

1. One persistent asyncio event loop runs in a daemon thread. All Playwright
   work runs on that loop, so the same `PlaywrightDriver` / `Page` survives
   between the fill call and the submit call.
2. One sequential worker thread pulls jobs off `_PENDING_QUEUE` and processes
   them one at a time. Chrome is a single resource — we never drive it
   concurrently.
3. Per job, the worker opens a `DraftSession`, fills the form via the regular
   ApplicationOrchestrator with dry_run=True (so NativeFormFiller stops at the
   submit button), stores the live session, pings Telegram, and blocks on a
   `threading.Event` until the user approves or rejects.
4. On "submit" the worker clicks Submit on the *same* page via
   `NativeFormFiller._click_navigation(dry_run=False)` — no second CDP
   connection, no duplicated 60 lines of submit-button CSS.
5. On success, the worker calls `confirm_application()` so the learning
   pipeline runs (quota, post-apply hook, correction capture, Drive, Notion).
6. On "skip" or error, the tab is closed and we move to the next job.
"""

from __future__ import annotations

import asyncio
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from shared.logging_config import get_logger

from jobpulse.applicator import confirm_application, prepare_application_inputs
from jobpulse.config import (
    APPLICANT_PROFILE as PROFILE,
    DATA_DIR,
    TELEGRAM_CHAT_ID,
)
from jobpulse.draft_queue import DraftQueue
from jobpulse.telegram_agent import send_message as send_telegram

logger = get_logger(__name__)

SCREENSHOT_DIR = DATA_DIR / "draft_screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ── Persistent background loop (survives across fill → review → submit) ──

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Start (once) and return the persistent background event loop."""
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running():
            return _loop
        _loop = asyncio.new_event_loop()

        def _run() -> None:
            assert _loop is not None
            asyncio.set_event_loop(_loop)
            _loop.run_forever()

        _loop_thread = threading.Thread(
            target=_run, daemon=True, name="draft-applicator-loop",
        )
        _loop_thread.start()
        return _loop


def _run_async(coro: Any, timeout: float | None = None) -> Any:
    """Schedule *coro* on the background loop and block until it completes."""
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


# ── Sequential worker (one draft at a time) ──

_PENDING_QUEUE: "Queue[dict[str, Any]]" = Queue()
_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()

# The draft currently awaiting user approval (at most one).
_active_session: "DraftSession | None" = None
_active_lock = threading.Lock()


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop, daemon=True, name="draft-applicator-worker",
        )
        _worker_thread.start()


def _worker_loop() -> None:
    """Pull jobs off the queue, fill + wait-for-approval, one at a time."""
    global _active_session
    while True:
        try:
            job = _PENDING_QUEUE.get(timeout=60.0)
        except Empty:
            continue

        session: DraftSession | None = None
        try:
            session = DraftSession(job)
            with _active_lock:
                _active_session = session

            session.fill_and_notify()

            action = session.wait_for_action()
            if action == "submit":
                session.run_submit_and_confirm()
            else:
                session.run_reject()
        except Exception as exc:
            logger.exception("draft_applicator: worker error: %s", exc)
            if session is not None:
                try:
                    session.mark_failed(str(exc))
                except Exception:
                    pass
        finally:
            if session is not None:
                session.release()
            with _active_lock:
                if _active_session is session:
                    _active_session = None


# ── macOS helper ──

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
                ["osascript", "-e", script], capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                return
        subprocess.run(
            ["osascript", "-e", 'tell application "Google Chrome" to activate'],
            capture_output=True, timeout=5, check=False,
        )
    except FileNotFoundError:
        logger.debug("osascript not available — cannot focus Chrome")
    except Exception as exc:
        logger.debug("Failed to focus Chrome: %s", exc)


# ── Draft session ──

class DraftSession:
    """One live draft: owns the PlaywrightDriver + Page for its lifetime.

    Lifecycle: new → fill_and_notify() → wait_for_action() →
    run_submit_and_confirm() OR run_reject() → release().
    """

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        self.url: str = job["url"]
        self.queue = DraftQueue()
        self.draft_id: str = self.queue.create_draft(
            job_id=job.get("job_id", ""),
            url=self.url,
            platform=job.get("platform", "generic"),
            company=job.get("company", ""),
            title=job.get("title", ""),
        )

        prep = prepare_application_inputs(
            url=self.url,
            ats_platform=job.get("ats_platform") or job.get("platform"),
            custom_answers=job.get("custom_answers"),
            job_context={
                "job_id": job.get("job_id", ""),
                "title": job.get("title", ""),
                "company": job.get("company", ""),
                "url": self.url,
            },
            cover_letter_path=(
                Path(job["cover_letter_path"]) if job.get("cover_letter_path") else None
            ),
        )
        self.ats_platform: str | None = prep["ats_platform"]
        self.platform_key: str = prep["platform_key"]
        self.merged_answers: dict = prep["merged_answers"]
        self.cover_letter_path: Path | None = prep["cover_letter_path"]
        self.cv_path: Path | None = (
            Path(job["cv_path"]) if job.get("cv_path") else None
        )

        self._driver: Any | None = None
        self._page: Any | None = None
        self._fill_result: dict = {}
        self._agent_mapping: dict[str, str] = {}
        # Populated from the live page right before click-submit, so that
        # `final_mapping != agent_mapping` whenever the user manually edited
        # any field. This resurrects the correction-capture feedback loop.
        self._final_mapping: dict[str, str] = {}

        self._action: str | None = None
        self._action_event = threading.Event()
        self._submit_result: dict = {}
        self._submit_done = threading.Event()

    # Called by external threads (Telegram command handlers).
    def set_action(self, action: str) -> None:
        if self._action is None:
            self._action = action
            self._action_event.set()

    def wait_for_action(self, timeout: float = 24 * 3600) -> str:
        self._action_event.wait(timeout=timeout)
        return self._action or "skip"

    def wait_for_submit_result(self, timeout: float = 180.0) -> dict | None:
        if not self._submit_done.wait(timeout=timeout):
            return None
        return dict(self._submit_result)

    # ── Fill phase (dry_run=True — never submits) ──

    async def _fill_async(self) -> dict:
        from jobpulse.application_orchestrator import ApplicationOrchestrator
        from jobpulse.playwright_driver import PlaywrightDriver

        if self.cv_path is None or not self.cv_path.exists():
            raise FileNotFoundError(f"CV not found: {self.cv_path}")

        driver = PlaywrightDriver()
        await driver.connect()
        self._driver = driver
        self._page = driver.page

        orchestrator = ApplicationOrchestrator(driver=driver, engine="playwright")
        result = await orchestrator.apply(
            url=self.url,
            platform=self.platform_key,
            cv_path=self.cv_path,
            cover_letter_path=self.cover_letter_path,
            profile=PROFILE,
            custom_answers=self.merged_answers,
            overrides=None,
            dry_run=True,  # MANDATORY — AI never submits before human approval
        )
        return result

    def fill_and_notify(self) -> None:
        """Fill the form (dry_run) and send the review notification to Telegram."""
        logger.info(
            "draft_applicator: filling draft %s (%s @ %s)",
            self.draft_id, self.job.get("title"), self.job.get("company"),
        )
        try:
            result = _run_async(self._fill_async(), timeout=15 * 60)
        except Exception as exc:
            logger.error("draft_applicator: fill failed: %s", exc)
            self.queue.update_draft(self.draft_id, status="error", error_message=str(exc))
            send_telegram(
                f"❌ Failed to fill draft for {self.job.get('title')} "
                f"@ {self.job.get('company')}:\n{exc}",
                chat_id=TELEGRAM_CHAT_ID,
            )
            # Auto-skip so the worker moves to the next job.
            self.set_action("skip")
            return

        self._fill_result = result or {}
        self._agent_mapping = dict(self._fill_result.get("agent_mapping") or {})

        if not self._fill_result.get("success"):
            err = self._fill_result.get("error", "fill returned success=False")
            logger.warning("draft_applicator: fill did not reach submit page: %s", err)
            self.queue.update_draft(self.draft_id, status="error", error_message=err)
            send_telegram(
                f"❌ Could not reach the submit page for "
                f"{self.job.get('title')} @ {self.job.get('company')}:\n{err}",
                chat_id=TELEGRAM_CHAT_ID,
            )
            self.set_action("skip")
            return

        screenshot_path = self._capture_screenshot()
        self.queue.update_draft(
            draft_id=self.draft_id,
            status="filled",
            screenshot_path=screenshot_path,
            filled_fields=self._agent_mapping,
            form_pages=self._fill_result.get("pages_filled", 0),
        )

        _bring_chrome_to_front(url=self.url)
        self._send_review_notification(screenshot_path)

    def _capture_screenshot(self) -> str | None:
        async def _shot() -> str | None:
            if self._page is None:
                return None
            path = SCREENSHOT_DIR / (
                f"draft_{self.draft_id}_"
                f"{datetime.now(timezone.utc).strftime('%H%M%S')}.png"
            )
            try:
                await self._page.screenshot(path=str(path), full_page=True)
                return str(path)
            except Exception as exc:
                logger.warning("draft_applicator: screenshot failed: %s", exc)
                return None

        try:
            return _run_async(_shot(), timeout=30)
        except Exception as exc:
            logger.warning("draft_applicator: screenshot dispatch failed: %s", exc)
            return None

    def _send_review_notification(self, screenshot_path: str | None) -> None:
        lines = [
            "📝 Draft Ready for Review",
            "",
            f"Job: {self.job.get('title', 'unknown')}",
            f"Company: {self.job.get('company', 'unknown')}",
            f"Platform: {self.job.get('platform', 'generic')}",
        ]
        if self.job.get("ats_score"):
            lines.append(f"ATS Score: {self.job['ats_score']:.1f}%")
        lines.extend([
            "",
            "👀 Review the filled form in Chrome, then reply:",
            f"  submit {self.draft_id}   — click Submit on the form",
            f"  skip   {self.draft_id}   — close the tab",
        ])
        caption = "\n".join(lines)

        sent_photo = False
        if screenshot_path:
            try:
                from jobpulse.telegram_bots import send_jobs_photo
                sent_photo = send_jobs_photo(screenshot_path, caption=caption)
            except Exception as exc:
                logger.debug("draft_applicator: send_jobs_photo failed: %s", exc)
        if not sent_photo:
            if screenshot_path:
                caption += f"\n\nScreenshot: {screenshot_path}"
            send_telegram(caption, chat_id=TELEGRAM_CHAT_ID)

    # ── Submit phase (only after human approval) ──

    async def _capture_final_mapping_async(
        self, filler: Any,
    ) -> dict[str, str]:
        """Read live page values right before click-submit.

        Uses the same accessible-name logic as fill-time (`_get_accessible_name`)
        so labels match `agent_mapping` keys and
        `CorrectionCapture.record_corrections` can diff them. File inputs are
        skipped — they aren't correction-learnable at this layer.

        Never raises: if any per-field read fails we fall back to the agent
        mapping rather than poison the submit flow.
        """
        page = self._page
        if page is None:
            return dict(self._agent_mapping)

        final: dict[str, str] = {}

        async def _read(loc: Any, label: str, kind: str) -> None:
            if not label:
                return
            try:
                if kind in ("text", "textarea", "select", "combobox"):
                    final[label] = (await loc.input_value()) or ""
                elif kind == "checkbox":
                    final[label] = "true" if await loc.is_checked() else "false"
                elif kind == "radio_group":
                    # loc is the radiogroup; find the checked option's label.
                    selected = ""
                    for r in await loc.get_by_role("radio").all():
                        try:
                            if await r.is_checked():
                                selected = await filler._get_accessible_name(r)
                                break
                        except Exception:
                            continue
                    final[label] = selected
            except Exception as exc:
                logger.debug(
                    "draft_applicator: final-mapping read failed for %r: %s",
                    label, exc,
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
                "draft_applicator: final-mapping capture crashed: %s", exc,
            )
            return dict(self._agent_mapping)

        return final

    async def _click_submit_async(self) -> dict:
        from jobpulse.native_form_filler import NativeFormFiller

        assert self._page is not None, "draft session has no live page"
        filler = NativeFormFiller(page=self._page, driver=self._driver)

        # Capture what's actually on the page RIGHT BEFORE we click Submit.
        # Anything the user edited between fill-and-notify and submit shows up
        # here as a diff against `_agent_mapping`, which `confirm_application`
        # then forwards to `CorrectionCapture` as reinforcement signal.
        try:
            self._final_mapping = await self._capture_final_mapping_async(filler)
            delta = sum(
                1 for k, v in self._final_mapping.items()
                if self._agent_mapping.get(k, "") != v
            )
            logger.info(
                "draft_applicator: captured final_mapping (%d fields, %d edits)",
                len(self._final_mapping), delta,
            )
        except Exception as exc:
            logger.warning(
                "draft_applicator: final-mapping capture failed, falling back: %s", exc,
            )
            self._final_mapping = dict(self._agent_mapping)

        clicked = await filler._click_navigation(dry_run=False)
        # Give the page time to navigate / show confirmation.
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
            "thank", "success", "received", "confirmation",
            "application sent", "application submitted",
            "we've received", "we have received",
        )
        error_markers = ("error", "failed", "please correct", "required field")

        saw_success = any(m in final_url.lower() or m in page_text for m in success_markers)
        saw_error = any(m in page_text for m in error_markers) and not saw_success

        return {
            "clicked": clicked,
            "final_url": final_url,
            "saw_success": saw_success,
            "saw_error": saw_error,
        }

    def run_submit_and_confirm(self) -> None:
        """Click Submit on the live page, then run the full learning pipeline."""
        logger.info("draft_applicator: submitting draft %s", self.draft_id)
        self.queue.update_draft(self.draft_id, status="pending_review")

        result: dict[str, Any] = {"success": False, "error": "submit not attempted"}
        try:
            click = _run_async(self._click_submit_async(), timeout=120)
            if click.get("clicked") not in ("submitted", "next"):
                result = {
                    "success": False,
                    "error": (
                        "Could not find the Submit button on the live page. "
                        "The form may have changed. Submit manually in Chrome."
                    ),
                    "final_url": click.get("final_url"),
                }
            elif click.get("saw_error"):
                result = {
                    "success": False,
                    "error": "Form validation failed after submit — see Chrome.",
                    "final_url": click.get("final_url"),
                }
            else:
                result = {"success": True, "final_url": click.get("final_url")}

            if result["success"]:
                self.queue.mark_submitted(self.draft_id)
                self._run_confirm_application()
            else:
                self.queue.update_draft(
                    self.draft_id, status="error", error_message=str(result.get("error")),
                )
        except Exception as exc:
            logger.exception("draft_applicator: submit crashed: %s", exc)
            result = {"success": False, "error": str(exc)}
            self.queue.update_draft(
                self.draft_id, status="error", error_message=str(exc),
            )
        finally:
            self._submit_result = result
            self._submit_done.set()
            self._send_post_submit_notification(result)

    def _run_confirm_application(self) -> None:
        """Trigger quota recording, post-apply hook, correction capture, etc."""
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
            )
        except Exception as exc:
            logger.warning("draft_applicator: confirm_application failed: %s", exc)

    def _send_post_submit_notification(self, result: dict) -> None:
        if result.get("success"):
            text = (
                f"✅ Submitted: {self.job.get('title')} @ {self.job.get('company')}\n"
                f"Final URL: {result.get('final_url', 'N/A')}"
            )
        else:
            text = (
                f"❌ Submit failed for {self.job.get('title')} @ "
                f"{self.job.get('company')}:\n{result.get('error', 'unknown error')}"
            )
        send_telegram(text, chat_id=TELEGRAM_CHAT_ID)

    # ── Reject / release ──

    def run_reject(self) -> None:
        self.queue.mark_rejected(self.draft_id)
        self._submit_result = {"success": False, "rejected": True}
        self._submit_done.set()
        send_telegram(
            f"⏭  Skipped: {self.job.get('title')} @ {self.job.get('company')}",
            chat_id=TELEGRAM_CHAT_ID,
        )

    def mark_failed(self, message: str) -> None:
        self.queue.update_draft(self.draft_id, status="error", error_message=message)
        self._submit_result = {"success": False, "error": message}
        self._submit_done.set()

    def release(self) -> None:
        """Close the live Playwright driver. Idempotent."""
        driver = self._driver
        self._driver = None
        self._page = None
        if driver is None:
            return

        async def _close() -> None:
            try:
                await driver.close()
            except Exception as exc:
                logger.debug("draft_applicator: driver.close error: %s", exc)

        try:
            _run_async(_close(), timeout=30)
        except Exception as exc:
            logger.debug("draft_applicator: release dispatch failed: %s", exc)


# ── Public API (used by dispatcher + job_autopilot) ──

def queue_drafts(jobs: list[dict[str, Any]]) -> int:
    """Enqueue *jobs* for sequential human-in-the-loop drafting.

    Returns the number of jobs enqueued. The worker runs them one at a time,
    filling the form (dry_run) and blocking for the user's `submit`/`skip`
    reply before moving on.
    """
    _ensure_worker()
    count = 0
    for job in jobs:
        if not job.get("url"):
            logger.warning("draft_applicator: dropping job with no URL: %s", job)
            continue
        _PENDING_QUEUE.put(job)
        count += 1
    logger.info("draft_applicator: enqueued %d draft(s)", count)
    return count


def create_draft_for_job(job: dict[str, Any]) -> str:
    """Enqueue a single job and return a placeholder id.

    Kept for backward compatibility with callers that still expect a one-shot
    API. The real draft_id is assigned by the worker when filling starts and
    is reported via Telegram.
    """
    queue_drafts([job])
    return "queued"


def submit_draft(draft_id: str) -> dict[str, Any]:
    """Approve the currently-active draft and submit it.

    Clicks Submit on the *same* live page that was filled earlier; no new CDP
    connection is opened. Runs `confirm_application()` on success.
    """
    with _active_lock:
        session = _active_session
    if session is None or session.draft_id != draft_id:
        queue = DraftQueue()
        draft = queue.get_draft(draft_id)
        if draft and draft.get("status") == "submitted":
            return {"success": False, "error": f"Draft {draft_id} already submitted."}
        return {
            "success": False,
            "error": (
                f"No live session for draft {draft_id}. "
                "The tab may have been closed or the daemon restarted — submit manually in Chrome."
            ),
        }

    session.set_action("submit")
    result = session.wait_for_submit_result(timeout=180)
    if result is None:
        return {"success": False, "error": "Submit timed out after 180s."}
    return result


def reject_draft(draft_id: str) -> str:
    """Skip the currently-active draft and close its tab."""
    with _active_lock:
        session = _active_session
    if session is None or session.draft_id != draft_id:
        queue = DraftQueue()
        if queue.mark_rejected(draft_id):
            return f"⏭  Marked {draft_id} as rejected (no live session)."
        return f"Draft {draft_id} not found."
    session.set_action("skip")
    # Wait briefly so the worker's reject path runs before we return.
    session.wait_for_submit_result(timeout=30)
    return f"⏭  Skipped: {session.job.get('title')} @ {session.job.get('company')}"


def show_drafts() -> str:
    """Return a formatted list of pending drafts for Telegram."""
    queue = DraftQueue()
    drafts = queue.get_pending_drafts()
    if not drafts:
        return "No drafts pending review."

    lines = [f"📝 {len(drafts)} draft(s) awaiting review:\n"]
    for i, d in enumerate(drafts, 1):
        lines.append(f"{i}. {d['title']} — {d['company']}")
        lines.append(f"   ID: {d['draft_id']} | Platform: {d['platform']}")
        if d.get("screenshot_path"):
            lines.append(f"   📎 {d['screenshot_path']}")
        lines.append(f"   submit {d['draft_id']}  or  skip {d['draft_id']}")
        lines.append("")

    return "\n".join(lines)


def expire_old_drafts() -> int:
    """Expire drafts older than 24 hours (called by cron)."""
    queue = DraftQueue()
    count = queue.expire_old_drafts()
    if count:
        logger.info("draft_applicator: expired %d old drafts", count)
    return count
