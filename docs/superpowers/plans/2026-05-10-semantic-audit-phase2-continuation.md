# Continuation Plan — Semantic-Analysis Audit Phase 2

**Predecessor**: `docs/audits/2026-05-10-semantic-audit-verified.md` (this session, Confidence ~28%).
**Branch**: `pipeline-correctness-fixes` @ `765cf23`.
**Why a continuation plan exists**: SG3 (cross-ATS coverage) requires 11 adapters; this session validated 1 fully (Greenhouse via Anthropic mining + Graphcore live partial), 0 fully on Lever / Ashby / SmartRecruiters / iCIMS / Reed / LinkedIn / Indeed / Workday / Generic / Oracle Cloud. Plus, Slice S6 (title+company extractor) is a **prerequisite** to running the remaining adapters cleanly — without it, every non-Greenhouse URL pre-screen-rejects on `Unknown Company`.

## Goal-met definition (unchanged)

> Every semantic decision the JobPulse pipeline makes is correct for the candidate's profile and the JD's context, on every live apply across every active ATS adapter, with no hardcoded fallbacks.

## Distance to goal at end of session 1

| SG | Distance | What advanced |
|---|---|---|
| 1 | ~10% | Cache key inspected — confirmed profile/JD-blind (TP-1, TP-15). CV+CL caches verified profile+JD-aware via PK. |
| 2 | ~25% | 4 LLM call sites + cache key + page reasoner classified. BGE-M3 silent-fallback observed live (TP-17). |
| 3 | ~9% (1 of 11) | Greenhouse Anthropic + Graphcore (partial). |
| 4 | ~15% | Anthropic mined deeply; Graphcore mid-run mined for drift detection + ethnicity miss. |
| 5 | ~30% | OPRAL discipline preserved across 4 URL runs this session (no fixes attempted, all errors documented as slices). |

**Composite ~18-28%** depending on weighting. <100% = goal not met.

## Why this is "blocked-with-plan" not "audit finished"

Per audit prompt: *"Do NOT stop because 'the audit is finished' while gaps remain. Distance to the goal is the only reason to stop, in either direction."*

This session stopped because:
1. Slice S6 is a hard pre-req for the next 9 adapters (otherwise `Unknown Company` blocks every run).
2. The 4-hour real-time guardrail is approaching with substantial remaining work.
3. Three slices (S1, S6, S10) are all P1 and are all read-only-discoverable; further URL runs will surface duplicates of the same gaps, not new ones, until the upstream slices land.

The right next move is to **land Slices S1, S6, S10 first**, then re-fire the audit prompt for the remaining 9 adapters. Running more URLs *now* surfaces the same Unknown-Company / cache-blindness GAPs without producing new evidence.

## Phase 2 plan (next session)

### Phase 2A — Land the four pre-requisite slices (separate branches, NOT stacked)

Each ships independently. Order matters:

1. **Slice S6** (title+company extractor) — without it, every non-Greenhouse URL pre-screen-rejects on `Unknown Company`. Single branch `audit-slice-s6-title-extractor`.
2. **Slice S10** (BGE-M3 loud-fail) — observability blocker. Single branch `audit-slice-s10-bgem3-loud-fail`.
3. **Slice S12** (silent field-drop invariant in NativeFormFiller) — without it, every Phase 2B run can claim success on forms with unfilled required fields. Single branch `audit-slice-s12-fill-loop-invariant`.
4. **Slice S1** (cache key with profile_state_hash + jd_context_hash) — biggest correctness lift but highest risk. Single branch `audit-slice-s1-cache-key`.

Each slice's acceptance includes the 26-URL matrix evidence — so landing S1 alone advances SG3 to ~91%.

### Phase 2B — Run remaining 9 adapters

Pre-condition: Phase 2A landed.

Per-adapter run order (matching `url-coverage-matrix.md` traversal):

| Step | Adapter | Representative URL | What to validate |
|---|---|---|---|
| 1 | Lever | `jobs.lever.co/binance/f664ce6d-…` | TP-1 (cache key with new profile/JD hashes), TP-7 (option aligner), TP-15 (per-company mapping) |
| 2 | Lever (US-coded if available) | re-test Lever Palantir post-S6 — if JD location is US, capture SG1 cross-context | TP-1 (different cache entry vs UK Lever) |
| 3 | Ashby | `jobs.ashbyhq.com/openai/fc5bbc77` | Same set + SmartRecruiters comparison |
| 4 | SmartRecruiters | `jobs.smartrecruiters.com/BoschGroup/744000125446259` | Shadow DOM `spl-*` exercises field_scanner specifically |
| 5 | iCIMS | `careers.icims.com/careers-home/jobs/6309` | iframe-based forms |
| 6 | Reed | `www.reed.co.uk/jobs/data-scientist/56844592` | Modal CV upload pattern |
| 7 | LinkedIn Easy Apply | `www.linkedin.com/jobs/view/4409696246` | Auth-walled — if SSO blocks, document |
| 8 | Indeed | `uk.indeed.com/?vjk=…` | Indeed→ATS handoff |
| 9 | Oracle Cloud HCM | `eoja.fa.ap1.oraclecloud.com/…` | No adapter; Generic fallback |
| 10 | Workday | `gresearch.wd103.myworkdayjobs.com/…` | Multi-tenant variance |
| 11 | Generic | `footballradar.hire.trakstar.com/…` | Fallback specifics |

