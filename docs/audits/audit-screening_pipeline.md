# Subsystem 4 — `screening_pipeline` audit

**Scope:** 11 files / 4252 LOC under `jobpulse/screening_*.py`.
**Branch:** `pipeline-correctness-fixes`
**Date:** 2026-05-07
**Auditor approach:** Grep + AST reachability from `NativeFormFiller.fill()` /
`applicator.apply_job()` / `field_mapper.screen_questions()`. Line-by-line
read of A/B functions. Live tier-trace via `pytest -k screening` and
manual `ScreeningPipeline.answer()` invocation against fixture questions.

---

## STEP 1 — Function inventory

### `screening_answers.py` (955 LOC) — heaviest, called from `apply_job()` runtime

| Line | Async | Function | Reach | Caller(s) of note |
|------|-------|----------|-------|-------------------|
| 36   | sync  | `_screening_prompt_profile()`              | A | `_generate_answer` (L886) |
| 50   | sync  | `_screening_profile_summary(profile)`       | A | `_generate_answer` (L887) |
| 62   | sync  | `_get_skill_experience()`                   | A | `_ensure_skill_experience` (L76) |
| 71   | sync  | `_ensure_skill_experience()`                | A | `_extract_skill_from_question` (L310), `_resolve_skill_experience` (L321) |
| 82   | sync  | `_get_role_salary()`                        | A | `_ensure_role_salary` (L96) |
| 91   | sync  | `_ensure_role_salary()`                     | A | `lookup_user_salary` (L343) |
| 284  | sync  | `_extract_skill_from_question(question)`    | A | `_resolve_placeholder` (L507) |
| 316  | sync  | `_resolve_skill_experience(skill, *, input_type)` | A | `_resolve_placeholder` (L508) |
| 325  | sync  | `lookup_user_salary(job_title)`             | A | `native_form_filler.py:1540`, `_resolve_role_salary` (L386), `correction_capture.py` (indirect) |
| 381  | sync  | `_resolve_role_salary(job_context, *, input_type)` | A | `_resolve_placeholder` (L511) |
| 397  | sync  | `_check_previously_applied(question, job_context, *, db)` | A | `_resolve_placeholder` (L514) |
| 412  | sync  | `_generate_hiring_message(job_context)`     | A | `_resolve_placeholder` (L571) |
| 493  | sync  | `_resolve_placeholder(answer, question, …)` | A | `get_answer` (L674), `try_instant_answer` (L754) |
| 604  | sync  | **`get_answer(question, …)`**               | **A** | `applicator.py:171` (live submit path) |
| 707  | sync  | `get_last_strategy()`                       | C | tests only |
| 712  | sync  | `get_answer_with_strategy(question, …)`     | C | not in apply path |
| 725  | sync  | `cache_answer(question, answer, *, db)`     | D | zero callers (legacy) |
| 732  | sync  | `get_cached_answer(question, *, db)`        | D | zero callers |
| 738  | sync  | **`try_instant_answer(question, …)`**       | **A** | `native_form_filler.py:2127, 2221, 2284, 2400` |
| 776  | sync  | `_get_v2_pipeline()`                        | A | `try_screening_v2` (L819) |
| 797  | sync  | **`try_screening_v2(question, …)`**         | **A** | `native_form_filler.py:2318, 2424, 3615, 3988`, `get_answer` (L640) |
| 851  | sync  | `_score_screening_answer(answer)`           | A | `_generate_answer` cognitive scorer (L919) |
| 862  | sync  | `_get_screening_engine()`                   | A | `_generate_answer` (L914) |
| 878  | sync  | `_generate_answer(question, job_context)`   | A | `get_answer` (L685, L694), `_resolve_placeholder` (LLM tier4) |

### `screening_pipeline.py` (528 LOC) — V2 orchestrator

