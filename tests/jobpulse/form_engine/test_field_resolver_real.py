"""Tests for field_resolver.py — real data, no mocks."""

import pytest


class TestFuzzyLabelToProfileKey:
    def test_first_name(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key

        assert fuzzy_label_to_profile_key("First Name") == "first_name"

    def test_email_address(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key

        assert fuzzy_label_to_profile_key("Email Address") == "email"

    def test_phone_number(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key

        assert fuzzy_label_to_profile_key("Phone Number") == "phone"

    def test_linkedin_profile(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key

        result = fuzzy_label_to_profile_key("LinkedIn")
        assert result == "linkedin"

    def test_unknown_label(self):
        from jobpulse.form_engine.field_resolver import fuzzy_label_to_profile_key

        assert fuzzy_label_to_profile_key("zzzz_unknown_field_zzzz") is None


class TestCanonicalizeCountryValue:
    def test_uk_abbreviation(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value

        result = canonicalize_country_value("Country", "uk")
        assert result == "United Kingdom"

    def test_full_name_passthrough(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value

        result = canonicalize_country_value("Country", "United Kingdom")
        assert result == "United Kingdom"

    def test_non_country_label_passthrough(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value

        result = canonicalize_country_value("City", "London")
        assert result == "London"

    def test_us_abbreviation(self):
        from jobpulse.form_engine.field_resolver import canonicalize_country_value

        result = canonicalize_country_value("Country", "us")
        assert result == "United States"


class TestBuildOptionAliases:
    def test_returns_dict(self):
        from jobpulse.form_engine.field_resolver import build_option_aliases

        aliases = build_option_aliases()
        assert isinstance(aliases, dict)
        assert len(aliases) > 0

    def test_contains_country_aliases(self):
        from jobpulse.form_engine.field_resolver import build_option_aliases

        aliases = build_option_aliases()
        assert "uk" in aliases or "united kingdom" in aliases


class TestLabelMappingStore:
    def test_store_and_retrieve(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore

        store = LabelMappingStore(_db_path=str(tmp_path / "labels.db"))
        store.learn("Email Address", "email")
        result = store.get("Email Address")
        assert result == "email"

    def test_miss_returns_none(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore

        store = LabelMappingStore(_db_path=str(tmp_path / "labels.db"))
        assert store.get("nonexistent") is None

    def test_seed_mappings(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore

        store = LabelMappingStore(_db_path=str(tmp_path / "labels.db"))
        store.seed_mappings({"first name": "first_name", "email": "email"})
        assert store.get("first name") == "first_name"
        assert store.get("email") == "email"

    def test_case_insensitive(self, tmp_path):
        from jobpulse.form_engine.field_resolver import LabelMappingStore

        store = LabelMappingStore(_db_path=str(tmp_path / "labels.db"))
        store.learn("Email Address", "email")
        assert store.get("email address") == "email"


class TestGetFieldGap:
    def test_short_label_returns_small_delay(self):
        from jobpulse.form_engine.field_resolver import get_field_gap

        gap = get_field_gap("Name")
        assert 0.3 <= gap < 1.0

    def test_long_label_returns_larger_delay(self):
        from jobpulse.form_engine.field_resolver import get_field_gap

        gap = get_field_gap("Please describe your experience with machine learning in detail")
        assert gap >= 0.8

    def test_empty_label(self):
        from jobpulse.form_engine.field_resolver import get_field_gap

        gap = get_field_gap("")
        assert gap >= 0.3
