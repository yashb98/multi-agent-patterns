# S26-follow-up-N (P0) — Scanner-complete coverage + DOM pre-check + verified-state cache

**Date filed**: 2026-05-12
**Slot**: after M (Playwright `locator.screenshot()`), M-2 (Anthropic kwarg filter), M-4 (scanner_unfilled_required surfacing)
**Status**: SCOPED — ready for next session to execute

---

## TL;DR

The M slice and follow-ups (M-2, M-3, M-4, M-5, M-sidecar) shipped the architecture for clean per-field vision verification. Three coverage / efficiency gaps remain, each anchored in a real user-observed scenario from the 2026-05-12 verification session:

1. **Scanner-to-filler coverage gap** — the field scanner sees fields (e.g. Greenhouse's `Have you added your full legal name and surname...?*`) that the production form-filler silently drops because the screening pipeline has no category for introspection questions. M-4 surfaces this in the verifier sidecar, but the actual filler-side fix (extend screening pipeline) is still pending.

2. **No DOM-level pre-check before vision** — every field, regardless of type, goes through a 25–90 s vision call. For text/textarea/checkbox/radio/native-select, the browser already knows the answer deterministically via `input_value()` / `is_checked()` / `selectedOptions[0].textContent`. Reading the DOM first catches 70–80 % of fields in <10 ms and only escalates to vision for combobox + custom widgets where DOM state isn't trivially readable.

3. **No verified-state cache** — within a single dry-run the filler operates page-by-page (no re-fill within a page). But there's no `verified_fills.db` table keyed `(domain, label, value) → ts, page_hash` that future runs can consult. Every apply re-fills every field even when the DOM already shows the right value. With a cache, the filler can skip fills where (a) the value was verified-OK on this domain previously AND (b) the DOM still shows that value.

This slice closes all three gaps in one coherent change. The reason they're scoped together: the cache (N-3) is fed by the verifier (N-2 DOM pre-check produces the same `passed/mismatch_detected` tier), and the scanner-coverage gap (N-1) determines the universe of fields the verifier examines.

---

## Three binding requirements (user-stated, 2026-05-12)

### Requirement 1 — Verifier must cover EVERY scanner field

The current verifier verifies only fields the filler CLAIMED to fill. The user wants every scanner-discovered field to have a verdict, falling into one of these tiers:
- **filled + verified-correct** — DOM/vision confirms the rendered value matches the filler's claim
- **filled + verified-mismatch** — value rendered doesn't match the claim
- **scanner-saw-filler-skipped** — required, scanner saw, filler had no_mapping. **Surfaced via M-4.**
- **scanner-saw-not-fillable** — buttons, per-option radios — explicitly excluded from verification
- **vision-unavailable** — both vendors failed

The verifier's sidecar JSON must enumerate every scanner field in exactly one of these buckets. Today the verifier silently omits buckets 3 and 4 (M-4 only fixes bucket 3 for required fields). N-1 makes the bucketing complete.

### Requirement 2 — DOM-first verification, vision is fallback

For every fillable field, attempt DOM-level read first:

| Type | DOM read | Match logic |
|---|---|---|
| text / textarea / email / tel / url / number / password | `input_value()` | strip whitespace, case-insensitive equality against claim |
| checkbox | `is_checked()` | True iff claim is truthy ("1", "true", "yes", "on") |
| radio (per-option) | `is_checked()` on the input whose `value` attribute matches the claim's option text | exact match |
| select (native `<select>`) | `evaluate("el => el.selectedOptions[0]?.textContent")` | semantic match (whitespace/case insensitive) |
| combobox (React-Select, Greenhouse, Workday, custom) | DOM read is unreliable — the displayed value lives in a sibling element with platform-specific selectors | **escalate to vision** |
| custom_dropdown | same as combobox | **escalate to vision** |
| file | `evaluate("el => el.files?.length > 0")` | True iff filename in claim was uploaded (compare basename) |

Only fields that fall through DOM matching (combobox/custom_dropdown, or where DOM read raises) reach the vision call. Per-call vision cost drops by ~70–80 %, wall-clock drops similarly because most fields skip the 90 s qwen path entirely.

### Requirement 3 — Verified-state cache so the next run skips already-correct fields

A new SQLite table `data/verified_fills.db` keyed:
```sql
CREATE TABLE verified_fills (
    domain          TEXT NOT NULL,
    label_norm      TEXT NOT NULL,   -- stripped-required, lowercased
    field_type      TEXT NOT NULL,
    verified_value  TEXT NOT NULL,   -- the value confirmed in DOM
    ts              INTEGER NOT NULL,
    method          TEXT NOT NULL,   -- dom_input_value | dom_is_checked | dom_select_text | vision
    PRIMARY KEY (domain, label_norm, verified_value)
);
```

Reads:
- Before `_fill_by_label(label, value)`, the filler queries `verified_fills.lookup(domain, label_norm, value)`. If a row exists AND the DOM currently still shows `verified_value`, log `fill ⊘ reason=already_verified` and skip.
- If a row exists but the DOM now shows a different value, log `fill: re-fill, value_drifted` and proceed (the previous-verified value isn't there anymore).

Writes:
- After every `tier_reached == "passed"` verdict, write a row.
- After `mismatch_detected`, delete any row for that `(domain, label_norm)` so the next run knows not to trust the cache.

Forgetting:
- 30-day TTL — rows older than 30 days are auto-pruned by the daemon (no per-call cost).
- On scanner update (field appears with new options), domain is invalidated.

---

## The Plan

### Phase 1 — N-1: Scanner-complete coverage in verifier sidecar (~1 hour)

`verify_form_page` already has `field_metadata` (all scanner fields). Extend the sidecar JSON to enumerate every scanner field with its verification bucket:

```json
"scanner_coverage": {
    "total": 45,
    "filled_verified_passed": [...],            // verdict tier == "passed"
    "filled_verified_mismatch": [...],          // verdict tier == "mismatch_detected"
    "filled_vision_unavailable": [...],         // verdict tier == "vision_unavailable"
    "scanner_saw_filler_skipped_required": [...], // M-4 already does this
    "scanner_saw_filler_skipped_optional": [...], // NEW
    "scanner_noise_excluded": [...]             // buttons, per-option radios
}
```

Sidecar's `total` must equal scanner output count. Sum of all buckets must equal `total`. Cross-check assertion in `tests/jobpulse/form_engine/test_vision_verifier.py`.

### Phase 2 — N-2: DOM-level pre-check (~2 hours)

In `_field_crop.py`, add `read_dom_value(input_locator, ftype) -> Optional[str]` returning the rendered value when DOM read is deterministic, None when it isn't.

In `vision_verifier.py:_extract_field_crops`, before capturing the crop:
1. Call `read_dom_value(input_locator, ftype)`.
2. If non-None: compare against `claimed_value`. If match (per the table in Requirement 2), emit a `FieldCrop` with `crop_bytes=None`, `resolve_method="dom_match"`, and a pre-set verdict `tier_reached="passed"`. Skip the screenshot.
3. If non-None but mismatch: still capture the crop (vision will produce the canonical mismatch_detected verdict + observed_value).
4. If None: capture the crop as before.

In `verify_form_page`, before sending to vision, filter out the `dom_match` crops — they don't need vision verification. Vision only sees crops that DOM couldn't decide.

Tests:
- Add `test_dom_match_skips_vision_call` — fake a text input with `input_value()` returning the claim → assert vision is never called for that field.
- Add `test_dom_mismatch_still_calls_vision` — same input but DOM returns a different value → assert vision IS called.

### Phase 3 — N-3: Verified-state cache (~2 hours)

New module `jobpulse/form_engine/verified_fills_db.py`:
- `init_db(path)` — creates the schema.
- `lookup(domain, label, value) -> Optional[dict]` — returns row if found.
- `record(domain, label, type, value, method)` — upserts.
- `invalidate(domain, label)` — deletes rows for a label (called on mismatch).
- `prune(ttl_days=30)` — called by the daemon's hourly optimize tick.

Wire into:
- `vision_verifier.verify_form_page`: after each verdict, if `passed`, call `verified_fills_db.record(...)`. If `mismatch_detected`, call `verified_fills_db.invalidate(...)`.
- `native_form_filler._fill_by_label`: at the top, call `verified_fills_db.lookup(domain, label, value)`. If hit AND DOM currently still shows the cached value (cheap `read_dom_value` check), short-circuit with `success=True, skipped=already_verified`. Else proceed with fill.

Tests:
- `test_verified_fill_skips_refill` — pre-populate cache, run filler, assert no Playwright fill call was issued.
- `test_drifted_value_triggers_refill` — pre-populate cache, but DOM shows a different value → filler refills.

### Phase 4 — Audit doc + commit (~30 min)

Update `docs/audits/2026-05-10-semantic-audit-verified.md` with a new "S26-follow-up-N ✅ SHIPPED" section. Three commits:
- `feat(form_engine): S26-follow-up-N-1 — scanner-complete coverage in verifier sidecar`
- `feat(form_engine): S26-follow-up-N-2 — DOM-level pre-check skips vision on deterministic types`
- `feat(form_engine): S26-follow-up-N-3 — verified_fills.db cache + filler short-circuit`

Plus one docs commit.

---

## Acceptance gates

- **N-G1** — `scanner_coverage.total` in every sidecar matches the field scanner's count; sum of buckets equals total. Live Greenhouse evidence shows 45 scanner fields fully bucketed.
- **N-G2** — On a live Greenhouse run, vision call count drops from 1 (all-fields composite) to ≤ 1 with `composite_layout.panels_total ≤ 5` (the comboboxes only). Text inputs (Email, First Name, Last Name, Phone, LinkedIn, Website) all hit DOM match.
- **N-G3** — `verified_fills.db` has rows after a successful dry-run; a second run on the same URL shows `fill ⊘ reason=already_verified` for the cached fields and skips the actual fill action.
- **N-G4** — Tests 22/22 in `test_vision_verifier.py` (19 existing + 3 new: dom_match_skips_vision, dom_mismatch_still_calls_vision, cache_short_circuits_filler).
- **N-G5** — `grep -nE "if (platform\|ats\|domain) ==" jobpulse/form_engine/{vision_verifier,_field_crop,verified_fills_db}.py` empty on the diff.
- **N-G6** — Audit doc has scanner-coverage table + before/after vision call counts + cache hit rate from a second dry-run.

---

## Open questions to resolve in the N session

1. **Combobox DOM read robustness**: some combobox implementations (Greenhouse) store the selected value as the input's `aria-activedescendant` pointing to an option in the listbox. Reading `aria-activedescendant` + finding that element's `textContent` would handle Greenhouse without vision. Test on the Graphcore live tab — if it works, demote combobox out of vision into DOM tier; if not, leave combobox in vision.

2. **Multi-page persistence**: when the filler navigates from page 1 to page 2, the `_fields_by_label` is rebuilt. The verified_fills.db lookup keys on `(domain, label_norm, value)` — does the same label appear on multiple pages? If yes, page_hash should be part of the key.

3. **Re-fill threshold**: the cache currently says "if DOM still shows verified_value, skip". But what about partial matches? If the cached value is "Tier 4 (General) Student Visa" and the form now offers only "Graduate Visa" (option removed), the lookup fails the DOM-still-matches check — but should we re-fill, or surface to user? Default: re-fill (the cache assumes the option still exists).

4. **Test isolation**: `verified_fills.db` is a production DB by default. Tests must use `tmp_path` per `.claude/rules/testing.md`.

5. **The screening-pipeline gap (legal name)** is OUT OF SCOPE for N — that's a separate slice (call it O) targeting `screening_pipeline.resolve` to handle introspection / consent / agreement question categories. N just makes the gap auditable; O fixes the gap.

---

## Goal-oriented engineering prompt for the next session

> **GOAL**: Cut the vision verifier's per-page latency by 70–80 % via DOM pre-check on deterministic field types, surface 100 % of scanner-discovered fields in the verifier sidecar's bucketed coverage report, and add a `verified_fills.db` cache so the second run on a domain skips already-verified fields entirely.
>
> **WHY**: Vision verification on Greenhouse currently takes 90–120 s per page for 11–14 fields because every fill goes through the cloud → fallback vendor chain. But the rendered values for text/textarea/checkbox/radio/native-select are already in the DOM at fill time — vision is unnecessary for them. Also, every dry-run today re-fills every field even when nothing has changed; a cache keyed `(domain, label, value)` lets the filler short-circuit. M-4 already surfaces filler-coverage gaps; N completes the bucketing so every scanner field has a verdict.
>
> **INVOKE WITH**: `claude --dangerously-skip-permissions`. Stays on `pipeline-correctness-fixes`. Touches `jobpulse/form_engine/_field_crop.py` (new `read_dom_value`), `jobpulse/form_engine/vision_verifier.py` (DOM-first + scanner_coverage bucketing), new `jobpulse/form_engine/verified_fills_db.py`, `jobpulse/native_form_filler.py` (cache lookup before fill), `tests/jobpulse/form_engine/test_vision_verifier.py` (3 new tests), `docs/audits/2026-05-10-semantic-audit-verified.md`.
>
> **READ FIRST (binding, in order)**:
> 1. `docs/audits/2026-05-12-verifier-coverage-and-cache-prompt.md` — this file.
> 2. `docs/audits/2026-05-12-vision-verifier-coverage-prompt.md` — M-2 sweep prompt (the still-pending matrix coverage).
> 3. `docs/audits/2026-05-10-semantic-audit-verified.md` — M section (line ~2117).
> 4. `jobpulse/form_engine/_field_crop.py` — the resolver cascade you're extending.
> 5. `jobpulse/form_engine/vision_verifier.py` — `_extract_field_crops`, `verify_form_page`.
> 6. `jobpulse/native_form_filler.py` — `_fill_by_label` (where the cache lookup goes), `_fields_by_label` (where `field_metadata` originates).
> 7. `data/audits/vision_verifier/1778596191_*.json` — live evidence of M-4's `scanner_unfilled_required` surfacing on Graphcore.
>
> **PRECONDITIONS (halt if any fail)**:
> - Branch `pipeline-correctness-fixes`, clean after M-4 (commit `b3488e6`).
> - Chrome CDP up on `localhost:9222`.
> - Daemon NOT running.
> - Graphcore tab still open (so live re-verify can use the existing filled form).
>
> **PROCESS DISCIPLINE**:
>
> - **DOM pre-check stays in `_field_crop.py`**, not `vision_verifier.py`. The verifier's responsibility is *orchestration + composite + sidecar*. The "can I read this value without vision?" decision lives at the field level.
> - **Cache writes happen at verifier level**, not at filler level. The verifier is the only thing that produces a `passed` verdict; the cache record follows the verdict.
> - **Cache reads happen at filler level** — the filler decides whether to skip a fill, not the verifier.
> - **No per-platform branches.** If a combobox implementation needs special handling, the fix is a new DOM-read strategy in the table (Requirement 2), never `if platform == "X"`.
> - **Time cap: 5 hours. Iteration cap: 2 per gate.**
>
> **CONSTRAINTS**:
> - `dry_run=True` only. Never auto-submit.
> - Forbidden to introduce per-platform branches in any touched file.
> - Forbidden to mock the cache in tests with file-backed SQLite — use `tmp_path`.
> - Forbidden to write cache rows for `mismatch_detected` (only `passed`).
> - The screening-pipeline fix for the legal name field is OUT OF SCOPE — N just surfaces it.
>
> **WHAT THIS SLICE EXPLICITLY DOES NOT DO**:
> - Doesn't fix the legal name field's fill gap (screening pipeline scope).
> - Doesn't change the resolver cascade in `_field_crop._resolve_form_row`.
> - Doesn't address L's G3 latency floor for vision-only fields (still environmental).
> - Doesn't change the verifier's tier names or `FieldVerdict` shape.
>
> **ACCEPTANCE (the SHIPPED state)**:
>
> N ships when N-G1 through N-G6 hold on a single iteration. Greenhouse Graphcore is the smoke test:
>   - Before: 14 fields → 14 vision verdicts (each through cloud or qwen)
>   - After: 14 fields → 6 DOM-match passed + 5 vision-via-composite passed + 3 scanner-coverage-bucketed (legal name combobox+text marked `scanner_saw_filler_skipped_required`; demographic duplicates collapsed via dedup as today)
>   - Second run: 0 fills attempted (all cached); only the demographic dedup pair + buttons trigger any verifier action.
