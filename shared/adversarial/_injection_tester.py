"""Injection testing — run golden suite cases against governance primitives."""

from __future__ import annotations

import math
from dataclasses import dataclass

from shared.adversarial._golden_suite import GoldenCase
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TestResult:
    case_id: str
    passed: bool
    actual: str
    expected: str
    notes: str


class InjectionTester:
    def test_score_integrity(self, cases: list[GoldenCase]) -> list[TestResult]:
        from shared.governance._score_validator import validate_review, reset_anomaly_counter
        results = []
        for case in cases:
            review = case.input.get("review", {})
            reset_anomaly_counter()
            validated = validate_review(review)
            passed = True
            notes = ""

            raw_score = review.get("overall_score")
            if isinstance(raw_score, float) and math.isnan(raw_score):
                passed = validated.overall_score == 5.0
                notes = f"NaN → {validated.overall_score}"
            elif isinstance(raw_score, float) and math.isinf(raw_score):
                expected = 10.0 if raw_score > 0 else 0.0
                passed = validated.overall_score == expected
                notes = f"Inf → {validated.overall_score}"
            elif isinstance(raw_score, str):
                passed = validated.overall_score == 5.0
                notes = f"string → fallback {validated.overall_score}"
            elif isinstance(raw_score, (int, float)):
                passed = 0.0 <= validated.overall_score <= 10.0
                notes = f"{raw_score} → {validated.overall_score}"
            elif raw_score is None:
                passed = validated.overall_score == 5.0
                notes = "missing → fallback"

            results.append(TestResult(
                case_id=case.id,
                passed=passed,
                actual=str(validated.overall_score),
                expected=case.expected_behavior,
                notes=notes,
            ))
        return results

    def test_output_sanitization(self, cases: list[GoldenCase]) -> list[TestResult]:
        from shared.governance._output_sanitizer import sanitize_agent_output
        results = []
        for case in cases:
            text = case.input.get("text", "")
            agent = case.input.get("agent", "test")
            sanitized = sanitize_agent_output(text, agent)
            passed = True
            notes = ""

            if "</system>" in text:
                passed = "</system>" not in sanitized
                notes = "system tag stripped" if passed else "system tag NOT stripped"
            elif "<script>" in text:
                passed = "<script>" not in sanitized
                notes = "script tag stripped" if passed else "script tag NOT stripped"
            elif "</agent_output>" in text and "<agent_output" in text:
                inner_count = sanitized.count("<agent_output")
                passed = inner_count == 1
                notes = f"agent_output tag count: {inner_count}"
            else:
                passed = f'<agent_output from="{agent}">' in sanitized
                notes = "wrapped correctly" if passed else "missing wrapper"

            results.append(TestResult(
                case_id=case.id,
                passed=passed,
                actual=sanitized[:200],
                expected=case.expected_behavior,
                notes=notes,
            ))
        return results

    def test_prompt_input_defense(self, cases: list[GoldenCase]) -> list[TestResult]:
        from shared.prompt_defense import sanitize_user_input, MAX_USER_INPUT_LENGTH
        results = []
        for case in cases:
            text = case.input.get("text", "")
            sanitized = sanitize_user_input(text, source="test")
            passed = True
            notes = ""

            if "</system>" in text:
                passed = "</system>" not in sanitized
                notes = "system tag stripped" if passed else "NOT stripped"
            elif "<agent_output" in text:
                passed = '<agent_output from="admin">' not in sanitized
                notes = "agent_output tag stripped" if passed else "NOT stripped"
            elif "<instruction>" in text:
                passed = "<instruction>" not in sanitized
                notes = "instruction tag stripped" if passed else "NOT stripped"
            elif len(text) > MAX_USER_INPUT_LENGTH:
                passed = "[TRUNCATED]" in sanitized
                notes = "truncated" if passed else "NOT truncated"
            else:
                passed = "<user_input" in sanitized
                notes = "wrapped in user_input boundary"

            results.append(TestResult(
                case_id=case.id,
                passed=passed,
                actual=sanitized[:200],
                expected=case.expected_behavior,
                notes=notes,
            ))
        return results
