"""LLM Cost Tracking — per-model pricing, token counting, and cost summaries.

Tracks token usage and estimated USD cost for every LLM call.
Used by agent nodes and pattern finish nodes for cost visibility.
"""

import os
import threading

from shared.logging_config import get_logger

logger = get_logger(__name__)

# Approximate pricing per 1M tokens (USD) for common models.
# Updated as of 2026-04.

MODEL_COSTS = {
    # model_prefix: (input_per_1M, output_per_1M)
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o3-mini": (1.10, 4.40),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate USD cost for a single LLM call based on token counts."""
    costs = MODEL_COSTS.get(model)
    if not costs:
        # Try prefix match (e.g. "gpt-4o-mini-2024-07-18" → "gpt-4o-mini")
        for prefix, c in MODEL_COSTS.items():
            if model.startswith(prefix):
                costs = c
                break
    if not costs:
        costs = (0.15, 0.60)  # Default to cheapest tier

    return (prompt_tokens * costs[0] + completion_tokens * costs[1]) / 1_000_000


def track_llm_usage(response, agent_name: str) -> dict:
    """Extract token usage from a LangChain response and return a tracking dict.

    Works with ChatOpenAI responses that have response_metadata.
    """
    metadata = getattr(response, "response_metadata", {}) or {}
    usage = metadata.get("token_usage", {}) or {}
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    model = metadata.get("model_name", "gpt-4.1-mini")
    cost = estimate_cost(model, prompt_tokens, completion_tokens)

    return {
        "agent": agent_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "model": model,
        "cost_usd": cost,
    }


def compute_cost_summary(token_usage: list[dict]) -> dict:
    """Compute aggregate cost summary from accumulated token_usage entries."""
    total_prompt = sum(u.get("prompt_tokens", 0) for u in token_usage)
    total_completion = sum(u.get("completion_tokens", 0) for u in token_usage)
    total_cost = sum(u.get("cost_usd", 0) for u in token_usage)
    per_agent = {}
    for u in token_usage:
        agent = u.get("agent", "unknown")
        per_agent.setdefault(agent, 0.0)
        per_agent[agent] += u.get("cost_usd", 0)
    return {
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "total_cost_usd": total_cost,
        "calls": len(token_usage),
        "cost_per_agent": per_agent,
    }


class BudgetExceededError(Exception):
    """Raised when LLM spending exceeds the configured budget cap."""
    def __init__(self, spent: float, cap: float, estimated: float):
        self.spent = spent
        self.cap = cap
        self.estimated = estimated
        super().__init__(
            f"Budget exceeded: ${spent:.4f} spent + ${estimated:.4f} estimated > ${cap:.2f} cap"
        )


class CostEnforcer:
    """Thread-safe budget cap for LLM spending.

    Set LLM_BUDGET_CAP_USD env var or pass max_budget_usd. 0 = unlimited.
    """
    def __init__(self, max_budget_usd: float | None = None):
        if max_budget_usd is not None:
            self.max_budget_usd = max_budget_usd
        else:
            self.max_budget_usd = float(os.getenv("LLM_BUDGET_CAP_USD", "10.00"))
        self.total_spent = 0.0
        self._lock = threading.Lock()

    def record(self, cost_usd: float):
        with self._lock:
            self.total_spent += cost_usd

    def check_budget(self, estimated_cost: float = 0.0):
        if self.max_budget_usd <= 0:
            return
        with self._lock:
            if self.total_spent + estimated_cost > self.max_budget_usd:
                raise BudgetExceededError(self.total_spent, self.max_budget_usd, estimated_cost)

    def remaining(self) -> float:
        with self._lock:
            return max(0, self.max_budget_usd - self.total_spent)

    def reset(self):
        with self._lock:
            self.total_spent = 0.0
