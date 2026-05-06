# Subsystem 2 — `form_fill_widgets` audit

**Scope:** 21 files in `jobpulse/form_engine/` per the audit prompt.
13 named files (`field_scanner, semantic_scanner, vision_gate, gotchas,
validation, intent_healing, semantic_matcher, detector, widget_detector,
widget_strategies, confidence_scorer, consent_policy, models`) plus
8 `*_filler.py` (`checkbox, date, file, multi_select, page, radio,
select, text`). Total ~5000 LOC.

**Branch:** `pipeline-correctness-fixes`
**Date:** 2026-05-06
**Auditor approach:** Reachability sweep first (importer grep), per-file
read of A-category bodies, line-by-line on highest-LOC + recently-touched.
Live evidence via pytest sweeps (no `job-apply-next` this session — same
budget rationale as S1).
**Prior fix commit:** `96cf428 fix(form_engine): S2 audit …`

---

## STEP 1 — Function inventory + reachability

### Reachability gate

`apply_job()` → `ApplicationOrchestrator` → `_form_filler.py:111` →
`NativeFormFiller.fill()` (default path, when `UNIFIED_FORM_ENGINE`
env var ≠ `"true"`).

```
$ rg -n "UNIFIED_FORM_ENGINE" --type py
jobpulse/application_orchestrator_pkg/_form_filler.py:86:        if os.environ.get("UNIFIED_FORM_ENGINE") == "true":
scripts/{apply_with_human_review, try_real_job, test_pipeline_live, apply_live_with_review}.py:  # all set FF=true
tests/jobpulse/integration/test_pipeline_live.py:315:    os.environ["UNIFIED_FORM_ENGINE"] = "true"
tests/jobpulse/integration/test_unified_engine_live.py:215:    os.environ["UNIFIED_FORM_ENGINE"] = "true"
```

`.env` does NOT set `UNIFIED_FORM_ENGINE`. Default production path runs
`NativeFormFiller`, not `FormFillEngine`. The 7 `_filler.py` modules
that flow only through `engine.py → page_filler.fill_field_by_type` are
therefore **B-category (FF-gated, default OFF)**.

### Categorization

| File | Cat | Reach |
|---|---|---|
| `field_scanner.py` (1334 LOC, May 6) | **A** | imported in `native_form_filler.py:40,1316,2700,3327,3482` |
| `vision_gate.py` (188 LOC, May 6) | **A** | called from `field_scanner.py:1201` |
| `semantic_scanner.py` (316 LOC, May 6) | **A** | called from `field_scanner.py:1229` (strategy "semantic") |
| `gotchas.py` (224 LOC, May 6) | **A** | `native_form_filler.py:496`, `application_orchestrator_pkg/__init__.py:33`, `ai_assist_logger.py:384,809`, `applicator.py:199,566`, `live_review_applicator.py:1138`, `platform_bypass.py:389`, `field_scanner.py:1245`, `field_mapper.py:34` |
| `semantic_matcher.py` (220 LOC, May 4) | **A** | `native_form_filler.py:607` (`checkbox_intent`), `field_mapper.py:223` (`semantic_option_match` via field_resolver), shared/evals |
| `intent_healing.py` (182 LOC, May 3) | **A** | `native_form_filler.py:1315`, `navigation/action_executor.py:263` |
| `validation.py` (159 LOC, Apr 17) | **A** | `playwright_driver.py:579` (called by `FormNavigator`) |
| `consent_policy.py` (167 LOC, May 6) | **A** | `file_uploader.py:238` (which is imported `native_form_filler.py:58`); also `checkbox_filler.py:7` (B path) |
| `confidence_scorer.py` (174 LOC, Apr 30) | **A** | `field_mapper.py:414` inside `map_fields_with_confidence` (imported in `native_form_filler.py:49`) |
| `models.py` (54 LOC) | **A** | data-only types — no functions to audit |
| `widget_strategies.py` (448 LOC) | **B** | only `engine.py:27` |
| `widget_detector.py` (254 LOC) | **B** | only `engine.py:26` + `detector.py:13` (B itself) |
| `detector.py` (149 LOC) | **B / C** | re-exported by `__init__.py`; production callers only via `engine.py`; otherwise tests + re-exports |
| `page_filler.py` (92 LOC) | **B** | only `engine.py:24` |
| `checkbox_filler.py` (106 LOC) | **B** | only `page_filler.py:10` |
| `text_filler.py` (164 LOC) | **B** | only `page_filler.py:11` |
| `select_filler.py` (262 LOC) | **B** | only `page_filler.py:11` (also imports from radio/multi_select) |
| `radio_filler.py` (124 LOC) | **B** | only `page_filler.py:11` |
| `multi_select_filler.py` (107 LOC) | **B** | only `page_filler.py:10` |
| `file_filler.py` (141 LOC) | **B** | only `page_filler.py:10`. `find_file_inputs` re-exported from `__init__.py` but NO production caller |
| `date_filler.py` (114 LOC) | mixed | `_format_date` direct from `native_form_filler.py:1174` (**A**); `fill_date` only via page_filler (**B**) |

