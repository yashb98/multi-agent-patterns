# Semantic Analysis — Pipeline-Wide Audit

**Date**: 2026-05-10
**Scope**: every phase, step, and feature of the job application pipeline.
**Goal**: enumerate every semantic decision the pipeline makes, classify each by mechanism (LLM / embedding / semantic_matcher / hybrid / regex / hardcoded / structural), and mark gaps against the "Dynamic Over Hardcoded" + "No regex for semantic work" rules in `.claude/rules/jobpulse.md` and `.claude/rules/shared.md`.

## Live-Evidence + Correctness-Validation Rule (MANDATORY)

**Every status, every gap, every fix in this audit requires TWO things, both supplied by a real live apply run on a real public URL:**

1. **Live evidence** — proof the decision *executed* (apply log line, `data/*.db` row, `page.input_value()` DOM readback, Notion page, Drive URL, Telegram message).
2. **Correctness validation** — proof the executed decision was *the right thing for this context* (ground-truth join with profile DB, LLM-as-judge with written rubric, domain-reasonableness review, or cross-URL consistency check).

Mechanical execution evidence alone is NOT sufficient. A row in a DB / a green checkbox / a log line proves that something ran; it does NOT prove the right thing ran. Per `.claude/skills/audit-semantic-analysis/SKILL.md → Live-Evidence + Correctness-Validation Rule`.

For each entry below, the `Verify by` line names the live-run artefact required for evidence. **An auditor (human or AI) promoting an entry to `OK` must additionally write a correctness-check note** answering the four-question rubric: right input / right mechanism / right output for this JD+profile / right downstream consumption. Without that, the entry stays `UNVERIFIED`.

Pytest invocations referenced below are **hints about what to look for in the live run**, NOT sufficient evidence. AI agents performing audit work on this document MUST disagree with mechanical PASS claims when the underlying value is wrong, even if all checkboxes are green. Treat ambiguous outputs as `UNVERIFIED`, not `OK`.

## Prior Work (do not duplicate)

| Document | Scope | Status |
|---|---|---|
| `docs/superpowers/specs/2026-04-30-semantic-analysis-overhaul-design.md` | 11 form-fill components, embedding-first restructure | Foundation (`shared/semantic_utils.py`) shipped; component restructure ~70% (semantic_matcher, screening_detector, screening_intent, screening_semantic_cache, nlp_classifier all on shared embedder; screening_option_aligner `_fuzzy_score` bug fixed; page_reasoner semantic cache + classifier embedding signal **not yet shipped**). |
| `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md` | 8 regex-heavy files | In-flight on this branch (`pipeline-correctness-fixes`). Modified in working tree: `consent_policy.py`, `email_preclassifier.py`, `nlp_classifier.py`, `pattern_router.py`, `rejection_analyzer.py`, `dispatcher.py`. Regex counts still high in `dispatcher.py` (19), `pattern_router.py` (8), `screening_answers.py` (7), `screening_decomposer.py` (6). Graceful demotion (regex as last-resort fallback) is the spec, so non-zero is acceptable but each file needs runtime verification that the embedding/LLM tier fires first. |

## Companion: prompt-level audit

This doc covers **mechanism** (regex vs embedding vs LLM). A separate companion covers **prompt construction** for the touchpoints whose mechanism is `LLM` or `Hybrid`: `docs/audits/2026-05-10-llm-prompt-context-audit.md`. That doc audits 8 prompt dimensions per call site (wrapper, message structure, context payload, truncation, output schema, output validation, caching, cost/fallback/determinism) and inventories 35 LLM call sites in the pipeline. A "GAP" in either audit blocks an `OK` claim.

## Companion: comprehensive 12-category dimension framework

