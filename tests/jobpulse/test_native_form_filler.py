"""Tests for NativeFormFiller — Playwright native pipeline."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _empty_locator():
    """Return a locator-like mock that reports 0 elements."""
    loc = MagicMock()
    loc.count = AsyncMock(return_value=0)
    loc.all = AsyncMock(return_value=[])
    return loc


def _make_filler(page_mock=None, driver_mock=None):
    """Create a NativeFormFiller with mocked dependencies."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = page_mock or MagicMock()
    if not isinstance(page.evaluate, AsyncMock):
        page.evaluate = AsyncMock(return_value=[])
    if not isinstance(page.frame, AsyncMock):
        page.frame = MagicMock(return_value=None)
    page.get_by_role = MagicMock(side_effect=lambda *a, **kw: _empty_locator())
    driver = driver_mock or AsyncMock()
    driver.page = page
    return NativeFormFiller(page=page, driver=driver)


# ── _get_accessible_name ──


@pytest.mark.asyncio
async def test_get_accessible_name_returns_label():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="Email Address")

    result = await filler._get_accessible_name(locator)
    assert result == "Email Address"
    locator.evaluate.assert_called_once()


@pytest.mark.asyncio
async def test_get_accessible_name_empty_fallback():
    filler = _make_filler()
    locator = AsyncMock()
    locator.evaluate = AsyncMock(return_value="")

    result = await filler._get_accessible_name(locator)
    assert result == ""


# ── _scan_fields ──


@pytest.mark.asyncio
async def test_scan_fields_text_inputs():
    """Scans textbox role elements and returns field dicts."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    textbox = AsyncMock()
    textbox.input_value = AsyncMock(return_value="")
    textbox.get_attribute = AsyncMock(return_value=None)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[textbox])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role

    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    from jobpulse.form_scanner import FormScanResult
    with patch("jobpulse.form_scanner.scan_form", new_callable=AsyncMock,
               return_value=FormScanResult(fields=[])), \
         patch("jobpulse.form_engine.field_scanner.get_accessible_name",
               new_callable=AsyncMock, return_value="First Name"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["label"] == "First Name"
    assert fields[0]["type"] == "text"
    assert fields[0]["locator"] is textbox


@pytest.mark.asyncio
async def test_scan_fields_select_with_options():
    """Scans combobox (select) elements and captures options."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    select_el = AsyncMock()
    select_el.input_value = AsyncMock(return_value="")
    select_el.evaluate = AsyncMock(return_value="select")
    option_locator = MagicMock()
    option_locator.all_text_contents = AsyncMock(return_value=["USA", "UK", "Canada"])
    select_el.locator = lambda sel: option_locator

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[select_el])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    from jobpulse.form_scanner import FormScanResult
    with patch("jobpulse.form_scanner.scan_form", new_callable=AsyncMock,
               return_value=FormScanResult(fields=[])), \
         patch("jobpulse.form_engine.field_scanner.get_accessible_name",
               new_callable=AsyncMock, return_value="Country"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "select"
    assert fields[0]["options"] == ["USA", "UK", "Canada"]


@pytest.mark.asyncio
async def test_scan_fields_checkbox():
    """Scans checkbox elements with checked state."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)

    textbox_group = AsyncMock()
    textbox_group.all = AsyncMock(return_value=[])
    combobox_group = AsyncMock()
    combobox_group.all = AsyncMock(return_value=[])
    radiogroup_group = AsyncMock()
    radiogroup_group.all = AsyncMock(return_value=[])
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])

    def _get_by_role(role, **kwargs):
        return {
            "textbox": textbox_group,
            "combobox": combobox_group,
            "radiogroup": radiogroup_group,
            "checkbox": checkbox_group,
        }.get(role, AsyncMock(all=AsyncMock(return_value=[])))

    page.get_by_role = _get_by_role
    textarea_loc = MagicMock()
    textarea_loc.all = AsyncMock(return_value=[])
    file_loc = MagicMock()
    file_loc.all = AsyncMock(return_value=[])
    page.locator = lambda sel: textarea_loc if "textarea" in sel else file_loc

    from jobpulse.form_scanner import FormScanResult
    with patch("jobpulse.form_scanner.scan_form", new_callable=AsyncMock,
               return_value=FormScanResult(fields=[])), \
         patch("jobpulse.form_engine.field_scanner.get_accessible_name",
               new_callable=AsyncMock, return_value="Agree to terms"):
        fields = await filler._scan_fields()

    assert len(fields) == 1
    assert fields[0]["type"] == "checkbox"
    assert fields[0]["checked"] is False


# ── _fill_by_label ──


@pytest.mark.asyncio
async def test_fill_by_label_text_input():
    """Fills a text field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="john@example.com")

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Email", "john@example.com")

    assert result["success"] is True
    el.fill.assert_called_once_with("john@example.com")


