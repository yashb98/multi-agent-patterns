# Pipeline Bugs — Consolidated Findings (12-Subsystem Audit)

Aggregated from `docs/audits/audit-{subsystem}.md` × 12 (`form_fill_dispatch`,
`form_fill_widgets`, `navigation`, `screening_pipeline`, `post_apply`,
`cognitive_engine`, `pre_screen`, `materials`, `scan_loop`, `optimization_engine`,
`memory_layer`, `ats_adapters`) and `audit-followup-worklist.md`.

> **Scope of this file**: every finding from the line-by-line audit pass that is
> **not yet fixed** OR is fixed but represents a class of bug to watch for. If a
> finding was shipped in its audit session, it is not duplicated here — see the
> per-subsystem audit doc and the `fix(...)` commit referenced there.
>
> **What this is not**: a TODO list. Some items are "won't fix" by design
> (e.g. `screening_defaults` was *deliberately* removed because the consumer
> path is `ScreeningPipeline`, not the strategies). Each row states the call
> for action explicitly.

## Legend

| Symbol | Category | What it means |
|---|---|---|
| 🔴 | **Open bug** | Real bug, can affect production correctness, not yet fixed |
| 🩹 | **Missing code** | Function/attribute called but not implemented; caller silently swallows the error |
| 💀 | **Dead code** | Defined but zero callers anywhere — orphaned in source |
| ⏸ | **Not triggered** | Defined and reachable in theory, but the apply path never calls it (B/C-tier) |
| 🔌 | **Wiring gap** | Producer/consumer mismatch: writes happen but no reader, or signals emitted with no consumer |
| 📝 | **Contract lie** | Docstring / `CLAUDE.md` / `docs/job-application-pipeline.md` claims behavior the code doesn't implement |

Severity sub-tags inside each category: **B**=blocker, **M**=major, **m**=minor, **n**=nit.

---

## Section 1 — 🔴 Open bugs (deferred majors)

These are real bugs that didn't get fixed in their audit session — usually
because they need a reproducer, touch a different subsystem, or require a
broader redesign.

### S1 — `form_fill_dispatch` (`native_form_filler.py`)

| ID | Location | Description |
|---|---|---|
| 🔴 M-1.a | `native_form_filler.py:1024` | `select_option` branch returns `value_verified=True` without DOM readback. |
| 🔴 M-1.b | `native_form_filler.py:1538` | `list_button_radio` (Oracle HCM) reports `value_verified=True` from JS click return — no DOM readback. Ghost click goes unnoticed. |
| 🔴 M-3 | `native_form_filler.py:2346` | Hardcoded `"privacy"/"consent"/"agree"` keyword skip — Principle 8 violation; duplicates `consent_policy.checkbox_intent`. |
| 🔴 M-4 | `native_form_filler.py:161` | `_resolve_dropdown_from_profile`: hardcoded substring patterns for visa-option classification + bare `try/except: pass` at L197. |
| 🔴 M-5 | `native_form_filler.py:1389` | Inline regex DUPLICATES `_SELECT_PLACEHOLDER_RE` (L86) and `_is_select_placeholder` (L92). |
| 🔴 M-6 | `native_form_filler.py:3119` | Workday fallback `_click_navigation`: `if dry_run: return "dry_run_stop"` fires on Next clicks (not just Submit). Multi-page Workday dry-run terminates at page 1. |

### S2 — `form_fill_widgets` (`form_engine/*`)

| ID | Location | Description |
|---|---|---|
| 🔴 M-B | `form_engine/field_scanner.py:627` | JS scan hardcodes `[data-testid="dropdown-basic"]` and `[data-testid="agree-data-privacy-dropdown"]` (Revolut-specific). Principle 8. |
| 🔴 M-C | `form_engine/field_scanner.py:807` | JS regex `salaryRx = /salary\|compensation\|gbp\|usd\|gross\|annual\|per year\|per annum/i` — Principle 8, English-only. |
| 🔴 M-D | `form_engine/semantic_scanner.py:28` | `QUESTION_STARTERS`, `NON_QUESTION_PHRASES`, `FIELD_LABEL_HEURISTIC` regex are the **primary** classification path for the "semantic" scan. Principle 8. |
| 🔴 M-E | `form_engine/validation.py:75` | Error-message text >200 chars silently dropped; truncate-with-ellipsis would preserve signal. |

### S3 — `navigation`

| ID | Location | Description |
|---|---|---|
| 🔴 M-A | `application_orchestrator_pkg/_navigator.py:1759` | `jobspy.scrape_jobs(...)` blocks the event loop in `_scrape_direct_url` (called from async `_try_platform_bypass`). Same shape as B-2 fix; needs `asyncio.to_thread`. |
| 🔴 M-B | `_navigator.py:1785` | `verify_submission` is wired by `_bind_compat_aliases` but never called in apply path. Dead code masquerading as a separate post-submit signal. |
| 🔴 M-C | `_navigator.py:1648` | `_dismiss_site_prompt_if_present` matches against English-only literal tuple. Principle 8. |
| 🔴 M-E | `page_analysis/page_reasoner.py:495` | `_apply_field_count_guard` is omitted from the reflection path (`reason_with_failure`). LLM that returns `fill_and_advance` after failure but drops required fields slips through. |

### S4 — `screening_pipeline`

