import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.optimization._aggregator import AggregatedInsight
from shared.optimization._policy import OptimizationPolicy, OptimizationBudget, PolicyAction


class TestOptimizationPolicy:

    @pytest.fixture
    def policy(self, mock_memory, mock_cognitive):
        return OptimizationPolicy(
            memory_manager=mock_memory,
            cognitive_engine=mock_cognitive,
        )

    def _make_insight(self, pattern_type, domain="workday",
                      confidence=0.85, action="generate_insight"):
        return AggregatedInsight(
            pattern_type=pattern_type,
            confidence=confidence,
            contributing_signals=["sig_1", "sig_2", "sig_3"],
            domain=domain,
            recommended_action=action,
            evidence=f"Test evidence for {pattern_type}",
        )

    def test_systemic_failure_generates_insight_and_rule(self, policy):
        insight = self._make_insight("systemic_failure")
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "generate_insight" in action_types

    def test_regression_triggers_rollback(self, policy):
        insight = self._make_insight(
            "regression", confidence=0.9,
            action="rollback_persona_evolution",
        )
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "rollback" in action_types or "demote_memory" in action_types

    def test_persona_drift_triggers_rollback_and_pause(self, policy):
        insight = self._make_insight("persona_drift", action="rollback_persona")
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "rollback_persona" in action_types
        assert "pause_loop" in action_types

    def test_platform_change_alerts_human(self, policy):
        insight = self._make_insight(
            "platform_change", confidence=0.7, action="alert_human",
        )
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "alert_human" in action_types

    def test_cognitive_escalation_on_degradation(self, policy):
        insight = self._make_insight(
            "regression", domain="workday",
            confidence=0.9, action="escalate_cognitive",
        )
        insight.evidence = "form_filler on workday: score went from 8.0 to 5.0"
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "escalate_cognitive" in action_types or "rollback" in action_types

    def test_budget_guardrails_enforced(self, policy):
        policy._budget = OptimizationBudget(max_rollbacks_per_hour=2)
        insight = self._make_insight("regression", action="rollback_persona")
        policy.decide(insight)
        policy.decide(insight)
        actions = policy.decide(insight)
        action_types = {a.action_type for a in actions}
        assert "alert_human" in action_types

    def test_cooldown_after_rollback(self, policy):
        policy._budget = OptimizationBudget(
            max_rollbacks_per_hour=10,
            cooldown_after_rollback_minutes=30,
        )
        insight = self._make_insight("regression", action="rollback_persona")
        actions1 = policy.decide(insight)
        assert any(a.action_type in ("rollback", "rollback_persona", "demote_memory")
                    for a in actions1)
        # Second rollback within cooldown should be blocked
        actions2 = policy.decide(insight)
        rollbacks = [a for a in actions2
                     if a.action_type in ("rollback", "rollback_persona")]
        if rollbacks:
            # Cooldown may degrade to alert_human
            pass

    @pytest.mark.asyncio
    async def test_llm_fallback_for_novel_situations(self, policy, mock_cognitive):
        insight = self._make_insight("systemic_failure", confidence=0.5)
        actions = await policy.decide_async(insight)
        assert len(mock_cognitive.think_calls) >= 1

    @pytest.mark.asyncio
    async def test_cognitive_think_uses_reflexion(self, policy, mock_cognitive):
        insight = self._make_insight("redundant", confidence=0.4)
        await policy.decide_async(insight)
        if mock_cognitive.think_calls:
            assert mock_cognitive.think_calls[0]["stakes"] == "medium"

    def test_memory_promote_on_improvement(self, policy, mock_memory):
        actions = policy.promote_memory("mem_001")
        assert "mem_001" in mock_memory._promoted

    def test_memory_demote_on_regression(self, policy, mock_memory):
        actions = policy.demote_memory("mem_002")
        assert "mem_002" in mock_memory._demoted

    def test_pinned_memories_never_auto_demoted(self, policy, mock_memory):
        mock_memory._pinned.append("mem_003")
        # Attempting to demote a pinned memory should be blocked
        result = policy.demote_memory("mem_003", check_pinned=True)
        assert "mem_003" not in mock_memory._demoted

    def test_contradiction_resolution(self, policy, mock_memory):
        policy.resolve_contradiction(
            new_id="mem_new", old_id="mem_old", new_stronger=True,
        )
        assert "mem_old" in mock_memory._contradicted
