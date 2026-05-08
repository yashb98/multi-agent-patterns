# Subsystem 12 — `ats_adapters` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: `applicator.select_adapter(ats_platform)` →
  `ats_adapters.get_adapter()` → `PlaywrightAdapter.fill_and_submit`.
  Strategy dispatch via `ats_adapters.strategy.get_strategy(platform, url)` —
  consumed by `NativeFormFiller.fill` (default apply path) and
  `form_engine.engine.FormFillEngine` (B-tier, behind `UNIFIED_FORM_ENGINE=true`).
- Files (15 modules, 1 403 LOC total):
  - `__init__.py` (38) — registry + `get_adapter` factory
  - `_strategy_synthesis.py` (54) — FormExperienceDB → LearnedStrategy synthesizer
  - `base.py` (66) — `BaseATSAdapter` ABC + `FillSubmitResult` TypedDict
  - `discovery.py` (129) — URL/DOM platform detection
  - `generic.py` (13) — fallback strategy
  - `learned_strategy.py` (84) — runtime-synthesized strategy from FE DB
  - `strategy.py` (215) — `BasePlatformStrategy` ABC + `_STRATEGY_REGISTRY`
  - 8 platform strategies — `ashby.py` (79), `greenhouse.py` (95),
    `icims.py` (134), `indeed.py` (90), `lever.py` (85),
    `linkedin.py` (114), `smartrecruiters.py` (70), `workday.py` (137)
- Output of subsystem:
  - `BaseATSAdapter` instance (always `PlaywrightAdapter` post-2026-04 unification)
  - `BasePlatformStrategy` instance per (platform, url) consumed by NativeFormFiller
    and form_engine `field_scanner` / `field_mapper`.

---

## 1. Function inventory + wiring

### Category legend
- **A** — runtime: definitely called during `apply_job()` via `NativeFormFiller`
- **B** — runtime-conditional: only when `UNIFIED_FORM_ENGINE=true`
  (FormFillEngine path; flag not set in production)
- **C** — runtime-unreachable from apply path; tests / scripts / CLI only
- **D** — orphan: zero callers anywhere; truly dead

### 1.1 `__init__.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 23 | `get_adapter()` | A | Returns `PlaywrightAdapter()`. Pre-fix accepted an unused `ats_platform` parameter (m-1 fixed inline). 1 production caller: `applicator.select_adapter:75`. |
| 34 | `reset_adapter()` | D | "No-op for test compatibility" — zero callers in current tree. |

### 1.2 `_strategy_synthesis.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 17 | `_get_fe_db()` | A (lazy) | Patchable accessor; only `synthesize_strategy_for_domain` calls it. |
| 23 | `synthesize_strategy_for_domain(url)` | A | Called from `strategy.get_strategy:51` when platform name is unknown to registry. Production callers: 0 (apply path goes through registered strategies); but reachable via `LearnedStrategy` synthesis when a FE-known novel domain shows up. |

### 1.3 `base.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 22 | `BaseATSAdapter` (ABC) | A | Only concrete subclass: `PlaywrightAdapter`. |
| 26 | `detect(url)` | C | Abstract; only `PlaywrightAdapter.detect` overrides (returns False). Not on apply path. |
| 30 | `fill_and_submit(...)` | A | Implemented by `PlaywrightAdapter`; called by `applicator._call_fill_and_submit:115`. |
| 49 | `resolve_selector` | D | Zero callers — was used by deleted per-platform adapters. |
| 61 | `get_wait_override` | D | Zero callers anywhere. |

### 1.4 `discovery.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 17 | `_URL_PATTERNS` | A | Pre-fix: 8 platforms + missing `reed`. Post-fix M-A: includes `reed`. |
| 30 | `_DOM_PATTERNS` | A | 9 platforms (incl. `reed`) for DOM-aware detection. |
| 87 | `detect_platform(url, snapshot)` | A | Called by `applicator._infer_platform_from_url` (URL only) and `ApplicationOrchestrator.apply:167` (URL + DOM). Also `_aggregator.check_realtime:81`. |
| 132 | `detect_platform_from_url(url)` | A | URL-only wrapper. |

### 1.5 `generic.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 8 | `GenericStrategy` | A | Self-registers as `"generic"`. Final fallback when the registry has no match and synthesis returns None. |
| 12 | `detect()` | D | Returns `False`; registry is name-keyed, `detect()` never invoked. |

