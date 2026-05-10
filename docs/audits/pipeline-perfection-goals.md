# Job Application Pipeline — Perfection Goals

> **What this is**: outcome states to achieve, not tasks to do. Tasks live
> in the audit runners (`pipeline-bugs-runner.md`, `cache-or-llm-audit.md`,
> the upcoming `ai-assist-audit.md`). Goals here describe **what "done" looks
> like** for each phase of the pipeline. A goal is met when the verification
> below passes on a live URL, not when a code change ships.

> **What this is NOT**: a session plan. There is no "S1 / S2 / S3" sequencing
> here. Goals are independent — each one can be advanced incrementally by
> any session that touches the relevant phase. The audit runners ARE the
> sessions; this file is the bar each session is trying to clear.

---

## How to read this file

Each goal has four parts:

- **State**: the end condition. Phrased as a present-tense fact (when achieved).
- **Why it matters**: the cost of not having it. Often: an incident, a memory entry, or a recurring failure.
- **Live verification**: the test that proves the goal is met. ALWAYS a live URL run with quoted log/DB evidence. Mocks don't count.
- **Status today**: a one-line read of where we are right now.

Goals are grouped by pipeline phase. The pipeline is the 6-stage flow from
`CLAUDE.md`:

```
JD URL → ① Pre-Screen → ② Materials (CV/CL) → ③ Form Fill →
         ④ Dry-Run Review → ⑤ Submit → ⑥ Learning
```

Plus the cross-cutting infra (memory, cognitive, optimization) that every
phase touches.

---

## ① Pre-Screen — Goals

### G1.1 — Every gate passes or kills with quoted evidence

**State**: Gates 0–4 each emit a structured pass/fail row to
`data/applications.db.gate_effectiveness` and `data/optimization.db.signals`,
with the specific reason in payload. No gate silently passes; no gate
silently kills.

**Why it matters**: Audit S5 found `gate_effectiveness` was empty in
production despite the table being created in S4 — the producer wasn't
firing. Silent passes mean low-quality JDs reach materials generation;
silent kills mean good JDs never apply.

**Live verification**: Run `job-process-url` on (a) a known-blocked JD
(<200 chars, boilerplate) — `gate_effectiveness` shows
`gate=A1, outcome=killed, reason='jd_too_short'`. (b) a known-strong JD —
all 5 gate rows show `outcome=passed`.

**Status today**: ✅ Wired in pipeline-bugs S5 + S9 (gate4_quality
post_apply_hook). Live evidence captured 2026-05-08 for Gate 0–3.
Gate 4B (LLM recruiter review) verification deferred — depends on
non-reasoning Ollama model.

---

### G1.2 — Skill-extraction is deterministic-first, LLM-fallback

**State**: The skill extractor's rule-based path covers ≥95% of JDs by
volume, with a logged miss before any LLM call. Production logs show
`Rule-based extracted N skills, skipping LLM` more often than `falling
back to LLM`.

**Why it matters**: Skill extraction runs on every JD scan. Rule-based is
~50ms; LLM is ~3000ms. At 3 platforms × 200 jobs/day, the difference is
the daemon hitting Telegram/Notion timeouts vs. completing within the
scan window.

**Live verification**: Tail the daemon for one cron tick (~7 PM job-scan).
Count `Rule-based extracted` vs `Rule-based extracted only N skills,
falling back to LLM`. Ratio target: ≥19:1.

**Status today**: ✅ Pattern is correct — `skill_extractor.py` already
does deterministic-first. Goal: copy this pattern to every other LLM
caller in the pipeline (tracked in `cache-or-llm-audit.md`).

---

### G1.3 — Pre-screen rejection is explanatory, not opaque

**State**: When pre-screen kills a JD, the user can see WHICH gate, WHICH
skill/condition, WHICH threshold. No "rejected" without a why. Notion
"Job Tracker" status field reflects the gate that killed the JD, not just
"Rejected".

**Why it matters**: The user can't debug or correct a pre-screen miss
without knowing why. Silent rejection breaks the OPRAL learning loop —
no signal, no rule, no improvement.

**Live verification**: Apply manually to a JD the daemon rejected. If
the user disagrees, the rejection reason in Notion's "Notes" property
should match what the user can argue against.

