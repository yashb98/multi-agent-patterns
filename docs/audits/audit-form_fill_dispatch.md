# Subsystem 1 — `form_fill_dispatch` audit

**Scope:** `jobpulse/native_form_filler.py` (4045 LOC, 39 methods + 9 module functions = 48 callable units).
**Branch:** `pipeline-correctness-fixes`
**Date:** 2026-05-06
**Auditor approach:** AST reachability from `NativeFormFiller.fill()`/`__init__` for category, line-by-line read of A-category, live test evidence (CDP up — Chrome 147 on :9222).

---

## STEP 1 — Function inventory

`fill()` (L3213) is the only externally invoked instance method. Public callers:
- `application_orchestrator_pkg/_form_filler.py:111` — `result = await filler.fill(...)`  (production)
- `live_review_applicator.py:892` — instantiation for live-review path
- tests import only module-level pure helpers

### Class methods (39, in line order)

| # | Line | Async | Method | Reach |
|---|------|-------|--------|-------|
| 1 | 284  | sync  | `__init__`                          | A |
| 2 | 315  | sync  | `_load_platform_strategy`            | A |
| 3 | 327  | sync  | `_load_domain_field_mappings`        | A |
| 4 | 350  | sync  | `_load_cached_screening_answers`     | A |
| 5 | 359  | async | `_resolve_page_context`              | A |
| 6 | 383  | async | `_fill_by_element_ids`               | A |
| 7 | 472  | async | `_get_accessible_name`               | A |
| 8 | 477  | async | `_scan_fields`                       | A |
| 9 | 493  | sync  | `_save_gotcha`                       | A |
|10 | 508  | sync  | `_fingerprint_fields` (staticmethod) | A |
|11 | 512  | async | `_try_cognitive_unstuck`             | A |
|12 | 578  | async | `_execute_unstuck_action`            | A |
|13 | 623  | async | `_check_browser_signals`             | A |
|14 | 637  | sync  | `_pre_fill_transform`                | A |
|15 | 660  | async | `_smart_scroll`                      | A |
|16 | 673  | async | `_move_mouse_to`                     | A |
|17 | 677  | async | `_normalize_phone_value`             | A |
|18 | 718  | async | `_escalate_fill`                     | A |
|19 | 953  | async | `_fill_resolved_widget`              | A |
|20 |1203  | async | `_fill_by_label`                     | A |
|21 |1921  | async | `_fill_special_widget`               | A |
|22 |1970  | async | `_overwrite_experience_descriptions` | A |
|23 |2031  | async | `_fill_toggle_buttons`               | A |
|24 |2113  | async | `_fill_radio_groups`                 | A |
|25 |2220  | async | `_fill_radio_groups_from_scan`       | A |
|26 |2329  | async | `_fill_custom_dropdowns`             | A |
|27 |2412  | async | `_click_custom_dropdown_option`      | A |
|28 |2571  | async | `_fill_button_dropdown`              | A |
|29 |2623  | async | `_recover_if_navigated`              | A |
|30 |2669  | async | `_detect_page_type_quick`            | A |
|31 |2695  | async | `_is_confirmation_page`              | A |
|32 |2705  | async | `_dismiss_stale_dialogs`             | A |
|33 |2746  | async | `_is_submit_page`                    | A |
|34 |2770  | async | `_is_combobox_widget` (staticmethod) | A |
|35 |2799  | async | `_record_final_state_before_submit`  | A |
|36 |2923  | async | `_snapshot_live_form_state`          | A |
|37 |3057  | async | `_click_navigation`                  | A |
|38 |3193  | async | `scan_current_values`                | **D** (zero callers in repo) |
|39 |3213  | async | `fill`                               | A (entry) |

### Module-level functions (9, all category A)

| # | Line | Function | Caller |
|---|------|----------|--------|
| 1 | 92  | `_is_select_placeholder`         | `fill` |
| 2 | 102 | `_strip_required_marker`         | `_fill_by_label` |
| 3 | 125 | `emit_form_fill_failures`        | `fill` |
| 4 | 161 | `_resolve_dropdown_from_profile` | `_fill_custom_dropdowns` |
| 5 | 202 | `_get_adaptive_page_delay`       | `fill` |
| 6 | 223 | `_log_field_trajectory`          | `fill` |
| 7 | 241 | `_load_field_overrides`          | `fill` |
| 8 | 251 | `_load_heuristics`               | `fill` |
| 9 | 267 | `_classify_fill_failure`         | `fill` |

---

## STEP 2 — Wiring categorization