| ID | Location | Description |
|---|---|---|
| 🔴 B-5 | `screening_pattern_extractor.py:267` | `find_matching_pattern` searches Qdrant for similar vectors but **ignores `results`** and returns the highest-success-rate pattern across the entire intent. Two distinct questions in same intent always return same pattern — semantic search is decorative. |
| 🔴 B-6 | `screening_validator.py:213` | Substring word matching produces false positives: `"I do not need sponsorship"` triggers BOTH `answer_says_yes` AND `answer_says_no`. Validator can flip-flop on phrasing. |
| 🔴 B-8 | `screening_answers.py:117` | `COMMON_ANSWERS` 130-line regex dict — Tier 3 fallback hit when intent classifier returns <0.55 confidence. Principle 8. |

### S5 — `post_apply`

| ID | Location | Description |
|---|---|---|
| 🔴 M-5.1 | `process_logger.py:229` | Module-level `init_process_db()` + `cleanup_old_trails(30)` fire at every import (15+ jobpulse modules import this). Import-time side-effect violates Principle 1; `DELETE` runs on every cron tick / test-runner startup. |

### S6 — `cognitive_engine`

| ID | Location | Description |
|---|---|---|
| 🔴 M-D | `shared/cognitive/_engine.py:142` | Escalation cost-reporting drops original level's cost. `escalated_result.cost` carries only the escalated level's spend; per-call `ThinkResult.cost` introspection drifts (~$0.001 per escalated call). |
| 🔴 M-E | `shared/cognitive/_engine.py:185` | L0→L1 escalated successes never reach `flush()`. Early `return escalated_result` happens BEFORE the L1 batch-write block. Production data: ~158 templates per ~1 200 calls (13%) discarded — direct learning-rate hit. |

### S7 — `pre_screen`

(none — both M-A and M-B fixed inline)

### S8 — `materials`

| ID | Location | Description |
|---|---|---|
| 🔴 M-F | `github_matcher.py:89` | `synonyms = load_skill_synonyms()` called inside `score_repo`, called per-repo by `pick_top_projects`. ~22 repos × 36K-entry JSON × 3 callsites per repo. |
| 🔴 M-G | `ats_scorer.py:165` | `_keyword_in_text` falls through to O(N) iteration over the entire 36K synonyms dict per missed keyword per call. |
| 🔴 M-H | `portfolio_variants.py:23` | `_load_variants_from_db` has THREE bare `except: ... return None` (inner JSON parse fail, outer SQLite open fail, empty row). CV gets non-archetype bullets when DB locked / schema changed / JSON malformed. |
| 🔴 M-I | `cv_tailor.py:336` | `tailor_summary_and_tagline` validates only the **summary**, not the **tagline**. If the LLM drifts on tagline format (drops degree, wrong YOE, wrong role title), wrong header line ships on the CV. |

### S9 — `scan_loop`

| ID | Location | Description |
|---|---|---|
| 🔴 M-9.B | `jobpulse/job_scanners/indeed.py:43` | `scan_indeed` has no scan_learning wiring at all (no `can_scan_now`, `record_success`, or block-event recording). Highest-block-rate platform empirically. |
| 🔴 M-9.C | `jobpulse/job_scanners/reed.py:103` | `scan_reed` consults `engine.can_scan_now` but never emits success or block events. |
| 🔴 M-9.D | `jobpulse/job_scanners/__init__.py:123` | `handle_block` is shape-incompatible with httpx scanners — typed for `verification_detector.VerificationWall` but only test fakes pass it. |

### S10 — `optimization_engine`

| ID | Location | Description |
|---|---|---|
| 🔴 M-10.A | `shared/optimization/_engine.py:329, 346, 364, 371, 381, 397, 406` | 7 `logger.debug` swallows on memory `demote`/`promote`/`pin`/alert-callback in `investigate_domain` / AutoRuleGenerator. OPRAL violation. |
| 🔴 M-10.B | `shared/optimization/_aggregator.py:359, 379` | `_dedup_with_memory` and `_cross_domain_search` use bare `except Exception: pass` / `return []`. Memory failures silently swallowed. |
| 🔴 M-10.C | `shared/optimization/_policy.py:71` | `_load_budget_state` bare `except: pass` AND opaque monotonic↔wall-clock conversion. |

### S11 — `memory_layer`

| ID | Location | Description |
|---|---|---|
| 🔴 M-11.A | `shared/memory_layer/_manager.py:151` | `AutonomousLinker.link_with_neighbors` is wired into `MemoryManager.__init__` but no production callsite ever calls it. **Result: Neo4j has Memory nodes but zero edges in production** → 3 of 6 ForgettingEngine signals always return defaults → `compute_decay` is half-functional. |
| 🔴 M-11.B | `shared/memory_layer/_stores.py:217` | `SemanticMemory.learn` documents `max_facts=500` but has **no eviction logic**. Production `semantic.json` has 1 041 entries despite the cap; will hit ~10 K / 5 MB within ~6 months. |
| 🔴 M-11.C | `shared/memory_layer/_manager.py:364, 368` | `get_procedural_entries` / `get_episodic_entries` read JSON-only (capped 100/200) while SQLite has 19 789 procedural / 203 episodic. Cognitive sees ~1/4 of procedural strategies. |
| 🔴 M-11.D | `shared/memory_layer/_neo4j_store.py:46` | `Neo4j.verify()` fails when `NEO4J_PASSWORD` unset — graph signals dormant in any env without docker-compose secrets. Affects forgetting + linker + signals chain. |
| 🔴 M-11.E | `shared/memory_layer/_sync.py:88` | `reconcile()` is O(N) embedding + O(N) Qdrant `has_point` per missing entry. With 27 786 entries: ~$0.50 per full reconcile, ~23 min latency. Called on every daemon start. |

