"""Tests for cost enforcement / budget cap."""

import pytest
from shared.cost_tracker import CostEnforcer, BudgetExceededError


def test_allows_under_budget():
    enforcer = CostEnforcer(max_budget_usd=1.00)
    enforcer.record(0.50)
    assert enforcer.remaining() == pytest.approx(0.50, abs=0.01)


def test_blocks_over_budget():
    enforcer = CostEnforcer(max_budget_usd=1.00)
    enforcer.record(0.80)
    with pytest.raises(BudgetExceededError):
        enforcer.check_budget(estimated_cost=0.30)


def test_respects_env_override(monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_CAP_USD", "5.00")
    enforcer = CostEnforcer()
    assert enforcer.max_budget_usd == 5.00


def test_disabled_when_zero():
    """Budget cap of 0 means unlimited."""
    enforcer = CostEnforcer(max_budget_usd=0)
    enforcer.record(999.99)
    enforcer.check_budget(estimated_cost=100.0)  # Should not raise


def test_reset():
    enforcer = CostEnforcer(max_budget_usd=1.00)
    enforcer.record(0.90)
    enforcer.reset()
    assert enforcer.total_spent == 0.0