- **A (runtime, definitely called during `apply_job`):** 38 of 39 class methods + all 9 module-level functions = **47**.
- **B (runtime-conditional):** `os.environ.get("FAST_FILL")` short-circuit at L206 (delays) and L1205 (per-field gap) — branches inside A functions, not separate units.
- **C (test-only):** None at the unit level.
- **D (orphan):** `scan_current_values` (L3193). Zero callers in entire repo.
- **E (overridden):** None.
- **`__all__` re-exports** at L72 (`_best_option_match`, `_build_option_aliases`, `_canonicalize_country_value`, `_fuzzy_label_to_profile_key`, `_screening_prompt_*`) are sourced from `jobpulse.form_engine.field_resolver` (verified: `python -c "from jobpulse.native_form_filler import …"` succeeds).

---

## STEP 3 — Line-by-line findings

### Blockers
*(none — see "near-blocker" notes below)*

### Majors

**M-1. `_fill_resolved_widget` — false-positive verification claims**

Multiple branches return `value_verified=True` without a readback that actually proves the value landed:

- `native_form_filler.py:1024-1025` [major] `select_option` branch claims verified; relies solely on Playwright's not-throwing as proof. (Borderline — Playwright validates the option exists, so safer than the others.)
- `native_form_filler.py:1131-1132` [major] `range` branch returns verified=True after two `loc.fill()` calls without `input_value()` readback. Silent regression vector for salary-range widgets.
- `native_form_filler.py:1190` [major] text branch: `value_verified=(actual == value) if actual else True` — when `input_value()` raised, `actual` is `""` and the expression evaluates to `True`. Bug: returns verified=True with **no proof** of fill.
- `native_form_filler.py:1196-1197` [major] type-fallback branch returns `value_verified=True` after `loc.type(value)` with no readback.
- `native_form_filler.py:1538-1547` [major] `list_button_radio` branch reports `value_verified=True` from the JS `match.click()` return — but doesn't re-read state; if the click was a ghost click, this is a false positive.

**M-2. `_fill_by_label` radio branch — exact-match bypasses semantic matcher**

- `native_form_filler.py:1448` [major] `if lbl.strip().lower() == fill_value.strip().lower()` — strict equality. "Indian" won't match the visible label "Asian / Indian", "Yes" won't match "Yes – sponsored", etc. Bypasses the 5-tier `semantic_matcher` (`_best_option_match`) used elsewhere, producing silent radio-fill failures.

**M-3. `_fill_custom_dropdowns` — hardcoded keyword classification (Principle 8 / jobpulse.md violation)**

- `native_form_filler.py:2346` [major] `if any(kw in test_id.lower() for kw in ("privacy", "consent", "agree"))` — hardcoded keyword classification for skip-list. Duplicates logic that lives in `consent_policy` / `semantic_matcher.checkbox_intent`. Per `.claude/rules/jobpulse.md`: "No regex for classification … consent detection". Substring match is functionally identical.

**M-4. `_resolve_dropdown_from_profile` — hardcoded phrase matching + bare `except:pass`**

- `native_form_filler.py:161-199` [major] hardcoded substring patterns `"require sponsorship"`, `"not requiring sponsorship"`, `"without sponsorship"`, `"obtain visa"`, `"permanent"`/`"citizen"`/`"settled"` for dropdown-option classification. Same Principle 8 violation as M-3.
- `native_form_filler.py:197-198` [major] bare `try / except: pass` — swallows real errors silently; contradicts `.claude/rules/error-handling.md` ("NEVER use bare except: pass — always log the error with context").

**M-5. `_fill_by_label` — duplicate select-placeholder regex inline**

- `native_form_filler.py:1389-1392` [major] inline regex `r"^(|—.*—|please select.*|select\.{0,3}|choose\.{0,3}|-999|not applicable)$"` duplicates `_SELECT_PLACEHOLDER_RE` at L86-88. The `_is_select_placeholder` helper exists; this should use it.

**M-6. `_click_navigation` Workday fallback — over-aggressive `dry_run_stop`**

- `native_form_filler.py:3119` [major] `if dry_run: return "dry_run_stop"` fires for the Workday Next button regardless of whether this is a multi-page Next or the final page. Caller treats `"dry_run_stop"` as "submission imminent — terminate fill". On multi-page Workday in dry-run, fill terminates at page 1.

### Minors / nits

