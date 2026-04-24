# Universal Form Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all ATS platforms route through one universal form engine (NativeFormFiller + form_scanner a11y tree), remove all hardcoded personal data, and fix LinkedIn Easy Apply modal blindness.

**Architecture:** Fix the snapshot layer to use a11y tree for field discovery (fixes LinkedIn modals + shadow DOM). Replace 6 hardcoded personal data locations in NativeFormFiller with ProfileStore lookups. Collapse SmartRecruiters' 1100-line standalone adapter into a ~60-line thin strategy override.

**Tech Stack:** Playwright CDP, Chrome Accessibility API, ProfileStore (SQLite + Fernet encryption), BasePlatformStrategy

**Spec:** `docs/superpowers/specs/2026-04-24-universal-form-engine-design.md`

---

### Task 1: Remove Hardcoded Personal Data — Option Aliases + Country Canonicalization

**Files:**
- Modify: `jobpulse/native_form_filler.py:63-76` (replace `_INTENT_OPTION_ALIASES`)
- Modify: `jobpulse/native_form_filler.py:180-201` (replace `_UK_OPTION_ALIASES` + `_canonicalize_country_value`)
- Test: `tests/jobpulse/test_native_form_filler.py`

- [ ] **Step 1: Write failing test for dynamic option aliases**

In `tests/jobpulse/test_native_form_filler.py`, add:

```python
def test_build_option_aliases_from_profile_store(tmp_path):
    """Option aliases built from ProfileStore screening defaults, not hardcoded."""
    from shared.profile_store import ProfileStore
    from jobpulse.native_form_filler import _build_option_aliases

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_screening_default("gender", "Male")
    store.set_screening_default("ethnicity", "Asian or Asian British - Indian")

    aliases = _build_option_aliases(store)

    assert "man" in aliases.get("male", ())
    assert "male" in aliases.get("man", ())
    assert any("asian" in a.lower() for a in aliases.get("asian or asian british - indian", ()))
    store.close()


def test_build_option_aliases_empty_store(tmp_path):
    """Empty ProfileStore produces empty aliases — no crash."""
    from shared.profile_store import ProfileStore
    from jobpulse.native_form_filler import _build_option_aliases

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    aliases = _build_option_aliases(store)
    assert isinstance(aliases, dict)
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py::test_build_option_aliases_from_profile_store -v`
Expected: FAIL — `_build_option_aliases` does not exist yet.

- [ ] **Step 3: Write failing test for dynamic country canonicalization**

In `tests/jobpulse/test_native_form_filler.py`, add:

```python
def test_canonicalize_country_value_from_profile(tmp_path):
    """Country canonicalization uses ProfileStore location, not hardcoded UK."""
    from shared.profile_store import ProfileStore
    from jobpulse.native_form_filler import _canonicalize_country_value

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_identity(location="London, United Kingdom")

    # Should still canonicalize UK aliases
    assert _canonicalize_country_value("Country", "UK", store) == "United Kingdom"
    assert _canonicalize_country_value("Country", "gb", store) == "United Kingdom"
    # Non-country labels pass through
    assert _canonicalize_country_value("First Name", "UK", store) == "UK"
    store.close()


def test_canonicalize_country_value_us_profile(tmp_path):
    """US-based profile canonicalizes US aliases."""
    from shared.profile_store import ProfileStore
    from jobpulse.native_form_filler import _canonicalize_country_value

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_identity(location="New York, United States")

    assert _canonicalize_country_value("Country", "US", store) == "United States"
    assert _canonicalize_country_value("Country", "usa", store) == "United States"
    store.close()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py::test_canonicalize_country_value_from_profile -v`
Expected: FAIL — signature mismatch (current `_canonicalize_country_value` takes 2 args, not 3).

- [ ] **Step 5: Implement `_build_option_aliases` and update `_canonicalize_country_value`**

In `jobpulse/native_form_filler.py`, replace lines 63-76 (`_INTENT_OPTION_ALIASES`) and lines 180-201 (`_UK_OPTION_ALIASES` + `_canonicalize_country_value`) with:

