"""Tests for NativeFormFiller — pure-function helpers only.

Per project policy: no mocking of the Playwright bridge (page/locator/driver).
DOM-dependent behavior (scan_fields, fill_by_label, click_navigation, full
fill pipeline, etc.) is exercised end-to-end against real Chrome via CDP in
`tests/jobpulse/integration/test_pipeline_live.py` and the `_real.py` files.

What remains here:
  - Semantic option matching (best_option_match, build_option_aliases,
    canonicalize_country_value) against real string inputs and real
    ProfileStore on tmp_path.
  - Screening-prompt construction with real ProfileStore.
  - Fuzzy label→profile-key matching (pure dict lookup).
  - Adaptive timing / fill-failure classification / strategy defaults.

Removed 2026-05-03: 60+ tests built on the `_make_filler(page_mock=...)`
fixture (Category B — Playwright bridge mock).
"""
from __future__ import annotations

import pytest

def test_best_option_match_prefers_united_kingdom_plus44():
    from jobpulse.native_form_filler import _best_option_match

    options = ["Ukraine (+380)", "United Kingdom (+44)", "United States (+1)"]
    assert _best_option_match("Phone Country", "UK", options) == "United Kingdom (+44)"
    assert _best_option_match("Country", "+44", options) == "United Kingdom (+44)"


def test_best_option_match_picks_student_visa_status():
    from jobpulse.native_form_filler import _best_option_match

    options = [
        "British or Irish Citizen",
        "Tier 4 (General) Student Visa",
        "Graduate Visa",
        "Other",
    ]
    value = "Student Visa; converting to Graduate Visa from 9 May 2026 (valid 2 years)"
    assert _best_option_match("Please select your right to work status", value, options) == (
        "Tier 4 (General) Student Visa"
    )


def test_best_option_match_understands_gender_and_asian_indian_intent():
    from jobpulse.native_form_filler import _best_option_match

    assert _best_option_match("I identify my gender as", "Male", ["Woman", "Man", "Non-binary"]) == "Man"
    assert _best_option_match(
        "What is your ethnicity?",
        "Asian or Asian British - Indian",
        [
            "Arab",
            "Asian (Indian, Pakistani, Bangladeshi, Chinese, Any other Asian background)",
            "White",
        ],
    ) == "Asian (Indian, Pakistani, Bangladeshi, Chinese, Any other Asian background)"


# ── _build_option_aliases ──


def _make_profile_store(tmp_path):
    """Create a ProfileStore with tmp_path DB for testing."""
    from shared.profile_store import ProfileStore

    db_path = tmp_path / "test_profile.db"
    key_path = tmp_path / ".test_key"
    store = ProfileStore(db_path=db_path, key_path=key_path)
    return store


def test_build_option_aliases_includes_gender_aliases():
    """Generic gender aliases are always present regardless of store."""
    from jobpulse.native_form_filler import _build_option_aliases

    aliases = _build_option_aliases()
    assert "man" in aliases["male"]
    assert "male" in aliases["man"]


def test_build_option_aliases_includes_country_aliases():
    """Generic country aliases map between abbreviations and full names."""
    from jobpulse.native_form_filler import _build_option_aliases

    aliases = _build_option_aliases()
    assert "united kingdom" in aliases["uk"]
    assert "uk" in aliases["united kingdom"]


def test_build_option_aliases_includes_ethnicity_aliases():
    """Ethnicity aliases cover common ATS form variations."""
    from jobpulse.native_form_filler import _build_option_aliases

    aliases = _build_option_aliases()
    # "asian indian" should map to common ATS long-form labels
    assert any("asian" in a for a in aliases.get("asian indian", ()))


def test_build_option_aliases_with_store_does_not_crash(tmp_path):
    """Passing a ProfileStore does not crash and returns the same generic aliases.

    The store parameter is a forward-compatible placeholder (Task 2).  It is
    intentionally unused today — generic tables cover all needed aliases.
    """
    from jobpulse.native_form_filler import _build_option_aliases

    store = _make_profile_store(tmp_path)
    store.set_screening_default("gender", "Male")
    store.set_screening_default("ethnicity", "Asian Indian")

    aliases_with_store = _build_option_aliases(store)
    aliases_without_store = _build_option_aliases(None)

    # Store is unused: output must be identical to the no-store path
    assert aliases_with_store == aliases_without_store
    store.close()


def test_build_option_aliases_no_store_still_works():
    """Without a store, _build_option_aliases returns generic aliases only."""
    from jobpulse.native_form_filler import _build_option_aliases

    aliases = _build_option_aliases(None)
    assert isinstance(aliases, dict)
    assert "male" in aliases
    assert "uk" in aliases


