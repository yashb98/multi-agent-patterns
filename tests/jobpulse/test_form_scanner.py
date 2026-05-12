"""Tests for jobpulse.form_scanner — pure-function helpers only.

Per project policy: no mocking of the Playwright bridge. DOM-dependent
behavior (scan_form/scan_combobox_options/select_combobox_option/
multi-strategy scanner with mock_page fixtures) was removed 2026-05-03 —
real DOM scan paths are exercised in
`tests/jobpulse/integration/test_pipeline_live.py` against real Chrome.

What remains:
  - FormField / FormScanResult dataclass tests (real Python objects)
  - best_option_match / best_range_match pure-function tests
  - validate_field_scan pure-function tests
  - _merge_fields / _fillable_count pure-function tests
  - TestScanStrategyStorage — real FormExperienceDB on tmp_path
  - TestCookieButtonFilter — pure regex tests
"""

from jobpulse.form_engine.field_scanner import validate_field_scan
from jobpulse.form_scanner import (
    FormField,
    FormScanResult,
    best_option_match,
    best_range_match,
)


# ── FormField dataclass ──


class TestFormField:
    def test_empty_field_is_empty(self):
        f = FormField(label="Name", role="textbox")
        assert f.is_empty is True

    def test_filled_field_not_empty(self):
        f = FormField(label="Name", role="textbox", value="Yash")
        assert f.is_empty is False

    def test_required_empty_needs_fill(self):
        f = FormField(label="Name", role="textbox", required=True)
        assert f.needs_fill is True

    def test_required_filled_no_fill(self):
        f = FormField(label="Name", role="textbox", required=True, value="Yash")
        assert f.needs_fill is False

    def test_to_dict(self):
        f = FormField(label="City", role="combobox", value="London", required=True)
        d = f.to_dict()
        assert d["label"] == "City"
        assert d["role"] == "combobox"
        assert d["value"] == "London"
        assert d["required"] is True


# ── FormScanResult ──


class TestFormScanResult:
    def test_required_empty(self):
        fields = [
            FormField(label="A", role="textbox", required=True),
            FormField(label="B", role="textbox", required=True, value="val"),
            FormField(label="C", role="combobox", required=False),
        ]
        scan = FormScanResult(fields=fields)
        assert len(scan.required_empty) == 1
        assert scan.required_empty[0].label == "A"

    def test_field_types(self):
        fields = [
            FormField(label="A", role="textbox"),
            FormField(label="B", role="combobox"),
            FormField(label="C", role="textbox"),
        ]
        scan = FormScanResult(fields=fields)
        assert scan.field_types == ["combobox", "textbox"]

    def test_summary(self):
        fields = [FormField(label="Name", role="textbox", required=True)]
        scan = FormScanResult(fields=fields)
        s = scan.summary()
        assert "1 fields" in s
        assert "Name" in s


# ── best_option_match ──


class TestBestOptionMatch:
    def test_exact_match(self):
        assert best_option_match("Male", ["Female", "Male", "Other"]) == "Male"

    def test_case_insensitive(self):
        assert best_option_match("male", ["Female", "Male", "Other"]) == "Male"

    def test_alias_match(self):
        aliases = {"he/him": ("Him/His/Himself",)}
        result = best_option_match(
            "He/Him",
            ["Her/Hers/Herself", "Him/His/Himself", "They/Their/Themselves"],
            aliases=aliases,
        )
        assert result == "Him/His/Himself"

    def test_substring_match(self):
        result = best_option_match("Indian", ["Asian or Asian British - Indian", "White"])
        assert result == "Asian or Asian British - Indian"

    def test_no_match(self):
        assert best_option_match("Klingon", ["Male", "Female"]) is None

    def test_empty_options(self):
        assert best_option_match("Male", []) is None


# ── best_range_match ──


class TestBestRangeMatch:
    def test_matches_salary_range(self):
        options = ["£20,000 - £30,000", "£30,000 - £40,000", "£40,000 - £50,000"]
        assert best_range_match(35000, options) == "£30,000 - £40,000"

    def test_matches_age_range(self):
        options = ["18 - 24", "25 - 34", "35 - 44"]
        assert best_range_match(27, options) == "25 - 34"

    def test_no_match_out_of_range(self):
        options = ["£20,000 - £30,000", "£30,000 - £40,000"]
        assert best_range_match(50000, options) is None

    def test_boundary_inclusive(self):
        options = ["£40,000 - £50,000"]
        assert best_range_match(40000, options) == "£40,000 - £50,000"
        assert best_range_match(50000, options) == "£40,000 - £50,000"


def test_validate_scan_too_many_fields():
    fields = [{"label": f"field_{i}", "type": "text"} for i in range(35)]
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("linkedin")
    result = validate_field_scan(fields, strategy)
    assert not result["valid"]
    assert result["reason"] == "too_many_fields"