**Status today**: 🟡 Partial. Gates 0–3 emit rejection reasons to logs.
Notion sync writes `Status=Rejected` but reason field is sometimes blank.

---

## ② Materials (CV / Cover Letter) — Goals

### G2.1 — CV generation is reproducible per JD

**State**: Running `tailor_all_sections()` on the same JD + profile twice
produces identical CV PDFs (same text, same project order, same skills
section). Reproducibility = no LLM noise leaks into the output.

**Why it matters**: A non-deterministic CV makes A/B testing impossible
("did this version perform better, or did it just look different?"). Also
defeats caching — every regeneration is a "novel" output even when the
inputs are identical.

**Live verification**: Generate CV for `https://job-boards.greenhouse.io/anthropic/jobs/<id>`
twice. Diff the PDFs (text-extracted). Should be identical or only
differ in timestamps.

**Status today**: 🔴 Not met. LLM tailoring introduces variance per call.
`cache-or-llm-audit.md` S4 targets a `(jd_hash, profile_version)` cache
that fixes this.

---

### G2.2 — Every CV bullet has a quantified metric or domain term

**State**: 100% of bullets in any generated CV pass the rule
`(contains_number OR contains_domain_keyword OR matches_pattern_verb_object)`.
No "Worked on stuff" bullets.

**Why it matters**: User memory `feedback_experience_formatting` says
this was an explicit correction. Bullets without numbers or domain terms
fail ATS scoring.

**Live verification**: Generate CV for 5 randomly-pulled production JDs.
Run a lint that checks every bullet against the pattern. Zero violations.

**Status today**: 🟡 Partial. CV templates have the structure but LLM
sometimes generates bullets that pass length checks but lack metrics.

---

### G2.3 — Cover letter is structured, not free-form

**State**: Every generated cover letter has 3 paragraphs in this exact
shape: (1) Hook + role + company-specific reason, (2) 2-3 quantified
projects mapped to the JD's must-haves, (3) Closing with availability +
contact info. No 6-paragraph essays, no missing sections.

**Why it matters**: User memory `feedback_hiring_message` was explicit
about cover-letter style. Free-form LLM output drifts toward 6+
paragraphs.

**Live verification**: Generate covers for 3 different roles (data,
software, ML). All three pass a structural lint that counts paragraphs
+ checks for the required elements.

**Status today**: 🟡 Partial. `cover_letter_agent.py` has the prompt
shape but no validator on the output.

---

### G2.4 — PDF filenames + titles are human-readable

**State**: Every generated PDF (CV + CL) has filename
`Yash_Bishnoi_<Company>.pdf` (no `.com` suffix, no UUIDs) and PDF metadata
title `<Role> @ <Company>` (no hash suffix).

**Why it matters**: User memories `feedback_pdf_titles` and
`feedback_cv_filename` are explicit corrections.

**Live verification**: Generate CV+CL for 5 jobs. Check filename pattern
+ PDF metadata via `pdfinfo`.

**Status today**: ✅ Met for the daemon path. Manual scripts may still
emit UUID filenames — to be cleaned in S10c.

---

## ③ Form Fill — Goals

### G3.1 — Every field is filled top-to-bottom, never out-of-order

**State**: For any multi-field form, the fill order matches the visual DOM
order (top-to-bottom). Logs show the order; no out-of-order fills.

**Why it matters**: User memory `feedback_fill_order` was explicit.
Out-of-order fills break ATS validation (some ATSs trigger
field-dependent shows/hides on blur).

**Live verification**: Live dry-run on Greenhouse + Workday. Tail logs
for `nav: filled label=<X>` lines. Order should match the DOM order
visible in Chrome.

**Status today**: ✅ Confirmed in current `field_scanner.py` + `field_mapper.py`
output ordering.

---

### G3.2 — CV is uploaded exactly once per form

**State**: For any form with a CV upload field, the CV file is uploaded
exactly once. Reed's modal-CV-flow + Greenhouse's auto-pre-fill scenarios
do not result in duplicate uploads.

**Why it matters**: User memories `feedback_upload_dedup` and
`feedback_reed_cv_modal` were both explicit corrections about double
uploads.

**Live verification**: Live dry-run on Reed (modal flow) + Greenhouse
(direct flow). Count `set_input_files` log lines per form. Must be 1.

