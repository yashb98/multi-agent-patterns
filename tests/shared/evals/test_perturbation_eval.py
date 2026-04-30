"""Tests for perturbation eval runner."""
from __future__ import annotations

import json
import pytest

from shared.evals.perturbation_runner import (
    PerturbationEvalRunner,
    PerturbationEvalResult,
)


SAMPLE_FIELDS = [
    {"label": "Gender", "type": "radio", "options": ["Male", "Female", "Other"], "value": ""},
    {"label": "Experience", "type": "select", "options": ["0-1 years", "2-3 years", "4-5 years"], "value": ""},
]

EXPECTED_MATCHES = {
    "Gender": {"desired": "Male", "expected_option": "Male"},
    "Experience": {"desired": "3 years", "expected_option": "2-3 years"},
}


class TestPerturbationEvalRunner:
    def test_run_on_original_all_pass(self):
        runner = PerturbationEvalRunner()
        result = runner.eval_semantic_matcher(
            fields=SAMPLE_FIELDS,
            expected_matches=EXPECTED_MATCHES,
        )
        assert isinstance(result, PerturbationEvalResult)
        assert result.total_fields == 2
        assert result.correct >= 1

    def test_run_on_perturbed_variants(self):
        runner = PerturbationEvalRunner()
        results = runner.eval_with_perturbations(
            fields=SAMPLE_FIELDS,
            expected_matches=EXPECTED_MATCHES,
            n_variants=5,
        )
        assert len(results) == 6  # 1 original + 5 variants
        assert results[0].strategy == "original"
        strategies = {r.strategy for r in results}
        assert "original" in strategies

    def test_result_has_failures_list(self):
        runner = PerturbationEvalRunner()
        result = runner.eval_semantic_matcher(
            fields=SAMPLE_FIELDS,
            expected_matches=EXPECTED_MATCHES,
        )
        assert isinstance(result.failures, list)


class TestFailureToBenchmark:
    def test_export_failures_as_cases(self):
        runner = PerturbationEvalRunner()
        fields = [
            {"label": "Gender", "type": "radio",
             "options": ["Masculine", "Feminine"],
             "value": ""},
        ]
        expected = {"Gender": {"desired": "Male", "expected_option": "Masculine"}}
        result = runner.eval_semantic_matcher(fields, expected, strategy="rename_labels")
        cases = runner.failures_to_benchmark_cases([result])
        assert isinstance(cases, list)
        for case in cases:
            assert "case_id" in case
            assert "flow" in case
            assert case["flow"] == "field_mapping_perturbation"

    def test_write_failures_to_json(self, tmp_path):
        runner = PerturbationEvalRunner()
        fields = [
            {"label": "Weird Field", "type": "radio",
             "options": ["XYZ", "ABC"], "value": ""},
        ]
        expected = {"Weird Field": {"desired": "Male", "expected_option": "XYZ"}}
        results = runner.eval_with_perturbations(fields, expected, n_variants=3)
        cases = runner.failures_to_benchmark_cases(results)
        out = tmp_path / "perturbation_failures.json"
        runner.export_failures(cases, out)
        assert out.exists()
        data = json.loads(out.read_text())
        assert isinstance(data, list)
