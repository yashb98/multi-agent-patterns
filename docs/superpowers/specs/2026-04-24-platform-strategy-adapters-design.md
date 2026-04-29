# Platform Strategy Adapters — Design Spec

**Date:** 2026-04-24
**Status:** Approved
**Goal:** Split monolithic PlaywrightAdapter into per-platform strategy files with shared form-filling core, and add platform-scoped DB tables for field/screening pattern memory.

---

## Problem

- `NativeFormFiller` (1837 lines) handles LinkedIn, Greenhouse, Lever, Workday, Indeed, Reed, and generic forms in a single class with scattered platform conditionals.
- `PlaywrightAdapter` (68 lines) is a thin pass-through with no platform awareness — it delegates everything to `ApplicationOrchestrator` which passes a `platform` string around.
- Platform-specific behavior (page timing, field mapping, navigation selectors, pre-fill hooks) lives in dicts and `if platform ==` checks instead of structured, testable units.
- `SmartRecruitersAdapter` (1348 lines) is fully standalone and shares zero logic with the common pipeline.
- `form_experience.db` stores per-domain but doesn't capture platform-level field/screening patterns that could be reused across domains on the same ATS.

## Approach

**Strategy pattern (option B):** Keep `NativeFormFiller` as the shared fill loop. Extract platform-specific behavior into per-platform strategy classes that plug in via a `BasePlatformStrategy` ABC. Each strategy provides overrides; the filler consults the strategy at each decision point.

---

## Architecture

### File Structure

```
jobpulse/ats_adapters/
├── __init__.py                # get_adapter() + get_strategy()
├── base.py                    # BaseATSAdapter (unchanged)
├── strategy.py                # BasePlatformStrategy ABC + registry
├── linkedin.py                # LinkedInStrategy
├── greenhouse.py              # GreenhouseStrategy
├── lever.py                   # LeverStrategy
├── workday.py                 # WorkdayStrategy
├── indeed.py                  # IndeedStrategy
├── reed.py                    # ReedStrategy
├── generic.py                 # GenericStrategy (fallback)
└── smartrecruiters.py         # SmartRecruitersAdapter (existing, unchanged)
```

### BasePlatformStrategy ABC

```python
class BasePlatformStrategy(ABC):
    name: str                          # e.g. "linkedin", "greenhouse"
    min_page_time: float = 5.0         # anti-detection delay per page
    max_form_pages: int = 20           # safety bound

    @abstractmethod
    def detect(self, url: str) -> bool:
        """Return True if this strategy handles this URL."""

    def extra_label_mappings(self) -> dict[str, str]:
        """Platform-specific label→profile_key mappings beyond the shared seed dict."""
        return {}

    async def pre_fill(self, page, cv_path, profile, custom_answers) -> dict:
        """Hook before form filling. Return dict with skip_fields, injected values, etc."""
        return {}

    async def post_page(self, page, page_num, result) -> None:
        """Hook after each page is filled (screenshots, logging, state tracking)."""

    def next_button_selectors(self) -> list[str]:
        """Ordered list of CSS selectors for next/submit buttons."""
        return []

    def screening_defaults(self) -> dict[str, str]:
        """Platform-specific default screening answers."""
        return {}

    async def custom_field_scan(self, page) -> list[dict] | None:
        """Override field scanning for platforms with non-standard DOM.
        Return None to use default scan."""
        return None

    def field_fill_overrides(self) -> dict:
        """Platform-specific fill behavior (e.g. typing delay, dropdown strategy)."""
        return {}
```

### Per-Platform Strategy Details

#### LinkedInStrategy
- `min_page_time = 3.0`
- `pre_fill`: LinkedIn auto-fills name/email from logged-in profile — return `skip_fields` for those
- `next_button_selectors`: `aria-label="Continue to next step"`, `aria-label="Review your application"`, `aria-label="Submit application"`
- `extra_label_mappings`: `{"headline": "headline", "phone country code": "phone_code"}`
- `screening_defaults`: `{"How did you hear about this job?": "LinkedIn"}`
- `field_fill_overrides`: human-like typing 50-150ms/char

#### GreenhouseStrategy
- `min_page_time = 5.0`
- React Select comboboxes: `custom_field_scan` returns fields with `aria-owns` scoping
- `next_button_selectors`: `button[type="submit"]`, `input[type="submit"]`
- Cover letter detection: checks for CL file upload field
- `extra_label_mappings`: Greenhouse-specific field labels (varies per employer config)

