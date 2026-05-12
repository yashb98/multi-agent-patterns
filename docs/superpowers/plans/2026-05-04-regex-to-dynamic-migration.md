# Regex → Dynamic Analysis Migration Plan

**Goal:** Migrate the 8 files identified by the regex audit to use embedding similarity / LLM classification / semantic matching instead of regex for semantic classification work. Keep regex for legitimate uses (text normalization, security sanitization, structural format validation, parsing fixed external API responses, number extraction).

**Strategy: graceful migration.** For each file, the dynamic classifier becomes primary; regex becomes a last-resort fallback under low-confidence conditions. We do NOT delete regex outright — we demote it. This avoids regression in cases the dynamic classifier hasn't learned yet, while making the dynamic path the default.

## Priority order (apply-pipeline-critical first)

1. **`screening_answers.py:COMMON_ANSWERS`** — biggest live-fire impact (every screening question routes through this).
2. **`consent_policy.py`** — correctness risk (wrong consent decision = wrong DEI/marketing-opt-in submission).
3. **`screening_decomposer.py`** — compound questions silently drop sub-answers if missed.
4. **`page_analysis/classifier.py`** — page-type detection drives navigation; misclassification stalls the agent.
5. **`email_preclassifier.py`** — email triage; medium urgency.
6. **`rejection_analyzer.py`** — post-apply analytics; lower urgency.
7. **`pattern_router.py`** — research patterns, not apply-flow.
8. **`dispatcher.py`** — Telegram command parsing; risky and large; defer until others land.

## Per-file migration

### 1. `screening_answers.py:get_answer()` — line 555-570

Currently iterates `COMMON_ANSWERS` regex dict; on first match either returns the cached answer or triggers LLM. Migration: call `try_screening_v2()` first (which uses `screening_intent.py` embeddings, 175 prototypes / 31 intents). Demote regex iteration to fallback when V2 confidence < 0.55.

**Test:** existing `test_screening_v2.py` and `test_screening_pipeline_real.py` exercise the path; just need to verify embedding-tier hits more questions than before.

### 2. `consent_policy.py:is_required_consent()` and friends

Currently uses `_DENY_PATTERNS` and `_ALLOW_PATTERNS`. Migration: use `semantic_matcher` to compare label against canonical archetype phrases ("required consent", "marketing opt-in", "newsletter", "terms and conditions"). LLM fallback with `cognitive_llm_call` at low confidence. Keep regex as final fallback.

### 3. `screening_decomposer.py:decompose()`

Currently uses `_COMPOUND_INDICATORS` regex + `_SKILL_LIKE` heuristic. Migration: call LLM with structured prompt asking "is this question compound? if yes, return list of sub-questions, else return empty." Keep heuristic regex as a cheap cache key (short questions skip LLM call entirely).

### 4. `page_analysis/classifier.py`

Currently button-text regex (`_APPLY_BUTTONS`, `_LOGIN_BUTTONS`, etc.). Migration: replace each regex match with embedding similarity against canonical button intents. The DOM-based features (has_form, dialog_present, etc.) stay — those ARE structural. The `*_BUTTONS` regex pattern matching is what migrates.

### 5. `email_preclassifier.py:classify()`

Currently regex on subject + body to flag REJECTED/SELECTED. Migration: lower the regex confidence threshold drastically; always pass to LLM unless strong signal (e.g. obvious rejection keywords AND the LLM cost is high — preserve the cheap-tier optimization but make LLM the source of truth).

### 6. `rejection_analyzer.py`

Currently `re.search(pattern, r)` against blocker pattern library. Migration: embedding distance from each rejection reason to canonical archetypes ("skills gap", "visa", "experience", "salary"). Store learned patterns in `OptimizationEngine`.

### 7. `pattern_router.py`

5 regex patterns route research queries. Migration: embedding similarity to pattern archetypes (debate, hierarchical, plan-and-execute, peer_debate, dynamic_swarm).

### 8. `dispatcher.py`

10+ regex patterns parse Telegram commands. Migration: route through `nlp_classifier.classify()` first (already has 41 intents). Numeric argument extraction stays regex (legitimate structural). The intent-detection patterns demote to fallback.

## Test strategy

Per-file, after each edit:
- `python -m pytest tests/jobpulse/<corresponding_test_file> -x -q`
- For files without tests, smoke-test imports + a single representative call.

Halt migration and reconsider if any test fails or any pipeline-critical path breaks.

## Rollback plan

Each migration is a single file edit. If a downstream issue surfaces, revert the file via git. The graceful-migration pattern (regex preserved as fallback) means partial reverts are safe.

## What we keep regex for (per shared.md / pii-policy.md / seven-principles.md)

- Text normalization: `re.sub(r"\s+", " ", x)` — whitespace, punctuation, case
- Security sanitization: `prompt_defense.py` injection-tag stripping
- Structural format validation: email/phone/date/URL/ID
- Number extraction from known formats: salary ranges, "X+ years"
- Parsing fixed external API responses: Notion errors, GitHub trending HTML
- ATS platform URL detection: structural URL pattern matching