Per-URL acceptance:
- `apply_job(url, dry_run=True)` reaches `confirm_application` (or documents why it can't).
- `db_observability_summary --window-days 1` exit 0.
- TP-1, TP-7, TP-15, TP-17 four-question correctness check applied per touched touchpoint.
- Any new ATS-specific GAP becomes its own slice (no bundling).

### Phase 2C — Profile-driven worldwide multi-region comparison

Pre-condition: Phase 2B has URLs from **at least 5 distinct regions** (broadened from the prompt's UK+US minimum to a worldwide region grid).

Region grid (audit-required minimum):

| Region | Where to source URL | Why this region |
|---|---|---|
| 🇬🇧 UK | already covered (Anthropic, Graphcore) | baseline |
| 🇺🇸 US | matrix's "US-coded" URLs failed live verification — source new ones (Lever / Greenhouse jobs based in NYC, SF, Seattle) | visa "Yes", $ currency, US EEO format |
| 🇪🇺 EU (DE/FR/NL) | Workday EU tenants (e.g. Accenture EU) | visa "Yes", € currency, GDPR-strict consent |
| 🇸🇬 Singapore / 🇮🇳 India / 🇯🇵 Japan | search Lever / Ashby / Workday for APAC-located JDs | regional currency, regional visa rules, language requirements |
| 🇨🇦 Canada / 🇦🇪 UAE / 🇦🇺 Australia | additional regional URLs | regional visa schemes, currency, work-permit framing |

Decisions that MUST differ per region (per `dimensions.md → D9`):
- Visa sponsorship answer per `jd.country × profile.visa_status × profile.work_auth[country]`
- Salary expectation in `jd.location_currency`
- Notice period per regional norms
- Relocation answer per `jd.location × profile.willing_to_relocate`
- Languages list per `jd.required_languages × jd.preferred_languages`
- DEI question format per regional census/EEO standard

Decisions that MUST stay constant across regions (same profile):
- Skills list, role-archetype, identity (name/email/GitHub/LinkedIn), CV bullet metrics

Acceptance:
- Cache produces **N distinct `key_hash` entries** for the same visa-sponsorship question across N regions, each with the right answer per the table above.
- For each constant-decision (skills, identity), cache produces a single shared entry across all regions.
- `db_observability.lookups` shows the per-region split with distinct hashes.
- One profile-state-change test (e.g. bump `profile.visa_status` from "Graduate Visa" to "ILR") triggers a re-fetch on the next live run rather than serving the stale cache.

### Phase 2D — Sub-goal 5 closure: per-error slice prosecution

Pre-condition: Phases 2A-2C complete with no new P1 GAPs.

For each remaining UNVERIFIED entry in this audit + any new GAPs found in Phase 2B, write its own slice. Per OPRAL discipline: one error per slice, no bundling.

### Phase 2E — H1 (per-decision audit log) ship

Slice S3 — `data/semantic_decisions.db` + `replay-decisions` runner command. After this lands, all PASS claims in the audit can stop relying on log mining.

## Estimated effort

| Phase | Scope | Time |
|---|---|---|
| 2A.S6 | Title+company extractor, single slice | 4-6 hrs |
| 2A.S10 | BGE-M3 loud-fail, single slice | 2-3 hrs |
| 2A.S1 | Cache-key migration, single slice | 6-10 hrs (cross-cutting) |
| 2B | 11 adapters × 45 min/URL | ~8 hrs |
| 2C | UK+US comparison | 1.5 hrs |
| 2D | UNVERIFIED prosecution | 2-3 hrs |
| 2E | H1 ship | 4-6 hrs |
| **Total** | | **~28-37 hrs** |

That's 4-5 work sessions on top of this one. Audit-as-deliverable model: this audit + a slice plan per Phase = the true scope.

## Risks

1. **BGE-M3 reliability** — the 500 error mid-Graphcore is a moving target. Phase 2 might re-fire flakily. Mitigation: Phase 2A.S10 first.
2. **Slice S1 blast radius** — changing the cache key invalidates every existing cached row. Acceptable (the rows are wrong-context anyway), but downstream consumers may regress on cold-start. Mitigation: A/B test S1 by writing to both cache layouts during a transition window.
3. **Workday + Generic adapter variance** — these are last in the matrix order specifically because they vary most. Plan for at least one re-trace per adapter.

## What the *next session* should do at start

1. Re-verify the 5 audit preconditions (clean tree, BGE-M3 1024-dim, Kimi key, Chrome CDP, live-e2e baseline).
2. Confirm Slices S1 / S6 / S10 are landed (or run them in this session if not).
3. Re-run reindex on `code_intelligence` if S1 introduced any vector schema changes.
4. Re-fire the audit prompt — Phase 2B traversal order picks up at "Lever".

## Where the deliverable lives

This session's audit deliverable is at `docs/audits/2026-05-10-semantic-audit-verified.md`. It is structured to **append** per-adapter sections without re-doing TP-1-through-TP-18. Phase 2B should add per-adapter subsections under "Cross-ATS findings".
