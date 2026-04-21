"""Tests for Awareness Loop — TaskRunner middleware."""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock


class TestConfidenceTracker:
    def test_initial_confidence(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan

        plan = TaskPlan(
            confidence=0.8,
            strategy=[],
            anti_patterns=[],
            cognitive_level="L1",
            start_tier=1,
            escalation_hints=[],
        )
        tracker = ConfidenceTracker(plan)
        assert tracker.confidence == 0.8

    def test_confidence_drops_on_mismatch(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan
        from shared.execution._verifier import VerifyResult

        plan = TaskPlan(
            confidence=0.8,
            strategy=[],
            anti_patterns=[],
            cognitive_level="L1",
            start_tier=1,
            escalation_hints=[],
        )
        tracker = ConfidenceTracker(plan)
        decision = tracker.after_action(
            event={"event_type": "form.fields_filled"},
            verify=VerifyResult(field_mismatch=True),
        )
        assert tracker.confidence == pytest.approx(0.6)

    def test_confidence_recovers_on_ok(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan
        from shared.execution._verifier import VerifyResult

        plan = TaskPlan(
            confidence=0.5,
            strategy=[],
            anti_patterns=[],
            cognitive_level="L1",
            start_tier=1,
            escalation_hints=[],
        )
        tracker = ConfidenceTracker(plan)
        tracker.after_action({}, VerifyResult())
        assert tracker.confidence == pytest.approx(0.55)

    def test_escalates_below_threshold(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan, Decision
        from shared.execution._verifier import VerifyResult

        plan = TaskPlan(
            confidence=0.5,
            strategy=[],
            anti_patterns=[],
            cognitive_level="L1",
            start_tier=1,
            escalation_hints=[],
        )
        tracker = ConfidenceTracker(plan)
        decision = tracker.after_action({}, VerifyResult(field_mismatch=True))
        assert decision.action == "escalate"


class TestTaskPreFlight:
    def test_cold_start_fast_path(self, event_store):
        from shared.execution._awareness import TaskPreFlight

        mock_memory = MagicMock()
        mock_memory.recall.return_value = []
        preflight = TaskPreFlight(
            memory=mock_memory,
            cognitive=None,
            optimization=None,
            event_store=event_store,
        )
        plan = preflight.prepare(
            {
                "input": {"domain": "test.com", "platform": "greenhouse"},
                "skill_id": "apply-job",
            }
        )
        assert plan.confidence == 0.5
        assert plan.start_tier == 1
        assert plan.cognitive_level == "L1"

    def test_full_path_with_memories(self, event_store):
        from shared.execution._awareness import TaskPreFlight

        mock_memory = MagicMock()
        mock_memory.recall.return_value = [{"strategy": "fill top-to-bottom"}]
        mock_cognitive = MagicMock()
        mock_cognitive.assess.return_value = MagicMock(
            confidence=0.85, recommended_level="L1"
        )
        mock_opt = MagicMock()
        mock_opt.get_domain_stats.return_value = {"success_rate": 0.9}
        preflight = TaskPreFlight(
            memory=mock_memory,
            cognitive=mock_cognitive,
            optimization=mock_opt,
            event_store=event_store,
        )
        plan = preflight.prepare(
            {
                "input": {"domain": "test.com", "platform": "greenhouse"},
                "skill_id": "apply-job",
            }
        )
        assert plan.confidence == 0.85
        assert plan.start_tier == 1


class TestTaskRunner:
    def test_wraps_agent_and_runs(self, event_store):
        from shared.execution._awareness import TaskRunner, TaskPlan

        async def mock_agent(task, plan, tracker):
            return {"success": True, "failure_reason": None}

        runner = TaskRunner(
            agent_fn=mock_agent,
            memory=MagicMock(recall=MagicMock(return_value=[])),
            cognitive=None,
            optimization=None,
            event_store=event_store,
        )
        task = {
            "task_id": "test123",
            "input": {"domain": "x", "platform": "y"},
            "skill_id": "test",
            "timeout_s": 30,
        }
        result = asyncio.run(runner.run(task))
        assert result["success"] is True

    def test_timeout_produces_failure(self, event_store):
        from shared.execution._awareness import TaskRunner

        async def slow_agent(task, plan, tracker):
            await asyncio.sleep(10)
            return {"success": True}

        runner = TaskRunner(
            agent_fn=slow_agent,
            memory=MagicMock(recall=MagicMock(return_value=[])),
            cognitive=None,
            optimization=None,
            event_store=event_store,
        )
        task = {
            "task_id": "timeout_test",
            "input": {"domain": "x", "platform": "y"},
            "skill_id": "test",
            "timeout_s": 1,
        }
        result = asyncio.run(runner.run(task))
        assert result["success"] is False
        assert "timeout" in result["failure_reason"]