### S12 — `ats_adapters`

| ID | Location | Description |
|---|---|---|
| 🔴 W-12.1 | `smartrecruiters.py:43` → `native_form_filler.py:3322` | `custom_answers["_cv_pre_uploaded"]` is **write-only** — SmartRecruiters auto-uploads CV in `pre_fill`, but `form_engine/file_uploader.py` never reads the flag. **Risk: double-CV-upload** (violates user-memory rule "Single resume upload"). |

---

## Section 2 — 🩹 Missing code (called but absent / silently no-op)

These are functions/attributes referenced from production code that either
weren't implemented or stopped being implemented, with a `try/except` masking
the AttributeError.

| ID | Location | Description | Status |
|---|---|---|---|
| 🩹 S11 B-1 | `shared/memory_layer/_manager.py:552` → `ForgettingEngine.sweep` | `MemoryManager.run_forgetting_sweep` called `self._forgetting.sweep(...)` for ~2 months; `sweep` was never defined. AttributeError swallowed by the wrapper's `try/except → logger.warning`. Hourly daemon tick was a silent no-op the entire time. | **FIXED** in `e9b2919` (S11 audit) — sweep now exists. **Documented here as a pattern to grep for** elsewhere. |
| 🩹 S11 M-11.A | `shared/memory_layer/_linker.py` | `AutonomousLinker.link_with_neighbors` is implemented but never invoked from production. Effect is identical to "missing code" — 3 of 6 forgetting signals always return defaults. | OPEN |
| 🩹 S5 M-5.2 | `agent_rules.py:346` | `get_escalation_fields` returns fields where `auto_generate_from_correction` set `action='escalate'` after 3+ corrections. Form filler / screening pipeline never short-circuit on it. The `escalate` action value is written but never consumed. | OPEN |
| 🩹 S5 M-5.3 | `trajectory_store.py:555-787` | `record_heuristic_outcome`, `invalidate_stale_heuristics`, `load_heuristics_for_application` — implemented, called once each by `strategy_reflector`, but `times_applied/times_succeeded` stay at 0 forever and the GRPO replay loop never runs. | OPEN |
| 🩹 S5 M-5.5 | `form_experience_db.py:377, 444, 902` | "PRAXIS-aware" cross-domain `content_hash` matching — `store(...)` never called in production (only tests); `content_hash` column always empty string. | OPEN |
| 🩹 S6 d-1 | `shared/cognitive/_strategy.py:165` | `StrategyComposer.record_template_outcome` is implemented but never called — `_reflexion._store_success` uses `MemoryManager.learn_procedure` instead. Templates never get outcome-tagged. | OPEN |
| 🩹 S2 m-1 | `form_engine/vision_gate.py:142` | Screenshot failure logs at `log.debug`. Vision was triggered because page looked sparse — the failure should be visible. | OPEN |
| 🩹 S5 d-5.1 | `correction_capture.py:160-220` | `get_correction_count`, `get_correction_rate`, `get_high_correction_fields` — test-only island. Suggested by file as a learning telemetry surface; nothing in production reads it. | OPEN |

**Pattern to grep for**: `except (AttributeError|Exception) .* logger\.(debug|warning).*\n.*return (None|\{\}|\[\])` — that shape can hide a method that was never written. S11 B-1 lived for months in exactly this form.

---

## Section 3 — 💀 Dead code (zero callers anywhere)

D-tier in the apply path AND in tests means truly orphan source. These are
candidates for deletion in a single post-audit cleanup PR.

### S1 — form_fill_dispatch

| ID | Location | Description |
|---|---|---|
| ✅ S4 | `native_form_filler.py:3240-3258` | `scan_current_values` — public method, **0 callers**. Accesses `f["locator"]` which the field scanner doesn't always populate. | Deleted. |

### S3 — navigation

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `_navigator.py:1785-1824` | `verify_submission` (also S3 M-B). | Deferred — `_bind_compat_aliases` wires it but tests still import. Cross-ref with S5 (wire-or-delete decisions). |
| ✅ S4 (partial) | `overlay_dismisser.py` | `_dismiss_cookie_banner / _generic_modal / _promo_popup` + `dismiss_all` — legacy `cookie_dismisser.dismiss` runs instead. | Deleted all four methods + module docstring rewritten; only `dismiss_linkedin_discard` remains. |
| ⏸ S4 deferred | `account_manager.py` | After 2026-05-04 auth rewrite, only `mark_verified` is reachable. `create_account / get_credentials / get_account_info / list_accounts` unused. | Deferred — partial deletion across 4 methods + tests still call them; risk of cascading test breakage outside Tier-1 scope. |
| ⏸ S4 deferred | `verification_detector.py` | Module unused in apply path; `playwright_driver.get_snapshot` does the equivalent inline. | Deferred — whole-module deletion overlaps with S5 wire-or-delete; some tests still import. |