Mechanism + prompt construction together still don't cover everything that makes a semantic decision correct. Embedding model version drift, magic-number thresholds, missing decision audit logs, learning loops that record but never consume, prompt-injection coverage, drift detection — these are non-mechanism concerns. The full 70-dimension checklist across 12 categories (Foundation / Input Hygiene / Anchors / Mechanism / Prompt / Caching / Reliability / Observability / Learning / Quality / Live-Run / Cross-Cutting) is in `.claude/skills/audit-semantic-analysis/dimensions.md`. Future audits should attach a `Dimension matrix` block per touchpoint with `PASS / FAIL / N/A` per applicable dimension; the touchpoint's `Status` is derived from its weakest dimension.

## Cross-reference: in-flight live-run session (do not duplicate)

A separate Claude session is executing `docs/superpowers/plans/2026-05-10-live-e2e-dry-run.md` on this same branch. That plan is a **concrete instance of dimension category K (Live-Run Verification)** for the Anthropic Greenhouse URL. It verifies: real-DOM fill readback (B), DB observability rows (H1), cache-hit on second run (F1), six-chain learning row deltas (I3), Kimi mandate cost attribution (H2), Telegram fallback paths (G6).

This audit:
- **Does not** duplicate that session's acceptance items (visa-combobox "No", relocation "Yes", Greenhouse 2-Attach pattern, etc. — all are owned by the live-e2e session).
- **Does** treat that session as the canonical exemplar of category K. Any future Slice-K audit should mirror its structure (real URL → real DOM → real DBs → row deltas → cache deltas).
- **Does not** edit code on this branch. The live-e2e session owns the working tree until it lands.

## Method

For each pipeline step in `CLAUDE.md → Live Pipeline Observation`, list every place the agent *interprets meaning* — option matching, intent classification, page typing, field labelling, skill mapping, screening Q&A, consent inference, rejection categorisation, etc. Skip pure structural code (DOM walking, mutex, rate limiting, persistence) unless it embeds a semantic decision (e.g. a regex used for routing).

Status legend:
- `OK` — semantic-first; matches the rule.
- `OK (graceful)` — semantic-first with regex demoted to fallback per 2026-05-04 design.
- `GAP` — regex/keyword-first or hardcoded, no embedding/LLM tier.
- `UNVERIFIED` — code path looks correct on inspection but I have not traced runtime behaviour end-to-end.
- `IN-FLIGHT` — covered by a current plan/branch, not yet landed.

Priority legend (apply-pipeline impact):
- **P1** — wrong decision blocks or misroutes a real application.
- **P2** — wrong decision degrades quality (worse CV match, wrong screening answer) but doesn't block.
- **P3** — wrong decision affects analytics / non-apply paths.

---

## Phase 1 — Pre-Screen (Gates 0-4)

### 1.1 Gate 0 — recruiter / title filter (`recruiter_screen.py`)
- **Current**: regex on title + keyword list, pre-LLM kill.
- **Target**: embedding similarity vs role-anchor phrases (from `_PAGE_TYPE_ANCHORS`-style canonical role descriptions), LLM tiebreaker on borderline.
- **Status**: GAP (1 regex, 0 dynamic). Not in prior plans.
- **Priority**: P1 — Gate 0 kills jobs before they ever reach LLM evaluation. Misclassification = silent loss.

### 1.2 Gate 1-3 — kill signals / must-haves / competitiveness (`skill_graph_store.py`)
- **Current**: hybrid skill extraction (582-skill taxonomy first, LLM fallback when <10 extracted). Skill matching is canonical-form lookup against a graph.
- **Target**: confirm taxonomy lookup uses synonym + embedding fallback for unknown skills (e.g. JD says "ML Ops" — does taxonomy resolve to "MLOps"?). Verify kill-signal phrases are embedding-anchored, not literal substring match.
- **Status**: UNVERIFIED. 2 regex hits in file, 0 embedding refs at module level — but the taxonomy may handle this elsewhere. Needs runtime trace on a JD with paraphrased skills.
- **Priority**: P1 — wrong kill signal = good jobs blocked. Wrong must-have match = bad jobs through.

