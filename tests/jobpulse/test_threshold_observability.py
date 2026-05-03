"""Verify that each magic-number threshold emits a structured THRESHOLD_OBS log line.

Six thresholds covered:
  1. vision_gate        — _navigator.py, confidence < 0.7
  2. field_count_guard  — page_reasoner.py, coverage < 0.8
  3. synthesis          — _strategy_synthesis.py, apply_count < 3
  4a. pre_submit_review              — pre_submit_gate.py review()
  4b. pre_submit_semantic_correctness — pre_submit_gate.py check_semantic_correctness()
  5. readback_retry     — action_executor.py, 200ms sleep on first-verify fail
  6. substring_guard    — action_executor.py, 3-char gate in _verify_fill
"""
from __future__ import annotations

import logging
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helper: make a minimal PageAction
# ---------------------------------------------------------------------------


def _page_action(**kwargs):
    from jobpulse.page_analysis.page_reasoner import PageAction
    defaults = dict(
        page_understanding="t",
        action="fill_and_advance",
        target_text="",
        reasoning="t",
        confidence=0.9,
        page_type="application_form",
        field_fills=[],
        advance_button="Submit",
        overlays_to_dismiss=[],
        expected_outcome="url_changes",
    )
    defaults.update(kwargs)
    return PageAction(**defaults)


# ---------------------------------------------------------------------------
# 1. Vision gate — fired (confidence < 0.7) and skipped (confidence >= 0.7)
# ---------------------------------------------------------------------------


class TestVisionGateLog:
    """Log emitted immediately before the if action.confidence < 0.7 branch."""

    def _make_navigator(self):
        """Build a FormNavigator with a fully-mocked orch/driver."""
        from jobpulse.application_orchestrator_pkg._navigator import FormNavigator

        # Mock driver with all async attributes needed by _phase_act.
        driver = MagicMock()
        driver.page = AsyncMock()
        driver.page.url = "https://example.com/apply"
        driver.page.screenshot = AsyncMock(return_value=b"fake_png")
        driver.intelligence = None
        driver.get_snapshot = AsyncMock(return_value={
            "url": "https://example.com/applied",
            "content_hash": "post_hash",
            "has_dialog": False,
            "fields": [],
            "buttons": [],
        })

        orch = MagicMock()
        orch.driver = driver
        orch.analyzer = MagicMock()
        orch.cookie_dismisser = MagicMock()
        orch.sso = MagicMock()
        orch.learner = MagicMock()

        nav = FormNavigator.__new__(FormNavigator)
        nav._orch = orch
        nav.auth = MagicMock()
        nav._classifier = MagicMock()
        return nav

    def _make_ctx(self, action):
        """Build a minimal StepContext with a planned action."""
        from jobpulse.application_orchestrator_pkg._navigator import StepContext, TabState
        return StepContext(
            snapshot={"url": "https://example.com/apply", "has_dialog": False},
            url="https://example.com/apply",
            tab_state=TabState.NORMAL,
            planned_action=action,
        )

    @pytest.mark.asyncio
    async def test_vision_gate_log_fires_low_confidence(self, monkeypatch, caplog):
        """confidence < 0.7 → decision=fired in log."""
        from jobpulse.navigation.action_executor import ExecutorResult
        from jobpulse.application_orchestrator_pkg._navigator import ActionVerification

        nav = self._make_navigator()

        # Patch _verify_action to return a minimal verification (no ghost click).
        async def _fake_verify(*args, **kwargs):
            return ActionVerification(
                pre_url="https://example.com/apply",
                pre_hash="pre",
                pre_dialog=False,
                post_url="https://example.com/apply",
                post_hash="pre",
                post_dialog=False,
                ghost_click=False,
                expected_outcome_met=True,
            )
        nav._verify_action = _fake_verify
        nav._check_expected_outcome = lambda action, v: v

        # Patch NavigationActionExecutor so no real Playwright calls happen.
        monkeypatch.setattr(
            "jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor",
            MagicMock(return_value=MagicMock(execute=AsyncMock(return_value=ExecutorResult()))),
        )
        # emit_fill_failures is imported locally inside _phase_act — patch at source.
        monkeypatch.setattr(
            "jobpulse.navigation.action_executor.emit_fill_failures",
            MagicMock(),
        )
        # Patch vision tier so it returns fast.
        monkeypatch.setattr(
            "jobpulse.vision_tier.classify_page_type_from_screenshot",
            AsyncMock(return_value="unknown"),
        )
        # Patch PROFILE import inside _phase_act.
        monkeypatch.setattr(
            "jobpulse.applicator.PROFILE", {}, raising=False,
        )

        action = _page_action(confidence=0.5, action="fill_and_advance")
        ctx = self._make_ctx(action)

        with caplog.at_level(logging.INFO, logger="jobpulse.application_orchestrator_pkg._navigator"):
            try:
                await nav._phase_act(ctx, platform="generic", steps=[], wall_bypass_attempts=0)
            except Exception:
                pass  # tolerate any downstream failures — log fires before them

        obs_records = [r for r in caplog.records if "THRESHOLD_OBS: vision_gate" in r.message]
        assert obs_records, "vision_gate THRESHOLD_OBS log not emitted"
        obs = obs_records[0]
        assert "threshold=0.7" in obs.message
        assert "decision=fired" in obs.message

    @pytest.mark.asyncio
    async def test_vision_gate_log_skipped_high_confidence(self, monkeypatch, caplog):
        """confidence >= 0.7 → decision=skipped in log."""
        from jobpulse.navigation.action_executor import ExecutorResult
        from jobpulse.application_orchestrator_pkg._navigator import ActionVerification

        nav = self._make_navigator()

        async def _fake_verify(*args, **kwargs):
            return ActionVerification(
                pre_url="https://example.com/apply",
                pre_hash="pre",
                pre_dialog=False,
                post_url="https://example.com/apply",
                post_hash="pre",
                post_dialog=False,
                ghost_click=False,
                expected_outcome_met=True,
            )
        nav._verify_action = _fake_verify
        nav._check_expected_outcome = lambda action, v: v

        monkeypatch.setattr(
            "jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor",
            MagicMock(return_value=MagicMock(execute=AsyncMock(return_value=ExecutorResult()))),
        )
        monkeypatch.setattr(
            "jobpulse.navigation.action_executor.emit_fill_failures",
            MagicMock(),
        )
        monkeypatch.setattr(
            "jobpulse.applicator.PROFILE", {}, raising=False,
        )

        action = _page_action(confidence=0.9, action="fill_and_advance")
        ctx = self._make_ctx(action)

        with caplog.at_level(logging.INFO, logger="jobpulse.application_orchestrator_pkg._navigator"):
            try:
                await nav._phase_act(ctx, platform="generic", steps=[], wall_bypass_attempts=0)
            except Exception:
                pass

        obs_records = [r for r in caplog.records if "THRESHOLD_OBS: vision_gate" in r.message]
        assert obs_records, "vision_gate THRESHOLD_OBS log not emitted"
        assert "decision=skipped" in obs_records[0].message


