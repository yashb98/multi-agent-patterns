"""Trajectory-level evaluation for application pipeline.

Scores not just outcome (success/fail) but process:
- Path optimality: were there unnecessary steps?
- Strategy choice: did the agent use the cheapest effective strategy?
- Time efficiency: was the fill time reasonable?
"""
from __future__ import annotations

from collections import Counter

from shared.logging_config import get_logger

logger = get_logger(__name__)

_STRATEGY_COST_ORDER = ["deterministic", "cached", "consensus", "llm", "vision"]


def score_trajectory(
    trajectory: list[dict],
    *,
    success: bool,
) -> float:
    if not trajectory:
        return 0.0

    if not success:
        return min(0.3, len(trajectory) * 0.05)

    action_counts = Counter(step.get("action", "") for step in trajectory)
    total_steps = len(trajectory)

    repeated_actions = sum(max(0, count - 1) for count in action_counts.values())
    repetition_penalty = min(0.4, repeated_actions * 0.1)

    step_penalty = max(0, (total_steps - 4) * 0.05)
    step_penalty = min(0.3, step_penalty)

    total_time_ms = sum(step.get("time_ms", 0) for step in trajectory)
    time_penalty = 0.0
    if total_time_ms > 30_000:
        time_penalty = min(0.2, (total_time_ms - 30_000) / 100_000)

    score = 1.0 - repetition_penalty - step_penalty - time_penalty
    return max(0.0, min(1.0, round(score, 3)))


def score_strategy_choice(
    chosen_strategy: str,
    available_strategies: list[str],
    outcome_success: bool,
) -> float:
    if not outcome_success:
        return 0.3

    cheapest_available = None
    for s in _STRATEGY_COST_ORDER:
        if s in available_strategies:
            cheapest_available = s
            break

    if cheapest_available is None:
        return 0.5

    chosen_rank = (
        _STRATEGY_COST_ORDER.index(chosen_strategy)
        if chosen_strategy in _STRATEGY_COST_ORDER
        else len(_STRATEGY_COST_ORDER)
    )
    cheapest_rank = _STRATEGY_COST_ORDER.index(cheapest_available)

    gap = chosen_rank - cheapest_rank
    penalty = gap * 0.15
    return max(0.0, min(1.0, round(1.0 - penalty, 3)))
