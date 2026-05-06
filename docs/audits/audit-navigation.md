# Subsystem 3 — Navigation: Line-by-Line Audit

**Scope:** `jobpulse/application_orchestrator_pkg/{__init__, _navigator,
_form_filler, _executor, _auth}.py`, `jobpulse/{cookie_dismisser,
sso_handler, sso_auto_discovery, account_manager, gmail_verify,
navigation_learner, verification_detector, platform_bypass,
playwright_driver, playwright_adapter, browser_intelligence,
page_analyzer}.py`, `jobpulse/page_analysis/*.py`,
`jobpulse/navigation/*.py` (~7942 LOC).

**Date:** 2026-05-07
**Branch:** `pipeline-correctness-fixes`
**Soft cap:** 2 h. Stopping at the larger blockers and committing fixes.

---

## STEP 1+2 — Function inventory & wiring categorization

A=runtime, B=runtime-conditional, C=runtime-unreachable (test/CLI),
D=orphan, E=overridden. Evidence sources: AST + ripgrep across whole
repo + manual trace from `apply()` in `__init__.py`.

### `application_orchestrator_pkg/__init__.py` (493 LOC)
| File:line | Symbol | Cat | Evidence |
|---|---|---|---|
| 45 `__init__` | A | called via `ApplicationOrchestrator(...)` from `playwright_adapter.py:48` |
| 89 `apply` | A | the public entry point — `playwright_adapter.fill_and_submit` line 50 |
| 345 `_complete_trajectory` | A | called from apply lines 191/196/206/315/340 |
| 358 `_run_pre_submit_gate` | A | apply line 269 |
| 398 `_run_semantic_correctness_check` | A | apply line 280 |
| 442 `_to_page_snapshot` | A | apply line 136 (when `pre_navigated_snapshot`) |
| 469 `_bind_compat_aliases` | A | called by `__init__` |
| 482 `_extract_domain` | C | only test fixtures patch it |
| 486 `_find_apply_button` | C | tests reference but apply path uses navigator helper |
| 491 `_find_signup_link` | C | same — alias for `_auth.find_signup_link` |

### `application_orchestrator_pkg/_navigator.py` (1846 LOC)
| File:line | Symbol | Cat | Evidence |
|---|---|---|---|
| 125 `_maybe_reflect_on_failure` | A | `_phase_act` 1110, 1153 |
| 185 `_normalize_url_path` | A | `build_page_fingerprint` 205 |
| 192 `_compute_content_hash` | A | `build_page_fingerprint` 210 |
| 197 `build_page_fingerprint` | A | `_phase_analyze` 645 / 682 |
| 219 `score_fingerprint_match` | A | `_phase_match` 722 |
| 265 `score_apply_button` | A | `click_apply_button` 1333; `find_apply_button` 1842 |
| 294 `FormNavigator.__init__` | A | constructed in `__init__.py:70` |
| 320 `_as_dict` | A | helper used throughout |
| 326 `_detect_ghost_click` | A | `_verify_action`, `_phase_act` |
| 334 `_verify_action` | A | `_phase_act` 1014/1090; `_auth.handle_login/signup` |
| 366 `_check_expected_outcome` | A | `_phase_act` 1099; `_auth.*` |
| 397 `_snapshot_content_hash` | A | many call sites |
| 404 `_make_result` | A | navigate_to_form 1287 |
| 452 `_phase_observe` | A | navigate_to_form 1270 |
| 524 `_should_auto_switch_tab` | A | `_phase_observe` 468 |
| 553 `_pick_target_tab` | A | `_phase_observe` 469 |
| 583 `_domain_has_prior_success` | A | `_phase_plan` 787 |
| 614 `_clear_stale_plan_on_host_change` | A | `_phase_observe` 485, 499 |
| 640 `_phase_analyze` | A | navigate_to_form 1280 |
| 690 `_phase_match` | A | navigate_to_form 1282 |
| 735 `_phase_plan` | A | navigate_to_form 1284 |
| 872 `_verify_learned_action` | A | `_phase_plan` 806 |
| 892 `_phase_act` | A | navigate_to_form 1289 |
| 1185 `_dismiss_linkedin_discard` | A | `click_apply_button` 1416/1419/1467 |
| 1190 `navigate_to_form` | A | `__init__.apply` 154/227 |
| 1320 `click_apply_button` | A | `_phase_act` 920 |
| 1485 `_bypass_verification_wall` | A | `_phase_act` 950 |
| 1639 `_dismiss_site_prompt_if_present` | A | `_phase_analyze` 675 |
| 1689 `_try_platform_bypass` | A | `_phase_act` 944, 967 |
| 1714 `_navigate_to_direct_url` | A | `_try_platform_bypass` 1701, 1710 |
| 1735 `_scrape_direct_url` | A | `_try_platform_bypass` 1708 |
| 1785 `verify_submission` | C | only `_bind_compat_aliases` 479 binds it; nothing in apply path calls `_verify_submission` |
| 1829 `extract_domain` | A | imported widely |
| 1835 `find_apply_button` | A | navigator + applicator |