### 1.6 `learned_strategy.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 20 | `_get_fe_db()` | A | Lazy accessor for the 3 FE-DB methods below. |
| 26 | `_normalize_domain(value)` | A | Pure helper. |
| 50 | `__init__(domain, apply_count)` | A | Constructed by `synthesize_strategy_for_domain` when FE has ≥3 successful applies. |
| 55 | `detect(url)` | C | Inherited contract; LearnedStrategy is name-keyed via `f"learned:{domain}"`, `detect()` never invoked. **Pre-fix had unnecessary `try/except` around pure-urllib `_normalize_domain` — removed in M-2/3/4 fix.** |
| 64 | `form_container_hint()` | A | Reachable via `field_scanner.resolve_form_container:88`. **Pre-fix swallowed FE failures silently — M-2 fixed: `logger.warning`.** |
| 70 | `expected_field_range()` | A | Reachable via `field_scanner.validate_field_scan:416`. **M-3 fixed.** |
| 80 | `extra_label_mappings()` | A | Reachable via `field_mapper.seed_mapping:269`. **M-4 fixed.** |

### 1.7 `strategy.py`

| Line | Symbol | Cat | Notes |
|---|---|---|---|
| 22 | `register_strategy(cls)` | A (import-time) | Decorator, populates `_STRATEGY_REGISTRY` at import. |
| 28 | `get_strategy(platform, url)` | A | 18 callers; main consumer is `NativeFormFiller.fill:3317`. Order: registered → synthesis (`learned:<domain>`) → GenericStrategy. **M-5 fixed: synthesis exception now `logger.warning` (was `debug`).** |
| 61 | `list_registered_strategies()` | D | Zero callers. |
| 66+ | `BasePlatformStrategy.<methods>` | mixed | See § 1.8. |

### 1.8 `BasePlatformStrategy` virtual methods — production wiring

| Method | Production caller | Cat |
|---|---|---|
| `detect(url)` | none — registry uses class name, not detect | D |
| `apply_button_selectors()` | none in production | D |
| `next_page_selectors()` | only `form_engine/engine.py:436` (FormFillEngine) | B |
| `submit_selectors()` | only `form_engine/engine.py:435` (NativeFormFiller hardcodes `css_submit_selectors`, never calls strategy) | B |
| `wait_for_form_hydrated_ms()` | none anywhere | D |
| `form_container_hint()` | `form_engine/field_scanner.py:88` | A |
| `expected_field_range()` | `form_engine/field_scanner.py:416` | A |
| `known_widget_libraries()` | only `form_engine/engine.py:245` | B |
| `iframe_names()` | none — `native_form_filler.py:365` is `iframe_names = []` (local var, not a method call); production hardcodes `"icims_content_iframe"` and reads `_platform_strategy["quirks"]` | D |
| `custom_field_scan()` | none anywhere | D |
| `normalize_label()` | `form_engine/field_mapper.py:284` + `correction_capture.py` | A |
| `extra_label_mappings()` | `form_engine/field_mapper.py:269` | A |
| ~~`screening_defaults()`~~ | **REMOVED** in B-1 fix (PII policy + zero production callers) | — |
| `pre_fill()` | `native_form_filler.py:3321` | A |
| `post_page()` | only `form_engine/engine.py:304` (FormFillEngine) | B |
| `field_fill_overrides()` | none anywhere | D |
| `fill_combobox()` | `native_form_filler.py:1600` (only when widget is combobox; guarded by `getattr(strategy, "fill_combobox", None)`) | A |

**Headline finding: only 6 of 17 BasePlatformStrategy methods are reachable in
the default apply path** (`pre_fill`, `fill_combobox`, `form_container_hint`,
`expected_field_range`, `extra_label_mappings`, `normalize_label`).

`UNIFIED_FORM_ENGINE` is set only in `scripts/*.py` and integration tests —
never in production. So the FormFillEngine path that consumes
`apply_button_selectors`, `next_page_selectors`, `submit_selectors`,
`post_page`, `known_widget_libraries` is B-tier (test-only).

### 1.9 Per-platform strategy methods

For each strategy file, methods classified as A (called via NativeFormFiller),
B (called only via FormFillEngine), or noted unique overrides.