### S4 — screening_pipeline

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `screening_detector.py` | Zero production callers. | Deferred — whole-module deletion + screening_pipeline.py imports it; cross-ref S5. |
| ⏸ S4 deferred | `screening_pattern_extractor.py` | `extract_patterns` and `find_matching_pattern` write to clusters DB, but reads C/D-tier. | Deferred — wire-or-delete decision (S5). |
| ⏸ S4 deferred | `screening_pipeline.record_outcome` | Not called in apply path. | Deferred — wrapper has multi-step orchestration logic (semantic cache update + intent example + answer caching); tests rely on it. Wire-or-delete decision (S5). |
| ✅ S4 | `query_memory_for_similar_answer` | Reads `MemoryManager` semantic engine but never invoked from production. | Deleted module-level helper + `_get_memory_manager` factory + dedicated test file + 2 test functions in `test_full_pipeline_real_data.py`. |

### S5 — post_apply

| ID | Location | Description |
|---|---|---|
| ✅ S4 | `correction_capture.py:160-220` | `get_correction_count`, `get_correction_rate`, `get_high_correction_fields` (test-only). | Deleted all 3 methods + corresponding test classes (`TestCorrectionRate`, `TestHighCorrectionFields`) + 1 test in `test_adaptation_chains_real.py`. |
| ✅ S4 | `agent_rules.py:346-349` | `get_escalation_fields` (also S5 M-5.2). | Deleted method + corresponding `TestGetEscalationFields` class + 1 test in `test_adaptation_chains_real.py`. |
| ⏸ S4 deferred | `cross_platform_field_transfer.py` (whole module) | No production importer; only tests + `weekly_optimize.py`. | Deferred — whole-module deletion + `weekly_optimize.py` imports it; wire-or-delete (S5). |
| ⏸ S4 deferred | `form_experience_db.py:377, 444, 902-958` | PRAXIS-aware subsystem. | Deferred — partial deletion within a live module; needs careful surgery (S5). |
| ⏸ S4 deferred | `platform_transfer.py:362-396` | `record_outcome` has no production caller despite signal-emit path now working post-S5 B-1. | Deferred — wire-or-delete (S5). |
| ⏸ S4 deferred | `trajectory_store.py:555-584, 714-787` | Heuristic-replay subsystem. | Deferred — partial deletion across two ranges in a live module (S5). |

### S6 — cognitive_engine

| ID | Location | Description |
|---|---|---|
| ✅ S4 | `shared/cognitive/_strategy.py:165-170` | `StrategyComposer.record_template_outcome` — no production caller. | Deleted method + 3 corresponding test functions in `test_strategy.py`. |
| ⏸ S4 deferred | `shared/cognitive/_engine.py:292` | `CognitiveEngine.report()` reachable only from tests + analytics. | Deferred — analytics is a soft consumer; keeping for now (S5 wire-or-delete). |

### S7 — pre_screen

| ID | Location | Description |
|---|---|---|
| ✅ S4 | `jd_analyzer.py:100-103` | `_SINGLE_SALARY_RE` defined, never referenced. | Deleted regex constant. |

### S9 — scan_loop

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `jobpulse/job_scanners/totaljobs.py` (whole module) | Removed from `PLATFORM_SCANNERS` 2026-05-04 (scripts/install_cron.py:47); only test imports it. | Deferred — `platform_bypass.py` still references the module name in a string lookup; whole-module deletion needs broader audit. |

### S10 — optimization_engine

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `shared/optimization/_policy.py:155 decide_async` | Only test calls it; production uses synchronous `decide`. Emits `cognitive_decision` action that has zero rows in production and zero handlers in `_execute_one`. | Deferred — partial deletion within live module (S5). |
| ✅ S4 | `shared/optimization/_gate_policy.py` (whole module, 242 LOC) | Only `tests/shared/optimization/test_gate_policy.py` imports it. Not in `__init__.py`. `_discover_domains:190` uses hardcoded English-only keyword classification (Principle 8). | Deleted whole module + dedicated test file + line in `shared/optimization/CLAUDE.md` module table. |

### S11 — memory_layer

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `data/agent_memory/memory.db` (0 bytes) | Orphan file, no production code references it. | Deferred — `data/*.db` deletion not reversible; safer to leave (zero impact). |
| ⏸ S4 deferred | `_linker.py` whole module (apply path) | See S11 M-11.A. | Deferred — wire-or-delete decision (S5/S6). M-11.A asks for wiring, not deletion. |
| ⏸ S4 deferred | `_router.py` `TieredRouter` | Constructed but never invoked from apply path. | Deferred — wire-or-delete (S5). |
| ✅ S4 | `_qdrant_store.py:213` `search_all_tiers` | No production caller. | Deleted method + corresponding `test_cross_tier_search` in `test_qdrant_store.py`. (Mock setup lines in `test_integration.py`, `test_manager.py`, `test_linker.py` left as harmless leftovers.) |
| ⏸ S4 deferred | `_qdrant_store.py:240` `count` | Test/analytics only. | Deferred — analytics is a soft consumer; tests rely on it (S5). |
| ✅ S4 | `_sqlite_store.py:262, 271, 280, 289, 307` | `query_by_tier`, `query_by_domain`, `query_by_lifecycle`, `query_by_decay_desc`, `query_tombstoned_recent` — `MemoryManager.query` doesn't use them. | Deleted all 5 methods + 4 corresponding test functions (`test_tier_views_filter_correctly`, `test_domain_filter`, `test_lifecycle_filter`, `test_decay_score_ordering`). |
| ⏸ S4 deferred | `_stores.py` `ShortTermMemory` whole class | Pattern-tier only. | Deferred — whole-class removal in shared module; broader audit needed (S5). |
| ⏸ S4 deferred | `_pattern.py` whole module (apply path) | Pattern-tier only. | Deferred — whole-module deletion (S5). |
| ✅ S4 | `_entries.py:108` `MemoryEntry.touch` | No production caller (`SQLiteStore.touch` is canonical). | Deleted method. (`Experience.touch` in `experiential_learning.py` is a different class.) |