#### LeverStrategy
- `min_page_time = 5.0`
- Single-page forms (typically), no multi-step
- Cover letter detection via additional file upload
- `next_button_selectors`: `button[type="submit"]` (single page, just submit)

#### WorkdayStrategy
- `min_page_time = 45.0` (highest — aggressive detection)
- `custom_field_scan`: React controlled inputs need special value injection via `nativeInputValueSetter`
- 5-step form wizard, skills multiselect quirks
- Session timeout handling
- `extra_label_mappings`: Workday-specific labels

#### IndeedStrategy
- `min_page_time = 10.0`
- Conservative — aggressive anti-automation detection
- `field_fill_overrides`: extra delays, mouse movement simulation

#### ReedStrategy
- `min_page_time = 5.0`
- `pre_fill`: handles modal-based CV upload (detect CV mismatch → Update → file chooser)
- Drag-drop CV upload fallback
- `screening_defaults`: Reed-specific dropdown values

#### GenericStrategy
- `min_page_time = 5.0`
- No overrides — pure fallback, uses all NativeFormFiller defaults
- Used when platform is unknown or unsupported

### Strategy Registry

```python
# In strategy.py
_STRATEGY_REGISTRY: dict[str, type[BasePlatformStrategy]] = {}

def register_strategy(cls):
    """Decorator to register a platform strategy."""
    _STRATEGY_REGISTRY[cls.name] = cls
    return cls

def get_strategy(platform: str | None) -> BasePlatformStrategy:
    """Return the strategy for a platform, or GenericStrategy."""
    key = (platform or "generic").lower()
    cls = _STRATEGY_REGISTRY.get(key)
    if cls is None:
        from jobpulse.ats_adapters.generic import GenericStrategy
        return GenericStrategy()
    return cls()
```

### Wiring Into NativeFormFiller

`NativeFormFiller.fill()` gains an optional `strategy` parameter:

```python
async def fill(self, platform: str, ..., strategy: BasePlatformStrategy | None = None):
    if strategy is None:
        strategy = get_strategy(platform)

    # Pre-fill hook
    pre = await strategy.pre_fill(self.page, cv_path, profile, custom_answers)
    skip_fields = pre.get("skip_fields", [])

    # Merge strategy label mappings
    label_map = dict(_SEED_LABEL_TO_PROFILE_KEY)
    label_map.update(strategy.extra_label_mappings())

    # Use strategy.min_page_time instead of _PLATFORM_MIN_PAGE_TIME dict
    min_time = strategy.min_page_time

    for page_num in range(1, strategy.max_form_pages + 1):
        # Custom field scan if strategy provides one
        fields = await strategy.custom_field_scan(self.page)
        if fields is None:
            fields = await self._scan_fields()

        # ... existing fill logic, using label_map and skip_fields ...

        # Post-page hook
        await strategy.post_page(self.page, page_num, page_result)

        # Anti-detection timing
        min_time = strategy.min_page_time
```

The `_PLATFORM_MIN_PAGE_TIME` dict is deleted. Platform-specific label overrides move into each strategy. The `platform` string is still passed for screening_answers and logging.

### Wiring Into PlaywrightAdapter

```python
class PlaywrightAdapter(BaseATSAdapter):
    async def fill_and_submit(self, url, ...):
        from jobpulse.ats_adapters.strategy import get_strategy

        platform = _detect_ats_platform(url)
        strategy = get_strategy(platform)

        # Pass strategy through to orchestrator → filler
        orchestrator = ApplicationOrchestrator(driver=driver, engine="playwright")
        result = await orchestrator.apply(
            ..., strategy=strategy,
        )
```

### Wiring Into ApplicationOrchestrator

`ApplicationOrchestrator.apply()` accepts `strategy` and passes it to `FormFiller`:

```python
async def apply(self, ..., strategy=None):
    # strategy flows through to NativeFormFiller.fill()
```

---

## DB Schema Changes

### Existing Table: Add Platform Index

```sql
ALTER TABLE form_experience ADD COLUMN ats_platform TEXT DEFAULT 'generic';
CREATE INDEX idx_fe_platform ON form_experience(ats_platform);
```

Backfill: the `platform` column already exists in `form_experience` — rename to `ats_platform` for clarity, or add `ats_platform` as a dedicated indexed column alongside it. The existing `platform` column stores the source platform (LinkedIn, Indeed), while `ats_platform` stores the ATS that handled the form (Greenhouse, Lever, etc.) — these differ for external redirects.