| File | A-tier methods | B/D-only overrides |
|---|---|---|
| `ashby.py` | (no extra A overrides — uses base) | `apply_button_selectors`, `next_page_selectors`, `submit_selectors`, `wait_for_form_hydrated_ms` (B/D) |
| `greenhouse.py` | `form_container_hint`, `expected_field_range`, `extra_label_mappings`, `normalize_label`, `pre_fill` | `apply_button_selectors`, `next_page_selectors`, `submit_selectors`, `wait_for_form_hydrated_ms` (B/D) |
| `icims.py` | `pre_fill`, `fill_combobox`, `extra_label_mappings`, `normalize_label`, `iframe_names` (declared but never called — D) | `apply_button_selectors`, `next_page_selectors`, `submit_selectors`, `wait_for_form_hydrated_ms`, `known_widget_libraries`, `custom_field_scan` (B/D) |
| `indeed.py` | `pre_fill`, `extra_label_mappings`, `normalize_label` | `apply_button_selectors`, `next_page_selectors`, `submit_selectors`, `wait_for_form_hydrated_ms` (B/D) |
| `lever.py` | `extra_label_mappings`, `normalize_label`, `pre_fill` (no-op) | same 4 (B/D) |
| `linkedin.py` | `form_container_hint`, `expected_field_range`, `extra_label_mappings`, `normalize_label`, `pre_fill` | same 4 + `known_widget_libraries` + `post_page` (no-op comment about Follow checkbox) |
| `smartrecruiters.py` | `pre_fill` (CV auto-upload — see W-12.1), `fill_combobox` | (uses base for the rest) |
| `workday.py` | `expected_field_range`, `normalize_label`, `pre_fill`, `fill_combobox` | same 4 (B/D) + `known_widget_libraries` |

---

## 2. Findings (severity-tagged)

> Severity: **B** = blocker · **M** = major · **m** = minor · **n** = nit · **D** = dead.

### Blockers (fixed this session)

| ID | Location | Severity | Description | Fix |
|---|---|---|---|---|
| B-1 | `strategy.py:166-168` (base) + `ashby.py:67-70`, `greenhouse.py:80-84`, `icims.py:77-80`, `indeed.py:70-73`, `lever.py:73-76`, `linkedin.py:84-88`, `workday.py:81-85` | **blocker** | `screening_defaults()` returns hardcoded screening answers — PII policy violation per `.claude/rules/pii-policy.md` ("screening answers, visa/work-auth must come from databases at runtime"). Compounded: zero production callers — DEAD code that exfiltrates PII. | **REMOVED** the method from base + all 8 strategies. Updated `tests/jobpulse/ats_adapters/test_strategy.py` and `tests/jobpulse/test_native_form_filler.py` to assert no strategy class defines `screening_defaults`. Screening answers come from `ScreeningPipeline` at runtime. |

### Majors (fixed this session)

| ID | Location | Severity | Description | Fix |
|---|---|---|---|---|
| M-A | `discovery.py:17-26` | major | `_DOM_PATTERNS["reed"]` was registered without a `_URL_PATTERNS["reed"]` counterpart, so `applicator._infer_platform_from_url` (URL-only path used to populate `ats_platform` for telemetry/post_apply) returned "generic" for Reed jobs while `jd_analyzer.detect_ats_platform` returned "reed". Verified disagreement on `https://www.reed.co.uk/jobs/123`. | Added `"reed": ["reed.co.uk"]` to `_URL_PATTERNS`. Regression test in `test_audit_s12.py::test_reed_url_classified_via_url_only_path` + `::test_reed_detection_agrees_with_jd_analyzer`. |
| M-1 | `_strategy_synthesis.py:34-36` | major | `lookup()` failure swallowed at `logger.debug` — OPRAL violation. FE DB-layer faults (corruption, missing schema, lock) silently fall back to GenericStrategy without surfacing why. | Promoted to `logger.warning` with domain context. Test in `test_audit_s12.py::test_synthesize_warns_on_fe_lookup_failure`. |
| M-2 | `learned_strategy.py:64-68` | major | `form_container_hint()` `try/except: return None` — silent FE swallow. | Promoted to `logger.warning`. Parametrised test in `test_audit_s12.py::test_learned_strategy_warns_on_fe_failure`. |
| M-3 | `learned_strategy.py:70-78` | major | `expected_field_range()` `try/except: pass` — silent FE swallow. Plus restructured to compute `n` outside the try-block (cleaner). | Promoted to `logger.warning`. Same parametrised test. |
| M-4 | `learned_strategy.py:80-83` | major | `extra_label_mappings()` `try/except: return {}` — silent FE swallow. | Promoted to `logger.warning`. Same parametrised test. |
| M-5 | `strategy.py:54-55` | major | `get_strategy` synthesis-failure logged at `debug` — masks synthesis-import / synthesis-call failures. | Promoted to `logger.warning`. Test in `test_audit_s12.py::test_get_strategy_warns_when_synthesis_raises`. |

