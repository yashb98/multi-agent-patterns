"""S3 navigation audit guards.

- B-1: `_phase_plan` must NOT call the broken cognitive-engine block —
  only emit a structured stuck-state advisory.
- B-2: `handle_email_verification` must run gmail polling on a worker
  thread so the asyncio event loop continues to make progress while
  Gmail is being polled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jobpulse.application_orchestrator_pkg._auth import AuthHandler
from jobpulse.application_orchestrator_pkg._navigator import (
    FormNavigator, StepContext, TabState,
)
from jobpulse.page_analysis.page_reasoner import PageAction


def _stuck_action() -> PageAction:
    return PageAction(
        page_understanding="loop",
        action="click_element",
        target_text="Apply",
        reasoning="repeat",
        confidence=0.2,  # below 0.3 to satisfy the stuck-state trigger
        page_type="job_description",
    )


def _navigator(orch=None) -> FormNavigator:
    orch = orch or MagicMock()
    orch.driver = MagicMock()
    auth = MagicMock()
    return FormNavigator(orch, auth)


class TestB1NoCognitiveCoroutineLeak:
    """The pre-fix code did `engine.think(...)` (async) without await,
    then called `.get(...)` on the resulting coroutine — AttributeError.
    Guard: stuck-state branch must not raise and must not import the
    cognitive engine on the hot path."""

    def test_phase_plan_stuck_state_does_not_call_cognitive_engine(
        self, caplog,
    ):
        nav = _navigator()
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "page_text_preview": "stuck",
                "fields": [],
                "buttons": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            dom_confidence=0.4,
        )

        # >=2 states with count >=2 satisfies the trigger
        visited = {"a:click_element": 2, "b:fill_form": 2}

        action = _stuck_action()
        with patch(
            "jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner",
        ) as get_pr, patch(
            "shared.cognitive.get_cognitive_engine",
        ) as get_cog:
            get_pr.return_value.reason_sync = MagicMock(return_value=action)
            with caplog.at_level(logging.INFO):
                ctx = nav._phase_plan(ctx, visited, wall_bypass_attempts=0)

        # Cognitive engine MUST NOT be reached now — the dead block was
        # removed in S3 audit. If a future revival re-imports it, this
        # guard fires.
        assert not get_cog.called, (
            "cognitive engine should not be called on stuck state until a "
            "ThinkResult→PageAction translator is wired"
        )
        assert ctx.planned_action is action
        # Confirm the structured stuck-state advisory log line is emitted.
        assert any(
            "stuck" in rec.message.lower()
            and "cognitive escalation is not wired" in rec.message.lower()
            for rec in caplog.records
        ), "expected stuck-state advisory log"

    def test_phase_plan_stuck_state_does_not_raise_on_old_path(self):
        """Regression for the AttributeError on coroutine.get(...).

        Even if some downstream code does `engine.think(...)` again, the
        navigator must not propagate `AttributeError` out of `_phase_plan`.
        """
        nav = _navigator()
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "page_text_preview": "stuck",
                "fields": [],
                "buttons": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            dom_confidence=0.4,
        )
        visited = {"a:click_element": 2, "b:fill_form": 2}

        with patch(
            "jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner",
        ) as get_pr:
            get_pr.return_value.reason_sync = MagicMock(
                return_value=_stuck_action(),
            )
            # Should not raise.
            nav._phase_plan(ctx, visited, wall_bypass_attempts=0)


class TestB2EmailVerificationDoesNotBlockEventLoop:
    """`gmail.wait_for_verification` is sync and calls `time.sleep`. If
    we await its return directly, the event loop stalls. The fix wraps
    it in `asyncio.to_thread`. This test proves another coroutine
    continues to run while wait_for_verification is sleeping.
    """

    @pytest.mark.asyncio
    async def test_concurrent_coroutine_progresses_during_polling(self):
        """Spawn handle_email_verification + a tick coroutine. The tick
        must increment several times during the simulated 1.5 s sleep
        inside gmail.wait_for_verification.
        """
        orch = MagicMock()
        orch.driver = AsyncMock()
        orch.driver.page = AsyncMock()

        # The fake gmail polling sleeps 1.5 s on the calling thread.
        sleep_seconds = 1.5

        def fake_wait_for_verification(domain):
            # This is intentionally a synchronous time.sleep — the whole
            # point of the fix is that this should run on a worker
            # thread so the event loop keeps ticking.
            time.sleep(sleep_seconds)
            return f"https://{domain}/verify?token=abc"

        orch.gmail = MagicMock()
        orch.gmail.wait_for_verification = fake_wait_for_verification
        orch.driver.navigate = AsyncMock()
        orch.driver.get_snapshot = AsyncMock(return_value={
            "url": "https://example.com/applied",
            "fields": [], "buttons": [],
            "page_text_preview": "ok",
            "has_dialog": False,
        })
        orch.accounts = MagicMock()

        handler = AuthHandler(orch)

        ticks = 0

        async def tick():
            nonlocal ticks
            # Tick every 100 ms. If the loop is blocked, this won't run
            # until after handle_email_verification returns.
            for _ in range(20):
                await asyncio.sleep(0.1)
                ticks += 1

        snap = {"url": "https://example.com/signup",
                "page_text_preview": "verify your email", "has_dialog": False,
                "fields": [], "buttons": []}

        # Run both. handle_email_verification must yield to the loop
        # while gmail polling sleeps on a worker thread.
        verify_task = asyncio.create_task(
            handler.handle_email_verification(snap, "generic", "https://example.com/return"),
        )
        tick_task = asyncio.create_task(tick())

        # Wait for the verification path to finish.
        await verify_task
        # Cancel the tick early so the test doesn't take 2 s.
        tick_task.cancel()
        try:
            await tick_task
        except asyncio.CancelledError:
            pass

        # During the 1.5 s sync gmail sleep, the tick coroutine should
        # have completed at least ~10 iterations (every 100 ms). With
        # the old code (no `to_thread`), the loop would have been
        # blocked and ticks would have been ~0 until verify finishes.
        assert ticks >= 8, (
            f"event loop appears blocked: only {ticks} ticks during "
            f"{sleep_seconds}s sync gmail wait — `to_thread` likely missing"
        )
