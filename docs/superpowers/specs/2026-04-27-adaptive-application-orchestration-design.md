# Adaptive Application Orchestration — Design Spec

**Date:** 2026-04-27
**Status:** Approved
**Goal:** Raise application orchestration from 5.5/10 to 9.5+/10 — fix LinkedIn form scoping, generic platform failures, Workday slowness, and overall 64% fill success rate.

---

## Problem

Current application orchestration has four critical weaknesses:

1. **LinkedIn catastrophe** — `scan_form()` scans the entire page. Owen Thomas session filled 29 elements including navbar ("Home", "Me", "For Business"). `_NAV_NOISE_LABELS` regex is a brittle blocklist that can't keep up with LinkedIn DOM changes.
2. **Generic platform failures** — ASOS and Arm sessions failed (2/3). `GenericStrategy` is 13 lines with zero logic. No form container scoping, so headers/footers/sidebars pollute the scan.
3. **Workday 600s slowness** — `_PLATFORM_MIN_PAGE_TIME["workday"] = 45.0` means 45s anti-detection sleep per page. With 5 pages that's 225s in sleeps alone, plus 15s hydration waits and per-field delays.
4. **64% overall success rate** — Mapping and filling are disconnected: mapper suggests "United Kingdom" but dropdown only has "UK". No scan validation catches obviously wrong scans. Recovery pipeline doesn't know why fills failed.

## Design Principles

- **Learn, don't hardcode** — no regex blocklists, no hardcoded selectors, no fixed timings. The system learns from every successful fill and self-heals when DOM changes.
- **Container-first** — scope to the form container before scanning. Noise never enters the pipeline.
- **Options-aware mapping** — mapping and option selection happen together, not in separate phases. By the time `fill_by_label()` runs, the value is already the exact option text.
- **Both modes** — works autonomously (cron) with anti-detection and in Claude Code sessions with `FAST_FILL=true` for zero delays.

---

## Section 1: Adaptive Form Container Scoping

### Three-Tier Container Detection

**Tier 1 — Learned (fastest, zero cost):**
`FormExperienceDB` stores the successful `container_selector` per domain. On revisit, resolve it to a DOM node and scope the a11y scan to it. If it returns 0 fields (DOM changed), delete the stale selector and fall through to Tier 2.

**Tier 2 — Auto-Detect (works on any ATS, no code changes needed):**
Walk the full a11y tree to find all form-role nodes (textbox, combobox, radio, checkbox, file input). Compute their common DOM ancestor. Validate: ancestor has >=3 interactive children and contains a submit/next button. If valid, scope to it. This works universally because forms have a consistent structure: clustered interactive elements with a submit action.

**Tier 3 — Strategy Hint (bootstrap only):**
`strategy.form_container_hint()` returns an optional CSS selector. Used only when Tier 1 and 2 fail (first visit, unusual DOM). After successful fill, the hint gets overwritten by the learned selector in FormExperienceDB.

### Implementation

**`scan_form()` changes (form_scanner.py):**
- Accept optional `container_backend_node_id` parameter
- When provided, use `Accessibility.getPartialAXTree(backendNodeId=...)` instead of `getFullAXTree`
- Fallback: if `getPartialAXTree` fails (old CDP version), use `getFullAXTree` + filter nodes by checking DOM ancestry against the container

**Container resolution function (new, in field_scanner.py):**
```python
async def resolve_form_container(page, strategy, form_experience_db) -> str | None:
    """Returns CSS selector for the form container, or None for full-page scan."""

    # Tier 1: Learned
    domain = extract_domain(page.url)
    stored = form_experience_db.get_container(domain)
    if stored:
        container = page.locator(stored)
        if await container.count():
            return stored
        form_experience_db.delete_container(domain)  # stale

    # Tier 2: Auto-detect via common ancestor
    detected = await _detect_form_container(page)
    if detected:
        return detected

    # Tier 3: Strategy hint
    hint = strategy.form_container_hint()
    if hint:
        container = page.locator(hint)
        if await container.count():
            return hint

    return None  # full-page scan
```

