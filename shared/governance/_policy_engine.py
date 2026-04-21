"""Declarative policy enforcement — score bounds, cost caps, call limits."""

from __future__ import annotations

from dataclasses import dataclass

from shared.logging_config import get_logger

logger = get_logger(__name__)

POLICIES = {
    "score_bounds": {"min": 0.0, "max": 10.0},
    "cost_cap_per_run": 2.00,
    "max_llm_calls_per_agent": 50,
    "require_output_sanitization": True,
    "max_input_length": 8000,
}


class PolicyViolation(Exception):
    def __init__(self, policy_name: str, reason: str):
        self.policy_name = policy_name
        self.reason = reason
        super().__init__(f"Policy violated: {policy_name} — {reason}")


@dataclass
class PolicyResult:
    allowed: bool
    policy_name: str
    reason: str


def check_policy(policy_name: str, value) -> PolicyResult:
    policy = POLICIES.get(policy_name)
    if policy is None:
        return PolicyResult(allowed=False, policy_name=policy_name, reason="unknown policy")

    if policy_name == "score_bounds":
        lo, hi = policy["min"], policy["max"]
        if lo <= value <= hi:
            return PolicyResult(allowed=True, policy_name=policy_name, reason="within bounds")
        return PolicyResult(allowed=False, policy_name=policy_name,
                            reason=f"{value} outside [{lo}, {hi}]")

    if policy_name == "cost_cap_per_run":
        if value <= policy:
            return PolicyResult(allowed=True, policy_name=policy_name, reason="under cap")
        return PolicyResult(allowed=False, policy_name=policy_name,
                            reason=f"${value:.2f} exceeds cap ${policy:.2f}")

    if policy_name == "max_llm_calls_per_agent":
        if value <= policy:
            return PolicyResult(allowed=True, policy_name=policy_name, reason="under limit")
        return PolicyResult(allowed=False, policy_name=policy_name,
                            reason=f"{value} calls exceeds limit {policy}")

    if policy_name == "max_input_length":
        if value <= policy:
            return PolicyResult(allowed=True, policy_name=policy_name, reason="under limit")
        return PolicyResult(allowed=False, policy_name=policy_name,
                            reason=f"{value} chars exceeds {policy}")

    return PolicyResult(allowed=True, policy_name=policy_name, reason="no check defined")


class PolicyEnforcer:
    def __init__(self):
        self._total_cost: float = 0.0
        self._call_counts: dict[str, int] = {}
        self._violation_emitted: bool = False

    @property
    def total_cost(self) -> float:
        return self._total_cost

    def track_llm_call(self, agent_name: str, cost_usd: float) -> None:
        self._total_cost += cost_usd
        self._call_counts[agent_name] = self._call_counts.get(agent_name, 0) + 1

    def check_cost_cap(self) -> None:
        result = check_policy("cost_cap_per_run", self._total_cost)
        if not result.allowed:
            if not self._violation_emitted:
                self._violation_emitted = True
                try:
                    from shared.execution import emit
                    emit("governance:policy", "governance.policy_violated", {
                        "policy": "cost_cap_per_run",
                        "value": self._total_cost,
                        "cap": POLICIES["cost_cap_per_run"],
                    })
                except Exception:
                    pass
            raise PolicyViolation("cost_cap_per_run", result.reason)

    def reset(self) -> None:
        self._total_cost = 0.0
        self._call_counts.clear()
        self._violation_emitted = False