```python
# Generic gender aliases — not user-specific
_GENDER_ALIASES: dict[str, tuple[str, ...]] = {
    "male": ("man",),
    "man": ("male",),
    "female": ("woman",),
    "woman": ("female",),
}

# Country name → {aliases} + canonical phone code
_COUNTRY_DATA: dict[str, dict] = {
    "united kingdom": {
        "aliases": {"uk", "u k", "gb", "great britain", "england", "scotland", "wales"},
        "phone_code": "+44",
    },
    "united states": {
        "aliases": {"us", "usa", "america", "united states of america"},
        "phone_code": "+1",
    },
    "india": {
        "aliases": {"in", "bharat"},
        "phone_code": "+91",
    },
    "canada": {
        "aliases": {"ca",},
        "phone_code": "+1",
    },
    "australia": {
        "aliases": {"au", "aus"},
        "phone_code": "+61",
    },
}

# Ethnicity canonical → platform variants (generic mapping, not personal)
_ETHNICITY_ALIASES: dict[str, tuple[str, ...]] = {
    "asian or asian british - indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian indian",
        "asian",
    ),
    "asian indian": (
        "asian (indian, pakistani, bangladeshi, chinese, any other asian background)",
        "asian or asian british - indian",
    ),
    "white - british": ("white", "white british", "caucasian"),
    "black - african": ("black", "black or black british - african"),
    "mixed - white and asian": ("mixed", "mixed or multiple ethnic groups"),
}


def _build_option_aliases(store: Any = None) -> dict[str, tuple[str, ...]]:
    """Build option aliases from ProfileStore screening defaults + generic mappings."""
    aliases: dict[str, tuple[str, ...]] = dict(_GENDER_ALIASES)

    # Add country aliases for the user's country
    if store:
        location = store.identity().location or ""
        country = _extract_country_from_location(location)
        if country:
            data = _COUNTRY_DATA.get(country.lower(), {})
            for alias in data.get("aliases", set()):
                aliases[alias] = (country,)
            aliases[country.lower()] = tuple(data.get("aliases", set()))

        # Add ethnicity aliases from screening default
        ethnicity = store.screening_default("ethnicity") or ""
        if ethnicity:
            eth_lower = ethnicity.lower()
            if eth_lower in _ETHNICITY_ALIASES:
                aliases[eth_lower] = _ETHNICITY_ALIASES[eth_lower]
                for variant in _ETHNICITY_ALIASES[eth_lower]:
                    vl = variant.lower()
                    if vl not in aliases:
                        aliases[vl] = (ethnicity,)

    return aliases


def _extract_country_from_location(location: str) -> str:
    """Extract country name from a location string like 'London, United Kingdom'."""
    if not location:
        return ""
    parts = [p.strip() for p in location.split(",")]
    if len(parts) >= 2:
        return parts[-1]
    return parts[0]


def _get_user_country_data(store: Any = None) -> tuple[str, dict]:
    """Return (canonical_country_name, country_data_dict) from ProfileStore."""
    if not store:
        return "", {}
    location = store.identity().location or ""
    country = _extract_country_from_location(location)
    if not country:
        return "", {}
    data = _COUNTRY_DATA.get(country.lower(), {})
    return country, data


def _canonicalize_country_value(label: str, value: str, store: Any = None) -> str:
    """Normalize country aliases using ProfileStore location."""
    norm_label = _normalize_match_text(label)
    if "country" not in norm_label:
        return value

    country, data = _get_user_country_data(store)
    if not country:
        return value

    norm_value = _normalize_match_text(value)
    all_aliases = {norm_value for norm_value in {country.lower(), data.get("phone_code", "").lstrip("+")}}
    all_aliases |= {a.lower() for a in data.get("aliases", set())}
    if norm_value in all_aliases:
        return country
    return value
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py::test_build_option_aliases_from_profile_store tests/jobpulse/test_native_form_filler.py::test_build_option_aliases_empty_store tests/jobpulse/test_native_form_filler.py::test_canonicalize_country_value_from_profile tests/jobpulse/test_native_form_filler.py::test_canonicalize_country_value_us_profile -v`
Expected: all PASS.

- [ ] **Step 7: Update existing tests that rely on old hardcoded behavior**

Update `test_best_option_match_prefers_united_kingdom_plus44` — the function now takes an optional `store` param:

```python
def test_best_option_match_prefers_united_kingdom_plus44(tmp_path):
    from jobpulse.native_form_filler import _best_option_match
    from shared.profile_store import ProfileStore

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_identity(location="London, United Kingdom")

    options = ["Ukraine (+380)", "United Kingdom (+44)", "United States (+1)"]
    assert _best_option_match("Phone Country", "UK", options, store=store) == "United Kingdom (+44)"
    assert _best_option_match("Country", "+44", options, store=store) == "United Kingdom (+44)"
    store.close()
```