**Status today**: 🟡 Reed's modal handler is wired but the audit's W-12.1
(`_cv_pre_uploaded` flag write-only) tracks an unfixed issue. Pending
S11 verification readback sweep.

---

### G3.3 — No PII appears in source code

**State**: A grep across `jobpulse/`, `shared/`, `scripts/`, `tests/`
returns zero matches for the user's actual name, email, phone, address,
LinkedIn URL, GitHub URL, or DEI answers (verified against
`data/profile.db`). All PII is retrieved at runtime from databases.

**Why it matters**: `.claude/rules/pii-policy.md` mandates this. Source
code commits are forever; PII in git history leaks via forks, CI logs,
contributor visibility.

**Live verification**: A lint test that:
1. Reads PII values from `data/profile.db`
2. Greps the codebase for each value (case-insensitive)
3. Asserts zero matches outside `data/`, `.claude/projects/*/memory/`, and
   `.env`

**Status today**: ✅ As of pipeline-bugs S1-S5. Lint not yet automated —
the assertion is manual today. Should be added to `tests/lint/`.

---

### G3.4 — Every field-fill failure becomes a rule

**State**: When a form field fails to fill, the failure context (label,
attempted value, reason, DOM signature) is captured in
`agent_rules.db` and consulted on the next encounter of the same domain.
Same field doesn't fail twice for the same reason.

**Why it matters**: This is the OPRAL loop's core promise — every error
makes the system smarter. Audit S6 confirmed `CorrectionCapture` →
`AgentRulesDB` is wired. The goal is end-to-end: rule applied automatically
on next visit.

**Live verification**: Live dry-run on a form. Manually inject a fill
failure (e.g., wrong DOM selector). Verify rule appears in `agent_rules.db`.
Run the same form again. The rule should be applied; same failure should
NOT recur.

**Status today**: 🟡 Partial. Rule capture confirmed. Rule consumption
on second visit is intermittent — `auto_generate_from_correction`
sometimes emits `escalate` action that downstream consumers don't read
(audit S5 M-5.2).

---

### G3.5 — Selector discovery is dynamic, not hardcoded

**State**: No hardcoded ATS selectors anywhere except as last-resort
fallbacks. Every selector either: (a) comes from a platform strategy
class, (b) was learned via `FormExperienceDB`, or (c) was discovered via
a11y tree at runtime. Generic CSS-selector blocks remain only for
`platform=generic` fallback.

**Why it matters**: User memory `feedback_dynamic_not_hardcoded` is
explicit. Hardcoded selectors break on the FIRST ATS UI redesign.

**Live verification**: Grep `jobpulse/` for `'button[`,
`'div[data-`, `'.application-` literal selectors. Each match should
be: (a) inside a strategy class returning a list, (b) inside a `# generic
fallback` block, or (c) inside an a11y-tree query. No "load-bearing"
hardcoded selectors.

**Status today**: 🟡 Partial. Pipeline-bugs S10 deleted Workday's inline
hardcode. Greenhouse, Lever, Ashby still have some inline selectors in
NativeFormFiller pending S10b/c.

---

### G3.6 — Screening Q&A always reads cache before LLM

**State**: For any screening question, the lookup order is:
(1) `screening_cache.db` exact match → (2) embedding-similarity match in
the cache → (3) profile DB direct field → (4) LLM with profile context →
(5) LLM with no profile (fallback). Step 4 fires on <20% of questions
in steady state.

**Why it matters**: Screening questions repeat across applications
("Are you legally authorized to work in UK?", "Visa status?", etc.).
Re-asking the LLM each time is the redundant-LLM-call problem.

**Live verification**: Live dry-run on a Greenhouse form with screening
questions. Tail logs for `screening_pipeline:` events. Count cache hits
vs LLM calls. Cache hit ratio target: ≥80% in steady state (after the
profile has been used on a few prior applications).

**Status today**: 🟡 Tier exists but `_align_to_options` re-runs LLM on
cache hits. Targeted by `cache-or-llm-audit.md` S3.

---

### G3.7 — Captcha / SSO walls fall back to human via Telegram

**State**: When any of the 6 bypass stages fails (auto-wait, human-sim,
Turnstile, reload×2), the agent always asks for human help via Telegram
with a cropped screenshot. Never aborts silently.

**Why it matters**: User memory `feedback_captcha_crop` is explicit about
cropped screenshots. Bypass-without-human-fallback was an audit-S5 risk.

