"""Tests for MODEL_COSTS integrity."""

from shared.cost_tracker import MODEL_COSTS, estimate_cost


def test_no_duplicate_model_keys():
    """MODEL_COSTS must not have entries that shadow each other."""
    assert MODEL_COSTS["gpt-4.1-mini"] == (0.40, 1.60), (
        f"gpt-4.1-mini should be (0.40, 1.60), got {MODEL_COSTS['gpt-4.1-mini']}"
    )


def test_gpt41_mini_cost_estimate():
    """1M input + 1M output on gpt-4.1-mini should cost $2.00."""
    cost = estimate_cost("gpt-4.1-mini", 1_000_000, 1_000_000)
    assert abs(cost - 2.00) < 0.01, f"Expected ~$2.00, got ${cost:.2f}"