@pytest.mark.asyncio
async def test_fill_by_label_select():
    """Fills a select field found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(side_effect=["input", "select", "United States"])
    el.get_attribute = AsyncMock(return_value=None)
    el.select_option = AsyncMock()
    option_locator = AsyncMock()
    option_locator.all_text_contents = AsyncMock(return_value=["United States", "Canada", "UK"])
    el.locator = MagicMock(return_value=option_locator)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country", "United States")

    assert result["success"] is True
    el.select_option.assert_called_once_with(label="United States", timeout=5000)


@pytest.mark.asyncio
async def test_fill_by_label_select_reports_unverified_when_value_does_not_stick():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(side_effect=["select", "select", "Canada"])
    el.get_attribute = AsyncMock(return_value=None)
    el.select_option = AsyncMock()
    option_locator = AsyncMock()
    option_locator.all_text_contents = AsyncMock(return_value=["United States", "Canada"])
    el.locator = MagicMock(return_value=option_locator)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country", "United States")

    assert result["success"] is True
    assert result["value_verified"] is False
    assert result["actual_value"] == "Canada"


@pytest.mark.asyncio
async def test_fill_by_label_not_found():
    """Returns error when no field matches label or placeholder."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=empty_locator)

    with patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Nonexistent", "value")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_fill_by_label_checkbox():
    """Checks a checkbox found by label."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value="checkbox")
    el.check = AsyncMock()
    el.is_checked = AsyncMock(return_value=True)

    label_locator = MagicMock()
    label_locator.count = AsyncMock(return_value=1)
    label_locator.nth = MagicMock(return_value=el)
    label_locator.first = el

    page.get_by_label = MagicMock(return_value=label_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("I agree", "yes")

    assert result["success"] is True
    el.check.assert_called_once()


@pytest.mark.asyncio
async def test_fill_by_label_placeholder_fallback():
    """Falls back to placeholder when label locator finds nothing."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    el = AsyncMock()
    el.evaluate = AsyncMock(return_value="input")
    el.get_attribute = AsyncMock(return_value=None)
    el.fill = AsyncMock()
    el.input_value = AsyncMock(return_value="test")

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)

    placeholder_locator = MagicMock()
    placeholder_locator.count = AsyncMock(return_value=1)
    placeholder_locator.nth = MagicMock(return_value=el)
    placeholder_locator.first = el

    page.get_by_label = MagicMock(return_value=empty_locator)
    page.get_by_placeholder = MagicMock(return_value=placeholder_locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Search", "test")

    assert result["success"] is True
    page.get_by_placeholder.assert_called_once()


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


@pytest.mark.asyncio
async def test_normalize_phone_value_for_split_uk_widget(tmp_path):
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    store = _make_profile_store(tmp_path)
    store.set_identity(location="London, United Kingdom")
    filler._profile_store = store

    plus44 = MagicMock()
    plus44.count = AsyncMock(return_value=1)
    page.get_by_text = MagicMock(return_value=plus44)

    assert await filler._normalize_phone_value("Phone", "07909 445288") == "7909445288"
    store.close()


@pytest.mark.asyncio
async def test_fill_special_widget_sets_country_options_to_united_kingdom(tmp_path):
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    store = _make_profile_store(tmp_path)
    store.set_identity(location="London, United Kingdom")
    filler._profile_store = store

    button = AsyncMock()
    button.count = AsyncMock(return_value=1)
    button.click = AsyncMock()
    button.get_attribute = AsyncMock(return_value="Change country, selected United Kingdom (+44)")

    search = AsyncMock()
    search.fill = AsyncMock()
    search.press = AsyncMock()
    search.count = AsyncMock(return_value=1)

    option = AsyncMock()
    option.count = AsyncMock(return_value=1)
    option.click = AsyncMock()

    def locator(selector, **kwargs):
        if selector == "button.iti__selected-country":
            return MagicMock(first=button)
        if selector == "#iti-0__search-input":
            return MagicMock(first=search)
        if selector == "#iti-0__country-listbox li":
            return MagicMock(first=option)
        raise AssertionError(selector)

    page.locator = MagicMock(side_effect=locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country Options", "UK")

    assert result["success"] is True
    assert result["value_verified"] is True
    search.fill.assert_any_call("United Kingdom")
    option.click.assert_called_once()
    store.close()


# ── _screening_prompt with ProfileStore ──


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


@pytest.mark.asyncio
async def test_normalize_phone_value_uses_profile_country(tmp_path):
    """Phone normalization uses ProfileStore country code instead of hardcoded +44."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    store = _make_profile_store(tmp_path)
    store.set_identity(location="Berlin, Germany")
    filler._profile_store = store

    # Simulate a page with +49 country code widget
    plus49 = MagicMock()
    plus49.count = AsyncMock(return_value=1)
    page.get_by_text = MagicMock(return_value=plus49)

    # German number starting with 0 should strip leading 0 for split widget
    result = await filler._normalize_phone_value("Phone", "0171 1234567")
    assert result == "1711234567"

    # Without split widget, should prepend +49
    plus49.count = AsyncMock(return_value=0)
    result = await filler._normalize_phone_value("Phone", "0171 1234567")
    assert result == "+491711234567"
    store.close()


@pytest.mark.asyncio
async def test_fill_special_widget_uses_profile_country(tmp_path):
    """Special widget fills the country from ProfileStore instead of hardcoded UK."""
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    store = _make_profile_store(tmp_path)
    store.set_identity(location="Berlin, Germany")
    filler._profile_store = store

    button = AsyncMock()
    button.count = AsyncMock(return_value=1)
    button.click = AsyncMock()
    button.get_attribute = AsyncMock(return_value="Change country, selected Germany (+49)")

    search = AsyncMock()
    search.fill = AsyncMock()
    search.press = AsyncMock()

    option = AsyncMock()
    option.count = AsyncMock(return_value=1)
    option.click = AsyncMock()

    def locator(selector, **kwargs):
        if selector == "button.iti__selected-country":
            return MagicMock(first=button)
        if selector == "#iti-0__search-input":
            return MagicMock(first=search)
        if selector == "#iti-0__country-listbox li":
            return MagicMock(first=option)
        raise AssertionError(selector)

    page.locator = MagicMock(side_effect=locator)

    with patch.object(filler, "_smart_scroll", new_callable=AsyncMock), \
         patch.object(filler, "_move_mouse_to", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock):
        result = await filler._fill_by_label("Country Options", "DE")

    assert result["success"] is True
    assert result["value_set"] == "Germany (+49)"
    search.fill.assert_any_call("Germany")
    option.click.assert_called_once()
    store.close()


# ── map_fields (LLM Call 1) ──


@pytest.mark.asyncio
async def test_map_fields_basic():
    """Maps profile data to form fields via LLM."""
    from jobpulse.form_engine.field_mapper import map_fields

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Phone", "type": "text", "value": "", "required": False},
        {"label": "Resume", "type": "file"},
    ]
    profile = {"email": "test@example.com", "phone": "+44123456789"}

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"Email": "test@example.com", "Phone": "+44123456789"}'

    with patch("jobpulse.form_engine.field_mapper.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result, _ = await map_fields("", fields, profile, {}, "greenhouse", False, "")

    assert result == {"Email": "test@example.com", "Phone": "+44123456789"}


@pytest.mark.asyncio
async def test_map_fields_skips_file_fields():
    """File fields are excluded from the LLM prompt."""
    from jobpulse.form_engine.field_mapper import map_fields

    fields = [
        {"label": "Resume", "type": "file"},
    ]

    result, _ = await map_fields("", fields, {}, {}, "linkedin", False, "")
    assert result == {}


@pytest.mark.asyncio
async def test_map_fields_includes_options():
    """Text field options are passed in the LLM prompt."""
    from jobpulse.form_engine.field_mapper import map_fields

    fields = [
        {"label": "Preferred Location", "type": "text", "options": ["USA", "UK"], "value": ""},
    ]

    captured_prompt = {}

    def fake_cognitive_llm_call(*, task, domain, stakes):
        captured_prompt["task"] = task
        return '{"Preferred Location": "UK"}'

    with patch("shared.agents.cognitive_llm_call", side_effect=fake_cognitive_llm_call):
        result, _ = await map_fields("", fields, {}, {}, "greenhouse", False, "")

    assert result == {"Preferred Location": "UK"}
    assert "USA" in captured_prompt["task"]


@pytest.mark.asyncio
async def test_map_fields_keeps_seed_mapping_and_leaves_question_fields_for_screening():
    from jobpulse.form_engine.field_mapper import map_fields

    fields = [
        {"label": "Website", "type": "text", "value": ""},
        {"label": "How did you hear about this role?", "type": "text", "value": ""},
    ]
    profile = {"portfolio": "https://yashbishnoi.io"}

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"Website": "", "How did you hear about this role?": "LinkedIn"}'
    )

    with patch("jobpulse.form_engine.field_mapper.try_cached_mapping", return_value=None), \
         patch("jobpulse.form_engine.field_mapper.get_openai_client") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = mock_response
        result, _ = await map_fields("", fields, profile, {}, "linkedin", False, "")

    assert result == {"Website": "https://yashbishnoi.io"}


# ── screen_questions (LLM Call 2) ──


@pytest.mark.asyncio
async def test_screen_questions_basic():
    from jobpulse.form_engine.field_mapper import screen_questions

    unresolved = [
        {"label": "Are you authorized to work in the UK?", "type": "radio",
         "options": ["Yes", "No"]},
        {"label": "Expected salary", "type": "text"},
    ]

    def fake_answer(question, field=None, job_context=None):
        answers = {
            "Are you authorized to work in the UK?": "Yes",
            "Expected salary": "50000",
        }
        return {"answer": answers.get(question, ""), "confidence": 1.0, "source": "mock"}

    with patch("jobpulse.screening_pipeline.ScreeningPipeline") as MockPipeline:
        MockPipeline.return_value.answer = fake_answer
        result, _ = await screen_questions(
            unresolved, {"title": "SWE at Acme"}, None, "",
        )

    assert result["Are you authorized to work in the UK?"] == "Yes"
    assert result["Expected salary"] == "50000"


@pytest.mark.asyncio
async def test_screen_questions_includes_options():
    from jobpulse.form_engine.field_mapper import screen_questions

    unresolved = [
        {"label": "Years of experience", "type": "select",
         "options": ["0-1", "2-3", "4-5", "6+"]},
    ]

    captured_fields = []

    def fake_answer(question, field=None, job_context=None):
        captured_fields.append(field)
        return {"answer": "2-3", "confidence": 1.0, "source": "mock"}

    with patch("jobpulse.screening_pipeline.ScreeningPipeline") as MockPipeline:
        MockPipeline.return_value.answer = fake_answer
        result, _ = await screen_questions(
            unresolved, {"title": "Data Analyst"}, None, "",
        )

    assert result["Years of experience"] == "2-3"
    assert captured_fields[0]["options"] == ["0-1", "2-3", "4-5", "6+"]


# ── review_form (LLM Call 3) ──

import base64


@pytest.mark.asyncio
async def test_review_form_pass():
    from jobpulse.form_engine.field_mapper import review_form

    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")

    mock_response = MagicMock()
    mock_response.output_text = '{"pass": true}'

    with patch("jobpulse.form_engine.field_mapper.get_openai_client") as mock_openai:
        mock_openai.return_value.responses.create.return_value = mock_response
        result, _ = await review_form(page)

    assert result["pass"] is True


@pytest.mark.asyncio
async def test_review_form_fail_with_issues():
    from jobpulse.form_engine.field_mapper import review_form

    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG fake")

    mock_response = MagicMock()
    mock_response.output_text = '{"pass": false, "issues": ["Phone empty", "Wrong country"]}'

    with patch("jobpulse.form_engine.field_mapper.get_openai_client") as mock_openai:
        mock_openai.return_value.responses.create.return_value = mock_response
        result, _ = await review_form(page)

    assert result["pass"] is False
    assert len(result["issues"]) == 2


@pytest.mark.asyncio
async def test_review_form_sends_image():
    """Screenshot is sent as base64 input_image in the Responses API call."""
    from jobpulse.form_engine.field_mapper import review_form

    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG test")

    mock_response = MagicMock()
    mock_response.output_text = '{"pass": true}'

    with patch("jobpulse.form_engine.field_mapper.get_openai_client") as mock_openai:
        mock_openai.return_value.responses.create.return_value = mock_response
        await review_form(page)

    call_kwargs = mock_openai.return_value.responses.create.call_args[1]
    content = call_kwargs["input"][0]["content"]
    assert isinstance(content, list)
    image_parts = [p for p in content if p.get("type") == "input_image"]
    assert len(image_parts) == 1


# ── upload_files ──


@pytest.mark.asyncio
async def test_upload_files_cv_only():
    from jobpulse.form_engine.file_uploader import upload_files

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "upload resume"},
    ])
    fi = MagicMock()
    locator_mock = MagicMock(first=fi, nth=MagicMock(return_value=fi))
    page.locator = MagicMock(return_value=locator_mock)
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return ""

    with patch("jobpulse.form_engine.file_uploader.upload_pdf", new_callable=AsyncMock) as mock_upload:
        await upload_files(page, "/tmp/cv.pdf", None, None, _mock_name)

    mock_upload.assert_called_once_with(fi, "/tmp/cv.pdf")


@pytest.mark.asyncio
async def test_upload_files_cv_and_cl():
    from jobpulse.form_engine.file_uploader import upload_files

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "upload resume"},
        {"idx": 1, "id": "cover_letter", "name": "", "label": "upload cover letter"},
    ])
    fi_cv = MagicMock()
    fi_cl = MagicMock()
    cv_locator = MagicMock(first=fi_cv)
    cl_locator = MagicMock(first=fi_cl)

    def _locator_factory(sel):
        if "resume" in sel:
            return cv_locator
        if "cover_letter" in sel:
            return cl_locator
        return MagicMock(first=MagicMock())

    page.locator = MagicMock(side_effect=_locator_factory)
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return ""

    with patch("jobpulse.form_engine.file_uploader.upload_pdf", new_callable=AsyncMock) as mock_upload:
        await upload_files(page, "/tmp/cv.pdf", "/tmp/cl.pdf", None, _mock_name)

    assert mock_upload.call_count == 2
    mock_upload.assert_any_call(fi_cv, "/tmp/cv.pdf")
    mock_upload.assert_any_call(fi_cl, "/tmp/cl.pdf")


@pytest.mark.asyncio
async def test_upload_files_skips_autofill():
    from jobpulse.form_engine.file_uploader import upload_files

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"idx": 0, "id": "resume", "name": "", "label": "autofill from resume"},
    ])
    page.locator = MagicMock(return_value=MagicMock(nth=MagicMock()))
    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return ""

    with patch("jobpulse.form_engine.file_uploader.upload_pdf", new_callable=AsyncMock) as mock_upload:
        await upload_files(page, "/tmp/cv.pdf", None, None, _mock_name)

    mock_upload.assert_not_called()


# ── check_consent ──


@pytest.mark.asyncio
async def test_check_consent_checks_unchecked():
    from jobpulse.form_engine.file_uploader import check_consent

    page = MagicMock()
    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return "I agree to the terms"

    with patch("jobpulse.form_engine.file_uploader.check_consent_selects", new_callable=AsyncMock):
        await check_consent(page, _mock_name)

    cb.check.assert_called_once()


@pytest.mark.asyncio
async def test_check_consent_skips_non_consent():
    from jobpulse.form_engine.file_uploader import check_consent

    page = MagicMock()
    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=False)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return "Subscribe to newsletter"

    with patch("jobpulse.form_engine.file_uploader.check_consent_selects", new_callable=AsyncMock):
        await check_consent(page, _mock_name)

    cb.check.assert_not_called()


@pytest.mark.asyncio
async def test_check_consent_skips_already_checked():
    from jobpulse.form_engine.file_uploader import check_consent

    page = MagicMock()
    cb = AsyncMock()
    cb.is_checked = AsyncMock(return_value=True)
    cb.check = AsyncMock()

    checkbox_group = AsyncMock()
    checkbox_group.all = AsyncMock(return_value=[cb])
    page.get_by_role = MagicMock(return_value=checkbox_group)

    async def _mock_name(loc):
        return "I accept privacy policy"

    with patch("jobpulse.form_engine.file_uploader.check_consent_selects", new_callable=AsyncMock):
        await check_consent(page, _mock_name)

    cb.check.assert_not_called()


@pytest.mark.asyncio
async def test_check_consent_selects_i_accept():
    """iCIMS GDPR pattern: select dropdown with 'I accept' option."""
    from jobpulse.form_engine.file_uploader import check_consent_selects

    page = MagicMock()
    option_loc = MagicMock()
    option_loc.all_text_contents = AsyncMock(
        return_value=["— Make a Selection —", "I accept"],
    )
    select_loc = MagicMock()
    select_loc.locator = MagicMock(return_value=option_loc)
    select_loc.evaluate = AsyncMock(return_value="— Make a Selection —")
    select_loc.select_option = AsyncMock()

    select_group = MagicMock()
    select_group.all = AsyncMock(return_value=[select_loc])
    page.locator = MagicMock(return_value=select_group)

    await check_consent_selects(page)

    select_loc.select_option.assert_called_once_with(label="I accept", timeout=5000)


@pytest.mark.asyncio
async def test_check_consent_selects_already_accepted():
    """Skip consent select when already set to 'I accept'."""
    from jobpulse.form_engine.file_uploader import check_consent_selects

    page = MagicMock()
    option_loc = MagicMock()
    option_loc.all_text_contents = AsyncMock(
        return_value=["— Make a Selection —", "I accept"],
    )
    select_loc = MagicMock()
    select_loc.locator = MagicMock(return_value=option_loc)
    select_loc.evaluate = AsyncMock(return_value="I accept")
    select_loc.select_option = AsyncMock()

    select_group = MagicMock()
    select_group.all = AsyncMock(return_value=[select_loc])
    page.locator = MagicMock(return_value=select_group)

    await check_consent_selects(page)

    select_loc.select_option.assert_not_called()


# ── _fuzzy_label_to_profile_key ──


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


# ── _is_confirmation_page ──


@pytest.mark.asyncio
async def test_is_confirmation_page_true():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Thank you for applying! We will review your application."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is True


@pytest.mark.asyncio
async def test_is_confirmation_page_false():
    page = MagicMock()
    body_locator = MagicMock()
    body_locator.text_content = AsyncMock(
        return_value="Please fill in your details below."
    )
    page.locator = MagicMock(return_value=body_locator)
    filler = _make_filler(page_mock=page)

    assert await filler._is_confirmation_page() is False


# ── _is_submit_page ──


@pytest.mark.asyncio
async def test_is_submit_page_true():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if "Submit" in (name or ""):
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role
    assert await filler._is_submit_page() is True


@pytest.mark.asyncio
async def test_is_submit_page_false():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    assert await filler._is_submit_page() is False


# ── _click_navigation ──


@pytest.mark.asyncio
async def test_click_navigation_submit():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "submitted"


@pytest.mark.asyncio
async def test_click_navigation_dry_run_stop():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Submit" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    result = await filler._click_navigation(dry_run=True)
    assert result == "dry_run_stop"


@pytest.mark.asyncio
async def test_click_navigation_next():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    btn = MagicMock()
    btn.count = AsyncMock(return_value=1)
    btn.first = MagicMock()
    btn.first.is_visible = AsyncMock(return_value=True)
    btn.first.click = AsyncMock()
    page.wait_for_load_state = AsyncMock()

    def _get_by_role(role, name=None, exact=False):
        if role == "button" and name and "Continue" in name:
            return btn
        empty = MagicMock()
        empty.count = AsyncMock(return_value=0)
        return empty

    page.get_by_role = _get_by_role

    with patch.object(filler, "_move_mouse_to", new_callable=AsyncMock):
        result = await filler._click_navigation(dry_run=False)

    assert result == "next"


@pytest.mark.asyncio
async def test_click_navigation_none_found():
    page = MagicMock()
    filler = _make_filler(page_mock=page)

    empty = MagicMock()
    empty.count = AsyncMock(return_value=0)
    page.get_by_role = MagicMock(return_value=empty)

    result = await filler._click_navigation(dry_run=False)
    assert result == ""


# ── fill() — main loop ──


@pytest.mark.asyncio
async def test_fill_single_page_success():
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Resume", "type": "file", "locator": AsyncMock()},
    ]

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"Email": "test@test.com"}, 0)), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "test@test.com"}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True
    assert "field_types" in result
    assert "agent_mapping" in result


@pytest.mark.asyncio
async def test_fill_dry_run_stops():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"Name": "John"}, 0)), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=True), \
         patch.object(filler, "_click_navigation", return_value="dry_run_stop"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=True,
        )

    assert result["success"] is True
    assert result["dry_run"] is True
    assert "agent_mapping" in result


@pytest.mark.asyncio
async def test_fill_retries_unverified_fields_with_llm_recovery():
    filler = _make_filler()
    fields = [{"label": "Country", "type": "combobox", "value": "", "required": True}]

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"Country": "UK"}, 0)), \
         patch.object(
             filler,
             "_fill_by_label",
             side_effect=[
                 {"success": True, "value_verified": False, "actual_value": "Select..."},
                 {"success": True, "value_verified": True, "actual_value": "United Kingdom"},
             ],
         ) as mock_fill, \
         patch("jobpulse.native_form_filler.recover_failed_fields_with_llm",
               new_callable=AsyncMock,
               return_value=({"Country": "United Kingdom"}, 1)) as mock_recover, \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):
        result = await filler.fill(
            platform="greenhouse",
            cv_path="/tmp/cv.pdf",
            cl_path=None,
            profile={},
            custom_answers={},
            dry_run=False,
        )

    assert result["success"] is True
    mock_recover.assert_awaited_once()
    assert mock_fill.call_args_list[0].args == ("Country", "UK")
    assert mock_fill.call_args_list[1].args == ("Country", "United Kingdom")
    assert result["agent_mapping"]["Country"] == "United Kingdom"


@pytest.mark.asyncio
async def test_fill_confirmation_page():
    filler = _make_filler()

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=[]), \
         patch.object(filler, "_is_confirmation_page", return_value=True), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_fill_no_nav_button():
    filler = _make_filler()

    fields = [{"label": "Name", "type": "text", "value": "", "required": True}]

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"Name": "John"}, 0)), \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value=""), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={}, custom_answers={}, dry_run=False,
        )

    assert result["success"] is False
    assert "No navigation button" in result["error"]


@pytest.mark.asyncio
async def test_fill_calls_screening_for_unresolved():
    """fill() calls screen_questions for unresolved non-file fields."""
    filler = _make_filler()

    fields = [
        {"label": "Email", "type": "text", "value": "", "required": True},
        {"label": "Work auth?", "type": "radio", "options": ["Yes", "No"]},
    ]

    with patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch.object(filler, "_scan_fields", return_value=fields), \
         patch.object(filler, "_is_confirmation_page", return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"Email": "a@b.com"}, 0)), \
         patch("jobpulse.screening_answers.try_instant_answer", return_value=None), \
         patch("jobpulse.native_form_filler.screen_questions", new_callable=AsyncMock,
               return_value=({"Work auth?": "Yes"}, 1)) as mock_screen, \
         patch.object(filler, "_fill_by_label", return_value={"success": True}), \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch.object(filler, "_is_submit_page", return_value=False), \
         patch.object(filler, "_click_navigation", return_value="submitted"), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None):

        result = await filler.fill(
            platform="greenhouse", cv_path="/tmp/cv.pdf", cl_path=None,
            profile={"email": "a@b.com"}, custom_answers={}, dry_run=False,
        )

    mock_screen.assert_called_once()
    assert result["success"] is True


# ── Orchestrator integration ──

from jobpulse.application_orchestrator import ApplicationOrchestrator


@pytest.mark.asyncio
async def test_fill_application_routes_to_native_filler():
    """fill_application creates NativeFormFiller when engine='playwright'."""
    driver = AsyncMock()
    driver.page = MagicMock()
    driver.page.frame = MagicMock(return_value=None)
    orch = ApplicationOrchestrator(driver=driver, engine="playwright")

    with patch("jobpulse.native_form_filler.NativeFormFiller") as MockFiller:
        mock_instance = AsyncMock()
        mock_instance.fill = AsyncMock(return_value={"success": True, "pages_filled": 1})
        MockFiller.return_value = mock_instance

        result = await orch._filler.fill_application(
            platform="greenhouse",
            snapshot={"url": "https://example.com", "fields": [], "buttons": []},
            cv_path="/tmp/cv.pdf",
            cover_letter_path=None,
            profile={"email": "test@test.com"},
            custom_answers={},
            overrides=None,
            dry_run=False,
            form_intelligence=None,
        )

    MockFiller.assert_called_once_with(page=driver.page, driver=driver)
    mock_instance.fill.assert_called_once()
    assert result["success"] is True


# ── _fingerprint_fields / stuck detection ──


def test_fingerprint_fields_deterministic():
    """Same fields in different order produce the same fingerprint."""
    from jobpulse.native_form_filler import NativeFormFiller

    fields_a = [
        {"type": "text", "label": "First Name"},
        {"type": "email", "label": "Email"},
        {"type": "select", "label": "Country"},
    ]
    fields_b = [
        {"type": "select", "label": "Country"},
        {"type": "text", "label": "First Name"},
        {"type": "email", "label": "Email"},
    ]
    assert NativeFormFiller._fingerprint_fields(fields_a) == NativeFormFiller._fingerprint_fields(fields_b)


def test_fingerprint_fields_different():
    """Different fields produce different fingerprints."""
    from jobpulse.native_form_filler import NativeFormFiller

    fields_a = [{"type": "text", "label": "First Name"}]
    fields_b = [{"type": "text", "label": "Last Name"}]
    assert NativeFormFiller._fingerprint_fields(fields_a) != NativeFormFiller._fingerprint_fields(fields_b)


@pytest.mark.asyncio
async def test_stuck_detection_aborts_after_two_identical_pages():
    """fill() returns success=False when the same page fingerprint appears 2 times in a row."""
    from jobpulse.native_form_filler import NativeFormFiller

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[])
    page.frame = MagicMock(return_value=None)
    page.get_by_role = MagicMock(side_effect=lambda *a, **kw: _empty_locator())
    page.locator = MagicMock(return_value=_empty_locator())
    page.url = "https://example.com/apply"
    driver = AsyncMock()
    driver.page = page

    filler = NativeFormFiller(page=page, driver=driver)

    same_fields = [
        {"type": "text", "label": "First Name", "locator": MagicMock()},
        {"type": "email", "label": "Email", "locator": MagicMock()},
    ]

    mock_fe_db = MagicMock()
    mock_fe_db.return_value.get_timing.return_value = None
    mock_fe_db.return_value.get_container.return_value = None
    mock_fe_db.return_value.lookup.return_value = None
    mock_fe_db.return_value.get_field_mappings.return_value = {}
    mock_fe_db.normalize_domain.return_value = "example.com"

    with patch.object(filler, "_scan_fields", new_callable=AsyncMock, return_value=same_fields), \
         patch.object(filler, "_click_navigation", new_callable=AsyncMock, return_value="next"), \
         patch.object(filler, "_is_confirmation_page", new_callable=AsyncMock, return_value=False), \
         patch.object(filler, "_is_submit_page", new_callable=AsyncMock, return_value=False), \
         patch.object(filler, "_resolve_page_context", new_callable=AsyncMock), \
         patch.object(filler, "_try_cognitive_unstuck", new_callable=AsyncMock, return_value=False), \
         patch("jobpulse.native_form_filler.map_fields", new_callable=AsyncMock,
               return_value=({"First Name": "Test", "Email": "test@test.com"}, 0)), \
         patch("jobpulse.native_form_filler.vision_map_unlabeled_fields", new_callable=AsyncMock,
               return_value=({}, 0)), \
         patch("jobpulse.native_form_filler.screen_questions", new_callable=AsyncMock,
               return_value=({}, 0)), \
         patch("jobpulse.native_form_filler.recover_failed_fields_with_llm", new_callable=AsyncMock,
               return_value=({}, 0)), \
         patch("jobpulse.native_form_filler.recover_failed_fields_with_vision", new_callable=AsyncMock,
               return_value=({}, 0)), \
         patch("jobpulse.native_form_filler.handle_modal_cv_upload", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.upload_files", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.check_consent", new_callable=AsyncMock), \
         patch("jobpulse.native_form_filler.asyncio.sleep", new_callable=AsyncMock), \
         patch("shared.profile_store.get_profile_store", return_value=None), \
         patch("jobpulse.form_experience_db.FormExperienceDB", mock_fe_db):

        result = await filler.fill(
            cv_path=None,
            cl_path=None,
            profile={"name": "Test"},
            custom_answers={},
            platform="generic",
            dry_run=False,
        )

    assert result["success"] is False
    assert "Stuck" in result["error"]


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


