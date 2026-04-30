"""Tests for unified_scanner.py — static helpers, no browser."""

import pytest
from typing import Any


class TestNormalizeInputType:
    def test_email_to_text(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("email") == "text"

    def test_tel_to_text(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("tel") == "text"

    def test_combobox_to_select(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("combobox") == "select"

    def test_textbox_to_text(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("textbox") == "text"

    def test_unknown_passes_through(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("file") == "file"

    def test_select_one_to_select(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._normalize_input_type("select-one") == "select"


class TestSelectorQuality:
    def test_id_selector_highest(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._selector_quality("#email-input") == 3

    def test_attribute_id_highest(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._selector_quality('[id="email"]') == 3

    def test_short_name_attribute(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._selector_quality('[name="email"]') == 2

    def test_text_locator_low(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._selector_quality(':has-text("Email")') == 1

    def test_id_beats_generic(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        score_id = UnifiedFieldScanner._selector_quality("#email")
        score_generic = UnifiedFieldScanner._selector_quality("input")
        assert score_id > score_generic


class TestBboxOverlap:
    def test_no_overlap(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        a = {"x": 0, "y": 0, "width": 100, "height": 50}
        b = {"x": 200, "y": 200, "width": 100, "height": 50}
        assert UnifiedFieldScanner._bbox_overlap(a, b) == 0.0

    def test_full_overlap(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        a = {"x": 0, "y": 0, "width": 100, "height": 50}
        overlap = UnifiedFieldScanner._bbox_overlap(a, a)
        assert overlap > 0.99

    def test_none_bbox_returns_one(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._bbox_overlap(None, None) == 1.0

    def test_one_none_returns_one(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        a = {"x": 0, "y": 0, "width": 100, "height": 50}
        assert UnifiedFieldScanner._bbox_overlap(a, None) == 1.0

    def test_partial_overlap(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        a = {"x": 0, "y": 0, "width": 100, "height": 100}
        b = {"x": 50, "y": 50, "width": 100, "height": 100}
        overlap = UnifiedFieldScanner._bbox_overlap(a, b)
        assert 0.0 < overlap < 1.0


class TestParseAxNode:
    def test_parses_textbox(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        node = {
            "role": {"value": "textbox"},
            "name": {"value": "Email"},
            "value": {"value": "test@example.com"},
            "properties": [
                {"name": "required", "value": {"value": True}},
            ],
        }
        role, name, value, props = UnifiedFieldScanner._parse_ax_node(node)
        assert role == "textbox"
        assert name == "Email"
        assert value == "test@example.com"
        assert props["required"] is True

    def test_empty_node(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        role, name, value, props = UnifiedFieldScanner._parse_ax_node({})
        assert role == ""
        assert name == ""
        assert value == ""
        assert props == {}

    def test_filters_properties(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        node = {
            "role": {"value": "checkbox"},
            "name": {"value": "Agree"},
            "value": {"value": ""},
            "properties": [
                {"name": "checked", "value": {"value": True}},
                {"name": "disabled", "value": {"value": False}},
                {"name": "unknown_prop", "value": {"value": "x"}},
            ],
        }
        _, _, _, props = UnifiedFieldScanner._parse_ax_node(node)
        assert "checked" in props
        assert "disabled" in props
        assert "unknown_prop" not in props


class TestIsNoiseLabel:
    def test_navigation_labels_are_noise(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._is_noise_label("Home") is True
        assert UnifiedFieldScanner._is_noise_label("Jobs") is True
        assert UnifiedFieldScanner._is_noise_label("Messaging") is True

    def test_form_labels_are_not_noise(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._is_noise_label("First Name") is False
        assert UnifiedFieldScanner._is_noise_label("Email Address") is False

    def test_case_insensitive(self):
        from jobpulse.form_engine.unified_scanner import UnifiedFieldScanner

        assert UnifiedFieldScanner._is_noise_label("HOME") is True
        assert UnifiedFieldScanner._is_noise_label("notifications") is True