Update `test_best_option_match_understands_gender_and_asian_indian_intent` similarly — pass `store` with ethnicity screening default set.

- [ ] **Step 8: Run full test file**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "refactor(form-filler): replace hardcoded option aliases with ProfileStore-driven dynamic aliases"
```

---

### Task 2: Remove Hardcoded Personal Data — Screening Prompt + Phone + Country Widget

**Files:**
- Modify: `jobpulse/native_form_filler.py:83-107` (replace `_screening_prompt_profile` and `_screening_prompt_background`)
- Modify: `jobpulse/native_form_filler.py:204-255` (update `_best_option_match`)
- Modify: `jobpulse/native_form_filler.py:449-476` (update `_normalize_phone_value`)
- Modify: `jobpulse/native_form_filler.py:676-711` (update `_fill_special_widget`)
- Test: `tests/jobpulse/test_native_form_filler.py`

- [ ] **Step 1: Write failing test for ProfileStore-based screening prompt**

```python
def test_screening_prompt_background_from_profile_store(tmp_path):
    """Screening prompt uses ProfileStore, not hardcoded UK values."""
    from shared.profile_store import ProfileStore
    from jobpulse.native_form_filler import _screening_prompt_background

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_identity(
        first_name="Alice", last_name="Smith",
        location="London, United Kingdom", education="BSc CS, UCL",
    )
    store.set_sensitive("visa_status", "Graduate Visa", category="work_auth")
    store.set_screening_default("notice_period", "2 weeks")
    store.set_screening_default("relocation", "Yes, anywhere in the UK")
    store.set_screening_default("right_to_work", "Yes")

    profile = {
        "first_name": "Alice", "last_name": "Smith",
        "education": "BSc CS, UCL", "location": "London, United Kingdom",
        "visa_status": "Graduate Visa", "notice_period": "2 weeks",
    }
    result = _screening_prompt_background(profile, store)

    assert "Alice" in result
    assert "Graduate Visa" in result
    assert "2 weeks" in result or "notice" in result.lower()
    # Should NOT contain hardcoded "anywhere in the UK" — should come from store
    assert "Yes, anywhere in the UK" in result
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py::test_screening_prompt_background_from_profile_store -v`
Expected: FAIL — signature mismatch (current takes 1 arg).

- [ ] **Step 3: Write failing test for dynamic phone normalization**

```python
@pytest.mark.asyncio
async def test_normalize_phone_value_uses_profile_country(tmp_path):
    """Phone normalization reads country code from ProfileStore, not hardcoded +44."""
    from shared.profile_store import ProfileStore

    store = ProfileStore(db_path=tmp_path / "profile.db", key_path=tmp_path / ".key")
    store.set_identity(location="London, United Kingdom", phone="+447909445288")

    page = MagicMock()
    filler = _make_filler(page_mock=page)
    filler._profile_store = store

    plus44 = MagicMock()
    plus44.count = AsyncMock(return_value=1)
    page.get_by_text = MagicMock(return_value=plus44)

    result = await filler._normalize_phone_value("Phone", "07909 445288")
    assert result == "7909445288"
    store.close()
```

- [ ] **Step 4: Implement — update `_screening_prompt_profile`, `_screening_prompt_background`, `_best_option_match`, `_normalize_phone_value`, `_fill_special_widget`**

Replace `_screening_prompt_profile()` (lines 83-93):

```python
def _screening_prompt_profile(store: Any = None) -> dict[str, Any]:
    if store:
        ident = store.identity()
        work_auth = store.as_work_auth()
        return {
            "first_name": ident.first_name,
            "last_name": ident.last_name,
            "education": store._education_summary(),
            "location": ident.location,
            "visa_status": work_auth.get("visa_status", ""),
            "notice_period": work_auth.get("notice_period", ""),
        }
    from jobpulse.applicator import PROFILE, WORK_AUTH
    return {
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "education": PROFILE["education"],
        "location": PROFILE["location"],
        "visa_status": WORK_AUTH["visa_status"],
        "notice_period": WORK_AUTH["notice_period"],
    }
