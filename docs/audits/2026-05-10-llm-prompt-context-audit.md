# LLM Prompt & Context Audit (companion to pipeline-wide audit)

**Date**: 2026-05-10
**Companion to**: `docs/audits/2026-05-10-semantic-analysis-pipeline-audit.md`
**Scope**: every site where the JobPulse pipeline calls an LLM. For each call site, audit the 8 prompt dimensions defined in `.claude/skills/audit-semantic-analysis/SKILL.md → Prompt-level audit`.

## Live-Evidence + Correctness-Validation Rule (MANDATORY)

**Every prompt-dimension claim in this audit requires TWO things, both supplied by a real live apply run on a real public URL:**

1. **Live evidence** — apply log line showing the actual prompt sent (with `agent_name` cost-tracker attribution), `data/db_observability.db` row showing cache hit/miss, real LLM provider response from the live run, or a row in a prior live-run audit deliverable.
2. **Correctness validation** — confirmation that the prompt structure was *right for this call site*: did the prompt include the right context fields, did the LLM actually produce the correct answer for this JD+profile, did the downstream consumer use the answer correctly. Use ground-truth joins (profile DB, screening cache) for factual dimensions, LLM-as-judge with a written rubric for output-quality dimensions.

Mechanical execution evidence alone is NOT sufficient. "The prompt was sent" / "the response parsed" / "the cache key was stable" prove execution, not correctness. Per `.claude/skills/audit-semantic-analysis/SKILL.md → Live-Evidence + Correctness-Validation Rule`.