**Auto-detect algorithm (`_detect_form_container`):**
1. Get full a11y tree via CDP
2. Collect all nodes with form roles (textbox, combobox, radio, checkbox, spinbutton)
3. For each node, resolve its backend DOM node ID
4. Walk up the DOM tree from each form node, collecting ancestors
5. Find the deepest common ancestor that contains >=3 form nodes
6. Validate: ancestor subtree also contains a button with submit/next/continue/apply semantics
7. Return the CSS selector for that ancestor (use ID if available, else build a path)

### What Gets Deleted

- `_NAV_NOISE_LABELS` regex in `form_scanner.py` — container scoping makes it unnecessary
- `scope_to_dialog()` in `field_scanner.py` — replaced by container resolution

---

## Section 2: Adaptive Timing

### Problem

`_PLATFORM_MIN_PAGE_TIME` is a hardcoded dict: Workday=45s, LinkedIn=3s, etc. These are guesses, not measurements. The 45s Workday delay is catastrophic at scale.

### Solution: Measured Timings in FormExperienceDB

**What gets measured per page:**
- `hydration_ms` — time from navigation to first interactive field appearing (poll a11y tree every 500ms until a form-role node exists)
- `fill_ms` — wall clock for the entire fill phase (mapping + filling all fields)
- `transition_ms` — time from clicking Next to next page's fields appearing

**FormExperienceDB schema additions:**
```sql
ALTER TABLE form_experience ADD COLUMN container_selector TEXT;
ALTER TABLE form_experience ADD COLUMN avg_hydration_ms INTEGER;
ALTER TABLE form_experience ADD COLUMN avg_fill_ms INTEGER;
ALTER TABLE form_experience ADD COLUMN avg_transition_ms INTEGER;
```

**How timings are used:**

