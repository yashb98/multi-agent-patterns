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

## Subsystem 5 — `post_apply`

*pending audit*

---

## Subsystem 6 — `cognitive_engine`

*pending audit*

---

## Subsystem 7 — `pre_screen`

*pending audit*

---

## Subsystem 8 — `materials`

*pending audit*

---

## Subsystem 9 — `scan_loop`

*pending audit*

---

## Subsystem 10 — `optimization_engine + memory_layer`

*pending audit*

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