**Live verification**: Live dry-run against a known-blocked URL (e.g.,
LinkedIn after rate-limit). After 6 bypass stages, verify Telegram alert
fired with attachment.

**Status today**: ✅ Wired in `_navigator.py:_bypass_verification_wall`
through `platform_bypass.py` + Telegram fallback.

---

## ④ Dry-Run Review — Goals

### G4.1 — Filled-form screenshot reaches Telegram before any submit

**State**: For every application that gets to the pre-submit page, a
screenshot of the fully-filled form is sent to Telegram. Submit blocks
on user's "approve" / "reject" reply. No submit happens without explicit
human approval.

**Why it matters**: User memory `feedback_dry_run_checklist` and
`feedback_final_form_approval` are explicit. The dry-run is the safety
gate that stops bad applications.

**Live verification**: Live dry-run on Workday or Greenhouse. Verify
Telegram receives screenshot. Verify the runner waits for reply before
proceeding (or aborts on `JOB_AUTOPILOT_AUTO_SUBMIT=false` without reply).

**Status today**: ✅ Wired. `apply_live_with_review.py` has the explicit
flow.

---

### G4.2 — Submit is gated by `confirm_application()`

**State**: Every successful submit calls `confirm_application()` exactly
once. Quota counter increments. `post_apply_hook()` fires synchronously.
Status reaches Notion within 30s of submit.

**Why it matters**: `.claude/rules/jobs.md` mandates this. Without
`confirm_application()`, daily quota tracking breaks and learning chains
miss the success signal.

**Live verification**: Apply to one job (real submit, not dry-run). Verify
in `data/applications.db.applications` that `confirmed_at` is set. Verify
Notion page status reaches `Applied`. Verify `data/optimization.db.signals`
has a `success` row from this application.

**Status today**: ✅ Wired. Real submits go through it.

---

### G4.3 — Double-submit is structurally impossible

**State**: A single job_id never produces two `submitted` events. The
`apply_job()` mutex + `application_recorded_before_submit` invariant
prevents duplicate submits even on retry.

**Why it matters**: A double-submit on Greenhouse means a second
identical application, which the recruiter sees as bot behavior.

**Live verification**: Run `apply_job(job_id=X)` twice in parallel via
`asyncio.gather`. Verify only one submission completes; second returns
`already_submitted`.

**Status today**: ✅ Mutex is in place. Race-condition test exists.

---

## ⑤ Submit — Goals

### G5.1 — Rate limits are enforced per-platform

**State**: `apply_job()` checks the rolling-24h count before every
submit. Counts respect `.claude/rules/jobs.md`'s per-platform caps:
LinkedIn 15/day, Greenhouse/Lever 7/day, Indeed 8/day, Workday 5/day,
Reed 7/day, etc. Total cap: 30/day.

**Why it matters**: Exceeding ATS rate caps gets the account flagged or
shadowbanned. The pipeline must self-throttle.

**Live verification**: Force-attempt a 16th LinkedIn application in a
single day (manually queue 16 jobs). The 16th must be rejected with
`rate_limit_exceeded` log message; no Playwright session opens.

**Status today**: ✅ Wired in `apply_job()`. Quota table in
`data/applications.db`.

---

### G5.2 — Session breaks fire on the right cadence

**State**: After 5 LinkedIn applications, the agent waits 30 minutes
before the 6th. This is enforced regardless of how the queue is fed
(manual, cron, Telegram).

**Why it matters**: User memory + `.claude/rules/jobs.md`. LinkedIn
rate-limits sessions, not just users. Breaks reduce captcha rate.

**Live verification**: Queue 6 LinkedIn jobs. After the 5th completes,
the 6th must wait 30 minutes (logged as `LINKEDIN_SESSION_BREAK active
until <time>`).

**Status today**: ✅ Wired. `LINKEDIN_SESSION_CAP=5`,
`SESSION_BREAK_MINUTES=10` (memory says 30 — discrepancy worth
auditing).

---

## ⑥ Learning — Goals

### G6.1 — Every error becomes a memory

**State**: For every exception in the apply path, an `EpisodicEntry` is
written via `record_episode()` with the error context, AND an
`adaptation` signal is emitted to `OptimizationEngine`. No silent
swallows.

