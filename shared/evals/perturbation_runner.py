"""Run semantic_matcher against perturbed form variants.

Evaluates robustness of option matching under field reordering,
label renaming, noise injection, option text changes, and option shuffling.
Failures become new benchmark cases.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field as dc_field
from pathlib import Path

from shared.evals.perturbation import PerturbationEngine
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FieldEvalFailure:
    field_label: str
    desired_value: str
    expected_option: str
    actual_option: str | None
    strategy: str


@dataclass
class PerturbationEvalResult:
    strategy: str
    total_fields: int
    correct: int
    failures: list[FieldEvalFailure] = dc_field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total_fields if self.total_fields else 0.0


class PerturbationEvalRunner:
    def eval_semantic_matcher(
        self,
        fields: list[dict],
        expected_matches: dict[str, dict],
        *,
        strategy: str = "original",
    ) -> PerturbationEvalResult:
        from jobpulse.form_engine.semantic_matcher import semantic_option_match

        total = 0
        correct = 0
        failures: list[FieldEvalFailure] = []

        for f in fields:
            label = f["label"]
            match_spec = None
            for exp_label, spec in expected_matches.items():
                if exp_label.lower() == label.lower() or label.lower() in exp_label.lower():
                    match_spec = spec
                    break
            if match_spec is None:
                continue

            options = f.get("options", [])
            if not options:
                continue

            total += 1
            desired = match_spec["desired"]
            expected = match_spec["expected_option"]

            actual = semantic_option_match(desired, options, field_label=label)

            if actual and actual.lower() == expected.lower():
                correct += 1
            else:
                failures.append(FieldEvalFailure(
                    field_label=label,
                    desired_value=desired,
                    expected_option=expected,
                    actual_option=actual,
                    strategy=strategy,
                ))

        return PerturbationEvalResult(
            strategy=strategy,
            total_fields=total,
            correct=correct,
            failures=failures,
        )

    def eval_with_perturbations(
        self,
        fields: list[dict],
        expected_matches: dict[str, dict],
        *,
        n_variants: int = 5,
        base_seed: int = 42,
    ) -> list[PerturbationEvalResult]:
        results: list[PerturbationEvalResult] = []

        results.append(self.eval_semantic_matcher(
            fields, expected_matches, strategy="original",
        ))

        engine = PerturbationEngine()
        variants = engine.generate_variants(
            fields, n_variants=n_variants, base_seed=base_seed,
        )
        for v in variants:
            results.append(self.eval_semantic_matcher(
                v["fields"], expected_matches, strategy=v["strategy"],
            ))

        return results

    def failures_to_benchmark_cases(
        self, results: list[PerturbationEvalResult],
    ) -> list[dict]:
        cases = []
        for r in results:
            for f in r.failures:
                cases.append({
                    "case_id": f"pert-{r.strategy}-{f.field_label}".lower().replace(" ", "_"),
                    "flow": "field_mapping_perturbation",
                    "input": {
                        "field_label": f.field_label,
                        "desired_value": f.desired_value,
                        "strategy": f.strategy,
                    },
                    "expected": {
                        "matched_option": f.expected_option,
                    },
                })
        return cases

    def export_failures(
        self, cases: list[dict], output_path: Path | str,
    ) -> None:
        path = Path(output_path)
        path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
        logger.info("Exported %d perturbation failure cases to %s", len(cases), path)