### Minors (fixed this session)

| ID | Location | Severity | Description | Fix |
|---|---|---|---|---|
| m-1 | `__init__.py:23-31` + `applicator.py:73-75` | minor | `get_adapter(ats_platform)` accepted an unused `ats_platform` parameter. The docstring openly said "Return the PlaywrightAdapter for all platforms," so this was documented dead-weight rather than a contract lie — but per the audit prompt's "function lies about its contract" rule, dropping it is the cleaner state. | Dropped the parameter from `get_adapter()`. Updated `select_adapter` docstring to clarify `ats_platform` is retained for telemetry only and is not passed to the registry. Updated the two test files that called `get_adapter("...")` to call `get_adapter()`. Regression: `test_audit_s12.py::test_get_adapter_no_platform_parameter`. |

### Deferred majors (per advisor — separate cleanup PR)

| ID | Location | Severity | Description | Why deferred |
|---|---|---|---|---|
| 🔴 W-12.1 | `smartrecruiters.py:43` writes `{"cv_uploaded": True}` → `native_form_filler.py:3322-3323` sets `custom_answers["_cv_pre_uploaded"] = True` | major (wiring gap) | The `_cv_pre_uploaded` flag is **write-only**. Zero readers in production code (verified via repo-wide grep). SmartRecruiters auto-uploads CV in `pre_fill` for ATS resume parsing, but `form_engine/file_uploader.py` doesn't consult the flag → `NativeFormFiller` re-uploads the CV via its own file-input path. Matches user-memory rule "Single resume upload — never upload CV more than once per form." Live confirmation requires a SmartRecruiters job, which is outside this audit's scope. | Touches `native_form_filler` + `form_engine/file_uploader` (form_fill_dispatch — S1 territory). The audit prompt's "balloon scope" rule says don't fix issues that don't share a function with this audit's blockers. Logged here for the next form_fill_dispatch session. |

### Minors (deferred — log-promotion sweep)

OPRAL violations — silent debug-log on per-platform `pre_fill` / `fill_combobox`
Exception paths. Each individually small; collectively a sweep target.

| ID | Location | Description |
|---|---|---|
| 🟡 m-12.1 | `icims.py:94-95` | `pre_fill` iframe-check exception — `logger.debug`. |
| 🟡 m-12.2 | `icims.py:108-110` | `custom_field_scan` exception — `logger.debug`. (And `custom_field_scan` is itself D-tier.) |
| 🟡 m-12.3 | `icims.py:131-134` | `fill_combobox` exception — `logger.debug`. |
| 🟡 m-12.4 | `indeed.py:88-90` | `pre_fill` Indeed-Resume probe exception — `logger.debug`. |
| 🟡 m-12.5 | `linkedin.py:101-103` | `pre_fill` outer-exception (the inner warn-log already exists) — `logger.debug`. |
| 🟡 m-12.6 | `smartrecruiters.py:43-46` | `pre_fill` CV auto-upload exception — `logger.debug`. |
| 🟡 m-12.7 | `smartrecruiters.py:68-70` | `fill_combobox` exception — `logger.debug`. |
| 🟡 m-12.8 | `workday.py:101-103` | `pre_fill` Start-button-click exception — `logger.debug`. |
| 🟡 m-12.9 | `workday.py:135-137` | `fill_combobox` exception — `logger.debug`. |

### Nits (deferred)

| ID | Location | Description |
|---|---|---|
| ⚪ n-12.1 | `_strategy_synthesis.py:42-46` | `THRESHOLD_OBS:` info-level log fires on every successful synthesis call. Verbose for production logs; demote to `debug` (the synthesized-success log at L50-53 already provides info-level signal). |
| ⚪ n-12.2 | `linkedin.py:32-33` | `detect()` matches `"linkedin.com" in url.lower()` — overly broad (matches `linkedin.com/feed`, `linkedin.com/in/profile`). C-tier (registry uses name not detect), so functionally moot, but misleading. |
| ⚪ n-12.3 | `ashby.py:72-79`, `greenhouse.py:86-95`, `lever.py:78-85` | `pre_fill` overrides that just `return {}` — duplicate of base default. Remove. |

### Dead code (D-tier — separate cleanup PR per advisor)

Documented for the post-12-audit cleanup PR.