First visit (no data):
- Use `strategy.wait_for_form_hydrated_ms()` as initial estimate
- Workday strategy changes from 15000ms (down from the pipeline's 45s)

Revisit (has data):
- Hydration wait: `stored_avg_hydration_ms * 1.3` safety margin
- Anti-detection page delay: `max(stored_avg_fill_ms * 1.1, strategy.min_page_time * 1000)`
- No separate `risk_delay_multiplier` stacking

**Two modes:**
- Autonomous cron: measured times with safety margins, anti-detection per-field gaps preserved
- Claude Code assisted: `FAST_FILL=true` env var disables all artificial delays (per-field gaps, page timing, hydration waits beyond actual load)

### What Gets Deleted

- `_PLATFORM_MIN_PAGE_TIME` dict in `native_form_filler.py`
- `risk_delay_multiplier` field and its stacking logic in `NativeFormFiller`
- Workday strategy `wait_for_form_hydrated_ms` reduced from 15000 to 10000 (initial estimate, overwritten by measurements)

### Projected Impact

Workday: 600s → ~75-90s (8x speedup)
LinkedIn: ~12s → ~10s (minor, already fast)
Generic: ~30s → ~20s (less conservative timing)

---

## Section 3: Fill Reliability — Scan Validation + Failure Classification

### Scan Validation Gate

After `scan_form()` returns fields, validate before proceeding:

```python
def validate_field_scan(fields, strategy, form_experience) -> ValidationResult:
    expected_min, expected_max = strategy.expected_field_range()
    domain_exp = form_experience.lookup(url) if form_experience else None

    if domain_exp and domain_exp.get("field_count"):
        expected_max = domain_exp["field_count"] * 1.5

    if len(fields) > expected_max:
        return ValidationResult(valid=False, reason="too_many_fields", count=len(fields))
    if len(fields) == 0:
        return ValidationResult(valid=False, reason="zero_fields")

    label_counts = Counter(f["label"] for f in fields)
    duplicates = sum(1 for c in label_counts.values() if c > 1)
    if duplicates > 3:
        return ValidationResult(valid=False, reason="duplicate_labels", count=duplicates)

    return ValidationResult(valid=True)
```

When validation fails: rescan with tighter container (if container was auto-detected) or try iframe resolution.

### Wire Dead Strategy Code

Three methods exist on `BasePlatformStrategy` but aren't called in the mapping pipeline:

1. `strategy.normalize_label(label)` — call in `seed_mapping()` before dict lookup
2. `strategy.extra_label_mappings()` — merge into `_FIELD_LABEL_TO_PROFILE_KEY` at the start of `seed_mapping()`
3. `strategy.screening_defaults()` — add as a tier in the screening answer pipeline (after pattern match, before LLM)

### Fill Failure Classification

Replace the current "retry everything via LLM" approach:

```python
def classify_fill_failure(result: dict) -> str:
    error = result.get("error", "")
    if "no field" in error.lower() or "not found" in error.lower():
        return "no_field"        # → normalize label, retry
    if "intercept" in error.lower() or "pointer" in error.lower():
        return "blocked"         # → dismiss overlay, retry same value
    if result.get("value_mismatch"):
        return "wrong_value"     # → try alternate format / semantic match
    if "readonly" in error.lower() or "disabled" in error.lower():
        return "readonly"        # → skip, don't waste LLM
    return "unknown"             # → LLM recovery
```

Each failure type routes to the right recovery:
- `no_field` → `strategy.normalize_label()` + retry locator with role fallback
- `blocked` → `_dismiss_stale_dialogs()` + retry same value
- `wrong_value` → `semantic_option_match()` with available options
- `readonly` → skip, record in gotchas DB
- `unknown` → existing LLM recovery pipeline

### Strategy Additions

```python
class BasePlatformStrategy:
    def form_container_hint(self) -> str | None:
        """Optional CSS selector hint for the form container."""
        return None

    def expected_field_range(self) -> tuple[int, int]:
        """Min, max expected fields per page for this platform."""
        return (1, 30)

    def validate_field_scan(self, fields: list[dict]) -> bool:
        """Platform-specific scan validation. Override for custom checks."""
        return True
```

Platform overrides:
- LinkedIn: `form_container_hint() → ".jobs-easy-apply-modal"`, `expected_field_range() → (3, 10)`
- Workday: `expected_field_range() → (3, 20)`
- Greenhouse: `form_container_hint() → "#application"`, `expected_field_range() → (3, 15)`
- Generic: `expected_field_range() → (1, 30)`

---

## Section 4: Semantic Field Understanding

### Options-Aware Mapping

**Change: Enrich fields with options BEFORE mapping phase.**

Currently `scan_form()` returns options for radiogroups but not comboboxes (require clicking to open). The new pipeline:

1. `scan_form()` returns fields with options for radio/select
2. For combobox fields: call `scan_combobox_options()` during the mapping phase, before value selection
3. All field dicts include `options: list[str]` by the time they reach `seed_mapping()`

**Change: `seed_mapping()` becomes options-aware.**

For text fields (no options): deterministic profile lookup, value as-is.
For constrained fields (radio/select/combobox with options): deterministic profile lookup → `semantic_option_match(desired_value, available_options)` → pick best option.

### Semantic Option Matching

New file: `jobpulse/form_engine/semantic_matcher.py`

**Six-tier matching cascade:**

```python
def semantic_option_match(
    desired_value: str,
    available_options: list[str],
    *,
    field_label: str = "",
    aliases: dict[str, tuple[str, ...]] | None = None,
    numeric_value: float | None = None,
) -> str | None:
    """Match a desired value to available options via cascading strategies."""

    # Tier 1: Exact match (case-insensitive, whitespace-normalized)
    # Tier 2: Canonical alias lookup (built-in synonym table)
    # Tier 3: Numeric range match (salary, age, experience years)
    # Tier 4: Token overlap score (Jaccard similarity, threshold >= 0.4)
    # Tier 5: Embedding similarity (Voyage cosine, threshold >= 0.75)
    # Tier 6: None — caller escalates to LLM
```

**Canonical alias table (built from real application data):**

```python
CANONICAL_ALIASES = {
    # Gender
    "male": ("man", "m", "he/him", "he/him/his", "masculine"),
    "female": ("woman", "f", "she/her", "she/her/hers", "feminine"),
    # Boolean
    "yes": ("true", "authorized", "i am", "i do", "i have", "y"),
    "no": ("false", "not authorized", "i am not", "i do not", "n"),
    # Ethnicity
    "indian": ("asian or asian british - indian", "south asian", "asian - indian"),
    "asian": ("asian or asian british", "east asian", "southeast asian"),
    # Visa
    "graduate visa": ("tier 4 graduate visa", "post-study work visa", "graduate route"),
    # Notice period
    "1 month": ("4 weeks", "one month", "30 days", "less than 30 days"),
    "2 weeks": ("14 days", "two weeks"),
    # Experience
    "2 years": ("2+ years", "2-3 years", "over 2 years"),
    "3 years": ("3+ years", "3-5 years", "over 3 years"),
}
```

### Checkbox Intent Detection

```python
def checkbox_intent(label: str, *, required: bool = False) -> bool | None:
    """Determine whether to check a checkbox based on its label.

    Returns True (check), False (don't check), or None (ambiguous).
    """
    label_lower = label.lower().strip()

    # Privacy/consent: always check (required for submission)
    if any(w in label_lower for w in ("privacy", "consent", "terms", "agree", "acknowledge", "confirm")):
        return True

    # Marketing opt-out: never check
    if any(w in label_lower for w in ("marketing", "newsletter", "promotional", "offers", "opt in")):
        return False

    # Required checkbox: check
    if required:
        return True

    return None  # ambiguous — skip or ask LLM
```

### Integration Point

The mapping function call changes from:
```python
# Old: mapping decides value, fill discovers options are wrong
mapping, llm_calls = await map_fields(url, fields, ...)
```
To:
```python
# New: fields enriched with options, mapping picks exact option text
enriched_fields = await enrich_with_options(page, fields)
mapping, llm_calls = await map_fields(url, enriched_fields, ...)
# mapping["Gender"] = "Man" (exact option text), not "Male" (our data)
```

---

## Section 5: New Pipeline Flow

### Before Fill Loop
```
1. Resolve container (Tier 1→2→3)
2. Load FormExperienceDB timing data
3. Load strategy (registered or generic auto-detect)
```

### Per Page
```
4. Scoped scan — CDP getPartialAXTree inside container
   → enrich: attach options to radio/select/combobox
5. Validate scan — field count, duplicate labels, zero fields
   → if invalid: rescan with tighter/wider container
6. Semantic mapping — seed_mapping with option awareness
   → strategy.normalize_label() before lookup
   → strategy.extra_label_mappings() wired in
   → semantic_option_match() for constrained fields
   → screening pipeline for question-like fields
   → LLM only for fields that survived all tiers
7. Fill — values are exact option text, direct fill
   → classify_fill_failure() routes to right recovery
   → checkbox_intent() for polarity
8. Adaptive timing — measured delay, not hardcoded
   → record fill_time, hydration_time, transition_time
9. Navigate — strategy selectors + unified fallback
```

### After Fill Loop
```
10. Store container selector in FormExperienceDB
11. Store measured timings in FormExperienceDB
12. Store successful field→value mappings for replay
```

---

## Files Changed

| File | Change |
|---|---|
| `jobpulse/form_scanner.py` | `scan_form()` accepts container node ID, uses `getPartialAXTree`. Delete `_NAV_NOISE_LABELS` regex. |
| `jobpulse/form_engine/field_scanner.py` | `scan_fields()` calls container resolution before scan. Delete `scope_to_dialog()`. Add `resolve_form_container()`. |
| `jobpulse/form_engine/field_mapper.py` | `seed_mapping()` calls `strategy.normalize_label()` + `extra_label_mappings()`. Options-aware mapping via `semantic_option_match()`. |
| `jobpulse/form_engine/semantic_matcher.py` | **New file.** `semantic_option_match()`, `checkbox_intent()`, `CANONICAL_ALIASES` table. |
| `jobpulse/form_experience_db.py` | Add `container_selector`, timing columns. `store_container()`, `get_container()`, `delete_container()`, `store_timing()`. |
| `jobpulse/native_form_filler.py` | Wire new pipeline: container resolution before loop, timing measurement, scan validation, failure classification. Delete `_PLATFORM_MIN_PAGE_TIME`, `risk_delay_multiplier`. |
| `jobpulse/ats_adapters/strategy.py` | Add `form_container_hint()`, `expected_field_range()`, `validate_field_scan()` to base class. |
| `jobpulse/ats_adapters/linkedin.py` | `form_container_hint()`, `expected_field_range()`. |
| `jobpulse/ats_adapters/workday.py` | Reduce `wait_for_form_hydrated_ms` to 10000. `expected_field_range()`. |
| `jobpulse/ats_adapters/generic.py` | Auto-detect logic as Tier 2 fallback. |
| `jobpulse/playwright_driver.py` | `_get_field_gap()` unchanged. Remove `risk_delay_multiplier` stacking. |

**Deleted:**
- `_NAV_NOISE_LABELS` regex (form_scanner.py)
- `scope_to_dialog()` (field_scanner.py)
- `_PLATFORM_MIN_PAGE_TIME` dict (native_form_filler.py)
- `risk_delay_multiplier` field (native_form_filler.py)

---

## Testing Strategy

**All tests use live URLs and real form data — no mocked DOM.**

### Live ATS Tests (CDP + Real Chrome)

Each test launches a real Chrome instance via CDP, navigates to a live application page, and runs the pipeline against real DOM.

**LinkedIn:**
- Navigate to a live Easy Apply job → open modal → run scoped scan → verify 0 navbar fields in results
- Verify `resolve_form_container()` returns the modal selector
- Verify field count is within `expected_field_range(3, 10)`

**Workday:**
- Navigate to a live Workday job (e.g. Expedia careers) → measure actual hydration time → verify pipeline uses measured timing on second run
- Verify total fill time < 120s (down from 600s)

**Generic (ASOS / Arm style):**
- Navigate to a generic ATS form → auto-detect container → verify scan excludes header/footer/nav fields
- Verify container stored in FormExperienceDB after successful scan

**Greenhouse / SmartRecruiters / iCIMS:**
- One live test per known ATS → verify container detection + field count + semantic matching all work

### Semantic Matcher Tests (real field data)

Test `semantic_option_match()` with real option lists captured from previous applications:

- Gender dropdowns: real options from Greenhouse ("Man", "Woman", "Non-binary", "Prefer not to say") → verify "male" maps to "Man"
- Salary ranges: real options from Workday ("£30,000 - £40,000", "£40,000 - £50,000") → verify 35000 maps to correct range
- Visa questions: real radio options from LinkedIn ("Yes", "No") → verify Graduate Visa context maps correctly
- Ethnicity: real options from SmartRecruiters ("Asian or Asian British - Indian", ...) → verify "Indian" matches

### Timing Tests

- Run fill on known domain → verify timings stored in FormExperienceDB
- Run fill again → verify stored timings used instead of strategy defaults
- Simulate stale container (DOM changed) → verify auto-detect triggers and new selector stored

### Regression Tests

- Run full pipeline on each of the 6 known ATS types → verify success rate >= 90%
- Verify no `_NAV_NOISE_LABELS` references remain in codebase
- Verify no `_PLATFORM_MIN_PAGE_TIME` references remain in codebase

---

## Success Criteria

| Metric | Current | Target |
|---|---|---|
| LinkedIn Easy Apply fill | Catastrophic (29 nav elements) | 0 nav elements, 95%+ fill accuracy |
| Generic platform fill | 33% success (1/3) | 85%+ success |
| Workday fill time | 600s | < 120s |
| Overall fill success rate | 64% (9/14) | 95%+ (target 9.5/10 score) |
| New ATS (no strategy) | Fails unpredictably | Auto-detect works first try |
| DOM changes | Regex maintenance required | Self-healing via re-detection |

---

## Three Parallel Workstreams

These are independent and can be implemented simultaneously:

**WS1: Adaptive Form Scoping (Sections 1 + 3 validation)**
Files: form_scanner.py, field_scanner.py, strategy.py, linkedin.py, generic.py, form_experience_db.py

**WS2: Adaptive Timing (Section 2)**
Files: native_form_filler.py, form_experience_db.py, workday.py, playwright_driver.py

**WS3: Semantic Matching (Sections 3 wiring + 4)**
Files: semantic_matcher.py (new), field_mapper.py, native_form_filler.py, strategy.py