### 1.3 `classify_action()` — routing (per CLAUDE.md, "route via classify_action(), not determine_match_tier()")
- **Current**: needs trace. CLAUDE.md flags `match_tier` as display-only — confirm `classify_action` is embedding/LLM-driven, not literal threshold matching.
- **Status**: UNVERIFIED.
- **Priority**: P1.

### 1.4 Gate 4 — quality check (`gate4_quality.py`)
- A1 JD quality (length / skill-count / boilerplate) — structural OK, no semantic decision needed.
- A2 Company blocklist — Notion DB lookup. Spam detection step needs review (substring vs embedding?).
- A3 Company background — soft signal, currently uses LLM (assumed).
- B1 Deterministic CV scrutiny — needs review.
- B2 LLM recruiter review — uses LLM, OK.
- **Status**: GAP / UNVERIFIED (4 regex, 2 dynamic). A2 spam detection and B1 deterministic scrutiny are the two semantic decisions to verify.
- **Priority**: P2 — Gate 4 is the last filter; failures cost ~$0.002 per JD but don't lose applications.

---

## Phase 2 — CV/CL Generation

### 2.1 Role profile selection (`cv_tailor.py:get_role_profile()`)
- **Current**: needs trace. Likely keyword-on-JD-title routing.
- **Target**: embedding similarity between JD role and profile-template anchors (data-analyst / backend-eng / ml-eng / etc.).
- **Status**: GAP / UNVERIFIED (6 regex, 3 dynamic across 853 lines).
- **Priority**: P1 — wrong role profile = wrong CV bullets and skill ordering.

### 2.2 Skills mapping — top-5 + "Also proficient in" (`build_extra_skills()`, `cv_templates/generate_cv.py`)
- **Current**: synonym dedup against JD requirements. Implementation uses `data/skill_synonyms.json` (36k mappings).
- **Target**: confirm synonym lookup falls through to embedding similarity when synonym table misses (otherwise unseen variants lose match).
- **Status**: UNVERIFIED.
- **Priority**: P2 — affects which skills surface; doesn't block submission.

### 2.3 Project selection — `build_dynamic_points()` (projects → skills mapping)
- **Current**: needs trace. Selecting which projects to feature based on JD overlap.
- **Target**: embedding overlap between project descriptions and JD skill list.
- **Status**: UNVERIFIED. `project_portfolio.py` has 0 regex / 0 dynamic — heavy refactor on this branch (362 changed lines).
- **Priority**: P2.

### 2.4 Recruiter email extraction — 3-tier (`cv_tailor.py` or co-located)
- **Current**: discard / generic_hr / recruiter classification.
- **Target**: embedding similarity vs canonical recruiter-vs-noreply anchors, with structural pre-filter (`noreply@`, `careers@`).
- **Status**: UNVERIFIED.
- **Priority**: P3 — affects Notion column, not application success.

### 2.5 Cover letter dynamic points (`cover_letter_agent.py`, `polish_points_llm()`)
- **Current**: 180-line file, 0 regex / 0 dynamic — likely uses LLM directly. OK in principle.
- **Status**: OK (subject to runtime verification).
- **Priority**: P2.

### 2.6 CV scrutiny calibrator (`cv_templates/scrutiny_calibrator.py`)
- **Current**: 275 lines, 0 regex / 0 dynamic. Probably structural scoring.
- **Status**: UNVERIFIED.
- **Priority**: P3.

---

## Phase 3 — Apply Orchestration (page detect, navigate, SSO, account, verify)

### 3.1 Page-type classification (`page_analysis/classifier.py`)
- **Current**: 478L, 24 embedding refs, 11 regex. Hybrid; the 2026-05-04 plan demotes button-text regex (`_APPLY_BUTTONS`, `_LOGIN_BUTTONS`) to fallback.
- **Status**: IN-FLIGHT (regex still present; embedding signal addition from 2026-04-30 spec **not yet shipped** — `_PAGE_TYPE_ANCHORS` not visible in current file). Verify and complete.
- **Priority**: P1.

