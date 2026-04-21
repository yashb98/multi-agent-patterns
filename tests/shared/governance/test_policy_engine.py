"""Tests for declarative policy enforcement — score bounds, cost caps, call limits."""

import pytest


class TestCheckPolicy:
    def test_score_within_bounds_allowed(self):
        from shared.governance._policy_engine import check_policy
        result = check_policy("score_bounds", 7.5)
        assert result.allowed is True

    def test_score_out_of_bounds_denied(self):
        from shared.governance._policy_engine import check_policy
        result = check_policy("score_bounds", 15.0)
        assert result.allowed is False

    def test_cost_under_cap_allowed(self):
        from shared.governance._policy_engine import check_policy
        result = check_policy("cost_cap_per_run", 1.50)
        assert result.allowed is True

    def test_cost_over_cap_denied(self):
        from shared.governance._policy_engine import check_policy
        result = check_policy("cost_cap_per_run", 3.00)
        assert result.allowed is False

    def test_unknown_policy_denied(self):
        from shared.governance._policy_engine import check_policy
        result = check_policy("nonexistent_policy", 1)
        assert result.allowed is False


class TestPolicyEnforcer:
    def test_track_llm_call(self):
        from shared.governance._policy_engine import PolicyEnforcer
        enforcer = PolicyEnforcer()
        enforcer.track_llm_call("researcher", 0.01)
        assert enforcer.total_cost == pytest.approx(0.01)

    def test_cost_cap_violation_raises(self):
        from shared.governance._policy_engine import PolicyEnforcer, PolicyViolation
        enforcer = PolicyEnforcer()
        enforcer._total_cost = 1.99
        enforcer.track_llm_call("writer", 0.02)
        with pytest.raises(PolicyViolation):
            enforcer.check_cost_cap()

    def test_reset_clears_state(self):
        from shared.governance._policy_engine import PolicyEnforcer
        enforcer = PolicyEnforcer()
        enforcer.track_llm_call("researcher", 0.50)
        enforcer.reset()
        assert enforcer.total_cost == 0.0
