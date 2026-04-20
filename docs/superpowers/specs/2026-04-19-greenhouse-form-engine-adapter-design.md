# Greenhouse Form Engine Adapter

**Date:** 2026-04-19
**Status:** Approved
**Approach:** A + B (Thin Adapter + Form Engine + LLM Field Mapper fallback)

## Summary

Restore the Greenhouse Playwright adapter, replacing all hardcoded selectors with the existing `form_engine/` pipeline. The adapter dynamically detects, classifies, and fills every field on the page using DOM analysis. A 3-tier field mapper (label match → attribute match → LLM fallback with SQLite cache) ensures the adapter handles both standard and unusual fields, learning over time.

## Architecture

```
GreenhouseAdapter.fill_and_submit(url, cv_path, ...)
  │
  ├─ 1. Playwright: open page (headed, anti-detection flags)
  ├─ 2. cookie_dismisser.dismiss_cookies(page)
  ├─ 3. scan_visible_fields(page)
  │     └─ querySelectorAll visible inputs/selects/textareas
  │     └─ detect_input_type() on each → list[FieldInfo]
  │     └─ extract label text for each field
  │     └─ sort by DOM order (top-to-bottom)
  │
  ├─ 4. map_fields_to_values(fields, profile, custom_answers)
  │     └─ Tier 1: label keyword match
  │     └─ Tier 2: attribute/id/autocomplete match
  │     └─ Tier 3: LLM fallback → SQLite cache per (domain, field_label_hash)
  │
  ├─ 5. Fill loop (ascending DOM order)
  │     └─ fill_field_by_type(page, field, value) for each
  │     └─ fill_file_upload() for CV (once only, deduplicated)
  │     └─ fill_file_upload() for CL if field detected
  │
  ├─ 6. answer_screening_questions(page, job_context)
  │     └─ pattern match → LLM fallback → SQLite cache
  │
  ├─ 7. Validate
  │     └─ scan_for_errors(page)
  │     └─ find_required_unfilled(page)
  │     └─ retry unfilled fields once (re-detect, re-fill)
  │
  └─ 8. Screenshot → return result
```

## Field Mapping

### Tier 1: Label Keywords (instant, free)

| Label contains | Maps to |
|----------------|---------|
| "first name" | `profile["first_name"]` |
| "last name" | `profile["last_name"]` |
| "email" | `profile["email"]` |
| "phone" | `profile["phone"]` |
| "linkedin" | `profile["linkedin_url"]` |
| "website" | `profile["website"]` |
| "location", "city" | `profile["location"]` |

### Tier 2: Attribute Fallback (instant, free)

Matches `id`, `name`, or `autocomplete` HTML attributes:
- `id="first_name"`, `name="first_name"`, `autocomplete="given-name"` → `profile["first_name"]`
- `id="email"`, `autocomplete="email"` → `profile["email"]`
- And so on for all standard profile keys.

### Tier 3: LLM Mapper (1-2s first run, cached after)

- Prompt: field label + surrounding context + available profile keys + available custom_answer keys
- Response: which profile/custom key to use, or "skip"
- Cached in SQLite keyed by `(domain, field_label_hash)` in `data/field_mapper_cache.db`
- Cost: ~$0.002 per unmapped field, $0.00 on cache hit
- Uses `smart_llm_call()` from `shared/agents.py`

## File Upload Deduplication

- Track uploaded files in a set during the fill loop
- CV: first `file` input matching resume/cv keyword in label/id/name → upload once, skip duplicates
- CL: only if a second file input matches cover/letter keyword AND `cover_letter_path is not None`
- If no file input found for CV → return error (critical field)

## Screening Questions

- Detected AFTER standard fields are filled (they often appear conditionally on Greenhouse)
- Routed through existing `answer_screening_questions()` on base class
- Flow: pattern match → LLM fallback → SQLite cache
- Uses `_job_context` from custom_answers for context-aware answers

## Validation & Retry