### 3.2 Page reasoner (`page_analysis/page_reasoner.py`)
- **Current**: 668L, only 2 embedding refs. Hash-based cache exists; semantic near-miss cache from 2026-04-30 spec **not yet shipped**.
- **Status**: GAP (semantic cache missing).
- **Priority**: P1 — page reasoner is the cognitive shortcut for the navigator; cache misses are expensive ($).

### 3.3 SSO detection (`sso_handler.py`)
- **Current**: 148L, **10 regex / 0 dynamic**. Detects Google / LinkedIn / Microsoft / Apple SSO buttons by literal pattern.
- **Target**: embedding similarity vs SSO-anchor phrases ("sign in with Google", "Continue with LinkedIn"); structural button-role pre-filter.
- **Status**: GAP. Not in prior plans.
- **Priority**: P1 — wrong SSO selection drops the agent into a dead-end auth flow.

### 3.4 Cookie / consent dismisser (called before every page detect)
- **Current**: needs trace. Likely button-label regex.
- **Status**: UNVERIFIED.
- **Priority**: P2 — failure means consent banner blocks the form; usually recoverable.

### 3.5 `find_next_button()` — Submit > Review > Save & Continue > Continue > Next > Proceed
- **Current**: priority list of literal button texts (per CLAUDE.md jobs.md).
- **Target**: embedding similarity vs each canonical action-intent anchor; literal list as fast-exit.
- **Status**: GAP.
- **Priority**: P1 — wrong button = wrong navigation; pipeline stalls.

### 3.6 Stuck detection — fingerprint comparison (per jobs.md, "compare chars 300-700, not first 200")
- **Current**: structural string comparison.
- **Status**: OK (structural, not semantic).
- **Priority**: n/a.

### 3.7 Verification wall detection (`playwright_driver.py:get_snapshot()` + `classifier.py`)
- **Current**: inline JS inspects Cloudflare/Turnstile selectors, text patterns, iframe URLs.
- **Status**: OK (structural — DOM/iframe/text fingerprints, not semantic).
- **Priority**: n/a.

### 3.8 Navigation learner (`navigation_learner.py`)
- **Current**: 267L, 0 regex / 0 dynamic. Stores per-domain navigation traces.
- **Target**: confirm replay match uses embedding similarity (or normalized URL/page-fingerprint structural match — both acceptable).
- **Status**: UNVERIFIED.
- **Priority**: P2.

---

## Phase 4 — Form Fill

### 4.1 Field discovery — `field_scanner.py`, `unified_scanner.py`, `semantic_scanner.py`
- `unified_scanner.py` (882L, 2 regex, 0 dynamic) — main scanner.
- `semantic_scanner.py` (316L, 3 regex, 0 dynamic) — named "semantic" but no embeddings. **Misnomer or unfinished.**
- **Target**: confirm scanner output ranks/labels by embedding similarity to canonical field intents (name / email / cover-letter / resume-upload / etc.). If `semantic_scanner.py` is supposed to be the embedding layer, it needs implementation.
- **Status**: GAP / UNVERIFIED.
- **Priority**: P1 — misidentified field = wrong value submitted.

### 4.2 Field analyzer / resolver (`field_analyzer.py` 455L, `field_resolver.py` 827L)
- `field_analyzer.py`: 4 dynamic refs — appears to use LLM/embedding.
- `field_resolver.py`: 2 regex, 0 dynamic.
- **Status**: UNVERIFIED for field_resolver.
- **Priority**: P1.

### 4.3 Field-to-answer mapping (`field_mapper.py`)
- **Current**: 973L, 3 embedding refs. 2026-04-30 spec calls for `best_semantic_match(label, custom_answer_keys)` after keyword matching + supplemental embedding for `_DIVERSITY_KEYWORDS`.
- **Status**: IN-FLIGHT / partial — embedding fallback ship status unverified.
- **Priority**: P1.

### 4.4 Widget detection / strategy (`widget_detector.py` 254L, `widget_strategies.py` 448L)
- 0 regex / 0 dynamic in both. Likely structural (a11y role + shape detection), no semantic decision.
- **Status**: OK pending verification.
- **Priority**: n/a — structural OK if no label-matching happens here.