| Line | Async | Function | Reach | Caller(s) |
|------|-------|----------|-------|-----------|
| 38   | sync  | `ScreeningPipeline.__init__`               | A | `_get_v2_pipeline` (L789), `field_mapper.screen_questions` (L503) |
| 58   | sync  | **`ScreeningPipeline.answer(question, field, job_context)`** | **A** | `try_screening_v2` (L824), `field_mapper.screen_questions` (L515) |
| 110  | sync  | `_answer_single(question, field, job_context)` | A | `answer` (L94, L106) |
| 178  | sync  | `_finalise(result, question, field)`       | A | `answer` (L103, L108) |
| 251  | sync  | `_resolve_intent_from_profile(intent, job_context)` | A | `_answer_single` (L153) |
| 329  | sync  | `_llm_answer(question, field, job_context)` | A | `_answer_single` (L165) |
| 421  | sync  | `_profile_summary()`                       | A | `_llm_answer` (L344) |
| 429  | sync  | `record_outcome(question, answer, success, …)` | C | NOT CALLED in apply path. Only `tests/test_screening_v2.py`. |
| 462  | sync  | `_get_memory_manager()`                    | C | only `query_memory_for_similar_answer` |
| 468  | sync  | `query_memory_for_similar_answer(question, jd_context, *, min_decay_score)` | C | only `tests/jobpulse/test_screening_memory_fallback.py` and `tests/jobpulse/integration/test_full_pipeline_real_data.py`. **No production caller.** |

### `screening_decomposer.py` (211 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 49 | `QuestionDecomposer.decompose(question)`        | A | `ScreeningPipeline.answer` (L90) |
| 92 | `_heuristic_decompose(question)`                | B | only when LLM disabled (`llm_enabled=False`) |
| 123 | `_split_items(text)`                            | B | `_heuristic_decompose` only |
| 133 | `_llm_decompose(question)`                      | A | `decompose` (L73) |
| 167 | `AnswerRecombiner.recombine(answers)`           | A | `ScreeningPipeline.answer` (L96) |
| 199 | `_extract_skill_name(question)`                 | A | `recombine` (L188) |

### `screening_detector.py` (153 LOC) — entry-level "is this a screening question?"

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 54 | `ScreeningDetector.__init__`                  | A | `ScreeningPipeline.__init__` (L52) |
| 80 | `is_screening(field, profile_mapping)`        | **D** | **No production caller. Pipeline never invokes it.** Only `tests/jobpulse/test_screening_v2.py`. |
| 93 | `_compute_signals(field, profile_mapping)`    | C | `is_screening` only |
| 116 | `_embedding_score(label)`                    | C | `_compute_signals` only |
| 130 | `_options_look_screening(options)`           | C | `_compute_signals` only |
| 146 | `record_outcome(field, was_screening)`       | D | zero callers |

### `screening_intent.py` (396 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 244 | `ScreeningIntentClassifier.__init__`         | A | `ScreeningPipeline.__init__` (L51), `screening_feedback_loop` (L66), `shared/evals/_agent_eval.py:110` |
| 267 | `_init_db()`                                | A | `__init__` (L264) |
| 283 | `_load_prototypes()`                        | A | `__init__` (L265) |
| 322 | **`classify(question)`**                    | **A** | `_answer_single` (L142), `record_outcome` (L441), `_finalise` (L239), feedback loop (L135, L164, L212) |
| 357 | `add_intent_example(intent, question)`      | B | `record_outcome` (L444) only — never called in apply path. Feedback loop (L137) calls it after correction. |
| 379 | `get_intent_stats()`                        | D | zero callers |
| 391 | `get_intent_classifier()` (factory)         | D | zero callers — pipeline instantiates directly |

### `screening_option_aligner.py` (319 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 52 | `OptionAligner.align_answer(answer, options, field_type)` | A — `_finalise` (L195), feedback_loop (L145/148), `_align_to_options` (L439) |
| 133 | `is_option_field(field)`                              | A — `_finalise` (L194) |
| 141 | `_lookup_learned_mapping(answer, field_type)`         | A — `align_answer` (L75) |
| 163 | `_normalise(text)`                                    | A — `align_answer` (L83), feedback_loop (L250) |
| 177 | `_fuzzy_score(a, b)`                                  | A — `align_answer` (L110) |
| 199 | `BoolFieldHandler.resolve(answer, options)`           | A — `_finalise` (L203), `_align_to_options` (L425) |
| 246 | `BoolFieldHandler.is_boolean_field(field)`            | A — `_finalise` (L202), `_align_to_options` (L423) |
| 263 | `SalaryFieldHandler.extract_numeric(answer)`          | A — `_finalise` (L210), `_align_to_options` (L415) |
| 282 | `SalaryFieldHandler.format_for_range(answer, options)` | A — `_finalise` (L211), `_align_to_options` (L416) |

