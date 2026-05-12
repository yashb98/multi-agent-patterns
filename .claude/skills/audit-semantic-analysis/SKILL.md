---
name: audit-semantic-analysis
description: Use when reviewing a JobPulse pipeline component, phase, or feature for semantic-analysis correctness — checking that intent routing, option matching, page typing, screening Q&A, consent inference, classification, skill mapping, or any meaning-interpretation step uses embeddings / LLM / semantic_matcher rather than regex / keyword lists / hardcoded values.
---

# Audit Semantic Analysis (JobPulse pipeline)

Enumerate every place the pipeline interprets meaning, classify each by mechanism + 8 prompt dimensions + the 72-dimension framework, mark gaps against the binding rules in `.claude/rules/jobpulse.md` + `.claude/rules/shared.md`. Heavy reference (rationalisation tables, applicability matrix, worked examples, AI-agent validation guide): **`dimensions.md`** in this skill directory.

## When to use

- "Is component X semantic?" / "Have we audited Y?"
- Touching a regex-heavy intent / routing file.
- Scoping a fix; need OK / IN-FLIGHT / GAP / UNVERIFIED status per touchpoint.
- New pipeline phase; need to confirm every semantic decision is dynamic before merging.

**Not for:** writing fixes (use a follow-up plan), refactors with no semantic content, rule lookups (read CLAUDE.md directly).

## The Goal (everything below serves this)

**Every semantic decision the pipeline makes is correct for the candidate's profile and the JD's context, on every live apply across every active ATS adapter, with no hardcoded fallbacks.** The audit measures distance to this goal and names the gaps + slices that close them. Work not advancing this goal is out of scope.

**Sub-goals** (each dimension serves one; status = measurement of distance, not a task):

1. Right value for context — `f(profile, JD, page, learned corrections)`; no constants. (rule 4; dims D9/D10/F3)
2. Right mechanism — embedding/LLM/semantic_matcher primary; regex/hardcoded only as graceful fallback. (rule 3; dim D8)
3. Right across every ATS — same correctness on all 11 adapters in the URL matrix. (rule 2)
4. Right per real run — live evidence + correctness, never mocks or static analysis. (rule 1)
5. Right when errors happen — traced to root, fixed surgically, blast-radius validated, learned. (rule 5)

**Goal met when** every touchpoint is `OK` (live + correctness) AND every gap has a scoped slice AND the system handles a never-before-seen ATS / profile / JD autonomously. Completing the audit is the means; correctness on every live run is the end. If you're ticking boxes without advancing a sub-goal, stop and recalibrate.

## Pre-flight

1. Read prior work — do NOT re-list as new:
   - `docs/superpowers/specs/2026-04-30-semantic-analysis-overhaul-design.md` (11 components)
   - `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md` (8 files, in-flight)
   - `docs/audits/2026-05-10-semantic-analysis-pipeline-audit.md` + `2026-05-10-llm-prompt-context-audit.md`
2. Check active sessions: `git status --short` + `ls -lat docs/superpowers/plans/`. Reference any in-flight plan; do not duplicate (e.g. `docs/superpowers/plans/2026-05-10-live-e2e-dry-run.md`).
3. Don't edit code on a branch another session owns.
4. **BGE-M3 reachable + serving 1024-dim** before audit. Test: `curl -sX POST http://localhost:11434/api/embeddings -H "Content-Type: application/json" -d '{"model":"bge-m3:latest","prompt":"x"}' | python3 -c "import json,sys; assert len(json.load(sys.stdin)['embedding'])==1024"`. If fail: STOP — don't run on the silent MiniLM-384 fallback (that fallback IS a sub-goal-2 violation; flag it as a slice, don't benefit from it). See dim A9.

## Five binding rules (apply to every status, every gap, every fix)

Detailed acceptable methods, examples, and the full rationalisation tables are in `dimensions.md`.

### 1. Live-Evidence + Correctness-Validation

A `PASS` requires BOTH halves, both supplied by a real `apply_job(url, dry_run=True)` run on a real public URL:

- **Live evidence** — apply log line / `data/*.db` row / `page.input_value()` DOM readback / Notion page / Drive URL / Telegram message. NOT pytest, NOT mocks, NOT `tmp_path`, NOT replayed snapshots, NOT static analysis.
- **Correctness validation** — answer all four for every PASS: (a) right input given to the decision, (b) right mechanism fired for the input difficulty, (c) right output for *this* JD/profile/page (cross-checked against profile DB / JD content / page DOM / LLM-as-judge with rubric), (d) right downstream consumption. Any "no" = `FAIL`. Any "I can't tell" = `UNVERIFIED`.

Mechanical execution evidence (row exists, log line written, checkbox green) is NOT approval. Read the row, judge the contents. AI agents performing audit work MUST disagree with mechanical PASS claims when the underlying value is wrong.

### 2. Multi-ATS Coverage Matrix (10–15 URLs)

A slice cannot be declared done until 10–15 distinct real URLs covering every active ATS adapter all pass evidence + correctness. Active adapters: **Greenhouse, Lever, Ashby, Workday, LinkedIn (Easy Apply), Indeed, Reed, SmartRecruiters, iCIMS, Generic**. URL-specific or ATS-specific success doesn't count. The curated matrix lives at `docs/audits/url-coverage-matrix.md`.

### 3. Dynamic-Only

Every change MUST be dynamic — resolved at runtime from DOM / a11y / DB / LLM / embeddings. Forbidden: per-ATS hardcoded selectors / option lists, per-form hardcoded answers, per-role hardcoded CV templates, per-company hardcoded DEI, `if ats == "X":` branches reading literal text. Hardcoded fallbacks acceptable only as last-resort defaults — verify the dynamic source was tried first on every URL in the matrix.

### 4. Profile-Driven Decisions

Every value-producing decision = `f(profile, JD, page context, learned corrections)`. Never a static answer. Never a constant. Never a cache that ignores profile/JD changes. The decision is wrong if it would produce the same answer regardless of who the candidate is or what JD they're applying to.

Worked example: visa-sponsorship for a UK candidate on Graduate Visa = `"No"` for a UK job before visa expiry, `"Yes"` for a US job, `"Yes"` for a UK job after visa expiry — three different correct answers from the same profile. Cache key MUST include `profile_state_hash` + `jd_context_hash`. Two identical answers across materially-different JDs from the same profile = `FAIL`. Decision-context table (visa, salary, notice, relocation, DEI, languages, role-profile, projects, etc.): `dimensions.md → D9`.

### 5. Error-Handling & Change-Discipline (OPRAL + blast-radius)

Every error follows this exact sequence — no shortcuts, no bundling:

1. **Observe** — capture full state. Don't suppress.
2. **Trace to core** — walk upstream until the earliest decision whose change would have eliminated the symptom. Downstream patches are symptom-fixes; reject.
3. **Reason** — which learning DB prevents recurrence? (`CorrectionCapture` / `GotchasDB` / `AgentRulesDB` / `NavigationLearner` / `ExperienceMemory`)
4. **Act** — surgical fix scoped to the core. No bundling. No "while we're here" refactors. No `try/except: pass`. No TODO markers.
5. **Validate blast radius** — re-run failing apply, then re-run across the full 10–15 URL coverage matrix. Re-check downstream dimensions via `impact_analysis`. Query DBs the fix should + shouldn't touch. `db_observability_summary` clean. Any regression on any URL → revert + re-trace.
6. **Learn** — verify learning DB row written. Confirm next live run handles autonomously.
7. **Move on** — only after 1–6 are validated. No WIP queue.

## How to advance the goal

The five binding rules ARE the goal-advancement loop. The work below is what those rules look like in practice — applied per touchpoint, in service of the sub-goals, not as a checklist to complete.

1. **Locate** the semantic decisions in scope. Each is a place where one of the five sub-goals could be violated. Skip structural code (DOM walking, mutex, format validation) — those don't violate sub-goals.
2. **Classify** mechanism + applicable dimensions. The classification is a measurement of *current distance to the goal* for that touchpoint, not a label.
3. **Verify per rule 1** — live evidence + four-question correctness check. A `PASS` means *this touchpoint contributes to the goal on a real apply*. `UNVERIFIED` means the goal-distance is unknown; that's a finding, not a task to do later.
4. **Surface gaps loudly**. A gap unfound is worse than a gap declared. The audit's value is in the gaps, not in the OKs.
5. **Propose goal-closing slices** for the highest-priority gaps (P1 first). A slice exists to close a sub-goal, not to "do work". Slice acceptance = the sub-goal is met.
6. **Loop until** every applicable touchpoint advances one of the five sub-goals OR has a scheduled slice. Stop when goal-met OR when genuinely blocked, with a written unblock plan. Never stop "because the audit is finished" — finish the audit only when the goal is met.