```

Replace `_screening_prompt_background()` (lines 96-107):

```python
def _screening_prompt_background(profile: dict[str, Any], store: Any = None) -> str:
    relocation = "Yes"
    commuting = "Yes"
    right_to_work = "Yes"
    country = "the UK"

    if store:
        relocation = store.screening_default("relocation") or "Yes"
        commuting = store.screening_default("commuting") or "Yes"
        right_to_work = store.screening_default("right_to_work") or "Yes"
        location = store.identity().location or ""
        country = _extract_country_from_location(location) or "the UK"

    return (
        f"Name: {wrap_pii_value('applicant.first_name', profile['first_name'])} "
        f"{wrap_pii_value('applicant.last_name', profile['last_name'])}. "
        f"Education: {wrap_pii_value('applicant.education', profile['education'])}. "
        f"Location: {wrap_pii_value('applicant.location', profile['location'])}. "
        f"Visa: {wrap_pii_value('applicant.visa_status', profile['visa_status'])}. "
        f"Notice: {wrap_pii_value('applicant.notice_period', profile['notice_period'])}. "
        f"Willing to relocate: {relocation}. "
        f"Commuting: {commuting}. "
        f"Right to work {country}: {right_to_work}."
    )
```

Update `_best_option_match()` (line 204) — add `store` kwarg, replace hardcoded UK+44 logic with dynamic country lookup, replace hardcoded visa types with `store.screening_default("visa_status")`:

```python
def _best_option_match(label: str, value: str, options: list[str], *, store: Any = None) -> str | None:
    if not options:
        return None

    canonical_value = _canonicalize_country_value(label, value, store)
    norm_label = _normalize_match_text(label)
    norm_value = _normalize_match_text(canonical_value)
    normalized_options = [_normalize_match_text(opt) for opt in options]
    if not norm_value:
        return None

    # Country preference — use user's country from ProfileStore
    country, country_data = _get_user_country_data(store)
    phone_code = country_data.get("phone_code", "")
    if "country" in norm_label and country and norm_value == _normalize_match_text(country):
        for opt, norm_opt in zip(options, normalized_options):
            if _normalize_match_text(country) in norm_opt and phone_code in opt:
                return opt
        for opt, norm_opt in zip(options, normalized_options):
            if norm_opt == _normalize_match_text(country) or norm_opt.startswith(_normalize_match_text(country)):
                return opt
        if phone_code:
            for opt in options:
                if phone_code in opt:
                    return opt

    # Visa status matching — read visa type from ProfileStore
    if "right to work status" in norm_label or ("visa" in norm_label and "status" in norm_label):
        visa = store.screening_default("visa_status") if store else ""
        visa_lower = visa.lower() if visa else norm_value
        for keyword in _extract_visa_keywords(visa_lower):
            for opt, norm_opt in zip(options, normalized_options):
                if keyword in norm_opt:
                    return opt

    # Alias matching
    aliases = _build_option_aliases(store)
    for alias in aliases.get(norm_value, ()):
        norm_alias = _normalize_match_text(alias)
        for opt, norm_opt in zip(options, normalized_options):
            if norm_opt == norm_alias or norm_alias.startswith(norm_opt) or norm_opt.startswith(norm_alias):
                return opt

    # Exact + prefix + substring match (unchanged)
    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt == norm_value:
            return opt
    for opt, norm_opt in zip(options, normalized_options):
        if norm_opt.startswith(norm_value):
            return opt
    if len(norm_value) >= 4:
        for opt, norm_opt in zip(options, normalized_options):
            if norm_value in norm_opt:
                return opt
    return None


def _extract_visa_keywords(visa_text: str) -> list[str]:
    """Extract searchable keywords from a visa status string."""
    keywords = []
    for term in ("student visa", "graduate visa", "skilled worker", "tier 2", "tier 4",
                 "work permit", "permanent resident", "citizen"):
        if term in visa_text:
            keywords.append(term)
    return keywords if keywords else [visa_text[:20]]
```

Update `_fill_special_widget()` (line 676) — read country from ProfileStore:

```python
async def _fill_special_widget(self, label: str, value: str) -> dict[str, Any] | None:
    norm_label = _normalize_match_text(label)
    if "country options" not in norm_label:
        return None

    button = self._page.locator("button.iti__selected-country").first
    if not await button.count():
        return {"success": False, "error": "No phone country widget found"}

    country, country_data = _get_user_country_data(getattr(self, "_profile_store", None))
    phone_code = country_data.get("phone_code", "+44")
    search_term = country or "United Kingdom"

    await self._smart_scroll(button)
    await self._move_mouse_to(button)
    await button.click()
    search = self._page.locator("#iti-0__search-input").first
    await search.fill("")
    await search.fill(search_term)
    await asyncio.sleep(0.5)

    option = self._page.locator(
        "#iti-0__country-listbox li", has_text=f"{search_term} ({phone_code})"
    ).first
    if await option.count():
        await option.click()
    else:
        await search.press("ArrowDown")
        await asyncio.sleep(0.2)
        await search.press("Enter")

    actual = (await button.get_attribute("aria-label")) or ""
    expected = f"{search_term} ({phone_code})"
    verified = search_term.lower() in actual.lower() and phone_code in actual
    return {
        "success": True,
        "value_set": expected,
        "value_verified": verified,
        "actual_value": actual,
        "options_seen": [expected],
        "expected_value": expected,
    }