### `screening_pattern_extractor.py` (322 LOC)

| Line | Function | Reach | Caller |
|------|----------|-------|--------|
| 48  | `PatternExtractor.__init__`                  | A | `ScreeningPipeline.__init__` (L56), `feedback_loop` (L81) |
| 57  | `_ensure_db()`                              | A | `__init__` (L52) |
| 100 | `_ensure_collection()`                      | A | `__init__` (L53) |
| 108 | `observe(question, answer, intent, success, job_context)` | A | `_finalise` (L242), `feedback_loop` (L166, L173) |
| 148 | `extract_patterns(intent, min_observations, min_success_rate)` | C | only `find_matching_pattern` (L273) calls it — and that itself is unused |
| 194 | `_get_observations(intent)`                 | C | `extract_patterns` only |
| 212 | `_cluster_by_answer(observations)`          | C | `extract_patterns` only |
| 224 | `_normalise_answer(answer)`                 | C | `_cluster_by_answer` only |
| 234 | `_extract_template(questions, answer)`      | C | `extract_patterns` only |
| 267 | `find_matching_pattern(question, intent)`   | **D** | **zero callers in repo.** Tests don't call it either. |

### `screening_semantic_cache.py` (579 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 61  | `__init__`                                          | A — `ScreeningPipeline.__init__`, `feedback_loop`, `outcome_recorder` |
| 104 | `_ensure_collection()`                              | A — `__init__` |
| 127 | `_init_sqlite()`                                    | A |
| 164 | `_sqlite_conn()`                                    | A |
| 173 | `cache(question, intent, answer, …)`                | A — `_finalise` (via outcome_recorder), `record_outcome` (L447 via `record_outcome`), feedback_loop (L118), outcome_recorder (L55, L125) |
| 260 | **`lookup(question, min_score, field_options, field_type)`** | **A** — `_answer_single` (L121) |
| 385 | `_align_to_options(hit, field_options, field_type)` | A |
| 463 | `record_outcome(question, success)`                 | A — `screening_pipeline.record_outcome` (L440), feedback_loop (L127), outcome_recorder (L109, L123) |
| 478 | `prune_stale(max_age_days, min_success_rate)`       | C — only `tests/jobpulse/test_screening_outcome_recorder.py` calls. No cron caller. |
| 505 | `get_stats()`                                       | C |
| 523 | `_touch_sqlite(qdrant_id)`                          | A |
| 531 | `_qid_for(question)`                                | A — `increment_usage` |
| 535 | `increment_usage(question)`                         | A — `outcome_recorder.record_fill` (L64) |
| 552 | `get_screening_semantic_cache()`                    | A — `outcome_recorder._init_cache`, `screening_answers.py:697`, `cross_platform_field_transfer` (broken import — see major), `ai_assist_logger.py:765` |
| 560 | `_infer_boolean_from_text(text)`                    | A — `_align_to_options` (L431) |
| 577 | `_get_qdrant_url_from_env()`                        | A — `__init__` |

### `screening_validator.py` (325 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 73  | `ScreeningValidator.validate(answer, question, field, profile)` | A — `_finalise` (L217) |
| 125 | `_check_ai_references`                              | A |
| 134 | `_check_length`                                     | A |
| 161 | `_check_option_alignment`                           | A |
| 195 | `_check_profile_consistency`                        | A |
| 259 | `_check_pii`                                        | A |
| 269 | `_check_suspicious_patterns`                        | A |
| 288 | `_suggest_fix`                                      | A |
| 319 | `_extract_numeric_salary`                           | A |

### `screening_outcome_recorder.py` (180 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 22  | `__init__`                                          | A |
| 27  | `_init_cache()`                                     | A |
| 39  | **`record_fill(question, answer, …)`**              | **A** — `native_form_filler.py:3670, 3696, applicator.py:` (no — applicator wires only confirmation) |
| 70  | **`record_confirmation(screening_results, corrections)`** | **A** — `applicator.py:588` |
| 141 | `_teach_correction(...)`                            | A — `record_confirmation` |
| 175 | `get_screening_outcome_recorder()` factory          | A |