def test_validate_scan_zero_fields():
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("generic")
    result = validate_field_scan([], strategy)
    assert not result["valid"]
    assert result["reason"] == "zero_fields"


class TestMergeFields:
    """Tests for _merge_fields deduplication."""

    def test_dedup_by_label_and_type(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [
            {"label": "Name", "type": "text", "value": "Yash"},
            {"label": "Email", "type": "text", "value": ""},
        ]
        secondary = [
            {"label": "Name", "type": "text", "value": ""},
            {"label": "Phone", "type": "text", "value": ""},
        ]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 3
        labels = [f["label"] for f in merged]
        assert labels.count("Name") == 1
        assert merged[0]["value"] == "Yash"

    def test_case_insensitive_dedup(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "First Name", "type": "text"}]
        secondary = [{"label": "first name", "type": "text"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 1

    def test_different_types_not_deduped(self):
        from jobpulse.form_engine.field_scanner import _merge_fields

        primary = [{"label": "Resume", "type": "text"}]
        secondary = [{"label": "Resume", "type": "file"}]
        merged = _merge_fields(primary, secondary)
        assert len(merged) == 2


class TestFillableCount:

    def test_excludes_buttons(self):
        from jobpulse.form_engine.field_scanner import _fillable_count

        fields = [
            {"label": "Name", "type": "text"},
            {"label": "Submit", "type": "button"},
            {"label": "Email", "type": "text"},
        ]
        assert _fillable_count(fields) == 2

    def test_all_fillable(self):
        from jobpulse.form_engine.field_scanner import _fillable_count

        fields = [
            {"label": "A", "type": "text"},
            {"label": "B", "type": "checkbox"},
            {"label": "C", "type": "radio"},
        ]
        assert _fillable_count(fields) == 3


class TestScanStrategyStorage:

    def test_store_and_retrieve(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_scan_strategy("example.com", "dom_query", 12)

        pref = db.get_scan_strategy("example.com")
        assert pref is not None
        assert pref["preferred_strategy"] == "dom_query"
        assert pref["field_count"] == 12
        assert pref["sample_count"] == 1

    def test_update_increments_sample_count(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_scan_strategy("example.com", "a11y_tree", 8)
        db.store_scan_strategy("example.com", "a11y_tree", 10)

        pref = db.get_scan_strategy("example.com")
        assert pref["sample_count"] == 2
        assert pref["field_count"] == 10

    def test_strategy_switch_overwrites(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_scan_strategy("example.com", "a11y_tree", 5)
        db.store_scan_strategy("example.com", "dom_query", 15)

        pref = db.get_scan_strategy("example.com")
        assert pref["preferred_strategy"] == "dom_query"
        assert pref["field_count"] == 15

    def test_returns_none_for_unknown_domain(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        assert db.get_scan_strategy("unknown.com") is None


def test_validate_scan_excessive_duplicates():
    fields = [{"label": "Name", "type": "text"}] * 5
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("generic")
    result = validate_field_scan(fields, strategy)
    assert not result["valid"]
    assert result["reason"] == "duplicate_labels"


def test_validate_scan_passes_normal_form():
    fields = [
        {"label": "First Name", "type": "text"},
        {"label": "Last Name", "type": "text"},
        {"label": "Email", "type": "text"},
        {"label": "Phone", "type": "text"},
        {"label": "Resume", "type": "file"},
    ]
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("greenhouse")
    result = validate_field_scan(fields, strategy)
    assert result["valid"]


class TestCookieButtonFilter:
    """Verify cookie-related buttons are filtered from a11y tree scan results."""

    def test_cookie_button_pattern_matches(self):
        from jobpulse.form_scanner import _COOKIE_BUTTON_PATTERNS
        for text in ("Manage Cookies", "Reject All", "Allow All",
                     "Accept All Cookies", "Cookie Settings", "Customize Cookies",
                     "Alle akzeptieren", "Tout accepter", "Tout refuser"):
            assert _COOKIE_BUTTON_PATTERNS.search(text), f"Expected match for: {text}"

    def test_form_buttons_not_matched(self):
        from jobpulse.form_scanner import _COOKIE_BUTTON_PATTERNS
        for text in ("Submit Application", "Next", "Continue",
                     "Save & Continue", "Review Application", "Apply"):
            assert not _COOKIE_BUTTON_PATTERNS.search(text), f"False match for: {text}"


# ---------------------------------------------------------------------------
# Removed 2026-05-03: 30+ Playwright-bridge tests
#   - TestScanForm, TestScanComboboxOptions, TestSelectComboboxOption
#   - test_scan_form_* (partial/full tree paths)
#   - test_resolve_container_*
#   - TestMultiStrategyScanner (whole class)
#   - TestDomQueryRadioNameAttribute (whole class)
# All required AsyncMock/MagicMock for the Playwright Page / CDP session
# (Category B per project policy). Real DOM scan behavior is exercised in
# tests/jobpulse/integration/test_pipeline_live.py against real Chrome.
# ---------------------------------------------------------------------------