```

Update `_normalize_phone_value()` (line 449) — read phone code from ProfileStore:

```python
async def _normalize_phone_value(self, label: str, value: str) -> str:
    if "phone" not in _normalize_match_text(label):
        return value

    digits = re.sub(r"\D+", "", value)
    if not digits:
        return value

    _, country_data = _get_user_country_data(getattr(self, "_profile_store", None))
    phone_code = country_data.get("phone_code", "").lstrip("+")
    if not phone_code:
        return value

    has_split_country_code = False
    try:
        country_hint = self._page.get_by_text(f"+{phone_code}", exact=False)
        has_split_country_code = bool(await country_hint.count())
    except Exception:
        has_split_country_code = False

    if has_split_country_code:
        if digits.startswith(phone_code):
            digits = digits[len(phone_code):]
        if digits.startswith("0") and len(digits) >= 10:
            digits = digits[1:]
        return digits

    if digits.startswith("0") and len(digits) >= 10:
        return f"+{phone_code}{digits[1:]}"
    if digits.startswith(phone_code):
        return f"+{digits}"
    return value
```

- [ ] **Step 5: Wire `_profile_store` into NativeFormFiller.__init__ and fill()**

In `NativeFormFiller.__init__` (line 286), add lazy ProfileStore load:

```python
def __init__(self, page: "Page", driver: Any) -> None:
    self._page = page
    self._driver = driver
    self._llm_fallback_count: int = 0
    self._profile_store: Any = None
    self._correction_warning: str = ""
```

At the start of `fill()` (line 1583), load the store:

```python
async def fill(self, platform, cv_path, cl_path, profile, custom_answers, dry_run):
    # Load ProfileStore for dynamic data
    try:
        from shared.profile_store import get_profile_store
        self._profile_store = get_profile_store()
    except Exception:
        self._profile_store = None
    ...
```

- [ ] **Step 6: Update all internal callers of modified functions**

Search for all calls to `_best_option_match`, `_canonicalize_country_value`, `_screening_prompt_background`, and `_screening_prompt_profile` inside `native_form_filler.py` and pass `store=self._profile_store` where applicable. Use `grep -n` to find them:

```bash
grep -n "_best_option_match\|_canonicalize_country_value\|_screening_prompt_background\|_screening_prompt_profile" jobpulse/native_form_filler.py
```

Each call site adds `store=self._profile_store` or `store=store` as keyword argument.

- [ ] **Step 7: Update existing tests**

Update `test_fill_special_widget_sets_country_options_to_united_kingdom` — mock `filler._profile_store` with a store that has UK location.

Update `test_normalize_phone_value_for_split_uk_widget` — mock `filler._profile_store`.

Update `test_best_option_match_picks_student_visa_status` — pass `store` with `visa_status` screening default.

- [ ] **Step 8: Run full test suite**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v`
Expected: all PASS.

- [ ] **Step 9: Grep verification — no hardcoded PII remaining**

```bash
grep -in "united kingdom\|+44\|male.*man\|asian indian\|anywhere in the uk\|right to work uk" jobpulse/native_form_filler.py
```

Expected: 0 hits (only generic `_COUNTRY_DATA` table references "united kingdom" as data, not logic).

- [ ] **Step 10: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "refactor(form-filler): remove all hardcoded PII, wire ProfileStore for screening/country/phone"
```

---

### Task 3: Fix Snapshot Layer — A11y Tree Field Discovery

**Files:**
- Modify: `jobpulse/playwright_driver.py:275-339` (enhance `get_snapshot`)
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:258-296` (modal wait)
- Modify: `jobpulse/page_analyzer.py:68-134` (dialog awareness)
- Test: `tests/jobpulse/test_playwright_driver.py`

- [ ] **Step 1: Write failing test for a11y-enhanced snapshot**