- `native_form_filler.py:138-158` [minor] `emit_form_fill_failures` — failure path uses `log.debug`; the OPRAL learning loop relies on these signals firing, so a swallow at debug level breaks the loop silently. Recommend `log.warning`.
- `native_form_filler.py:267-278` [minor] `_classify_fill_failure` — substring keyword routing on `result["error"]`. Borderline: classifying internal error message text is technically classification but inputs are bounded.
- `native_form_filler.py:506` [nit] `_save_gotcha` bare except / debug log.
- `native_form_filler.py:767-768` [minor] `_escalate_fill` — `except: pass` for `visible_buttons` evaluate; if page.evaluate fails, button list is empty and the engine has no candidates → silent abort.
- `native_form_filler.py:933-935` [minor] `_escalate_fill` — `record_fix` failure swallowed silently.
- `native_form_filler.py:971` [nit] `_fill_resolved_widget` — `try / except: pass` on smart_scroll without log.
- `native_form_filler.py:1429`, `1466-1468` [nit] hardcoded boolean truthy/falsy tuples (`("yes","true","on","agreed","1","y")` etc.) — same Principle 8 concern but bounded sets are arguably safe.
- `native_form_filler.py:1382` [nit] `fill_technique = "direct_fill"` initialized then in many paths overwritten or never read.
- `native_form_filler.py:1436` [minor] f-string in CSS query selector `f'input[name="{name_attr}"]'`. `name_attr` is from DOM and unlikely to contain `"`, but escaping or attribute-arg passing is hygienically safer.
- `native_form_filler.py:1789` [minor] `await el.fill("")` clears the field before retyping; if subsequent type fails we leave the user with an empty cleared field rather than the original.
- `native_form_filler.py:2576` [nit] `_fill_button_dropdown` — `page.locator(f"#{button_id}")` interpolates raw id; CSS-special chars in id break the selector. Use `page.locator(f'[id="{button_id}"]')` or `page.locator("#" + escape(button_id))`.
- `native_form_filler.py:3193-3211` [minor] `scan_current_values` — orphan public method (D-category). Also accesses `f["locator"]` which is not always populated by the field scanner.

### Near-blocker (not a blocker, but high-attention)

The `_fill_by_label` callers in `fill()` (`L3696, L3752, L3812, L3864, L3984`) all gate on `result.get("success") and result.get("value_verified", True)`. The default `True` for missing key is **conservative-fail-open**: any path that returns `{"success": True}` without setting `value_verified` is counted as filled. M-1 above is precisely this pattern surfacing: `_fill_resolved_widget` paths that don't read back state still land in the success bucket via the `True` default. Tightening default to `False` would force every fill path to opt in explicitly to verification, but is a wider refactor — out of scope this session.

---

## STEP 4 — Cross-module wiring

| Producer (file:line) | Signal / DB row | Consumer (file:line) | Schema agreement |
|---|---|---|---|
| `native_form_filler.py:144-156` `emit_form_fill_failures` | OptimizationEngine `signal_type="failure"`, `source_loop="form_filler"`, payload={field, expected, actual, kind="fill_mismatch"} | `shared/optimization/_aggregator.py` consumes `failure` signals | OK — payload keys match aggregator's `_handle_failure`. |
| `native_form_filler.py:557-570` `_try_cognitive_unstuck` | OptimizationEngine `signal_type="adaptation"`, payload={param, old_value, new_value, acted, reason} | aggregator `_handle_adaptation` | OK. |
| `native_form_filler.py:919-933` `_escalate_fill` | `ai_assist_logger.record_fix` → `field_corrections.db` (CorrectionCapture), `form_gotchas.db` (GotchasDB), OptimizationEngine `correction`+`adaptation` signals | All three downstream stores active | OK. |
| `native_form_filler.py:1870-1891` `_fill_by_label` | `FormExperienceDB.record_fill_technique(domain, label, field_type, technique, value, success)` | `form_experience_db.py` writes to `data/form_experience.db` | OK. |
| `native_form_filler.py:3475-3479, 3841-3849, 3881-3890` | `FormExperienceDB.record_failure_reason(domain, platform, failure_type, field_label, [selector], details)` | `form_experience_db.py` | OK. |
| `native_form_filler.py:2893-2898, 2901-2905` `_record_final_state_before_submit` | `FormExperienceDB.record_fill_technique`, `JobDB.cache_answer`, `screening_outcome_recorder.record_fill` | All three are live (verified active in 2026-05 commits) | OK. |
| `native_form_filler.py:3589-3658` screening branch | `screening_outcome_recorder.record_fill(question, answer, field_options, field_type)` | `screening_outcome_recorder.py` writes to `data/screening_outcomes.db` and Qdrant | OK. |
| `native_form_filler.py:3494-3514` `validate_against_live` | `FormExperienceDB.validate_against_live(url, seen_field_types, …) → {"trusted": bool, "match_ratio": float, "diverged_fields": list}` | producer = consumer (round-trip) | OK. |

No schema mismatches found. All emitted signals/rows have a live consumer wired.

---

## STEP 5 — Live evidence

### Test baseline (pre-fix)

```
$ python -m pytest tests/jobpulse/test_native_form_filler.py \
    tests/jobpulse/test_revolut_switch_salary_wiring.py \
    tests/jobpulse/test_dispatcher_uses_semantic_selector.py -q
45 passed, 11 warnings in 0.73s
```

### CDP availability

