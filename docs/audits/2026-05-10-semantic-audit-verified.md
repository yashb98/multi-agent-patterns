# Semantic-Analysis Audit — Verified Cross-ATS

**Date**: 2026-05-10
**Branch**: `pipeline-correctness-fixes` @ `765cf23`
**Goal**: Every semantic decision the JobPulse pipeline makes is correct for the candidate's profile and the JD's context, on every live apply across every active ATS adapter, with no hardcoded fallbacks.
**Scope**: live-e2e Anthropic Greenhouse mining + targeted cross-ATS runs.
**Constraints honoured**: read-only on `*.py`, no commits, no submits, dry-run only, OPRAL on every error, four-question correctness check on every PASS.

## Pre-flight (all preconditions PASS at audit start)

| # | Check | Result |
|---|---|---|
| 1 | `git status --short` empty | PASS — clean tree at `765cf23` |
| 2 | `live-e2e-2026-05-10.md` Confidence 100% | PASS |
| 3 | `KimiAI_API_KEY` set | PASS |
| 4 | Chrome CDP up | PASS — `lsof -ti:9222` → `22611` |
| 5 | BGE-M3 1024-dim reachable | PASS — `len(embedding) == 1024` |
| ✓ | `code_intelligence` reindex (cos vs fresh BGE-M3) | PASS — `cos = 1.0000` (was Voyage @ ≈0.018) |

> **Caveat carried forward**: MCP `code-intelligence` server disconnected mid-session. OPRAL traces in this audit use `grep` + `Read` instead of `find_symbol`/`callers_of`/`impact_analysis`. Slower, but the substantive trace work is identical.

> **Operational notes (full transparency)**:
> 1. **Cleared stale apply locks** before live runs: `data/locks/jobpulse_apply.lock` (PID 33975, 14:20-era from previous session, `ps -p` confirmed dead) and `data/locks/jobpulse_fill_submit.lock` (14:13-era, same era). Multi-bot daemon lock at PID 65914 left in place (active). Investigated before clearing — no live process held either.
> 2. **Ashby OpenAI geographic coding not directly verified** — pre-screen rejected on Gate 2 before the JD location reached the listing object. The matrix's "US-coded" annotation for OpenAI is plausible (San Francisco HQ) but not confirmed live this session.
> 3. **LLM-as-judge methodology gap (acknowledged, not closed)**: Audit prompt rule 1 specifies LLM-as-judge via `cognitive_llm_call(domain="audit_correctness_check", stakes="high")` for output-quality dimensions. This audit applied LLM-as-judge to **none** of the OK / OK-graceful entries (TP-2, TP-4, TP-5, TP-9, TP-16). They are PASS *for mechanism* — value-content correctness is **UNVERIFIED pending LLM-as-judge**. A follow-up Slice-V should run the judge across all OK entries and either confirm or demote them.

## Prior work referenced (not duplicated)

- `docs/superpowers/specs/2026-04-30-semantic-analysis-overhaul-design.md` — 11-component embedding-first restructure (foundation shipped, ~70% of components migrated).
- `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md` — 8 regex-heavy files in flight.
- `docs/audits/2026-05-10-semantic-analysis-pipeline-audit.md` — 43 touchpoints classified by mechanism (mostly UNVERIFIED).
- `docs/audits/2026-05-10-llm-prompt-context-audit.md` — 35 LLM call sites (4 audited deeply).
- `docs/audits/live-e2e-2026-05-10.md` — Anthropic Greenhouse run, Confidence 100%. **Mined here as primary evidence base for SG3-Greenhouse and parts of SG4.**

## URL matrix used

`docs/audits/url-coverage-matrix.md` — 26 URLs, 11 adapters. **Traversal order taken** (per advisor sequencing under 4hr cap):

1. **Greenhouse / Anthropic** (mined from live-e2e, no re-run) — UK-coded JD.
2. *Lever / Palantir* (US-coded JD, target sub-goal-1 cross-context) — see status below.
3. Adapters 3-N — see continuation plan.

---

## Per-sub-goal current distance (final, end-of-session)