In `tests/jobpulse/test_playwright_driver.py`, add:

```python
@pytest.mark.asyncio
async def test_get_snapshot_includes_a11y_fields():
    """get_snapshot merges a11y tree fields when available."""
    from jobpulse.playwright_driver import PlaywrightDriver

    driver = PlaywrightDriver.__new__(PlaywrightDriver)

    page = AsyncMock()
    driver._page = page

    # JS snapshot returns 0 fields (modal scenario)
    page.evaluate = AsyncMock(return_value={
        "url": "https://linkedin.com/jobs/view/123",
        "title": "Apply",
        "fields": [],
        "buttons": [{"text": "Submit", "selector": "button", "enabled": True, "href": None}],
        "page_text_preview": "Apply for this role",
        "has_file_inputs": False,
    })

    # Mock form_scanner.scan_form to return fields from a11y tree
    mock_scan = AsyncMock()
    mock_field = MagicMock()
    mock_field.label = "First Name"
    mock_field.role = "textbox"
    mock_field.value = ""
    mock_field.required = True
    mock_field.to_dict.return_value = {
        "label": "First Name", "role": "textbox", "value": "", "required": True,
    }
    mock_scan.return_value = MagicMock(fields=[mock_field])

    with patch("jobpulse.playwright_driver.scan_form", mock_scan):
        snapshot = await driver.get_snapshot()

    assert len(snapshot["fields"]) >= 1
    assert snapshot["fields"][0]["label"] == "First Name"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_playwright_driver.py::test_get_snapshot_includes_a11y_fields -v`
Expected: FAIL — `scan_form` not imported in playwright_driver.

- [ ] **Step 3: Implement a11y field merge in `get_snapshot()`**

In `jobpulse/playwright_driver.py`, at the end of `get_snapshot()` (after the JS evaluate, before `return`):

```python
async def get_snapshot(self, **kwargs) -> dict:
    snapshot = await self._page.evaluate("""() => { ... }""")  # existing JS

    # Merge a11y tree fields — discovers fields in modals and shadow DOM
    try:
        from jobpulse.form_scanner import scan_form
        scan = await scan_form(self._page)
        if scan.fields:
            a11y_fields = []
            existing_labels = {f.get("label", "").lower() for f in snapshot.get("fields", [])}
            for ff in scan.fields:
                if ff.label.lower() not in existing_labels:
                    a11y_fields.append({
                        "selector": f'[role="{ff.role}"][name="{ff.label}"]',
                        "type": ff.role,
                        "input_type": ff.role,
                        "value": ff.value,
                        "label": ff.label,
                        "required": ff.required,
                    })
            snapshot["fields"].extend(a11y_fields)
            if a11y_fields:
                snapshot["has_file_inputs"] = snapshot.get("has_file_inputs", False)
    except Exception as exc:
        logger.debug("get_snapshot: a11y merge failed: %s", exc)

    return snapshot
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_playwright_driver.py::test_get_snapshot_includes_a11y_fields -v`
Expected: PASS.

- [ ] **Step 5: Add modal wait to navigator**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, after `click_apply_button` returns (line 296), replace the blind `await asyncio.sleep(3)` with:

```python
# Wait for modal or new form fields (max 8s)
modal_found = False
for _ in range(16):  # 16 * 0.5s = 8s
    try:
        dialog = self.driver.page.locator('[role="dialog"], [aria-modal="true"]')
        if await dialog.count():
            modal_found = True
            break
    except Exception:
        pass
    await asyncio.sleep(0.5)

if not modal_found:
    await asyncio.sleep(1)  # brief fallback wait
```

- [ ] **Step 6: Add dialog awareness to page analyzer**

In `jobpulse/page_analyzer.py`, in `_dom_detect()`, add a check before step 6 (line 114):

```python
# 5.5. Modal/dialog with form fields — application form regardless of background
page_text_lower = page_text.lower()
has_dialog_hint = (
    "role=\"dialog\"" in page_text_lower
    or "aria-modal" in page_text_lower
    or any("dialog" in f.get("selector", "").lower() for f in fields)
)
if has_dialog_hint and (has_application_fields or len(fields) >= 2):
    return PageType.APPLICATION_FORM, 0.9
```

- [ ] **Step 7: Run navigator and page analyzer tests**

Run: `python -m pytest tests/jobpulse/test_playwright_driver.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add jobpulse/playwright_driver.py jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/page_analyzer.py tests/jobpulse/test_playwright_driver.py
git commit -m "fix(snapshot): merge a11y tree fields into snapshot, add modal wait and dialog detection"
```