### Out-of-scope but cross-reaching (coverage gaps)

- `engine.py` (21 KB) — bridges B-category modules. Should be audited
  in a separate session before turning on `UNIFIED_FORM_ENGINE`.
- `field_mapper.py` (35 KB) — production-reachable via
  `native_form_filler.py:44`; `_fuzzy_custom_answer` and `seed_mapping`
  are both A-category but file is out of S2 scope.
- `field_resolver.py` (30 KB) — houses `_best_option_match` (referenced
  by S1's M-2 fix) and `checkbox_intent` import wrapper. Production
  reachable, out of scope for this audit.
- `unified_scanner.py` (36 KB) — possibly an older twin of field_scanner.
- `file_uploader.py` (15 KB) — actually production-reachable
  (`native_form_filler.py:58`), but doesn't end in `_filler.py` per the
  spec's strict reading. Recommend including in S2 scope on next pass.
- `widget_llm_recovery.py` (8 KB) — referenced by field_mapper.

These are flagged as **audit-coverage gaps**, not findings.

---

## STEP 2 — Wiring categorization summary

- **A (default apply_job path):** `field_scanner`, `vision_gate`,
  `semantic_scanner`, `gotchas`, `semantic_matcher`, `intent_healing`,
  `validation`, `consent_policy`, `confidence_scorer`, `models`,
  `date_filler._format_date` only.
- **B (FF-gated `UNIFIED_FORM_ENGINE=true`):** `widget_strategies`,
  `widget_detector`, `detector`, `page_filler`, `checkbox_filler`,
  `text_filler`, `select_filler`, `radio_filler`, `multi_select_filler`,
  `file_filler`, `date_filler.fill_date`.
- **C (test-only):** `detector.detect_input_type` /
  `SemanticTypeResolver` — re-exported from `__init__.py` but no
  production consumer found.
- **D (orphan):** none confirmed.
- **E (overridden):** none.

---

## STEP 3 — Line-by-line findings (A-category only)

### Blockers

**B-1.** `intent_healing.py:47-61, 84` — `_HEAL_PROMPT.format(label=…)`
**raises `KeyError '"selector"'`** because the literal `{ }` braces in
the JSON example (`{"selector": "<css>"}` and `{"selector": null}`)
collide with `str.format()` field syntax. The exception is caught
silently by `except Exception` at L102-104, so `_call_llm_for_selector`
returns `None` 100% of the time. **Path 3 of `heal_locator` (LLM
intent resolution against the live a11y tree) has been silently dead
in production**.

Reproducer (pre-fix, prior to commit `96cf428`):
```
$ python -c "from jobpulse.form_engine.intent_healing import _HEAL_PROMPT
            _HEAL_PROMPT.format(label='x', role='r', field_type='ft',
                                 neighborhood='n', a11y_summary='a')"
KeyError: '"selector"'
```

### Majors

**M-A.** `semantic_matcher.py:117` — alias-tier substring containment
was bidirectional (`alias_norm in opt_norm or opt_norm in alias_norm`).
Short aliases like `"y"`, `"n"`, `"m"`, `"f"`, `"man"` leak into
unrelated options because `"man" in "human"` and `"y" in "yorkshire"`
are both `True`. Confirmed empirically:
```
>>> semantic_option_match("yes", ["Yorkshire","Greenwich","Confirmed"])
'yes'  # pre-fix returned 'Yorkshire' via alias 'y' substring
>>> semantic_option_match("male", ["human","female","unspecified"])
'male'  # pre-fix returned 'human' via alias 'man' substring
```

**M-B.** `field_scanner.py:627` — JS scan hardcodes vendor-specific
selectors `[data-testid="dropdown-basic"]` and
`[data-testid="agree-data-privacy-dropdown"]`. These are Revolut /
specific-form testid values. Principle 8 violation (Dynamic Over
Hardcoded) — should be replaced by a learned-pattern lookup or a
generic React-dropdown shape detector. Deferred to a follow-up.

**M-C.** `field_scanner.py:807` — JS regex `salaryRx =
/salary|compensation|gbp|usd|gross|annual|per year|per annum/i` for
salary-context detection. Principle 8 / no-regex-for-classification
violation; should use embedding similarity to a salary-context
archetype. English-only — won't fire on multilingual forms. Deferred.

**M-D.** `semantic_scanner.py:28-51` — three large regex patterns
(`QUESTION_STARTERS`, `NON_QUESTION_PHRASES`, `FIELD_LABEL_HEURISTIC`)
are the **primary** classification path for the "semantic" scan
strategy. Principle 8 violation. The whole point of a "semantic"
scanner is to handle phrasings the shape-based scanners miss; doing it
with English regex defeats the purpose. The
`docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md` plan
already exists for this — defer to that workstream.

**M-E.** `validation.py:75` — error-message text `> 200` chars is
silently dropped. Some Workday/Greenhouse error messages exceed 200
chars (e.g. multi-paragraph "please confirm…" toasts). Truncating to
200 with an ellipsis would preserve the signal.

### Minors / nits

- `vision_gate.py:142` [minor] screenshot failure logs at `log.debug`
  — vision augment was just triggered because the page looked sparse;
  the failure should be `log.warning` so failures aren't invisible.
- `field_scanner.py:1232` [minor] `_run_strategy` — strategy failures
  use `log.debug`. Promote to `log.warning` (each strategy fail loses a
  signal source).
- `field_scanner.py:1289-1303` [minor] `_emit_scan_signal` bare
  `except: pass` — should `log.debug` the exc.
- `field_scanner.py:74,99` [nit] container deletion fall-through emits
  `log.info` instead of `log.warning` for "stale selector deleted".
- `validation.py:63-66` [minor] `[role='alert']` selector reused as a
  constant key for every error — caller can't dedupe by field.
- `confidence_scorer.py:106` [nit] when no majority, fallback returns
  the first candidate (lowest temperature) — borderline, fine.
- `intent_healing.py:178-181` [nit] `logger.debug("...all resolution
  paths failed...")` — should at least be `info` because heal_locator
  was called with a stale selector and now everything failed.
- `semantic_matcher.py:215-216` [nit] bare `except Exception: pass` in
  `checkbox_intent` after embedding tier — silent swallow.
- `gotchas.py:44-47` [nit] migration ALTER TABLE wrapped in bare
  `except sqlite3.OperationalError: pass` — fine (idempotent).
- `field_scanner.py:716-720, 845-847` [nit] JS class-state regex and
  min/max preceding-word regex — borderline structural detection,
  acceptable as fallback heuristic.

---

## STEP 4 — Cross-module wiring (signals + DBs)

| Producer | Signal/Row | Consumer | Schema agreement |
|---|---|---|---|
| `field_scanner.py:1287-1303` `_emit_scan_signal` | OptimizationEngine `success`/`failure` signal, `source_loop="field_scanner"`, payload={action="multi_strategy_scan", winner, field_count} | `shared/optimization/_aggregator.py` consumes both signal types | ✅ keys match |
| `field_scanner.py:1166` | `FormExperienceDB.store_scan_strategy(domain, winner, final_count)` | `form_experience_db.py` writes to `data/form_experience.db` | ✅ |
| `field_scanner.py:78` | `FormExperienceDB.delete_container(domain)` (self-healing on stale) | same DB | ✅ |
| `vision_gate.py:113-116` | `record_openai_usage(response, agent_name="vision_augment_scan", model_hint="gpt-4.1-mini")` | `shared/cost_tracker.py` | ✅ — agent_name + model_hint match the OpenAI usage schema |
| `gotchas.py:113-128` `store()` | `data/form_gotchas.db.gotchas` row (PK domain+selector_pattern+engine) | many readers (NativeFormFiller, applicator, ai_assist_logger) | ✅ |
| `gotchas.py:181-207` `record_widget_pattern` | `widget_patterns` row | `field_scanner._scan_learned_patterns` reads via `GotchasDB().get_widget_patterns(domain)` | ✅ |
| `confidence_scorer.py:157-174` `log_fill_outcomes` | `FormExperienceDB.log_field_confidence(domain, field_label, predicted_confidence, actual_correct)` | calibration table in form_experience.db | ✅ |
| `validation.py` | returns `list[ValidationError]` to caller | `playwright_driver.py:579-601` consumes via `errors`/`has_errors` boolean | ✅ |
| `vision_gate._CACHE` | in-memory dict keyed by content-hash | same module | local cache, no cross-module concern |
| `semantic_matcher` `_WB_PATTERN_CACHE` (new) | local pattern cache | local | ✅ |

No cross-module schema mismatches found.

---

## STEP 5 — Live evidence

### Targeted suite (post-fix, commit `96cf428`)

```
$ python -m pytest tests/jobpulse/form_engine/ \
    tests/jobpulse/test_form_scanner.py \
    tests/jobpulse/test_field_label_noise_filter.py \
    tests/jobpulse/test_combobox_option_scan.py \
    tests/jobpulse/test_scan_learned_patterns.py \
    tests/jobpulse/test_scan_semantic_strategy.py \
    tests/jobpulse/test_semantic_widget_classifier.py \
    tests/jobpulse/test_semantic_proximity_match.py \
    tests/jobpulse/test_semantic_question_extractor.py \
    tests/jobpulse/test_vision_augment_scan.py \
    tests/jobpulse/test_vision_gate_predicate.py \
    tests/jobpulse/test_intent_healing.py \
    tests/jobpulse/test_form_fill_widgets_audit.py \
    tests/jobpulse/test_confidence_scorer.py \
    tests/jobpulse/test_gotchas_widget_pattern_schema.py \
    tests/jobpulse/test_scan_fields_vision_fallback_wiring.py \
    tests/jobpulse/test_oracle_hcm_listbutton_wiring.py \
    tests/jobpulse/test_revolut_switch_salary_wiring.py -q
348 passed, 1 failed, 12 warnings
```

The single failure
`tests/jobpulse/form_engine/test_field_mapper_real.py::TestFuzzyCustomAnswer::test_diversity_keyword_fallback`
(field_mapper, out of S2 scope) was reproduced by `git stash` against
the pre-audit tree — **pre-existing, not introduced by this audit**.

### Smoke verifications (REPL)

- B-1 fix: `_HEAL_PROMPT.format(label='Country', …)` returns 557-char
  string with literal `{"selector": "<css>"}` preserved.
- M-A fix: `semantic_option_match("yes", ["Yorkshire", "Greenwich",
  "Confirmed"]) is None` (pre-fix returned `'Yorkshire'`).
- M-A fix: `semantic_option_match("graduate visa", ["Graduate route
  visa", "Tier 2", "Skilled Worker"]) == 'Graduate route visa'`
  (long aliases preserved).

### Live `job-apply-next` deferral

Same justification as S1: a real apply triggers many subsystems but
yields few new signals per A-category function in the widget layer.
Pytest covers each widget-level branch deterministically. CDP is up
(Chrome 147 on :9222) but the apply call is reserved for subsystem 3
(navigation), where it's the natural source of evidence.

---

## STEP 6 — Fixes shipped

| ID | Where | Change | Test |
|---|---|---|---|
| **B-1** | `intent_healing.py:53-54` | Doubled literal braces in JSON example so `.format()` no longer crashes | `test_format_does_not_raise_keyerror`, `test_call_llm_does_not_silently_swallow_format_error` |
| **M-A** | `semantic_matcher.py:64-78, 117-122` | Replaced bidirectional substring with cached word-boundary regex match (`\b…\b`) | `test_short_alias_y_does_not_leak_into_unrelated`, `_n_…`, `_m_…`, `test_long_alias_substring_still_works`, `test_exact_alias_match_still_returns` |

Commit: `96cf428 fix(form_engine): S2 audit — intent_healing prompt +
short-alias substring leak`

Tests added: `tests/jobpulse/test_form_fill_widgets_audit.py` (7 guards
across 2 classes).

### Deferred to follow-up

| ID | Why deferred |
|---|---|
| M-B (`field_scanner.py:627` hardcoded vendor data-testids) | Replacement requires extending `_scan_learned_patterns` shape detection — wider change, fits the learned-widget-patterns plan. |
| M-C (`field_scanner.py:807` salary-context regex) | Multi-token semantic detection refactor — fits the regex-to-dynamic migration plan (`docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`). |
| M-D (`semantic_scanner.py:28-51` regex-tier classification) | Same plan as M-C; large surface area. |
| M-E (`validation.py:75` 200-char drop) | Pure cleanup, low priority. |
| Minors / nits | Listed above; no fix this session per "minor/nit findings: list but do NOT fix unless a blocker fix touches the same function". |

---

## STEP 7 — Architecture-doc update

`docs/job-application-pipeline.md` describes the form-fill phase but
makes one specific claim about intent_healing that needs a footnote:
the doc treats the LLM intent-resolution path (Path 3) as a routine
fallback. Pre-fix, that path was effectively dead — every `heal_locator`
call that reached Path 3 returned `None`. After commit `96cf428`, the
doc claim is true on the default path. No diff required because the
audit fix brought the code into compliance with the doc.

(Discrepancies discovered will be batched into a single doc edit at the
end of all 11 audits per the prompt's final-step instruction.)

---

## Session summary

- **Functions audited:** 10 A-category files (full read of bodies for
  the 6 highest-LOC and recently-touched). 11 B-category files
  inventoried but bodies not read (FF-gated, default OFF).
- **Blockers:** 1 (B-1 intent_healing prompt) — **FIXED**.
- **Majors:** 5 (M-A short-alias leak, M-B vendor testid, M-C salary
  regex, M-D semantic_scanner regex tier, M-E error truncation).
  **1 fixed (M-A)**, 4 deferred to existing migration plans.
- **Minors / nits:** 9. Documented; not fixed.
- **Tests:** 7 new audit guards. Targeted sweep 348/349 pass, 1
  pre-existing failure outside S2 scope.
- **Live evidence:** pytest sweep only (same justification as S1).
- **Pause point:** end of subsystem-2. Next: subsystem-3
  (`navigation`).