### S12 — ats_adapters (the most extreme dead-method ratio)

| ID | Location | Description |
|---|---|---|
| ⏸ S4 deferred | `BasePlatformStrategy` ABC | 5 virtual methods with **zero callers anywhere**: `apply_button_selectors`, `wait_for_form_hydrated_ms`, `iframe_names`, `custom_field_scan`, `field_fill_overrides`. | Deferred — these are defined as ABC virtuals + overridden in subclasses (icims, strategy.py); ABC pruning is a multi-file change that hits 8+ adapter modules. Out of Tier-1 scope. |
| ✅ S4 | `BaseATSAdapter` | `resolve_selector` (line 49) and `get_wait_override` (line 61) — zero callers. Existed for the deleted per-platform adapter classes; missed in 2026-04 unification. | Deleted both methods. |
| ✅ S4 | `__init__.py:34 reset_adapter` | Zero callers anywhere. | Deleted function + removed from `__all__`. |
| ✅ S4 | `strategy.py:61 list_registered_strategies` | Zero callers anywhere. | Deleted function. |

**Headline: of 17 `BasePlatformStrategy` virtual methods, only 6 are reachable
in the default apply path** (`pre_fill`, `fill_combobox`, `form_container_hint`,
`expected_field_range`, `extra_label_mappings`, `normalize_label`).

---

## Section 4 — 🔌 Wiring gaps (producer ↔ consumer)

A signal is emitted but no consumer reads it; or a DB row is written but the
read path doesn't exist; or a producer/consumer disagree on schema.

### S2 — form_fill_widgets

| ID | Description |
|---|---|
| 🔌 w-1 | `field_scanner._emit_scan_signal → shared/optimization/_aggregator` hop verified producer-side only. Spot-check needed when next touching `shared/optimization/`. |

### S4 — screening_pipeline

| ID | Description |
|---|---|
| 🔌 w-1 | `data/screening_intent_prototypes.db` is **empty in dev** because `record_outcome` (only writer) is C-tier. Verify in production. |
| 🔌 w-2 | `data/screening_patterns.db` written via `pattern_extractor.observe`, but reads via `extract_patterns` are C-tier dead. **Write-only DB.** |

### S5 — post_apply

| ID | Description |
|---|---|
| 🔌 M-5.2 | `agent_rules.get_escalation_fields` writes `escalate` action; no consumer in form filler / screening pipeline. |
| 🔌 M-5.3 | Heuristic-replay `record_heuristic_outcome` etc. — `times_applied/times_succeeded` stay at 0 forever. GRPO replay never runs. |
| 🔌 M-5.4 | `cross_platform_field_transfer` — no production importer; Qdrant/embedding transfer dormant. |
| 🔌 M-5.5 | `form_experience_db.store` never called in production; `content_hash` always `''`. |

### S6 — cognitive_engine

| ID | Description |
|---|---|
| 🔌 W-1 | Cognitive auto-escalate (L0→L1 etc.) writes `cognitive_outcomes(escalated=1)` only; **no `OptimizationEngine.emit(signal_type='adaptation')` is fired**. SignalAggregator never sees escalations. |
| 🔌 W-2 | `_classifier.py:165 load_persisted_stats` reads `memory.semantic.facts.items()` directly instead of `MemoryManager.query`. If SemanticMemory shape changes, restore silently degrades. |

### S7 — pre_screen

| ID | Description |
|---|---|
| 🔌 W-1 | `scan_pipeline.process_single_url` skips `record_gap` (skill_gap_tracker), so single-URL applies don't contribute to skill-gap learning telemetry. Cron path does. |
| 🔌 W-3 | `gate4_quality.JobDB.record_gate_decision` writes `gate_decisions` table; no clear consumer in pre-screen subsystem. |

### S8 — materials

| ID | Description |
|---|---|
| 🔌 W-1 | `archetype_engine.detect_archetype` and `get_archetype_framing` both gated behind `JOBPULSE_ARCHETYPE_ENGINE` flag; default `false` in `pipeline_hooks.feature_enabled`; `.env` has `=true`. Tests / `python -m jobpulse.runner job-process-url` without sourcing `.env` silently get the static-template branch. |
| 🔌 W-2 | Two separate lazy-CL generators — `route_and_apply.cl_generator` (inline) vs `application_materials.build_lazy_cover_letter_generator` — different argument shapes. Inline path produces less-tailored CL than live-review path. **Drift risk.** |
| 🔌 W-3 | `pipeline_hooks.enhanced_generate_materials` applies `normalize_text_for_ats` to `bundle.cv_text` ONLY — but the PDF was already generated upstream. **Effective no-op.** |

### S10 — optimization_engine

| ID | Description |
|---|---|
| 🔌 W-10.1 | `transfer` signal type (added in S5 audit fix) has **no aggregator detector**. Producer fires (35 prod rows from `platform_transfer.record_outcome`). Could be intentional or oversight. |
| 🔌 W-10.2 | `_policy.decide_async` produces a `cognitive_decision` action; `_execute_one` has no handler. Emit-without-consume. |
| 🔌 W-10.3 | `cognitive_outcomes` rows stored with `agent_name=real_agent` but `forced_level_overrides` lookup uses `agent_name=domain`. The L0 fast-path at `_classifier.py:57` therefore still never fires. |
| 🔌 W-10.4 | `_aggregator._detect_persona_drift` healthy in code but production has only 7 `score_change` signals total — drift detector dormant. |