### 4.5 Widget LLM recovery (`widget_llm_recovery.py`)
- 234L, 2 dynamic. Uses LLM for unrecoverable widgets.
- **Status**: OK.
- **Priority**: P2.

### 4.6 Intent healing (`intent_healing.py`)
- 182L, 2 dynamic. Heals misclassified field intents.
- **Status**: OK pending verification it heals via LLM/embedding not lookup table.
- **Priority**: P2.

### 4.7 Confidence scorer (`confidence_scorer.py`), validation (`validation.py`), gotchas (`gotchas.py`), vision_gate (`vision_gate.py`)
- All ≤180-275L, 0 regex / 0 dynamic. Likely structural.
- **Status**: UNVERIFIED — confirm none of them embed a semantic decision.
- **Priority**: P3.

### 4.8 Semantic matcher (`form_engine/semantic_matcher.py`)
- **Current**: 238L, 8 embedding refs, 3 regex. 6-tier cascade per 2026-04-30 spec.
- **Status**: OK (graceful) — embedding tier 4 added.
- **Priority**: P1 (currently met).

### 4.9 Option aligner (`screening_option_aligner.py`)
- **Current**: 336L, `_fuzzy_score` bug fixed (line 173 confirmed `min/max * 0.9`). Embedding fallback per 2026-04-30 spec.
- **Status**: OK (graceful).
- **Priority**: P1 (currently met).

### 4.10 Screening detector (`screening_detector.py`)
- **Current**: 153L, 6 embedding refs, 0 regex. Per 2026-04-30 spec, regex `_SCREENING_KEYWORDS` removed and embedding similarity is primary.
- **Status**: OK (verified — 0 regex).
- **Priority**: P1 (met).

### 4.11 Screening intent classifier (`screening_intent.py`)
- **Current**: 396L. Uses `shared.semantic_utils._get_embedder()` (verified). 175 prototypes / 31 intents.
- **Status**: OK.
- **Priority**: P1 (met).

### 4.12 Screening semantic cache (`screening_semantic_cache.py`)
- **Current**: 632L, 16 embedding refs. Shared embedder.
- **Status**: OK.
- **Priority**: P1 (met).

### 4.13 Screening pipeline (`screening_pipeline.py`)
- **Current**: 487L, orchestrates intent classifier → cache → profile resolve → LLM fallback (`cognitive_llm_call`).
- **Status**: OK pending verification that `_agent_rules()` was actually removed (2026-04-30 spec) and `_finalise()` salary detection uses classified intent not keyword.
- **Priority**: P1.

### 4.14 Screening answers (`screening_answers.py`)
- **Current**: 7 regex. Per 2026-05-04 plan, `try_screening_v2()` should fire first; regex iteration is fallback.
- **Status**: IN-FLIGHT.
- **Priority**: P1.

### 4.15 Screening decomposer (`screening_decomposer.py`)
- **Current**: 6 regex. Plan: LLM decomposition, regex as cache key only.
- **Status**: IN-FLIGHT.
- **Priority**: P2.

### 4.16 Consent policy (`consent_policy.py`)
- **Current**: 3 regex, 2 dynamic. Plan: semantic_matcher vs canonical archetypes.
- **Status**: IN-FLIGHT.
- **Priority**: P1 — wrong consent = wrong DEI / marketing answer.

### 4.17 Native form filler (`native_form_filler.py`)
- **Current**: **4470 lines**, 6 regex, 3 dynamic. The single largest pipeline file. Cognitive escalation (`_escalate_fill`) handles `form_recovery` / `form_navigation` domains in-line.
- **Status**: GAP / UNVERIFIED. Needs targeted audit of every semantic branch — option text matching, action selection, error classification, fail/skip routing.
- **Priority**: P1 — this file is the form-fill agent.

---

## Phase 5 — Dry Run / Submit

