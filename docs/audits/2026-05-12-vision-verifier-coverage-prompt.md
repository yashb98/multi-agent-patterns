# S26-follow-up-M-2 (P0) — Vision verifier cross-adapter coverage sweep

**Date filed**: 2026-05-12
**Slot**: after M (Playwright `locator.screenshot()`, shipped — `c1d5478`), M-5 (caption/prompt ordinal alignment, shipped — `4469cd4`), M-sidecar (audit clarity, shipped — `f702c86`)
**Status**: SCOPED — ready for next session to execute. The blocker that prevented M's matrix sweep has been fixed in-flight (see "What this slice does NOT include"); the remaining work is mechanical execution of the matrix.

---

## TL;DR

S26-follow-up-M shipped the right architecture (Playwright `ElementHandle.screenshot()` on a dynamically-resolved form-row container) but only proved coverage on **1/11 adapters** in the URL matrix — Greenhouse Graphcore. The plan called for full-pipeline `apply_job(dry_run=True)` coverage across all 11 adapters; M's iteration was blocked at the page-reasoner LLM call site because the multi-provider fallback chain propagated OpenAI's `response_format` kwarg into the Anthropic SDK, which raised `TypeError: Messages.create() got an unexpected keyword argument 'response_format'`.

The blocker has been fixed in-flight (commit `${M_2_COMMIT}` — `_MultiProviderLLM.bind()` now filters OpenAI-only kwargs for non-OpenAI providers). The remaining work for M-2 is purely execution: re-run the URL matrix and confirm:

1. Every adapter's `apply_job(dry_run=True)` reaches the verifier without aborting at page-reasoner
2. Every filled-fillable field per page is captured cleanly in the verifier's composite
3. Every claim row receives a verdict (no `vision_unavailable` from dropped panels)
4. Dedup correctly collapses platform-specific duplicate-bbox patterns (where applicable)

Once these gates pass on ≥ 9/11 adapters (allowing 2 expired/auth-walled URL skips per the original M plan), M-G5 flips from PARTIAL (1/11) to PASS, and M becomes ✅ SHIPPED instead of ⚠️ SHIPPED-PARTIAL.

---

## Three binding requirements (user-stated, 2026-05-12)

These shape every gate, every step, and every code change in this slice:

### Requirement 1 — Cross-check scanner ↔ verifier on every adapter

The verifier "captures all fields" claim must be proven via cross-check against the `field_scanner`'s output on every adapter. The protocol:

1. Run `field_scanner.scan_fields(page)` on the filled form → get the canonical "all fillable fields visible on this page" set.
2. Read each field's rendered DOM value (handling radio/checkbox via `is_checked()`, not `input_value()`).
3. Filter to *actually filled, actually fillable* fields (text/select/combobox/checkbox/textarea/radio-group). Exclude buttons and per-option radios (scanner noise).
4. Pass the same filtered mapping to `verify_form_page()` and inspect the artifact's sidecar JSON.
5. For every (label, value) in step 3, confirm the sidecar's `panels[]` contains an entry whose `original_ordinal` OR `dedup_with` includes that field's claim-row index. Zero misses on the diff.

The harness `/tmp/m_crosscheck.py` (from this session) already does this for one adapter at a time; the M-2 slice extends it to iterate over all 11 adapters in the matrix.

### Requirement 2 — No special-casing per adapter

The original M plan's "no per-platform branches" rule continues. The cross-check harness must work adapter-agnostic — the scanner already abstracts adapter-specific DOM patterns, so the cross-check is just "scanner output → verifier input → sidecar diff", with the same code path for every URL. If an adapter requires a tweak, the tweak goes in the resolver cascade (`_field_crop._resolve_form_row`), never in a `if adapter == "X"` branch.

### Requirement 3 — Honest evidence, no over-claiming

M's audit doc was upfront about which evidence was "full live verifier" vs "Phase-0 probe against unfilled tab" vs "structural resolver evidence only". M-2's audit must preserve this discipline:

