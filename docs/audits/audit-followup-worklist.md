# Pipeline Audit — Follow-up Worklist

Aggregated list of issues found during the 11-subsystem audit that were
**not fixed in their audit session** (deferred majors, minors, nits, wiring
gaps, dead code). Use this as the action list once all 11 subsystems are
audited.

**Sort order within each subsystem:** majors → minors → nits → dead code → wiring/doc deltas.

**Status legend:**
- 🔴 deferred-major — real bug or rule violation, not yet fixed
- 🟡 minor — quality issue, fix when adjacent code is touched
- ⚪ nit — cosmetic, no functional impact
- 💀 dead — orphan / unreachable code
- 🔌 wiring — producer/consumer mismatch or missing consumer
- 📝 doc — claim in `CLAUDE.md` / `docs/job-application-pipeline.md` no longer matches reality

---

## Subsystem 1 — `form_fill_dispatch` (`jobpulse/native_form_filler.py`)

Audit doc: `docs/audits/audit-form_fill_dispatch.md`
Fixes commit: `1c36f16`

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-1.a | `native_form_filler.py:1024-1025` | `select_option` branch returns `value_verified=True` without DOM readback. Borderline because Playwright validates option existence. | Lower-impact than M-1.c/d/e; revisit when touching the dispatcher. |
| 🔴 M-1.b | `native_form_filler.py:1538-1547` | `list_button_radio` (Oracle HCM) reports `value_verified=True` from JS click return — no DOM readback. Ghost click goes unnoticed. | Needs Oracle HCM live access to test the readback strategy. |
| 🔴 M-3   | `native_form_filler.py:2346` | Hardcoded keyword skip `if any(kw in test_id.lower() for kw in ("privacy", "consent", "agree"))`. Principle 8 violation; duplicates `consent_policy.checkbox_intent`. | Behavior-change risk — current skip interacts with `check_consent` path; needs brainstorming. |
| 🔴 M-4   | `native_form_filler.py:161-199` | `_resolve_dropdown_from_profile`: hardcoded substring patterns for visa-option classification + bare `try / except: pass` at L197. | Wider redesign — replace with learned mapping or `consent_policy`-style intent layer. |
| 🔴 M-5   | `native_form_filler.py:1389-1392` | Inline regex DUPLICATES `_SELECT_PLACEHOLDER_RE` (L86) and `_is_select_placeholder` helper (L92). | Pure cleanup; safe, low priority. |
| 🔴 M-6   | `native_form_filler.py:3119` | Workday fallback `_click_navigation`: `if dry_run: return "dry_run_stop"` fires on Next clicks (not just Submit). Multi-page Workday dry-run terminates at page 1. | Needs Workday dry-run reproducer. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `native_form_filler.py:138-158` | `emit_form_fill_failures` failure path uses `log.debug`; should be `log.warning` (OPRAL learning loop relies on signals firing). |
| 🟡 m-2 | `native_form_filler.py:267-278` | `_classify_fill_failure` substring keyword routing on `result["error"]`. Borderline — internal-message classification. |
| 🟡 m-3 | `native_form_filler.py:767-768` | `_escalate_fill` silently swallows `page.evaluate` failure for visible_buttons — engine has no candidates → silent abort. |
| 🟡 m-4 | `native_form_filler.py:933-935` | `_escalate_fill` swallows `record_fix` failure; ai_assist learning misses the win. |
| 🟡 m-5 | `native_form_filler.py:1436` | `f'input[name="{name_attr}"]'` — f-string in CSS selector; quotes in `name_attr` would break it. |
| 🟡 m-6 | `native_form_filler.py:1789` | `await el.fill("")` clears the field before retyping; if subsequent type fails, original pre-filled value is lost. |
| 🟡 m-7 | `native_form_filler.py:2576` | `page.locator(f"#{button_id}")` raw CSS-id interpolation; CSS-special chars break selector. Use `[id="…"]`. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `native_form_filler.py:506`  | `_save_gotcha` bare `try/except` with debug log only. |
| ⚪ n-2 | `native_form_filler.py:971`  | `_fill_resolved_widget` `try/except: pass` on `_smart_scroll` without log. |
| ⚪ n-3 | `native_form_filler.py:1382` | `fill_technique = "direct_fill"` initialized but unread in many paths. |
| ⚪ n-4 | `native_form_filler.py:1429`, `1466-1468` | Hardcoded boolean truthy/falsy tuples; consolidate via `semantic_matcher.checkbox_intent`. |

### Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `native_form_filler.py:3193-3211` | `scan_current_values` — public method, **0 callers in repo**. Accesses `f["locator"]` which the field scanner doesn't always populate. Candidate for deletion. |

### Wiring gaps

None — all signal/DB pairs verified in audit STEP 4.

---

## Subsystem 2 — `form_fill_widgets` (`jobpulse/form_engine/*`)