#### D-12.1 — `BasePlatformStrategy` virtual methods with zero callers anywhere

| Method | Defined in | Override sites | Callers |
|---|---|---|---|
| `apply_button_selectors` | `strategy.py:90` | base + 8 strategies | none in production |
| `wait_for_form_hydrated_ms` | `strategy.py:111` | base + 6 strategies | none anywhere |
| `iframe_names` | `strategy.py:140` | base + icims | none — see § 1.8 note |
| `custom_field_scan` | `strategy.py:144` | base + icims | none |
| `field_fill_overrides` | `strategy.py:200` | base only | none |
| `detect` (on `LearnedStrategy` and `GenericStrategy`) | n/a | n/a | registry is name-keyed |

#### D-12.2 — `BasePlatformStrategy` methods only consumed via `FormFillEngine` (B-tier; flag never set in production)

- `next_page_selectors` (engine.py:436)
- `submit_selectors` (engine.py:435 — NativeFormFiller has its own hardcoded `css_submit_selectors`)
- `post_page` (engine.py:304)
- `known_widget_libraries` (engine.py:245)

If `UNIFIED_FORM_ENGINE` stays disabled long-term, these can be deleted along
with `form_engine/engine.py`. If the engine is intended to ship, it needs a
flag-flip + an integration test that the production daemon honours the flag.

#### D-12.3 — `base.py` legacy adapter API

- `BaseATSAdapter.resolve_selector` (line 49) — zero callers
- `BaseATSAdapter.get_wait_override` (line 61) — zero callers
- These existed for the deleted per-platform adapter classes (LinkedInAdapter,
  GreenhouseAdapter etc.) and were not removed in the 2026-04 unification PR.

#### D-12.4 — `__init__.py:34 reset_adapter` — no callers anywhere

#### D-12.5 — `strategy.py:61 list_registered_strategies` — no callers anywhere

---

## 3. Cross-module wiring

### 3.1 `applicator.apply_job → adapter.fill_and_submit`

```
applicator.apply_job(url)
  └── _infer_platform_from_url(url)            # ats_adapters.discovery URL-only
        → ats_platform: str ('greenhouse' | 'reed' | ... | None)
  └── select_adapter(ats_platform)
        → get_adapter()                         # parameter dropped post-fix
        → PlaywrightAdapter()
  └── _call_fill_and_submit(adapter, url=url, ats_platform=ats_platform, ...)
        → adapter.fill_and_submit(...)
              ├── _detect_ats_platform(url)     # jd_analyzer.detect_ats_platform
              │     RECOMPUTES platform from URL — drops the passed-in ats_platform
              ├── orchestrator.apply(url, ...)
              │     ├── ApplicationOrchestrator.apply
              │     │     └── detect_platform(url, snapshot)   # ats_adapters.discovery DOM-aware
              │     └── NativeFormFiller.fill(platform, ...)
              │           └── get_strategy(platform, url)       # registry → synthesis → generic
              │                 └── strategy.pre_fill / .fill_combobox / etc.
```

**Schema agreements verified:**
- `applicator.select_adapter(ats_platform)` → `adapter.fill_and_submit` —
  `ats_platform` is **discarded** by `PlaywrightAdapter.fill_and_submit:41`
  which recomputes via `jd_analyzer.detect_ats_platform`. This is benign for
  current detection (URL+jd_analyzer agree on every platform tested except
  Reed pre-M-A; post-M-A they agree on Reed too) but **makes the adapter
  parameter cosmetic from the adapter's perspective**. Documented in M-A
  fix; no further action.
- `strategy.pre_fill → custom_answers["_cv_pre_uploaded"]` — **write-only**
  in production (W-12.1). Deferred.

### 3.2 `field_scanner` / `field_mapper` → strategy methods

Verified consumers + producers agree:
- `resolve_form_container(page, strategy, fe_db)` →
  `strategy.form_container_hint() -> str | None` ✅
- `validate_field_scan(fields, strategy)` →
  `strategy.expected_field_range() -> tuple[int, int]` ✅
- `seed_mapping(strategy, ...)` →
  `strategy.extra_label_mappings() -> dict[str, str]` ✅
- `seed_mapping(strategy, label)` →
  `strategy.normalize_label(label) -> str` ✅

No schema mismatches found.

### 3.3 No signal/DB writes from this subsystem