- **Strong evidence**: full pipeline `apply_job(dry_run=True)` → form filled → verifier fires → sidecar JSON shows 100 % reachability and 0 `claims_unresolved`. This is what M-G5 demands.
- **Medium evidence**: pipeline filled the form, scanner+cross-check harness was driven directly against the resulting tab (not via the verifier's production code path). Confirms the resolver works on the adapter's DOM but skips the verifier wiring; cite explicitly as such.
- **Weak evidence**: Phase-0 probe against an unfilled tab. Cite only as resolver-DOM-pattern evidence, never as coverage evidence.

Each adapter's evidence rung must be cited per row in the audit doc's per-adapter table.

---

## What this slice does NOT include

- **The page-reasoner `response_format` fix** has already landed in-flight via commit `${M_2_COMMIT}` (`shared/agents.py:_MultiProviderLLM.bind()` filters `response_format` for non-OpenAI providers). No new code in `shared/agents.py` or `jobpulse/page_analysis/page_reasoner.py` is needed for M-2.
- **The vision-verifier architecture** is from M (shipped `c1d5478`). M-2 doesn't touch `vision_verifier.py` or `_field_crop.py` unless an adapter surfaces a resolver pattern not handled by the existing 5-tier cascade — in which case the fix is a new tier in the cascade, not a per-adapter branch.
- **G3 latency budget** is unchanged from L's environmental floor (qwen3-vl hidden reasoning). M-2 doesn't address G3.

---

## The Plan

### Phase 0 — Verify the page-reasoner unblock holds (15 min)

Quick smoke against Lever Mistral or Ashby OpenAI via `process_single_url`. Pre-fix the call aborted at `PageReasoner: action=abort, type=unknown, confidence=0.00 — LLM reasoning failed`. Post-fix the call should reach `Application submitted via playwright (0 today)` or `DRY-RUN STOP (form ready for review)`.

If Phase 0 fails: the bind-filter fix didn't reach the right call site. Trace via `grep -nE "bind\\(response_format" jobpulse/` and confirm every page_reasoner call site goes through `_MultiProviderLLM.bind()`.

### Phase 1 — URL matrix sweep (~3 hours)

Drive `/tmp/m_probe_run.py --all` (or the equivalent for this session). For each adapter in the URL coverage matrix:

1. Run `process_single_url(url, platform=<source>, dry_run=True)`.
2. Capture the M-probe artifact via the `M_PROBE_ARTIFACT_DIR` env var.
3. ALSO run the live verifier against the filled tab (via `/tmp/m_verify_live.py <url-substring>`) so the sidecar JSON gets written under `data/audits/vision_verifier/`.
4. Run `/tmp/m_crosscheck.py` against the same tab to get scanner↔verifier coverage numbers.

Expected per-adapter outputs:
- `data/audits/vision_verifier/<ts>_<domain>_p1_composite.webp` + `_p1.json`
- `data/audits/vision_verifier/m_probe_<run>/<ts>_<adapter>_p1.webp` + `.json`
- Stdout from cross-check: `scan/value/captured/unres/degen/MISS`

Iteration cap: 3 per adapter (DOM patterns that don't fit the resolver cascade are M-3 scope, not M-2 scope; file them and move on).

### Phase 2 — Audit doc evidence table (45 min)

Append a `S26-follow-up-M-2 ✅ SHIPPED` (or `⚠️ SHIPPED-PARTIAL` if < 9/11) section to `docs/audits/2026-05-10-semantic-audit-verified.md`. The per-adapter table format:

| Adapter | URL | Pipeline reached verifier? | Scanner fillable | Verifier captured | Claims unresolved | Dedup pairs | Evidence rung |
|---|---|---|---|---|---|---|---|
| Greenhouse | graphcore/8539033002 | ✅ | 8 | 8 | 0 | 0 | full pipeline |
| Lever | mistral/... | TBD | TBD | TBD | TBD | TBD | full pipeline |
| ... | ... | ... | ... | ... | ... | ... | ... |

Plus an SG2 distance delta update if M-G5 flips to PASS.

### Phase 3 — Commit + close

One commit: `feat(form_engine): S26-follow-up-M-2 SHIPPED — cross-adapter verifier coverage`. Include diff stat, gate verdict, summary of the bind-filter fix that made it possible.

---

## Acceptance gates

- **M2-G1** — Every adapter in the URL matrix reaches the verifier (no page-reasoner aborts). 11/11 or with ≤ 2 documented skips for expired listings.
- **M2-G2** — For every adapter where the pipeline filled at least one form field, the cross-check reports `MISS=0`, `unresolved=0`. Edge-case allowance: `degenerate>0` is acceptable if all are buttons or per-option radios (scanner noise).
- **M2-G3** — For every adapter, the verifier sidecar's `claims_unresolved` is 0. Every claim row receives a verdict via panel match OR dedup_with mapping.
- **M2-G4** — `grep -nE "if (platform\|ats\|domain) ==" jobpulse/form_engine/{vision_verifier,_field_crop}.py` empty on the M-2 diff.
- **M2-G5** — Tests 19/19 in `test_vision_verifier.py`, 245/246 form_engine baseline preserved.
- **M2-G6** — Audit doc table populated for ≥ 9 adapters with the cited evidence rung.

---

## Open questions to resolve in the M-2 session

1. **iCIMS iframes + page-reasoner**: does `process_single_url` successfully reach the verifier on an iframe-embedded form, or does the verifier get called against the outer page only? If the latter, the verifier's iframe iteration (`_resolve_input_locator` → `page.frames`) should find the iframe-embedded inputs — but verify this end-to-end.

2. **LinkedIn Easy Apply auth-wall**: LinkedIn URLs need SSO. Does the dev environment have a valid session? If not, this is a documented skip (expired-listing equivalent).

3. **Workday's React-controlled inputs**: Workday's filler has the most idiosyncratic behavior (5-step form, session timeout). Confirm the resolver cascade resolves React-controlled inputs whose `<input>` element is invisible.

4. **Per-page coverage for multi-page forms**: SmartRecruiters, Workday, iCIMS render multi-page forms. The verifier runs per-page (`page_num=1,2,...`). Cross-check should run on each page, not just page 1.

5. **Generic adapter URLs**: 5 different portals (Trakstar, Dayforce, Talent-community, MLP, Avanade). DOM patterns will vary widely. Expect ≥ 1 to surface a new resolver-tier need; file as M-3 follow-up rather than blocking M-2.

---

## Why this slice was scoped, not just "do M's residuals"

M was scoped as a structural fix (replace bbox-math with locator.screenshot()). It met that bar. M-G5 was carried as PARTIAL because the iteration time-cap hit before all 11 adapters could be cycled, AND because of a structural blocker — the LLM fallback bug — that was outside M's scope to fix.

M-2's job is to drain the queue: with the structural fix in place AND the LLM blocker resolved, the only remaining work is mechanical sweep + evidence collection. The plan doesn't introduce new architecture; it produces the evidence that M-1 promised.

The slice exists separately from M because (a) M's commit history needs to read cleanly ("Phase 1 substitution" → "Phase 2 verification" → "Phase 3 doc"), and (b) the cross-adapter sweep itself is a 3-hour audit task, not a 30-minute code task.

---

## Goal-oriented engineering prompt for the next session

> **GOAL**: For every adapter in the URL coverage matrix, prove via cross-check that the M-era verifier captures 100 % of fields the form-filler filled. Strong evidence (full `apply_job(dry_run=True)` → verifier → sidecar JSON shows `claims_unresolved=0`) on ≥ 9/11 adapters. Append a per-adapter evidence table to the audit doc. Flip M-G5 from PARTIAL to PASS.
>
> **WHY**: M shipped the structural fix (Playwright `ElementHandle.screenshot()`) and proved coverage on Greenhouse (1/11 adapters). The matrix sweep was blocked by an unrelated LLM-stack bug (`response_format` propagation to Anthropic) which has been fixed in-flight via `${M_2_COMMIT}`. Without M-2, every M follow-on slice has to litigate "but did it work everywhere?" on its own.
>
> **INVOKE WITH**: `claude --dangerously-skip-permissions`. Stays on `pipeline-correctness-fixes` branch. Touches `docs/audits/2026-05-10-semantic-audit-verified.md` for the evidence table, no code edits to `vision_verifier.py` or `_field_crop.py` unless a resolver-tier gap surfaces.
>
> **READ FIRST (binding, in order)**:
> 1. `docs/audits/2026-05-12-vision-verifier-coverage-prompt.md` — this file.
> 2. `docs/audits/2026-05-10-semantic-audit-verified.md` — the M section (line ~2117 onwards) for the M-G5 PARTIAL framing M-2 is closing out.
> 3. `docs/audits/2026-05-11-vision-bbox-fix-prompt.md` — the M plan, especially Phase 0 + Phase 2.
> 4. `docs/audits/url-coverage-matrix.md` — the 11-adapter matrix.
> 5. `data/audits/vision_verifier/1778594165_*.json` — the M-5-final Greenhouse evidence (golden sidecar shape: `panels_total=9`, `claims_collapsed_via_dedup=5`, `claims_unresolved=0`, full forensic trace).
> 6. `/tmp/m_crosscheck.py` — the cross-check harness (working as of this session).
> 7. `/tmp/m_probe_run.py` + `/tmp/m_verify_live.py` — dev-phase drivers.
>
> **PRECONDITIONS (halt if any fail)**:
> - Branch `pipeline-correctness-fixes`, clean after the M + M-5 + M-sidecar commits.
> - Daemon NOT running (`python -m jobpulse.runner stop`).
> - Chrome CDP up on `localhost:9222`.
> - `KimiAI_API_KEY` set, `OPENAI_API_KEY` set (for the `gpt-4o-mini` page-reasoner cloud fallback if local LLM fails).
> - `VISION_VERIFICATION_ENABLED=1` for the live-verifier runs.
> - The page-reasoner unblock (commit `${M_2_COMMIT}`) is in the tree.
>
> **PROCESS DISCIPLINE**:
>
> - **Run pipelines in parallel where adapters don't share state** — Greenhouse and Lever live in different tabs; their `process_single_url` calls can interleave. Don't serialize 11 × ~3 min runs back-to-back unless forced to.
> - **Cite evidence rungs precisely** — full-pipeline > medium > weak. Don't paper over weak evidence as "validated".
> - **Iterate on the resolver cascade, not on slice scope** — if an adapter's react-select inputs all `element_fallback`, that's a candidate M-3 follow-up; the resolver still works (no bleed). Don't bundle the M-3 fix into M-2.
> - **Telegram reconfirmation is REMOVED** — dev-phase scaffolding was scoped out of the production verifier path in M. No human-in-loop gates in M-2 either.
> - **Time cap: 4 hours. Iteration cap: 3 per adapter.**
>
> **CONSTRAINTS**:
> - `dry_run=True` only. Never `JOB_AUTOPILOT_AUTO_SUBMIT=true`.
> - Forbidden to introduce `if platform == "X"` branches in `_field_crop.py` or `vision_verifier.py`.
> - Forbidden to add new env vars to gate the verifier path. The M code path IS the only path.
> - Forbidden to mock the LLM stack to "make tests pass" — the M-2 fix is verified by REAL pipeline runs.
>
> **WHAT THIS SLICE EXPLICITLY DOES NOT DO**:
> - Doesn't change the verifier architecture or the resolver cascade.
> - Doesn't change the LLM fallback chain beyond what `${M_2_COMMIT}` already did.
> - Doesn't address L's G3 latency floor (still environmental).
> - Doesn't run live `apply_job(dry_run=False)` — every run stays dry.
>
> **ACCEPTANCE (the SHIPPED state)**:
>
> M-2 ships when M2-G1 through M2-G6 hold on ≥ 9/11 adapters from the matrix. SHIPPED-PARTIAL is acceptable if 2 URLs are documented as expired or auth-walled. The audit doc has the per-adapter evidence table. M's M-G5 PARTIAL framing is updated to PASS in the same audit doc edit.