1. After filling all fields: `scan_for_errors(page)` checks for visible error banners
2. `find_required_unfilled(page)` catches fields that silently rejected input
3. If unfilled fields found: re-detect type, re-attempt fill (one retry only)
4. If errors persist after retry: screenshot + return `success: False`

### Failure Modes

| Failure | Handling |
|---------|----------|
| Greenhouse page structure changed | Form engine detects whatever is on page dynamically |
| Combobox dropdown stuck | `fill_custom_select` retries click, filters placeholders |
| File input not found | Return error, never silently skip CV |
| LLM mapper returns invalid key | Validate against known profile/custom keys, skip if invalid |
| Slow page load | `wait_for_load_state("networkidle")` + `get_wait_override()` |
| Modal/popup blocks form | Cookie dismisser runs first; unexpected popup → screenshot + error |

## Return Structure

```python
{
    "success": bool,
    "screenshot": Path | None,
    "error": str | None,
    "fields_filled": int,
    "fields_skipped": int,
    "cache_hits": int,
}
```

Extra tracking fields are additive. Existing callers ignore them via `dict` return type.

## Latency

| Step | Time | Cost |
|------|------|------|
| Open page | 2-4s | Free |
| Cookie dismiss | 200-500ms | Free |
| Scan fields | 300-800ms | Free |
| Label + attribute match | 1-5ms | Free |
| LLM mapper (per field, first run) | 1-2s | ~$0.002 |
| Fill loop (15-25 fields) | 3-6s | Free |
| Screening questions | 50ms cached / 1-2s LLM | ~$0.002 if LLM |
| Validate + retry | 500ms-2s | Free |
| Screenshot | 200-400ms | Free |

- **First run:** ~8-15s, ~$0.004-0.008
- **Cached runs:** ~6-12s, $0.00

## Key Invariants

- Zero hardcoded form selectors (URL detection only)
- Fields filled in ascending DOM order
- CV uploaded exactly once (deduplicated)
- `_`-prefixed keys filtered from custom_answers
- Selector overrides applied via `resolve_selector()` on every query
- All LLM calls via `smart_llm_call()` from `shared/agents.py`
- Tests use `tmp_path`, never touch `data/*.db`

## Files

| File | Action | Purpose |
|------|--------|---------|
| `jobpulse/ats_adapters/greenhouse.py` | Restore + rewrite | Main adapter, wired to form engine |
| `jobpulse/greenhouse_field_mapper.py` | New | 3-tier field mapping + SQLite cache |
| `jobpulse/greenhouse_field_scanner.py` | New | `scan_visible_fields()` wrapping form engine detection |
| `tests/jobpulse/test_greenhouse_adapter.py` | New | Adapter tests (URL, fill, dry run, validation) |
| `tests/jobpulse/test_greenhouse_field_mapper.py` | New | Mapper tests (tier 1/2/3, cache, dedup) |

## Tests

| Test | Verifies |
|------|----------|
| `test_detect_greenhouse_url` | URL matching: greenhouse.io, boards.greenhouse |
| `test_detect_rejects_non_greenhouse` | Negative: LinkedIn, Indeed, Lever |
| `test_label_mapping_tier1` | All standard label → profile mappings |
| `test_attribute_mapping_tier2` | id/name/autocomplete fallback |
| `test_llm_fallback_tier3` | Unmapped field triggers LLM, caches result |
| `test_llm_cache_hit` | Cached mapping skips LLM |
| `test_fill_order_dom_ascending` | DOM order preserved |
| `test_cv_upload_dedup` | Two file inputs → CV uploaded once |
| `test_cl_upload_only_when_detected` | No CL field → no CL upload |
| `test_internal_keys_filtered` | `_job_context` etc. stripped |
| `test_validation_retry` | Required unfilled → retry → success |
| `test_validation_retry_exhausted` | Persistent error → success: False |
| `test_dry_run_no_submit` | dry_run=True → screenshot, no submit |