`ats_adapters/` is pure routing/strategy code. No `OptimizationEngine.emit`,
`MemoryManager.learn_*`, or DB writes originate here. Producer/consumer
schema audits per-method are therefore N/A for this subsystem — covered by
the consumer subsystems (form_fill_dispatch, post_apply, optimization_engine).

---

## 4. Live evidence

### 4.1 Detection disagreement (M-A)

```
$ python -c "from jobpulse.jd_analyzer import detect_ats_platform as A
            from jobpulse.ats_adapters.discovery import detect_platform_from_url as B
            for u in ['https://reed.co.uk/jobs/123', ...]:
                print(u, A(u), B(u))"

https://www.reed.co.uk/jobs/some-role/12345 jd='reed'      disc='generic'   ← pre-fix
https://www.reed.co.uk/jobs/some-role/12345 jd='reed'      disc='reed'      ← post-fix
```

All other platforms (greenhouse, lever, workday, ashby, icims, linkedin,
indeed, smartrecruiters) agree on URL-only detection both pre- and post-fix.

### 4.2 Test sweep

Run after fixes:

```
$ python -m pytest tests/jobpulse/ats_adapters/ tests/test_adapter_screening_wiring.py \
    tests/jobpulse/test_smartrecruiters_adapter.py tests/test_applicator.py \
    tests/jobpulse/test_platform_bypass.py tests/jobpulse/test_native_form_filler.py -q

86 passed, 12 warnings in 1.63s
```

Wider sweep (`tests/jobpulse/` + `tests/shared/` excluding live integration):
all green except one pre-existing failure unrelated to S12
(`test_field_mapper_real.py::TestFuzzyCustomAnswer::test_diversity_keyword_fallback` —
verified failing on baseline pre-S12 via `git stash`/`git stash pop`).

### 4.3 New regression tests in `tests/jobpulse/ats_adapters/test_audit_s12.py`

| Test | Covers |
|---|---|
| `test_reed_url_classified_via_url_only_path` | M-A — discovery now matches `reed` |
| `test_reed_detection_agrees_with_jd_analyzer` | M-A — discovery and jd_analyzer agree on Reed |
| `test_synthesize_warns_on_fe_lookup_failure` | M-1 — `_strategy_synthesis` warns |
| `test_get_strategy_warns_when_synthesis_raises` | M-5 — `strategy.get_strategy` warns |
| `test_learned_strategy_warns_on_fe_failure[form_container_hint/expected_field_range/extra_label_mappings]` | M-2 / M-3 / M-4 |
| `test_get_adapter_no_platform_parameter` | m-1 — parameter dropped |

Plus the PII regression test in
`tests/jobpulse/test_native_form_filler.py::test_strategies_do_not_hardcode_screening_answers`
— asserts no `BasePlatformStrategy` subclass defines `screening_defaults`.

---

## 5. Fixes applied (commit hashes — see git log)

To be filled in after commit. Single commit covering B-1 + M-A + M-1..M-5 + m-1
plus regression tests.

---

## 6. Remaining work (deferred to post-12-audit cleanup)

See `docs/audits/audit-followup-worklist.md` § Subsystem 12 — the deferred
items are:

1. **W-12.1** — SmartRecruiters `_cv_pre_uploaded` write-only flag. Touches
   form_fill_dispatch; needs a SmartRecruiters live URL.
2. **m-12.1 to m-12.9** — log-promotion sweep on per-platform pre_fill /
   fill_combobox exception handlers.
3. **n-12.1 to n-12.3** — `THRESHOLD_OBS:` log demotion, broad LinkedIn
   `detect`, no-op pre_fill overrides.
4. **D-12.1 to D-12.5** — dead code deletion. Single PR after all audits land.

---

## 7. Documentation deltas (queued for the architecture-doc batch)

| ID | Location | Description |
|---|---|---|
| 📝 D-12.1 | `docs/job-application-pipeline.md` | "BasePlatformStrategy provides per-platform overrides for navigation, scanning, and screening" — remove the screening claim; only 6 of 17 methods are actually live. Add a note that platform strategies post-2026-04 are thin label/normalize/container/pre_fill helpers; navigation and screening go through dedicated subsystems. |
| 📝 D-12.2 | `jobpulse/CLAUDE.md` "Adaptive Form Pipeline" | Same — document that `screening_defaults` is intentionally absent (PII policy + S12 audit). |
| 📝 D-12.3 | `jobpulse/CLAUDE.md` "Platform Adapters" | "All platforms route through PlaywrightAdapter post-2026-04" is correct; add that `get_adapter()` takes no arguments (m-1). |
