"""Tests for environment perturbation strategies."""
from __future__ import annotations

import pytest

from shared.evals.perturbation import (
    PerturbationEngine,
    reorder_fields,
    rename_labels,
    add_noise_fields,
    change_option_text,
    shuffle_options,
)


SAMPLE_FIELDS = [
    {"label": "First Name", "type": "text", "options": [], "value": ""},
    {"label": "Email", "type": "text", "options": [], "value": ""},
    {"label": "Gender", "type": "radio", "options": ["Male", "Female", "Other"], "value": ""},
    {"label": "Resume", "type": "file", "options": [], "value": ""},
    {"label": "Experience", "type": "select", "options": ["0-1 years", "2-3 years", "4-5 years"], "value": ""},
]


class TestReorderFields:
    def test_preserves_all_fields(self):
        result = reorder_fields(SAMPLE_FIELDS, seed=42)
        assert len(result) == len(SAMPLE_FIELDS)
        result_labels = {f["label"] for f in result}
        original_labels = {f["label"] for f in SAMPLE_FIELDS}
        assert result_labels == original_labels

    def test_order_changes_with_seed(self):
        r1 = reorder_fields(SAMPLE_FIELDS, seed=1)
        r2 = reorder_fields(SAMPLE_FIELDS, seed=2)
        labels_1 = [f["label"] for f in r1]
        labels_2 = [f["label"] for f in r2]
        assert labels_1 != labels_2 or len(SAMPLE_FIELDS) < 3


class TestRenameLabels:
    def test_labels_are_different(self):
        result = rename_labels(SAMPLE_FIELDS, seed=42)
        original_labels = [f["label"] for f in SAMPLE_FIELDS]
        new_labels = [f["label"] for f in result]
        changed = sum(1 for a, b in zip(original_labels, new_labels) if a != b)
        assert changed >= 1

    def test_preserves_field_count(self):
        result = rename_labels(SAMPLE_FIELDS, seed=42)
        assert len(result) == len(SAMPLE_FIELDS)

    def test_preserves_types(self):
        result = rename_labels(SAMPLE_FIELDS, seed=42)
        for orig, pert in zip(SAMPLE_FIELDS, result):
            assert orig["type"] == pert["type"]


class TestAddNoiseFields:
    def test_adds_fields(self):
        result = add_noise_fields(SAMPLE_FIELDS, n_noise=3, seed=42)
        assert len(result) > len(SAMPLE_FIELDS)
        assert len(result) == len(SAMPLE_FIELDS) + 3

    def test_noise_fields_have_labels(self):
        result = add_noise_fields(SAMPLE_FIELDS, n_noise=2, seed=42)
        for f in result:
            assert "label" in f
            assert "type" in f


class TestChangeOptionText:
    def test_text_fields_unchanged(self):
        result = change_option_text(SAMPLE_FIELDS, seed=42)
        for orig, pert in zip(SAMPLE_FIELDS, result):
            if orig["type"] == "text":
                assert orig["options"] == pert["options"]


class TestShuffleOptions:
    def test_preserves_all_options(self):
        result = shuffle_options(SAMPLE_FIELDS, seed=42)
        for orig, pert in zip(SAMPLE_FIELDS, result):
            assert set(orig.get("options", [])) == set(pert.get("options", []))


class TestPerturbationEngine:
    def test_generate_variants(self):
        engine = PerturbationEngine()
        variants = engine.generate_variants(SAMPLE_FIELDS, n_variants=5, base_seed=42)
        assert len(variants) == 5
        for v in variants:
            assert "strategy" in v
            assert "fields" in v
            assert isinstance(v["fields"], list)

    def test_variant_strategies_are_diverse(self):
        engine = PerturbationEngine()
        variants = engine.generate_variants(SAMPLE_FIELDS, n_variants=5, base_seed=42)
        strategies = {v["strategy"] for v in variants}
        assert len(strategies) == 5