Specifically for the 8 prompt dimensions:
- **W1/W2 (wrapper, messages)**: live evidence = the actual prompt observed in apply log; correctness = a senior engineer would route this through the wrapper they did, with the message structure they used.
- **C1 (context payload)**: live evidence = the interpolated prompt; correctness = LLM-as-judge confirms every field needed to answer correctly is present (and no PII beyond what's needed).
- **C2 (truncation)**: live evidence = log shows `truncated=True` event when an input was long; correctness = the truncated prefix actually contained the information needed.
- **O1/O2 (schema, validation)**: live evidence = the parsed output; correctness = the validated value is the *right* answer for the candidate's profile vs the JD, not just a parseable answer.
- **R1 (cache)**: live evidence = cache-hit log line on second run; correctness = the cached value is *still correct* for the second-run input (same JD; same profile state).
- **R2 (cost / fallback / determinism)**: live evidence = `cost_tracker` row + on-error path observed; correctness = the fallback path produces a value that's still correct, not just a value that doesn't crash.

Pytest-based assertions about prompt structure are useful for development but do NOT satisfy this rule. The 4 sites audited below are marked from a static read; their statuses become authoritative only after a live run produces evidence + correctness — which the in-flight `docs/superpowers/plans/2026-05-10-live-e2e-dry-run.md` session will produce for sites B (screening_pipeline) and (partially) A (gate4_quality) on the Anthropic Greenhouse URL. AI agents reading those live-e2e logs to promote entries here MUST apply the four-question correctness check, not just confirm the artefact exists.

## What this audit answers

Two complementary questions to the mechanism audit:

1. **How does each component construct the prompt it sends to the LLM?** — wrapper, message structure, output schema, validation, cache, cost.
2. **What information is sent in the prompt?** — JD, profile, page state, options, prior corrections, learned rules.

A wrong context payload produces a wrong answer just as surely as a regex would. The mechanism audit said "this component uses LLM (good)"; this audit says "the LLM is given the right inputs and produces a contract-checked output (also good)" — or it doesn't.

## Inventory of LLM call sites (jobpulse/ + shared/)

35 call sites grouped by phase:

| Phase | Files (with line numbers of the LLM call) |
|---|---|
| Pre-Screen | `skill_extractor.py:384`, `gate4_quality.py:300`, `pre_submit_gate.py:140,258` |
| CV/CL | `cv_tailor.py:470`, `cv_templates/generate_cover_letter.py:317`, `portfolio_variants.py:236,329`, `persona_evolution.py:183` |
| Apply Orchestration | `page_analysis/page_reasoner.py:528,541`, `page_analyzer.py` (LLM tier — verify) |
| Form Fill | `screening_pipeline.py:417`, `screening_decomposer.py:163`, `screening_answers.py:617`, `form_engine/widget_llm_recovery.py:90`, `form_engine/intent_healing.py:91`, `form_engine/field_analyzer.py` (verify), `form_engine/confidence_scorer.py` (verify), `native_form_filler.py:981` |
| Learning | `email_preclassifier.py:469,493`, `rejection_analyzer.py` (post-migration), `strategy_reflector.py:197`, `shared/cognitive/_reflexion.py:19`, `shared/cognitive/_engine.py:25`, `shared/cognitive/_tree_of_thought.py:19,83`, `scan_learning.py:575` |
| Cross-cutting | `dispatcher.py` (post-migration via `nlp_classifier.classify`), `swarm_dispatcher.py:619`, `command_router.py:393`, `pattern_router.py` (post-migration), `conversation.py`, `morning_briefing.py`, `notion_agent.py:309`, `arxiv_agent.py:261,399`, `papers/ranker.py:269,333,361`, `gmail_agent.py:136`, `blog_generator.py:20`, `budget_nlp.py` |

## Audited call sites (4 representative — pattern-establishing)

The following 4 call sites are read top-to-bottom and audited against all 8 dimensions. The remaining 31 are recorded as `UNVERIFIED — pending prompt audit` and prosecutable by a follow-up slice.

### A. Gate 4 B2 LLM CV scrutiny (`gate4_quality.py:300`)

- **Current**: `cognitive_llm_call` review of CV against required + preferred skills, scored 0-10 across relevance / evidence / presentation / standout, JSON verdict.
- **Target**: stay as-is; this is the model implementation.
- **Status**: **OK**.
- **Priority**: P1.
- **Verify by**: smoke test — `pytest tests/jobpulse/test_gate4_quality.py -k scrutinize_cv_llm` on a real CV+JD pair, assert cache hit on second call.
- **Prompt audit**:
  - W1 Wrapper: `cognitive_llm_call` ✓
  - W2 Messages: single `task=prompt` string (no system distinction needed — persona is embedded "You are a senior IT recruiter at Google").
  - C1 Context: `role`, `company`, `required_skills[:15]`, `preferred_skills[:10]`, `cv_text[:3000]`, scoring rubric (4 axes), output schema.
  - C2 Truncation: ✓ — explicit `[:15]`, `[:10]`, `[:3000]`.
  - O1 Schema: `response_format={"type": "json_object"}` ✓ STRONG.
  - O2 Validation: `json.loads` with `try/except`, falls back to `LLMScrutinyResult(needs_review=True)` on parse failure. Score and verdict typed.
  - R1 Cache: 30-day SHA cache per `(cv_hash, jd_hash)` via `_cv_scrutiny_cache_lookup` ✓.
  - R2 Cost+fallback: `domain="cv_scrutiny"`, `stakes="high"`, fallback to `safe_openai_call` direct client if cognitive returns None, structured error result on all failure modes.

### B. Screening LLM fallback (`screening_pipeline.py:417`)

- **Current**: `cognitive_llm_call` produces a screening answer when intent classification + cache + profile resolve all miss. Two prompt branches: option-field (closed-set picker) vs free-text.
- **Target**: tighten W2 (proper message list) and C2 (bound `profile_summary`); keep O2 alignment (the strongest part of this call site).
- **Status**: **OK (graceful)** — answer validation compensates for the W2/C2 weaknesses.
- **Priority**: P1.
- **Verify by**: a unit test that asserts the LLM is reached only after intent classification + profile resolution miss, and that misaligned answers are rejected.
- **Prompt audit**:
  - W1 Wrapper: `cognitive_llm_call` ✓
  - W2 Messages: **DEGRADED** — flattens to `f"SYSTEM: {system_prompt}\nUSER: {user_prompt}"` and passes as `task=`. The cognitive engine sees one user message; the system role is just text. Fix: pass a structured pair through a wrapper that supports message lists, or split into a `system_prompt` argument if `cognitive_llm_call` accepts one.
  - C1 Context: `profile_summary` (PII), optional `context`, `question`, `options` (if option-field), `field_type`. Anti-AI-leak guard: "Never mention that you are an AI."
  - C2 Truncation: **NOT BOUNDED** — `profile_summary` length depends on caller. Needs explicit budget (e.g. `[:1500]`) and a log-line when truncation fires.
  - O1 Schema: prompt-instructed "Return EXACTLY ONE option, using the EXACT option text" / "1-3 sentences max" — no `response_format` parameter; raw text.
  - O2 Validation: ✓ STRONG. For option fields, `OptionAligner.align_answer(answer, options, field_type)` plus `opts_lower` set-membership. Misaligned answer logged at WARNING and treated as miss → caller falls through. This is the model pattern for downstream-contract validation.
  - R1 Cache: no direct cache at this site, but `screening_semantic_cache.py` (Qdrant) catches earlier in the pipeline before LLM is reached.
  - R2 Cost+fallback: `domain="screening_answers"`, `stakes="high"`. On exception → returns `None` (caller falls through). No `agent_name` — cost is recorded under `screening_answers` domain.

### C. Page reasoning (`page_analysis/page_reasoner.py:528,541`)

- **Current**: `smart_llm_call(llm, msgs)` produces a `PageAction` (page_understanding, action, target_text, reasoning, confidence, page_type, expected_outcome). Local LLM with cloud fallback on failure.
- **Target**: stay as-is for the LLM call; ship the un-shipped semantic cache from the 2026-04-30 spec to add R1.
- **Status**: **OK (graceful)** — strong everywhere except R1 (hash cache only; no semantic near-miss).
- **Priority**: P1.
- **Verify by**: a smoke test that calls `reason_sync` twice on slightly-different page snapshots from the same domain, asserting the second hits the (currently un-shipped) semantic cache.
- **Prompt audit**:
  - W1 Wrapper: `smart_llm_call` ✓.
  - W2 Messages: ✓ proper `[SystemMessage, HumanMessage]` list. System prompt defines the navigator role and the action enum.
  - C1 Context: `url`, `page_text`, `dialog_text`, `button_summary` (top-15 buttons, label[:40]), `field_summary` (top-20 fields with type and current-value snippet), `wall_info`, on reflection: `prior_action` + `forbidden_clause` + `failure_context`.
  - C2 Truncation: ✓ — collection slicing + per-string truncation.
  - O1 Schema: prompt-instructed structured response, parsed by `_parse_response`. No `response_format`.
  - O2 Validation: ✓ — `_apply_zero_fields_guard` (lowers confidence when required fields dropped from `field_fills`), `_apply_advance_button_guard`, action enum check in parser.
  - R1 Cache: hash-based cache per `(domain, content_hash)` ✓. Semantic near-miss cache from 2026-04-30 spec **NOT YET SHIPPED** — repeated near-identical pages incur full LLM cost.
  - R2 Cost+fallback: `temperature=0`, `max_tokens=500`, `agent_name="page_reasoner"`, local→cloud fallback (`is_local_llm() → force_cloud=True`), on-error returns `PageAction(action="abort", confidence=0.0)`.

### D. Widget LLM recovery (`form_engine/widget_llm_recovery.py:90`)

- **Current**: `smart_llm_call` produces a JSON array of Playwright actions (click/fill/press/select_option) for a custom widget that all standard fillers and vision-tier failed on.
- **Target**: minor — promote prompt to message list with separate `SystemMessage` for the role, log the `value` redacted (it may be PII).
- **Status**: **OK (graceful)**.
- **Priority**: P2 (one-shot recovery; rarely fires).
- **Verify by**: existing `test_widget_llm_recovery.py` unit test + a runtime trace from a session where the recovery actually fires (rare).
- **Prompt audit**:
  - W1 Wrapper: `smart_llm_call` ✓.
  - W2 Messages: **MINOR** — only `HumanMessage`; the persona ("You are an expert at Playwright browser automation") is embedded at the top of the user prompt rather than a `SystemMessage`. Fix when touched, low priority.
  - C1 Context: `label[:80]`, `field_role[:40]`, `value[:200]`, `html_snippet[:2000]`. Action schema embedded in prompt.
  - C2 Truncation: ✓ explicit on every input.
  - O1 Schema: prompt-instructed "Return ONLY a JSON array … no markdown fences." No `response_format`.
  - O2 Validation: `json.loads` + `isinstance(parsed, list)` check; returns `[]` on any failure.
  - R1 Cache: none. **OK** for one-shot recovery — this fires after standard fillers + vision tier fail and re-firing on the same widget on the same page produces the same input.
  - R2 Cost+fallback: `temperature=0`, `max_tokens=400`, `agent_name="widget_llm_recovery"`, returns `[]` on any exception. Skip conditions: no API key, empty html, empty value.

## Pattern findings across the 4 sites

1. **Strong validation pattern** — `OptionAligner` (B) and `_apply_zero_fields_guard` (C) are the canonical "validate LLM output before consumption" patterns. Other sites should adopt: `widget_llm_recovery` could enum-check `action.type`; `gate4` already json-schema-checks via parse fallback.
2. **Caching is uneven** — A has the right pattern (SHA cache + TTL); C has hash but no semantic; B relies on upstream cache; D has no cache (acceptable for one-shot). Several other call sites in the inventory above (untouched in this audit) have no caching info recorded — that is the next slice.
3. **Message-list discipline is uneven** — C is the gold standard (proper `[SystemMessage, HumanMessage]`). B flattens to a string. D omits the system message. The cognitive_llm_call wrapper accepts a single `task=` string, which encourages flattening; if the wrapper grew a `system=` parameter, B/D would be fixable in one line each.
4. **Truncation discipline is consistent** — A, C, D all bound their inputs explicitly. B does not bound `profile_summary`; this is a real gap and a P2 fix.
5. **Cost tracking via `agent_name`** — set in C and D (constructor-level `get_llm(agent_name=...)`). A and B route through `cognitive_llm_call` `domain=` instead. Both are recorded but the cost-tracker dashboard groups under different keys; consistency would help.

## Recommended slices for prompt-level fixes

These are commit-sized; each prosecutable in one branch.

1. **Slice P1 — Add `system=` parameter to `cognitive_llm_call`** in `shared/agents.py` so callers can pass message-list-style inputs without flattening. Update screening_pipeline.py:417 + screening_decomposer.py:163 to use it. Verify: cost tracker shows the same calls, but transcripts now include a SystemMessage. **No new file changes.**

2. **Slice P2 — Bound `profile_summary`** at the screening_pipeline call site with a budget and a log-line when truncated. Add a unit test that asserts `len(profile_summary) <= 1500` after truncation.

3. **Slice P3 — Audit the remaining 31 call sites** against the 8-dimension table. No code changes; 31 entries appended to this doc, statuses assigned. Reserve a session because the inventory crosses 8 phases.

4. **Slice P4 — Validation pattern adoption** — for each `UNVERIFIED` call site that has no `O2 Validation`, document what the contract is (enum / schema / option list) and how to validate. Skip sites whose output is genuinely free-form (cover-letter polish, briefing summary).

## Branch hygiene

Same as the pipeline audit — current branch (`pipeline-correctness-fixes`) is too large to stack these on. The slices above belong on a fresh `prompt-audit-X` branch each.

## What this audit does not do

- Doesn't audit the 31 remaining call sites; they're in the inventory and Slice P3 prosecutes them.
- Doesn't measure prompt quality (does the prompt elicit the right answer in practice?). That's an evals job; reuse `tests/jobpulse/test_semantic_quality.py` golden sets where applicable.
- Doesn't measure prompt cost. Run `python -m jobpulse.runner cost-report` if you want call-site $ amounts; this audit is structural.

---

## S13 closure of site B (`screening_pipeline._llm_answer`) and cognitive_llm_call(domain="screening_answers")

**Date**: 2026-05-10
**Branch**: `audit-slice-s13-cognitive-leak`
**Closes**: TP-1's "❌ Answer content FAIL pending S13" in the pipeline audit; site B's W2/C2 weaknesses remain (unchanged); site B's O2 strengthened.

The pipeline audit's TP-25 entry is the canonical write-up of the cognitive routing leak; what follows is the prompt-context-audit-side classification of the two call sites S13 closed against the 8-dimension table.

### Site B (free-text branch) — post-S13

- **Site**: `jobpulse/screening_pipeline.py:415` (the `else` branch at line 401, free-text questions).
- **Pre-S13 status**: OK (graceful) for option fields *only*; free-text branch had **no validation guard** so `cognitive_llm_call` orchestration leaks landed verbatim in `screening_semantic_cache.db` at score=1.00 and would have served on every subsequent matching apply (S1 cache-key changes alone would not have prevented this — they would have correctly bucketed the *wrong content* per (profile, JD) pair).
- **Post-S13 status**: OK (graceful) for both branches.
- **Prompt audit delta**:
  - W1 — unchanged (`cognitive_llm_call` ✓).
  - W2 — unchanged (still flattens; tracked under prompt-audit Slice P1).
  - C1 — unchanged (profile_summary, options, field_type, anti-AI-leak guard, JD via job_context).
  - C2 — unchanged (profile_summary still unbounded; tracked under Slice P2).
  - O1 — unchanged (prompt-instructed; no `response_format`).
  - **O2 — STRENGTHENED**: free-text branch now gates the LLM answer through `semantic_similarity(question, answer) ≥ 0.40` (BGE-M3 cosine; threshold derived from measured Q/A pairs). Below threshold → return None; caller falls through. **The cache-write path is gated by this same return value**, so leak text cannot poison the screening cache. Wrapped in try/except so BGE-M3 outages degrade to "accept answer", not crash.
  - R1 — unchanged (upstream `screening_semantic_cache` plus S1 (profile, JD) keying).
  - R2 — unchanged (`domain="screening_answers"`, `stakes="high"`, returns None on exception).

### Site B's upstream — `cognitive_llm_call(domain="screening_answers")`

- **Site**: `shared/agents.py:1053` (`cognitive_llm_call`) calling into `shared/cognitive/_engine.py` (`CognitiveEngine.think_sync`) → `shared/cognitive/_strategy.py` (`StrategyComposer.compose`) → `shared/memory_layer/_stores.py:393` (`ProceduralMemory.recall`).
- **Pre-S13 status**: silently returned cross-domain procedural strategies for any `domain` without its own templates. The L0_MEMORY path returned the cross-domain strategy text **verbatim** as `result.answer` whenever a "writing"-domain entry's `success_rate × avg_score` rank put it at the top of the (incorrectly-bled) "best procedures" list.
- **Post-S13 status**: domain isolation is enforced at the only producer/consumer boundary; cross-domain templates cannot cross.
- **Prompt audit (delta on the dimensions affected)**:
  - C1 (context payload) — fixed: the procedural strategies injected into the composed prompt are now strictly in-domain (or empty), so the LLM is no longer "echoing" cross-domain orchestration metadata it saw in the prompt.
  - O2 (validation) — unchanged at the wrapper level; relies on the caller (site B) to reject off-topic outputs. S13 added that caller-side guard.
  - F3 (cache key) — N/A at this site (not a caching site itself; closed under S1 for the downstream `screening_semantic_cache`).
  - H1 (per-decision audit log) — UNVERIFIED (no `data/semantic_decisions.db` row written; closure depends on **S3**, the per-decision audit log slice).

### What S13 did NOT change

- Site B W2 (message flattening) — slice P1 from the original prompt audit remains valid.
- Site B C2 (`profile_summary` bound) — slice P2 from the original prompt audit remains valid.
- Sites C / D unchanged.
- The remaining 31 untouched call sites — slice P3 unchanged.

### Live evidence

`logs/audit/s13_live_evidence.log` — see TP-25 in `2026-05-10-semantic-audit-verified.md` for the quoted excerpt.
