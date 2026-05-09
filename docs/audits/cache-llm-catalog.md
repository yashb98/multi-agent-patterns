# Cache-or-LLM Audit — Call-Site Catalog (S1)

> **Generated**: 2026-05-08 in `fix(cache-llm-S1)` session.
> **Scope**: every LLM call site in `jobpulse/` and `shared/` per the
> directive in `docs/audits/cache-or-llm-audit.md` §1.
> **Coverage**: 70 unique `(file, function)` call sites across 44 files.

This is the catalog deliverable for **S1** (audit-only, no code changes).
Per `cache-or-llm-audit.md` §6, S1's acceptance criterion is a sibling hash
check that the catalog covers every grep hit. See §I below for the check.

---

## Legend

| Symbol | Status |
|---|---|
| 🟡 | pending — classification + cache key proposed; deep verification deferred to its cluster session |
| 🔄 | in progress — a cluster session is currently working on this row |
| ✅ | fixed — replace with `✅ S<n> <commit-hash>` per `cache-or-llm-audit.md` §4.3 step 8 |
| 🚫 | out of scope — not in `cache-or-llm-audit.md` §6 cluster sequencing (papers, patterns, telegram conversational, audio transcription) |
| 🛠️ | infrastructure — LLM factory or wrapper, routes other calls; not itself a fix target but listed for completeness |
| ⭐ | gold-standard — reference implementation already follows the deterministic-first pattern (§2.2 #6) |

## Classification labels

| Label | Definition (from `cache-or-llm-audit.md` §4.2) |
|---|---|
| **NECESSARY** | Genuine synthesis or novel reasoning. Cache key would be too sparse to hit in practice. |
| **CACHE-REPLACEABLE** | Same input → same output 80%+ of the time. Cache key is well-defined. |
| **DETERMINISTIC** | Answer is in a DB / static map / rule. LLM is being used because of historical accident. |

## Routing labels

How each call site reaches an LLM today. Rows that do not route through
`cognitive_llm_call` skip the L0 Memory-Recall short-circuit and are
candidates for the **S7 cognitive-bypasses** cluster *in addition to* their
primary cluster fix.

| Routing | Meaning |
|---|---|
| `cognitive_llm_call` | Routes through `shared.cognitive` → L0/L1/L2/L3 escalation. Already correctly tiered. |
| `smart_llm_call` | Bound LangChain LLM via `shared.streaming.smart_llm_call`. **Bypasses cognitive.** |
| `chat.completions.create` | Direct OpenAI SDK call. **Bypasses cognitive.** |
| `responses.create` | Direct OpenAI Responses API (vision + tool use). **Bypasses cognitive.** |
| `llm.invoke` / `self.llm.invoke` | LangChain bound LLM `.invoke()`. **Bypasses cognitive.** |
| `ChatOpenAI` | Direct LangChain factory call (used inside `shared/agents.py` factories only). |

---

## §A — Methodology correction (load-bearing finding from S1)

`cache-or-llm-audit.md §4.1`'s grep command:

```
grep -rln "client\.chat\.completions\.create\|smart_llm_call\|get_llm()\|get_openai_client()" \
  --include="*.py" jobpulse/ shared/
```

**This pattern is incomplete.** It misses every call site that uses
`cognitive_llm_call(...)` (the canonical entry point defined in
`shared/agents.py:804`) — including `cv_tailor.py` and `screening_pipeline.py`
which are explicitly named as fix targets in §2.2. It also misses the
gold-standard `skill_extractor.py` (§2.2 #6).

The §4.1 grep returned **28 files**. The corrected grep returns **44 files**
with **70 unique `(file, function)` call sites**:

```
grep -rln "cognitive_llm_call\|smart_llm_call\|chat\.completions\.create\
\|chat\.completions\.acreate\|responses\.create\|ChatOpenAI(\|get_llm()\
\|get_openai_client()" --include="*.py" jobpulse/ shared/ \
  | grep -v __pycache__ | grep -v worktrees
```

Plus 5 files found via `\.invoke(` on bound LLM objects
(`shared/dynamic_agent_factory.py`, `shared/experiential_learning.py`,
`shared/persona_evolution.py`, `shared/prompt_optimizer.py`,
`shared/parallel_executor.py`).

`grep -rn "\.ainvoke(" --include="*.py" jobpulse/ shared/` returns no
results, so async-invoke is not an additional surface as of this commit.

**Future sessions should use the corrected grep.** The hash check in §L
uses the corrected grep as the source of truth.

---

## §B — Cluster S2 · Field-mapping (DETERMINISTIC + recovery)

Target files per `cache-or-llm-audit.md §6`: `field_mapper.py`,
`field_resolver.py`. Acceptance: live URL fills the same fields, log shows
`dict-mapped` for ≥ 80 % of fields.

**Two local-Ollama setup gaps** were uncovered while running the live
verification (SSH-tunnelled remote host); both are tooling, not S2's
own work, so they live as findings here rather than code in this commit:

1. `shared/agents.py:get_openai_client` default `timeout=30.0` cuts off
   qwen3:32b mid-response (30–60 s for cv_tailor-sized prompts).
   180 s is a safer default; OpenAI cloud calls finish in <10 s so it
   still trips on real hangs.
2. `jobpulse/cv_tailor.py:tailor_all_sections` runs four LLM calls
   through `ThreadPoolExecutor(max_workers=4)`. Single-tenant Ollama
   returns empty content for 2–3 of the four when fired in parallel
   (verified: 4 concurrent calls produced 2 empty + 2 valid).
   Workers should auto-scale to `1` when `is_local_llm()`.

Audit doc `cache-or-llm-audit.md §2.3` already covers Step 0 (install
non-reasoning model). These two are sibling Step-0 items uncovered by
S2's live run; recommend folding them into a `chore(setup)` commit
(separate from the `fix(cache-llm-S<n>):` chain) before S3.

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| ✅ S2 3de61d3 | `jobpulse/form_engine/field_mapper.py:322` | `map_fields` | **DETERMINISTIC** (verified) | `cognitive_llm_call` | `_FIELD_LABEL_TO_PROFILE_KEY` (static) + `form_experience.db._persist_label_mapping` (learned) | §2.2 #3 framing was **stale**: code at HEAD already does `_fill_by_element_ids → try_cached_mapping → seed_mapping → LLM` (dict-first). Live evidence (Anthropic Greenhouse, 2026-05-09): `DIRECT ID FILL: 2/6 fields set in single evaluate()` fired BEFORE `map_fields` LLM call, which then handled 6 residual labels not in any dict (Publications URL, Personal Preferences, Additional Information). 80 % coverage claim NOT met on this URL (actual 25 % = 2/8) — sparse 42-entry static dict + 73-row learned table is the real bottleneck, not ordering. Closed as no-code-fix verification; growing the dict / learned table per domain is the path to higher coverage (deferred to S8 final reconciliation). |
| 🟡 | `jobpulse/form_engine/field_mapper.py:614` | `recover_failed_fields_with_llm` | **NECESSARY** | `cognitive_llm_call` | — | Fires only when dict + learned + LLM round-1 all missed. Genuine fallback synthesis. |
| 🟡 | `jobpulse/form_engine/field_mapper.py:720` | `recover_failed_fields_with_vision` | **NECESSARY** | `responses.create` | — | Vision-based recovery; only fires on round-2 fail. **Direct OpenAI bypass** — verify if Ollama vision fallback exists. |
| 🟡 | `jobpulse/form_engine/field_mapper.py:804` | `vision_map_unlabeled_fields` | **NECESSARY** | `responses.create` | — | Vision OCR for fields with no a11y label; novel per page. **Direct OpenAI.** |
| 🟡 | `jobpulse/form_engine/field_mapper.py:896` | `review_form` | **CACHE-REPLACEABLE** | `responses.create` | `form_experience.db` keyed by `(domain, page_signature)` | Vision review of filled form before submit; same form layout will repeat. |
| 🟡 | `jobpulse/form_engine/intent_healing.py:78` | `_call_llm_for_selector` | **CACHE-REPLACEABLE** | `smart_llm_call` | `form_experience.db` keyed by `(domain, label, intent)` | Selector healing for failed widgets. Per-`(domain, label)` cache hits common. **Bypasses cognitive (S7).** |
| 🟡 | `jobpulse/form_engine/widget_llm_recovery.py:68` | `_call_llm_for_actions` | **NECESSARY** | `smart_llm_call` | — | Fires only on widget-fill failure. Per-page novel recovery actions. **Bypasses cognitive (S7).** |
| 🟡 | `jobpulse/form_engine/vision_gate.py:81` | `_call_vision_llm` | **NECESSARY** | `responses.create` | — | Vision gate firing when DOM classifier confidence is low. Genuine vision synthesis. **Direct OpenAI.** |
| 🟡 | `jobpulse/native_form_filler.py:724` | `NativeFormFiller._escalate_fill` | **NECESSARY** | `cognitive_llm_call` | — | Cognitive escalation per `CLAUDE.md` note — failed-field recovery. Already cognitive-routed. |
| 🟡 | `jobpulse/pre_submit_gate.py:114` | `PreSubmitGate.review` | **NECESSARY** | `cognitive_llm_call` | — | Per-form-state pre-submit review; rare and high-stakes. Already cognitive-routed. |
| 🟡 | `jobpulse/pre_submit_gate.py:213` | `PreSubmitGate._llm_field_judge` | **CACHE-REPLACEABLE** | `smart_llm_call` | `form_experience.db` keyed by `(domain, label, value)` | Per-`(label, value)` judge; deterministic given same inputs. **Bypasses cognitive (S7).** |

Sub-rows (one cluster, but extends beyond §6 S2's named files):

| Status | File:Line | Function | Class | Routing | Notes |
|---|---|---|---|---|---|
| 🟡 | `jobpulse/scan_learning.py:434` | `ScanLearningEngine.run_llm_analysis` | **NECESSARY** | `cognitive_llm_call` | Scan-loop LLM analysis per scan. Already cognitive-routed. Could cache by JD-pattern (deferred). Marked **S2-DEF** (deferred from S2). |

---

## §C — Cluster S3 · Screening alignment (CACHE-REPLACEABLE)

Target files per `cache-or-llm-audit.md §6`: `screening_answers.py`,
`screening_pipeline.py`. Acceptance: live URL with screening Qs, log shows
`cache hit, skipping LLM alignment`.

**§2.2 #4 audit-reliability check — second stale row found.** §2.2 #4
claims `_align_to_options` re-runs LLM on cache hits. **Verified false
at HEAD**: the function lives in `screening_semantic_cache.py`
(not `screening_answers.py`) and uses `OptionAligner.align_answer` —
fuzzy scoring only, **no LLM call**. When alignment fails, it returns
`None` so the caller falls through to LLM with options constraint
(intentional safety, not a bug). With S2's §2.2 #3 also stale, the
audit-reliability rate is **2 / 6 stale (33 %)** — S8 should re-verify
§2.2 #1, #2, #5, #6 before declaring done.

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| ✅ S3 5c5841e | `jobpulse/screening_pipeline.py:333` | `ScreeningPipeline._llm_answer` | **CACHE-REPLACEABLE** (verified) | `cognitive_llm_call` | `screening_semantic_cache.db` (already wired) — Qdrant + SQLite hybrid lookup at `_answer_single` short-circuits before LLM | §2.2 #4 framing **stale**: cache-hit path is silent return at `screening_pipeline.py:127` (source=`semantic_cache`), no LLM. S3 commit adds an explicit `screening_cache: hit on … — skipping LLM alignment` log line so the §6 acceptance evidence is observable. No code-path fix to the alignment itself. |
| ✅ S3 5c5841e | `jobpulse/screening_answers.py:421` | `_generate_hiring_message` | **CACHE-REPLACEABLE** (cache added) | `smart_llm_call` | NEW table `hiring_message_cache` in `applications.db` keyed by `(company_lower, role_archetype_lower)`, TTL = 30 days | Real cache add — first call generates + stores, repeat call on same `(company, role_archetype)` returns cached without firing LLM. `_classify_role_archetype` collapses trivial title variations (`Senior X`, `X II`, `Lead X` → same archetype). Unit test `tests/jobpulse/test_hiring_message_cache.py` (7 passing; all 7 fail without the change via stash-drill). Still uses `smart_llm_call` (S7 candidate for future routing through cognitive). |
| 🟡 S3-DEF | `jobpulse/screening_answers.py:887` | `_generate_answer` | **CACHE-REPLACEABLE** (already routed) | `chat.completions.create` | Cognitive engine L0 memory recall; `try_instant_answer` reads `applications.db:ats_answer_cache` upstream | §2.2 #4-sibling framing **partially stale**: this function ALREADY tries `_get_screening_engine()` first (cognitive route with L0 caching), then falls back to direct `chat.completions.create` only when cognitive engine is unavailable. Direct path is defensive, not the hot path. Closed without code change in S3; review during S7 (cognitive routing audit) for completeness. |
| 🟡 S3-EXT | `jobpulse/screening_decomposer.py:133` | `QuestionDecomposer._llm_decompose` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | NEW `question_decompose_cache` table keyed by `question_text_hash` | Real cache opportunity — same compound question phrasing repeats across companies. Deferred from S3 to keep cluster scope at ≤2 subsystems per `cache-or-llm-audit.md §7`. Schedule for a follow-up cluster session before S8. |
| 🟡 S3-EXT | `jobpulse/form_engine/field_mapper.py:556` | `_screen_questions_llm_batch` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | `applications.db:ats_answer_cache` (already wired upstream — confirmed via `JobDB.cache_answer` + `JobDB.get_cached_answer`) | Batch screening LLM is the legacy fallback after `try_screening_v2`. The cache writes already happen at line 605–607 (`JobDB.cache_answer`); reads happen earlier in `try_instant_answer` so this batch path only fires on cache miss + V2 miss. Deferred — needs measurement before adding more caching. |

---

## §D — Cluster S4 · CV tailoring (CACHE-REPLACEABLE)

Target files per `cache-or-llm-audit.md §6`: `cv_tailor.py`,
`content_hasher.py` (cache key generator — has no LLM call itself).
Acceptance: second run on same JD pulls cached bullets, no LLM call.

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| ✅ S4 | `jobpulse/cv_tailor.py:252` | `_call_with_correction` (called by `tailor_all_sections`) | **CACHE-REPLACEABLE** (cache added) | `cognitive_llm_call` | NEW table `tailored_cv_cache` in `applications.db` keyed by `(role_archetype, jd_hash, profile_version)`, TTL = 14 days | §2.2 #1 was correct: cv_tailor previously ran 4 LLM calls every JD with no cache. S4 wraps `tailor_all_sections` in a `(role_archetype, jd_hash, profile_version)` lookup that short-circuits all 4 LLM calls on hit. `_jd_hash` covers `(title, company, description, required, preferred)` order-independently for skills; `_profile_version_hash` covers `(experience, matched_projects)` so profile DB updates invalidate cache. Partial-failure CVs are NOT cached (check at `tailor_all_sections` end: `all((tagline, summary, experience, projects, cover_letter))`). `JOBPULSE_TEST_MODE=1` short-circuits the lookup/store with default `db=None` to prevent cross-test pollution. Live evidence deferred to S8 — unit test `tests/jobpulse/test_tailored_cv_cache.py` (10 passing; collection ERROR under stash-drill confirms regression catch). |
| 🟡 S4-DEF | `jobpulse/gate4_quality.py:254` | `scrutinize_cv_llm` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | NEW `gate4_cache` *or* extend `tailored_cv_cache` keyed by `(cv_hash, jd_hash)` | Phase B1/B2 LLM CV scrutiny; deterministic given same inputs. Deferred from S4 — keeps cluster scope at ≤2 subsystems per `cache-or-llm-audit.md §7`; the existing `tailored_cv_cache` already saves 4× LLM per JD, gate4 is one additional call. Schedule for a follow-up cluster session before S8. |

Extension rows (S4-EXT — same JD-hash key, different consumer):

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| 🟡 | `jobpulse/portfolio_variants.py:195` | `_generate_jd_aware_bullets` | **CACHE-REPLACEABLE** | `smart_llm_call` | NEW `portfolio_variants_cache` keyed by `(jd_hash, project_id)` | Bullets per `(jd, project)`; same combination repeats. **Bypasses cognitive (S7).** |
| 🟡 | `jobpulse/portfolio_variants.py:262` | `generate_portfolio_entry` | **CACHE-REPLACEABLE** | `smart_llm_call` | NEW `portfolio_variants_cache` keyed by `(jd_hash, project_id)` | Portfolio entry per `(jd, project)`; same combination repeats. **Bypasses cognitive (S7).** |

---

## §E — Cluster S5 · Cover letter (CACHE-REPLACEABLE)

Target file per `cache-or-llm-audit.md §6`: `cover_letter_agent.py`. The
actual LLM call lives in `cv_templates/generate_cover_letter.py:polish_points_llm`.
Acceptance: first run generates + caches, second run pulls from cache.

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| 🟡 | `jobpulse/cv_templates/generate_cover_letter.py:123` | `polish_points_llm` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | NEW table `cover_letter_cache` keyed by `(company, role_archetype, profile_version)` | §2.2 #2 — no cache; regenerates per company every time. |

---

## §F — Cluster S6 · Page reasoner (CACHE-REPLACEABLE)

Target file per `cache-or-llm-audit.md §6`: `page_analysis/page_reasoner.py`.
Acceptance: navigation across pages doesn't re-call LLM if signature
unchanged.

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| 🟡 | `jobpulse/page_analysis/page_reasoner.py:516` | `PageReasoner._call_llm` | **CACHE-REPLACEABLE** | `smart_llm_call` | `form_experience.db` *or* new `page_reasoner_cache` keyed by `(domain, page_signature)` | §2.2 #5 — partial caching; need per-`(domain, page_signature)` cache. **Bypasses cognitive (S7).** |
| 🟡 | `jobpulse/page_analyzer.py:45` | `_vision_detect` | **CACHE-REPLACEABLE** | `chat.completions.create` | `form_experience.db` keyed by `(domain, dom_hash)` | Older page-analyzer; vision detect of page type. **Direct OpenAI bypass.** |
| 🟡 | `jobpulse/vision_tier.py:41` | `analyze_field_screenshot` | **NECESSARY** | `responses.create` | — | Vision recovery per failed field; novel per attempt. **Direct OpenAI.** |
| 🟡 | `jobpulse/vision_tier.py:117` | `classify_page_type_from_screenshot` | **CACHE-REPLACEABLE** | `responses.create` | `form_experience.db` keyed by `(domain, screenshot_hash)` | Page-type classification from screenshot; same domain layout repeats. **Direct OpenAI.** |

Extension row (S6-EXT — page rescue agent):

| Status | File:Line | Function | Class | Routing | Cache key / DB source | Notes |
|---|---|---|---|---|---|---|
| 🟡 | `shared/execution/_rescue.py:39` | `RescueAgent._llm_analyze_page` | **NECESSARY** | `smart_llm_call` | — | Rescue agent for stuck pages; rare per-incident synthesis. **Bypasses cognitive (S7).** |

---

## §G — Cluster S7 · Cognitive bypasses

Target per `cache-or-llm-audit.md §6`: files that call LLM directly,
bypassing `cognitive/think()`. Migrate to cognitive engine entry point.
Acceptance: all-cluster log shows L0 Memory Recall hits before any LLM call.

The complete S7 picture is the union of every row above with routing ≠
`cognitive_llm_call` (`smart_llm_call`, `chat.completions.create`,
`responses.create`, `llm.invoke`). Listed here are S7-only sites — i.e.,
sites *not* already targeted by S2–S6 fixes.

| Status | File:Line | Function | Class | Routing | Notes |
|---|---|---|---|---|---|
| 🟡 | `jobpulse/swarm_dispatcher.py:600` | `_llm_judge_score` | **NECESSARY** | `cognitive_llm_call` | Per-task scoring of swarm-dispatched answers. Already cognitive-routed; included for review only. |
| 🟡 | `jobpulse/strategy_reflector.py:182` | `reflect_with_llm` | **NECESSARY** | `smart_llm_call` | Strategy reflection on past trajectories; synthesis. **Bypasses cognitive.** |
| 🟡 | `jobpulse/notion_agent.py:305` | `suggest_subtasks` | **NECESSARY** | `cognitive_llm_call` | Subtask suggestions per Notion task; novel synthesis. Already cognitive-routed. |
| 🟡 | `jobpulse/command_router.py:388` | `classify_llm` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | Telegram command classification fallback. Already cognitive-routed; consider `intent_classification_cache`. |
| 🟡 | `jobpulse/email_preclassifier.py:467` | `extract_patterns_from_email` | **CACHE-REPLACEABLE** | `cognitive_llm_call` | `email_patterns_cache (sender_domain, body_hash)`. Already cognitive-routed. |
| 🟡 | `jobpulse/gmail_agent.py:102` | `_classify_email` | **CACHE-REPLACEABLE** | `smart_llm_call` | Gmail recruiter classifier fallback. **Bypasses cognitive.** |

---

## §H — S0 reference: gold-standard pattern

| Status | File:Line | Function | Class | Routing | Notes |
|---|---|---|---|---|---|
| ⭐ S0-REF | `jobpulse/skill_extractor.py:339` | `_extract_skills_llm` | **NECESSARY** | `cognitive_llm_call` | §2.2 #6 — runs deterministic rule-based extraction first, escalates to LLM only on miss. **Pattern to copy across the rest of the codebase.** No fix needed. |

---

## §I — Out of scope for §6 cluster sequencing

`cache-or-llm-audit.md §1` says "every LLM call site in JobPulse," but §6's
cluster sequencing (S2–S8) targets only the *apply pipeline*. The rows
below are LLM call sites in `jobpulse/` and `shared/` but outside the
cluster sequencing — they are listed for completeness and to satisfy the
hash check in §J. Future audits (separate from this one) can address them.

### §I.1 — Papers / arXiv / blog pipeline (8 sites)

| File:Line | Function | Routing | Why out of scope |
|---|---|---|---|
| `jobpulse/arxiv_agent.py:208` | `llm_rank_broad` | `cognitive_llm_call` | Papers pipeline; not in §6 sequencing. |
| `jobpulse/arxiv_agent.py:390` | `summarize_paper` | `cognitive_llm_call` | Papers pipeline. |
| `jobpulse/blog_generator.py:17` | `_llm_call` | `cognitive_llm_call` | Papers / blog pipeline. |
| `jobpulse/papers/blog_pipeline.py:16` | `_llm_call` | `cognitive_llm_call` | Papers blog pipeline. |
| `jobpulse/papers/chart_generator.py:56` | `ChartGenerator._extract_chart_data` | `cognitive_llm_call` | Papers chart generator. |
| `jobpulse/papers/ranker.py:138` | `llm_rank` | `cognitive_llm_call` | Papers ranker. |
| `jobpulse/papers/ranker.py:230` | `extract_themes` | `cognitive_llm_call` | Papers ranker. |
| `jobpulse/papers/ranker.py:271` | `_summarize_paper` | `cognitive_llm_call` | Papers ranker. |

### §I.2 — Patterns / orchestration sibling subsystem (12 sites)

These belong to the orchestration patterns (multi_agent_patterns) sibling
of JobPulse, not the apply pipeline. Re-audit separately.

| File:Line | Function | Routing |
|---|---|---|
| `shared/agents.py:413` | `researcher_node` | `smart_llm_call` |
| `shared/agents.py:462` | `writer_node` | `smart_llm_call` |
| `shared/agents.py:529` | `reviewer_node` | `smart_llm_call` |
| `shared/dynamic_agent_factory.py:212` | `TaskComplexityAnalyzer.analyze` | `self.llm.invoke` |
| `shared/dynamic_agent_factory.py:408` | `DynamicAgentFactory.create_custom_agent` | `self.llm.invoke` |
| `shared/experiential_learning.py:375` | `TrainingFreeGRPO._extract_semantic_advantage` | `self.llm.invoke` |
| `shared/fact_checker.py:161` | `extract_claims` | `chat.completions.create` |
| `shared/fact_checker.py:202` | `verify_claims` | `chat.completions.create` |
| `shared/persona_evolution.py:193` | `PersonaEvolver._search_for_expertise` | `self.llm.invoke` |
| `shared/persona_evolution.py:251` | `PersonaEvolver._synthesise_persona` | `self.llm.invoke` |
| `shared/persona_evolution.py:300` | `PersonaEvolver._compress_persona` | `self.llm.invoke` |
| `shared/prompt_optimizer.py:216` | `PromptOptimizer._optimize_with_meta` | `self.llm.invoke` |

### §I.3 — Telegram conversational + budget + persona evolution (4 sites)

Different agent surfaces (Telegram chat, budget classifier, persona-evo).

| File:Line | Function | Routing |
|---|---|---|
| `jobpulse/conversation.py:89` | `chat` | `chat.completions.create` |
| `jobpulse/budget_nlp.py:17` | `classify_transaction` | `chat.completions.create` |
| `jobpulse/persona_evolution.py:81` | `_quick_evolve` | `chat.completions.create` |
| `jobpulse/persona_evolution.py:193` | `_deep_optimize.evaluator` | `chat.completions.create` |

### §I.4 — Audio transcription (Whisper)

`jobpulse/voice_handler.py` calls `client.audio.transcriptions.create` —
this is the Whisper STT API, not a chat-completion LLM. Not a target of
this audit. (No row in the catalog because the corrected grep doesn't pick
it up; mentioned here so a future reader doesn't re-flag it.)

---

## §J — Infrastructure (LLM factories, wrappers, cognitive plumbing)

Listed for completeness. These route other call sites; they are not
themselves cache-or-LLM fix targets.

### §J.1 — Factories + direct paths in `shared/agents.py`

| File:Line | Function | Routing | Role |
|---|---|---|---|
| `shared/agents.py:196` | `_make_openai_llm` | `ChatOpenAI` | Factory: returns `ChatOpenAI` bound to OpenAI endpoint. |
| `shared/agents.py:208` | `_make_local_llm` | `ChatOpenAI` | Factory: returns `ChatOpenAI` bound to Ollama endpoint. |
| `shared/agents.py:311` | `_InstrumentedLLM.invoke` | `self._llm.invoke` | Cost-tracking instrumentation wrapper. |
| `shared/agents.py:864` | `_direct_llm_call` | `chat.completions.create` | `cognitive_llm_call`'s fallback when CognitiveEngine fails or `response_format` is set. |

### §J.2 — Wrappers / fallbacks

| File:Line | Function | Routing | Role |
|---|---|---|---|
| `jobpulse/utils/safe_io.py:22` | `safe_openai_call` | `chat.completions.create` | Timeout + None-safety wrapper around chat completions. |
| `shared/llm_fallback.py:48` | `FallbackLLM._call_openai` | `chat.completions.create` | Multi-provider fallback (OpenAI). |
| `shared/llm_retry.py:180` | `resilient_llm_call` | `llm.invoke` | Retry wrapper around bound LLM `.invoke`. |
| `shared/parallel_executor.py:122` | `parallel_grpo_candidates` | `llm.invoke` | Parallel GRPO executor. |

### §J.3 — Cognitive engine internals (`shared/cognitive/`)

| File:Line | Function | Routing | Role |
|---|---|---|---|
| `shared/cognitive/_engine.py:21` | `_llm_generate` | `smart_llm_call` | L1 single-shot generator. |
| `shared/cognitive/_reflexion.py:15` | `_llm_generate` | `smart_llm_call` | L2 reflexion generator. |
| `shared/cognitive/_tree_of_thought.py:15` | `_llm_generate` | `smart_llm_call` | L3 tree-of-thought generator. |
| `shared/cognitive/_tree_of_thought.py:82` | `TreeOfThought._generate_branches_via_grpo.generate_one` | `llm.invoke` | Per-branch ToT generator. |

---

## §K — Distribution

### By cluster

| Cluster | Sites |
|---|---|
| S2 (field-mapping) | 11 |
| S2-DEF (deferred from S2) | 1 |
| S3 (screening) | 5 |
| S4 (CV tailoring) | 2 |
| S4-EXT (portfolio variants, same JD-hash key) | 2 |
| S5 (cover letter) | 1 |
| S6 (page reasoner) | 4 |
| S6-EXT (page rescue) | 1 |
| S7 (cognitive bypasses, not already in S2–S6) | 6 |
| S0-REF (gold-standard reference) | 1 |
| 🚫 OUT-PAPERS | 8 |
| 🚫 OUT-PATTERNS | 12 |
| 🚫 OUT-CONV/BUDG/PERS | 4 |
| 🛠️ INFRA | 8 |
| 🛠️ INFRA-COG | 4 |
| **Total** | **70** |

### By classification (apply-pipeline rows: clusters S2–S7, S0-REF, S2-DEF, S4-EXT, S6-EXT)

| Classification | Sites |
|---|---|
| **CACHE-REPLACEABLE** | 18 |
| **NECESSARY** | 15 |
| **DETERMINISTIC** | 1 |
| **Total apply-pipeline** | **34** |

### By routing (all 70 sites)

| Routing | Count | % |
|---|---|---|
| `cognitive_llm_call` | 24 | 34 % |
| `smart_llm_call` | 16 | 23 % |
| `chat.completions.create` | 11 | 16 % |
| `self.llm.invoke` | 7 | 10 % |
| `responses.create` | 6 | 9 % |
| `llm.invoke` | 3 | 4 % |
| `ChatOpenAI` | 2 | 3 % |
| `self._llm.invoke` | 1 | 1 % |
| **Cognitive-routed** (✅) | **24** | **34 %** |
| **Bypasses cognitive** (S7 candidates) | **46** | **66 %** |

---

## §L — Sibling hash check (S1 acceptance)

Per `cache-or-llm-audit.md §6`, S1's acceptance criterion is *"sibling hash
check that the catalog covers every grep hit."*

The corrected grep (§A) is the source of truth. Re-running it at the
S1 catalog commit `93c1987` (parent `85fdd45` — no `.py` files changed
in S1, so both snapshots produce the same numbers) yields:

```bash
$ grep -rln "cognitive_llm_call\|smart_llm_call\|chat\.completions\.create\
\|chat\.completions\.acreate\|responses\.create\|ChatOpenAI(\|get_llm()\
\|get_openai_client()" --include="*.py" jobpulse/ shared/ \
  | grep -v __pycache__ | grep -v worktrees | sort | wc -l
44

$ # Plus 5 files reachable only via .invoke() on bound LLM objects:
$ # shared/dynamic_agent_factory.py, shared/experiential_learning.py,
$ # shared/persona_evolution.py, shared/prompt_optimizer.py,
$ # shared/parallel_executor.py
$ echo $((44 + 5))
49

$ # AST-based per-(file, function) extractor (committed alongside this catalog):
$ python3 docs/audits/_tools/extract_llm_call_sites.py \
    $(cat /tmp/grep_recheck_files.txt) \
    shared/dynamic_agent_factory.py \
    shared/experiential_learning.py \
    shared/persona_evolution.py \
    shared/prompt_optimizer.py \
    shared/parallel_executor.py \
  | tail -n +2 | wc -l
70
```

| Source | Count |
|---|---|
| Files reached by corrected grep | 44 |
| Additional files reached via `llm.invoke` only | 5 |
| **Total files** | **49** |
| **Unique `(file, function)` call sites** (catalog rows) | **70** |
| Catalog rows in §B–§J above | **70** |

`70 == 70` → coverage hash check passes.

---

## §M — How later sessions update this catalog

Per `cache-or-llm-audit.md §4.3` step 8:

> Mark progress in `docs/audits/cache-llm-catalog.md` — `🟡 →` change to
> `✅ S<n> <commit-hash>`.

Sessions S2–S8 each update the relevant rows in §B–§G when their fix
ships, replacing the 🟡 cell with `✅ S<n> <hash>` after the
backfill commit (`docs(...)` follow-up). When every cluster row is ✅, the
S8 final-reconciliation session writes
`docs/audits/cache-llm-completion-report.md` per `cache-or-llm-audit.md §12`.