# ── _canonicalize_country_value with store ──


def test_canonicalize_country_value_uk_without_store():
    """Without a store, UK-style values still canonicalize to United Kingdom."""
    from jobpulse.native_form_filler import _canonicalize_country_value

    assert _canonicalize_country_value("Country", "UK") == "United Kingdom"
    assert _canonicalize_country_value("Country", "gb") == "United Kingdom"
    assert _canonicalize_country_value("Country", "+44") == "United Kingdom"


def test_canonicalize_country_value_with_store_uses_profile_country(tmp_path):
    """With a store, canonicalization reads user's country from profile location."""
    from jobpulse.native_form_filler import _canonicalize_country_value

    store = _make_profile_store(tmp_path)
    store.set_identity(location="Berlin, Germany")

    # When store has a different country, "de" should canonicalize to Germany
    assert _canonicalize_country_value("Country", "de", store=store) == "Germany"
    store.close()


def test_canonicalize_country_value_non_country_label_passes_through():
    """Non-country labels pass through without canonicalization."""
    from jobpulse.native_form_filler import _canonicalize_country_value

    assert _canonicalize_country_value("First Name", "UK") == "UK"


def test_canonicalize_country_value_unknown_value_passes_through():
    """Values that don't match any country alias pass through."""
    from jobpulse.native_form_filler import _canonicalize_country_value

    assert _canonicalize_country_value("Country", "Narnia") == "Narnia"


# ── _best_option_match with store kwarg ──


def test_best_option_match_accepts_store_kwarg(tmp_path):
    """_best_option_match accepts an optional store kwarg."""
    from jobpulse.native_form_filler import _best_option_match

    store = _make_profile_store(tmp_path)
    store.set_identity(location="London, United Kingdom")

    options = ["Ukraine (+380)", "United Kingdom (+44)", "United States (+1)"]
    result = _best_option_match("Phone Country", "UK", options, store=store)
    assert result == "United Kingdom (+44)"
    store.close()


def test_best_option_match_store_none_works():
    """_best_option_match still works when store=None (backward compat)."""
    from jobpulse.native_form_filler import _best_option_match

    options = ["Ukraine (+380)", "United Kingdom (+44)", "United States (+1)"]
    result = _best_option_match("Phone Country", "UK", options, store=None)
    assert result == "United Kingdom (+44)"


def test_screening_prompt_background_from_profile_store(tmp_path):
    """Screening prompt uses ProfileStore for relocation/commuting/right_to_work."""
    from jobpulse.native_form_filler import _screening_prompt_background

    store = _make_profile_store(tmp_path)
    store.set_identity(
        first_name="Jane", last_name="Doe",
        location="Berlin, Germany", education="MSc CS",
    )
    store.set_screening_default("relocation", "No")
    store.set_screening_default("commuting", "No")
    store.set_screening_default("right_to_work", "Yes")

    profile = {
        "first_name": "Jane", "last_name": "Doe",
        "education": "MSc CS", "location": "Berlin, Germany",
        "visa_status": "EU citizen", "notice_period": "2 weeks",
    }
    result = _screening_prompt_background(profile, store=store)

    assert "Willing to relocate: No." in result
    assert "Commuting: No." in result
    assert "Right to work Germany: Yes." in result
    # Hardcoded UK references should NOT appear
    assert "anywhere in the UK" not in result
    assert "any UK office" not in result
    store.close()


def test_screening_prompt_background_without_store_defaults_to_uk():
    """Without a store, screening prompt falls back to UK defaults."""
    from jobpulse.native_form_filler import _screening_prompt_background

    profile = {
        "first_name": "Test", "last_name": "User",
        "education": "BSc", "location": "London",
        "visa_status": "Graduate", "notice_period": "1 month",
    }
    result = _screening_prompt_background(profile)

    assert "Willing to relocate: Yes." in result
    assert "Commuting: Yes." in result
    assert "Right to work the UK: Yes." in result


def test_screening_prompt_profile_from_store(tmp_path):
    """_screening_prompt_profile reads from ProfileStore when available."""
    from jobpulse.native_form_filler import _screening_prompt_profile

    store = _make_profile_store(tmp_path)
    store.set_identity(
        first_name="Alice", last_name="Smith",
        location="Munich, Germany", education="PhD Physics",
    )
    store.set_sensitive("visa_status", "EU citizen", "work_auth")
    store.set_screening_default("notice_period", "3 months")

    result = _screening_prompt_profile(store=store)

    assert result["first_name"] == "Alice"
    assert result["last_name"] == "Smith"
    assert result["location"] == "Munich, Germany"
    assert result["education"] == "PhD Physics"
    assert result["visa_status"] == "EU citizen"
    assert result["notice_period"] == "3 months"
    store.close()