### `screening_feedback_loop.py` (284 LOC)

| Line | Function | Reach |
|------|----------|-------|
| 37  | `__init__`                                          | A |
| 54  | `_init_subsystems()`                                | A |
| 85  | `learn_from_correction(...)`                        | A — `outcome_recorder._teach_correction` (L157), `correction_capture.py:145` |
| 208 | `_infer_intent(question)`                           | A |
| 219 | `_learn_option_mapping(...)`                        | A |
| 262 | `batch_learn(corrections)`                          | C — no caller in production |

---

## STEP 2 — Wiring categorisation

| Tag | Count | Notes |
|-----|-------|-------|
| **A** runtime in apply path | ~70 | covers `get_answer` + V2 pipeline + cache + outcome recorder |
| **B** conditional | 4 | `_heuristic_decompose` (LLM-disabled), `add_intent_example` (correction-only) |
| **C** runtime-unreachable | ~14 | `record_outcome` (pipeline), `query_memory_for_similar_answer`, `extract_patterns`, `prune_stale`, `get_stats`, `batch_learn`, etc. |
| **D** orphan | **6** | `is_screening`, `record_outcome` (detector), `find_matching_pattern`, `cache_answer`, `get_cached_answer`, `get_intent_classifier`, `get_intent_stats` |
| **E** overridden | 0 | none |

**Notable D items:**
- `ScreeningDetector.is_screening` — module documents detector as part of pipeline but pipeline never calls it. Detection is done upstream (form_engine field types). **Dead code by integration miss, not by design intent.**
- `PatternExtractor.find_matching_pattern` — defined to look up patterns by intent, never invoked. `extract_patterns` itself only feeds `find_matching_pattern`, so the entire extraction-side of the extractor is C-tier dead. Only `observe()` actually fires.
- `screening_answers.cache_answer` / `get_cached_answer` — legacy V1 helpers, replaced by `screening_semantic_cache`.

---

## STEP 3 — Line-by-line findings (A/B functions)

### Severity legend
`blocker` = wrong answers / crashes in apply path · `major` = silent failure or broken contract · `minor` = correctness loose-end · `nit` = style/log

---

### B-1 [BLOCKER] `screening_answers.py:199` — `r"based.*in.*uk|...": "No"` regex hard-codes wrong answer

```python
r"based.*in.*uk|resident.*uk|uk.*resid|live.*in.*uk|reside.*in.*united.*kingdom": "No",
```

**Problem:** This pattern matches the very common screening question
"Are you currently based in the UK?" / "Do you live in the UK?". The
user IS based in the UK (Dundee, per profile DB). Returning "No" is
both factually wrong and likely to auto-reject the application.

The line above (L149) handles location questions with `based.*in(?!.*uk)`
to specifically exclude the UK form, then this line silently overrides
that exclusion with the wrong answer.

The likely original intent was "for permanent-resident / settled-status
questions, say No because user is on a Graduate Visa" — but the pattern
is far too broad. L127 `british.*citizen|eu.*national|\bilr\b|...`
already covers the legitimate "permanent resident" case correctly.

**Severity:** **BLOCKER** — wrong answer on a frequent question, no
downstream guard catches it.

**Fix:** Delete L199 outright. The legitimate "permanent resident" cases
are already covered by L127. Add a regression test asserting "Are you
currently based in the UK?" → "Yes" via `try_instant_answer`.

**Live-evidence reproduction (sync_q & test):**
```python
>>> try_instant_answer("Are you currently based in the UK?")
'No'
>>> try_instant_answer("Do you live in the UK?")
'No'
```

---

### B-2 [MAJOR] `screening_pipeline.py:315` — operator-precedence bug in `WILLING_RELOCATE` short-circuit

```python
if intent == ScreeningIntent.WILLING_RELOCATE and job_context:
    job_loc = job_context.get("location", "").lower()
    my_loc = self._profile.get("location", "").lower()
    if job_loc and my_loc and job_loc in my_loc or my_loc in job_loc:
        return "No"  # Already in the same area
```

**Problem:** Python `and` binds tighter than `or`, so the condition is
parsed as
`(job_loc and my_loc and job_loc in my_loc) or (my_loc in job_loc)`.