Audit doc: `docs/audits/audit-form_fill_widgets.md`
Fixes commit: `96cf428`

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-B | `form_engine/field_scanner.py:627` | JS scan hardcodes vendor-specific selectors `[data-testid="dropdown-basic"]` and `[data-testid="agree-data-privacy-dropdown"]` (Revolut-specific). Principle 8 violation. | Replacement requires extending `_scan_learned_patterns` shape detection — wider change, fits the learned-widget-patterns plan. |
| 🔴 M-C | `form_engine/field_scanner.py:807` | JS regex `salaryRx = /salary\|compensation\|gbp\|usd\|gross\|annual\|per year\|per annum/i` for salary-context detection. Principle 8 violation; English-only. | Multi-token semantic detection refactor — fits regex-to-dynamic migration plan. |
| 🔴 M-D | `form_engine/semantic_scanner.py:28-51` | Three large regex patterns (`QUESTION_STARTERS`, `NON_QUESTION_PHRASES`, `FIELD_LABEL_HEURISTIC`) are the **primary** classification path for the "semantic" scan strategy. Principle 8 violation — large surface area. | Subsumed by `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`. |
| 🔴 M-E | `form_engine/validation.py:75` | Error-message text `> 200` chars silently dropped. Some Workday/Greenhouse multi-paragraph "please confirm…" toasts exceed 200 chars; truncating to 200 with ellipsis would preserve the signal. | Pure cleanup, low priority. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `form_engine/vision_gate.py:142` | Screenshot failure logs at `log.debug`; should be `log.warning` (vision was triggered because page looked sparse — failure is meaningful). |
| 🟡 m-2 | `form_engine/field_scanner.py:1232` | `_run_strategy` strategy failures use `log.debug`. Promote to `log.warning` (each strategy fail loses a signal source). |
| 🟡 m-3 | `form_engine/field_scanner.py:1289-1303` | `_emit_scan_signal` bare `except: pass` — should `log.debug` the exc. |
| 🟡 m-4 | `form_engine/validation.py:63-66` | `[role='alert']` selector reused as constant key for every error — caller can't dedupe by field. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `form_engine/field_scanner.py:74,99` | Container deletion fall-through emits `log.info` instead of `log.warning` for "stale selector deleted". |
| ⚪ n-2 | `form_engine/confidence_scorer.py:106` | When no majority, fallback returns first candidate (lowest temp). Borderline. |
| ⚪ n-3 | `form_engine/intent_healing.py:178-181` | `logger.debug("...all resolution paths failed...")` should be `info` (heal_locator was called with stale selector and now everything failed). |
| ⚪ n-4 | `form_engine/semantic_matcher.py:215-216` | Bare `except Exception: pass` in `checkbox_intent` after embedding tier — silent swallow. |
| ⚪ n-5 | `form_engine/gotchas.py:44-47` | Migration `ALTER TABLE` wrapped in bare `except sqlite3.OperationalError: pass` — fine (idempotent). |
| ⚪ n-6 | `form_engine/field_scanner.py:716-720, 845-847` | JS class-state regex and min/max preceding-word regex — borderline structural detection, acceptable as fallback heuristic. |

### Wiring gaps

| ID | Description |
|---|---|
| 🔌 w-1 | Producer→consumer agreement was checked from the producer side only (verified emitted keys/payload), without opening every consumer body. The `field_scanner._emit_scan_signal → shared/optimization/_aggregator` hop should be spot-checked when anyone touches `shared/optimization/`. |

---

## Subsystem 3 — `navigation` (`application_orchestrator_pkg/*` + 11 navigation files)

Audit doc: `docs/audits/audit-navigation.md`
Fix commits: `87d407c` (B-1+B-2), `2317f13` (minor sweep)

> Note: most of S3's "minors" were already fixed in the post-blocker minor sweep (commit `2317f13`). Only the items listed below remain open.

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-A | `application_orchestrator_pkg/_navigator.py:1759` | `jobspy.scrape_jobs(...)` blocks the event loop in `_scrape_direct_url` (called from async `_try_platform_bypass`). Same shape as B-2 fix; needs `asyncio.to_thread`. | Indeed-only fallback inside aggregator-bypass path; rare runtime hits. |
| 🔴 M-B | `application_orchestrator_pkg/_navigator.py:1785-1824` | `verify_submission` is wired by `_bind_compat_aliases` but never called in apply path. Dead code masquerading as a separate post-submit signal. | Removal needs migration path; tests patch it. Documentation-only this session. |
| 🔴 M-C | `application_orchestrator_pkg/_navigator.py:1648-1654` | `_dismiss_site_prompt_if_present` matches against English-only literal tuple (`"are you interested"`, `"subscribe"`, …). Principle 8 violation. | Subsumed by `2026-05-04-regex-to-dynamic-migration.md`. |
| 🔴 M-E | `page_analysis/page_reasoner.py:495-503` | `_apply_field_count_guard` is omitted from the reflection path (`reason_with_failure`). LLM that returns `fill_and_advance` after failure but drops required fields slips through. | Same migration plan as M-C. |

### Minors / nits (open)

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `application_orchestrator_pkg/_navigator.py:1029-1043, 1357-1360, 1429-1437` | Retry-by-role pulls `(role="button"|"link")` in three places — de-dup into a helper. |

> All other S3 minors (constants, dispatch dict, action_executor consolidation, page_reasoner cache doc, iframe constant, account_manager + verification_detector + gmail_verify documentation flags) shipped in commit `2317f13`.

### Dead code (D-tagged in apply path)

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `application_orchestrator_pkg/_navigator.py:1785-1824` | `verify_submission` (same as M-B). |
| 💀 d-2 | `overlay_dismisser.py` | `_dismiss_cookie_banner / _generic_modal / _promo_popup` D-tagged; the legacy `cookie_dismisser.dismiss` + `dismiss_cookie_banner_playwright` paths run instead. Consolidation referenced in module docstring is incomplete. |
| 💀 d-3 | `account_manager.py` | Most of the API is D-tagged after the 2026-05-04 auth rewrite — only `mark_verified` is reachable in apply path. `create_account / get_credentials / get_account_info / list_accounts` unused. |
| 💀 d-4 | `verification_detector.py` | Module unused in apply path; `playwright_driver.get_snapshot` does the equivalent inline. Tests + scraper-side scripts still consume it. |

### Doc deltas (queued for final architecture-doc batch update)