**Why it matters**: OPRAL loop's "Learn" step. Audits S6 (B-1, B-2)
caught two silent-swallow bugs that hid for months.

**Live verification**: Inject a known error (e.g., kill Chrome
mid-session). Verify episode appears in `data/agent_memory/memories.db`
with `tier=episodic, payload.weaknesses=[...]`. Verify
`signal_type=adaptation` row in `data/optimization.db.signals`.

**Status today**: ✅ Wired post-S6 + S8. Lint guard from S2 prevents new
silent swallows.

---

### G6.2 — Every successful application improves the agent

**State**: Every `confirmed_at`-set application produces:
(a) one `success` signal in `optimization.db.signals`,
(b) one or more `ProceduralEntry` writes via `learn_procedure()`,
(c) one `EpisodicEntry` write via `record_episode()`,
(d) field-mapping persistence via `_persist_label_mapping()` for any new
labels encountered.

**Why it matters**: An application with no learning artifact is wasted
data. The system can't improve if the run isn't captured.

**Live verification**: Apply to one job. Query each table; expect ≥1
new row dated within 60s of the apply.

**Status today**: ✅ Wired. Field-mapping persistence intermittent —
some labels are learned, some bypass.

---

### G6.3 — Procedural strategies are read-back, not just written

**State**: On every new application, the cognitive engine consults
`get_procedural_entries(domain)` and uses the highest-success strategy
when its score / times-used / success-rate exceed L0_MEMORY thresholds.
No "write but never read" dead loops.

**Why it matters**: Pipeline-bugs S7 fixed the read-side of this:
`get_procedural_entries` now reads SQLite, not just JSON's 100-cap. But
the cognitive engine has to actually USE the result — not just retrieve
it.

**Live verification**: Apply to 3 Greenhouse jobs in a row. By the third,
the cognitive engine should pick `L0_MEMORY` (no LLM call) for at least
the field-mapping decision because the procedural template hits the
strong-template threshold (`times_used>=3 AND avg_score>=8 AND success_rate>=0.8`).

**Status today**: ✅ Wired post-S7 + S8. Behavior change to watch:
L0_MEMORY hit rate should increase substantially in production.

---

## ⑦ Cross-Cutting Infrastructure — Goals

### G7.1 — Memory layer reads source-of-truth, not stale JSON

**State**: For every memory tier (episodic, semantic, procedural,
pattern), reads come from the SQLite source-of-truth via
`MemoryManager.get_*_entries()` or `query()`. The legacy JSON-cap stores
exist only as fallback when `sqlite_store=None` (test fixtures).

**Why it matters**: Pipeline-bugs S7 caught cognitive reading 1/4 of
distinct procedural strategies due to JSON-vs-SQLite asymmetry.

**Live verification**: With the daemon running, query
`SELECT COUNT(*) FROM memories WHERE tier='procedural'` in
`data/agent_memory/memories.db`. Compare to `len(procedural.json)`.
SQLite should be much higher; cognitive consumers should see the SQLite
count.

**Status today**: ✅ Met post-S7. Lint guard prevents regression.

---

### G7.2 — Neo4j edges populate via AutonomousLinker

**State**: For every memory write that succeeds in SQLite + Qdrant, the
linker either creates Neo4j edges to similar existing memories (when
similarity > 0.5) or logs a "no neighbors" miss. `count_edges()` on
production Neo4j is non-zero and grows monotonically.

**Why it matters**: Pipeline-bugs S6 caught zero Neo4j edges in
production. Fixed via SyncService._link_neighbors. But the goal is
ongoing — every new write should keep the graph dense.

**Live verification**: Run `MATCH ()-[r]->() RETURN count(r)` on prod
Neo4j. Should be growing day-over-day. After every 5 `store_memory`
calls, count should increase (no flat plateau).

**Status today**: ✅ Wired post-S6. Production has 173 edges as of
2026-05-08. Monotonic growth not yet verified.

---

### G7.3 — Cognitive engine routes through escalation, not fixed-tier

**State**: For every LLM-needing decision, the cognitive engine's
`classify()` decides L0/L1/L2/L3. Code that bypasses cognitive and
goes straight to LLM is rare, documented, and only for tasks that are
genuinely outside the cognitive engine's domain (e.g., embeddings).