Priority is goal-relative: **P1** if the touchpoint can produce a wrong real-apply outcome (sub-goal 1, 2, or 3 violation that hits production); **P2** if it degrades quality (still right, less optimal); **P3** if it affects analytics or non-apply paths only.

## Mechanism taxonomy

| Mechanism | Notes |
|---|---|
| LLM | `cognitive_llm_call` / `smart_llm_call` / `get_llm()`. OK if cached + bounded. |
| Embedding | `shared.semantic_utils.semantic_similarity` / `best_semantic_match` / `_get_embedder`. |
| semantic_matcher | `form_engine/semantic_matcher.py` 6-tier cascade. |
| Hybrid | Structural fast-exit + embedding/LLM. OK ("graceful demotion"). |
| Regex (semantic) | Routing/classification by regex → **VIOLATION** unless demoted to fallback under low-confidence. |
| Hardcoded | Literal dict/list/string-equality routing → **VIOLATION** unless DB-seeded or fast-exit alongside semantic tier. |
| Structural | DOM/a11y/URL/format checks. Out of scope unless embedding semantic intent. |

## Touchpoint entry format

Every entry has all six fields. No prose paragraphs in lieu of fields.

```markdown
### N.M Component (`path/to/file.py:LINE`)
- **Current**: how the decision is made today (mechanism).
- **Target**: what semantic-first looks like.
- **Status**: OK / OK (graceful) / IN-FLIGHT / GAP / UNVERIFIED.
- **Priority**: P1 / P2 / P3.
- **Verify by**: live-run artefact + correctness check (ground-truth join / LLM-as-judge / cross-URL consistency).
- **Prompt audit** (if mechanism = LLM/Hybrid): W1 wrapper, W2 messages, C1 context, C2 truncation, O1 schema, O2 validation, R1 cache, R2 cost+fallback. Detail in `dimensions.md → E`.
- **Dimension matrix**: applicable dims from `dimensions.md` with PASS/FAIL/UNVERIFIED/N/A + evidence pointer + correctness check.
```

## Dimension framework — 73 dims across 12 categories

Heavy reference + applicability matrix in `dimensions.md`.

| Cat | Concern | # |
|---|---|---|
| A | Foundation (models, providers, determinism, embedder singleton, cold-start, BGE-M3 enforcement) | 9 |
| B | Input Hygiene (sanitisation, truncation, PII, multilingual, encoding) | 7 |
| C | Anchors & Prototypes (coverage, versioning, golden tests, real-embedder validation) | 7 |
| D | Mechanism & Threshold (tier order, calibration, OOD, profile+JD context, cache invalidation) | 10 |
| E | Prompt Construction (W1/W2/C1/C2/O1/O2/R1/R2 + few-shot + system role) | 10 |
| F | Caching (mechanism, TTL, key versioning incl. profile+JD, invalidation) | 6 |
| G | Reliability & Fallback (retry, circuit, timeout, structured error, escalation) | 8 |
| H | Observability & Audit (decision log, replay, trace ID, outcome linkage) | 8 |
| I | Learning Loop (capture, routing, consumption, recalibration, A/B) | 8 |
| J | Quality Assurance (live tests, regression, drift, adversarial) | 8 |
| K | Live-Run Verification (real-app log, replay tooling, alerts, decision trace) | 8 |
| L | Cross-Cutting (PII policy, log redaction, cost ceiling, SSRF, reproducibility) | 8 |

For each touchpoint: pick applicable dims (mechanism→category mapping in `dimensions.md`); record `PASS / FAIL / UNVERIFIED / N/A` with live-run evidence + correctness check. Touchpoint status = weakest applicable dim.

## Pipeline phase index