class TestFuzzyLabelMatcher:
    """Fuzzy label→profile_key matching handles unknown ATS label variants."""

    def test_standard_labels(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("first name") == "first_name"
        assert f("last name") == "last_name"
        assert f("email address") == "email"
        assert f("phone number") == "phone"

    def test_icims_labels(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("legal first name") == "first_name"
        assert f("legal last name") == "last_name"
        assert f("preferred first name") == "first_name"

    def test_international_labels(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("given name") == "first_name"
        assert f("family name") == "last_name"

    def test_address_labels(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("street address") == "address"
        assert f("address line 1") == "address"
        assert f("postal code") == "postcode"
        assert f("zip code") == "postcode"

    def test_ambiguous_single_tokens_rejected(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("type") is None
        assert f("number") is None
        assert f("name") is None

    def test_unknown_labels(self):
        from jobpulse.native_form_filler import _fuzzy_label_to_profile_key as f
        assert f("how did you hear about us") is None
        assert f("are you willing to relocate") is None
        assert f("company name") is None

# ── Adaptive timing ──


def test_platform_min_page_time_dict_removed():
    """_PLATFORM_MIN_PAGE_TIME should no longer exist."""
    import jobpulse.native_form_filler as mod
    assert not hasattr(mod, "_PLATFORM_MIN_PAGE_TIME")


def test_risk_delay_multiplier_removed():
    """NativeFormFiller should not have _risk_delay_multiplier after init."""
    import jobpulse.native_form_filler as mod
    from unittest.mock import AsyncMock, MagicMock
    page = AsyncMock()
    driver = MagicMock()
    filler = mod.NativeFormFiller(page, driver)
    assert not hasattr(filler, "_risk_delay_multiplier")


def test_fast_fill_env_var_skips_delays(monkeypatch):
    """When FAST_FILL=true, _get_adaptive_page_delay returns 0."""
    monkeypatch.setenv("FAST_FILL", "true")
    from jobpulse.native_form_filler import _get_adaptive_page_delay
    delay = _get_adaptive_page_delay("workday", None)
    assert delay == 0.0


def test_adaptive_delay_uses_measured_timing():
    """When timing_data is available, uses measured values."""
    from jobpulse.native_form_filler import _get_adaptive_page_delay
    timing = {"avg_fill_ms": 10000}
    delay = _get_adaptive_page_delay("workday", timing)
    assert delay == 11.0  # 10000/1000 * 1.1


def test_adaptive_delay_minimum_3_seconds():
    """Minimum delay is 3 seconds even with fast measured timing."""
    from jobpulse.native_form_filler import _get_adaptive_page_delay
    timing = {"avg_fill_ms": 1000}
    delay = _get_adaptive_page_delay("workday", timing)
    assert delay == 3.0  # max(1.1, 3.0)


def test_adaptive_delay_defaults_by_platform():
    """Without timing data, falls back to platform defaults."""
    from jobpulse.native_form_filler import _get_adaptive_page_delay
    assert _get_adaptive_page_delay("workday", None) == 8.0
    assert _get_adaptive_page_delay("linkedin", None) == 3.0
    assert _get_adaptive_page_delay("unknown", None) == 5.0


# ── Fill failure classification (Task 9) ──


def test_classify_no_field():
    from jobpulse.native_form_filler import _classify_fill_failure
    assert _classify_fill_failure({"success": False, "error": "No field for 'Name'"}) == "no_field"


def test_classify_blocked():
    from jobpulse.native_form_filler import _classify_fill_failure
    assert _classify_fill_failure({"success": False, "error": "Element is intercepted"}) == "blocked"


def test_classify_wrong_value():
    from jobpulse.native_form_filler import _classify_fill_failure
    assert _classify_fill_failure({"success": False, "value_mismatch": True}) == "wrong_value"


def test_classify_readonly():
    from jobpulse.native_form_filler import _classify_fill_failure
    assert _classify_fill_failure({"success": False, "error": "Element is readonly"}) == "readonly"


def test_classify_unknown():
    from jobpulse.native_form_filler import _classify_fill_failure
    assert _classify_fill_failure({"success": False, "error": "timeout"}) == "unknown"


# ── Strategy screening defaults (Task 10) ──


def test_strategy_screening_defaults_used():
    """Strategy screening_defaults() should be consulted for unresolved screening questions."""
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("linkedin")
    defaults = strategy.screening_defaults()
    assert "are you legally authorized to work" in defaults
    assert defaults["are you legally authorized to work"] == "yes"

