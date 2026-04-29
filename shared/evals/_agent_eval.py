"""Canonical-flow eval harness with committed golden fixtures.

This harness intentionally targets deterministic flows that matter to the
agent runtime: routing, strategy selection, prompt sanitization, output
sanitization, and review-score validation. The fixtures live in the repo so
they run on every PR without network access.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "evals"
    / "canonical_flows.json"
)


@dataclass
class CanonicalFlowCase:
    case_id: str
    flow: str
    input: dict[str, Any]
    expected: dict[str, Any]


@dataclass
class CanonicalFlowResult:
    case_id: str
    flow: str
    passed: bool
    actual: dict[str, Any]
    expected: dict[str, Any]


def load_canonical_flow_cases(path: str | Path | None = None) -> list[CanonicalFlowCase]:
    fixture_path = Path(path) if path is not None else _DEFAULT_FIXTURE
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [CanonicalFlowCase(**item) for item in raw]


def _run_case(case: CanonicalFlowCase) -> dict[str, Any]:
    if case.flow == "classify_command":
        from jobpulse.command_router import classify

        parsed = classify(case.input["text"])
        return {
            "intent": parsed.intent.value,
            "args": parsed.args,
        }

    if case.flow == "dispatch_strategy":
        from jobpulse.dispatch import default_strategy

        env_name = "JOBPULSE_SWARM"
        previous = os.environ.get(env_name)
        try:
            value = case.input.get("env_value")
            if value is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = str(value)
            strategy = default_strategy()
            return {"strategy": strategy.value}
        finally:
            if previous is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = previous

    if case.flow == "sanitize_prompt_input":
        from shared.prompt_defense import sanitize_user_input

        return {
            "sanitized": sanitize_user_input(
                case.input["text"],
                source=case.input.get("source", "eval"),
            ),
        }

    if case.flow == "sanitize_agent_output":
        from shared.governance._output_sanitizer import sanitize_agent_output

        return {
            "sanitized": sanitize_agent_output(
                case.input["text"],
                case.input.get("agent", "eval"),
            ),
        }

    if case.flow == "validate_review":
        from shared.governance._score_validator import reset_anomaly_counter, validate_review

        reset_anomaly_counter()
        review = validate_review(case.input["review"])
        return {
            "overall_score": review.overall_score,
            "accuracy_score": review.accuracy_score,
            "anomalies": review.anomalies,
        }

    raise ValueError(f"Unknown canonical flow: {case.flow}")


def _matches_expected(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, expected_value in expected.items():
        actual_value = actual.get(key)
        if isinstance(expected_value, str) and key.endswith("_contains"):
            source_key = key.removesuffix("_contains")
            source = str(actual.get(source_key, ""))
            if expected_value not in source:
                return False
            continue
        if actual_value != expected_value:
            return False
    return True


def run_canonical_flow_evals(
    path: str | Path | None = None,
) -> list[CanonicalFlowResult]:
    results: list[CanonicalFlowResult] = []
    for case in load_canonical_flow_cases(path):
        actual = _run_case(case)
        results.append(
            CanonicalFlowResult(
                case_id=case.case_id,
                flow=case.flow,
                passed=_matches_expected(actual, case.expected),
                actual=actual,
                expected=case.expected,
            )
        )
    return results
