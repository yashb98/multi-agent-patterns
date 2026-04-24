# Universal Form Engine — Design Spec

**Date:** 2026-04-24
**Status:** Approved
**Scope:** Fix snapshot layer, remove hardcoded data, collapse SmartRecruiters into thin strategy

## Problem

Three independent problems that compound:

1. **Snapshot layer blind to modals** — `playwright_driver.get_snapshot()` uses `document.querySelectorAll()` which cannot see inside LinkedIn Easy Apply modals or shadow DOM. LinkedIn form experience records `field_types: []`, `screening_questions: []` across 3 applications — the fields were never discovered.

2. **Hardcoded personal data in NativeFormFiller** — 6 locations with UK-specific, user-specific values baked into source code (`_INTENT_OPTION_ALIASES`, `_screening_prompt_background`, `_UK_OPTION_ALIASES`, `_canonicalize_country_value`, `_best_option_match`, `_fill_special_widget`, `_normalize_phone_value`). Not productionisable.

3. **SmartRecruiters adapter duplicates the universal engine** — 1100 lines reimplementing field scanning, label extraction, and form filling that NativeFormFiller already does. The adapter predates `form_scanner.py` (a11y tree) and `BasePlatformStrategy`. Its deep JS DOM walker is now redundant.

## Architecture

### Current Flow (broken for LinkedIn)

```
Click "Easy Apply"
  → get_snapshot() → querySelectorAll → sees 0 fields in modal
  → PageAnalyzer: "UNKNOWN"
  → Navigator gives up
  → NativeFormFiller never called
```

### Target Flow

```
PlaywrightAdapter.fill_and_submit()
  → ApplicationOrchestrator.apply()
    → Navigator: wait for modal/form (a11y-tree-aware)
    → NativeFormFiller.fill()          ← universal engine, all platforms
        ├── form_scanner.scan_form()          ← a11y tree, pierces shadow DOM + modals
        ├── ProfileStore.as_applicant_profile()  ← no hardcoded data
        ├── screening_answers.try_instant_answer()  ← all screening questions
        ├── PlatformStrategy overrides        ← thin, quirks only (50-80 lines each)
        └── LLM fallback → ats_answer_cache   ← self-adapting for unknown fields
```

All platforms route through `PlaywrightAdapter` → `NativeFormFiller`. Platform strategies provide only quirk overrides — never field scanning or answer resolution.

## Change 1: Fix Snapshot Layer

### 1a. `playwright_driver.get_snapshot()` — Add a11y tree field discovery

Current `get_snapshot()` uses a single `page.evaluate()` with `querySelectorAll`. This misses:
- Fields inside modal dialogs (LinkedIn Easy Apply)
- Shadow DOM web components (SmartRecruiters `spl-*`)
- Custom widgets without standard HTML form elements

**Change:** After the existing JS snapshot, run `form_scanner.scan_form()` (CDP `Accessibility.getFullAXTree`) and merge the discovered fields into the snapshot. The a11y tree result takes precedence when both find fields for the same element.

This is additive — the existing JS snapshot still provides buttons, page text, and URL. The a11y tree supplements it with accurate field discovery.

### 1b. Navigator modal wait

After clicking any apply button (`click_apply_button`), the navigator currently does a blind `await asyncio.sleep(3)`.

**Change:** Replace with an active wait loop (max 8s):
1. Check for `[role="dialog"]` appearing on page
2. Check for new form fields via quick a11y tree scan
3. If either condition met, proceed immediately
4. Fall back to existing 3s sleep if neither within timeout

This handles LinkedIn Easy Apply (modal appears ~2-5s after click) without slowing down platforms that don't use modals.

### 1c. Page analyzer modal awareness

`PageAnalyzer.detect()` classifies page type from the snapshot. Currently, if a modal is open with form fields but the background page looks like a job description, it may classify as `JOB_DESCRIPTION`.

**Change:** If the snapshot contains fields inside a `role="dialog"` container, classify as `APPLICATION_FORM` regardless of background page content.

## Change 2: Remove Hardcoded Personal Data

All 6 hardcoded locations in `native_form_filler.py` are replaced with dynamic data from `ProfileStore`.

### 2a. `_INTENT_OPTION_ALIASES` (line 63)

**Current:** Hardcodes "male"/"man", "asian indian" variants, "uk"/"united kingdom".

**Replace:** Build aliases at runtime from `ProfileStore.all_screening_defaults()`:
- Gender default → generate bidirectional aliases (male ↔ man, female ↔ woman)
- Ethnicity default → generate platform-variant aliases
- Country from `ProfileStore.identity().location` → generate country name variants

The alias dict is built once per `fill()` call, cached for the session.

### 2b. `_screening_prompt_background()` (line 96)

**Current:** Hardcodes "Willing to relocate: Yes, anywhere in the UK", "Right to work UK: Yes".

**Replace:** Build from ProfileStore:
```python
profile = get_profile_store()
work_auth = profile.as_work_auth()
relocation = profile.screening_default("relocation") or "Yes"
country = profile.identity().location.split(",")[-1].strip() or "UK"
```

### 2c. `_UK_OPTION_ALIASES` + `_canonicalize_country_value()` (line 180)

**Current:** Only knows about UK. Hardcodes {"uk", "gb", "great britain", "united kingdom", "+44"}.