### 5.1 Correction capture (`correction_capture.py`)
- **Current**: 207L, 0 regex / 0 dynamic. Captures before/after diffs.
- **Target**: confirm before/after comparison is structural (string equality after normalisation), not semantic. If a "rule" is generated from corrections, the rule pattern matching must be embedding/LLM, not literal substring.
- **Status**: UNVERIFIED.
- **Priority**: P1 — feeds AgentRulesDB.

### 5.2 Agent rules (`agent_rules.py`)
- **Current**: 399L, 0 regex / 0 dynamic. Stored rules consumed by NativeFormFiller.
- **Target**: rule lookup mechanism — exact match on field-label / option-text is fragile; needs embedding similarity for "near-miss" retrieval.
- **Status**: UNVERIFIED.
- **Priority**: P1 — broken rule lookup = corrections never reused.

### 5.3 `confirm_application()` outcome classification
- **Current**: needs trace.
- **Status**: UNVERIFIED.
- **Priority**: P2.

---

## Phase 6 — Learning

### 6.1 Strategy reflector / reflexion (`shared/cognitive/_reflexion.py`)
- **Current**: 176L, 2 dynamic. Uses LLM for reflexion.
- **Status**: OK.
- **Priority**: P2.

### 6.2 Strategy synthesis (`ats_adapters/_strategy_synthesis.py`)
- **Current**: 60L, 0 regex / 0 dynamic. Probably structural aggregation.
- **Status**: UNVERIFIED.
- **Priority**: P3.

### 6.3 Optimization signals / aggregator (`shared/optimization/_signals.py` 194L, `_aggregator.py` 388L)
- 0 regex / 0 dynamic in both.
- **Target**: confirm signal classification (which adaptation strategy to apply) is enum/typed not string-matched. Aggregator's signal categorisation is the semantic step to check.
- **Status**: UNVERIFIED.
- **Priority**: P2.

### 6.4 Email pre-classifier (`email_preclassifier.py`)
- **Current**: 3 regex, 4 dynamic. Plan: lower regex confidence threshold; LLM source of truth.
- **Status**: IN-FLIGHT.
- **Priority**: P2.

### 6.5 Rejection analyzer (`rejection_analyzer.py`)
- **Current**: 1 regex, 2 dynamic. Plan: embedding distance vs rejection archetypes (skills / visa / experience / salary).
- **Status**: IN-FLIGHT (well-migrated).
- **Priority**: P3 — analytics.

### 6.6 Followup tracker (`followup_tracker.py`)
- **Current**: 259L, 0 regex / 0 dynamic.
- **Target**: confirm followup categorisation (response / silence / interview / rejection) is LLM/embedding not subject-line keywords.
- **Status**: UNVERIFIED.
- **Priority**: P2.

### 6.7 Scan learning (`scan_learning.py`)
- **Current**: 676L, 0 regex / 2 dynamic.
- **Status**: OK pending verification.
- **Priority**: P3.

### 6.8 Notion sync (`job_notion_sync.py`)
- **Current**: 871L, **6 regex, 0 dynamic**. Heavy modifications on this branch (+126 lines).
- **Target**: any field-mapping or status-classification step that uses regex needs migration. Structural URL / ID parsing OK to keep.
- **Status**: GAP / UNVERIFIED.
- **Priority**: P2.

---

## Phase 7 — Cross-Cutting

### 7.1 NLP intent classifier (`nlp_classifier.py`)
- **Current**: 267L, 32 embedding refs, 0 regex. Shared embedder. 250+ examples / 41 intents.
- **Status**: OK.
- **Priority**: P1 (met).

### 7.2 Dispatcher (`dispatcher.py`) — Telegram
- **Current**: **19 regex still** (highest of any file). Plan: route through `nlp_classifier.classify()` first; numeric arg extraction stays regex.
- **Status**: IN-FLIGHT (least progress in regex-migration plan).
- **Priority**: P2 — affects Telegram UX, not apply pipeline directly.

