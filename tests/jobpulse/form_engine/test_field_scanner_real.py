"""Tests for field_scanner.py — validation and merge logic, no browser."""

import pytest


class _FakeStrategy:
    """Minimal strategy for testing validate_field_scan."""

    def __init__(self, min_fields=3, max_fields=15):
        self._min = min_fields
        self._max = max_fields

    def expected_field_range(self):
        return (self._min, self._max)


class TestValidateFieldScan:
    def test_valid_fields_pass(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan

        fields = [
            {"label": "First Name", "selector": "#fname", "type": "text"},
            {"label": "Email", "selector": "#email", "type": "email"},
            {"label": "Phone", "selector": "#phone", "type": "tel"},
        ]
        result = validate_field_scan(fields, _FakeStrategy())
        assert result["valid"] is True

    def test_empty_fields_rejected(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan

        result = validate_field_scan([], _FakeStrategy())
        assert result["valid"] is False
        assert result["reason"] == "zero_fields"

    def test_too_many_fields_rejected(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan

        fields = [{"label": f"Field {i}", "type": "text"} for i in range(50)]
        result = validate_field_scan(fields, _FakeStrategy(max_fields=15))
        assert result["valid"] is False
        assert result["reason"] == "too_many_fields"

    def test_duplicate_labels_rejected(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan

        fields = [{"label": "Name", "type": "text"} for _ in range(5)]
        result = validate_field_scan(fields, _FakeStrategy(max_fields=20))
        assert result["valid"] is False
        assert result["reason"] == "duplicate_labels"

    def test_form_experience_adjusts_max(self):
        from jobpulse.form_engine.field_scanner import validate_field_scan

        fields = [{"label": f"Field {i}", "type": "text"} for i in range(20)]
        result = validate_field_scan(
            fields, _FakeStrategy(max_fields=10),
            form_experience={"field_count": 20},
        )
        assert result["valid"] is True


class TestMergeFields:
    def test_merges_without_duplicates(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "Name", "type": "text"}]
        secondary = [{"label": "Email", "type": "email"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 2

    def test_primary_wins_on_conflict(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "Name", "type": "text", "source": "a11y"}]
        secondary = [{"label": "Name", "type": "text", "source": "dom"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 1
        assert merged[0]["source"] == "a11y"

    def test_different_types_both_kept(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "Name", "type": "text"}]
        secondary = [{"label": "Name", "type": "select"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 2

    def test_empty_label_skipped(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "Name", "type": "text"}]
        secondary = [{"label": "", "type": "text"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 1


class TestFillableCount:
    def test_counts_fillable(self):
        from jobpulse.form_engine.field_scanner import _fillable_count

        fields = [
            {"label": "Name", "type": "text"},
            {"label": "Submit", "type": "button"},
            {"label": "Email", "type": "email"},
        ]
        assert _fillable_count(fields) == 2

    def test_all_fillable(self):
        from jobpulse.form_engine.field_scanner import _fillable_count

        fields = [
            {"label": "Name", "type": "text"},
            {"label": "Phone", "type": "tel"},
        ]
        assert _fillable_count(fields) == 2

    def test_empty_list(self):
        from jobpulse.form_engine.field_scanner import _fillable_count

        assert _fillable_count([]) == 0