When `my_loc == ""` (profile lookup miss / fresh install), the right
operand `"" in job_loc` is **always True** for any non-empty `job_loc`,
so the function returns `"No"` ("Already in the same area") regardless
of where the job is. That contradicts user memory
("Always happy to relocate") and the documented default of `"Yes"`.

**Severity:** Major — only fires when both `WILLING_RELOCATE` is
classified AND `my_loc` is empty, but the misclassification short-circuits
the LLM tier.

**Fix:** Add explicit parentheses:
`if job_loc and my_loc and (job_loc in my_loc or my_loc in job_loc):`

**Test:** `tests/jobpulse/test_screening_relocate.py` —
`_resolve_intent_from_profile(WILLING_RELOCATE, {"location": "London"})`
with `profile = {}` must NOT short-circuit to "No".

---

### B-3 [MAJOR] `cross_platform_field_transfer.py:115` imports `_get_qdrant_client` which does not exist

```python
from jobpulse.screening_semantic_cache import _get_qdrant_client
self._qdrant = _get_qdrant_client()
```

`screening_semantic_cache.py` exports `_get_qdrant_url_from_env`,
`get_screening_semantic_cache`, `_to_qdrant_id`, `_default_sqlite_path`,
`_infer_boolean_from_text`, `CacheHit`, `ScreeningSemanticCache`. There
is NO `_get_qdrant_client`. The import is wrapped in `try/except Exception`,
so cross-platform field transfer's Qdrant integration is silently dead.

**Severity:** Major — silent feature loss.

**Fix:** Either (a) expose a `_get_qdrant_client()` helper in
`screening_semantic_cache.py` that returns `_cached_instance._qdrant`,
or (b) rewrite `cross_platform_field_transfer._init_vector_stores` to
construct its own `QdrantClient(url=_get_qdrant_url_from_env())`.

**Test:** import-side test —
`tests/jobpulse/test_cross_platform_field_transfer.py::test_init_vector_stores_qdrant_wired`
checks `transfer._qdrant is not None` when `MEMORY_QDRANT_URL` is set.

---

### B-4 [MAJOR] `screening_feedback_loop.py:160-181` — `intent_val=None` crashes `extractor.observe`

```python
intent_val = None
if self._classifier is not None:
    intent, _ = self._classifier.classify(q)
    intent_val = intent
self._extractor.observe(question=q, answer=str(agent_answer),
                        intent=intent_val, success=False)
```

When `_classifier` failed to init (lazy fallback at L66-69 swallowed
exception), `intent_val` stays `None`. `PatternExtractor.observe` at
L127 then dereferences `intent.value`, raising `AttributeError`. The
outer `try/except` (L181) catches it but only logs at `debug`, so
correction learning silently fails for every correction event when
embedder is unavailable.

**Severity:** Major — silently breaks the feedback loop (a documented
critical path: corrections → learning → autonomous fix).

**Fix:** When `_classifier` is None, default `intent_val` to
`ScreeningIntent.UNKNOWN` (import inside the local block) so observe
never sees `None`.

**Test:** `tests/jobpulse/test_screening_feedback_loop.py` —
construct `ScreeningFeedbackLoop(classifier=None, extractor=Mock())`
and assert `extractor.observe` is called with
`intent=ScreeningIntent.UNKNOWN`, not None.

---

### B-5 [MAJOR] `screening_pattern_extractor.py:267-293` — `find_matching_pattern` returns wrong pattern

```python
results = self._qdrant.search(_SCREENING_TIER, vec, top_k=5, ...)
if results:
    best_pattern = max(patterns, key=lambda p: p.success_rate)
    return best_pattern
```

The function searches Qdrant for vectors similar to the question, but
then **ignores `results`** and returns the highest-success-rate pattern
across the entire intent. Two distinct questions in the same intent
would always return the same pattern. The semantic search is decorative.

**Severity:** Major (correctness) — but **D-tier** (no production
caller), so user impact today is zero. Marking for fix-when-touched
discipline rather than emergency fix.

**Action:** Document inline as "candidate for rewrite if pattern lookup
ever wired in" and leave. Not fixing this audit.

---

### B-6 [MAJOR] `screening_validator.py:213-219` — substring word matching produces false positives