# ---------------------------------------------------------------------------
# 2. Field-count guard — log always fires for fill-related actions
# ---------------------------------------------------------------------------


class TestFieldCountGuardLog:
    def test_log_fires_on_low_coverage(self, tmp_path, caplog):
        """Coverage < 0.8 → decision=lowered_confidence in log."""
        from jobpulse.page_analysis.page_reasoner import PageReasoner

        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
            {"label": "Phone", "input_type": "tel", "required": True},
        ]
        action = _page_action(
            field_fills=[{"label": "Email", "value": "x@y.com", "method": "fill"}],
        )

        with caplog.at_level(logging.INFO, logger="jobpulse.page_analysis.page_reasoner"):
            pr._apply_field_count_guard(action, snap_fields)

        assert any(
            "THRESHOLD_OBS: field_count_guard" in r.message for r in caplog.records
        ), "field_count_guard THRESHOLD_OBS log not emitted"
        obs = next(r for r in caplog.records if "THRESHOLD_OBS: field_count_guard" in r.message)
        assert "threshold=0.8" in obs.message
        assert "decision=lowered_confidence" in obs.message

    def test_log_fires_on_full_coverage(self, tmp_path, caplog):
        """Coverage >= 0.8 → decision=passed in log."""
        from jobpulse.page_analysis.page_reasoner import PageReasoner

        pr = PageReasoner(db_path=str(tmp_path / "rc.db"))
        snap_fields = [
            {"label": "First name", "input_type": "text", "required": True},
            {"label": "Email", "input_type": "email", "required": True},
        ]
        action = _page_action(
            field_fills=[
                {"label": "First name", "value": "Ada", "method": "fill"},
                {"label": "Email", "value": "a@b.com", "method": "fill"},
            ],
        )

        with caplog.at_level(logging.INFO, logger="jobpulse.page_analysis.page_reasoner"):
            pr._apply_field_count_guard(action, snap_fields)

        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: field_count_guard" in r.message),
            None,
        )
        assert obs is not None
        assert "decision=passed" in obs.message


# ---------------------------------------------------------------------------
# 3. Synthesis threshold — log fires for both branches
# ---------------------------------------------------------------------------