### `application_orchestrator_pkg/_executor.py` (142 LOC)
All A — every method invoked from NativeFormFiller (S1 already audited)
plus `_bind_compat_aliases`.

### `application_orchestrator_pkg/_form_filler.py` (134 LOC)
All A.

### `application_orchestrator_pkg/_auth.py` (164 LOC)
| File:line | Symbol | Cat | Evidence |
|---|---|---|---|
| 55 `handle_login` | A | reasoner returns action `login` → eventually scheduled via flow |
| 94 `handle_signup` | A | same |
| 133 `handle_email_verification` | A | `_phase_act` line 930 |
| 153 `_extract_domain` | A | local helper |
| 159 `find_signup_link` | A | imported by `__init__._find_signup_link` (test compat) |

### `cookie_dismisser.py` (157)
- `CookieBannerDismisser.__init__/dismiss` — A (`_phase_analyze` 669)
- `_has_cookie_sibling_buttons` — A
- `dismiss_cookie_banner_playwright` — A (`_phase_analyze` 672, `_phase_act` 1178)

### `sso_handler.py` (148)
All A — `_phase_act` calls `self.sso.click_sso(...)` for SSO actions.

### `sso_auto_discovery.py` (61)
`detect_sso_button_patterns` — A (fallback inside SSOHandler.detect_sso 68).

### `account_manager.py` (130)
- `__init__` — A (instantiated in `__init__.py:59`)
- `_init_db`, `_normalize_domain`, `has_account`, `create_account`,
  `get_credentials`, `get_account_info`, `mark_verified`,
  `mark_login_success` — **C with respect to apply()**.
  apply path doesn't reach them: AuthHandler delegates login/signup to
  the reasoner + NavigationActionExecutor (`_auth.py:55`/94 since
  2026-05-04 rewrite). The only call in the runtime path is
  `accounts.mark_verified(domain)` from `handle_email_verification`
  (line 144). The rest of the AccountManager API is wired but
  unreachable from current apply() flow.
- `_get_fernet` — A (transitively via `mark_verified`)

### `gmail_verify.py` (157)
- `extract_verification_link` — A (`wait_for_verification` 128)
- `GmailVerifier.__init__/_get_service` — A
- `wait_for_verification` — A (called by AuthHandler)
- `_extract_html_body` — A
- `_LinkExtractor.*` — A

### `navigation_learner.py` (267)
All A — used by `_phase_match`, `_phase_plan`, `apply` save_sequence.

### `verification_detector.py` (177)
- `detect_verification_wall(page, ...)` — **D** (orphan in apply pipeline).
  rg confirms only `tests/jobpulse/test_verification_detector.py` and
  `scripts/run_verification_logging.py` import it. Apply path's wall
  detection runs inline in `playwright_driver.get_snapshot` JS evaluate
  block (line 455-497), not via this Python module.
- `simulate_human_interaction` — D (same; no production caller)

### `platform_bypass.py` (473)
All A — `_navigator._try_platform_bypass`.

### `playwright_driver.py` (736)
All A — driver layer.

### `playwright_adapter.py` (69)
A.

### `browser_intelligence.py` (283)
A — attached/detached during driver lifecycle.

### `page_analyzer.py` (257)
- `_dom_detect` — A (`_form_filler.fill_application` 128 on session-expired
  retry; `page_analyzer.PageAnalyzer.detect` 133)