**Replace:** `_canonicalize_country_value()` reads the user's country from ProfileStore and builds aliases for THAT country. Uses a small lookup table mapping country → {aliases, phone code}:
```python
_COUNTRY_ALIASES = {
    "united kingdom": {"uk", "gb", "great britain", "+44", "44"},
    "united states": {"us", "usa", "america", "+1", "1"},
    "india": {"in", "+91", "91"},
    # ... extensible
}
```

This table is generic infrastructure, not personal data.

### 2d. `_best_option_match()` UK+44 preference (line 204)

**Current:** Hardcodes preference for "United Kingdom (+44)" in country dropdowns and UK-specific visa types.

**Replace:** Read user's country from ProfileStore → prefer that country's phone code. Visa type matching reads from `ProfileStore.screening_default("visa_status")` instead of hardcoding "student visa"/"graduate visa".

### 2e. `_fill_special_widget()` hardcoded "United Kingdom (+44)" (line 676)

**Current:** The intl-tel-input phone widget handler searches for "United Kingdom" and "+44".

**Replace:** Read country from ProfileStore, search for that country name + code in the widget options.

### 2f. `_normalize_phone_value()` +44 assumption (line 449)

**Current:** Assumes +44 UK phone format.

**Replace:** Extract country code from ProfileStore phone number (already stored with country code prefix) or from the country alias table.

## Change 3: Collapse SmartRecruiters Adapter

### 3a. Delete standalone adapter

Delete the current `ats_adapters/smartrecruiters.py` (1100 lines, `BaseATSAdapter` subclass with full form-filling engine).

### 3b. Create thin strategy

New `ats_adapters/smartrecruiters.py` as a `BasePlatformStrategy` subclass (~60 lines):

```python
@register_strategy
class SmartRecruitersStrategy(BasePlatformStrategy):
    name = "smartrecruiters"
    min_page_time = 5.0

    def detect(self, url: str) -> bool:
        return "smartrecruiters.com" in url

    async def pre_fill(self, page, cv_path, profile, custom_answers):
        # Upload CV for auto-parse before form fill
        ...

    def field_fill_overrides(self):
        # spl-autocomplete combobox: type → ArrowDown → Enter
        return {"combobox_method": "type_arrow_enter"}
```

Field scanning → `form_scanner.scan_form()` (a11y tree pierces `spl-*` shadow DOM natively).
Field mapping → NativeFormFiller's `_map_fields()` + `screening_answers.try_instant_answer()`.
Filling → NativeFormFiller's `_fill_by_label()` with strategy overrides for comboboxes.

### 3c. Adapter registry

`ats_adapters/__init__.py` no longer special-cases SmartRecruiters:

```python
def get_adapter(ats_platform: str | None = None) -> BaseATSAdapter:
    # All platforms go through PlaywrightAdapter
    # Platform-specific quirks handled by BasePlatformStrategy
    from jobpulse.playwright_adapter import PlaywrightAdapter
    return PlaywrightAdapter()
```

NativeFormFiller already loads the correct strategy via `get_strategy(platform)` from `ats_adapters/strategy.py`.

## Change 4: Wire Strategy into NativeFormFiller

NativeFormFiller already receives `platform` in its `fill()` method. Add strategy loading:

```python
async def fill(self, platform, ...):
    from jobpulse.ats_adapters.strategy import get_strategy
    self._strategy = get_strategy(platform)
    # Use strategy.field_fill_overrides() in _fill_by_label()
    # Call strategy.pre_fill() before scanning
    # Call strategy.post_page() after each page
```

The strategy hooks are already defined in `BasePlatformStrategy` — they just need to be called from NativeFormFiller's fill loop.

## Files Changed

| File | Change |
|------|--------|
| `jobpulse/playwright_driver.py` | Add a11y tree field merge to `get_snapshot()` |
| `jobpulse/application_orchestrator_pkg/_navigator.py` | Modal wait loop after apply click |
| `jobpulse/native_form_filler.py` | Remove 6 hardcoded data locations, wire ProfileStore, load and call PlatformStrategy |
| `jobpulse/ats_adapters/smartrecruiters.py` | Replace 1100-line adapter with ~60-line strategy |
| `jobpulse/ats_adapters/__init__.py` | Remove SmartRecruiters special case |
| `jobpulse/form_scanner.py` | No changes (already universal) |
| `shared/profile_store.py` | No changes (already has all needed methods) |
| `tests/jobpulse/test_native_form_filler.py` | Update for ProfileStore-based data |
| `tests/jobpulse/test_smartrecruiters_adapter.py` | Update for thin strategy |

## Testing

- **Unit:** Existing tests updated — ProfileStore mocked via `tmp_path` fixture
- **Live:** LinkedIn Easy Apply on real job link — verify modal detection → field scan → fill → dry run screenshot
- **Regression:** Run ASOS SmartRecruiters form again to verify thin strategy produces same results
- **Regression:** Greenhouse/Workday form experience should not change

## Success Criteria

1. LinkedIn Easy Apply: `form_experience.field_types` is non-empty after a dry run
2. SmartRecruiters: same screening questions answered, adapter file < 80 lines
3. No hardcoded PII in `native_form_filler.py` — grep for "united kingdom", "male", "asian", "+44" returns 0 hits
4. All existing tests pass