class TestSynthesisThresholdLog:
    def _make_fe_db(self, apply_count: int):
        """Minimal fake FormExperienceDB."""
        db = MagicMock()
        db.lookup = MagicMock(return_value={"apply_count": apply_count})
        return db

    def test_log_skipped_below_threshold(self, monkeypatch, caplog):
        """apply_count=1 < 3 → decision=skipped."""
        monkeypatch.setattr(
            "jobpulse.ats_adapters._strategy_synthesis._get_fe_db",
            lambda: self._make_fe_db(1),
        )
        from jobpulse.ats_adapters._strategy_synthesis import synthesize_strategy_for_domain

        with caplog.at_level(logging.INFO, logger="jobpulse.ats_adapters._strategy_synthesis"):
            result = synthesize_strategy_for_domain("greenhouse.io")

        assert result is None
        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: synthesis" in r.message),
            None,
        )
        assert obs is not None, "synthesis THRESHOLD_OBS log not emitted"
        assert "threshold=3" in obs.message
        assert "decision=skipped" in obs.message

    def test_log_synthesized_at_or_above_threshold(self, monkeypatch, caplog):
        """apply_count=5 >= 3 → decision=synthesized."""
        monkeypatch.setattr(
            "jobpulse.ats_adapters._strategy_synthesis._get_fe_db",
            lambda: self._make_fe_db(5),
        )
        from jobpulse.ats_adapters._strategy_synthesis import synthesize_strategy_for_domain

        with caplog.at_level(logging.INFO, logger="jobpulse.ats_adapters._strategy_synthesis"):
            result = synthesize_strategy_for_domain("greenhouse.io")

        assert result is not None
        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: synthesis" in r.message),
            None,
        )
        assert obs is not None
        assert "decision=synthesized" in obs.message


# ---------------------------------------------------------------------------
# 4. PreSubmitGate — review() and check_semantic_correctness()
# ---------------------------------------------------------------------------


class TestPreSubmitGateLog:
    def _make_company(self):
        from jobpulse.perplexity import CompanyResearch
        return CompanyResearch(
            company="Acme", description="startup", tech_stack=["Python"],
        )

    @patch("shared.agents.cognitive_llm_call")
    def test_review_log_fires_passed(self, mock_llm, caplog):
        mock_llm.return_value = json.dumps(
            {"score": 8.0, "weaknesses": [], "suggestions": []}
        )
        from jobpulse.pre_submit_gate import PreSubmitGate
        gate = PreSubmitGate()

        with caplog.at_level(logging.INFO, logger="jobpulse.pre_submit_gate"):
            result = gate.review(
                filled_answers={"Why us?": "I love NLP."},
                jd_keywords=["NLP"],
                company_research=self._make_company(),
            )

        assert result.passed is True
        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: pre_submit_review" in r.message),
            None,
        )
        assert obs is not None, "pre_submit_review THRESHOLD_OBS log not emitted"
        assert "threshold=7.0" in obs.message
        assert "decision=passed" in obs.message

    @patch("shared.agents.cognitive_llm_call")
    def test_review_log_fires_blocked(self, mock_llm, caplog):
        mock_llm.return_value = json.dumps(
            {"score": 4.0, "weaknesses": ["generic"], "suggestions": []}
        )
        from jobpulse.pre_submit_gate import PreSubmitGate
        gate = PreSubmitGate()

        with caplog.at_level(logging.INFO, logger="jobpulse.pre_submit_gate"):
            result = gate.review(
                filled_answers={"Why us?": "I want a job."},
                jd_keywords=["NLP"],
                company_research=self._make_company(),
            )

        assert result.passed is False
        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: pre_submit_review" in r.message),
            None,
        )
        assert obs is not None
        assert "decision=blocked" in obs.message

    def test_semantic_correctness_log_fires_passed(self, caplog):
        """No LLM required — run_llm_judge=False makes it purely deterministic."""
        from jobpulse.pre_submit_gate import PreSubmitGate
        gate = PreSubmitGate()

        with caplog.at_level(logging.INFO, logger="jobpulse.pre_submit_gate"):
            result = gate.check_semantic_correctness(
                filled_answers={"Name": "Ada", "Email": "ada@example.com"},
                run_llm_judge=False,
            )

        assert result.passed is True
        obs = next(
            (r for r in caplog.records
             if "THRESHOLD_OBS: pre_submit_semantic_correctness" in r.message),
            None,
        )
        assert obs is not None, "pre_submit_semantic_correctness THRESHOLD_OBS log not emitted"
        assert "threshold=7.0" in obs.message
        assert "decision=passed" in obs.message

    def test_semantic_correctness_log_fires_blocked(self, caplog):
        """Five issues at 2pts each → score=0.0 → blocked."""
        from jobpulse.pre_submit_gate import PreSubmitGate
        gate = PreSubmitGate()

        # Six placeholder values each cost 2 pts → score = max(0, 10-12) = 0.0
        bad_answers = {
            f"Field{i}": "TODO" for i in range(6)
        }

        with caplog.at_level(logging.INFO, logger="jobpulse.pre_submit_gate"):
            result = gate.check_semantic_correctness(
                filled_answers=bad_answers,
                run_llm_judge=False,
            )

        assert result.passed is False
        obs = next(
            (r for r in caplog.records
             if "THRESHOLD_OBS: pre_submit_semantic_correctness" in r.message),
            None,
        )
        assert obs is not None
        assert "decision=blocked" in obs.message