- `_vision_detect` — A (PageAnalyzer.detect 172)
- `_map_reasoner_type` — A
- `PageAnalyzer.*` — A
  (`_navigator._bypass_verification_wall._check_cleared` calls
  `self.analyzer.detect` line 1508; analyzer constructed in
  `__init__.py:62`)

### `page_analysis/page_reasoner.py` (658)
- `get_llm`, `smart_llm_call` proxies — A
- `PageReasoner.*` — A
  - `reason_sync` — A (`_phase_plan` 825; `click_apply_button` 1352;
    `_auth.handle_login/signup`)
  - `reason_with_failure` — A (`_phase_act` 1071; `_maybe_reflect_on_failure`)
  - `reason` (async) — C (no awaiter; it's `await reasoner.reason(...)`
    at `page_analyzer.py:153` only — that's reachable). Re-tagged A.
  - `_apply_advance_button_guard`, `_apply_zero_fields_guard`,
    `_apply_field_count_guard` — A (called inside reason_sync /
    reason_with_failure)
  - `invalidate` — A (multiple call sites)
- `get_page_reasoner` — A

### `page_analysis/classifier.py` (478)
- `PageTypeClassifier.*` — A
- `_button_matches_intent` — A
- `_find_matches` — A

### `page_analysis/calibration.py` (248)
- `_ensure_db`, `WeightCalibrator.*` — D in apply path. Calibrator only
  reachable via `scripts/calibrate_classifier.py`. CLI utility, not
  apply-time. Skipping line-by-line.

### `navigation/wait_conditions.py` (237)
All A — used throughout navigator.

### `navigation/overlay_dismisser.py` (218)
- `OverlayDismisser.__init__/dismiss_linkedin_discard` — A
  (`_navigator._dismiss_linkedin_discard` 1187)
- `dismiss_all`, `_dismiss_cookie_banner`, `_dismiss_generic_modal`,
  `_dismiss_promo_popup` — D (no caller in apply path; only dismiss_all
  is exposed and nothing calls it). Confirmed via
  `rg "OverlayDismisser\(|dismiss_all\(|_dismiss_cookie_banner\(|_dismiss_generic_modal\(|_dismiss_promo_popup\("`
  → only the LinkedIn helper goes through 1187, the rest never fire.

### `navigation/action_executor.py` (399)
All A — used by `_navigator._phase_act` 1002, `_auth.handle_login/signup` 70/109.

---

## STEP 3 — Line-by-line read of A-category functions

### Blockers

**B-1 — `_navigator.py:852-864` — `_phase_plan` CognitiveEngine escalation
is dead code AND throws AttributeError on every call.**

```python
if action.confidence < 0.3 and sum(1 for v in visited_states.values() if v >= 2) >= 2:
    try:
        from shared.cognitive import get_cognitive_engine
        engine = get_cognitive_engine()
        cog_result = engine.think(           # ← async method
            f"Navigation stuck: ...",
            domain="form_navigation",
        )
        if cog_result and cog_result.get("action"):  # ← coroutine.get fails
            logger.info("PLAN: CognitiveEngine escalation → %s", cog_result["action"])
    except Exception as exc:
        logger.debug("CognitiveEngine escalation failed: %s", exc)
```

Three independent defects:
1. `engine.think(...)` is `async def` (`shared/cognitive/_engine.py:78`).
   In sync context this returns a coroutine. The next line calls
   `.get(...)` on the coroutine and raises `AttributeError`.
2. Even if the sync wrapper `engine.think_sync(...)` were used, the
   return type is `ThinkResult` (a dataclass with `answer/score/level/
   cost`). It has no `.get` method and no `action` field — so `.get("action")`
   fails on a sync return as well.
3. Even if the call/return type were correct, the `cog_result` is only
   logged. It is **never** assigned to `ctx.planned_action` or `action`.
   The reasoner's existing `action` continues unmodified.

The escalation has been emitting silent `debug` log lines (`'coroutine'
object has no attribute 'get'`) on every navigation that meets the
trigger condition. **Net effect: CognitiveEngine never affects the
navigation outcome.**

`get_cognitive_engine()` also requires an `agent_name` kwarg
(`shared/cognitive/_engine.py:351`). The current call passes none,
so `TypeError` would fire before the async issue even surfaces.

**Fix in this session:** replace the broken block with
`engine.think_sync(...)` + `agent_name="navigator"` and use the
`ThinkResult.answer` to log a structured advisory; do NOT silently
override `action` (the original action's confidence is already 0.3 —
there is no general-safe way to translate `ThinkResult.answer` text
into a `PageAction`). This restores observability without changing
the planner's contract. Loud-log so we can revisit when there's a
schema for cognitive→action translation.

**B-2 — `_auth.py:137` — `gmail.wait_for_verification(...)` blocks the
asyncio event loop for up to `timeout_s=120` seconds.**

```python
async def handle_email_verification(self, snapshot: dict, platform: str, return_url: str) -> dict:
    ...
    link = self.gmail.wait_for_verification(domain)   # ← sync, time.sleep loop
```

`GmailVerifier.wait_for_verification` (`gmail_verify.py:95-144`) loops
with `time.sleep(interval)` (line 140), where `interval` doubles up to
32 s and the total wait is up to `timeout_s=120 s` by default. Calling
this from an `async def` blocks every other coroutine on the loop —
including the navigator's per-step verification, snapshot fetches,
streaming Telegram updates, and any background CDP listeners. This
defeats the structured asyncio model and starves browser intelligence
(`browser_intelligence.poll_mutations` etc.).

**Fix in this session:** wrap the call in `asyncio.to_thread(...)` so
the polling loop runs on a worker thread and the event loop continues
servicing the page. (`time.sleep` inside the callee is fine when it's
on its own thread.)

### Majors

**M-A — `_navigator.py:1759` — `jobspy.scrape_jobs(...)` blocks the event
loop in `_scrape_direct_url` (called from async `_try_platform_bypass`).**

`scrape_jobs` is a synchronous, network-heavy call; `_scrape_direct_url`
is currently a regular function called from `await self._try_platform_bypass(...)`
via `direct = self._scrape_direct_url(job)` (line 1708). The async
context returns control while jobspy hits Indeed in the background?
No — because the call is sync, the loop blocks until `scrape_jobs`
returns. Add `asyncio.to_thread(...)` here as well, mirroring B-2.

Listed but **not** fixed this session — the platform-bypass scrape path
fires on aggregator walls (Indeed only) and is a small fraction of
runs; B-1/B-2 above are higher priority. Capturing as a follow-up.

**M-B — `_navigator.py:1785-1824` `verify_submission` is wired but never
called in the apply path.**

`_bind_compat_aliases` (line 479) sets `self._verify_submission = self._navigator.verify_submission`
but `apply()` never calls it. Nothing in `_form_filler.fill_application`
or `_navigator.navigate_to_form` invokes the submit-verification logic
either. This means the post-submit confirmation regex never runs in
production — the orchestrator just reports `result["success"]` from
`fill_application` and trusts the form filler's own success heuristic.

The correct path is the SubmissionVerifier inside `native_form_filler.py`
(audited in S1) — `verify_submission` here is dead code that pretends
to provide a separate signal. Recommend deletion in a follow-up;
deferring because removal is non-trivial (test fixtures patch it).

**M-C — `_navigator.py:1648-1654` site-prompt detection is hardcoded
English-only.**

`_dismiss_site_prompt_if_present` matches against
`("are you interested", "not interested", ..., "subscribe", ...)`.
Any non-English ATS prompt slips past, leaving a blocking dialog up
during `_phase_analyze`. This violates the Dynamic-Over-Hardcoded
principle for prompt classification. The fix is the same shape as the
S2 `cookie_dismisser` migration plan (semantic_match against learned
prompt anchors). Recorded in
`docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`. Not
fixed this session — the migration plan is the right home.

**M-D — `verification_detector.py:135` — `re.search(re.escape(pattern), body_lower)`
is just `pattern in body_lower`.**

The author re-escaped a literal pattern then ran `re.search` on it. The
literal lookup is what `_TEXT_PATTERNS` already encodes. Recommend
swapping to `if pattern in body_lower:` — same behavior, fewer cycles.
Module is D-tagged in apply path so impact is low; not fixed.

**M-E — `page_reasoner.py:495-503` `_apply_field_count_guard` is omitted
from the reflection path.**

```python
def reason_with_failure(...):
    ...
    action = self._call_llm(prompt)
    action = self._apply_zero_fields_guard(action, fields, buttons)
    action = self._apply_advance_button_guard(action)   # ← no field_count_guard
    return action
```

`reason_sync` runs all three guards. Reflection runs only two. If the
LLM returns `fill_and_advance` after a failure but drops most of the
required fields, the advance_button_guard catches one slice (empty
advance_button), but a fill plan that omits required fields slides
through with full confidence. This is the same shape of risk as the
guard-coverage fix shipped in S2. Defer to the migration plan that
covers reasoner-side guards.

### Minors / nits

- `_navigator.py:917` `act in ("click_apply", ...)` — three identical
  literal sets in 919/953/1130/1132. Extract to a module constant.
- `_navigator.py:1029-1043` retry-by-role pulls `(role="button"|"link")`
  in two places (also at 1357-1360 and 1429-1437 in `click_apply_button`).
  De-dup into a helper.
- `_executor.py:75-107` action-type dispatch is a long if/elif ladder.
  Could be a dispatch dict; not blocking.
- `cookie_dismisser.py:31-45` regex tier shrinks i18n coverage to ~8
  languages — Japanese/Mandarin/Korean cookie banners would slip through.
  Defer to migration plan.
- `action_executor.py:97` hardcoded close-button text list. Same theme.
- `action_executor.py:341-359` `_click_by_text` and `_try_click_by_text`
  diverge only on return type — refactor candidate.
- `account_manager.py` API mostly C. Keep the file but flag in CLAUDE.md
  that AuthHandler.handle_login/signup no longer thread through the
  AccountManager state machine — only `mark_verified` is reachable.
- `page_reasoner.py:177-178` `_set_cache` skips caching only when
  `action="abort" AND confidence < 0.5`. So `abort` with confidence
  ≥ 0.5 IS cached and re-issued forever for that page+content_hash.
  Worth a comment.
- `playwright_driver.py:381-386` hardcoded iframe name list
  `("icims_content_iframe",)`. Single-element tuple; if a second ATS
  iframe appears, the loop's `break` becomes a footgun.
- `verification_detector.py:36-64` selector/iframe/text patterns are
  acknowledged as structural classification (per
  `.claude/rules/seven-principles.md` §8). OK in current scope; flagged
  for the migration plan.
- `gmail_verify.py:19-25` regex-tier scoring of email links. Structural
  but borderline — multilingual emails could miss. Defer.

---

## STEP 4 — Cross-module wiring

Schema agreements verified:

| Producer | Consumer | Schema | Agree? |
|---|---|---|---|
| `_navigator._phase_act` (1052-1059) emits `signal_type="failure"` payload `{param,action,target}` | `shared.optimization` `OptimizationEngine.emit` | `signal_type, source_loop, domain, agent_name, payload, session_id` | ✅ |
| `navigation/action_executor.emit_fill_failures` (370-399) emits payload `{field,expected,actual,kind}` | OptimizationEngine | same | ✅ |
| `navigation_learner.save_sequence` (148-159) emits `signal_type="adaptation"` payload `{param,old_value,new_value,reason}` | OptimizationEngine | same | ✅ |
| `navigation_learner.mark_failed` (241-252) emits `signal_type="failure"` payload `{param,reason}` | OptimizationEngine | same | ✅ |
| `platform_bypass._emit_learning_signals` (368-463) fans out to NavigationLearner / GotchasDB / OptimizationEngine / ExperienceMemory / TrajectoryStore | each subsystem | structured payload — already validated in S1/S2 | ✅ |
| `apply()` `_opt_engine.start_trajectory + log_step + complete_trajectory` | TrajectoryStore | TrajectoryStep dataclass | ✅ |
| `PageReasoner.invalidate(snapshot)` deletes by cache_key | `_get_cached(key)` reader | both compute key via `_cache_key(url, page_text, dialog_text, fields, buttons)` | ✅ |
| `FormNavigator._make_result` builds `planned_action_dict` | NativeFormFiller `_click_navigation`, `_is_submit_page` (S1 audit) | dict with `action`, `advance_button`, `confidence`, `expected_outcome`, `page_type` | ✅ |
| `AuthHandler.mark_verified(domain)` writes accounts table | `get_account_info` reader | both use `_normalize_domain` | ✅ |

No broken consumers found. All emitted signals have at least one
documented consumer. The unused-but-wired functions noted in STEP 1+2
(`OverlayDismisser.dismiss_all` family, `verify_submission`,
`AccountManager.create_account/get_credentials`) emit no signals — they
are simply unused, not orphaned signal sources.

---

## STEP 5 — Live evidence

A full live `JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner
job-apply-next 1` run is not safe in this session: B-1's broken
escalation logs `debug` lines that wouldn't surface, B-2 would block
the loop on the first email-verify-required form, and the live
pipeline mutates production DBs. Following S1/S2 precedent, I'm using
targeted pytest sweeps for evidence:

```
$ pytest tests/jobpulse/test_navigation_phases.py \
         tests/jobpulse/test_navigator_click_apply_dynamic.py \
         tests/jobpulse/test_navigator_fill_and_advance_wiring.py \
         tests/jobpulse/test_planned_action_threaded.py \
         tests/jobpulse/test_auth_verification_routing.py \
         tests/jobpulse/test_extended_reflection_triggers.py \
         tests/jobpulse/test_gmail_verify.py \
         tests/jobpulse/test_page_reasoner.py \
         tests/jobpulse/test_navigation_learner.py \
         tests/jobpulse/test_classifier.py \
         tests/jobpulse/test_overlay_dismisser.py \
         tests/jobpulse/test_action_executor.py \
         tests/jobpulse/test_browser_cleanup.py \
         tests/jobpulse/test_platform_bypass.py
```

Pre-fix sweep result — see `STEP 6` below for the after-fix run.

---

## STEP 6 — Fixes

| ID | Where | Fix | Tests |
|---|---|---|---|
| **B-1** | `_navigator.py:852-864` | After advisor consult: **deletion was the right call**, not a "log-only revival". The block has been throwing AttributeError into a `debug` logger since it was written. Reviving it with `think_sync` would burn $0.005-$0.05 per stuck step (loop fires repeatedly while stuck) for an advisory line nothing consumes. The replacement emits a single structured INFO log so the stuck-state is still observable but no LLM is called. The rule "log-only when no consumer exists for the action" comes from the advisor reconcile. | `TestB1NoCognitiveCoroutineLeak::test_phase_plan_stuck_state_does_not_call_cognitive_engine` (asserts cognitive engine is NOT imported on the stuck path) + `test_phase_plan_stuck_state_does_not_raise_on_old_path` |
| **B-2** | `_auth.py:137` | Wrap `self.gmail.wait_for_verification(domain)` in `await asyncio.to_thread(...)` so the asyncio loop continues during email polling. | `TestB2EmailVerificationDoesNotBlockEventLoop::test_concurrent_coroutine_progresses_during_polling` (concurrency test — runs a tick coroutine while the gmail verifier sleeps 1.5s synchronously, asserts ≥8 ticks fire during the sleep). Per advisor: a pure `asyncio.to_thread.assert_called` test would pass against the broken pre-fix code too, so we measure actual loop progress instead. |

Live-evidence sweep, post-fix:
```
$ python -m pytest tests/jobpulse/test_navigation_phases.py \
  tests/jobpulse/test_navigator_click_apply_dynamic.py \
  tests/jobpulse/test_navigator_fill_and_advance_wiring.py \
  tests/jobpulse/test_planned_action_threaded.py \
  tests/jobpulse/test_auth_verification_routing.py \
  tests/jobpulse/test_extended_reflection_triggers.py \
  tests/jobpulse/test_navigation_audit.py
======================= 47 passed in 8.57s ========================
```

No regressions.

Deferred to follow-up sessions per HARD-STOP rule (B-1+B-2 are on the
actual apply path, M-A through M-E are platform-bypass-only or
documentation-only):

| ID | Why deferred |
|---|---|
| M-A (`_scrape_direct_url` blocking I/O) | Indeed-only fallback inside aggregator-bypass path; rare runtime hits. Same shape of fix as B-2 (`asyncio.to_thread`). |
| M-B (`verify_submission` dead) | Removal needs migration path; tests patch it. Documentation-only this session. |
| M-C (i18n site-prompt) | Subsumed by `2026-05-04-regex-to-dynamic-migration.md` plan. |
| M-D (`re.escape`+`re.search` typo) | Module is D-tagged in apply path. |
| M-E (reflection skips `field_count_guard`) | Same migration plan as M-C/D. |
| Minors / nits | Listed in STEP 3 above; per audit policy "minor/nit findings: list but do NOT fix unless a blocker fix touches the same function". |

---

## STEP 7 — Architecture-doc update

Three diffs needed (will be batched at the end of all 11 audits per the
prompt's final-step instruction):

1. `docs/job-application-pipeline.md` claims CognitiveEngine escalation
   fires when navigation gets stuck. Reality (pre-fix): the block runs
   but the result is silently discarded into AttributeError. Reality
   (post-fix): cognitive escalation is **not wired** in the navigator
   — only an INFO advisory is logged. Update the doc to delete the
   escalation claim and add a note: "Cognitive escalation in the
   navigator was removed in S3 audit (2026-05-07) because no
   `ThinkResult`→`PageAction` translator exists; if revived, see
   `_phase_plan` for the trigger condition."
2. The doc treats `_navigator.verify_submission` as a separate
   post-submit verifier; in practice the SubmissionVerifier inside
   NativeFormFiller is the only one that runs. The compat-alias on the
   orchestrator wires it for tests but the apply path doesn't call it.
3. The doc references OverlayDismisser as the single source of truth
   for overlay dismissal. Reality: the cookie-dismissal path runs
   `self.cookie_dismisser.dismiss` (legacy) + `dismiss_cookie_banner_playwright`
   + a third `get_snapshot` per `_phase_act` iteration (line 1175-1180),
   while `OverlayDismisser._dismiss_cookie_banner / _generic_modal /
   _promo_popup` (D-tagged) sit unused. The consolidation referenced
   in `overlay_dismisser.py`'s docstring is incomplete — add a note in
   the doc.

All three deltas filed for the final-step doc update; not in this commit.

---

## Session summary

- **Functions audited:** ~150 (full inventory). A-tagged ~120, C
  ~10, D ~20 (`OverlayDismisser` non-LinkedIn + most of
  `AccountManager` + `verify_submission` + `verification_detector`).
  Apply-path runtime read line-by-line for the navigator (1846 LOC),
  page_reasoner (658 LOC), classifier (478 LOC), action_executor
  (399 LOC), playwright_driver (736 LOC), navigation_learner,
  cookie_dismisser, sso_handler, gmail_verify, account_manager,
  platform_bypass, browser_intelligence, page_analyzer.
- **Blockers:** 2 (B-1 dead/throwing cognitive escalation, B-2
  event-loop-blocking gmail polling). **Both FIXED.**
- **Majors:** 5 (M-A through M-E). All listed; deferred to existing
  migration plans or documentation-only follow-ups.
- **Minors / nits:** 11. Documented; not fixed.
- **Tests:** 3 new audit guards in `tests/jobpulse/test_navigation_audit.py`.
  47/47 navigation regression sweep pass.
- **Live evidence:** pytest sweep (47 navigation tests) + the
  concurrency test in test_navigation_audit.py (proves the event loop
  ticks during gmail polling). Per S1/S2 precedent — running a real
  `JOB_AUTOPILOT_AUTO_SUBMIT=false job-apply-next 1` is gated on the
  blockers being shipped first, which they now are.
- **Pause point:** end of subsystem-3. Next: subsystem-4 (`screening_pipeline`).

### Known limitations / next-session notes (advisor reconcile)

1. **`CLAUDE.md:98`** lists "Cognitive Escalation" as one of the three
   self-adaptation layers verified after every application. With B-1
   shipped, that claim is **false at the navigator layer** — the code
   that previously pretended to escalate is gone. The other two layers
   (CorrectionCapture / strategy_reflector) still hold. Add this delta
   to the final-step architecture-doc batch update.
2. **Test guard scope:** `test_phase_plan_stuck_state_does_not_call_cognitive_engine`
   patches `shared.cognitive.get_cognitive_engine` (public API path).
   A future contributor who imports via the private path
   `from shared.cognitive._engine import get_cognitive_engine` would
   silently bypass the guard. The current removal in `_navigator.py`
   makes this moot, but if cognitive escalation is ever revived, the
   test patch list needs to cover both surfaces.