### S11 — memory_layer

| ID | Description |
|---|---|
| 🔌 W-11.1 | Linker not invoked → Neo4j has zero edges (M-11.A). |
| 🔌 W-11.2 | `memory_access_log` table is write-conditional + read-empty (m-11.2). Producer requires `trajectory_id != "no_trajectory"`; 0 rows in prod; no reader exists. |
| 🔌 W-11.3 | `pin_memory` only protects SQLite, not JSON cap. OptimizationEngine pins are half-applied. |
| 🔌 W-11.4 | `get_procedural_entries`/`get_episodic_entries` read JSON; `query` reads SQLite — **same store, divergent reads** (M-11.C). |
| 🔌 W-11.5 | `cognitive/_classifier.py:179` reaches into `self._memory.semantic.facts.items()` directly — bypasses `MemoryManager` (S6 W-2 carryover). |

### S12 — ats_adapters

| ID | Description |
|---|---|
| 🔌 W-12.1 | `_cv_pre_uploaded` write-only flag (see Section 1). |

---

## Section 5 — ⏸ Not triggered (B/C-tier in apply path)

Code that is implemented and reachable in theory, but the production apply path
never calls it — usually because a feature flag is not set, or the function is
on a code path replaced by another implementation.

### B-tier — gated behind a flag never set in production

| ID | Location | Flag |
|---|---|---|
| ⏸ S2 / S12 D-12.2 | `form_engine/engine.py` (whole module — `FormFillEngine.fill`, `_fill_page`, `_click_navigation`) | `UNIFIED_FORM_ENGINE=true`. Set only in `scripts/*.py` and integration tests. **Production daemon never sets it.** Cascade: `next_page_selectors`, `submit_selectors`, `post_page`, `known_widget_libraries` are only consulted via this engine. |
| ⏸ S8 W-1 | `archetype_engine.detect_archetype` / `get_archetype_framing` | `JOBPULSE_ARCHETYPE_ENGINE` (default false). |

### C-tier — implementation exists but the apply path uses a different one

| ID | Location | Description |
|---|---|---|
| ⏸ S3 d-2 | `OverlayDismisser._dismiss_cookie_banner / _generic_modal / _promo_popup` | Legacy `cookie_dismisser.dismiss` + `dismiss_cookie_banner_playwright` paths run instead. Module docstring claims consolidation that didn't happen. |
| ⏸ S3 d-4 | `verification_detector.py` | Apply path uses `playwright_driver.get_snapshot` inline. Tests + scraper-side scripts still consume this module. |
| ✅ S3 f4803b1 | `tests/conftest.py` | Conftest isolates `cognitive_budget.db` but NOT `data/optimization.db`. Production data shows 567/1197 cognitive_outcomes rows (47%) are from `agent_name='test_agent'` — tests are leaking via the `get_optimization_engine()` singleton inside `record_cognitive_outcome`. | Fix: added `OPTIMIZATION_DB` env var support in `_default_db_path`, autouse `isolate_optimization_db` fixture in `tests/conftest.py`, and `reset_optimization_engine()` helper. Wider-sweep production-DB delta confirmed at 0 (was 1235 → still 1235). 1235 historical leaked rows preserved (deletion not reversible). |
| ✅ S3 f4803b1 | (same root cause as S6 T-1) | 845/1571 production rows (54%) carry `agent_name='test_agent'`, plus cron fixtures. | Closed by the same fix as S6 T-1; both share the singleton root cause. |
| ⏸ S10 T-10.2 | `tests/shared/optimization/conftest.py:69, 72` | `MockMemoryManager` exposes BOTH `pin` and `pin_memory` — over-mocking that hid S10 M-A. |
| ⏸ S5 M-5.4 | `cross_platform_field_transfer.py` | Implemented, exhaustively tested, zero production importers. |
| ⏸ S6 d-2 | `CognitiveEngine.report()` | Test/analytics only. |

---

## Section 6 — 📝 Contract lies (saying X, doing Y)

The most insidious category — files that *claim* a behavior the code doesn't
implement. These mislead readers (and future Claude sessions) about what the
pipeline actually does.

### CLAUDE.md / project-level docs