| Phase | Files |
|---|---|
| 1. Pre-Screen | `recruiter_screen.py`, `skill_graph_store.py`, `gate4_quality.py` |
| 2. CV/CL | `cv_tailor.py`, `cv_templates/*`, `cover_letter_agent.py`, `project_portfolio.py` |
| 3. Apply Orchestration | `application_orchestrator.py`, `_navigator.py`, `page_analysis/*`, `sso_handler.py`, `navigation_learner.py` |
| 4. Form Fill | `form_engine/*`, `native_form_filler.py`, `screening_*.py` |
| 5. Dry Run / Submit | `correction_capture.py`, `agent_rules.py`, `confirm_application()` |
| 6. Learning | `shared/cognitive/*`, `shared/optimization/*`, `email_preclassifier.py`, `rejection_analyzer.py`, `followup_tracker.py`, `scan_learning.py`, `job_notion_sync.py` |
| 7. Cross-cutting | `nlp_classifier.py`, `dispatcher.py`, `swarm_dispatcher.py`, `pattern_router.py`, `conversation.py`, `morning_briefing.py`, `semantic_cache.py` |

## Quick survey

```bash
# Mechanism density (screening signal only — confirm by reading the function)
for f in <files>; do
  re=$(grep -c -E "re\.(search|match|findall|compile|sub|finditer|split)" "$f")
  dyn=$(grep -c -E "semantic_similarity|best_semantic_match|MemoryEmbedder|cognitive_llm_call|smart_llm_call|prototype|cosine|_get_embedder" "$f")
  echo "$f: regex=$re dynamic=$dyn"
done

# All LLM call sites
grep -rn -E "cognitive_llm_call|smart_llm_call|ChatOpenAI\(|client\.chat\.completions\.create" jobpulse/ shared/ --include="*.py"

# Direct LLM constructions (potential violations of seven-principles #2)
grep -rn -E "(ChatOpenAI|OpenAI|litellm\.completion)\(" jobpulse/ shared/ --include="*.py" | grep -v "def get_llm"
```

## Red flags — STOP and re-classify

- Status `OK` whose `Verify by` line names a unit test, mocked test, `tmp_path` fixture, or static-analysis result.
- Mechanism = `Regex (semantic)` + status `OK` without graceful-demotion design.
- LLM call site without caching mentioned (every LLM semantic decision should be cached).
- Audit deliverable doesn't reference the prior docs in Pre-flight.
- Slice declared done without all 10–15 URLs producing evidence + correctness for every dimension.
- `PASS` claim that doesn't include the four-question correctness check.
- `if ats == "X"` branch added by a fix.
- Cache key without `profile_state_hash` + `jd_context_hash` for value-producing decisions.
- Two identical answers across materially-different JDs from the same profile.

## Slice rule (goal-closing units, not task units)

Audits enumerate; they don't fix. Propose **goal-closing slices** — each acceptance = "sub-goal X met for component Y across the full ATS matrix", not "code edited". Each slice = one branch, validated end-to-end. Templates (each tied to a sub-goal): land in-flight migration (sub-goal 2); ship un-shipped half of a spec; high-priority GAPs (2–3 P1 entries); verify UNVERIFIED items (sub-goal 4); deep audit one large file; design-first phase. Never stack on an in-flight branch — land first, fresh branch next.

## What this skill does NOT do

- Doesn't write fixes — use a follow-up plan.
- Doesn't measure quality (precision/recall vs golden sets) — that's `tests/jobpulse/test_semantic_quality.py` + dim K8 (real-embedder live tests).
- Doesn't trace runtime behaviour automatically — `OK` requires evidence; this skill flags what's missing.
- Doesn't perform live-run verification on a specific URL — that's a Slice-K execution. Canonical example: `docs/superpowers/plans/2026-05-10-live-e2e-dry-run.md`.

## Reference

- **`dimensions.md`** (heavy ref): 73 dimensions, applicability matrix, all rationalisation tables, decision-context tables, worked examples, AI-agent validation guide.
- `shared/semantic_utils.py` — `_get_embedder()`, `semantic_similarity()`, `best_semantic_match()`, adaptive weights.
- `form_engine/semantic_matcher.py` — 6-tier cascade.
- `tests/jobpulse/test_semantic_quality.py` — golden sets (must be promoted to live per dim K8).