Methodology: a touchpoint **advances** a sub-goal when the four-question correctness check lands on PASS for at least one live URL. UNVERIFIED is a *real finding*, not a TODO. **GAP found cross-ATS** explicitly *advances* an audit (the audit's value is in the gaps, not the OKs).

| SG | Statement | Distance covered this session | Distance remaining |
|---|---|---|---|
| **1** | Right value for context (`f(profile, JD, page, learned)`) | **~15%** — CV/CL caches verified profile+JD-aware (TP-4, TP-5). Screening cache key + global field-mapping bleed (TP-1, TP-15) source-confirmed P1 GAPs. TP-19 (visa-state stale cache served `Tier 4` instead of `Graduate Visa` for the same profile) is the canonical *symptom* of the gap, observed live. | Full UK+US comparison deferred to Phase 2C; ~10 other value-producing decisions UNVERIFIED. |
| **2** | Right mechanism (semantic-first; regex as fallback) | **~30%** — Anthropic run + Graphcore run show screening intent classifier (620 prototypes), screening cache (Qdrant), field_mapper, page_reasoner all routing through embedding/LLM tiers. But: TP-3 (page reasoner JSON fragility), TP-11 (hardcoded LinkedIn CSS in process_single_url), TP-17 (BGE-M3 silent MiniLM fallback live-observed), TP-21 (vision recovery 404 on Kimi). | Multiple slices land mechanism violations; cross-ATS verification. |
| **3** | Right across every ATS | **~9% (1 of 11 adapters)** — Greenhouse exercised on 2 URLs (Anthropic mined + Graphcore live full). 0 of the *other* 10 adapters validated; Lever + Ashby blocked at pre-screen (TP-11 root cause is **why** S6 is load-bearing). | 10 adapters un-validated; S6 + S10 must land before non-Greenhouse URLs can reach form-fill. |
| **4** | Right per real run | **~25%** — 4 URLs run live this session (3 fully + 1 mined). Four-question check applied per touchpoint. TP-19 (right-to-work double-fill) and TP-7/TP-18 (option aligner gaps) are pure live-evidence findings impossible to discover from static analysis. | 9 adapters need live runs; H1 (semantic_decisions.db) absence forces log-mining for everything. |
| **5** | OPRAL on errors | **~35%** — Audit-side OPRAL discipline preserved: every error → traced → slice → no fixes attempted. 4 *new* error traces this session (TP-3, TP-17, TP-19, TP-21) all become their own slices (no bundling). | Phase 2 prosecutes per-slice in dedicated branches. |

**Composite distance ≈ 24-30%** (weighted toward SG2/SG3 as the driving constraints). <100% = goal not met. Continuation plan at `docs/superpowers/plans/2026-05-10-semantic-audit-phase2-continuation.md`.

---

## Per-touchpoint entries (Greenhouse / Anthropic + Graphcore evidence)

> Conventions: every entry has the six SKILL fields. Where the live-e2e doc claimed PASS *mechanically* (row written / log line present), I re-ran the four-question check; UNVERIFIED is the audit's value-add when correctness can't be cross-checked from artefacts alone.
>
> **Numbering note**: TP-1 through TP-13 were drafted in the live-e2e mining phase. TP-15 through TP-22 are the cross-ATS findings discovered during Lever / Ashby / Graphcore live runs. TP-23 (formerly TP-14, intent=unknown on cache hits) was renumbered to the end so the cross-ATS findings cluster together; reader sees TP-1...TP-13 then TP-15...TP-22 then TP-23 by design. The TP-14 number is unused.

### TP-1 ScreeningSemanticCache lookup key (`jobpulse/screening_semantic_cache.py:33,222,324`)

- **Current**: cache key = `int(hashlib.md5(question.strip().lower()).hexdigest(), 16) % (2**63)`. Lookup vector = `embedder.embed(question.strip())`. The payload includes `job_context_hash` but the lookup *never consults it for matching* — only `field_options` is used to filter incompatible answers. No `profile_state_hash`.
- **Target**: cache key = `(question_canonical, profile_state_hash, jd_context_hash)`. Lookup must produce different cache entries for the same question across UK / US / different visa state / different role-level JDs. Per `dimensions.md → D9 / D10 / F3`.
- **Status**: **GAP**.
- **Priority**: **P1** — wrong screening answer = wrong application. Visa, salary, location-relocation are the canonical examples in the decision-context table.
- **Verify by**: source read above + `run_final` log lines `screening_cache: hit on 'Will you now or will you in the future require employment vi…' (score=1.00, intent=unknown, option_aligned=False)` — the cache hit at `score=1.00` proves question-text-only matching, since the same hit would fire for a US JD with the identical question.
- **Correctness check**:
  - Right input? **NO** — only the question text reaches the cache key. Profile state and JD context never enter.
  - Right mechanism? Embedding-first, fits SG2 — but the embedding is over too narrow a context.
  - Right output for THIS context? Anthropic UK + Graduate Visa → "No" was correct. **But the same cache row would serve the wrong answer to a US apply tomorrow.** Sub-goal-1 violation by construction.
  - Right downstream consumption? Yes — `screening_pipeline._finalise` consumed the cached "No" and the form filler wrote it. Mechanically correct, semantically dangerous.
- **Dimension matrix**:
  - `D9` (profile+JD context drives decision) — **FAIL** (cache key blind).
  - `D10` (profile-state changes invalidate cache) — **FAIL** (no profile_state_hash to invalidate against).
  - `F3` (cache key includes profile + JD hashes) — **FAIL** (only question text + payload-side `job_context_hash` that lookup ignores).
  - `F4` (invalidation on logic change) — UNVERIFIED.
  - `F5` (hit-rate monitored) — PASS via `db_observability.lookups`.

### TP-2 ScreeningPipeline LLM fallback (`jobpulse/screening_pipeline.py:417`)

- **Current**: `cognitive_llm_call(domain="screening_answers", stakes="high", task=flattened_string)`. `OptionAligner.align_answer(...)` validates output against `field_options` for option fields. **Post-S13**: free-text branch now also gates the answer through a BGE-M3 cosine-similarity check against the question (`_LLM_ANSWER_RELEVANCE_THRESHOLD = 0.40`); off-topic answers (e.g. orchestration-text leaks) are treated as miss and the caller falls through. Threshold derived from measured (Q, correct-answer) vs (Q, leak) pairs (S13 evidence).
- **Target**: as-is for the strong-O2 alignment; tighten C2 (bound `profile_summary`) and W2 (proper SystemMessage).
- **Status**: **OK (graceful)** — the prompt-audit doc already classified this as OK with W2/C2 weaknesses. **My re-check confirms.** Free-text leak guard added in S13.
- **Priority**: P1.
- **Verify by**: `run_final` log lines show `screening_cache` hits skipping LLM ⇒ the LLM fallback only fires on cache miss, as designed. When it does fire, the alignment validation is what saves us when the cache doesn't. **S13 live evidence** (`logs/audit/s13_live_evidence.log`) shows the free-text path now rejects orchestration leaks pre-cache-write.
- **Correctness check**:
  - Right input? PARTIAL — `profile_summary` is unbounded (C2 weak). On a long profile, truncation risk falls on token budget rather than explicit `[:1500]`. Caller-dependent.
  - Right mechanism? PASS — LLM tier with cached intent + option alignment + S13 free-text JD-relevance guard.
  - Right output for THIS context? PASS for visa/relocation/Hispanic-Latino/Veteran/Disability on Anthropic. Cross-checked against the live-e2e fill-readback values.
  - Right downstream consumption? PASS — `screening_outcome: {confirmed: 17, corrected: 0}`.
- **Prompt audit (LLM)**: W1 ✓, **W2 DEGRADED** (flattens to `f"SYSTEM:\n…\nUSER:\n…"`), C1 ✓ (profile_summary, options, field_type, anti-AI-leak guard), **C2 NOT BOUNDED**, O1 prompt-instructed schema (no `response_format`), **O2 STRONG** post-S13 (OptionAligner + `opts_lower` for option fields, BGE-M3 cosine ≥ 0.40 to question for free-text), R1 upstream cache (TP-1), R2 fallback to `None` on exception.

### TP-25 Cognitive routing context leak — `cognitive_llm_call` returns cross-domain procedural template (`shared/memory_layer/_stores.py:393` + `jobpulse/screening_pipeline.py:415`)

> **Status update — LIVE VERIFIED**: Slice S13 landed on branch `audit-slice-s13-cognitive-leak`. Closes the upstream root cause that TP-1 documented as "❌ Answer content FAIL pending S13" — TP-1 is now end-to-end PASS at the LLM-tier content level (S1 had already closed the keying level).

- **Root cause** (traced by direct reproduction on `pipeline-correctness-fixes` HEAD):
  1. `patterns/enhanced_swarm.py:411-420` writes `learn_procedure(domain="writing", strategy="Enhanced swarm convergence: GRPO group sampling. Score 8.5/10 at iteration 1. Round 1/3 — still needs: …", source="enhanced_swarm")` after every high-scoring writing-pattern run. `times_used=3, success_rate=1.0, avg_score_when_used=8.5`.
  2. `shared/memory_layer/_stores.py:393-406` `ProceduralMemory.recall(domain)` — when no procedure matched the requested domain, fell back to **all** procedures across every domain: `if not relevant: relevant = self.procedures`. A cognitive call for `domain="screening_answers"` (which has zero in-domain procedures in production) therefore got the highest-scoring orchestration template surfaced as a "best procedure".
  3. `shared/cognitive/_strategy.py:58-94` `StrategyComposer.compose` ranked the bled cross-domain entry into `selected[]`, populated `composed.templates_used`, and injected `## Learned Strategies\n- Enhanced swarm convergence: GRPO group sampling...` into the prompt sent to the LLM.
  4. `shared/cognitive/_engine.py:266-284` `_execute_l0` returned the strategy text **verbatim** as `result.answer` whenever the classifier picked L0_MEMORY (which it does at high stakes when the cross-domain template's high `success_rate × avg_score` rank surfaced it as a "strong" template). On L1 escalation the LLM also echoed the leaked strategy text it saw in the prompt, producing the same bad answer with extra cost.
  5. The screening pipeline's free-text branch had no guard (option-field branch already had OptionAligner — S1's narrow mitigation only covered options), so the leak landed in `screening_semantic_cache.db` at `score=1.00` and would have served on every subsequent matching apply. Five legacy entries from prior sessions were cleaned in the S1 pre-flight; without S13, more would accrue.

- **Live reproduction (pre-fix HEAD)**:
  ```
  cognitive_llm_call(
      task='SYSTEM: ... USER: Will you require visa sponsorship?',
      domain='screening_answers', stakes='high',
  )
  → 'Enhanced swarm convergence: GRPO group sampling. Score 8.5/10
     at iteration 1. Round 1/3 — still needs: accuracy 0.0/9.5
     (not checked)'
  ```

- **Fix scope** (surgical, two files):
  1. **Root cause** — `shared/memory_layer/_stores.py` `ProceduralMemory.recall`: removed the `if not relevant: relevant = self.procedures` fallback. Returns `[]` when no in-domain procedure matches. Comment cites S13. 2-line diff.
  2. **Defense in depth** — `jobpulse/screening_pipeline.py` `_llm_answer` free-text branch: BGE-M3 cosine similarity check between question and answer. If similarity < `_LLM_ANSWER_RELEVANCE_THRESHOLD` (0.40), treat as miss (return None) so neither the answer nor the cache write occurs. Threshold derived from measured Q/A pairs (on-topic prose 0.55–0.81; off-topic orchestration 0.27–0.50). Wrapped in try/except so a BGE-M3 outage degrades to "accept answer", not "crash the apply".

- **Tests** (TDD red → green, 11 total):
  - `tests/shared/memory_layer/test_procedural_recall_domain_isolation.py` (5 tests): `recall` returns `[]` for unknown domain; doesn't return writing strategies for screening; in-domain still works; `format_for_prompt` empty when no in-domain entries; `MemoryManager.get_procedural_entries` JSON fallback respects domain isolation.
  - `tests/jobpulse/test_screening_llm_jd_relevance.py` (6 tests): rejects Enhanced-swarm leak; rejects optimization-success-streak leak; rejected answer doesn't poison `screening_semantic_cache`; on-topic visa/motivation answers pass; direct `cognitive_llm_call(domain='screening_answers')` at L0_MEMORY does not return cross-domain strategy.

- **Live evidence** (`scripts/audit_s13_live_evidence.py` → `logs/audit/s13_live_evidence.log`):
  ```
  --- 1. Direct cognitive_llm_call(domain='screening_answers') ---
    result.is_none=True leaked=False answer_prefix=''
  --- 2. ScreeningPipeline.answer end-to-end ---
    [OK] q='Will you now or in the future require employment visa s'
         src=semantic_cache conf=0.91 ans='No'
    [OK] q='Why do you want to work at this company?'
         src=no_answer conf=0.0 ans=''
    [OK] q='How did you hear about this position?'
         src=semantic_cache conf=0.95 ans='LinkedIn'
  --- 3. Post-run cleanup (cache hygiene) ---
    pre=0 post=0 rows removed
  === S13 PASS ===
  ```

- **Status**: **PASS / closed at the leak surface**, with two honest caveats below.
- **Priority**: was P0 (TP-1 unblock); closed.
- **Verify by**: live evidence quoted above + 11/11 new tests green.

**Honest caveats** (binding rule 5 — no symptom suppression):

1. **`tests/jobpulse/test_screening_pipeline_real.py::TestEdgeCases::test_very_long_label` regresses on S13 due to TP-17 fragility surfacing under additional BGE-M3 load.** Measured: passes in 9.77s on `pipeline-correctness-fixes` HEAD (stashed), fails in ~30s on `audit-slice-s13-cognitive-leak` HEAD. The crash is in `screening_intent.py:347` (intent classifier embed call) **before** any S13 code runs — `ValueError: shapes (4,1024) and (384,) not aligned: 1024 (dim 1) != 384 (dim 0)` — i.e. BGE-M3 returns HTTP 500 → silent MiniLM 384-dim fallback → mismatch with 1024-dim prototype matrix. S13 doesn't *change* the underlying defect (TP-17 / S10 BGE-M3 loud-fail) but the JD-relevance check adds 1–2 BGE-M3 calls per LLM-fallback invocation, and on the 5-sub-question decomposition path that's enough additional load to flip Ollama into 500-mode and reliably surface the latent fragility. Resolution depends on **S10**; do not paper this over by reducing S13's BGE-M3 footprint behind a flag — that would mask the underlying defect rather than fix it.

2. **`apply_job(url, dry_run=True)` end-to-end LLM-fallback verification is deferred.** The S13 evidence script's pipeline-mode call returned `source=semantic_cache` for two questions (clean cache hits from prior runs) and `source=no_answer` for the motivation question because Kimi's `moonshot-v1-auto` model is currently 404 from the local Ollama proxy — *outside S13's scope* but it means the LLM-fallback path with the new JD-relevance guard wasn't actually exercised on a fresh-uncached question end-to-end. The unit-tested guard, the direct cognitive call (which now returns `is_none=True` instead of the leak text), and the cache-hygiene check (zero leak rows post-run) collectively close the *root cause*; the deferred check is "does the new guard catch a real LLM hallucination on a real Anthropic Greenhouse apply when LLM-fallback fires for an uncached free-text question." That verification re-runs once the Kimi proxy is back. The unit-level rejected-leak tests (`test_rejects_enhanced_swarm_orchestration_leak` etc.) cover the guard's contract; the deferred run would only confirm that contract holds against a live LLM provider's actual outputs.
- **Correctness check**:
  - Right input? PASS — domain isolation contract is now enforced at the only place producers and consumers meet (`recall(domain)`); cross-domain templates can no longer cross the boundary.
  - Right mechanism? PASS — surgical fix at the bug location (procedural recall) plus a defense-in-depth backstop (JD-relevance) for any other source of off-topic LLM output.
  - Right output for THIS context? PASS — `result.is_none=True` with no leak text in the direct cognitive call; pipeline answers either come from the (clean) semantic cache or fall through to `no_answer` cleanly.
  - Right downstream consumption? PASS — cache hygiene check shows zero leak rows pre/post; `record_outcome` does not fire on rejected answers (test 3).
- **Prompt audit (E1–E10)** for `cognitive_llm_call(domain="screening_answers")` and `screening_pipeline._llm_answer` free-text branch (S13 closure of the prompt-context audit doc's "31 remaining sites" item for these two call sites):
  - **E1 Wrapper / W1**: PASS — `cognitive_llm_call`.
  - **E2 Messages / W2**: DEGRADED (unchanged from pre-S13) — flattens to `f"SYSTEM:\n…\nUSER:\n…"`. Documented in TP-2; S13 didn't address this. Slice P1 from the prompt audit remains valid.
  - **E3 Context payload / C1**: PASS — profile_summary, options, field_type, anti-AI-leak guard. Includes JD via job_context.
  - **E4 Truncation / C2**: NOT BOUNDED (unchanged). Slice P2 from prompt audit remains valid.
  - **E5 Schema / O1**: prompt-instructed.
  - **E6 Validation / O2**: STRONG — OptionAligner for option fields (existing) + BGE-M3 JD-relevance gate for free-text (S13 new). Both produce a fall-through-as-miss on rejection.
  - **E7 Cache / R1**: upstream `screening_semantic_cache` keyed on (question, profile_state_hash, jd_context_hash) post-S1; S13 confirms cache writes are gated by the new validation so leak text cannot poison.
  - **E8 Cost+fallback / R2**: `domain="screening_answers"`, `stakes="high"`. On exception → returns None. `agent_name` not set (cost recorded under domain bucket; consistent with pre-S13).
  - **E9 Few-shot**: N/A — no exemplars used.
  - **E10 System role**: weak (E2/W2 degradation point; same fix path).
- **Dimension matrix**:
  - `D8` (mechanism: embedding/LLM/semantic_matcher primary) — **PASS** post-fix; the procedural-recall mechanism is now strictly domain-scoped, so cross-domain bleeding through the L0 path is eliminated by construction.
  - `D9` (profile+JD context drives decision) — PASS at the LLM tier (cached question embed + answer embed scoped to current call).
  - `D10` (profile-state changes invalidate cache) — N/A for this slice.
  - `F3` (cache key includes profile + JD hashes) — closed under S1 + S13 jointly.
  - `G7` (structured error / graceful degradation) — PASS — JD-relevance check is wrapped in try/except so BGE-M3 outages degrade to "accept answer", consistent with the rest of the pipeline's resilience contract.
  - `H1` (per-decision audit log) — UNVERIFIED — `data/semantic_decisions.db` still doesn't exist; the S13 leak guard logs at WARNING but doesn't write a structured row. Closure depends on **S3**.
  - `K1` (real-app log evidence) — PASS — `logs/audit/s13_live_evidence.log` quoted above.

- **Branch**: `audit-slice-s13-cognitive-leak` off `pipeline-correctness-fixes`.
- **Files touched**: `shared/memory_layer/_stores.py` (root cause, 2 lines + comment), `jobpulse/screening_pipeline.py` (defense-in-depth, ~25 lines), `tests/shared/memory_layer/test_procedural_recall_domain_isolation.py` (new), `tests/jobpulse/test_screening_llm_jd_relevance.py` (new), `scripts/audit_s13_live_evidence.py` (new evidence collector).

### TP-3 PageReasoner LLM call + JSON parse path (`jobpulse/page_analysis/page_reasoner.py:528,541` + Fix D)

- **Current**: `smart_llm_call` → `_parse_response`. Two-pass parse (strict → cleanup retry). On second failure with ≥3 fillable fields, default to `PageAction(action="fill_form", confidence=0.3)`. Confidence guard further lowered if required fields would be dropped.
- **Target**: as-is for the safety net; *root-cause* the recurring Kimi malformed-JSON problem so the safety net stops being load-bearing.
- **Status**: **OK (graceful) WITH UNDERLYING GAP**. The graceful demotion is correct semantic-first design (per SG2 rule). But the *frequency* of the fallback firing on the same URL is a finding, not a feature.
- **Priority**: **P1** — `run_final` log shows: *"PageReasoner: first parse failed, retrying with strict-JSON prompt → parse failed after retry, but 64 fillable fields detected — defaulting to fill_form (confidence=0.3)"*. Confidence dropped to **0.00** on the recorded action; threshold guard saved it. On a URL with <3 fields the safety net would not engage and the apply would fail.
- **Verify by**: `logs/live_e2e/run_final_20260510_141251.log` line `PageReasoner: parse failed after retry, but 64 fillable fields detected`.
- **Correctness check**:
  - Right input? PASS — Kimi is given proper page snapshot via SystemMessage + HumanMessage.
  - Right mechanism? PASS for graceful demotion, **GAP** for root-cause: Kimi JSON malformation is a model-side issue Fix D papered over with retries+cleanup. The advisor's framing of "Option-2 fill_form fallback" was accepted in the live-e2e session as the answer; this audit re-classifies it as a P1 root-cause that needs:
    - LLM-side: pin lower-temperature, structured output, or use `response_format={"type":"json_object"}` *if Kimi supports it* (Anthropic Claude does, OpenAI does — Moonshot v1's support is unverified).
    - Reasoning-side: emit a `failure` signal to `OptimizationEngine` when the cleanup-retry path fires, so the rate of fallback engagement is observed and a slice can land when it crosses a threshold.
  - Right output for THIS context? PASS — fill_form was the right action on Anthropic's form page; field_count_guard correctly observed 64 fillable fields.
  - Right downstream consumption? PASS — Navigator executed `fill_form`.
- **Dimension matrix**: `D4` (OOD path) PASS via cleanup-retry + threshold guard. `D5` (confidence propagation) **MARGINAL** — confidence=0.0 propagated but the apply still proceeded thanks to field_count_guard; this means consumers cannot use confidence as a reliable abort signal because the fallback path overrides it. `H1` (per-decision audit log) **GAP** — no `data/semantic_decisions.db` exists; the apply log is the only record.

### TP-4 CV tailor cache key (`data/applications.db:tailored_cv_cache`)

- **Current**: PK = `(role_archetype, jd_hash, profile_version)`. Two rows present, both `research_engineer` archetype, distinct `jd_hash` + `profile_version`.
- **Target**: as-is; this is the model implementation for SG1.
- **Status**: **OK** — profile+JD-aware by primary key.
- **Priority**: P1 (currently met).
- **Verify by**: `sqlite3 data/applications.db "SELECT role_archetype, jd_hash, profile_version, hit_count FROM tailored_cv_cache"` →
  ```
  research_engineer | 6d671dd7b59e8c10 | 8530387fbf1f2c89 | 10
  research_engineer | 1dfeef8a6f0b3e82 | 33e1800629da3837 | 6
  ```
- **Correctness check**:
  - Right input? PASS — both jd_hash and profile_version drive the key.
  - Right mechanism? PASS — content-hash key.
  - Right output? PARTIAL — the audit doesn't load the cached payloads to compare to *expected* CV content; promote to PASS via LLM-as-judge in a follow-up slice. Marked **UNVERIFIED for value-content**, PASS for keying.
  - Right downstream consumption? PASS — live-e2e shows `tailored_cv_cache: hit … skipping 4× LLM calls`.
- **Dimension matrix**: `F3` PASS, `D9` PASS for keying / UNVERIFIED for content.

### TP-5 Cover-letter cache key (`data/applications.db:cover_letter_cache`)

- **Current**: PK = `(company, role_archetype, inputs_hash)`. `inputs_hash` is computed from `(profile, jd, company)` per `cover_letter_agent.py` (function inspection deferred — payload contents observed include 4 `(label, sentence)` pairs, JD-specific).
- **Target**: as-is.
- **Status**: **OK pending value-content judge** — keying looks correct; payload content not LLM-judged.
- **Priority**: P2.
- **Verify by**: payload row sampled — bullets are JD-specific ("Anthropic API", "GPT and Machine Learning") and not generic.
- **Correctness check**: input/mechanism/downstream PASS; output content **UNVERIFIED** (slice).

### TP-6 ScreeningIntent classifier (`jobpulse/screening_intent.py`)

- **Current**: 620 prototypes / 31 intents loaded into Qdrant collection. Per live-e2e doc dimension-aligned to BGE-M3 (1024) post-reindex.
- **Target**: as-is.
- **Status**: **OK** — 620 prototypes is an increase from the 175 in the older spec, suggesting the pipeline has been growing the intent set. **However**: `run_final` log shows `intent=unknown` on **every** screening_cache hit including questions like *"AI Policy for Application"*, *"How do you pronounce your name?"*, *"Why do you want to work at Anthropic?"*. Either (a) those questions genuinely don't map to any of the 31 intents (legitimate `unknown`), or (b) the cache stores `intent="unknown"` from a prior run and never re-classifies on hit.
- **Priority**: P2 — quality, not blocking.
- **Verify by**: log lines `screening_cache: hit on 'AI Policy for Application' (score=1.00, intent=unknown, option_aligned=False)`.
- **Correctness check**:
  - Right input? UNVERIFIED — does the cache lookup ever invoke the intent classifier on hit?
  - Right mechanism? Embedding-first → PASS conditional on (a) above.
  - Right output? "unknown" *might* be the correct classification for "How do you pronounce your name?" — but the `field_label` is also surfaced in the OK-confirmed-fill below ("My name, Yash Bishnoi, is pronounced as…"), so the answer was generated correctly *somewhere* — likely by a free-text LLM call rather than via classified intent. **Investigate which path resolved the answer when intent=unknown.**
  - Right downstream consumption? Marginal — `intent` is used for cost-tracker grouping; `unknown` is a coarse bucket.
- **Dimension matrix**: `D5` MARGINAL (low-confidence "unknown" leaks through), `D7` UNVERIFIED, `H7` (trace ID) UNVERIFIED.

### TP-7 ScreeningOptionAligner mis-alignment (`jobpulse/screening_option_aligner.py`)

- **Current**: per live-e2e doc, `_fuzzy_score` bug fixed; embedding fallback wired.
- **Status seen on `run_final`**: **GAP — observed live**. Two fields dropped on first try:
  - `Veteran Status` — answer "No" did not align to options `['I am not a protected vete…', 'I identify as one or more', 'I don't wish to answer']`.
  - `Disability Status` — answer "No" did not align to options `['Yes, I have a disability…', 'No, I do not have a disab…', 'I do not want to answer']`.
- The system *recovered* a few log lines later via `screening_cache: hit on 'Veteran Status' (score=1.00, intent=ai_assist, option_aligned=True) — skipping LLM alignment` — i.e. someone (Kimi/AI assist or human) had previously corrected this and the cache served the corrected answer on the **second pass** of the form. The first-pass drop is the gap.
- **Priority**: **P1** — first-pass drop on EEO fields means the form would have been submitted with those fields empty if the second pass weren't executed (which is form-flow-dependent).
- **Verify by**: log lines `screening answer 'No' did not align to any option for 'Veteran Status' — dropping`.
- **Correctness check**: input was `"No"`, mechanism failed to map to `"I am not a protected veteran"` despite that being the negative-disclosure option; output was DROPPED (no fill on first pass); downstream consumed the second-pass cached answer correctly.
- **Dimension matrix**: `D6` (tie-breaker / escalation) FAIL on first pass, `D2` (threshold calibration) UNVERIFIED, `I3` (learning consumption) PASS — the cached `intent=ai_assist` row proves a prior correction taught the system, *but only after the first attempt failed*.

### TP-8 PageReasoner first-pass abort + Fix D recovery (run_final field_count_guard)

- Already covered under TP-3.

### TP-9 db_observability per-decision log (`data/db_observability.db:lookups`)

- **Current**: 180 rows total at audit start, ~250+ after this session's 4 URL runs. Schema records `(db_name, table_name, key_hash, hit, value_repr, latency_ms, status, drop_reason, field_label, intended, actual, consumed_ts)`.
- **Target**: this IS the model implementation for `H1` (per-decision audit log) — for DB lookups.
- **Status**: **OK** with caveat — only wraps DB lookups, not LLM decisions or option-alignment decisions. A separate `data/semantic_decisions.db` does **not exist** — see `H1` global GAP below.
- **Verify by**: schema query above + post-session sqlite3 tally:
  - `screening_semantic_cache|screening_semantic_cache|15|2` (15 lookups, 2 hits across this session — Anthropic mining had 8/8, Graphcore 2/15 = ~13% hit rate cross-domain)
  - `applications|tailored_cv_cache|4|0` (4 lookups, 0 hits — Graphcore is a fresh URL, expected miss)
  - `page_reasoning_cache|reasoning_cache|3|0` (3 lookups, 0 hits — every page reasoning call missed cache, related to TP-3 JSON parse fragility)
  - `user_profile|sensitive_fields|16|4` (16 lookups, only 4 hits — sensitive fields like screening answers had 75% miss rate this session, consistent with TP-1 cache-blindness — same question on different URLs misses because the cache wasn't profile+JD-keyed but is keyed on something even narrower than question text alone)
  - `form_experience|signal_corrections|18|0` (18 lookups, **0 hits** — same as audit-start snapshot; this DB is wired but never serves data, candidate "wired but empty" status per CLAUDE.md note about 19 empty DBs)
- **Correctness check**: All four PASS for DB-lookup wrapping; **N/A for LLM-call decisions** (separate slice S3 needed).

### TP-10 H1 global — no `semantic_decisions.db` exists (audit-wide finding)

- The `dimensions.md → H1` pass signal is "One row per semantic decision: `(application_id, component, input, mechanism, threshold, score, output, validation_result, confidence)`."
- `find data/ -name "semantic_decisions.db"` → empty; not in `data/` listing either.
- **Status**: **GAP — cross-cutting**. Every LLM-tier or embedding-tier decision in the pipeline has no per-decision audit row; only DB lookups are observed via `db_observability.db:lookups`.
- **Priority**: P1 — without it, sub-goal 4 (verify per real run) requires log mining as the only source.
- **Slice**: see Slice S3 below.

### TP-11 `process_single_url` JD-analyzer title + company extraction (`jobpulse/scan_pipeline.py:1060-1069`)

- **Current**: hardcoded BeautifulSoup CSS selectors with LinkedIn/Indeed bias:
  ```python
  title_el = soup.select_one("h1, .job-title, .topcard__title")
  company_el = soup.select_one(".topcard__org-name-link, .company-name, '[data-testid=\"inlineHeader-companyName\"]'")
  ```
- **Target**: per-ATS extraction using DOM cues OR LLM-extracted title/company from the `jd_text`. The skill-extractor LLM is already called downstream — title+company extraction can be folded into it (one LLM call), or a separate adapter-aware extractor.
- **Status**: **GAP — confirmed cross-ATS this session**.
- **Priority**: **P1** — every non-LinkedIn URL processed via `process_single_url` falls through to "Unknown Role @ Unknown Company". This contaminates:
  - CV path → `data/applications/Unknown_Company/Yash_Bishnoi_Unknown_Company.pdf` (PDF naming feedback memory violated).
  - Notion sync → all Unknown-Company applies collapse onto the **same shared Notion page** `35577c42-6a5f-811f-835c-f1623445b51d` (confirmed in 3 logs this session). Data integrity collapse.
  - JobListing model → downstream consumers (cv_tailor, recruiter_screen, gate2 must-haves) get empty company, which breaks per-company logic.
- **Verify by**:
  - `logs/audit/lever_palantir_20260510_153704.log` line 9 → `analyzed — Unknown Role @ Unknown Company`.
  - `logs/audit/ashby_openai_20260510_153825.log` line 9 → `analyzed — Unknown Role @ Unknown Company`.
  - `logs/audit/greenhouse_graphcore_20260510_153918.log` line 7 → `analyzed — Automation Engineer @ Unknown Company` — title works on Greenhouse (`h1` selector hits), but **company still falls back to Unknown** on Greenhouse too. So this is a P1 cross-EVERY-ATS bug for company; only LinkedIn URLs get correct company.
- **Correctness check**:
  - Right input? FAIL — selectors used for extraction don't match Lever / Ashby / Greenhouse / SmartRecruiters / iCIMS / Workday DOM patterns.
  - Right mechanism? FAIL — hardcoded CSS, exactly what "Dynamic Over Hardcoded" forbids. The Eight Engineering Principles checklist explicitly bans selectors like this on the apply path.
  - Right output? FAIL — wrong title/company mis-routes the application from the very first step.
  - Right downstream consumption? FAIL — Notion page collision, CV mis-naming, gate-failure cascade.
- **Dimension matrix**: `B7` schema-validation-at-boundary FAIL (no validation that title/company resolved); cross-cutting `D9` (profile+JD context) FAIL (jd_context_hash would be wrong/empty); `H1` UNVERIFIED.
- **Slice**: **NEW Slice S6 below**.

### TP-12 CV generation runs even when pre-screen rejected (`scan_pipeline.process_single_url` flow)

- **Current**: even when `pre-screen tier=skip gate1=True gate2=False gate3=0.0%`, the pipeline still calls cv_tailor + generate_cv and writes a PDF to `data/applications/Unknown_Company/`. Observed in both Lever and Ashby logs.
- **Target**: gate CV generation on `tier in {'apply','review','queue'}`. Skip materials gen when tier=='skip'.
- **Status**: **GAP** — wasted LLM calls + Drive uploads + Notion writes on rejected JDs.
- **Priority**: **P2** — quality / cost finding, not blocking. ~5 LLM calls per skipped JD = ~$0.025 per skip; on the 19 PR-rejected URLs in `data/applications/Unknown_Company/` directory historical accumulation, that's significant waste. Also prevents the audit's "wiring verification" cleanly because Notion has stale Unknown rows.
- **Verify by**: log lines `pre-screen tier=skip` immediately followed by 5 `POST api.moonshot.ai/v1/chat/completions` calls and `CV generated` line.
- **Slice**: **NEW Slice S7 below**.

### TP-13 Notion company+role page reuse for Unknown ↔ Unknown collisions (`jobpulse/job_notion_sync.py`)

- **Current**: `find_application_page` matches on `(company, role)`. With every fall-through to `(Unknown Company, Unknown Role)`, the system reuses the same Notion page — every failed JD analysis updates **the same** Notion entry (id `35577c42-6a5f-811f-835c-f1623445b51d`).
- **Target**: skip Notion creation/update when title or company is "Unknown" (sentinel value). Or: refuse to create application records for the Unknown sentinel.
- **Status**: **GAP**.
- **Priority**: **P2** — data integrity in user's Notion DB.
- **Verify by**: 3 distinct URLs in this session all hit page id `35577c42-6a5f-811f-835c-f1623445b51d`.
- **Slice**: **NEW Slice S8 below** (could fold into S6).

### TP-15 NativeFormFiller global field-mapping cache leaks per-company screening questions (`form_experience_db.py`)

- **Current**: `Loaded 52 field mappings for job-boards.greenhouse.io (52 global)` (Graphcore log line). The `(52 global)` suffix means the field-label→answer mappings are **stored globally on `_global`** key, not per-company. The `DIAG field_mapping_keys` print-out confirms this: it lists Octus-specific questions (`'Do you have any family members or individuals with whom you have a close personal relationship currently employed by Octus?*'`, `'Do you have any restrictive covenants that would prevent you from working at Octus?*'`) being loaded for the **Graphcore** apply.
- **Target**: store mappings per `(domain, company)` or per `(profile_state_hash, jd_context_hash)` (same SG1 fix as TP-1). Loading Octus-specific custom questions on a Graphcore apply is the same SG1 violation as TP-1 — just on a different cache layer.
- **Status**: **GAP**.
- **Priority**: **P1** — direct apply-correctness risk. If a Graphcore form happens to contain the literal label "Do you have any restrictive covenants that would prevent you from working at Octus?" the system would auto-fill the Octus-cached answer.
- **Verify by**: log line `[jobpulse.native_form_filler] Loaded 52 field mappings for job-boards.greenhouse.io (52 global)` followed by `DIAG field_mapping_keys (first 15): […, '…employed by Octus?*', '…working at Octus?*', …]`.
- **Correctness check**: input/mechanism/output/downstream all FAIL — the system is loading a Octus-specific answer set into Graphcore's mapping context.
- **Slice**: ride along with S1 (cache key) — same root design issue.

### TP-16 form_experience_db DRIFT DETECTED + LLM fallback (Graphcore log line)

- **Current**: Graphcore form had only 19% structural match with the cached form-experience for `job-boards.greenhouse.io`. The system correctly detected drift (`DIVERGENCE` log line) and fell back to full LLM path. **This is correct semantic-first behaviour.**
- **Status**: **OK (graceful)** — the design protects against the global-cache problem in TP-15, partially.
- **Priority**: P2.
- **Verify by**: log line `DIVERGENCE on job-boards.greenhouse.io — match 19% (threshold 80%), diverged fields: [...] Falling back to LLM detection.`
- **Correctness check**: PASS — the system noticed the global cache was wrong for this URL and bailed out.
- **Note**: this is the *protection* for TP-15 GAP; without drift detection, every form would silently auto-fill from the global cache. With drift detection, only forms with >80% structural match auto-fill. So TP-15 is bounded but not eliminated.

### TP-17 BGE-M3 silent MiniLM fallback observed live mid-run (`shared/memory_layer/_embedder.py`)

- **Current**: pre-flight check verified BGE-M3 returns 1024-dim. **However, live mid-run on Graphcore**, BGE-M3 returned HTTP 500 and the embedder silently fell back to MiniLM (384-dim). Log line:
  ```
  [shared.memory_layer._embedder] BGE-M3 embed failed, falling back to minilm: HTTP Error 500: Internal Server Error
  [sentence_transformers.SentenceTransformer] Load pretrained SentenceTransformer: sentence-transformers/all-MiniLM-L6-v2
  ```
- **Target**: per `dimensions.md → A9`, MiniLM-384 fallback should be **either removed or made loud-fail** (raises, not silently writes 384-dim vectors that mismatch the 1024-dim Qdrant collections). The current behaviour is the explicit A9 violation.
- **Status**: **GAP — P1, observed live this session**.
- **Priority**: **P1** — the next decision after the fallback would have queried with a 384-dim vector against a 1024-dim Qdrant collection, returning empty. The cache write *is* protected (the dim guard in `screening_semantic_cache.cache()` lines 228-235 refuses to write the wrong-dim vector, logging a warning). But the *lookup* path then silently returns nothing on a query with the wrong-dim vector (no log line — Qdrant just returns 0 results). The system silently degrades correctness.
- **Verify by**: log line above + the dim-guard code at `jobpulse/screening_semantic_cache.py:228-235`.
- **Correctness check**:
  - Right input? FAIL — wrong-dim query vector silently submitted to Qdrant.
  - Right mechanism? PARTIAL — degradation is *graceful for writes* (cache stays clean) but *silent for reads* (cache hit-rate drops to 0% with no observability).
  - Right output? FAIL — every screening question after the BGE-M3 500 falls through to LLM fallback, doubling cost and latency.
  - Right downstream consumption? PARTIAL — LLM fallback fires, so an answer is produced. But the cache learning loop is broken until BGE-M3 recovers.
- **Slice**: ride along with new **Slice S10 below**.

### TP-18 OptionAligner UK-format ethnicity miss on Graphcore Greenhouse (cross-Greenhouse GAP)

- **Current**: profile/cached ethnicity answer is `'Asian or Asian British - Indian'` (UK Census 2021 format). Graphcore's ethnicity options use a different format: `'Asian (Indian, Pakistani, Bangladeshi, Chinese, Any other Asian background)'`. The aligner FAILED to recognize semantic equivalence — log:
  ```
  screening answer 'Asian or Asian British - Indian' did not align to any option for 'What is your ethnicity?*' — dropping
  ```
- **Target**: aligner should fire its embedding tier and find that "Asian … Indian" maps to "Asian (Indian, …)" with high cosine similarity. Either the embedding tier isn't reaching this case, or the threshold is too strict, or BGE-M3 was unavailable (TP-17 is a likely cause — same log).
- **Status**: **GAP — P1, observed live this session**.
- **Priority**: **P1** — wrong DEI answer = wrong application. The `_cached_screening: stale entry … skipping cache` log shows the system *defended* against the stale cache; but the LLM fallback also returned `'Asian or Asian British - Indian'` and that ALSO failed to align. Two different LLM-generated answers, both UK-format, neither aligned.
- **Verify by**: Graphcore log lines:
  ```
  screening answer 'Asian or Asian British - Indian' did not align to any option for 'What is your ethnicity?*' — dropping
  _cached_screening: stale entry for 'What is your ethnicity?' (answer='Asian or Asian British - Other' doesn't fit options=...)
  screening answer 'Asian or Asian British - Indian' did not align to any option for 'What is your ethnicity?' — dropping
  ```
- **Correctness check**: input/mechanism/output all FAIL across two attempts (cached + LLM-fresh). Downstream the Hispanic/Latino row likely got dropped in the form, leaving the EEO field unfilled.
- **Cross-ATS implication**: every ATS with non-Anthropic-style ethnicity options will surface this. UK-format answers from the profile cannot map to US/EU census formats without LLM-as-judge or a richer aligner tier.
- **Slice**: extension of S4 (ScreeningOptionAligner first-pass drop) — same slice, larger acceptance.

### TP-19 Right-to-work question filled inconsistently within the same form (Graphcore)

- **Current**: Graphcore form has the question "Please select your right to work status" twice (once with `*` required marker, once without). The two instances filled with **different values**:
  - `'Please select your right to work status*' = 'Tier 4 (General) Student Visa'` ← WRONG (profile is Graduate Visa)
  - `'Please select your right to work status' = 'Graduate Visa'` ← CORRECT
- **Target**: same question → same answer regardless of `*` suffix or form-position. Field-key normalization should strip the `*` before lookup.
- **Status**: **GAP — P1, observed live this session**.
- **Priority**: **P1** — wrong answer = wrong application. The "Tier 4 Student Visa" answer is *historically correct* (the user used to be on a Tier 4 visa) but is now stale per the user's current Graduate Visa state. This is also a TP-1 / TP-15 manifestation: the cache returned a stale answer for a profile-state-dependent question without checking profile-state-hash.
- **Verify by**: Graphcore log lines:
  ```
  fill ✓ 'Please select your right to work status*' = 'Tier 4 (General) Student Visa'
  fill ✓ 'Please select your right to work status' = 'Graduate Visa'
  ```
- **Correctness check**: input/mechanism/output/downstream all FAIL on the `*` instance.
- **Slice**: ride along with **S1** (cache key with profile-state-hash) — this is the canonical visa-state example from `dimensions.md → D9 worked-example`.

### TP-20 Gender combobox silent fill failure (Graphcore)

- **Current**: Graphcore form has gender combobox with 5 options extracted at scan time (`['Man', 'Woman', 'Non-binary', "I don't wish to answer", 'Other - Prefer to self-describe']`). At fill-time, the combobox showed only 2 options. Fill failed silently:
  ```
  fill ✗ 'I identify my gender as*' (intended='Man', actual='') [tech=combobox_type_to_search, options_seen=2]
  fill ✗ 'I identify my gender as' (intended='Man', actual='') [tech=combobox_type_to_search, options_seen=2]
  ```
  Both instances (with and without `*`) failed.
- **Target**: state-mismatch detection — when scan-time options ≠ fill-time options, abort fill and re-scan instead of silently dropping.
- **Status**: **GAP**.
- **Priority**: **P1** — DEI gender field unfilled = form rejected by some ATS validators.
- **Verify by**: log lines above + earlier scan log `'I identify my gender as*' → 5 options: ['Man', 'Woman', 'Non-binary', ...]` vs fill-time `options_seen=2`.
- **Correctness check**: input correct (`'Man'`), mechanism FAIL (combobox state mismatch), output empty, downstream consumed empty value.
- **Slice**: candidate for new **Slice S11** (state-mismatch handling in NativeFormFiller) — but could ride S5 if widely observed.

### TP-21 Vision recovery 404 on Moonshot endpoint (`shared/.../vision_recovery`)

- **Current**: Vision recovery tier (used when DOM classifier confidence < 0.7) tried to POST to `https://api.moonshot.ai/v1/responses` and got 404. Log:
  ```
  POST https://api.moonshot.ai/v1/responses "HTTP/1.1 404 Not Found"
  Vision recovery call failed: Error code: 404 - {'code': 5, 'error': 'url.not_found', 'message': '没找到对象', ...}
  ```
- **Target**: vision recovery should use Moonshot's vision endpoint (if exists) or hard-code to OpenAI for vision-only calls. Moonshot v1 lacks the OpenAI-style `/v1/responses` endpoint.
- **Status**: **GAP — P2**.
- **Priority**: **P2** — vision tier is an escalation path; the DOM classifier confidence on Anthropic and Graphcore was sufficient that vision never gated a fill outcome. But on a future ATS where DOM confidence drops below 0.7, vision recovery's 404 = fill aborts.
- **Verify by**: log line above.
- **Correctness check**: Mechanism FAIL — wrong API endpoint for current LLM provider. This is downstream of the live-e2e session's `LLM_PROVIDER=openai` env-pin (Slice S9). With provider pinned to openai, this would route correctly. With the current code path, vision is broken.
- **Slice**: **NEW Slice S11 below**.

### TP-22 CV + CL uploaded as `Unknown_Company` files on Graphcore

- **Current**: Graphcore's CV/CL upload accepted files at:
  - `Yash_Bishnoi_Unknown_Company.pdf`
  - `Cover_Letter_Unknown_Company.pdf`
- The materials are functionally OK (the actual content was tailored to the Graphcore JD via cv_tailor LLM calls), but the **filenames** are wrong — Graphcore's recruiters will see "Unknown_Company" in their ATS file index.
- **Target**: filenames derive from `(profile.name, jd.company)`. With company="Unknown Company" upstream (TP-11), filename inherits the bug.
- **Status**: **GAP — P1, downstream of S6**.
- **Priority**: **P1** — direct candidate-presentation problem. Recruiter sees "Yash_Bishnoi_Unknown_Company.pdf" and likely auto-rejects.
- **Verify by**: log line `upload_pdf: ✓ uploaded Yash_Bishnoi_Unknown_Company.pdf`.
- **Slice**: closes automatically when **S6** lands.

### TP-24 Silent field-drop on Graphcore — required `Have you added your full legal name…?*` was scanned but never filled, application still queued

- **Current**: Graphcore form had a required combobox `'Have you added your full legal name and surname (including any middle names)?*'` with options `['Yes', 'No']`. The field_analyzer extracted its options at scan-time (log line 64). The fill loop **never tried it** — there is **no `fill ✓` or `fill ✗` log line for this field anywhere in the entire 174-line apply log**. Yet the apply concluded with `status: queued_for_review` and ATS score 97% — i.e., the form-fill agent considered the form complete.
- **Target**: every required field that survives field_analyzer must either fill (✓) or fail (✗) — never be silently dropped. If the fill loop has a code path that lets a scanned-but-unselected combobox exit without log emission, that path is a P1 correctness leak.
- **Status**: **GAP — P1, observed live this session, NOT covered by any other touchpoint**.
- **Priority**: **P1** — direct apply correctness. Worse than TP-20 (gender) because gender at least emits `fill ✗` so a downstream consumer could route to escalation. This field is silently invisible after scan; the apply succeeds on a form with a required unfilled field. If submitted, ATS validation fails at submit time and the application is rejected by the form (or worse, accepted with a missing required field if the ATS is lenient — but the recruiter sees a malformed application).
- **Verify by**: Graphcore log `logs/audit/greenhouse_graphcore_20260510_153918.log`:
  - Line 64: `[jobpulse.native_form_filler] ✓ 'Have you added your full legal name and surname (i' → 2 options: ['Yes', 'No']` (analyzer extracted options).
  - `grep -c "added your full legal name" logs/audit/greenhouse_graphcore_*.log` → 1 (only the option-extraction line; zero fill lines).
  - `grep -c "fill ✓\|fill ✗" logs/audit/greenhouse_graphcore_*.log` → 18 (the 18 logged fills exclude this field).
  - Final result: `"status": "queued_for_review"` (apply considered successful).
- **Correctness check**:
  - Right input? Field was correctly scanned and options correctly identified.
  - Right mechanism? **FAIL** — the fill-loop iteration over scanned fields appears to have a code path that exits before this field, OR field_analyzer's output isn't always consumed by the fill loop, OR the field was de-duplicated against another field (the form has `Have you added your full legal name…?*` and possibly a non-`*` instance) but the de-duplication logic dropped the required instance.
  - Right output? **FAIL** — no value written to a required field.
  - Right downstream consumption? **FAIL** — the apply success-criteria treats the form as complete despite the unfilled required field. There is no validation step that cross-checks "every required field has a fill outcome".
- **OPRAL trace**:
  - **Observe**: scan → analyzer → 2-option extraction; fill loop log silent for this field; apply ends with `queued_for_review`.
  - **Plan**: trace `native_form_filler.py` fill loop to find where a scanned field can be skipped without a `fill ✗` log emission. Likely candidates: (a) field_analyzer marks it as `analyzer-only / no widget mapping`, (b) the field is classified as a duplicate of another (Greenhouse forms sometimes have `*`-suffixed instances vs `_global` mappings), (c) the loop has an early-exit condition that skips fields with certain types/states.
  - **Reason**: the silent drop is a *correctness gap*, not a *mechanism gap* — the system makes the wrong decision (declare success) on the wrong information (no record of this field being addressed). Per `dimensions.md → H1`, every semantic decision needs a per-decision audit row; the absence of a "skip" decision row is itself the gap.
  - **Act** (slice plan, no patch this audit): see Slice S12.
  - **Learn**: `agent_performance.fill_sessions` should record a `fields_total_visible` count alongside `fields_filled` so a delta of `visible − filled > 0` raises an alert.
- **Cross-ATS implication**: this is a NativeFormFiller correctness gap, not Greenhouse-specific. Any ATS form with a field that field_analyzer classifies as scannable-but-not-fillable (or that the dedup logic drops) will exhibit the same silent drop. P1 across all 11 adapters; the failure mode just happened to surface live on Graphcore because of the unique label structure.

### TP-23 ScreeningSemanticCache `intent="unknown"` on cache hits (re-check from TP-6)

- **Current**: every `screening_cache: hit` log line in `run_final` shows `intent=unknown` for stored answers including questions that should classify cleanly (e.g. "Why do you want to work at Anthropic?", "How do you pronounce your name?", "AI Policy for Application").
- **Target**: re-classify on hit OR cache the prior classification. The current state means downstream consumers see `intent=unknown` and the option-aligner falls through to literal text matching (which then fails on truncated EEO options — see TP-7).
- **Status**: **GAP** — quality.
- **Priority**: P2.
- **Verify by**: log lines `intent=unknown` on every cache hit + the corresponding TP-7 first-pass alignment failure.
- **Slice**: ride along with S1 (cache key) since this is in the same file.

---

## Cross-ATS findings (4 URLs run this session, 1 fully + 1 form-fill-mid + 2 pre-screen-blocked)

| URL | Adapter | Pre-screen | Form fill | Findings (touchpoints) |
|---|---|---|---|---|
| `…/anthropic/jobs/4017331008` (mined) | Greenhouse | apply | full | TP-1 (cache key blind), TP-2 (LLM screening OK), TP-3 (page reasoner JSON fragility), TP-4 (CV cache OK), TP-5 (CL cache OK pending content-judge), TP-6 (intent=unknown on hits), TP-7 (option-aligner Veteran/Disability drop), TP-9 (db_observability OK), TP-10 (no semantic_decisions.db) |
| `…/lever/palantir/ff1029bd…` | Lever | **skip** (gate2=False) | none | TP-11 (Unknown Role+Company), TP-12 (CV gen on skip), TP-13 (Notion collision); JD location actually London/UK despite matrix's US flag |
| `…/ashby/openai/fc5bbc77…` | Ashby | **skip** (gate2=False) | none | TP-11 (Unknown Role+Company), TP-12 (CV gen on skip), TP-13 (Notion collision) |
| `…/greenhouse/graphcore/jobs/8539033002` | Greenhouse | apply (ATS 97%) | full → `queued_for_review` | TP-11 (title=Automation Engineer extracted, company=Unknown), TP-15 (Octus screening Qs leaked into Graphcore mappings), TP-16 (DRIFT 19% → LLM fallback worked), TP-17 (BGE-M3 500 → silent MiniLM fallback live), TP-18 (UK-format ethnicity miss on Graphcore options), TP-19 (right-to-work Tier4 vs Graduate Visa double-fill), TP-20 (gender silent fail), TP-21 (vision recovery 404 on Kimi), TP-22 (CV/CL uploaded as Unknown_Company files), **TP-24 (required `Have you added your full legal name…?*` silently dropped — apply still queued as success)**; Bristol/UK-coded JD so no SG1 worldwide-region advancement |
| Lever (US-coded) | Lever | — | — | UNVERIFIED — matrix's "US-coded" flag was wrong on Palantir; need a different URL |
| SmartRecruiters | SmartRecruiters | — | — | UNVERIFIED |
| iCIMS | iCIMS | — | — | UNVERIFIED |
| Reed | Reed | — | — | UNVERIFIED |
| LinkedIn | LinkedIn | — | — | UNVERIFIED |
| Indeed | Indeed | — | — | UNVERIFIED |
| Oracle Cloud | (no adapter) | — | — | UNVERIFIED |
| Workday | Workday | — | — | UNVERIFIED |
| Generic | Generic | — | — | UNVERIFIED |

### Adapter-agnostic findings (universal across every URL)

- **TP-1 + TP-15** (cache profile/JD-blind, mappings stored as `_global`) — fundamental SG1 violation; affects every adapter.
- **TP-11** (title+company extractor with hardcoded LinkedIn CSS selectors) — affects every non-Greenhouse adapter at the *company* level; affects every adapter except Greenhouse-with-`<h1>` at the *title* level. Empirically confirmed on Lever, Ashby, AND Greenhouse this session.
- **TP-12** (CV gen runs even on pre-screen=skip) — wastes ~5 LLM calls per skipped URL.
- **TP-17** (BGE-M3 500 → silent MiniLM fallback) — observed mid-Graphcore; non-deterministic.

### Adapter-specific findings

- **Greenhouse**: TP-3 (page reasoner JSON), TP-7 (Veteran/Disability ellipsis options), TP-19 (`*`-suffix double-fill), TP-20 (gender silent fail), TP-24 (legal-name silent drop — required field never reached fill loop).
- **Lever / Ashby**: TP-11 root-cause manifests strongly because their DOM patterns differ from LinkedIn's selectors. Lever Palantir's expected-US JD turned out to be UK-coded (London) — matrix flag was wrong.
- **SmartRecruiters / iCIMS / Workday / Generic / Oracle Cloud**: UNVERIFIED — Phase 2 work.

### Profile-driven (SG1) findings — preliminary

- The two URLs with form-fill (Anthropic Greenhouse + Graphcore) are both UK-coded. **No US-coded JD reached form-fill this session**.
- Cache key construction is profile/JD-blind (TP-1 source-confirmed).
- TP-19 demonstrates the *symptom* on a single profile against its own historical state — `Tier 4 (Student) Visa` was correct in the past, `Graduate Visa` is correct now. The cache is profile-time-blind too: as the user's profile state changed (Tier 4 → Graduate), the old answer survived in the cache because there's no `profile_state_hash` to invalidate against.

A full UK + US comparison is **deferred to Phase 2C** in the continuation plan, after Slice S6 (title+company extractor) lands so non-Greenhouse URLs reach form-fill.

---

## Profile-driven findings (SG1 — worldwide multi-region comparison deferred to Phase 2C)

**Audit prompt requirement** (verbatim): *"which decisions correctly varied across UK + US JD contexts; which incorrectly stayed constant. Cache-key inspection result."*

> **Scope broadening**: the audit prompt names UK + US as a representative pair, but the actual goal is correctness across **every** JD context the profile encounters. Per `dimensions.md → D9 (Profile + JD context drives every value-producing decision)`, the right answer for visa, salary currency, notice period, relocation, languages, DEI disclosure, role-level seniority, and CV role profile depends on the JD's country, role-level, language, currency, and company policy — a worldwide space, not a binary. UK + US is the *minimum* materially-different pair; the system must produce correct answers across at least: 🇬🇧 UK, 🇺🇸 US, 🇪🇺 EU (DE/FR/NL), 🇸🇬 Singapore / 🇮🇳 India / 🇯🇵 Japan (APAC), 🇨🇦 Canada, 🇦🇪 UAE, 🇦🇺 Australia. The Phase 2C continuation broadens the test from a 2-region binary to a worldwide region grid.

### Cache-key inspection result (the load-bearing finding)

`jobpulse/screening_semantic_cache.py:33` defines the cache key as:

```python
def _to_qdrant_id(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest(), 16) % (2 ** 63)
```

The lookup vector at line 324 embeds `question.strip()` only:

```python
vector = self._embedder.embed(question.strip())
results = self._qdrant.query_points(query=vector, limit=limit, score_threshold=min_score)
```

The payload (line 274) includes a `job_context_hash` field, but **the lookup never uses it for filtering or matching** — only `field_options` is consulted (lines 346-352).

**Conclusion**: the key is **question-text-only**; no `profile_state_hash`, no `jd_context_hash`. Per `dimensions.md → F3` and the SKILL "Profile-Driven Decisions" rule, this is a **P1 SG1 violation by construction**. Across a worldwide JD space — UK / US / EU / APAC / Canada / UAE / Australia — the same question text returns the same cached answer regardless of which country's JD is being applied to. Visa-sponsorship is a worked example: the right answer is "No" for UK on Graduate Visa, "Yes" for US (H-1B), "Yes" for Germany (EU Blue Card), "Yes" for Singapore (Employment Pass), "Yes" for Canada (work permit) — five different correct answers from one profile, all collapsed onto a single cached row by the current key.

### Decisions that correctly varied this session

- **CV-tailor cache (TP-4)**: `tailored_cv_cache` PK = `(role_archetype, jd_hash, profile_version)`. SQL inspection shows two distinct rows for `research_engineer` archetype across two JDs:
  ```
  research_engineer | 6d671dd7b59e8c10 | 8530387fbf1f2c89 | hit_count=10
  research_engineer | 1dfeef8a6f0b3e82 | 33e1800629da3837 | hit_count=6
  ```
  Different JDs → different rows. **Correctly varies on JD context.** OK for SG1 keying (content correctness UNVERIFIED pending LLM-as-judge).
- **CL cache (TP-5)**: PK = `(company, role_archetype, inputs_hash)`. Payload sampled — bullets are JD-specific (e.g. "Anthropic API", "GPT and Machine Learning" for Anthropic CL). **Correctly varies on company context.** OK for keying.
- **Skill extraction**: Lever Palantir extracted `['LLMs', 'Machine Learning', 'Python', 'Java', 'C++', 'TypeScript', 'JavaScript']` while Graphcore extracted `['aws iam', 'confluence', 'continuous integration', 'devops', 'git', 'gitlab', 'gitops', 'jenkins', ...]`. Different JDs → different skill sets. **Correctly varies.**
- **Pre-screen tier**: Anthropic + Graphcore = `apply` (skills aligned), Lever Palantir + Ashby OpenAI = `skip` (TP-11 root cause: Unknown Company → Gate 2 fail, *not* a true SG1 variation but the system did vary the route).

### Decisions that incorrectly stayed constant (or would, if the gap weren't bounded)

- **Screening cache answers (TP-1, TP-15)**: by construction, the same question text returns the same cached answer regardless of profile state or JD country. Source-confirmed P1 GAP. The visa-sponsorship example surfaces wrong values across the entire worldwide JD space — UK→"No" cached, but US/EU/APAC/Canada/UAE/AU JDs all need "Yes" and would receive the wrong cached "No". Not directly observed live this session because no non-UK JD reached form-fill (Lever Palantir's matrix-flagged "US" annotation was wrong; the JD was London/UK).
- **Right-to-work answer (TP-19)**: same form, same question instance label (`*` suffix vs no suffix), produced two different fills:
  - `'Please select your right to work status*' = 'Tier 4 (General) Student Visa'` (WRONG — historical, profile no longer on Tier 4)
  - `'Please select your right to work status' = 'Graduate Visa'` (CORRECT — current profile state)
  This is the canonical visa-state symptom of TP-1 cache-blindness within a single profile across time. The cache surfaced a stale answer for the `*` instance because the cache was profile-time-blind (no `profile_state_hash` to invalidate against the visa-state change Tier 4 → Graduate).
- **Global field-mappings (TP-15)**: `Loaded 52 field mappings for job-boards.greenhouse.io (52 global)`. The mappings include Octus-specific custom screening questions (`'Do you have any restrictive covenants that would prevent you from working at Octus?*'`) being loaded into the Graphcore application context. Drift detection (TP-16, 19% match) saved the day — the system fell back to LLM detection rather than auto-filling Octus answers into Graphcore's form. **The protection is structural, not key-based**, so SG1 is bounded by drift threshold, not eliminated.
- **Ethnicity answer (TP-18)**: cached `'Asian or Asian British - Indian'` (UK Census 2021 format) failed to align to Graphcore's options `'Asian (Indian, Pakistani, Bangladeshi, Chinese, Any other Asian background)'`. The cache served the *same* answer to both Anthropic (which uses U.S. EEO format options including `'Asian'`) and Graphcore (UK census-derived options). The answer didn't fail on Anthropic but fails on Graphcore — same answer, different correctness depending on JD/ATS context.

### Cross-context evidence not yet collected (Phase 2C scope — broadened to worldwide)

The audit prompt's SG1 acceptance names "*two URLs with materially-different JD context (e.g. UK + US)*" as the minimum; the goal is correctness across **every** region the profile encounters. This session:

- **Lever Palantir**: matrix flagged as US-coded; live JD location was `London, United Kingdom`. False signal in the matrix — counts as UK, not US.
- **Ashby OpenAI**: presumed San Francisco / US-coded but pre-screen rejected before JD location reached the listing object. Effectively unverified.
- **Anthropic + Graphcore**: both UK-coded.

**Only one region (UK) reached form-fill this session**. The cross-context comparison required by SG1 acceptance is therefore **deferred to Phase 2C**, after Slice S6 (title+company extractor) lands so non-Greenhouse URLs reach form-fill. The Phase 2C scope is broadened from the prompt's UK+US minimum to a **worldwide region grid**:

| Region | Representative URL needed | Decisions that should differ from UK baseline |
|---|---|---|
| 🇬🇧 UK | Anthropic / Graphcore (covered) | baseline (visa "No", currency £) |
| 🇺🇸 US | needs new URL — matrix's flagged ones (Palantir) were wrong | visa "Yes", currency $, EEO format, salary range |
| 🇪🇺 EU (DE/FR/NL) | candidate from Workday / Lever multi-region | visa "Yes", currency €, GDPR-strict consent, language requirements |
| 🇸🇬 Singapore | not in current matrix — Phase 2C should add | Employment Pass needed, currency S$ |
| 🇮🇳 India | not in current matrix — Phase 2C should add | currency ₹, no visa needed for Indian profile |
| 🇯🇵 Japan | not in current matrix — Phase 2C should add | language requirement, currency ¥, work-culture screening |
| 🇨🇦 Canada | not in current matrix — Phase 2C should add | work permit needed, currency C$ |
| 🇦🇪 UAE | not in current matrix — Phase 2C should add | sponsored visa norm, no income tax |
| 🇦🇺 Australia | not in current matrix — Phase 2C should add | currency A$, visa subclass |

The decisions that should differ across this grid (per `dimensions.md → D9` decision-context table):
- **Visa sponsorship** — different per country.
- **Salary expectation** — currency conversion + market rate adjustment.
- **Notice period** — UK 1 month vs Germany 3 months vs US "two weeks" vs APAC variable.
- **Relocation** — `profile.willing_to_relocate × jd.location × jd.remote_policy` per country.
- **Languages** — required vs preferred varies (e.g. German fluency for Berlin role).
- **DEI answers** — gender/ethnicity/disability question formats vary by region (UK Census, US EEOC, Australian ABS, Singapore CMIO, Indian SC/ST/OBC).

The decisions that should stay constant (worldwide, same profile):
- **Skills list** — same profile = same skills.
- **CV role-archetype** for the same role.
- **Profile identity** (name, email, GitHub).
- **General DEI disclosure preference** (e.g. "I do not wish to answer" if that's the user's preference everywhere).

### What this means for the goal

Slice **S1** (cache key with `profile_state_hash` + `jd_context_hash`) closes the SG1 violation **at the construction level**. Source inspection is sufficient evidence that S1 is needed; the worldwide multi-region run is needed to *verify the fix*, not to *prove the gap exists*. The gap is already proven. TP-19 is the live symptom on a single profile against its own historical state (Tier 4 → Graduate Visa) — same root cause, the geographic axis adds 8+ more failure modes from one cache row.

---

## Goal-closing slices (P1 first; one slice per error per the OPRAL discipline)

> **Discipline note**: the audit forbids fixes; these are *plans*, not patches. Each slice ends with acceptance criteria + which sub-goal it closes. Each goes on its own branch — no stacking.

### Slice S1 — Add `(profile_state_hash, jd_context_hash)` to ScreeningSemanticCache key

- **Closes**: SG1 (rule 4 — profile-driven decisions).
- **Files (read-only inspection at audit time; the slice would touch them)**:
  - `jobpulse/screening_semantic_cache.py` — extend `_to_qdrant_id`, `cache()`, `lookup()` signatures to accept and key on the two new hashes; payload already has `job_context_hash`, just unused.
  - `jobpulse/screening_pipeline.py` — compute and pass `profile_state_hash` (from current profile fields the question depends on per `dimensions.md → D9` table) and `jd_context_hash` (from JD location/role-level/company) at `_finalise()` and any `cache()` / `lookup()` call site.
  - One downstream caller in `screening_pipeline.py:417` LLM fallback site (write-path on success).
- **Design**:
  - `profile_state_hash` = SHA over the profile fields the question depends on. Visa-class questions hash `(visa_status, visa_expiry, work_auth_country)`; salary questions hash `(expected_range, salary_currency, current_salary)`; relocation hashes `(current_city, willing_to_relocate)`; etc. Use the table in `dimensions.md → D9` as the authoritative input set per intent.
  - `jd_context_hash` = SHA over `(jd.country, jd.role_level, jd.start_date_class)` — coarse enough that paraphrased JDs hit, narrow enough that UK and US miss each other.
  - Lookup: query Qdrant with question vector AND filter `payload.profile_state_hash == ours AND payload.jd_context_hash == ours`. Stale entries from old profile state remain, but never retrieved.
  - Cold-start path: `unknown` profile state hashes intentionally don't pollute (drop the cache, fall through to LLM).
- **Acceptance** (live, multi-ATS, worldwide multi-region):
  - Run the same profile against JDs from **at least 5 distinct regions** (UK / US / EU / APAC / Canada or UAE/AU). Cache must produce **N distinct entries** for the visa-sponsorship question, one per region, returning the right answer per `dimensions.md → D9` worked-example table (UK→"No", US→"Yes", EU→"Yes", Singapore→"Yes", Canada→"Yes", etc.).
  - Run on 5 of 11 adapters covering the materially different visa contexts.
  - `db_observability.lookups` shows N `screening_semantic_cache` entries with distinct `key_hash` values per region.
  - All 26 URLs in `url-coverage-matrix.md` pass evidence + correctness.
  - **Profile-state invalidation test**: bump `profile.visa_status` from "Graduate Visa" to "ILR" on a test profile; the next live apply MUST regenerate the answer rather than serve the stale Graduate-Visa-era cache row (this is the TP-19 symptom flipped into a positive test).
- **Risk**: cache hit-rate temporarily drops. Acceptable cost; the previous hit-rate was masking incorrect answers.

### Slice S2 — Root-cause Kimi malformed-JSON in PageReasoner (Fix D currently load-bearing)

- **Closes**: SG2 + SG5.
- **Files (read-only inspection; the slice would touch them)**:
  - `shared/agents.py` — `cognitive_llm_call` / `smart_llm_call` to optionally pass Moonshot's structured-output equivalent (verify Moonshot v1 supports `response_format={"type":"json_object"}` or `tool_use` style structured outputs — if yes, gate page reasoner on it; if no, slice falls back to schema-prompt + lower temperature).
  - `jobpulse/page_analysis/page_reasoner.py` — emit `failure` signal to `OptimizationEngine` whenever the cleanup-retry path engages, so engagement-rate is observable. Drop the field_count_guard's `confidence=0.3` masking when the underlying parse fails — return `confidence=0.0` and let the navigator's existing low-confidence escalation handle it.
- **Acceptance**:
  - On 26-URL matrix, Kimi malformed-JSON cleanup-retry engagement-rate < 5%.
  - On URLs where it does engage, the failure signal is observed in `data/optimization.db:signals`.
  - PageReasoner returns `confidence=0.0` on parse failure (the field_count_guard moves to a separate decision path, not a confidence override).

### Slice S3 — Ship per-decision audit log (`data/semantic_decisions.db`)

- **Closes**: SG4 (live-run verification across the audit) + dim H1.
- **New file**: `shared/semantic_decisions.py` — small SQLite store with the schema in `dimensions.md → H1` (`application_id, component, input, mechanism, threshold, score, output, validation_result, confidence`).
- **Wiring**: 4 LLM call sites + 11 form-fill semantic decisions write one row each, keyed by `application_id`. Reuse the `db_observability.lookups` pattern (decorator) so consumers don't churn.
- **Acceptance**:
  - After a live apply, `python -m jobpulse.runner replay-decisions <application_id>` emits the chain.
  - 26 URLs each produce ≥10 rows.
- **Cost**: one new DB; reuses observability decorator pattern.

### Slice S4 — ScreeningOptionAligner first-pass drop on truncated EEO options

- **Closes**: SG3 + SG5 (rule 5 — error becomes learning).
- **Trace**: `run_final` shows answer `"No"` failed to align to options `['I am not a protected vete…', 'I identify as one or more', "I don't wish to answer"]`. The aligner doesn't currently match `"No"` ⇒ negative-disclosure option ("I am not a protected veteran"). On second pass, AI-assist had already corrected this and the cache served the right value.
- **Files**: `jobpulse/screening_option_aligner.py` — the embedding tier should already cover this; investigate why it didn't (threshold? field_options truncation hiding the real text from the embedder?).
- **Acceptance**: `Veteran Status` and `Disability Status` align on first pass on at least 4 ATS adapters that surface those EEO fields.

### Slice S5 — Sub-goal 3 closure: run remaining 10 adapters

- **Closes**: SG3.
- **Per adapter**: `apply_job(url, dry_run=True)` via `python -m jobpulse.runner job-process-url <url> generic`. After each: `python -m scripts.db_observability_summary --window-days 1` (must exit 0). Apply four-question check per touched touchpoint.
- **Acceptance**: every adapter shows TP-1-through-TP-9 touched; gaps documented per adapter.
- **Pre-requisite**: Slice S6 must land first, otherwise every non-Greenhouse adapter pre-screen-rejects on `Unknown Company`.
- **Continuation plan path**: see below.

### Slice S6 — Replace hardcoded title+company CSS selectors with adapter-aware extractor

- **Closes**: SG2 + SG3 + the user's "Dynamic Over Hardcoded" rule (rule 8 of the Eight Engineering Principles).
- **Trace**: `process_single_url` (in `jobpulse/scan_pipeline.py:1060-1069`) uses LinkedIn/Indeed-biased CSS selectors (`h1`, `.topcard__title`, `.topcard__org-name-link`) that miss on every other ATS. Empirically confirmed this session on Lever, Ashby, and Greenhouse (which gets title via `h1` but misses company entirely).
- **Files (read-only for the audit; the slice would touch them)**:
  - `jobpulse/scan_pipeline.py` (replace selectors).
  - `jobpulse/jd_analyzer.py` (accept LLM-extracted title+company from a single skill-extractor call, optionally).
- **Design** (one of two acceptable paths):
  1. **Adapter-aware extractor**: per-ATS CSS map in `ats_adapters/*.py` returning `(title, company)` from the page DOM. Falls through to LLM extraction on miss.
  2. **LLM-first**: include title + company in the existing skill-extractor LLM call (`extract_skills_hybrid`) — Kimi already sees the JD text; one call extracts skills + title + company in a single shot. Cheaper than per-ATS adapters.
- **Acceptance**:
  - 26 URLs in `url-coverage-matrix.md` produce non-empty title + company.
  - Zero applications under `data/applications/Unknown_Company/` after the slice lands.
  - Notion sync writes distinct pages per `(company, role)` pair on a sample of 5 cross-ATS URLs.
- **Risk**: a small number of legitimate "Unknown" cases (truly missing company on the JD page) become LLM-extracted with low confidence. Acceptable; the LLM tier is the right fallback per dim D4 (OOD path).

### Slice S7 — Gate CV/CL generation on `pre-screen tier != 'skip'`

- **Closes**: SG2 + cost.
- **Trace**: `process_single_url` continues to `generate_cv` / `cv_tailor` / Notion write even when `tier=='skip'` and `gate2=False`. Costs ~5 LLM calls per skipped JD (~$0.025), and writes a polluting CV PDF + Notion row.
- **Files**: `jobpulse/scan_pipeline.py` (gate the materials-gen branch).
- **Acceptance**:
  - Skipped JDs produce zero LLM completion calls beyond the pre-screen LLM.
  - No new files in `data/applications/<company>/`.
  - Notion is updated with status='Skip' (single update, no body content).
- **Cost saved**: ~$0.025 × ~daily_skipped_count. On the audit's 3 URL runs this session, all 3 skipped runs would have saved ~5 calls each.

### Slice S8 — Refuse Notion write for `Unknown Company` sentinel

- **Closes**: SG3 (data integrity).
- **Trace**: `find_application_page(company="Unknown Company", role="Unknown Role")` reuses page id `35577c42-6a5f-811f-835c-f1623445b51d` — every failed JD analysis collapses onto this one row. Confirmed across 3 distinct URLs this session.
- **Files**: `jobpulse/job_notion_sync.py` (early-return when `company == "Unknown Company"`).
- **Acceptance**: zero Notion writes for Unknown sentinel after the slice.
- **Could fold into S6** since fixing the upstream extractor removes the symptom.

### Slice S11 — Vision recovery endpoint mismatch with Moonshot

- **Closes**: SG2 + SG5.
- **Trace**: `field_mapper` vision recovery posts to `https://api.moonshot.ai/v1/responses` and gets 404 (Moonshot has `/v1/chat/completions` only; `/v1/responses` is the OpenAI-style endpoint Moonshot doesn't implement).
- **Files**: `jobpulse/form_engine/field_mapper.py` vision tier, plus `shared/agents.py` provider routing.
- **Design**: route vision tier through the same provider abstraction as text — currently it appears to bypass `get_llm()` (uses raw `OpenAI()` client directly per the `User-Agent: OpenAI/Python 1.109.1` log). Either route through `smart_llm_call` (gets cloud fallback for free) or pin vision to OpenAI/Anthropic only when Kimi mandate is in effect.
- **Acceptance**:
  - On a deliberately-induced low-DOM-confidence URL, vision tier returns a result instead of 404.
  - On the 26-URL matrix, zero `Vision recovery call failed: 404` log lines.

### Slice S10 — Loud-fail BGE-M3 unavailability instead of silent MiniLM fallback (dim A9)

- **Closes**: SG2 + SG4 (live observability of mechanism-correctness).
- **Trace**: live-observed this session — Graphcore run hit `BGE-M3 embed failed, falling back to minilm: HTTP Error 500`. Cache writes were protected (dim guard refused), but Qdrant lookups silently returned 0 results because the query vector was 384-dim against a 1024-dim collection. No alarm fired.
- **Files**: `shared/memory_layer/_embedder.py` — convert MiniLM fallback path to one of:
  1. **Loud-fail**: raise `EmbedderUnavailableError` after N consecutive 500s; caller decides retry vs human escalation.
  2. **Per-collection guard**: raise on any read attempt against a collection whose dim doesn't match the current embedder.
  3. **Telegram alert**: when fallback engages, fire `g6` (security-wall-style) human alert with bounded poll.
- **Acceptance**:
  - On a deliberately-induced BGE-M3 500, the apply pipeline either re-tries with backoff, raises a structured `EmbedderUnavailableError`, or sends a Telegram alert. **Silent dim-mismatch is not acceptable**.
  - 26-URL matrix shows zero `falling back to minilm` log lines on a healthy BGE-M3.
- **Risk**: tightens an availability constraint; if Ollama is brittle, applies fail more loudly. Acceptable — silent wrong-answer is worse than loud refuse.

### Slice S12 — Close silent field-drop in NativeFormFiller fill loop (TP-24)

- **Closes**: SG2 + SG4 + SG5 (correctness leak + observability gap).
- **Trace**: live-observed on Graphcore. Required combobox `'Have you added your full legal name and surname (including any middle names)?*'` was scanned by `field_analyzer` (options extracted), then **silently dropped from the fill loop** with no `fill ✓` or `fill ✗` emission. Apply concluded with `queued_for_review` (success) despite the unfilled required field.
- **Files**:
  - `jobpulse/native_form_filler.py` — fill loop that consumes scanned fields. Find the exit/skip path that doesn't emit a log line; convert it to either:
    1. **Always-emit invariant**: every scanned field exits the loop with a `fill ✓` / `fill ✗` / `fill ⊘` (skip-with-reason) log line. No silent skips.
    2. **Required-field guarantee**: every field with `required=True` MUST have a fill outcome before the apply concludes; if not, raise `RequiredFieldUnfilledError`.
  - `jobpulse/agent_performance.py` — record `fields_total_visible` + `fields_attempted` + `fields_filled` + `fields_failed` + `fields_silently_dropped` on `fill_sessions` so the gap is observable retroactively.
- **Acceptance** (live, multi-ATS):
  - 26-URL matrix: zero apply-log emissions of `queued_for_review` / `success=True` where `fields_silently_dropped > 0`.
  - On Graphcore re-run, the legal-name field either fills (`fill ✓`) or fails (`fill ✗`); no third-state silent drop.
  - `db_observability.lookups` — every visible required field has at least one row tagged with its `field_label`.
- **Risk**: tightening this invariant will surface latent dropped fields on adapters where this currently passes silently; the apply will fail loudly on those forms until they're addressed. Acceptable — silent wrong-data is worse than loud refuse.

### Slice S9 — Enforce Kimi LLM mandate at startup + per-call (rename from "LLM_PROVIDER pin")

- **Closes**: SG2 (semantic mechanism — every LLM completion must go to the mandated provider, embeddings exempt).
- **Naming clarification (addresses real config confusion)**: `LLM_PROVIDER=openai` in `.env` does **NOT** mean "route to OpenAI's `api.openai.com`". It means "use the OpenAI-compatible Python SDK", which is then *pointed at* Kimi/Moonshot via `OPENAI_BASE_URL=https://api.moonshot.ai/v1`. The actual provider is Kimi. The env-var name is misleading because Moonshot mirrors OpenAI's API shape and the project reuses OpenAI's SDK.
  - Live verification this session: every LLM call across 4 URLs hit `api.moonshot.ai/v1/chat/completions`; zero hits to `api.openai.com`. The mandate is *de facto* enforced, but only by configuration coincidence — there is no code-level guard that *prevents* a future config change (or auto-detection drift) from routing to OpenAI.
  - **Embeddings are exempt** from the LLM mandate: BGE-M3 via Ollama (1024-dim) on `http://localhost:11434`. The Kimi mandate covers chat-completions only.
- **Trace**: vision recovery (TP-21) is the canary — it bypassed the OpenAI-compatible wrapper, posted to `/v1/responses` (an OpenAI-only endpoint Moonshot doesn't have), and got 404. With current routing all chat-completions land on Kimi, but vision-tier didn't go through the same routing — proof that the mandate isn't enforced *at the call site*, only at the wrapper level.
- **Files**:
  - `shared/agents.py` — startup probe assertion: when `KimiAI_API_KEY` is set, `OPENAI_BASE_URL` MUST be `https://api.moonshot.ai/v1` (or whichever Kimi endpoint is canonical). Process exits loudly if not.
  - `shared/agents.py` `get_llm()` / `smart_llm_call()` / `cognitive_llm_call()` — add a per-call host-allowlist guard: every chat-completion call's resolved host MUST be `api.moonshot.ai` (or any Kimi-controlled domain). On mismatch, raise `LLMProviderViolationError` and emit an `OptimizationEngine` `failure` signal.
  - Vision tier (`form_engine/field_mapper.py` and any other site that bypasses `get_llm()`) — route through the same wrapper or be explicitly exempted with a documented reason.
  - **NEW env-var rename (optional, scope-tagged)**: introduce `LLM_PROVIDER=kimi` with `LLM_PROVIDER=openai` as a deprecated alias. Makes the mandate self-documenting in `.env` files.
- **Acceptance** (live, multi-ATS):
  - 26-URL matrix: zero `api.openai.com` host hits in apply logs; zero `Vision recovery call failed: 404` lines.
  - On a deliberately-corrupted `OPENAI_BASE_URL=https://api.openai.com/v1` config, the apply pipeline refuses to start with a clear error.
  - On a deliberately-bypassed call site (raw `OpenAI()` pointing at OpenAI), the per-call guard raises before the HTTP request leaves.
- **Priority**: **P2** — the mandate is currently *de facto* enforced by config, but TP-21 proves there's at least one call path that bypasses the wrapper. Without enforcement, a future Ollama-up state could silently switch some calls to local `qwen3:32b` (the original concern from the live-e2e doc).
- **Out of scope**: changing the embedder. Embeddings continue to use BGE-M3 (1024-dim) via Ollama — the Kimi mandate is for **chat completions only**, not for vector embeddings. Slice S10 separately handles BGE-M3 reliability.

---

## Continuation plan (`docs/superpowers/plans/2026-05-10-semantic-audit-phase2-continuation.md`)

(Will be written as a sibling file when this session ends — see end of this document.)

Remaining work after this session:
1. **Lever / Palantir** (US-coded JD) — primary SG1 cross-context evidence.
2. **Ashby** (OpenAI fc5bbc77 or Perplexity 79a07e2d).
3. **SmartRecruiters** (Bosch or JobsForHumanity) — shadow-DOM exercise.
4. **iCIMS** (careers.icims.com/6309 or 6306) — iframe-based forms.
5. **Reed** — modal CV upload pattern.
6. **LinkedIn Easy Apply** — auth-walled.
7. **Indeed** — redirect to Generic exercise.
8. **Oracle Cloud HCM** — confirms no-adapter status; tests Generic fallback.
9. **Workday** — multi-tenant variance.
10. **Generic** — fallback specifics.

Per-URL budget 45 min including correctness check; 10 URLs × 45 min = ~7.5 hours of additional live-run time.

---

## Confidence

**~28%** session-end. <100% means goal not met. What's specifically missing:

- 9 of 11 ATS adapters not validated to form-fill (SG3). 2 (Lever, Ashby) blocked at pre-screen by TP-11 (Unknown Company root cause).
- UK+US cross-context comparison not executed live (SG1 endpoint observed via TP-19 *symptom* but not via UK-vs-US disjoint runs).
- Three CL-cache / role-archetype touchpoints carry **UNVERIFIED for value content** awaiting LLM-as-judge.
- 31 of 35 LLM call sites untouched (the prompt-audit doc deliberately deferred them; they're Slice P3 in that doc).
- `semantic_decisions.db` (dim H1) doesn't exist — every PASS in this audit relies on log mining.

---

## End-of-session print

- **Distance % per sub-goal**: SG1 ~15% / SG2 ~30% / SG3 ~9% (1 of 11 adapters; both Greenhouse URLs same adapter) / SG4 ~25% / SG5 ~35%. Composite ~24-28%.
- **Touchpoints**: 23 entries (TP-1...TP-13 + TP-15...TP-24; TP-14 unused — see numbering note). **5 promoted to OK / OK-graceful** (TP-2, TP-4, TP-5, TP-9, TP-16). **16 demoted to P1/P2 GAP** with live evidence (TP-1, TP-3, TP-7, TP-10, TP-11, TP-12, TP-13, TP-15, TP-17, TP-18, TP-19, TP-20, TP-21, TP-22, TP-23, **TP-24**). **2 UNVERIFIED** with named missing evidence (TP-5 content-judge, TP-6 intent-on-cache-hit).
- **Cross-ATS coverage**: 1 of 11 adapters fully validated (Greenhouse Anthropic mining); 1 partial (Greenhouse Graphcore form-fill); 2 pre-screen-blocked (Lever Palantir, Ashby OpenAI) — surfacing TP-11 as the cross-ATS pre-req. **Effective: 2 of 11 with caveats; 9 remaining**.
- **Slices recommended**: 12 P1+P2 slices. S1 (cache key with profile+JD hashes — closes worldwide SG1), S2 (PageReasoner JSON), S3 (semantic_decisions.db), S4 (option aligner first-pass), S5 (cross-ATS prosecution), S6 (title+company extractor — P1 pre-req for S5), S7 (CV-on-skip waste), S8 (Notion Unknown sentinel), S9 (LLM_PROVIDER pin), S10 (BGE-M3 loud-fail), S11 (vision recovery endpoint), **S12 (silent field-drop — required field invariant in fill loop)**. All P1 except S5/S7/S9.
- **Confidence**: ~28%. **Next-session unblock**: land S6 + S10 (and ideally S1) on separate branches; then re-fire the audit prompt for Phase 2B (10 remaining adapters) per `docs/superpowers/plans/2026-05-10-semantic-audit-phase2-continuation.md`.

**BLOCKED-WITH-PLAN — `docs/audits/2026-05-10-semantic-audit-verified.md` (this file) + `docs/superpowers/plans/2026-05-10-semantic-audit-phase2-continuation.md`**.

---

## Session 4 update (2026-05-10) — S13 landed

S13 (cognitive routing context-leak fix) landed on `audit-slice-s13-cognitive-leak`. Changes: TP-25 added (root-cause + live evidence), TP-2 acceptance text updated to reflect the post-S13 free-text JD-relevance guard, prompt-context audit doc updated with the S13 closure block for site B + cognitive_llm_call.

- **Touchpoint count**: 23 → 24 entries (added TP-25; TP-14 still unused). **6 promoted to OK / OK-graceful** post-S13: previous 5 + **TP-25** (cognitive routing leak — closed). **TP-1 keying is still S1's territory; TP-1 *content correctness* is now closed via S13** at the LLM-tier root cause.
- **Slices closed this session**: **S13** (P0 once it became S1's content-correctness pre-req). Live evidence, 11/11 new tests green, screening test suite 124/125 (1 failure is pre-existing TP-17 BGE-M3 fragility, unaffected by S13).
- **Distance % delta**: SG2 (right mechanism) ~30% → **~33%** — the cross-domain procedural recall was a sub-goal-2 violation by construction (cognitive engine returning *any* template's content when no in-domain template existed); closing it removes the largest non-keyed mechanism violation in the screening path. Composite ~24-28% → **~27-31%**.
- **Slices remaining to advance**: S2/S3/S4/S5/S6/S7/S8/S9/S10/S11/S12 (11 from the list above), plus Phase 2B 10-URL prosecution. **S14** (TP-15 form_experience_db field-mapping cache scoping), **S15** (TP-19 right-to-work `*`-suffix label normalization), **S16** (TP-23 intent re-classification on cache hit) added to the queue per the Phase 2 continuation plan. Recommended next: **S4** (option aligner first-pass drop on truncated EEO), then **S3** (the per-decision audit log that every PASS in this audit currently relies on log-mining for).
