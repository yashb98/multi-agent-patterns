"""Tests for form_analyzer — deterministic fill + fuzzy dropdown matching."""

from __future__ import annotations

import pytest

from jobpulse.ext_models import Action, FieldInfo, PageSnapshot
from jobpulse.form_analyzer import deterministic_fill, _match_to_available_options


class TestMatchToAvailableOptions:
    def test_exact_match(self):
        assert _match_to_available_options("Yes", ["Yes", "No"]) == "Yes"

    def test_case_insensitive(self):
        assert _match_to_available_options("yes", ["Yes", "No"]) == "Yes"

    def test_partial_contains(self):
        result = _match_to_available_options(
            "Yes", ["Yes, I am authorised to work in the UK", "No"]
        )
        assert result == "Yes, I am authorised to work in the UK"

    def test_abbreviation_expansion(self):
        result = _match_to_available_options(
            "United Kingdom", ["UK", "US", "India"]
        )
        assert result == "UK"

    def test_reverse_contains(self):
        result = _match_to_available_options(
            "Graduate Visa", ["Student Visa", "Graduate visa (Tier 4)", "Work Visa"]
        )
        assert result == "Graduate visa (Tier 4)"

    def test_no_match_returns_original(self):
        assert _match_to_available_options("Zebra", ["Yes", "No"]) == "Zebra"

    def test_empty_options(self):
        assert _match_to_available_options("Yes", []) == "Yes"

    def test_male_dropdown(self):
        result = _match_to_available_options(
            "Male", ["Male (he/him)", "Female (she/her)", "Non-binary", "Prefer not to say"]
        )
        assert result == "Male (he/him)"

    def test_no_preference_skips_placeholder(self):
        result = _match_to_available_options(
            "Yes", ["Select...", "Yes", "No"]
        )
        assert result == "Yes"


def _make_field(selector: str, label: str, input_type: str = "text",
                options: list[str] | None = None, role: str = "") -> FieldInfo:
    attrs = {}
    if role:
        attrs["role"] = role
    return FieldInfo(
        selector=selector, input_type=input_type, label=label,
        options=options or [], attributes=attrs,
    )


def _make_snapshot(fields: list[FieldInfo]) -> PageSnapshot:
    return PageSnapshot(url="https://example.com/apply", title="Apply", fields=fields)


class TestDeterministicFill:
    def test_first_name_fills(self):
        snap = _make_snapshot([_make_field("#fname", "First Name")])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert actions[0].type == "fill"
        assert actions[0].value

    def test_combobox_uses_fuzzy_match(self):
        snap = _make_snapshot([
            _make_field("#gender", "Gender", input_type="combobox",
                        options=["Male (he/him)", "Female (she/her)", "Non-binary"],
                        role="combobox"),
        ])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert actions[0].value == "Male (he/him)"
        assert actions[0].type == "fill_combobox"

    def test_right_to_work_fuzzy(self):
        snap = _make_snapshot([
            _make_field("#rtw", "Do you have the right to work in the UK?",
                        input_type="combobox",
                        options=["Yes, I have the right to work", "No, I require sponsorship"],
                        role="combobox"),
        ])
        actions = deterministic_fill(snap)
        assert len(actions) == 1
        assert "right to work" in actions[0].value.lower()

    def test_skips_already_filled(self):
        field = _make_field("#email", "Email")
        field.current_value = "test@example.com"
        snap = _make_snapshot([field])
        actions = deterministic_fill(snap)
        assert len(actions) == 0

    def test_skips_file_inputs(self):
        snap = _make_snapshot([_make_field("#cv", "Resume", input_type="file")])
        actions = deterministic_fill(snap)
        assert len(actions) == 0