### 7.3 Swarm dispatcher (`swarm_dispatcher.py`)
- **Current**: 670L, 1 regex, 2 dynamic. Routes via `get_handler_map()` per dual-dispatcher invariant.
- **Status**: OK.
- **Priority**: P1 (met).

### 7.4 Pattern router (`pattern_router.py`)
- **Current**: 8 regex, 2 dynamic. Plan: embedding similarity to pattern archetypes.
- **Status**: IN-FLIGHT.
- **Priority**: P3 — research patterns, not apply flow.

### 7.5 Conversation (`conversation.py`)
- **Current**: 143L, 0 regex / 0 dynamic. Likely uses LLM directly.
- **Status**: UNVERIFIED.
- **Priority**: P3.

### 7.6 Morning briefing (`morning_briefing.py`)
- **Current**: 317L, 0 regex / 0 dynamic.
- **Target**: prioritisation of items in the digest — confirm LLM/embedding-driven not keyword scoring.
- **Status**: UNVERIFIED.
- **Priority**: P3.

### 7.7 Semantic cache (`semantic_cache.py`)
- **Current**: 236L, **15 dynamic refs**, 0 regex. Generic semantic caching layer.
- **Status**: OK.
- **Priority**: P1 (met).

---

## Summary Tally

| Status | Count | Notes |
|---|---|---|
| OK / OK (graceful) | 11 | Mostly the form-fill semantic stack from 2026-04-30 + nlp_classifier + semantic_cache + reflexion. |
| IN-FLIGHT | 9 | Owned by 2026-05-04 regex migration; needs landing + verification. |
| GAP | 8 | `recruiter_screen` (Gate 0), `sso_handler`, `find_next_button`, `page_reasoner` semantic cache, `page_analysis/classifier` embedding signal (un-shipped half of 2026-04-30), `semantic_scanner` (misnomer), `native_form_filler` semantic branches, `job_notion_sync` field-mapping. |
| UNVERIFIED | 18 | Need runtime trace or focused read to classify. |

## Recommended Execution Slices

These are *commit-sized*. Pick one and prosecute end-to-end before opening the next.

1. **Slice A — Land 2026-05-04 in-flight regex migration** (this branch). Verify each of the 9 in-flight files actually fires the embedding/LLM tier first via a smoke test per file. Currently the regex counts say the demotion is partial; we need behavioural proof, not file-existence proof. **No new audit work in this slice.**

2. **Slice B — Ship the un-shipped half of 2026-04-30** — `page_reasoner` semantic cache + `page_analysis/classifier` embedding signal + verify `screening_pipeline._agent_rules()` removal and `_finalise()` salary detection.

3. **Slice C — High-priority GAPs not in any prior plan**:
   - `sso_handler.py` (10 regex; P1)
   - `recruiter_screen.py` Gate 0 (P1)
   - `find_next_button()` button-text priority (P1)

4. **Slice D — Verify the 18 UNVERIFIED items** with targeted reads + 1-line per file in this audit (no code changes). Promotes them to OK or GAP. Reserve a full slice because `native_form_filler.py` alone is 4470 lines.

5. **Slice E — `native_form_filler.py` deep audit**. Single file, dedicated session.

6. **Slice F — Pre-screen + CV/CL semantic decisions** (Gate 1-3 skill matching, role profile selection, project selection). Needs design before code.

## Branch Hygiene

The current branch `pipeline-correctness-fixes` already has 33 modified files (+930/-1255). Do **not** stack the gap fixes on top — land the in-flight migration first, then open a fresh `semantic-audit-slice-X` branch per slice above.

## What This Audit Does Not Do

- Doesn't trace runtime behaviour. File inspection ≠ runtime proof. Every "OK" entry needs a smoke test before it can be claimed met.
- Doesn't measure quality (precision/recall vs golden sets). `tests/jobpulse/test_semantic_quality.py` exists from 2026-04-30 but only covers the 11 components in that spec.
- Doesn't propose a fix for any GAP. That's the next slice.