# ---------------------------------------------------------------------------
# 5. Read-back retry (200ms) — log fires when first verify fails
# ---------------------------------------------------------------------------


class TestReadbackRetryLog:
    @pytest.mark.asyncio
    async def test_readback_retry_log_fires_on_mismatch(self, caplog):
        """First verify fails (returns wrong value) → THRESHOLD_OBS readback_retry emitted."""
        from jobpulse.navigation.action_executor import NavigationActionExecutor, ExecutorResult

        page = AsyncMock()
        page.url = "https://example.com"

        # First call to input_value returns wrong value (triggers retry);
        # second call also returns wrong (so we get a fill-failure, not verified).
        loc = AsyncMock()

        async def _input_value():
            # Always return wrong value — we just need the retry branch to fire.
            return "wrong"

        loc.input_value = _input_value
        loc.fill = AsyncMock()

        locator_with_count = MagicMock()
        locator_with_count.count = AsyncMock(return_value=1)
        locator_with_count.first = loc

        page.get_by_label = MagicMock(return_value=locator_with_count)
        page.get_by_placeholder = MagicMock(return_value=locator_with_count)

        executor = NavigationActionExecutor(page)
        result = ExecutorResult()

        with caplog.at_level(logging.INFO, logger="jobpulse.navigation.action_executor"):
            await executor._execute_fill(
                {"label": "Email", "value": "correct@example.com", "method": "fill"},
                profile={},
                result=result,
            )

        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: readback_retry" in r.message),
            None,
        )
        assert obs is not None, "readback_retry THRESHOLD_OBS log not emitted"
        assert "threshold_ms=200" in obs.message
        assert "decision=retrying" in obs.message


# ---------------------------------------------------------------------------
# 6. Substring guard — debug-level log, fires on every _verify_fill call
# ---------------------------------------------------------------------------


class TestSubstringGuardLog:
    @pytest.mark.asyncio
    async def test_substring_guard_log_allowed(self, caplog):
        """Both strings >= 3 chars → decision=substring_allowed."""
        from jobpulse.navigation.action_executor import NavigationActionExecutor

        page = AsyncMock()
        loc = AsyncMock()
        loc.input_value = AsyncMock(return_value="hello world")
        executor = NavigationActionExecutor(page)

        with caplog.at_level(logging.DEBUG, logger="jobpulse.navigation.action_executor"):
            result = await executor._verify_fill(loc, "hello world extended")

        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: substring_guard" in r.message),
            None,
        )
        assert obs is not None, "substring_guard THRESHOLD_OBS log not emitted"
        assert "threshold=3" in obs.message
        assert "decision=substring_allowed" in obs.message

    @pytest.mark.asyncio
    async def test_substring_guard_log_exact_only_short_string(self, caplog):
        """One string < 3 chars → decision=exact_only.

        The log fires only when norm_e != norm_a (after the early-return
        exact-match check on line 307). Use non-equal strings where min
        length is < 3 so the substring gate rejects them.
        """
        from jobpulse.navigation.action_executor import NavigationActionExecutor

        page = AsyncMock()
        loc = AsyncMock()
        # actual="ab" (len 2), expected="xy" (len 2) — not equal, so log fires
        loc.input_value = AsyncMock(return_value="ab")
        executor = NavigationActionExecutor(page)

        with caplog.at_level(logging.DEBUG, logger="jobpulse.navigation.action_executor"):
            result = await executor._verify_fill(loc, "xy")

        # Since min(2, 2) < 3, substring check is skipped → returns False
        assert result is False
        obs = next(
            (r for r in caplog.records if "THRESHOLD_OBS: substring_guard" in r.message),
            None,
        )
        assert obs is not None, "substring_guard THRESHOLD_OBS log not emitted"
        assert "decision=exact_only" in obs.message