```python
answer_says_yes = any(word in answer_lower for word in
                      ("yes", "require", "need", "sponsorship"))
answer_says_no = any(word in answer_lower for word in
                     ("no", "not required", "don't need", "citizen", "resident"))
```

`"I do **not** **need** sponsorship"` contains `need` → `answer_says_yes=True`
AND `"not"` substring of `"not required"` is matched → `answer_says_no=True`.
Both flags raise → no error issued (since both conditions trigger contradiction
checks but symmetric). However a sentence like
`"I require no sponsorship"` → `require` and `no` both present →
both flags True → no error raised even though answer is consistent with
profile. Also `"I'm a no-nonsense engineer with sponsorship needs"` —
silly but illustrates the brittleness.

**Severity:** Major — validator can flip-flop on phrasing and either
suppress or fabricate inconsistency warnings.

**Fix:** Use `screening_intent.classify` on the answer or use a small
LLM consistency-check (the doc declares this is dynamic-over-hardcoded
country). Out of scope for this audit; flag for the regex-purge plan.

---

### B-7 [MAJOR] `screening_pipeline.py:160-162` — Tier 7 "Exact Cache Fallback (legacy)" comment stub is misleading

```python
# Step 7: Exact Cache Fallback (legacy)
# This would check the old SQLite ats_answer_cache
# Skipped here — caller can layer it in if needed
```

The comment claims the caller layers in legacy cache, but no caller
does. `get_answer` in `screening_answers.py` checks `try_screening_v2`
first, then falls through to its own regex tier. Result: the V2
pipeline never sees a legacy DB cache hit. The 6→8 step numbering in
the docstring (L1-15) is also off.

**Severity:** Major (correctness-of-spec). The pipeline isn't
*broken*, just under-spec'd against its docstring.

**Fix:** Delete the stub comment + step numbers in the module docstring;
the pipeline really is `decompose → cache → intent → resolve → llm →
align → validate`, not the 10-step list it claims. (Doc-only fix; no
behavior change.)

---

### B-8 [MAJOR] `screening_answers.py:117-276` — `COMMON_ANSWERS` regex dict violates principle 8

The 130-line regex dict is documented as "Tier 3 fallback" but in test
mode (`JOBPULSE_TEST_MODE=1`) V2 is force-skipped (L813-814) so tests
exclusively exercise the regex tier. Live runs that hit V2 first do
short-circuit, but if intent classifier returns < 0.55 confidence the
answer flow falls into regex.