| ID | Description |
|---|---|
| 📝 doc-1 | `CLAUDE.md:98` lists "Cognitive Escalation" as one of three self-adaptation layers verified after every application. With B-1 shipped, that claim is **false at the navigator layer** — the escalation code was deleted. The other two layers (CorrectionCapture / strategy_reflector) still hold. |
| 📝 doc-2 | `docs/job-application-pipeline.md` claims CognitiveEngine escalation fires when navigation gets stuck. Reality post-fix: not wired. Add note: "Cognitive escalation in the navigator was removed in S3 audit (2026-05-07) because no `ThinkResult`→`PageAction` translator exists." |
| 📝 doc-3 | `docs/job-application-pipeline.md` treats `_navigator.verify_submission` as a separate post-submit verifier. In practice the SubmissionVerifier inside NativeFormFiller is the only one that runs. |
| 📝 doc-4 | `docs/job-application-pipeline.md` references OverlayDismisser as single source of truth for overlay dismissal. Reality: legacy paths run instead; OverlayDismisser non-LinkedIn methods are D-tagged. |

---

## Subsystem 4 — `screening_pipeline` (`jobpulse/screening_*.py`)

Audit doc: `docs/audits/audit-screening_pipeline.md`
Fix commit: `a747f16`

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 B-5 | `screening_pattern_extractor.py:267-293` | `find_matching_pattern` searches Qdrant for similar vectors but **ignores `results`** and returns the highest-success-rate pattern across the entire intent. Two distinct questions in same intent always return same pattern — semantic search is decorative. | D-tier (no production caller); user impact today is zero. Mark for fix-when-touched. |
| 🔴 B-6 | `screening_validator.py:213-219` | Substring word matching produces false positives: `"I do not need sponsorship"` triggers BOTH `answer_says_yes` (via `need`) AND `answer_says_no` (via `not required` substring). Validator can flip-flop on phrasing. | Out of scope; flag for the regex-purge plan. |
| 🔴 B-8 | `screening_answers.py:117-276` | `COMMON_ANSWERS` 130-line regex dict is documented as "Tier 3 fallback" but tests force-skip V2 (`JOBPULSE_TEST_MODE=1`) and exercise this tier exclusively. Live runs hit it when intent classifier returns < 0.55 confidence. Principle 8 violation. | Tracked in `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `screening_pipeline.py:175` | `_finalise` step-numbering comments out of sync with empty-question early-exit path. Confirmed safe. |
| 🟡 m-2 | `screening_intent.py:340-352` | Embedding similarity loop creates `np.array(query_arr)` on every iteration. Cache once outside the per-intent loop. |
| 🟡 m-3 | `screening_intent.py:362` | Inline `__import__("datetime")` triple-call. Should be top-of-file import. |
| 🟡 m-4 | `screening_semantic_cache.py:457` | `_align_to_options` annotated `-> CacheHit` but returns `None` when aligned answer not in `field_options`. Caller handles `None`, but type hint lies. |
| 🟡 m-5 | `screening_decomposer.py:163` | `except Exception as exc: logger.debug(...)` should be `logger.warning` per `.claude/rules/error-handling.md`. |
| 🟡 m-6 | `screening_outcome_recorder.py:52` | Silent skip if `self._cache is None`; should log warning. |
| 🟡 m-7 | `screening_feedback_loop.py:250` | Accesses private `_aligner._normalise`. Cross-module coupling. |
| 🟡 m-8 | `screening_validator.py:309` | Imports private `_best_option_match` from `form_engine.field_resolver`. Cross-module coupling on private symbol. |
| 🟡 m-10 | `screening_option_aligner.py:46` | `_OPTION_FIELD_TYPES` includes `"textbox"` (free-text type); option alignment for textbox produces noisy logs. |
| 🟡 m-11 | `screening_pattern_extractor.py:228-232` | Regex for value normalisation in clustering. Allowed by §8 (text normalisation), but `{LOCATION}` substitutions UK-centric only. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ m-9 | `screening_pipeline.py:73` | Docstring claims `Tier 3 Pattern match — screening_answers.lookup_canned_answer` but no such function exists. |

### Dead code (D-tier in apply path)

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `screening_detector.py` | Zero production callers (per audit). |
| 💀 d-2 | `screening_pattern_extractor.py` | `extract_patterns` and `find_matching_pattern` are write-only (write to clusters DB, but reads C/D-tier). |
| 💀 d-3 | `screening_pipeline.record_outcome` | Not called in apply path. C-tier dead. |
| 💀 d-4 | `query_memory_for_similar_answer` | Reads `MemoryManager` semantic engine but never invoked from production. |

### Wiring gaps

| ID | Description |
|---|---|
| 🔌 w-1 | `data/screening_intent_prototypes.db` is **empty in dev** because `record_outcome` (only writer) is C-tier. Migration: `feedback_loop.py:137` also writes — verify in production. |
| 🔌 w-2 | `data/screening_patterns.db` written via `pattern_extractor.observe`, but reads via `extract_patterns` are C-tier dead. Write-only DB. |

---

## Subsystem 5 — `post_apply` (`jobpulse/post_apply_hook.py` + 16 sister files)

Audit doc: `docs/audits/audit-post_apply.md`
Blockers fixed in audit session (B-1 transfer signal, B-2 zero-delta
before/after, B-3 adaptation payload key — see audit doc Step 5).

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-5.1 | `process_logger.py:229-230` | Module-level `init_process_db()` + `cleanup_old_trails(30)` fire at every import (15+ jobpulse modules import this). Import-time side-effect violates Principle 1; the `DELETE` runs on every cron tick / test-runner startup. | Touches daemon startup path; needs broader regression run beyond post_apply scope. |
| 🔴 M-5.2 | `agent_rules.py:346-349` (consumers) | `get_escalation_fields()` returns fields where `auto_generate_from_correction` set `action='escalate'` after 3+ corrections, but no production caller consumes it. The "escalate" action value is written but the form filler / screening pipeline never short-circuit on it. | Pure feature-wiring gap; touches form_fill_dispatch / screening_pipeline. Revisit when next touching those. |
| 🔴 M-5.3 | `trajectory_store.py:555-584, 714-787` | `record_heuristic_outcome`, `invalidate_stale_heuristics`, `load_heuristics_for_application` all unreferenced in production. Heuristics get written by `strategy_reflector` but `times_applied/times_succeeded` stay at 0 forever and the GRPO replay loop never runs. | Heuristic-replay subsystem is write-only; defer until owner decides wire-vs-delete. |
| 🔴 M-5.4 | `cross_platform_field_transfer.py` (whole module) | No production importer; only tests + `weekly_optimize.py`. Cross-platform Qdrant/embedding transfer dormant. | Needs product-owner decision on whether to wire or remove. |
| 🔴 M-5.5 | `form_experience_db.py:377` (`store`), `:444` (`lookup_by_content_hash`), `:902-958` (negative exemplars + confidence calibration) | "PRAXIS-aware" cross-domain `content_hash` matching wired only in tests. `post_apply_hook.record(...)` is used; `store(...)` never called in production. `content_hash` column always `''`. | Defer; large-file scope creep. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-5.1 | `correction_capture.py:140`, `:155` | LLM/Optimization signal failure paths use `logger.debug`; should be `logger.warning` for non-retryable errors so future schema regressions surface. |
| 🟡 m-5.2 | `agent_rules.py:122-127` | Bare `except sqlite3.OperationalError: pass` for ALTER TABLE migration loses "column already exists" vs "table missing" distinction. |
| 🟡 m-5.3 | `cross_platform_field_transfer.py:34, 76-77` | `Optional[Any]` referenced but `Any` not imported; latent defect (only saved by `from __future__ import annotations`). |
| 🟡 m-5.4 | `post_apply_hook.py:79`, `:299`, `:138` | Session IDs use raw `company` string with spaces/unicode → brittle `LIKE` queries on `optimization.db`. |
| 🟡 m-5.5 | `drive_uploader.py:46` | `_file_name_prefix` falls back to literal `"Resume"` when both ProfileStore and `APPLICANT_*` config are missing — silent name drift. |
| 🟡 m-5.6 | `process_logger.py:69-72` | `step_input: str = None` etc. mis-typed as `str` instead of `str | None`. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-5.1 | `form_experience_db.py:28-134` (`_schema_sql`) vs `:149-298` (`_init_db`) | Same DDL written twice — drift risk if a future ALTER lands in only one. |
| ⚪ n-5.2 | `agent_performance.py:88-95` | `INSERT` SQL uses positional placeholders without column count assertion. |
| ⚪ n-5.3 | `trajectory_store.py:402` | Raw `success` int passed to `success: bool` dataclass field. |

### Dead code

| ID | Location |
|---|---|
| 💀 d-5.1 | `correction_capture.py:160-220` — `get_correction_count`, `get_correction_rate`, `get_high_correction_fields` (test-only island) |
| 💀 d-5.2 | `agent_rules.py:346-349` — `get_escalation_fields` (see M-5.2) |
| 💀 d-5.3 | `cross_platform_field_transfer.py` (whole module, see M-5.4) |
| 💀 d-5.4 | `form_experience_db.py:377, 444, 902-958` — PRAXIS-aware subsystem (see M-5.5) |
| 💀 d-5.5 | `platform_transfer.py:362-396` — `record_outcome` has no production caller despite the signal-emit path now working post-B-1 |
| 💀 d-5.6 | `trajectory_store.py:555-584, 714-787` — `record_heuristic_outcome`, `invalidate_stale_heuristics`, `load_heuristics_for_application` (see M-5.3) |

---

## Subsystem 6 — `cognitive_engine`

Audit doc: `docs/audits/audit-cognitive_engine.md`
Fixes commit: TBD (S6 — 1 blocker + 3 majors)

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-D | `shared/cognitive/_engine.py:142-148` | Escalation cost-reporting drops original level's cost. `escalated_result.cost` carries only the escalated level's spend; per-call `ThinkResult.cost` introspection drifts (~$0.001 per escalated call). Budget caps still work. | Cosmetic — needs threading the original level's cost into `escalated_result.cost` without double-charging the budget tracker. |
| 🔴 M-E | `shared/cognitive/_engine.py:185-197` | L0→L1 escalated successes never reach `flush()`. Early `return escalated_result` on L164 happens BEFORE the L1 batch-write block at L185-197. Production data: ~158 templates per ~1200 calls (13%) discarded — direct learning-rate hit. | Restructure of escalation early-return — needs careful test for both scoreless and scored escalation paths so the queue receives the right templates without double-writing on success. |

### Wiring gaps

| ID | Location | Description |
|---|---|---|
| 🔌 W-1 | `shared/cognitive/_engine.py:78-199` (`think`) | Cognitive auto-escalate (L0→L1, L1→L2, L2→L3) writes `cognitive_outcomes(escalated=1)` only; **no `OptimizationEngine.emit(signal_type='adaptation', ...)`** is fired. Per `shared/optimization/CLAUDE.md` "All learning loops MUST emit signals at key decision points." The SignalAggregator never sees escalations. |
| 🔌 W-2 | `_classifier.py:165-176 load_persisted_stats` | Reads `memory.semantic.facts.items()` directly instead of going through `MemoryManager.query`. If SemanticMemory's in-memory shape changes, restore silently degrades to "no stats restored" (and the M-A patch hides this — recovery within 10 samples). Worth porting to a proper `query` call. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `shared/cognitive/_classifier.py:8-21` | `STAKES_REGISTRY` hand-curated `{stakes: [domains]}` map. Borderline Principle-8 violation but explicit `stakes` arg overrides — qualifies as last-resort default. |
| 🟡 m-2 | `shared/cognitive/_engine.py:91-99` | Classifier exception → "fall back to L1" path logs `%s, e` only — stack traces dropped. Hard to root-cause classifier failures from a single log line. |
| 🟡 m-3 | `shared/cognitive/_engine.py:301-322` | `think_sync` builds a fresh `ThreadPoolExecutor(max_workers=1)` per call when an event loop is already running. ~1-3ms overhead per call; no thread-safety guard around `self._pending_writes` (currently safe via GIL + per-agent singleton, but ordering non-deterministic). |
| 🟡 m-4 | `shared/cognitive/_strategy.py:101-103` | `failures = [e for e in episodic if e.final_score < 5.0]` — relies on `EpisodicEntry.final_score` being non-Optional. Defensive `getattr(..., 0.0)` would harden. |
| 🟡 m-5 | `shared/cognitive/_strategy.py:20-24` | `STRATEGY_PAYLOAD_KEYS` claims a canonical payload-key set that no producer fully respects (`_engine.flush`, `_reflexion._store_success`, `_tot.explore` each emit slightly different `context` strings). Consumer doesn't parse them — currently harmless drift. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `shared/cognitive/_engine.py:215-217` | Fall-through `return await self._execute_l1(...)` is unreachable — `ThinkLevel` is a 4-value IntEnum fully covered by the if/elif chain. |
| ⚪ n-2 | `shared/cognitive/_classifier.py:181` | `re.match` parsing persisted facts. Borderline regex-for-classification but parses a structured format the classifier itself wrote — falls under "structural format validation" exemption. |

### Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-1 | `shared/cognitive/_strategy.py:165-170` | `StrategyComposer.record_template_outcome` is dead — no production caller mutates template dicts in-place; `_reflexion._store_success` uses `MemoryManager.learn_procedure` instead. |
| 💀 d-2 | `shared/cognitive/_engine.py:292` | `CognitiveEngine.report()` reachable only from tests + analytics; no apply-path consumer. Cheap to keep. |

### Test infrastructure gaps

| ID | Location | Description |
|---|---|---|
| 🔌 T-1 | `tests/shared/cognitive/conftest.py:96-100` | Conftest isolates `cognitive_budget.db` via env override but does NOT isolate `data/optimization.db`. Production data shows 567/1197 cognitive_outcomes rows (47%) are from `agent_name='test_agent'` — tests are leaking via the `get_optimization_engine()` singleton inside `record_cognitive_outcome`. Fix: add a fixture that monkeypatches `shared.optimization.get_optimization_engine` to a tmp-DB instance for the cognitive test scope. |

### Doc deltas

| ID | Location | Description |
|---|---|---|
| 📝 D-1 | `docs/job-application-pipeline.md` (any line claiming cognitive emits adaptation signals on escalation) | Update once W-1 lands — currently no `emit()` from cognitive. |
| 📝 D-2 | `shared/cognitive/CLAUDE.md` (Rules section) | Add note about the 3 producer sites for `learn_procedure` and the aspirational `STRATEGY_PAYLOAD_KEYS` contract, when m-5 lands. |

---

## Subsystem 7 — `pre_screen`

Audit doc: `docs/audits/audit-pre_screen.md`
Fixes: `7e10b10` (B-1), `45749a2` (B-2), `0de4527` (B-3), `4fa9fb0` (M-A + M-B)

### Deferred majors

(none — both M-A and M-B fixed inline this session)

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `skill_graph_store.py:215-217` | `total_projects` query result computed but never used in `get_projects_for_skills` — wasted COUNT statement on every `pre_screen_jd` call. |
| 🟡 m-2 | `skill_graph_store.py:432` | `[m.lower() for m in matched]` rebuilt inside list-comprehension over `top5` — O(n×m) and the `.lower()` is no-op since `matched` is pre-lowercased upstream. |
| 🟡 m-3 | `skill_graph_store.py:408` | `_check_kill_signals`: `if primary and self._normalize(primary) not in profile and not self._skill_match(primary, profile)` — the `not in profile` clause is redundant; `_skill_match` already does that check internally. |
| 🟡 m-4 | `gate4_quality.py:217-221` | `bullet_lines` heuristic flags any line >20 chars not all-caps as a bullet, producing false-positive "missing metric" warnings on paragraph-style summary lines. |
| 🟡 m-5 | `recruiter_screen.py:44-49` | `try / except: pass` on AgentRulesDB load — should `logger.debug(..., exc_info=True)` minimum so silent failures are observable. |
| 🟡 m-6 | `jd_analyzer.py:145` | `import re` inside `_canonicalize_url` shadows the module-level `import re` at line 23 — pure cleanup. |
| 🟡 m-7 | `skill_extractor.py:392-396` | `_FakeChoice` wrapper around `cognitive_llm_call` response is unnecessary; the returned string can be parsed directly. |
| 🟡 m-8 | `gate4_quality.py:130` | Generic-company detection fires on plausible names like "Cloud Solutions Ltd" (3 words all in `GENERIC_WORDS`). Soft flag only, but produces noise in logs. |
| 🟡 m-9 | `scan_pipeline.py:1078-1090` | `process_single_url` skips Gate 0 (title) and Gate 4A (blocklist, JD quality, spam). For ad-hoc URL submissions this may be intentional, but the asymmetry isn't documented. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `jd_analyzer.py:100-103` | `_SINGLE_SALARY_RE` defined, never referenced. Dead code. |
| ⚪ n-2 | `scrutiny_calibrator.py:184` | `import json` inside `get_insight` — unused. |
| ⚪ n-3 | `skill_extractor.py:32-33` | `SYNONYMS_PATH` and `_LEARNING_DB_PATH` use `Path(__file__).parent.parent` instead of the centralized `DATA_DIR` from `jobpulse.config`. |

### Wiring / doc deltas

| ID | Location | Description |
|---|---|---|
| 🔌 W-1 | `scan_pipeline.process_single_url` | Doesn't call `record_gap` (skill_gap_tracker), so single-URL applies don't contribute to skill-gap learning telemetry. Cron path does. |
| 📝 W-2 | `docs/job-application-pipeline.md` | Doesn't document scan-vs-single-URL gate-coverage asymmetry: cron path runs Gate 0 + Gate 4A, single-URL path skips both. |
| 🔌 W-3 | `gate4_quality.JobDB.record_gate_decision` | Writes to `gate_decisions` table; no consumer found inside the pre-screen subsystem. Possibly read by `job_analytics.get_funnel_stats`, but the link is loose. |

---

## Subsystem 8 — `materials`

Audit doc: `docs/audits/audit-materials.md`
Fix commits: `21e836d` (B-1 archetype lazy), `d1252a9` (B-2 + M-B/C/D/E log silent swallows)

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-F | `github_matcher.py:89` | `synonyms = load_skill_synonyms()` called inside `score_repo`, which is called per-repo by `pick_top_projects`. ~22 repos × 36K-entry JSON × 3 `_skill_match` callsites per repo → JSON parsed N×3 per scan-window. | Already noted as REMAINING in `seven-principles.md` §3 — not first surfaced by this audit; queue with the cross-subsystem N+1 cleanup batch. |
| 🔴 M-G | `ats_scorer.py:165-178` | `_keyword_in_text` falls through to O(N) iteration over the entire synonyms dict (~36K entries) per missed keyword per `score_ats` call. Reverse-lookup map should be precomputed once per process. | Same theme as M-F; bundle with the N+1 cleanup. |
| 🔴 M-H | `portfolio_variants.py:23-57` | `_load_variants_from_db` has THREE bare `except: ... return None` (inner JSON parse fail, outer SQLite open fail, `if not row: return None`). Multiple silent failure modes on the lookup hot path; CV gets non-archetype bullets when DB is locked / schema changed / JSON malformed. | Larger redesign — facade-via-MemoryManager or at minimum logger.warning at each fail site. Touches the regex-to-dynamic migration plan. |
| 🔴 M-I | `cv_tailor.py:336` | `tailor_summary_and_tagline` validates only the **summary**, not the **tagline**, despite the prompt saying "Tagline EXACTLY: {required_format}". If the LLM drifts on tagline format (drops degree, wrong YOE, wrong role title), the wrong header line ships on the CV with no signal. | Add a `validate_tagline` mirror; needs format-spec extracted from the prompt's `_required_tagline_format` to assert against. Pure additive. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `scan_pipeline.py:664-678` | CV PDF generation runs **before** Gate 4B (line 689-723). If Gate 4B's LLM scrutiny says `needs_review=True`, the PDF is already on disk and `notion_status` flips to "Needs Review". Wasteful disk + ~100ms but not a correctness bug — reorder when next touched. |
| 🟡 m-2 | `archetype_engine.py:122` | `re.compile(re.escape(keyword), re.IGNORECASE)` recompiled per keyword per archetype per `detect_archetype` call. Cache once. |
| 🟡 m-3 | `ats_scorer.py:188` | Same shape as m-2 — `re.compile` recompiled per keyword inside `_word_present`. |
| 🟡 m-4 | `generate_cv.py:601` | Renderer strips `^\d+\.\s*` from project title (`_re.sub`) then re-adds `f"{i+1}. "` at line 605. Two-place numbering — pick one canonical site. |
| 🟡 m-5 | `generate_cv.py:525-526` + `application_materials.py:39` | Company sanitisation does not strip domain suffixes (`.com`, `.co.uk`). Per user memory, expected output is `Yash_Bishnoi_ASOS.pdf`, not `Yash_Bishnoi_ASOS.com.pdf`. Should fix at extraction (jd_analyzer) layer rather than per-renderer. |
| 🟡 m-6 | `cv_tailor.py:160` + `generate_cover_letter.py:42` | `_METRIC_RE` regex pattern duplicated; move to a shared constant. |
| 🟡 m-7 | `archetype_engine.py:18` + `portfolio_variants.py:61` | `Path(__file__).parent.parent / "data" / ...` instead of centralised `DATA_DIR` from `jobpulse.config`. Same nit as S7 n-3. |
| 🟡 m-8 | `application_materials.py:110` | `db = db or JobDB()` opens fresh JobDB connection per-invocation when caller didn't pass one. Combined with `ensure_tailored_cv_for_job` being on the live-review pre-flight hot path, this is a connection-per-call pattern (Principle §3). |
| 🟡 m-9 | `generate_cv.py:155-156` | `_load_default_projects` returns `[]` on a fresh DB. `proj_list = projects or _load_default_projects()` then renders empty Projects section. Add an explicit non-empty assertion or hardcoded "see GitHub" fallback. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `cv_tailor.py:48` | `_required_tagline_format = build_required_tagline` back-compat alias — collapse callers. |
| ⚪ n-2 | `archetype_engine.py:90-99` | `_TITLE_ARCHETYPE_MAP` inconsistent grouping (`ai engineer` → `agentic` but `ml engineer` / `mlops` → `data_platform`). Soft naming nit. |
| ⚪ n-3 | `portfolio_variants.py:38-39` | `import sqlite3` and `import json` inside the function — both already imported at module top in many sister files; lazy imports buy nothing. |
| ⚪ n-4 | `cv_tailor.py:392, 462, 520` | When `_call_with_correction` returns `None` and `_parse(None)` returns `None`, the parse-fail path doesn't retry — only validator-fail does. Document or add a parse-fail retry. |

### Wiring / doc deltas

| ID | Location | Description |
|---|---|---|
| 🔌 W-1 | `archetype_engine.detect_archetype` and `get_archetype_framing` | Both gated behind `JOBPULSE_ARCHETYPE_ENGINE` flag. Default in `pipeline_hooks.feature_enabled` is `false`; `.env` has `=true`. Tests / `python -m jobpulse.runner job-process-url` without sourcing `.env` silently get the static-template branch. Document. |
| 🔌 W-2 | `route_and_apply.cl_generator` (`scan_pipeline.py:830-851`) vs `application_materials.build_lazy_cover_letter_generator` | Two separate lazy-CL generators co-exist with different argument shapes — inline closure skips `tailor_cover_letter_prose`, builder includes it. Inline path produces less-tailored CL than live-review path. Drift risk. |
| 🔌 W-3 | `pipeline_hooks.enhanced_generate_materials` (`pipeline_hooks.py:96-123`) | Wraps `generate_materials` and applies `normalize_text_for_ats` to `bundle.cv_text` ONLY. Since the PDF is already generated, the normalised `cv_text` lives only in memory; nothing downstream consumes it (`score_ats` ran upstream). Effectively a no-op. |
| 📝 D-1 | `docs/job-application-pipeline.md` | Lazy CV path (`ensure_tailored_cv_for_job`) used by live-review and `job_autopilot.handle_apply_review` is undocumented. |
| 📝 D-2 | `docs/job-application-pipeline.md` | Two-path split for cover letter generation (eager `cl_generator` closure vs lazy `build_lazy_cover_letter_generator`) is undocumented. |
| 📝 D-3 | `docs/job-application-pipeline.md` | PDF gen runs before Gate 4B — `Needs Review` verdict still leaves the PDF on disk. Note in the architecture doc. |

---

## Subsystem 9 — `scan_loop`

Audit doc: `docs/audits/audit-scan_loop.md`
Fixes commits: `bdb6892` (B-1 liveness filter), `e93320e` (M-A linkedin record_success guard)

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-9.B | `jobpulse/job_scanners/indeed.py:43` | `scan_indeed` has no scan_learning wiring at all (no `can_scan_now`, `record_success`, or block-event recording). Highest-block-rate platform empirically. | Shares fix shape with M-9.C / M-9.D — single follow-up session to design a shared block-event producer. |
| 🔴 M-9.C | `jobpulse/job_scanners/reed.py:103` | `scan_reed` consults `engine.can_scan_now` but never emits success or block events. | Same producer-coverage gap as M-9.B/D. |
| 🔴 M-9.D | `jobpulse/job_scanners/__init__.py:123` | `handle_block` is shape-incompatible with httpx scanners — typed for `verification_detector.VerificationWall` but only test fakes pass it. Either the type contract is wrong (should accept `wall_type` string for 429/403) or the function is dead. | Touches three scanners + `__init__.py` + a contract change; too large for the B-1 session. |

### Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-9.1 | `jobpulse/job_scanners/totaljobs.py` (whole module) | `scan_totaljobs` was removed from `PLATFORM_SCANNERS` 2026-05-04 (`scripts/install_cron.py:47`). Module remains, only `tests/jobpulse/test_job_scanner_platforms.py:20` imports it. |

---

## Subsystem 10 — `optimization_engine`

Audit doc: `docs/audits/audit-optimization_engine.md`
Fixes commits: `aa6fe74` (B-1 forced_level + M-A pin_memory), `619ee4c` (M-B import direction + M-C log promotions)

### Deferred majors

| ID | Location | Description | Why deferred |
|---|---|---|---|
| 🔴 M-10.A | `shared/optimization/_engine.py:329, 346, 364, 371, 381, 397, 406` | 7 `logger.debug` swallows on memory `demote`/`promote`/`pin`/alert-callback in `investigate_domain` / AutoRuleGenerator unavailable / rule-deploy fall-through / outer auto-rule deploy. Per OPRAL, every error must surface. | Cluster — 7 lines, no fix-shared function, low risk. Bundle when next touching `_execute_one`. |
| 🔴 M-10.B | `shared/optimization/_aggregator.py:359, 379` | `_dedup_with_memory` and `_cross_domain_search` use bare `except Exception: pass` / `return []`. Memory failures during pattern detection are swallowed silently — could mask MemoryManager regressions. | Routine OPRAL warning promotion. |
| 🔴 M-10.C | `shared/optimization/_policy.py:71-77` | `_load_budget_state` bare `except: pass` AND opaque monotonic↔wall-clock conversion (functionally correct via `_maybe_reset_window` cleanup, but semantically confusing). Refactor to `if (time.time() - saved_window) < 3600`. | Pure cleanup; verified safe via trace but readable form preferred. |

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-10.1 | `_tracker.py:362` | `correction_rate` calc has bare `except Exception: pass` — silent zero on signal_bus failure. |
| 🟡 m-10.2 | `_engine.py:525` | `health()` reads `self._aggregator._paused_loops` (private attr). Add a public `_aggregator.paused_loops` property. |
| 🟡 m-10.3 | `_engine.py:18-28` | `_get_auto_rule_generator` references `logger` before module-level `logger = get_logger(__name__)` at line 30. Functionally fine (lazy), but reads weird; move logger up. |
| 🟡 m-10.4 | `_engine.py:567-572` | `_NoOpTracker.get_domain_stats` uses `agent_name=domain` — same shape that caused B-1 in the live tracker. Harden when refactoring to a domain-only `get_domain_stats(domain)`. |

### Dead code

| ID | Location | Description |
|---|---|---|
| 💀 d-10.1 | `_policy.py:155` (`decide_async`) | Only test calls it; production uses synchronous `decide`. Emits `cognitive_decision` action that has zero rows in production and zero handlers in `_execute_one`. The whole async LLM-fallback policy branch is dead. |
| 💀 d-10.2 | `shared/optimization/_gate_policy.py` (whole module, 242 LOC) | Only `tests/shared/optimization/test_gate_policy.py` imports it. Not in `__init__.py`. `_discover_domains:190` uses hardcoded English-only keyword classification (Principle 8 violation, but unreachable). M-B fixed the `from jobpulse.config` import-direction violation regardless. Candidate for deletion in the post-audit cleanup PR. |

### Wiring gaps

| ID | Location | Description |
|---|---|---|
| 🔌 W-10.1 | `_aggregator.py` | The `transfer` signal type (added 2026-05-07 in S5 audit fix) has no aggregator detector. Producer fires (`platform_transfer.record_outcome`, 35 prod rows). Could be intentional (record-keeping only) or a wiring oversight. Document the design choice or add a detector. |
| 🔌 W-10.2 | `_policy.py:189-195` (`cognitive_decision` action) | Emit-without-consume: `decide_async` produces a `cognitive_decision` action but `_execute_one` has no handler. Either delete or wire. |
| 🔌 W-10.3 | `_tracker.py:315` (`get_domain_stats`) — soft | The forced_level override now surfaces post-fix, but the computed l0/l1/l2/l3 success rates derived from `cognitive_outcomes` still return 0.0 for the `(domain, domain)` lookup shape because real outcomes are stored with `agent_name=real_agent`. The L0 fast-path at `_classifier.py:57` therefore still never fires. Bigger fix: aggregate `cognitive_outcomes` by domain (drop agent_name from the WHERE clause) or thread real `agent_name` through `EscalationClassifier.classify`. Needs design discussion. |
| 🔌 W-10.4 | `_aggregator._detect_persona_drift:193` | Detector is healthy in code but production has only 7 `score_change` signals total — drift detector is effectively dormant. Either persona evolution emits too rarely, or producers aren't wired. Audit `persona_evolution.py` signal coverage in a follow-up. |

### Test-suite findings

| ID | Location | Description |
|---|---|---|
| 🔌 T-10.1 | `tests/shared/cognitive/conftest.py:96-100` | Conftest isolates `cognitive_budget.db` via env override but does NOT isolate `data/optimization.db`. Production `cognitive_outcomes` has 845/1571 rows (54%) with `agent_name='test_agent'` plus `agent_3=13`, `agent_4=13`, `cron_agent=13` — all test-suite leakage via the `get_optimization_engine()` singleton inside `record_cognitive_outcome`. Fix: monkeypatch `shared.optimization.get_optimization_engine` to a tmp-DB instance for the cognitive test scope. (Carryover from S6 audit T-1; not shipped because it requires checking against `test_wiring_e2e` and similar tests that intentionally exercise the singleton.) |
| 🔌 T-10.2 | `tests/shared/optimization/conftest.py:69, 72` | `MockMemoryManager` exposes BOTH `pin` and `pin_memory` — the over-mocking that hid the M-A bug. Cleanup risks breaking other tests; deferred. The S10 M-A regression test sidesteps this with a dedicated `_PinOnlyMemory` stub. |

### Doc deltas

| ID | Location | Description |
|---|---|---|
| 📝 D-10.1 | `shared/optimization/CLAUDE.md` | The `transfer` signal type is documented (line 67 — added in S5 audit fix) but the lack of an aggregator consumer should be noted alongside it. |
| 📝 D-10.2 | `docs/job-application-pipeline.md` | The schema-shape mismatch between `cognitive_outcomes` (`agent_name=real_agent`) and `forced_level_overrides` (`agent_name=domain`) was fixed at the read-path level (B-1) but should be documented so future contributors don't re-introduce it. |

---

## Subsystem 11 — `ats_adapters`

*pending audit*

---

## Cross-subsystem themes (so far)

Patterns recurring across S1-S4 worth a thematic followup:

1. **Regex-for-classification violations (Principle 8)** — recurring in S1 (M-3, M-4), S2 (M-B, M-C, M-D), S3 (M-C, m-3 i18n cookies, gmail_verify), S4 (B-6, B-8). The migration plan `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md` is the right home.
2. **Verification claims without readback** — concentrated in S1 (M-1 family) but the same pattern likely recurs in any "fill X then return success" code. Worth a sweep in S5 (`post_apply`) and S11 (`ats_adapters`).
3. **Bare `except: pass` swallowing real errors** — S1 (M-4), S2 (n-4), S3 (silently-handled cognitive escalation, fixed), S4 (B-4 silently-broken feedback loop, fixed). Add a lint rule.
4. **Dead/unused code in production path** — `scan_current_values` (S1), `verify_submission` (S3), `account_manager` API (S3), `verification_detector` (S3), `screening_detector` (S4), `find_matching_pattern` (S4). Candidate for a single deletion PR after all 11 audits.
5. **CLAUDE.md / architecture-doc drift** — S3 introduced 4 doc deltas; S4 already shipped doc updates inline. After all 11 audits, batch the remaining deltas into one architecture-doc PR (per the audit prompt's STEP 7 instruction).