### New Table: Field Patterns

Per-platform field mapping memory. When the LLM maps a field label to a profile key and it succeeds, record it. Next time the same label appears on the same platform, skip the LLM call.

```sql
CREATE TABLE field_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    domain TEXT NOT NULL,
    field_label TEXT NOT NULL,
    field_type TEXT NOT NULL,
    profile_key TEXT,
    last_value TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, domain, field_label)
);
CREATE INDEX idx_fp_platform ON field_patterns(platform);
CREATE INDEX idx_fp_domain ON field_patterns(domain);
```

Usage: Before LLM Call 1 (map fields), query `field_patterns` for `(platform, domain)`. If `success_count > 0` and `failure_count == 0`, use the stored `profile_key` directly — no LLM needed.

### New Table: Screening Patterns

Per-platform screening answer cache. Supplements the existing `screening_answers.py` pattern matcher with learned answers.

```sql
CREATE TABLE screening_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    question_normalized TEXT NOT NULL,
    answer TEXT NOT NULL,
    success_count INTEGER DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'llm',
    job_context_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, question_normalized)
);
CREATE INDEX idx_sp_platform ON screening_patterns(platform);
```

`source` values: `pattern` (from screening_answers.py), `llm` (LLM-generated), `user_correction` (from correction_capture.py). User corrections always win.

### FormExperienceDB Changes

Add methods to read/write the new tables:

```python
class FormExperienceDB:
    # Existing methods unchanged

    def record_field_pattern(self, platform, domain, field_label, field_type,
                             profile_key, value, success: bool): ...

    def lookup_field_patterns(self, platform, domain) -> dict[str, str]:
        """Return {field_label: profile_key} for known successful mappings."""

    def record_screening_pattern(self, platform, question, answer, source): ...

    def lookup_screening_pattern(self, platform, question) -> str | None:
        """Return cached answer if success_count > 0."""
```

---

## What Changes

| File | Change |
|------|--------|
| `ats_adapters/strategy.py` | **New** — `BasePlatformStrategy` ABC + registry |
| `ats_adapters/linkedin.py` | **New** — `LinkedInStrategy` |
| `ats_adapters/greenhouse.py` | **New** — `GreenhouseStrategy` |
| `ats_adapters/lever.py` | **New** — `LeverStrategy` |
| `ats_adapters/workday.py` | **New** — `WorkdayStrategy` |
| `ats_adapters/indeed.py` | **New** — `IndeedStrategy` |
| `ats_adapters/reed.py` | **New** — `ReedStrategy` |
| `ats_adapters/generic.py` | **New** — `GenericStrategy` |
| `ats_adapters/__init__.py` | Add `get_strategy()` export |
| `native_form_filler.py` | Accept `strategy` param, delete `_PLATFORM_MIN_PAGE_TIME` dict, use strategy hooks |
| `playwright_adapter.py` | Resolve strategy, pass to orchestrator |
| `application_orchestrator_pkg/__init__.py` | Accept `strategy` param, pass to form filler |
| `application_orchestrator_pkg/_form_filler.py` | Accept `strategy` param, pass to NativeFormFiller |
| `form_experience_db.py` | Add `field_patterns` and `screening_patterns` tables + methods |

## What Doesn't Change

- `BaseATSAdapter` interface
- `SmartRecruitersAdapter` (already platform-specific)
- `form_engine/` field-type fillers
- `applicator.py` (still calls `get_adapter()`, adapter resolves strategy internally)
- `screening_answers.py` (strategies supplement it, don't replace it)

## Testing

- Unit test each strategy's `detect()`, `extra_label_mappings()`, `next_button_selectors()`
- Unit test `get_strategy()` registry for all platform names
- Unit test `FormExperienceDB` new tables with `tmp_path`
- Integration test: `NativeFormFiller.fill()` with strategy injection (mock page)
- Existing tests unchanged — `strategy=None` falls back to `GenericStrategy`

## Migration

- `_PLATFORM_MIN_PAGE_TIME` dict → delete, values move into each strategy's `min_page_time`
- Platform-specific label mappings currently in `_SEED_LABEL_TO_PROFILE_KEY` that are universal stay in the seed dict; platform-specific ones move to `extra_label_mappings()`
- Existing `form_experience` rows: backfill `ats_platform` from existing `platform` column
- No breaking changes — `strategy` param is optional everywhere, defaults to `GenericStrategy`