| ID | Source | Claim | Reality |
|---|---|---|---|
| ✅ S1 2ab8ffe | `CLAUDE.md:95-99` | Lists "Cognitive Escalation" as one of three self-adaptation layers verified after every application | Rewritten to "2 self-adaptation layers" + paragraph explaining cognitive escalation runs in-line during fill, not post-apply. |
| ✅ S1 2ab8ffe (verified absent) | `docs/job-application-pipeline.md` | Claims CognitiveEngine escalation fires when navigation gets stuck | No such claim in current pipeline doc — the only `CognitiveEngine.think` references (lines 911, 1003) are for form-fill `_escalate_fill`, which IS wired. |
| ✅ S1 2ab8ffe (verified absent) | `docs/job-application-pipeline.md` | Treats `_navigator.verify_submission` as a separate post-submit verifier | `verify_submission` is not referenced in pipeline doc; only the SubmissionVerifier inside NativeFormFiller is documented. |
| ✅ S1 2ab8ffe (verified absent) | `docs/job-application-pipeline.md` | References OverlayDismisser as the single source of truth for overlay dismissal | Pipeline doc names `cookie_dismisser` as the primary banner-handler (line 212) and lists `overlay_dismisser.py` only as the LinkedIn save-overlay handler (line 1180) — accurate. |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` (post-apply diagram, line ~752) | Claims cognitive emits adaptation signals on escalation | Diagram now shows 2 layers; CognitiveEngine.flush() removed and explained as a write-back, not an adaptation layer. |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` ① pre-screen | Doesn't document scan-vs-single-URL gate-coverage asymmetry: cron path runs Gate 0 + Gate 4A, single-URL path skips both | Asymmetry now documented — `process_single_url` skips Gate 0 + Gate 4A; `record_gap` only fires on the cron path. |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` ② materials | Lazy CV path (`ensure_tailored_cv_for_job`) used by live-review and `job_autopilot.handle_apply_review` is undocumented | Both paths now documented — eager `generate_materials` + lazy `application_materials.ensure_tailored_cv_for_job`. |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` ② materials | Two-path split for cover letter (eager `cl_generator` closure vs lazy `build_lazy_cover_letter_generator`) is undocumented | Drift risk now documented — inline closure vs builder, with the differing argument shapes called out. |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` ② materials | Doesn't note that PDF gen runs before Gate 4B — `Needs Review` verdict still leaves the PDF on disk | Documented: PDF render at line ~681 of scan_pipeline runs before Gate 4B at line ~703. |
| ✅ S1 2ab8ffe | `shared/optimization/CLAUDE.md` | Documents `transfer` signal type | Note added: producer fires but no aggregator detector consumes the type yet (S10 W-10.1). |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` (signals listing) | (post-fix) shape mismatch between `cognitive_outcomes` (`agent_name=real_agent`) and `forced_level_overrides` (`agent_name=domain`) | Schema-shape note added: contributors must pass agent identity to cognitive_outcomes and domain to forced_level_overrides. |
| ✅ S1 2ab8ffe | `shared/memory_layer/CLAUDE.md:53` | "Forgetting sweep runs hourly — 6-signal decay" | Qualifier added inline: 3 of 6 signals (connectivity/impact/uniqueness) depend on AutonomousLinker.link_with_neighbors being wired (M-11.A still open). |
| ✅ S1 2ab8ffe | `jobpulse/CLAUDE.md` Memory Layer Integration | "All old API calls (`learn_fact`, `record_episode`, `learn_procedure`) now automatically feed the 3-engine memory stack" | Read-path asymmetry now documented (writes feed all 3 engines; reads via `get_procedural_entries`/`get_episodic_entries` still hit JSON-only legacy stores). |
| ✅ S1 2ab8ffe | `shared/CLAUDE.md:53` | "ALL memory access goes through MemoryManager" | Exception now documented: `cognitive/_classifier.py:load_persisted_stats` reaches into `semantic.facts.items()` directly (W-11.5). |
| ✅ S1 2ab8ffe | `docs/job-application-pipeline.md` (BasePlatformStrategy ABC) | Implies `BasePlatformStrategy` controls screening behavior | Listing now states 6 of 17 methods are reachable in default path; remainder are FormFillEngine-only (B-tier) or D-tier dead. |
| ✅ S1 2ab8ffe | `jobpulse/CLAUDE.md` Platform Strategies | Does not document the Native vs FormFillEngine path divergence — `submit_selectors`/`next_page_selectors`/`post_page`/`known_widget_libraries` are only consulted via FormFillEngine. | Now documented; `screening_defaults()` removed from the listing (it was deleted per PII policy). |
| ✅ S1 2ab8ffe | `jobpulse/CLAUDE.md` Adapter Registry | Doesn't document that `get_adapter()` takes no arguments (m-1). | New `Adapter Registry` section added; documents that `get_adapter()` is parameter-less. |

### Function-level contract lies (docstring vs. behavior)

| ID | Location | Description |
|---|---|---|
| ✅ S1 2ab8ffe (verified absent) | `screening_pipeline.py:73` | Docstring claims `Tier 3 Pattern match — screening_answers.lookup_canned_answer` but no such function exists. | Cleaned up by S4 audit commit `a747f16`; lint guard in `tests/lint/test_claude_md_truth.py` prevents reintroduction. |
| ✅ S1 2ab8ffe | `screening_semantic_cache.py:390` | `_align_to_options` annotated `-> CacheHit` but returns `None` when aligned answer not in `field_options`. Caller handles `None`, but type hint lies. | Annotation changed to `-> CacheHit | None` + docstring explains the cache-miss signal. |
| ✅ S1 2ab8ffe | `shared/cognitive/_strategy.py:20-29` | `STRATEGY_PAYLOAD_KEYS` claims a canonical payload-key set that no producer fully respects (`_engine.flush`, `_reflexion._store_success`, `_tot.explore` each emit slightly different `context` strings). | Comment block added marking the set as aspirational; consumers must tolerate missing keys. |
| ✅ S1 2ab8ffe | `process_logger.py:69-72, 98` | `step_input: str = None` — annotated `str` but accepts `None`. Should be `str | None`. | Both `log_step` and `step` annotations updated to `str \| None = None`. |
| ✅ S1 2ab8ffe | `cross_platform_field_transfer.py:34` | `Optional[Any]` referenced but `Any` not imported; latent defect (only saved by `from __future__ import annotations`). | `Any` added to the `from typing import` line. |
| 📝 S12 (pre-fix) | `__init__.py get_adapter(ats_platform)` | Accepted parameter, ignored it. **FIXED** in S12 audit — parameter dropped. |

---

## Cross-subsystem themes

Patterns that recur across multiple subsystems — worth a thematic followup
session each.

1. **Regex-for-classification violations (Principle 8)** — S1 (M-3, M-4), S2
   (M-B, M-C, M-D), S3 (M-C, m-3 i18n cookies, gmail_verify), S4 (B-6, B-8),
   S6 (m-1 stakes registry borderline). Migration plan:
   `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`.
   Tripwire: `tests/lint/test_no_classification_regex.py` (added in
   `pipeline-bugs-S2`) — clean-files allowlist; future regex-purge
   sessions extend it as files are cleaned.
2. **Verification claims without DOM readback** — S1 M-1.a/b are the canonical
   shape. Same pattern likely recurs in any "fill X then return success" code.
3. **Bare `except: pass` swallowing real errors** — S1 (M-4), S2 (n-4), S3
   (silently-handled cognitive escalation, fixed), S4 (B-4 silently-broken
   feedback loop, fixed), S10 (M-10.A 7 sites, M-10.B), S11 (m-11.9 experiential
   path), S12 (M-1..M-5 promoted to warning this session). Tripwire:
   `tests/lint/test_no_silent_attribute_error.py` (added in
   `pipeline-bugs-S2`) — clean-files allowlist for the audit-specified
   shape `except (AttributeError|Exception): logger.debug|warning; return
   None|{}|[]`. Repo-wide cleanup tracked separately in S17.
4. **Wired-but-unconsumed infrastructure** — S5 M-5.3 (heuristic replay),
   S5 M-5.4 (cross-platform Qdrant transfer), S5 M-5.5 (PRAXIS-aware FormExperienceDB),
   S6 d-1 (StrategyComposer.record_template_outcome), S10 d-10.2 (gate_policy module),
   S11 M-11.A (AutonomousLinker), S12 W-12.1 (`_cv_pre_uploaded`). **Decide
   wire-or-delete on each in a single follow-up session (S5 in the runner
   table).** Tripwire for the `_cv_pre_uploaded`-shape regression:
   `tests/lint/test_no_write_only_flag.py` (added in `pipeline-bugs-S2`) —
   guards `jobpulse/ats_adapters/` + `jobpulse/form_engine/`.
5. **Method called but never defined (silent AttributeError swallowed by `try/except`)** —
   S11 B-1 (`ForgettingEngine.sweep`) lived this way for ~2 months. The
   silent-attribute-error tripwire from theme #3 catches this same shape
   in audited-clean modules.
6. **CLAUDE.md / architecture-doc drift** — S3 (4 doc deltas), S6 (1), S7 (1),
   S8 (3), S10 (2), S11 (3), S12 (3). **Batch into one architecture-doc PR.**
   Advisor flagged this as overdue.
7. **Test-suite leakage into production DBs via singletons** — S6 T-1 / S10 T-10.1
   closed in `pipeline-bugs-S3`. `cognitive_outcomes` table was 47–54 %
   `agent_name='test_agent'` rows because `cognitive_budget.db` was isolated
   by env-override but `data/optimization.db` wasn't. Fix: added
   `OPTIMIZATION_DB` env var to `_default_db_path` (consistent with
   `LLM_USAGE_DB` / `COGNITIVE_BUDGET_DB`), autouse fixture
   `isolate_optimization_db` in `tests/conftest.py` that resets
   `_shared_engine = None` before/after every test, and
   `reset_optimization_engine()` helper. Wiring test
   (`tests/shared/optimization/test_db_isolation_wiring.py`) asserts the
   tmp DB receives the write AND the production DB row count is unchanged.
   1235 historical leaked rows preserved — they don't affect production
   reads since `forced_level_overrides` is keyed by `agent_name=domain`
   per S10 B-1, not by real agent name.

---

## Methodology

Each subsystem audit followed the same 7-step protocol (see
`docs/superpowers/plans/2026-05-06-line-by-line-pipeline-audit.md`):

1. Function-inventory extraction (rg + AST walk)
2. Wiring categorization (A/B/C/D-tier via `callers_of` + `grep_search`)
3. Line-by-line read of A/B-tier function bodies
4. Cross-module wiring audit (producer ↔ consumer schema check)
5. Live evidence (pytest, real-data run, log inspection)
6. Fix blockers/majors with regression tests
7. Update audit doc + worklist

Only fixes with passing regression tests landed in their audit session.
Deferred items in this file are categorized by why they were deferred (scope,
needs reproducer, cross-cutting, design discussion required).

The 12 audit docs and the worklist are in `docs/audits/`.

## How to use this file

- **Triaging a regression** — Section 1 (open bugs) first. If the symptom
  matches an open bug there, that audit doc has the trace + reproducer notes.
- **Refactoring a module** — Section 3 (dead code) first. Don't preserve API
  surface for code with zero callers.
- **Adding a new feature** — Section 4 (wiring gaps) and Section 6 (contract
  lies) so you don't repeat the pattern.
- **Reading CLAUDE.md or architecture docs** — Section 6: cross-reference any
  claim against this file before relying on it.
- **Cleanup PR planning** — Sections 3 + 5 + 6 are the natural scope for the
  post-12-audit cleanup PR.
