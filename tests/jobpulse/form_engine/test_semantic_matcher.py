"""Tests for semantic option matching — all option lists are real data
captured from actual ATS form fills."""

import pytest


class TestExactMatch:
    def test_exact_case_insensitive(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Male", "Female", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("male", options) == "Male"
        assert semantic_option_match("FEMALE", options) == "Female"

    def test_exact_with_whitespace(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [" Yes ", "No"]
        assert semantic_option_match("Yes", options) == " Yes "


class TestCanonicalAliases:
    def test_gender_male_to_man(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("male", options) == "Man"

    def test_gender_female_to_woman(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("female", options) == "Woman"

    def test_boolean_yes_authorized(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Yes, I am authorized", "No, I am not authorized"]
        assert semantic_option_match("yes", options) == "Yes, I am authorized"

    def test_ethnicity_indian(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [
            "White", "Mixed", "Asian or Asian British - Indian",
            "Asian or Asian British - Pakistani", "Black or Black British",
            "Prefer not to say",
        ]
        assert semantic_option_match("indian", options) == "Asian or Asian British - Indian"

    def test_visa_graduate(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Tier 2 (General)", "Tier 4 Graduate visa", "Indefinite Leave", "British Citizen"]
        assert semantic_option_match("graduate visa", options) == "Tier 4 Graduate visa"

    def test_notice_period_1_month(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Immediately", "Less than 30 days", "1-3 months", "3+ months"]
        assert semantic_option_match("1 month", options) == "Less than 30 days"

    def test_experience_years(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["0-1 years", "2-3 years", "3-5 years", "5+ years"]
        assert semantic_option_match("2 years", options) == "2-3 years"


class TestNumericRange:
    def test_salary_range_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["£20,000 - £30,000", "£30,000 - £40,000", "£40,000 - £50,000", "£50,000+"]
        assert semantic_option_match("35000", options, numeric_value=35000) == "£30,000 - £40,000"

    def test_age_range_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["18 - 24", "25 - 34", "35 - 44", "45+"]
        assert semantic_option_match("27", options, numeric_value=27) == "25 - 34"


class TestTokenOverlap:
    def test_partial_match_via_tokens(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [
            "I have a valid UK work permit",
            "I require sponsorship",
            "I am a British citizen",
        ]
        assert semantic_option_match("valid UK work permit", options) == "I have a valid UK work permit"


class TestNoMatch:
    def test_returns_none_when_no_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Red", "Blue", "Green"]
        assert semantic_option_match("purple", options) is None

    def test_returns_none_for_empty_options(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        assert semantic_option_match("yes", []) is None


class TestCheckboxIntent:
    def test_privacy_consent_check(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("I agree to the privacy policy") is True

    def test_terms_and_conditions(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("I acknowledge the terms and conditions") is True

    def test_marketing_opt_out(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Send me marketing emails and newsletters") is False

    def test_promotional_offers(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Opt in to promotional offers") is False

    def test_required_checkbox_checked(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Some custom checkbox", required=True) is True

    def test_ambiguous_returns_none(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Follow this company") is None