**Why it matters**: The cognitive engine IS the reasoning controller
(see today's "always-reasoning" discussion). Bypassing it sends every
call through the same model regardless of stakes/budget.

**Live verification**: For a single apply, count direct LLM calls vs
calls via `cognitive.think()`. Direct calls should be <3 (likely:
embeddings, OCR, captcha-solve).

**Status today**: 🔴 Many call sites bypass cognitive. Targeted by
`cache-or-llm-audit.md` S7 ("cognitive bypasses").

---

### G7.4 — Optimization engine signals fire on every learning event

**State**: Every learning loop (CorrectionCapture, strategy_reflector,
forgetting sweep, cognitive escalation, A/B test outcomes) emits a
typed signal. Aggregator detects patterns. Policy decides actions.
Tracker measures impact. Trajectory logs sequence.

**Why it matters**: Without signals, the optimization engine is blind.
Without aggregation, patterns are missed. Without measurement, the
"learning" claim is unprovable.

**Live verification**: Apply to one job. Within 1 minute, expect ≥3
new signal rows in `optimization.db.signals` (one each for: post_apply
hook, cognitive outcome if escalated, success/correction). Aggregator
should produce ≥1 `AggregatedInsight`.

**Status today**: ✅ Wired post-S6/S8. Adaptation signal from cognitive
escalation now fires (S8). transfer signal type is producer-only — no
consumer (S10 W-10.1).

---

### G7.5 — Every LLM call site is intentional, not vestigial

**State**: For every `client.chat.completions.create()` call site, the
caller has a documented reason: "synthesis (CV bullet)", "novel question
(screening)", "ambiguous parse (JD analysis)". No "we used LLM here
because that's what we did 6 months ago".

**Why it matters**: Today, ~50-70% of per-apply LLM calls are avoidable
(see today's analysis). They cost money + latency without improving
output.

**Live verification**: A catalog file `docs/audits/cache-llm-catalog.md`
exists with every call site classified. Live applies show LLM call
count drops by ≥50% on the second-and-onward apply to the same domain
(cache hits eliminate the redundant calls).

**Status today**: 🔴 Catalog doesn't exist yet. Tracked by
`cache-or-llm-audit.md` S1.

---

### G7.6 — Cost tracking covers every provider

**State**: `compute_cost_summary()` in every pattern/agent run produces a
breakdown by provider (OpenAI, Anthropic, Voyage, Ollama). Per-call tokens
+ cost are logged. Daily total is queryable via
`data/cost_tracking.db`.

**Why it matters**: The user wants to fully migrate to OSS Ollama. Without
per-provider cost tracking, you can't measure progress (or whether
Ollama-local is actually cheaper after factoring in latency × hardware).

**Live verification**: Run a daily report (`runner cost-summary` or
similar). Expect rows for every provider used in the day. Ollama rows
should have `cost=0.0` but `tokens` populated.

**Status today**: 🟡 OpenAI tracking is solid. Ollama tracking exists but
only logs token counts (cost=0 by definition). Anthropic + Voyage
intermittent.

---

### G7.7 — Forgetting sweep prevents memory growth

**State**: The hourly forgetting sweep evaluates every memory, applies
decay, promotes/demotes lifecycle, tombstones decayed entries. Memory
counts in production stabilize over time, not growing unboundedly.

**Why it matters**: Pipeline-bugs S6 B-1 caught a silent no-op in
forgetting sweep that ran for ~2 months. Production has 22,131 entries
that should-have-been tombstoned.

**Live verification**: Run `mm.run_forgetting_sweep(dry_run=False)` once
(after backup). Verify counts: `len(active) decreases`, `len(tombstoned)
increases`. Run it again the next day. Counts should change again as
new memory ages.

**Status today**: ✅ Sweep wired post-S6. Production cleanup pending —
running it once would shrink the store from 27,827 → ~5,696 entries.
Not done because that's the user's call, not the audit's.

---

## ⑧ Code Quality — Goals

### G8.1 — Every of the 8 engineering principles has a passing lint

**State**: For each of the 8 principles in `.claude/rules/seven-principles.md`,
a lint test exists in `tests/lint/` that catches violations. CI-fail on
new violations.

**Why it matters**: Principles without enforcement drift. The seven-principles
audit (2026-04-20) found 27 violations; some of those would have been
prevented by lint.

**Live verification**: `pytest tests/lint/ -v` returns green on every
commit. Adding a new violation (e.g., a new `from openai import OpenAI`
direct call) breaks CI.

**Status today**: 🟡 Partial. Lints exist for: no-bare-except-pass
(pipeline-bugs S2), no-blocking-sleep, no-classification-regex (S2),
no-write-only-flag (S2). Missing: no-direct-OpenAI, no-hardcoded-PII,
no-N-plus-1-query.

---

### G8.2 — No regex used for classification anywhere

**State**: A grep across `jobpulse/`, `shared/` for `re.match`,
`re.search`, `re.compile` returns ZERO results outside the §8 allowlist
(format validation: emails/phones/dates; security: prompt-defense
sanitization; structural normalization: whitespace). All classification
goes through embeddings, semantic_matcher, LLM, or learned patterns.

**Why it matters**: Regex breaks on input variation (typos, paraphrasing,
i18n). The user has explicit memory `feedback_no_regex_classification`.

**Live verification**: Run the existing
`tests/lint/test_no_classification_regex.py`. Expects zero violations.

**Status today**: 🟡 Partial. Pipeline-bugs S12-S14 will purge remaining
regex from screening, form scanner, and navigator. Today's lint test
allowlists known violations; goal is to drain that allowlist.

---

### G8.3 — Test isolation: zero production-DB writes during tests

**State**: Running `pytest tests/ -v` does not modify any file under
`data/*.db`. Verified via mtime check + row-count snapshot before/after.

**Why it matters**: A 2026-03-25 incident wiped production
`mindgraph.db` via `storage.clear_all()` in a test. Pipeline-bugs S3
fixed `cognitive_outcomes` test leaks via autouse fixture.

**Live verification**: `for f in data/*.db; do mtime_before=$(stat -f %m
$f); done; pytest tests/ -q; for f in data/*.db; do mtime_after=...
done`. All mtimes unchanged.

**Status today**: ✅ Met for tests touching `optimization.db`,
`memories.db`, `applications.db`. Verified post-S3. Other DBs not all
checked.

---

### G8.4 — Every audit row in `pipeline-bugs.md` is closed or explicitly deferred

**State**: The 100+ rows in `pipeline-bugs.md` are each marked: ✅ FIXED
+ commit hash, ⏸ DEFERRED + reason + target session, or 🚫 WONTFIX +
reason. No 🔴 status remains.

**Why it matters**: The audit is the user's contract with the codebase.
Open rows = unmet promises.

**Live verification**: `grep -c '^| 🔴' docs/audits/pipeline-bugs.md`
returns 0. The post-completion verification block in
`pipeline-bugs-runner.md:286` runs green.

**Status today**: 🟡 In progress. As of 2026-05-09: S1–S9 complete, S10
partial, S11–S18 pending. ~70% of rows closed.

---

## ⑨ Operations — Goals

### G9.1 — One job application end-to-end takes <90 seconds

**State**: From `apply_job(url)` invocation to `confirmed_at` write,
median latency is <90s on a Greenhouse form. <120s on Workday (more pages).

**Why it matters**: At 30 jobs/day, slow applications mean the daemon
runs into the next-day's scan window. Throughput matters for daily
quota fulfillment.

**Live verification**: Apply 3 jobs back-to-back. Median time from
`process_single_url:starting` to `confirm_application:complete` <90s
each on Greenhouse. Today's run on Anthropic hung indefinitely on the
qwen3.6 reasoning issue — that's the bottleneck to fix.

**Status today**: 🔴 Currently 5-15min per apply due to LLM issues.
Targeted by `cache-or-llm-audit.md` (reduce LLM calls) +
non-reasoning-Ollama-model migration.

---

### G9.2 — Daemon survives 24h without manual restart

**State**: The daemon runs through a full 24h cycle (3 cron-driven scans,
~30 applications, 1 morning briefing, 1 weekly archive on Sundays)
without a process death, hang, or manual intervention.

**Why it matters**: This is the "production-ready" bar. Manual restarts
are a red flag — they paper over an underlying bug.

**Live verification**: Pick any 24h window. Check
`logs/daemon-stderr.log` and `logs/health.log`. Zero `process died`
lines. Zero `health check restart` events.

**Status today**: 🟡 Partial. Daemon runs but `cron daemon-restart`
fires every 3h (proactive). The restart is healthy preventive
maintenance, not a real death — but distinguishing one from the other is
the goal.

---

### G9.3 — All errors reach the user within 5 minutes

**State**: Any error that requires user attention (captcha, bypass
failure, daemon crash, low ATS score on a high-tier match) reaches the
user via Telegram within 5 minutes of detection.

**Why it matters**: Silent failures destroy trust. The user's mental
model is "the daemon either applies or alerts me — never silently
fails".

**Live verification**: Inject a known error (force a captcha).
Stopwatch from agent's "captcha detected" log line to Telegram message
arrival. Should be <5 min.

**Status today**: ✅ Wired. Telegram alerts fire on captcha, bypass
failure, daemon crash. Low-ATS-on-high-tier alerting is intermittent.

---

## How sessions advance these goals

The goals here are *outcome states*. The audits (`pipeline-bugs-runner.md`,
`cache-or-llm-audit.md`, the upcoming `ai-assist-audit.md`) are the
*sessions* that advance them. The mapping is many-to-many:

| Audit session | Goals advanced |
|---|---|
| pipeline-bugs S1 (doc batch) | G8.4 (audit completeness) |
| pipeline-bugs S2 (lint rules) | G8.1, G8.2 |
| pipeline-bugs S3 (test isolation) | G8.3 |
| pipeline-bugs S4 (dead code) | G8.4 |
| pipeline-bugs S5 (wire-or-delete) | G8.4 |
| pipeline-bugs S6 (linker) | G7.2 |
| pipeline-bugs S7 (memory reads) | G7.1, G6.3 |
| pipeline-bugs S8 (cognitive cost+adaptation) | G7.4 |
| pipeline-bugs S9 (scan_loop) | G6.1 |
| pipeline-bugs S10 (form_engine port) | G3.5, G7.3 (partial) |
| pipeline-bugs S11 (verification readback) | G3.2, G3.4 |
| pipeline-bugs S12-S14 (regex purge) | G8.2 |
| pipeline-bugs S15 (materials cleanup) | G2.1, G9.1 |
| pipeline-bugs S16 (navigator follow-ups) | G3.4, G3.7 |
| pipeline-bugs S17 (log promotion) | G6.1, G8.4 |
| pipeline-bugs S18 (final reconciliation) | G8.4 + all G* end-to-end smoke |
| cache-or-llm S1-S8 | G7.5, G2.1, G3.6, G9.1 |
| ai-assist (proposed) | G3.4, G6.1, G7.3 |

Goals NOT covered by any audit yet (gaps to file new audit rows for):

- G1.3 (rejection explanatory) — file as `pipeline-bugs.md` row
- G2.2 (CV bullet quality lint) — file as `pipeline-bugs.md` row
- G2.3 (cover letter structure validator) — file as `pipeline-bugs.md` row
- G3.4 (rule-applied-on-second-visit verification) — file as
  `ai-assist-audit.md` cluster
- G7.6 (cost tracking per provider) — file as `pipeline-bugs.md` row
- G7.7 (production forgetting sweep — ops decision, not code)
- G8.1 (missing lints) — file as `pipeline-bugs.md` rows
- G9.1 (latency target) — file as `cache-or-llm-audit.md` cluster
- G9.2 (daemon 24h survival) — file as ops monitoring goal, not audit row
- G9.3 (low-ATS alerting) — file as `pipeline-bugs.md` row

---

## What "perfect pipeline" means in practice

When all these goals are met, the production observation looks like:

- A user pushes a job URL. Within 30s, Notion has the analyzed listing
  with all gates' verdicts.
- If passed, materials are generated reproducibly (cache-hit on similar
  past JDs) within 60s.
- Form fill completes top-to-bottom on a real ATS within 90s.
- Telegram receives the dry-run screenshot.
- User approves. Submit fires within 5s of reply.
- Within 60s of submit, Notion shows `Applied`,
  `optimization.db.signals` has the `success` row, `agent_memory` has
  the procedural template captured.
- The agent applied to 30 jobs that day. Tomorrow morning's briefing
  shows: 30 applied, 2 captcha-fallback to user, 0 silent failures, 0
  duplicates, 0 PII-in-source incidents.

That's the bar. The audits get us there one cluster at a time.