---

### Task 4: Collapse SmartRecruiters into Thin Strategy

**Files:**
- Rewrite: `jobpulse/ats_adapters/smartrecruiters.py` (1100 → ~70 lines)
- Modify: `jobpulse/ats_adapters/__init__.py` (remove special case)
- Modify: `jobpulse/ats_adapters/strategy.py` (add combobox override hook)
- Test: `tests/jobpulse/test_smartrecruiters_adapter.py`

- [ ] **Step 1: Add combobox override hook to BasePlatformStrategy**

In `jobpulse/ats_adapters/strategy.py`, add to `BasePlatformStrategy`:

```python
async def fill_combobox(
    self, page: "Page", locator: Any, value: str, label: str,
) -> str | None:
    """Override combobox fill behavior. Return selected text, or None to use default."""
    return None
```

- [ ] **Step 2: Write the thin SmartRecruitersStrategy**

Rewrite `jobpulse/ats_adapters/smartrecruiters.py`:

```python
"""SmartRecruiters platform strategy — thin override for spl-autocomplete comboboxes.

Field scanning, label extraction, answer resolution, and filling all handled
by NativeFormFiller + form_scanner.py.  This strategy only overrides the
combobox interaction pattern for SmartRecruiters' shadow DOM web components.
"""
from __future__ import annotations

import asyncio
from typing import Any, TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.ats_adapters.strategy import BasePlatformStrategy, register_strategy

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


@register_strategy
class SmartRecruitersStrategy(BasePlatformStrategy):
    name = "smartrecruiters"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "smartrecruiters.com" in url

    async def pre_fill(
        self, page: "Page", cv_path: str | None,
        profile: dict, custom_answers: dict,
    ) -> dict[str, Any]:
        """Upload CV for auto-parse before form scanning."""
        if not cv_path:
            return {}
        try:
            file_inputs = page.locator("input[type='file']")
            if await file_inputs.count():
                await file_inputs.first.set_input_files(cv_path)
                await asyncio.sleep(3)
                logger.info("SR: uploaded CV for auto-parse")
                return {"cv_uploaded": True}
        except Exception as exc:
            logger.debug("SR: CV auto-parse upload failed: %s", exc)
        return {}

    async def fill_combobox(
        self, page: "Page", locator: Any, value: str, label: str,
    ) -> str | None:
        """SmartRecruiters spl-autocomplete: type → ArrowDown → Enter."""
        try:
            await locator.click()
            await locator.fill("")
            await locator.fill(value)
            await asyncio.sleep(0.5)

            option = page.get_by_role("option").first
            if await option.count():
                text = (await option.text_content() or "").strip()
                await option.click()
                return text

            await locator.press("ArrowDown")
            await asyncio.sleep(0.2)
            await locator.press("Enter")
            return value
        except Exception as exc:
            logger.debug("SR combobox fill failed for %s: %s", label[:40], exc)
            return None
```

- [ ] **Step 3: Update adapter registry**

Replace `jobpulse/ats_adapters/__init__.py`:

```python
"""ATS adapter registry — all platforms route through PlaywrightAdapter.

Platform-specific quirks handled by BasePlatformStrategy subclasses
(strategy.py). SmartRecruiters, LinkedIn, etc. register thin strategies
that override only what differs from the universal NativeFormFiller pipeline.
"""
from __future__ import annotations

from jobpulse.ats_adapters.base import BaseATSAdapter


def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    """Return the PlaywrightAdapter for all platforms.

    Platform-specific behavior is handled by strategies loaded inside
    NativeFormFiller via ``get_strategy(platform)``.
    """
    # Ensure strategies are registered on first import
    import jobpulse.ats_adapters.smartrecruiters  # noqa: F401
    import jobpulse.ats_adapters.generic  # noqa: F401

    from jobpulse.playwright_adapter import PlaywrightAdapter
    return PlaywrightAdapter()


def reset_adapter() -> None:
    """No-op — kept for test compatibility."""


__all__ = ["BaseATSAdapter", "get_adapter", "reset_adapter"]
```

- [ ] **Step 4: Wire strategy combobox override into NativeFormFiller**

In `jobpulse/native_form_filler.py`, in `_fill_by_label()` where combobox filling happens (around line 540-630), add strategy check before the default combobox path:

```python
# Try strategy combobox override first
strategy = getattr(self, "_strategy", None)
if strategy and hasattr(strategy, "fill_combobox"):
    override_result = await strategy.fill_combobox(page, locator, fill_value, label)
    if override_result is not None:
        return {"success": True, "value_set": override_result, "value_verified": True}
```

Also load strategy in `fill()`:

```python
# At start of fill(), after ProfileStore load
from jobpulse.ats_adapters.strategy import get_strategy
self._strategy = get_strategy(platform)

# Call pre_fill hook
if self._strategy:
    pre_result = await self._strategy.pre_fill(self._page, cv_path, profile, custom_answers)
    if pre_result.get("cv_uploaded"):
        # Skip duplicate CV upload later
        custom_answers["_cv_pre_uploaded"] = True
```

- [ ] **Step 5: Update SmartRecruiters tests**

Rewrite `tests/jobpulse/test_smartrecruiters_adapter.py`:

```python
"""Tests for SmartRecruiters thin platform strategy."""
import pytest
from jobpulse.ats_adapters.smartrecruiters import SmartRecruitersStrategy


class TestSmartRecruitersStrategy:
    def test_detect_oneclick_url(self):
        strategy = SmartRecruitersStrategy()
        assert strategy.detect("https://jobs.smartrecruiters.com/esureGroup/744000106347626")

    def test_detect_company_url(self):
        strategy = SmartRecruitersStrategy()
        assert strategy.detect("https://jobs.smartrecruiters.com/oneclick-ui/company/esureGroup/publication/829c279f")

    def test_reject_other_ats(self):
        strategy = SmartRecruitersStrategy()
        assert not strategy.detect("https://boards.greenhouse.io/company/jobs/123")
        assert not strategy.detect("https://linkedin.com/jobs/view/123")

    def test_name(self):
        assert SmartRecruitersStrategy.name == "smartrecruiters"

    def test_min_page_time(self):
        assert SmartRecruitersStrategy().min_page_time == 5.0


class TestAdapterRegistry:
    def test_all_platforms_return_playwright_adapter(self):
        from jobpulse.ats_adapters import get_adapter
        from jobpulse.playwright_adapter import PlaywrightAdapter

        assert isinstance(get_adapter("smartrecruiters"), PlaywrightAdapter)
        assert isinstance(get_adapter("greenhouse"), PlaywrightAdapter)
        assert isinstance(get_adapter(None), PlaywrightAdapter)

    def test_strategy_registry_has_smartrecruiters(self):
        from jobpulse.ats_adapters.strategy import get_strategy

        strategy = get_strategy("smartrecruiters")
        assert strategy.name == "smartrecruiters"
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/jobpulse/test_smartrecruiters_adapter.py -v`
Expected: all PASS.

- [ ] **Step 7: Run full test suite to catch regressions**

Run: `python -m pytest tests/jobpulse/ -v --timeout=30`
Expected: all PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add jobpulse/ats_adapters/smartrecruiters.py jobpulse/ats_adapters/__init__.py jobpulse/ats_adapters/strategy.py jobpulse/native_form_filler.py tests/jobpulse/test_smartrecruiters_adapter.py
git commit -m "refactor(adapters): collapse SmartRecruiters to thin strategy, unify all platforms through NativeFormFiller"
```

---

### Task 5: Integration Verification

**Files:**
- No new files — verification only

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --timeout=60 -x
```

Expected: all PASS.

- [ ] **Step 2: Verify no hardcoded PII in form filler**

```bash
grep -in "united kingdom\|+44\|male.*man\|asian indian\|anywhere in the uk\|right to work uk" jobpulse/native_form_filler.py | grep -v "_COUNTRY_DATA\|_GENDER_ALIASES\|_ETHNICITY_ALIASES"
```

Expected: 0 hits.

- [ ] **Step 3: Verify SmartRecruiters adapter is thin**

```bash
wc -l jobpulse/ats_adapters/smartrecruiters.py
```

Expected: < 80 lines.

- [ ] **Step 4: Verify adapter registry routes all platforms through PlaywrightAdapter**

```bash
python -c "from jobpulse.ats_adapters import get_adapter; print(type(get_adapter('smartrecruiters')).__name__)"
```

Expected: `PlaywrightAdapter`

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -A && git status
```

Only commit if there are changes. If clean, skip.

- [ ] **Step 6: Final commit message**

```bash
git commit -m "test(integration): verify universal form engine — all platforms via NativeFormFiller"
```
