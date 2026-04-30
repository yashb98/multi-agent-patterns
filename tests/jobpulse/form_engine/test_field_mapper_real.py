"""Tests for field_mapper.py — real data, no mocks."""

import pytest


class TestIsScreeningLikeField:
    def test_select_is_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field

        assert is_screening_like_field({"type": "select", "label": "Gender"}) is True

    def test_question_mark_is_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field

        assert is_screening_like_field({"type": "text", "label": "Do you require visa sponsorship?"}) is True

    def test_text_no_question_not_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field

        assert is_screening_like_field({"type": "text", "label": "First Name"}) is False

    def test_radio_is_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field

        assert is_screening_like_field({"type": "radio", "label": "Work authorization"}) is True

    def test_checkbox_is_screening(self):
        from jobpulse.form_engine.field_mapper import is_screening_like_field

        assert is_screening_like_field({"type": "checkbox", "label": "I agree"}) is True


class TestCleanMapping:
    def test_removes_none_values(self):
        from jobpulse.form_engine.field_mapper import clean_mapping

        result = clean_mapping({"Name": "Test", "Empty": None})
        assert "Name" in result
        assert "Empty" not in result

    def test_strips_whitespace(self):
        from jobpulse.form_engine.field_mapper import clean_mapping

        result = clean_mapping({"Name": "  Test User  "})
        assert result["Name"] == "Test User"

    def test_removes_empty_strings(self):
        from jobpulse.form_engine.field_mapper import clean_mapping

        result = clean_mapping({"Name": "Test", "Blank": "", "Space": "   "})
        assert "Blank" not in result
        assert "Space" not in result

    def test_empty_mapping(self):
        from jobpulse.form_engine.field_mapper import clean_mapping

        assert clean_mapping({}) == {}


class TestFuzzyCustomAnswer:
    def test_substring_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer

        result = _fuzzy_custom_answer("email address", {"email": "test@example.com"})
        assert result == "test@example.com"

    def test_reverse_substring_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer

        result = _fuzzy_custom_answer("email", {"email address": "test@example.com"})
        assert result == "test@example.com"

    def test_no_match(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer

        result = _fuzzy_custom_answer("zzzzz_nothing", {"email": "test@example.com"})
        assert result is None

    def test_skips_internal_keys(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer

        result = _fuzzy_custom_answer("stream", {"_stream": "true", "name": "Test"})
        assert result is None

    def test_diversity_keyword_fallback(self):
        from jobpulse.form_engine.field_mapper import _fuzzy_custom_answer

        result = _fuzzy_custom_answer("what is your gender identity", {"gender": "Male"})
        assert result == "Male"