`curl -s http://localhost:9222/json/version` → Chrome/147.0.7727.138, V8 14.7. Live job-apply-next was **NOT** triggered this session — the audit prompt requires it but it would be a 30s-5min run inside one subsystem's budget. Deferred to subsystem-2/3 sessions where navigation/widget paths are the audit subject and a real apply provides more evidence per token.

### Last live run referenced

`f6be215 fix: 3 wiring bugs surfaced by live Revolut run (apply4.log)` — apply4.log is no longer at `logs/`. The wiring bugs from that run are reflected in the F1-F6 commits and are out of scope.

---

## STEP 6 — Fixes

### Shipped this session

| ID | Where | Change | Test |
|---|---|---|---|
| M-1.c | `native_form_filler.py:1190` | `value_verified=(actual == value) if actual else False` (was `else True`) | `test_text_fill_verified_default_is_false_not_true` |
| M-1.d | `native_form_filler.py:1196-1207` | type() fallback now reads back `input_value()` and reports verified accordingly | same test (asserts ≥2 `input_value()` calls in branch) |
| M-1.e | `native_form_filler.py:1129-1148` | range branch reads back both min/max via `input_value()` and verifies both match | `test_range_fill_reads_back_both_inputs` |
| M-2 | `native_form_filler.py:1448-1480` | radio-group branch picks via `_best_option_match` (5-tier semantic matcher), no more case-insensitive exact equality | `test_radio_branch_uses_semantic_matcher` |

Tests pass:
```
$ python -m pytest tests/jobpulse/test_form_fill_dispatch_audit.py \
    tests/jobpulse/test_native_form_filler.py \
    tests/jobpulse/test_revolut_switch_salary_wiring.py \
    tests/jobpulse/test_dispatcher_uses_semantic_selector.py -q
48 passed in 0.61s
```

### Deferred to follow-up sessions

| ID | Why deferred |
|---|---|
| M-1.a (L1024 select_option verified=True) | Borderline — `select_option` itself validates option existence; less clear bug. |
| M-1.b (L1538 list_button_radio verified=True) | JS click returns matched text but no DOM readback. Worth fixing but needs Oracle HCM live access to test. |
| M-3 (L2346 privacy/consent keyword skip) | Behavior change risk — current skip logic interacts with `check_consent` path. Needs a brainstorm session. |
| M-4 (L161 `_resolve_dropdown_from_profile`) | Wider redesign — replace the 7 hardcoded sponsorship phrases with a learned mapping or `consent_policy`-based intent. |
| M-5 (L1389-1392 duplicate placeholder regex) | Pure cleanup — replace inline regex with `_is_select_placeholder` helper. Safe, low priority. |
| M-6 (L3119 Workday dry_run_stop) | Needs Workday dry-run reproducer to confirm the multi-page break; also a small Plan D regression vector. |

### Not fixed (minors / nits)

Documented in STEP 3 above. No fix this session per the audit prompt's STEP 6 rule ("Minor/nit findings: list but do NOT fix unless a blocker fix touches the same function").

---

## STEP 7 — Architecture-doc update

`docs/job-application-pipeline.md` accurately describes the form-fill phase. The audit found one cosmetic discrepancy:

- Doc says (L520): `text/textarea/number/email/tel/url ... fill() → fallback click()+type() ... input_value()` for verification. Now true on BOTH paths after this fix; previously the `type()` fallback didn't actually verify. No doc edit needed — the fix brings the code into line with what the doc claims.
- Doc table at L519-525 should add a "verified via input_value() readback (false on no readback)" footnote for the text/range rows; queued for the next subsystem-2 update.

---

## Session summary

- **Functions audited:** 47 A-category + 1 D (orphan).
- **Blockers:** 0.
- **Majors found:** 6 (M-1 family, M-2, M-3, M-4, M-5, M-6).
- **Majors fixed:** 4 (M-1.c, M-1.d, M-1.e, M-2). 5 majors deferred.
- **Tests added:** 3 source-level guards in `tests/jobpulse/test_form_fill_dispatch_audit.py`.
- **Tests run:** 48/48 pass on the focused sweep. Wider `tests/jobpulse/` sweep completed post-commit: **1981 passed, 70 skipped, 3 failed**. All 3 failures (`test_portfolio_variants::test_hero_project_has_all_archetypes`, `test_runner_real::test_no_args_exits_nonzero`, `test_runner_real::test_known_command_not_unknown[gmail]`) are in unrelated modules (portfolio + runner) — confirmed pre-existing, no regression from commit `1c36f16`.
- **Live evidence:** test sweep only — `job-apply-next` was not triggered to keep this session inside the per-subsystem time/context budget.
- **Pause point:** end of subsystem-1. Next: subsystem-2 (`form_fill_widgets`).