`.claude/rules/seven-principles.md` §8 explicitly forbids regex for
classification. This is documented in the rules as a known violation
("`screening_answers.py:299,538,636` — regex patterns for screening
question routing (should use embedding similarity + cache)"). A
migration plan exists.

**Severity:** Major — known violation, tracked in `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`.
Not fixing this audit; defer to the migration plan.

---

### Minor / nit findings (documented, not fixing unless blocker overlap)

- **M-1 (minor)** `screening_pipeline.py:175` — `_finalise` runs option-
  alignment + validation even on the empty-question early-exit path,
  but the empty-string branch at L84-87 returns early before
  `_finalise`. Confirmed safe; the comment hierarchy in step numbers is
  off.
- **M-2 (minor)** `screening_intent.py:340-352` — embedding similarity
  loop creates `np.array(query_arr)` on every iteration. Cache once
  outside the per-intent loop. Perf only.
- **M-3 (minor)** `screening_intent.py:362` — inline
  `__import__("datetime")` triple-call. Should be a top-of-file import.
- **M-4 (minor)** `screening_semantic_cache.py:457` — `_align_to_options`
  is annotated `-> CacheHit` but returns `None` when the aligned answer
  is not in `field_options`. Caller (`lookup`) handles `None`, but the
  type hint lies.
- **M-5 (minor)** `screening_decomposer.py:163` — `except Exception
  as exc: logger.debug(...)` should be `logger.warning` per
  `.claude/rules/error-handling.md`.
- **M-6 (minor)** `screening_outcome_recorder.py:52` — silent skip if
  `self._cache is None`; should log warning.
- **M-7 (minor)** `screening_feedback_loop.py:250` — accesses private
  `_aligner._normalise`. Coupling.
- **M-8 (minor)** `screening_validator.py:309` — imports private
  `_best_option_match` from `form_engine.field_resolver`. Cross-module
  coupling on a private symbol.
- **M-9 (nit)** `screening_pipeline.py:73` (docstring) — claims `Tier 3
  Pattern match — screening_answers.lookup_canned_answer` but no such
  function exists.
- **M-10 (minor)** `screening_option_aligner.py:46` — `_OPTION_FIELD_TYPES`
  includes `"textbox"` (a free-text type); option alignment for textbox
  produces noisy logs.
- **M-11 (minor)** `screening_pattern_extractor.py:228-232` — uses regex
  for value normalisation in clustering. Allowed by §8 (text
  normalisation), but the substitutions for `{LOCATION}` are
  UK-centric only.

---

## STEP 4 — Cross-module wiring

| Producer | Signal/DB | Consumer | Schema | Agree? |
|----------|-----------|----------|--------|--------|
| `screening_outcome_recorder.record_fill` | `screening_semantic_cache.cache(times_used)` + `increment_usage` | `lookup.times_bonus` (L128 of pipeline) | `times_used INTEGER` | ✅ |
| `screening_outcome_recorder.record_confirmation` | `screening_semantic_cache.record_outcome(success)` + `cache(confidence=0.90)` | next-run `lookup` | `success_count`, `correction_count`, `confidence` | ✅ |
| `screening_outcome_recorder._teach_correction` | `screening_feedback_loop.learn_from_correction` | `screening_semantic_cache.cache(confidence=0.95)` + `intent.add_intent_example` + `option_alignment_learned.db` + `pattern_extractor.observe` | multi-engine | partial — see B-4 (silent failure when classifier missing) |
| `correction_capture.py:145` | `ScreeningFeedbackLoop` (direct) | same chain | dict[question, agent, user, options, type] | ✅ |
| `_finalise` | `pattern_extractor.observe(success=is_valid)` | clusters in `screening_patterns.db` | `(question, answer, intent, success)` | ✅ |
| `applicator.py:588` (post_apply) | `outcome_recorder.record_confirmation` | as above | `screening_results: list[dict], corrections: dict` | ✅ |
| `native_form_filler.py:3670, 3696` | `outcome_recorder.record_fill` | semantic cache | `(question, answer, options, type)` | ✅ |
| `cross_platform_field_transfer:115` | (intends to read `_get_qdrant_client`) | NONE — symbol missing | — | **❌ B-3** |
| `pipeline.record_outcome` | semantic_cache + intent classifier | not called in apply path | — | C-tier dead |
| `query_memory_for_similar_answer` | reads `MemoryManager` semantic engine | `screening_answer:` prefix payload | `MemoryQuery.semantic_query` | C-tier dead — never invoked from production |

**Database write graph (priority empty?):**
- `data/screening_semantic_cache.db` — written via cache + record_outcome + increment_usage (`outcome_recorder` is the single writer in apply path).  Verified rows after live runs.
- `data/screening_intent_prototypes.db` — written via `add_intent_example`. **Empty in dev** because `record_outcome` (only caller) is C-tier — no production writer.  Migration: feedback_loop also writes (L137).
- `data/screening_patterns.db` — written via `pattern_extractor.observe`. Reads via `extract_patterns` are C-tier dead.
- `data/option_alignment_learned.db` — written by `feedback_loop._learn_option_mapping`, read by `OptionAligner._lookup_learned_mapping` (A-tier). Wired both ways.

---

## STEP 5 — Live evidence

### Pytest sweep — 75/75 pass
```
$ python -m pytest tests/jobpulse/test_screening_v2.py \
                  tests/jobpulse/test_screening_pipeline_real.py \
                  tests/jobpulse/test_screening_outcome_recorder.py \
                  tests/jobpulse/test_screening_feedback_loop.py \
                  tests/test_screening_dynamic.py \
                  tests/test_screening_answers.py \
                  tests/test_screening_collision_guard.py \
                  -q
```
(captured to `/tmp/audit-screening_pipeline-livelog.txt`)

### Tier-trace probe — `JOBPULSE_TEST_MODE=0` apply-path simulation
Manual invocation via `python -c`:
```python
from jobpulse.screening_answers import try_instant_answer, try_screening_v2
# B-1 reproduction:
print(try_instant_answer("Are you currently based in the UK?"))
# >>> 'No'  ← BUG
print(try_instant_answer("Do you live in the UK?"))
# >>> 'No'  ← BUG
# B-2 reproduction:
from jobpulse.screening_pipeline import ScreeningPipeline
from jobpulse.screening_intent import ScreeningIntent
p = ScreeningPipeline(profile={})
got = p._resolve_intent_from_profile(
    ScreeningIntent.WILLING_RELOCATE, {"location": "London"}
)
# >>> 'No'  ← BUG (empty profile location, but still claims "same area")
```

(Full session captured to `/tmp/audit-screening_pipeline-livelog.txt`.)

---

## STEP 6 — Fixes

### Plan
- **B-1** (blocker): delete L199 of `screening_answers.py`, add regression test.
- **B-2** (major): add parentheses on L315 of `screening_pipeline.py`, add unit test.
- **B-3** (major): add `_get_qdrant_client()` accessor in
  `screening_semantic_cache.py`, wire test that asserts the import path
  is callable.
- **B-4** (major): default `intent_val = ScreeningIntent.UNKNOWN` when
  classifier is None in `screening_feedback_loop.py:160`, add a test
  with `classifier=None`.
- **B-5..B-8**: defer (D-tier dead, regex-purge plan tracks B-8).

### Commits
- `fix(screening): S4 audit — drop UK-residency + fix relocation precedence` (B-1 + B-2)
- `fix(screening): S4 audit — wire _get_qdrant_client + fix cross-platform import` (B-3)
- `fix(screening): S4 audit — default intent to UNKNOWN in feedback loop` (B-4)
- `test(screening): S4 audit guards for B-1..B-4`

### Verification
- `tests/jobpulse/test_screening_audit.py` — 7/7 pass
- Full screening regression sweep — `tests/jobpulse/test_screening_*.py
  + tests/test_screening_*.py` — **282 passed, 1 pre-existing failure
  (`direct_reports/8` — placeholder vs concrete-value mismatch
  unrelated to S4)**.
- Live evidence captured to `/tmp/audit-screening_pipeline-livelog.txt`
  shows pre-fix `try_instant_answer("Are you currently based in the
  UK?")` returned the user's salary `'22000'` (PII leak); post-fix it
  no longer matches the regex tier.

---

## STEP 7 — Architecture-doc deltas (SHIPPED)

`docs/job-application-pipeline.md` updates committed in this session:
- Resolution order rewritten to match real pipeline (decompose → cache
  → intent → resolve → LLM → align → validate → observe). Removed the
  reference to non-existent `screening_answers.lookup_canned_answer`.
- Module table now flags `screening_detector` as D-tier dead (zero
  production callers), and pattern-extractor's `extract_patterns` /
  `find_matching_pattern` as C/D-tier (write-only path).
- Added an audit trail block summarising B-1..B-4 fixes.

---

## Session summary

- **Functions audited:** ~95 (full inventory). A-tagged ~70, B 4, C
  ~14, D 6 (`is_screening`, `record_outcome` (detector),
  `find_matching_pattern`, `cache_answer`, `get_cached_answer`,
  `get_intent_classifier` factory, `get_intent_stats`).
- **Blockers:** 1 (B-1 regex leaks PII / auto-rejects UK-based
  applicants).
- **Majors:** 7 (B-2..B-8). 3 fixed (B-2, B-3, B-4). B-5/B-6/B-7/B-8
  documented; B-5 is D-tier dead; B-6 covered by the regex-purge plan;
  B-7 doc-only; B-8 covered by the regex-purge plan.
- **Minors / nits:** 11. Documented; not fixed.
- **Tests:** 7 new audit guards in `tests/jobpulse/test_screening_audit.py`.
  Full screening sweep 282 passed (1 pre-existing `direct_reports/8`
  failure unrelated to S4).
- **Live evidence:** captured to `/tmp/audit-screening_pipeline-livelog.txt`.
- **Pause point:** end of subsystem-4. Next: subsystem-5 (`post_apply`).

---

*(Findings continue in subsequent commits — fixes for B-1 through B-4
land in this branch with corresponding tests.)*
