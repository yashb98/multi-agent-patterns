# Job Application Pipeline — End-to-End Architecture

> Every component, every step, every database touchpoint between
> "agent receives a URL" and "application submitted". Grounded in the
> actual code at the time of writing (2026-05-06, branch
> `pipeline-correctness-fixes`).

---

## TL;DR — the 7 phases

```
URL ──▶ ① PRE-SCREEN ──▶ ② MATERIAL GEN ──▶ ③ NAVIGATION ──▶
        ④ FORM FILL ──▶ ⑤ DRY-RUN APPROVAL ──▶ ⑥ SUBMIT ──▶ ⑦ LEARN
```

Each phase is a Python module, has its own state, writes to its own DB,
and emits signals to the next. A single application takes 30 s – 5 min
depending on JD complexity, login flow, and human review time.

---

## ⓪ UPSTREAM — how URLs reach `apply_job`

Before a URL even hits phase ①, two upstream paths feed it in:

**Path A — cron scan loop** (separate pipeline):
1. `job_scanner.scan_all_platforms` runs at 7 AM / 1 PM / 7 PM (LinkedIn,
   Indeed, Reed) + 10 AM / 4:30 PM (quick scan).
2. Per-platform adapters in `platforms/` and platform-specific scanners
   (`linkedin_scanner.py`, `indeed_scanner.py`, `reed_scanner.py`,
   etc.) fetch listings.
3. `liveness_checker.classify` runs on every fetched URL — 12 ghost-job
   patterns × 3 languages (EN/DE/FR). Expired listings are dropped.
4. `job_deduplicator` collapses duplicates: same `(company, title)` =
   one job, even if found on multiple platforms.
5. New unique listings → `applications.db` with `status='Found'` +
   Notion Job Tracker page (`job_notion_sync`).
6. `liveness_checker` re-checks daily; expired jobs flip to
   `status='Expired'`.

**Path B — direct URL** via Telegram or CLI:
- `python -m jobpulse.runner job-process-url <URL>` — runs the same
  apply path on demand.
- Telegram message containing a job URL → `nlp_classifier` →
  `dispatcher.handle_job_url` → same path.

**Notion is the source of truth** (per `CLAUDE.md` memory): the apply
queue reads `Status='Found'` rows from the Notion Job Tracker DB; the
SQLite mirror is fallback only.

`apply_job(url, ...)` then enters phase ①.

---

## ① PRE-SCREEN  —  decide whether to apply at all

**Entry:** `jobpulse/runner.py:241` (`job-apply-next`) →
`jobpulse/applicator.py:241` (`apply_job(url, ...)`) →
`jobpulse/scan_pipeline.py` (`prescreen_listings`).

Five gates run in order. Any "kill" stops the pipeline; the JD is
marked rejected with the kill reason.

| Gate | What it checks | Module | Cost |
|---|---|---|---|
| **0 Title relevance** | Job title vs. user's target roles via embedding similarity. Rejects "Senior PHP Architect" for a Data Engineer. | `recruiter_screen.gate0_title_relevance` | Free (embedding) |
| **1 Kill signals** | JD contains hard blockers (5+ yr seniority, security clearance the user doesn't have, location mismatch). | `skill_graph_store.SkillGraphStore` | Free (rule-based) |
| **2 Must-haves** | All required skills from JD present in user's verified-skills graph. | same | Free |
| **3 Competitiveness** | Top-5 JD skill match ≥ 3 AND ≥ 2 matching projects. Tiered M1/M2/M3. | same | Free |
| **4A JD quality** | Length, signal density, company blocklist (Notion DB). | `gate4_quality.gate4a_jd_quality` | Free |
| **4B CV scrutiny** | Deterministic CV-vs-JD scrutiny + LLM recruiter review (≥7/10 to proceed). | `gate4_quality.gate4b_cv_scrutiny` | ~$0.002 |

**Other modules wired in pre-screen:**
- `jd_analyzer` — parses JD text, detects ATS platform, extracts
  required vs preferred skills.
- `recruiter_screen.gate0_title_relevance` — Gate 0.
- `skill_extractor` — rule-based JD skill extraction (582-entry
  taxonomy with LLM fallback for <10 skills).
- `skill_graph_store.SkillGraphStore` — Gates 1-3, MindGraph
  abstraction over the verified-skills DB.
- `gate4_quality.gate4a_jd_quality` + `gate4b_cv_scrutiny` — Gate 4.
- `company_blocklist` — Notion Company Blocklist DB lookup.
- `cv_templates.scrutiny_calibrator` — adaptive thresholds for the
  CV scrutiny score (calibrates per-company, per-platform).
- `gate_threshold_adapter` — adaptive gate thresholds from
  historical data.

**Scan vs single-URL gate-coverage asymmetry** (`pipeline-bugs.md` S7 W-2):
The cron path (`prescreen_listings`) runs **all five gates** (0 + 1-3 + 4A + 4B).
The single-URL path (`process_single_url`, used by `apply_now.py` and ad-hoc
`job-process-url` invocations) **skips Gate 0 and Gate 4A** and only runs Gates
1-3 + 4B. This means a manually-pasted URL bypasses title-relevance filtering
(may apply to a "Senior PHP Architect" role for a Data Engineer profile) and
the company blocklist + JD-quality screen. `skill_gap_tracker.record_gap` also
only fires on the cron path.

**State written:**
- `data/applications.db` — application row with status `Pending Approval`.
- `data/job_listings.db` (table inside applications.db) — JD details.
- `data/audit.db` — gate decisions + reasons.
- `data/gate_thresholds.db` — adaptive threshold updates.
- `data/cv_scrutiny_calibration.db` — Gate 4B calibration.
- `data/skill_gaps.db` — `skill_gap_tracker` records missing skills (cron path only).

**Information flow out:**
- `JDAnalysis` dict → phase ②
- `MatchTier` (M1/M2/M3) + `MatchedProjects` → phase ②
- `form_hints` (correction accuracy, frequently-corrected fields per
  domain) — `form_prefetch.prefetch_form_hints` → phase ④
- `Reject` → end (logged with reason).

---

## ② MATERIAL GENERATION  —  CV + cover letter PDFs

**Entry:** `jobpulse/scan_pipeline.py:511` (`generate_materials`)
delegates to `jobpulse/application_materials.py`.

**Pre-generation checklist (mandatory order, see `.claude/rules/jobs.md`):**
1. `sync_verified_to_profile()` — pull latest verified skills from
   Notion's Skill Tracker page.
2. Re-run pre-screen with the freshened skill graph.
3. THEN generate the materials.

**Steps:**

1. **Profile sync** — `github_profile_sync` (3 AM cron, but a fresh
   sync is forced if last sync > 24 h).
2. **Project selection** — `cv_tailor.select_projects` ranks projects
   by JD-skill overlap; picks the top-N for inclusion.
3. **Role profile detection** — `archetype_engine.get_role_profile`
   classifies the JD into Data Analyst / Data Engineer / Software
   Engineer / etc. Drives which CV template variant is used and
   which experience-section bullets surface.
4. **CV generation** — `cv_templates/generate_cv.build_cv`. ReportLab
   PDF, 2 pages max, justified text, role-adaptive sections,
   quantified bullets.
5. **Cover letter** — *lazy*: only generated when the form actually
   has a CL field. Stub stored; `cl_generator` callback fires inside
   the form filler when a CL upload widget is detected.
6. **PDF sanitization** — `cv_templates._sanitize_pdf` (PyMuPDF) —
   strip embedded scripts, normalize fonts, set human-readable title.
7. **ATS scoring** — `ats_scorer` against the JD; scores 0-100.
   Score < 85 may get retry with adjusted projects.

**Lazy CV path** (`pipeline-bugs.md` S8 D-1): Two distinct CV-generation paths
exist. `scan_pipeline.generate_materials` (above) generates eagerly when
`ats_score >= 85`. `application_materials.ensure_tailored_cv_for_job` is the
**lazy** path used by `live-review` and `job_autopilot.handle_apply_review` —
it only generates the tailored PDF on first form-fill if `cv_path` isn't yet
on the application row. Both paths share the same generator (`generate_cv_pdf`)
but the lazy path is invoked from the form-fill phase, not pre-screen.

**Two cover-letter generators** (`pipeline-bugs.md` S8 D-2 / W-2): The eager
path (`route_and_apply.cl_generator` inline closure in
`scan_pipeline.py:830-851`) and the lazy path
(`application_materials.build_lazy_cover_letter_generator`) co-exist with
**different argument shapes**. The inline closure skips
`tailor_cover_letter_prose`, so it produces a less-tailored CL than the
live-review path. Drift risk — when changing CL behaviour, update both.

**PDF generation runs before Gate 4B** (`pipeline-bugs.md` S8 D-3): The CV
PDF is rendered in `generate_materials` (line ~681 of `scan_pipeline.py`) when
`ats_score >= 85`, *before* Gate 4B (CV scrutiny) runs at line ~703. If Gate
4B's verdict is "Needs Review" (score 5-6.9), the rendered PDF stays on disk
unused — wastes ~100 ms + a few MB per rejected application. Tracked but not
yet fixed (re-ordering would require Gate 4B to score against a non-PDF
projection of the bundle).

**State written:**
- `data/applications/<Company>/Yash_Bishnoi_<Company>.pdf` — CV
- `data/applications/<Company>/cover_letter_<Company>.pdf` — CL (if generated)
- `data/applications.db` — `cv_path`, `cover_letter_path`,
  `ats_score`, `match_tier`, `matched_projects`.

**Other modules wired in materials:**
- `archetype_engine.get_role_profile` — JD role classifier.
- `cv_tailor.select_projects` + `cv_tailor.build_extra_skills` —
  project ranking + dynamic skill section.
- `portfolio_variants` — per-JD project variant selection.
- `project_portfolio` — project DB + variant generator.
- `github_matcher` — match GitHub commits to JD requirements (used
  for "what have you built with X?" answers).
- `github_profile_sync` — nightly 3 AM cron syncs GitHub →
  MindGraph. Materials phase uses cached data.
- `skill_tracker_notion.sync_verified_to_profile` — pull verified
  skills from Notion Skill Tracker.
- `cv_templates.generate_cv` + `generate_cover_letter` — ReportLab
  PDF generation.
- `ats_scorer` — deterministic 0-100 ATS score.
- `ats_adapters/discovery.detect_ats_platform` — pick the right
  adapter based on URL pattern + DOM.

**Information flow out:**
- `cv_path: Path` → phase ③
- `cover_letter_path: Path | None` → phase ③ (None for lazy CL)
- `agent_mapping: dict[str, str]` (pre-computed answers for known
  fields like first_name, email, phone) → phase ④
- `ats_platform` (greenhouse / lever / workday / ...) → phase ③
  for adapter selection
- `form_hints` (correction accuracy, frequently-corrected fields)
  → phase ④ for adaptive prompt warnings

---

## ③ NAVIGATION — get from the listing URL to the application form

**Entry:** `jobpulse/application_orchestrator_pkg/__init__.py:execute_application`
→ `_navigator.FormNavigator.navigate_to_form`.

The navigator is a **3-phase loop** (Observe → Analyze → Act) running
up to **MAX_NAVIGATION_STEPS = 10** iterations.

```
            ┌──── _phase_observe ────┐
            │  ─ get_snapshot()      │
URL ─▶ goto │  ─ tab/redirect detect │ ─┐
            │  ─ wall detection      │  │
            └────────────────────────┘  │
                       │                 │
                       ▼                 │
            ┌──── _phase_analyze ────┐  │
            │  ─ DOM classifier      │  │
            │  ─ page_reasoner       │  │ loop
            │    → PageAction        │  │ max
            └────────────────────────┘  │ 10×
                       │                 │
                       ▼                 │
            ┌──── _phase_act ────────┐  │
            │  routes by action.act: │  │
            │    fill_form ── exits ─┼──┴──▶  ④ FORM FILL
            │    fill_and_advance ─┐ │
            │    click_apply       │ │
            │    sso_<provider>    │ │ stays
            │    verify_email      │ │ in
            │    dismiss_overlay   │ │ loop
            │    wait_human (wall) │ │
            │    abort             │ │
            └──────────────────────┘ │
```

**Sub-systems wired in `_phase_act`:**

- `cookie_dismisser` — pattern-based cookie banner detection. Always
  runs before page detection.
- `verification_detector` + 6-stage **security wall bypass**:
  1. Auto-wait 15 s (Cloudflare auto-resolves)
  2. Human-simulation (mouse movement, scroll, random delays)
  3. Turnstile checkbox click (iframe entry)
  4. Page reload (`domcontentloaded`)
  5. Page reload (`networkidle`)
  6. **Human fallback via Telegram (mandatory)** — never abort
     without asking
- `platform_bypass` — when aggregators (Indeed/LinkedIn/TotalJobs/Reed)
  block persistently, resolve direct ATS URL via cached mapping →
  FormExperienceDB → known ATS board patterns → Playwright web search.
- `sso_handler` — SSO button detection (Google > LinkedIn >
  Microsoft > Apple). Reasoner emits `sso_<provider>` with
  `target_text`.
- `account_manager` — SQLite credential store, `ATS_ACCOUNT_PASSWORD`
  env var. Used for Greenhouse / Lever / Workday account creation.
- `gmail_verify` — exponential backoff (5 s → 10 s → 30 s → 60 s)
  polling Gmail for verification link, then HTML-parsing the link
  out and visiting it.
- `navigation_learner` — replays per-domain navigation sequences. On
  successful nav, saves the (URL → action → URL) chain.
- **Plan D / F1**: `_phase_act` consults `PageAction.advance_button`
  + `action == 'done'` from `page_reasoner`. No hardcoded button
  lists. Click-apply path also consults `PageAction.target_text` for
  JD-page Apply buttons (F1-2).

**Page reasoner contract (`page_analysis/page_reasoner.py`):**

`PageReasoner.reason_sync(snapshot) → PageAction`:
```python
@dataclass
class PageAction:
    page_understanding: str       # one-line summary
    action: str                   # fill_form | fill_and_advance | done | click_apply | sso_<x> | dismiss_overlay | wait_human | abort
    target_text: str              # button text to click (for click_apply)
    advance_button: str           # button text to click after fill (for fill_and_advance)
    field_fills: list[dict]       # pre-planned text fills the navigator can do directly
    overlays_to_dismiss: list[str]
    expected_outcome: str         # url_changes | fields_filled | dialog_dismissed | page_unchanged
    confidence: float
    page_type: str                # job_description | application_form | login_form | …
    reasoning: str
```

Cached per `(domain, content-hash)` in `data/page_reasoner_cache.db`.
Three guard validators:
- `_apply_zero_fields_guard` — if `fill_form` but page has 0 fields,
  override to `click_element` on the most-Apply-shaped button.
- `_apply_field_count_guard` — if `fill_and_advance` and required
  fields are missing from `field_fills`, downgrade confidence.
- `_apply_advance_button_guard` (Plan D) — if `fill_and_advance` but
  `advance_button` is empty, downgrade confidence to 0 (forces
  re-plan, never lets the consumer click nothing).

**Verification machinery (`_navigator._verify_action`):**
- Pre-action snapshot (URL, content hash, dialog presence,
  field-fill state).
- Execute action.
- Post-action snapshot.
- Compare against `expected_outcome`. If mismatch → ghost click
  detected → `PageReasoner.invalidate(snapshot)` +
  `reason_with_failure(snapshot, failure_context)` for re-grounding.

**State written:**
- `data/page_reasoner_cache.db` — per-domain reasoning cache.
- `data/navigation_sequences.db` — per-domain successful nav chains.
- `data/form_experience.db` — page sequence + container selectors.
- Optimization signals (`OptimizationEngine.emit`) at every tab
  recovery / wall block / ghost-click / submit.

**ATS adapters (`jobpulse/ats_adapters/`):**

15 adapter files implementing `BaseATSAdapter` (`base.py`) +
`PlatformStrategy` (`strategy.py`):
- `playwright_adapter.py` — universal default that everything routes
  through now (post-2026-04 unification). Wraps `playwright_driver`.
- `greenhouse.py`, `lever.py`, `workday.py`, `linkedin.py`,
  `indeed.py`, `ashby.py`, `icims.py`, `smartrecruiters.py` —
  platform-specific quirks: container hints, expected field range,
  screening defaults, label mapping overrides.
- `generic.py` — fallback strategy for unknown ATSes.
- `learned_strategy.py` — synthesizes a strategy from
  FormExperienceDB observations on first visit.
- `discovery.py` — auto-detects which adapter to use from the URL
  domain + DOM signals.
- `_strategy_synthesis.py` — composes strategy from multiple sources.

`PlatformStrategy` ABC declares 17 methods, but only **6 are reachable in the
default apply path** (`pre_fill`, `fill_combobox`, `form_container_hint`,
`expected_field_range`, `extra_label_mappings`, `normalize_label`).
`screening_defaults` was **deliberately removed** (PII policy — answers come
from `ScreeningPipeline`, not the strategy). The remaining methods —
`submit_selectors`, `next_page_selectors`, `post_page`, `known_widget_libraries`,
`apply_button_selectors`, `wait_for_form_hydrated_ms`, `iframe_names`,
`custom_field_scan`, `field_fill_overrides` — are **only consulted via
`form_engine.engine.FormFillEngine`**, which is gated behind
`UNIFIED_FORM_ENGINE=true` and **not enabled in production**. The default
path is `NativeFormFiller`, which never calls them. Tracked in
`pipeline-bugs.md` S12 D-12.1 / D-12.2.

```python
# Reachable in default apply path:
class BasePlatformStrategy:
    def pre_fill(self, page) -> None
    def fill_combobox(self, page, label, value) -> bool
    def form_container_hint(self) -> str
    def expected_field_range(self) -> tuple[int, int]
    def normalize_label(self, label: str) -> str
    def extra_label_mappings(self) -> dict
    # The other 11 methods are FormFillEngine-only (B-tier) or D-tier dead.
```

**Information flow out:**
- `nav_result: dict` with `page_type`, `snapshot`, **`planned_action`**
  (Plan D), `expired`, `error`, `screenshot` → phase ④.
- `ats_platform` resolved → phase ④ for strategy lookup.
- The browser is now sitting on the application form's first page.

---

## ④ FORM FILL — the longest phase

**Entry:** `_form_filler.fill_application` (the legacy path uses
`native_form_filler.NativeFormFiller`; the unified path uses
`form_engine.engine.FormFillEngine` when `UNIFIED_FORM_ENGINE=true`).
Both consume the same `planned_action` from phase ③ (Plan D / F1).

The form filler is itself a **multi-page loop** (up to
**MAX_FORM_PAGES = 20**) per application. Each page goes through
**12 sub-phases**.

```
For each page (1..20):
  ① container resolution
  ② multi-strategy scan_fields
  ③ option discovery (F2)
  ④ noise filter (F4)
  ⑤ field_mapper builds {label: value} mapping
  ⑥ dispatch each field → _fill_by_label
  ⑦ post-fill rescan (catches conditionally-revealed fields)
  ⑧ snapshot live form state (per-page snapshot for correction capture)
  ⑨ pre-submit review (final page only)
  ⑩ click navigation (reasoner advance_button)
  ⑪ verification
  ⑫ → next page or break
```

### ④.1 Container scoping

`field_scanner.resolve_form_container` — 3-tier:
1. **Learned** — `FormExperienceDB.get_container(domain)`
2. **Auto-detect** — JS common-ancestor of form elements with submit
   button check
3. **Strategy hint** — `strategy.form_container_hint()` (platform
   adapter)

Container scoping uses CDP `Accessibility.getPartialAXTree` so the
scan only covers the form subtree (massive noise reduction).

Self-healing: stored selector returning 0 fields → deleted +
re-detected.

### ④.2 Multi-strategy `scan_fields`

`field_scanner.scan_fields` runs **5 strategies in parallel** via
`asyncio.gather` and picks the winner by fillable-field count, then
merges unique fields from runners-up.

| Order | Strategy | What it does |
|---|---|---|
| **0** | `_scan_learned_patterns` (Plan C-3) | Reads `GotchasDB.widget_patterns` for the current domain; emits fields with locator pre-attached. **Strategy 0 — domain knowledge wins.** |
| 1 | `_scan_a11y_tree` | CDP Accessibility tree (pierces shadow DOM, rich metadata) |
| 2 | `_scan_dom_query` | `querySelectorAll` on standard form elements (hydration-resilient) |
| 3 | `scan_fields_locator_fallback` | Playwright `get_by_role` (pierces shadow DOM) |
| 4 | `scan_semantic` (Plan A) | Question text → widget proximity match → classify. Catches custom React widgets the shape detectors miss. |

**Vision augment (Plan B)** runs after the merge **iff** the result is
sparse on a confident form page:
- predicate `should_force_vision(scanner_count ≤ 10, page_type ==
  application_form, reasoner_confidence ≥ 0.7)`
- vision LLM (`gpt-4.1-mini`) gets a screenshot + the existing field
  list; returns missing fields tagged `vision_only=True`.

Hydration retry: if all strategies return 0 fields, wait 2 s, retry
(up to 2 times).

### ④.3 Option discovery (F2)

`_populate_combobox_options` — for every combobox/custom_select/
multiselect/select field with empty options:
1. Click trigger to open
2. Read `[role=option]` / `[role=radio]` / `li[role=option]`
3. Press Escape
4. Cache per `(url, label)` in module-level `_COMBOBOX_OPTION_CACHE`

**Why this matters:** native `<select>` options come for free, but
custom React comboboxes have empty `options` until opened. Without
F2, the screening LLM generated answer "Yes" while the real options
were "Yes - I require sponsorship" → token overlap fails →
`_best_option_match` returns None → field unfilled.

### ④.4 Noise filter (F4)

`_filter_noise_fields` drops:
- `tag in (button, a)` — buttons live in the buttons array
- `label.startswith("_unlabeled_")` — synthetic labels
- `label == placeholder` — labelFor() walker fell back to placeholder
- `is_extension_injected = True` — behavioral feature detection
  (max-int32 z-index, unregistered custom element, shadow-DOM host
  outside form flow). No vendor namespace strings.

### ④.5 Field mapping — `agent_mapping = {label: value}`

`field_mapper.build_mapping`:
- Static profile values → `get_profile()`, `get_address()`,
  `get_profile_links()`.
- Domain-specific overrides → `_load_domain_field_mappings`
  (`field_label_mappings.db`) — per-domain label aliases learned
  from prior corrections.
- `_pre_fill_transform(domain, label, value)` — domain-specific value
  transforms (e.g. phone formatting via `_normalize_phone_value`).
- `_load_heuristics(domain, platform)` — fetches platform/domain
  heuristics and the `_correction_warning` (when domain has < 90 %
  historical correction accuracy, the LLM gets a warning to
  double-check those fields).
- Skill questions → `_extract_skill_for_experience` →
  `SKILL_EXPERIENCE` lookup.
- `_fill_by_element_ids` — direct fill by element ID (for known
  fields like `#first-name`) BEFORE label-based dispatch.
- `_resolve_dropdown_from_profile` — match dropdown options to
  profile values.
- Screening questions → **screening pipeline** (next).
- `cross_platform_field_transfer` — Thompson Sampling decides
  whether to transfer a label→value mapping from another platform
  with a similar field.
- `agent_rules.AgentRulesDB.apply_rules` — apply rules generated
  from past corrections BEFORE the LLM runs (every "user changed X
  to Y" correction becomes a rule).

**Screening pipeline (`screening_pipeline.py`)** — 9 internal modules:

| Module | Role | Wiring (S4 audit) |
|---|---|---|
| `screening_detector` | "Is this a screening question?" — embedding-primary classifier | **D-tier dead** — `is_screening()` has zero production callers. Field-type detection happens upstream in `form_engine`. Documented for ref only. |
| `screening_decomposer` | Splits compound questions ("salary AND notice") into atoms via LLM (regex-gated) | A — invoked by `pipeline.answer` |
| `screening_semantic_cache` | Qdrant + SQLite cache, keyed by question embedding. Single writer for fill/confirm signals via `screening_outcome_recorder`. | A |
| `screening_intent` | Embedding-based intent classification across 31 intents | A |
| `screening_pattern_extractor` | Auto-extracts new screening patterns from observations | A on `observe()`; `extract_patterns` / `find_matching_pattern` are C/D-tier — no production read of the patterns DB. |
| `screening_option_aligner` | Aligns generated answers to one of the offered options (5-tier matcher) | A |
| `screening_validator` | Post-generation validation: length, format, AI-self-reference, profile consistency | A |
| `screening_outcome_recorder` | Single writer for per-question fill + confirmation signals | A |
| `screening_feedback_loop` | Corrections → semantic cache, intent classifier, option mappings, pattern extractor, cross-platform transfer | A |

Resolution order per question (`ScreeningPipeline.answer`):
1. **Empty guard** — return early on blank input
2. **Compound decomposition** — `screening_decomposer.decompose` (LLM-gated by regex pre-filter)
3. **Semantic cache lookup** — `screening_semantic_cache.lookup` (Qdrant first, SQLite-vector fallback, option-aware filtering)
4. **Intent classification** — `screening_intent.classify`
5. **Profile resolution** — `_resolve_intent_from_profile` maps intent → profile field with job-context overrides (salary range, work mode, location)
6. **LLM fallback** — `_llm_answer`. When the field has options, the prompt is option-constrained.
7. **Option alignment** — `screening_option_aligner.align_answer`, plus `BoolFieldHandler` and `SalaryFieldHandler` for type-specific picks
8. **Validation** — `screening_validator.validate` with auto-correct via `_suggest_fix`
9. **Pattern observation** — `_finalise` records the (question, answer, intent, success) tuple for future learning

Audit trail (S4, 2026-05-07): four blockers fixed —
- B-1: `current.*base` regex tightened + `based.*in.*uk|...` pattern deleted (was leaking PII / auto-rejecting UK-based applicants)
- B-2: operator-precedence bug in `_resolve_intent_from_profile` for `WILLING_RELOCATE` with empty profile location
- B-3: missing `_get_qdrant_client()` accessor in `screening_semantic_cache` + broken `shared.embeddings` import in `cross_platform_field_transfer` (silently disabled the cross-platform vector path)
- B-4: `screening_feedback_loop` passed `intent=None` to `PatternExtractor.observe`, silently dropping every correction observation when the intent classifier failed.

The legacy `screening_answers.get_answer` path remains as a regex fallback when V2 confidence is below threshold; migration to embedding-first lives in `docs/superpowers/plans/2026-05-04-regex-to-dynamic-migration.md`.

### ④.6a Specialised pre-dispatch sweeps

Before the per-field `_fill_by_label` loop runs, NativeFormFiller does
**three platform-aware sweeps** that handle whole groups of fields at
once. Each sweep can fill many fields in one pass and removes them
from the dispatch list.

1. **`_fill_radio_groups`** + `_fill_radio_groups_from_scan` —
   detects radio-group widgets (Yes/No, gender, ethnicity) by ARIA
   role and fills via direct DOM click. Faster + more reliable than
   per-radio resolution.
2. **`_fill_toggle_buttons`** — Yes/No toggle button pairs (the
   Revolut visa-sponsorship pattern). Reads the screening pipeline's
   answer, finds the matching button by accessible name, clicks.
3. **`_fill_custom_dropdowns`** + `_click_custom_dropdown_option` —
   Workday-style button dropdowns (`<button id="X" aria-haspopup>`).
   Routes via `_fill_button_dropdown` which clicks → reads
   `[role=option]` → matches → clicks.
4. **`_overwrite_experience_descriptions`** — Workday-specific:
   their pre-parsed experience entries are wrong, so we click "Edit"
   on each role and overwrite the textarea with the structured
   bullets from `ProfileStore.experience()`.

After these sweeps, `_fill_by_label` only handles the remaining
"single text/select/check" fields.

### ④.6 Dispatch — `_fill_by_label(label, value)`

The dispatcher resolution chain, in order:

1. **Special widget short-circuit** — `_fill_special_widget` for the
   phone country picker (W-16; F3-8 will deprecate this).
2. **Semantic / learned-pattern short-circuit** (Plan A-5 + C-3): if
   `_fields_by_label[label]` carries `semantic_match=True` or
   `learned_pattern=True` with selector + widget_type → route to
   `_fill_resolved_widget` (per-widget click dispatcher).
3. **`page.get_by_label(label, exact=False)`**.
4. **`page.get_by_placeholder(label)`**.
5. **`page.get_by_role(role, name=label)`** for combobox/textbox/
   spinbutton (shadow DOM piercer).
6. **`intent_healing.heal_locator`** — re-resolves via a11y snapshot
   + LLM. Recovers from hydration races.
7. **`page.get_by_placeholder(base_label, exact=False)`** as final
   fallback.
8. **`_escalate_fill`** (Plan E + F6) — when 1-7 all fail.

#### `_fill_resolved_widget` — per-widget execution

Routes by `input_type`:

| Widget type | Action | Verification |
|---|---|---|
| `switch` (Plan A-5) | click → re-click if state wrong | `aria-checked` / `aria-pressed` |
| `checkbox` | click iff state ≠ desired | `is_checked()` |
| `combobox` / `custom_select` / `multiselect` / `radio_group` (Plan A-5) | open → scan `[role=option]` → `_best_option_match` → click | option text |
| native `<select>` | `select_option(label=value)` | playwright auto |
| `text` / `textarea` / `number` / `email` / `tel` / `url` (`c0a3796`) | `fill()` → fallback `click()+type()` | `input_value()` |
| `range` / `salary_range` (F3-1) | walk ancestors for sibling `[type=number]` pair, split value on `-`, fill min/max | both readback |
| `rich_text` / `contenteditable` (F3-4) | `click()` + `press_sequentially(value, delay=10)` | `el.innerText` |
| `date_native` / `date` (F3-5) | `_format_date()` → `fill(iso_value)` | `input_value()` |

#### `_escalate_fill` — cognitive fallback (Plan E + F6)

3-attempt retry loop:
1. Compose failure context (label, value, failure tier, visible
   fields summary, visible buttons summary, prior attempts).
2. Call `cognitive_llm_call(domain="form_recovery", stakes="high")`.
3. Parse JSON plan: `{action, selector, widget_type, option_text}`.
4. Execute via `_fill_resolved_widget`.
5. On success: record via `ai_assist_logger.record_fix(dom_signature=…)`
   so next visit hits Strategy 0 (`_scan_learned_patterns`).
6. On failure: append to attempt history, re-prompt engine with the
   history. Cap 3 attempts.

#### Validation-error scanning

`form_engine/validation.py:scan_validation_errors` reads visible
error text after fill (red-bordered fields, `aria-invalid=true`,
inline error labels). Plan F6's planned-but-deferred V-02:
re-prompt engine with the validation error included.

### ④.7 Post-fill rescan

After all fields fill, re-run `_scan_fields`. New fields appearing
indicate **conditionally-revealed** widgets (e.g., selecting "Yes"
to "Sponsorship?" reveals "Which country?"). Loop back to ④.5 for
the new fields.

### ④.8 Per-page snapshot

`_snapshot_live_form_state` — read every visible input's current
value AND DOM signature (Plan C-4). Stored in
`self._per_page_live_snapshots`. Survives mid-flow user edits on
screening pages whose inputs are removed by review time.

**DOM signature emitted per field:**
```python
{label + "__dom": {
    "selector": "#field-id" or 'input[name="x"]',
    "widget_type": "select" | "switch" | …,
    "ancestor_classes": "...",
    "aria_label": "..."
}}
```

### ④.8a Stale-dialog cleanup

Before navigating to next page, `_dismiss_stale_dialogs` checks for
modals that didn't close (e.g. a still-open select dropdown,
confirmation dialog, or success toast). Dismissed via Escape or
explicit close-button click — prevents the next-page click from
hitting "Cancel" on a modal instead of "Continue" on the form.

### ④.8b Pre-submit gate (final page only)

`pre_submit_gate.PreSubmitGate.review(filled_form, jd, expected_values)`
runs on the final page when `_is_submit_page()` returns True AND
domain is **unknown** (no `FormExperienceDB.get_container` row).

The gate:
1. Snapshots every filled field's current value.
2. Sends `(filled_values, jd_summary, expected_values)` to the LLM.
3. LLM returns `{score: 0-10, issues: [...], pass: bool}`.
4. Score < 7 → log warnings + send screenshot to Telegram for human
   review BEFORE clicking Submit.
5. Score ≥ 7 → continue to actual click.

Threshold: `THRESHOLD_OBS: pre_submit_review threshold=7.0`. Logged so
adaptive calibration can adjust.

For known domains, this gate is skipped — `FormExperienceDB` already
captures what works.

### ④.9-12 Navigation + verification

`_click_navigation(dry_run)` (Plan D + F1):
1. Read `self._planned_action.advance_button` from phase ③.
2. Read `self._planned_action.action == 'done'` → submit page?
3. Click via `page.get_by_role("button", name=advance_button,
   exact=True)` then non-exact then link role.
4. If reasoner-named button not on page, fall back to Workday-style
   structural selectors (`button[data-automation-id=…]`) — no
   string lists.
5. Returns `"submitted"` | `"next"` | `"dry_run_stop"` | `""`.

**State written during phase ④:**
- `data/form_experience.db` — container, scan strategy, timing
  (running averages), preferred fill technique per field.
- `data/form_gotchas.db` — domain quirks, **`widget_patterns`** (Plan
  C-3 / F6).
- `data/field_corrections.db` — agent vs final diffs (after submit).
- `data/agent_rules.db` — auto-generated rules from corrections.
- `data/screening_cache.db` — generated answers (cached by question
  embedding).
- `data/ai_assist_sessions.db` + `data/ai_assist.db` — escalation +
  human fix records.
- `data/cognitive_budget.db` — per-call cost tracking.

**Information flow out:**
- `agent_mapping: dict[str, str]` (what the agent filled) → phase ⑤.
- `_per_page_live_snapshots: list[dict]` (with `__dom` keys) → phase ⑤.
- `screening_results: list[dict]` — per-question outcomes → phase ⑤.

---

## ⑤ DRY-RUN APPROVAL  —  the human-in-the-loop gate

**Mandatory** when `JOB_AUTOPILOT_AUTO_SUBMIT=false` (default).

`live_review_applicator._capture_final_mapping_async`:
1. Layer 1: per-page snapshots from `NativeFormFiller`
   (preserves screening-page edits).
2. Layer 2: AI-assist logger fixes pulled in.
3. Layer 3: live read of the current review page.
Result: `final_mapping = {label: value}` (and `__dom` keys for Plan
C-4 capture).

**Approval delivery** via Telegram bot (`approval_request`):
- CV PDF sent.
- Filled-form screenshot.
- Approve / reject buttons.

**Polling:** the agent waits on `data/live_review_active.json` →
removed when the user approves. Timeout: configurable (default 10
min).

If user **rejects**: application marked `Rejected`, no submit.
If user **approves**: phase ⑥.

**Telegram bot infrastructure (`multi_bot_listener.py`):**
- 5 dedicated bots (Main, Budget, Research, Jobs, Alert) — Jobs bot
  handles approval requests for this phase.
- `voice_handler` (Whisper transcription) — voice approval/rejection
  ("approve", "yes go ahead").
- `nlp_classifier` strips trailing `[.!?]+` (Whisper adds
  punctuation) before matching.

**Draft applicator path** (alternative HITL flow):
- `draft_applicator` + `draft_queue` — when `JOB_AUTOPILOT_DRAFT_MODE=true`,
  the agent generates a draft (CV + form fills) WITHOUT opening the
  browser. Drafts queue in `application_drafts.db` for batched human
  review later. Less common path; auto-mode (above) is default.

---

## ⑥ SUBMIT  —  click the actual button

`confirm_application(dry_run_result, url, cv_path, ..., agent_mapping,
final_mapping, ai_meta)` — the only function that records a real
submission.

1. Acquire `_apply_lock` (process-wide mutex).
2. `RateLimiter.record_application` — checks daily caps per platform
   (LinkedIn 15, Greenhouse 7, Indeed 8, etc.).
3. `CorrectionCapture.record_corrections(domain, platform,
   agent_mapping, final_mapping)` — diff agent vs user, store
   per-field corrections in `field_corrections.db`. Emits
   `OptimizationEngine` `correction` signals.
4. `AgentRulesDB.auto_generate_from_correction` — turn each
   correction into a rule so the next visit's
   `field_mapper.apply_agent_rules` applies it before the LLM.
5. **Plan C-4 widget pattern capture**: for every correction whose
   `final_mapping[label + "__dom"]` exists, write to
   `GotchasDB.widget_patterns` keyed by domain. Next visit hits
   Strategy 0 in `scan_fields`.
6. `screening_outcome_recorder.record_confirmation` — record
   per-screening-question feedback (success / corrected) in
   `screening_outcomes.db`.
7. `post_apply_hook(result, job_context)` (next phase).

8. `browser_cleanup.cleanup_chrome_profile_caches()` — between
   applications, expendable Chrome cache dirs are deleted to free
   disk. After every Nth application: `restart_chrome()` to clear
   accumulated tab/memory state.

**State written:**
- `data/applications.db` — status `Applied`, `applied_at` timestamp.
- `data/field_corrections.db` — every diff.
- `data/agent_rules.db` — generated rules.
- `data/form_gotchas.db.widget_patterns` — DOM signatures for
  corrected fields.
- `data/screening_outcomes.db`.
- `data/agent_performance.db` — per-application metrics
  (`claude_corrections_count`, `ai_fixes_count`, `pages_filled`,
  `time_seconds`, `llm_calls`).
- `data/cross_platform_fields.db` — Thompson Sampling updates from
  this application's field outcomes.

---

## ⑦ POST-APPLY + LEARN  —  three concurrent learning chains

`post_apply_hook.post_apply_hook(result, job_context)` fires
non-blocking after every successful submission (auto OR manual). Three
concerns happen in sequence:

### ⑦.1 Form experience persistence
`FormExperienceDB.record(domain, success=True, ...)`:
- Container selector that worked
- Scan strategy that won
- Timing measurements (hydration, fill, transition)
- Field count for next-visit prediction
- Page sequence (multi-page forms)

Success **never** overwrites failure: `success=True` rows are
preserved against later `success=False` rows for the same domain.

### ⑦.2 Drive upload
`drive_uploader.upload_cv(cv_path)` + `upload_cover_letter(cl_path)`
— shareable Google Drive links recorded in the application row so
recruiters can re-download.

### ⑦.3 Notion sync
`update_application_page(notion_page_id, status="Applied", ...)`:
- Status, Applied Date, Resume Drive link, CL Drive link
- Match Tier, ATS Score, Matched Projects
- Recruiter Email (extracted from JD)
- "Needs Review" tag (if Gate 4B scored 5-6.9)

### Two self-adaptation layers fire in parallel

```
post_apply_hook ──┬──▶ ① CorrectionCapture ──▶ AgentRulesDB
                  │                            └─▶ NativeFormFiller consumes
                  │                                next visit
                  │
                  └──▶ ② strategy_reflector ──▶ TrajectoryStore
                                             └─▶ ExperienceMemory (LRU)
```

`CognitiveEngine.flush()` is **not** a third self-adaptation layer — it is a
write-back of queued strategy templates that runs at cron-tick boundaries.
Cognitive escalation runs *in-line during form fill* via
`native_form_filler._escalate_fill` (see ④.6 — domain `form_recovery` /
`form_navigation`), not after submission. Navigator-level cognitive escalation
was removed in the 2026-05-07 audit because no `ThinkResult`→`PageAction`
translator exists; cognitive does not yet emit `adaptation` signals to
`OptimizationEngine` on escalation. Tracked in `pipeline-bugs.md` S6 W-1 and
S3 doc-1/doc-2.

**OptimizationEngine signals emitted:**
- `success` (every submission, plus `form_experience.record` outcomes,
  plus `strategy_reflector` summary on successful applications)
- `correction` (per field corrected via `CorrectionCapture`)
- `adaptation` (per learning DB write — `agent_rules`,
  `auto_rule_generator`)
- `failure` (Gate 4 reject, fill error, ghost click; also emitted
  from `post_apply_hook` when `result.success == False`)
- `score_change` (when ATS score crosses threshold)
- `rollback` (policy auto-revert when a learning action regresses
  metrics)
- `transfer` (cross-domain donor outcomes via
  `PlatformTransferEngine.record_outcome`). Added to
  `VALID_SIGNAL_TYPES` 2026-05-07 — pre-fix the producer raised
  `ValueError` and the optimization signal was silently dropped.
  **No aggregator detector consumes `transfer` yet** — producer fires
  but the pattern-detection rules don't read this type
  (`pipeline-bugs.md` S10 W-10.1).

**Schema-shape note** (`pipeline-bugs.md` S10 D-10.2): `cognitive_outcomes`
rows are stored with `agent_name=<real-agent>` (e.g. `field_mapper`,
`screening_pipeline`), while `forced_level_overrides` is keyed by
`agent_name=<domain>` (e.g. `form_recovery`, `email_classification`). The
read-path mismatch was fixed in audit-S10 B-1 (the L0 fast-path now resolves
the override by domain), but the underlying shape divergence remains —
contributors adding new producers should pass the **agent identity** to
`cognitive_outcomes` and the **domain** to `forced_level_overrides`, never
the reverse.

`post_apply_hook` also wraps itself with
`OptimizationEngine.before_learning_action("post_apply", domain, ...)`
and pairs the call with `after_learning_action(...)` at the bottom.
The `_before` / `_after` dicts carry the four outcome booleans this
hook owns (`drive_cv_uploaded`, `drive_cl_uploaded`,
`notion_updated`, `nav_learned`) plus `elapsed_seconds`. Pre-2026-05
the `_before` snapshot only carried unchanged form-fill metrics, so
`learning_actions.improvement` was always 0; that's now fixed and
the tracker can detect Drive/Notion regressions.

Aggregator (`shared/optimization/_aggregator.py`) detects patterns:
e.g. ≥10 corrections for the same field in one week → emit `failure`
signal → policy raises confidence threshold for that field's auto-fill.
The `adaptation_worked` detector reads `payload["param"]` to label
the insight evidence — every emitter must populate that key, not
just `field` (`agent_rules.auto_generate_from_correction` was the
last hold-out, fixed 2026-05-07).

---

## DATABASE TOUCHPOINT MAP

```
PHASE              DATABASES WRITTEN
─────              ────────────────
① pre-screen   ──▶ applications.db (status), audit.db (gates),
                   gate_thresholds.db (adaptive thresholds)

② materials    ──▶ applications.db (cv_path, cl_path, ats_score),
                   project_selection_outcomes.db (project picks)

③ navigation   ──▶ page_reasoner_cache.db (decisions),
                   navigation_sequences.db (chains),
                   form_experience.db (containers + page seq)

④ form fill    ──▶ form_experience.db (timing, technique),
                   form_gotchas.db (quirks + widget_patterns),
                   screening_cache.db (answers),
                   ai_assist_sessions.db + ai_assist.db (escalation),
                   cognitive_budget.db (LLM costs),
                   form_interactions.db (per-field decisions),
                   field_label_mappings.db (label aliases)

⑤ approval     ──▶ applications.db (status=Pending Approval),
                   live_review_active.json (lock file)

⑥ submit       ──▶ applications.db (status=Applied),
                   field_corrections.db (diffs),
                   agent_rules.db (generated rules),
                   screening_outcomes.db (per-Q feedback),
                   form_gotchas.db.widget_patterns (DOM signatures),
                   trajectory_store.db (action sequence),
                   agent_performance.db (per-application metrics)

⑦ post-apply   ──▶ form_experience.db (success record),
                   experience_memory.db (LRU),
                   optimization.db (signals + aggregations),
                   applications.db (drive links, notion id)
```

51 SQLite files in `data/` total. 27 actively written, 19 wired
but empty (waiting for the right code path to fire), 5 dead/legacy.

---

## INFORMATION FLOW — what gets passed where

```
┌─ apply_job(url, ...) ─────────────────────────────────────────────┐
│                                                                   │
│  url + job_dict                                                   │
│       │                                                           │
│       ▼                                                           │
│  ① pre-screen                                                     │
│       │                                                           │
│       │ JDAnalysis, MatchTier, MatchedProjects                    │
│       ▼                                                           │
│  ② materials                                                      │
│       │                                                           │
│       │ cv_path, cover_letter_path (lazy), agent_mapping          │
│       ▼                                                           │
│  ③ navigation                                                     │
│       │                                                           │
│       │ nav_result {snapshot, page_type, planned_action}          │
│       ▼                                                           │
│  ④ form fill                                                      │
│       │                                                           │
│       │ fill_result {agent_mapping, screening_results,            │
│       │              per_page_snapshots (with __dom keys),        │
│       │              llm_calls, success}                          │
│       ▼                                                           │
│  ⑤ approval (human)                                               │
│       │                                                           │
│       │ final_mapping {label: value, label__dom: {sig...}}        │
│       ▼                                                           │
│  ⑥ submit / confirm_application                                   │
│       │                                                           │
│       │ corrections, agent_rules, widget_patterns                 │
│       ▼                                                           │
│  ⑦ post-apply + learn                                             │
│       │                                                           │
│       │ Drive link, Notion update, signals × 3 chains             │
│       ▼                                                           │
│  return final_result                                              │
└───────────────────────────────────────────────────────────────────┘
```

---

## FAILURE-RECOVERY PATHS (OPRAL loop everywhere)

Every error follows **Observe → Plan → Reason → Act → Learn**. Below
are the actual recovery paths wired today.

| Failure | Detected by | Recovery | Learning DB |
|---|---|---|---|
| Hydration race (0 fields scanned) | `scan_fields` retry loop | 2× retry with 2 s wait | — |
| Stale container selector | `validate_field_scan` 0-fields check | Delete + re-detect | `form_experience.db` |
| Ghost click (URL/content unchanged after action) | `_verify_action` | `PageReasoner.invalidate` + `reason_with_failure` | `page_reasoner_cache.db` |
| Reasoner low confidence (< 0.7) | `_phase_act` | `classify_page_type_from_screenshot` cross-check | `page_reasoner_cache.db` |
| Verification wall | snapshot's `verification_wall` field | 6-stage bypass → human Telegram fallback | `form_experience.db` (block events) |
| Aggregator persistent block | wall bypass exhausted on aggregator domain | `platform_bypass.resolve_ats_url` | `navigation_learner.db`, `gotchas_db`, `optimization.db` |
| SSO required | snapshot has SSO buttons | `sso_handler.click_sso(provider)` | `navigation_sequences.db` |
| Account creation needed | reasoner action `signup` | `account_manager.create_account` + `gmail_verify` | `ats_accounts.db` |
| Field label not found | `_fill_by_label` | role fallback → intent_healing → **`_escalate_fill`** (Plan E + F6) | `ai_assist_sessions.db`, `widget_patterns` |
| Widget can't accept text fill | dispatcher | `_fill_resolved_widget` per-widget handler (Plan A-5 + F3) | — |
| Wrong value | `_classify_fill_failure` | LLM recovery | `screening_cache.db` |
| Submit fails | post-submit page check | mark application failed; emit `failure` signal | `applications.db` |
| Stuck on identical page (multi-page form) | snapshot fingerprint compare | `CognitiveEngine.think(form_navigation, medium)` | `cognitive_budget.db` |
| Validation errors after fill | `validation.scan_validation_errors` | LLM-recovery on the offending field (V-02 in F6 plan, deferred) | `field_corrections.db` |

Telegram human bypass is the **floor** for every recovery path —
the agent never silently abandons. If automated recovery fails, the
user gets a Telegram approval-request with a screenshot.

---

## EXTERNAL INTEGRATIONS

| Integration | Module | Used in phase | Purpose |
|---|---|---|---|
| **OpenAI API** | `shared/agents.py:get_openai_client` | ②, ③, ④, ⑤ | LLM calls (GPT-4o, GPT-4.1-mini for vision, embeddings for screening) |
| **Google Drive** | `drive_uploader.py` | ⑦ | CV/CL shareable links |
| **Notion API** | `job_notion_sync.py`, `notion_agent.py` | ②, ⑦ | JD source, Skill Tracker sync, application tracker, company blocklist |
| **Gmail API** | `gmail_verify.py`, `gmail_agent.py` | ③ | Account verification email polling |
| **Telegram Bot** | `telegram_client.py`, 5 dedicated bots | ⑤, recovery | Approval requests, alerts, voice commands |
| **GitHub API** | `github_profile_sync.py` | ② (3 AM cron) | Verified-skills graph |
| **LinkedIn / Indeed / Reed APIs** | `platforms/*.py` | (cron job-scan) | Listing fetch (not in apply path) |
| **Qdrant** (Docker, port 6333) | `shared/memory_layer/_qdrant_store.py` | ④ | Screening cache vector search |
| **Neo4j** (Docker, port 7687) | `shared/memory_layer/_neo4j_store.py` | ④, ⑦ | A-MEM autonomous memory linking |
| **Voyage 3 Large** (cloud embeddings) | `shared/memory_layer/_embedder.py` | ④, ⑦ | Embeddings (with MiniLM fallback) |
| **Cloudflare / reCAPTCHA / hCaptcha** (verification walls) | `verification_detector.py` | ③ | Detect, then bypass via 6 stages |

---

## CONCURRENCY MODEL

- **Process-level**: `_apply_lock` (`threading.Lock` in
  `applicator.py:23`) — only one `apply_job()` or
  `confirm_application()` runs at a time across the whole daemon.
- **Pipeline-level**: `run_scan_window()` lock — prevents the cron
  scan loop from racing the daemon.
- **Per-domain rate limit**: `RateLimiter.record_application` with
  daily caps + session breaks (every 5 apps for LinkedIn, 10 min
  break).
- **Per-platform mutex**: implicit via the `_apply_lock` above.
- **Inside form filling**: single asyncio event loop; concurrent
  scans use `asyncio.gather` (5 strategies in parallel) but the
  fill loop is sequential per field.
- **OptimizationEngine** signals: emit-only, async-safe (writes go
  through a queue + background flush).

---

## OBSERVABILITY

- **Logs:** every module uses `shared/logging_config.get_logger`.
  Per-agent log files in `logs/`. RotatingFileHandler (5 MB × 5
  backups). Daemon stdout + cron stream to Telegram.
- **Cost tracking:** `shared/cost_tracker.record_openai_usage` /
  `record_llm_usage` on every LLM call. Aggregated per-application
  in `agent_performance.db`.
- **Decision logging:** every gate, every reasoner call, every
  cognitive escalation logs the input → decision → output triple.
- **Optimization signals:** `OptimizationEngine.emit(...)` is the
  unified telemetry channel. Replays available via
  `data/optimization.db`.
- **Trajectory store:** `trajectory_store.db` records every
  application as an action sequence (ShareGPT-JSONL exportable for
  fine-tuning).

---

## KEY ENV VARS

```bash
JOB_AUTOPILOT_AUTO_SUBMIT=false      # default; true = skip phase ⑤ approval
JOB_AUTOPILOT_MAX_DAILY=10           # daily cap across all platforms
LINKEDIN_SESSION_CAP=5               # LinkedIn-specific session break
SESSION_BREAK_MINUTES=10
FAST_FILL=true                       # zero per-field delays (Claude Code)
UNIFIED_FORM_ENGINE=true             # use FormFillEngine instead of NativeFormFiller
COGNITIVE_ENABLED=false              # kill switch for CognitiveEngine
OPTIMIZATION_ENABLED=false           # kill switch for OptimizationEngine
BUTTON_CLASSIFIER_DISABLED=true      # F1 fallback (keep using string lists)
ATS_ACCOUNT_PASSWORD=...             # Greenhouse / Lever / Workday signups
JOB_APPLY_PASSWORD=...               # Greenhouse / Lever / Workday only
```

---

## RECENT ARCHITECTURAL ADDITIONS (this session)

| ID | What | Module | Phase |
|---|---|---|---|
| **B** | Vision-augment gate on sparse + high-confidence form scans | `form_engine/vision_gate.py` | ④.2 |
| **C** | Per-domain learned widget patterns + auto-capture on corrections | `form_engine/gotchas.py:widget_patterns`, `field_scanner._scan_learned_patterns` | ④.2, ⑥ |
| **A** | Semantic-first scanner: question → widget proximity → classify | `form_engine/semantic_scanner.py` | ④.2 |
| **A-5** | Per-widget click dispatcher (`_fill_resolved_widget`) | `native_form_filler.py` | ④.6 |
| **D** | Consume reasoner `advance_button` + `action='done'` instead of hardcoded button-text lists | `native_form_filler._click_navigation`, `_is_submit_page` | ④.10 |
| **E** | Auto-escalate stuck fills to `CognitiveEngine.think(form_recovery, stakes=high)` | `native_form_filler._escalate_fill` | ④.6 (failure path) |
| **F1** | Mirror Plan D into FormFillEngine + JD-page Apply finder | `form_engine/engine.py`, `_navigator.click_apply_button` | ③, ④.10 |
| **F2** | Pre-fill option scanning for closed comboboxes | `field_scanner._scan_combobox_options`, `_populate_combobox_options` | ④.3 |
| **F3** | Range slider, contenteditable rich-text, native date handlers | `_fill_resolved_widget` | ④.6 |
| **F4** | Field-label noise filter | `field_scanner._filter_noise_fields` | ④.4 |
| **F6** | Engine plan-retry loop (3 attempts with failure history) | `_escalate_fill` | ④.6 |

Deferred / queued (separate plans in `docs/superpowers/plans/`):
- **F5 deep purge** (4 files, 4 sessions): `screening_answers.py`,
  `email_preclassifier.py`, `screening_decomposer.py`,
  `dispatcher.py:279-598` — see
  `2026-05-06-regex-classification-purge.md`.
- Universal-plan F3-2/3/6/7/8 (multi-pick, tag input, calendar grid,
  drag-drop fallback, phone country generic) — see
  `2026-05-06-universal-dynamic-form-fill.md` §F3.

---

## QUICK ENTRY POINTS

```bash
# Apply next 1 job from the queue (live, headed, dry-run gate)
python -m jobpulse.runner job-apply-next 1

# Apply a specific URL through the full pipeline
python -m jobpulse.runner job-process-url <URL>

# Auto-submit (skip phase ⑤)
JOB_AUTOPILOT_AUTO_SUBMIT=true python -m jobpulse.runner job-apply-next 1

# Fast Claude Code session (no per-field delays)
FAST_FILL=true python -m jobpulse.runner job-apply-next 1
```

---

## VERIFICATION AUDIT — what's covered, what's intentionally out of scope

Audit method: BFS three levels deep from the 12 entry-point files.
Final transitive set: **212 unique modules**. Each is categorized below
as one of:

- **A** — directly wired apply-runtime module (called during `apply_job()`)
- **B** — internal sub-module of an engine listed in §"External Integrations"
- **C** — imported transitively but NOT called during apply runtime
  (used by other agents, dispatcher command-routing, dev tools, etc.)
- **D** — wired but not currently consumed (latent; either a feature
  flag is off or the integration point is incomplete)

### A — Apply-runtime modules (directly called during `apply_job()`)

```
jobpulse/applicator.py                         ✓ phase entry point
jobpulse/scan_pipeline.py                      ✓ phase ① + ②
jobpulse/runner.py                             ✓ Quick Entry Points
jobpulse/recruiter_screen.py                   ✓ Gate 0
jobpulse/skill_graph_store.py                  ✓ Gates 1-3
jobpulse/skill_extractor.py                    ✓ ① pre-screen
jobpulse/gate4_quality.py                      ✓ Gate 4A + 4B
jobpulse/company_blocklist.py                  ✓ Gate 4A
jobpulse/jd_analyzer.py                        ✓ ① pre-screen
jobpulse/liveness_checker.py                   ✓ ⓪ upstream
jobpulse/job_deduplicator.py                   ✓ ⓪ upstream
jobpulse/job_scanner.py                        ✓ ⓪ upstream
jobpulse/job_notion_sync.py                    ✓ ⓪ upstream + ⑦ post-apply
jobpulse/job_db.py                             ✓ database layer
jobpulse/cv_tailor.py                          ✓ ② materials
jobpulse/cv_templates/generate_cv.py           ✓ ② materials
jobpulse/cv_templates/generate_cover_letter.py ✓ ② materials (lazy)
jobpulse/cv_templates/scrutiny_calibrator.py   ✓ ② materials
jobpulse/archetype_engine.py                   ✓ ② materials
jobpulse/portfolio_variants.py                 ✓ ② materials
jobpulse/project_portfolio.py                  ✓ ② materials
jobpulse/github_matcher.py                     ✓ ② materials
jobpulse/github_profile_sync.py                ✓ ② materials
jobpulse/skill_tracker_notion.py               ✓ ② materials
jobpulse/skill_gap_tracker.py                  ✓ ① pre-screen state
jobpulse/ats_scorer.py                         ✓ ② materials
jobpulse/ats_adapters/                         ✓ ATS adapters table
jobpulse/application_materials.py              ✓ ② materials coordinator
jobpulse/form_prefetch.py                      ✓ ① form_hints output
jobpulse/application_orchestrator_pkg/         ✓ ③ navigation
jobpulse/playwright_driver.py                  ✓ ③ + ④ (CDP driver)
jobpulse/playwright_adapter.py                 ✓ ATS adapters
jobpulse/cookie_dismisser.py                   ✓ ③ navigation sub-system
jobpulse/sso_handler.py                        ✓ ③ navigation sub-system
jobpulse/account_manager.py                    ✓ ③ account creation
jobpulse/gmail_verify.py                       ✓ ③ verification
jobpulse/navigation_learner.py                 ✓ ③ replay
jobpulse/page_analyzer.py                      ✓ ③ DOM classifier
jobpulse/page_analysis/classifier.py           ✓ ③ page detection
jobpulse/page_analysis/page_reasoner.py        ✓ ③ semantic reasoner
jobpulse/page_analysis/calibration.py          ✓ ③ adaptive thresholds
jobpulse/verification_detector.py              ✓ ③ wall detection
jobpulse/platform_bypass.py                    ✓ ③ aggregator bypass
jobpulse/native_form_filler.py                 ✓ ④ form fill
jobpulse/form_engine/                          ✓ ④ scanners + fillers
jobpulse/form_engine/field_scanner.py          ✓ ④.2 multi-strategy scan
jobpulse/form_engine/field_mapper.py           ✓ ④.5 mapping
jobpulse/form_engine/field_resolver.py         ✓ ④ locator resolution
jobpulse/form_engine/intent_healing.py         ✓ ④.6 healing
jobpulse/form_engine/semantic_scanner.py       ✓ Plan A
jobpulse/form_engine/semantic_matcher.py       ✓ ④.6 option matching
jobpulse/form_engine/vision_gate.py            ✓ Plan B
jobpulse/form_engine/gotchas.py                ✓ Plan C-3 + widget_patterns
jobpulse/form_engine/validation.py             ✓ ④.6 validation errors
jobpulse/form_engine/file_uploader.py          ✓ ④ CV upload
jobpulse/form_engine/file_filler.py            ✓ ④ alt file uploader
jobpulse/form_engine/text_filler.py            ✓ ④.6 widget handlers
jobpulse/form_engine/select_filler.py          ✓ ④.6
jobpulse/form_engine/radio_filler.py           ✓ ④.6
jobpulse/form_engine/checkbox_filler.py        ✓ ④.6
jobpulse/form_engine/date_filler.py            ✓ ④.6 + F3-5
jobpulse/form_engine/multi_select_filler.py    ✓ ④.6 + tag input
jobpulse/form_engine/page_filler.py            ✓ ④.6 (unified-engine router)
jobpulse/form_engine/engine.py                 ✓ unified FormFillEngine + F1
jobpulse/form_engine/detector.py               ✓ ④.6 widget classifier
jobpulse/form_engine/widget_detector.py        ✓ ④.6 React-Select detection
jobpulse/form_engine/widget_strategies.py      ✓ ④.6 vendor strategies
jobpulse/form_engine/widget_llm_recovery.py    ✓ ④.6 LLM recovery
jobpulse/form_engine/confidence_scorer.py      ✓ ④ scoring
jobpulse/form_engine/consent_policy.py         ✓ ④ checkbox consent
jobpulse/form_engine/unified_scanner.py        ✓ used by FormFillEngine
jobpulse/form_engine/models.py                 ✓ InputType enum
jobpulse/screening_pipeline.py                 ✓ ④.5 (7 sub-modules)
jobpulse/screening_decomposer.py               ✓ screening pipeline
jobpulse/screening_detector.py                 ✓ screening pipeline
jobpulse/screening_intent.py                   ✓ screening pipeline
jobpulse/screening_option_aligner.py           ✓ screening pipeline
jobpulse/screening_pattern_extractor.py        ✓ screening pipeline
jobpulse/screening_semantic_cache.py           ✓ screening pipeline
jobpulse/screening_validator.py                ✓ screening pipeline
jobpulse/screening_outcome_recorder.py         ✓ ⑥ submit
jobpulse/screening_feedback_loop.py            ✓ ⑦ learn
jobpulse/screening_answers.py                  ✓ ④.5 (F5 target)
jobpulse/correction_capture.py                 ✓ ⑥ submit
jobpulse/agent_rules.py                        ✓ ⑥ submit + ④.5
jobpulse/cross_platform_field_transfer.py      ✓ ④.5
jobpulse/agent_performance.py                  ✓ ⑥ + ⑦ metrics
jobpulse/strategy_reflector.py                 ✓ ⑦ learn chain ②
jobpulse/trajectory_store.py                   ✓ ⑦ learn chain ②
jobpulse/post_apply_hook.py                    ✓ ⑦ post-apply
jobpulse/drive_uploader.py                     ✓ ⑦ Drive
jobpulse/form_experience_db.py                 ✓ ⑦ form experience
jobpulse/ai_assist_logger.py                   ✓ Plan E + C-2
jobpulse/pre_submit_gate.py                    ✓ ④.8b
jobpulse/browser_cleanup.py                    ✓ ⑥ submit step 8
jobpulse/rate_limiter.py                       ✓ ⑥ submit step 2
jobpulse/process_logger.py                     ✓ Observability
jobpulse/pipeline_hooks.py                     ✓ ⑦ extension points
jobpulse/draft_applicator.py                   ✓ ⑤ draft mode
jobpulse/draft_queue.py                        ✓ ⑤ draft mode
jobpulse/multi_bot_listener.py                 ✓ ⑤ Telegram bots
jobpulse/voice_handler.py                      ✓ ⑤ voice approval
jobpulse/nlp_classifier.py                     ✓ ⓪ + ⑤
jobpulse/dispatcher.py / swarm_dispatcher.py   ✓ ⓪ Telegram routing
jobpulse/handler_registry.py                   ✓ ⓪ shared handler map
jobpulse/intent_registry.py                    ✓ ⓪ intent groups
jobpulse/command_router.py                     ✓ ⓪ Intent enum
jobpulse/rejection_analyzer.py                 ✓ ⑦ rejection learning
jobpulse/followup_tracker.py                   ✓ ⑦ post-apply (cron)
jobpulse/interview_prep.py                     ✓ post-application (separate)
jobpulse/ats_api_scanner.py                    ✓ ⓪ alt scan path
jobpulse/scan_learning.py                      ✓ ⓪ scan signals
jobpulse/content_hasher.py                     ✓ ④ structural fingerprint (PRAXIS cross-domain)
jobpulse/form_models.py                        ✓ Pydantic types: FillResult, PageType, FieldInfo, FillSubmitResult
jobpulse/application_orchestrator.py           ✓ ③ re-export shim → application_orchestrator_pkg
jobpulse/application_orchestrator_pkg/_auth.py ✓ ③ login/signup + email verification
jobpulse/application_orchestrator_pkg/_executor.py ✓ ③ action execution (delegated)
jobpulse/auto_rule_generator.py                ✓ ⑦ wired via OptimizationEngine — generates rules from corrections + trajectories
jobpulse/browser_intelligence.py               ✓ ④ injected per-page (signal capture: console errors, network, focus)
jobpulse/config.py                             ✓ all phases — env var central
jobpulse/email_review.py                       ✓ ⑤ Telegram-based review reply handler (process_review_reply)
jobpulse/form_interaction_log.py               ✓ ④ per-page field structure log (FormInteractionLog) — feeds form_prefetch
jobpulse/form_scanner.py                       ✓ ④ legacy FormScanner.scan_form + scan_combobox_options (combobox option discovery, separate from F2)
jobpulse/ghost_detector.py                     ✓ ⓪ detect_ghost_job (loaded lazily by pipeline_hooks)
jobpulse/navigation/action_executor.py         ✓ ③ NavigationActionExecutor — verification primitive, used by _auth.handle_login/handle_signup AND _phase_act
jobpulse/navigation/overlay_dismisser.py       ✓ ③ OverlayDismisser — LinkedIn "Save this application?" overlay
jobpulse/navigation/wait_conditions.py         ✓ ③ wait_for_page_stable, wait_for_dom_idle
jobpulse/notion_client.py                      ✓ ⑦ Notion REST wrapper (used by job_notion_sync)
jobpulse/platform_transfer.py                  ✓ ⑦ PlatformTransferEngine — wraps cross_platform_field_transfer; called from form_experience_db, post_apply_hook, navigation_learner
jobpulse/signal_interpreter.py                 ✓ ④ SignalInterpreter — reads BrowserIntelligence signals (console errors, JS exceptions) during fill
jobpulse/sso_auto_discovery.py                 ✓ ③ detect_sso_button_patterns (called by sso_handler)
jobpulse/telegram_stream.py                    ✓ Observability — streams pipeline logs to Telegram during cron runs
jobpulse/tracked_driver.py                     ✓ A/B testing — ABTracker per-field metrics (used in form_engine/engine.py FormFillEngine path)
jobpulse/utils/safe_io.py                      ✓ atomic file writes (used by JSON cache writes)
jobpulse/vision_tier.py                        ✓ ③ classify_page_type_from_screenshot (low-confidence cross-check) + ④ analyze_field_screenshot (Tier 5 fallback) + vision_map_unlabeled_fields
jobpulse/models/application_models.py          ✓ Pydantic dataclasses for application records
jobpulse/job_scanners/linkedin.py + indeed.py + reed.py ✓ ⓪ platform-specific scanners
shared/alerting.py                             ✓ recovery (Telegram alert bot)
shared/locks.py                                ✓ Concurrency Model (process_lock + system_lock)
shared/pii.py                                  ✓ ② + ④ (PII wrapper + leak audit)
shared/cognitive/                              ✓ ④ + ⑦ (sub-modules: _engine, _classifier, _budget, _strategy, _reflexion, _tree_of_thought, _prompts)
shared/optimization/                           ✓ ⑦ signals (sub-modules: _engine, _aggregator, _policy, _signals, _tracker, _trajectory, _replay)
shared/memory_layer/                           ✓ ⑦ Qdrant + Neo4j (sub-modules: _manager, _sqlite_store, _qdrant_store, _neo4j_store, _embedder, _entries, _linker, _forgetting, _query, _router, _stores, _sync, _pattern)
shared/governance/                             ✓ Security boundary (sub-modules: _output_sanitizer, _score_validator)
shared/prompts/                                ✓ ② + ④ prompt registry + orchestration templates
shared/agents.py                               ✓ LLM factory (get_llm, get_openai_client, cognitive_llm_call)
shared/streaming.py                            ✓ smart_llm_call
shared/cost_tracker.py                         ✓ Observability
shared/profile_store.py                        ✓ ② + ④
shared/logging_config.py                       ✓ Observability
shared/telegram_client.py                      ✓ ⑤ + recovery
shared/circuit_breaker.py                      ✓ recovery
shared/safe_fetch.py                           ✓ HTTP boundary
shared/llm_retry.py                            ✓ LLM resilience
shared/llm_fallback.py                         ✓ LLM provider fallback (OpenAI → Anthropic)
shared/semantic_utils.py                       ✓ embedding similarity (best_semantic_match)
shared/parallel_executor.py                    ✓ ④.2 strategy gather + GRPO candidates
shared/code_intelligence/                      ✓ Observability (CodeGraph)
shared/agentic_loop.py                         ✓ stop_reason loop (used by patterns; reachable via fact_checker import)
shared/context_compression.py                  ✓ tiktoken token counting (LLM prompt budget enforcement)
shared/experiential_learning.py                ✓ ExperienceMemory (Training-Free GRPO) — used by ⑦
shared/external_verifiers.py                   ✓ fact-checker external sources (Semantic Scholar, web search)
shared/fact_checker.py                         ✓ used by patterns; reached via shared/__init__ side imports
shared/google_retry.py                         ✓ Google API retry decorator (Drive + Gmail)
shared/hybrid_search.py                        ✓ FTS5 + vector RRF (used by memory_layer)
shared/prompt_defense.py                       ✓ injection-tag stripping before every prompt
shared/rate_monitor.py                         ✓ rate-limit observability (apply path uses this)
shared/self_healing.py                         ✓ DB health + memory desync detection (background)
shared/db.py                                   ✓ get_pooled_db_conn (shared SQLite pool)
shared/paths.py                                ✓ DATA_DIR constant
shared/state.py                                ✓ AgentState TypedDict + prune_state
shared/daemon_threads.py                       ✓ background thread registration
```

### B — Internal sub-modules (rolled up under their engine in the doc)

These are the leaf files of the engines named in §"External Integrations".
The engine's public surface is documented; these are its private parts.
Listed here for completeness so nothing is invisible.

```
shared/cognitive/_engine.py            CognitiveEngine.think entry
shared/cognitive/_classifier.py        EscalationClassifier (L0→L3)
shared/cognitive/_budget.py            per-hour LLM/$$ caps
shared/cognitive/_strategy.py          StrategyComposer
shared/cognitive/_reflexion.py         L2 reflexion executor
shared/cognitive/_tree_of_thought.py   L3 ToT executor
shared/cognitive/_prompts.py           anti-pattern prompt fragments
shared/optimization/_engine.py         OptimizationEngine facade
shared/optimization/_aggregator.py     signal-to-pattern aggregator
shared/optimization/_policy.py         policy decisions
shared/optimization/_signals.py        Signal dataclass
shared/optimization/_tracker.py        before/after measurement
shared/optimization/_trajectory.py     TrajectoryStore writes
shared/optimization/_replay.py         replay from optimization.db
shared/memory_layer/_manager.py        MemoryManager facade
shared/memory_layer/_sqlite_store.py   truth store
shared/memory_layer/_qdrant_store.py   vector store
shared/memory_layer/_neo4j_store.py    graph store
shared/memory_layer/_embedder.py       Voyage 3 + MiniLM fallback
shared/memory_layer/_entries.py        MemoryEntry dataclass
shared/memory_layer/_linker.py         A-MEM autonomous linking
shared/memory_layer/_forgetting.py     6-signal decay
shared/memory_layer/_query.py          QueryRouter
shared/memory_layer/_router.py         engine selection
shared/memory_layer/_stores.py         atomic JSON writes
shared/memory_layer/_sync.py           3-engine reconciliation
shared/memory_layer/_pattern.py        pattern memory tier
shared/governance/_output_sanitizer.py output safety
shared/governance/_score_validator.py  score range enforcement
shared/code_graph/_indexer.py          Python AST → SQLite
shared/code_graph/_algorithms.py       BFS, fan-in, blast radius
shared/code_graph/_risk.py             risk scoring
shared/prompts/registry.py             PromptRegistry
shared/prompts/orchestration.py        orchestration prompt templates
```

### C — Imported transitively but NOT called during apply runtime

These show up in the BFS because of import chains (e.g.
`dispatcher.py` imports `gmail_agent` for command routing; that
module's transitive imports surface in the apply BFS even though
they're not invoked when `apply_job()` runs).

```
jobpulse/gmail_agent.py                 — daily email classification (cron)
jobpulse/email_preclassifier.py         — used by gmail_agent
jobpulse/persona_evolution.py           — used by gmail_agent + morning_briefing
jobpulse/telegram_agent.py              — Telegram chat agent (general)
jobpulse/telegram_bots.py               — bot config
jobpulse/perplexity.py                  — Perplexity API (other agents)
jobpulse/tone_framework.py              — tone calibration (content gen, not apply)
shared/code_graph/                      — dev-time CodeGraph indexer
                                          (reaches BFS via shared/agents.py
                                          but not called by apply path)
```

### D — Wired but latent (feature-flagged off / unused integration point)

```
jobpulse/auto_rule_generator.py         — wired via OptimizationEngine but
                                          fires only when batch threshold met
jobpulse/tracked_driver.py (ABTracker)  — only fires when application_id
                                          passed to FormFillEngine
                                          (UNIFIED_FORM_ENGINE=true path)
shared/self_healing.py                  — runs in background daemon, not
                                          per-apply
```

### Intentionally out of scope

These exist in the codebase but are **not** part of the URL-to-submit
apply pipeline. They run on separate cron paths and are documented
elsewhere:

```
jobpulse/budget_agent.py + budget_*.py     — financial tracking (cron)
jobpulse/calendar_agent.py                 — calendar agent (cron)
jobpulse/gmail_agent.py + email_*.py       — email classification (cron)
jobpulse/github_agent.py                   — yesterday's commits (cron)
jobpulse/arxiv_agent.py                    — arXiv papers (cron)
jobpulse/notion_agent.py                   — Notion task CRUD (cron)
jobpulse/briefing_agent.py + morning_briefing.py — daily digest
jobpulse/blog_generator.py                 — content generation
jobpulse/conversation.py + telegram_listener.py — chat agent
jobpulse/healthcheck.py + daemon_threads.py — daemon health
jobpulse/webhook_server.py + *_api.py      — FastAPI server (port 8080)
jobpulse/job_analytics.py                  — `job stats` Telegram cmd
jobpulse/job_api.py + analytics_api.py     — REST endpoints
jobpulse/install_cron.py                   — crontab installer
jobpulse/voice_handler.py (non-approval)   — general voice commands
shared/adversarial/                        — red-teaming framework
shared/execution/                           — durable execution
shared/governance/                          — auth, score validation
shared/evals/                               — agent evaluation harness
patterns/                                   — LangGraph orchestration patterns
mindgraph_app/                              — code review graph
```

### Database touchpoints — verified against `ls data/*.db`

51 SQLite files exist in `data/`. The apply pipeline writes to or
reads from 32 of them (listed in the Database Touchpoint Map). The
remaining 19 are:
- 11 used by other agents (budget, gmail, calendar, etc.)
- 5 dead/legacy (cleanup pending, none load)
- 3 wired-but-empty (pending the right code path firing — listed in
  CLAUDE.md "Database Wiring Status")

### Coverage confirmation

**Method:** breadth-first import discovery, 3 levels deep, starting
from the 12 entry-point files (`applicator.py`, `scan_pipeline.py`,
all of `application_orchestrator_pkg/*.py`, `native_form_filler.py`,
`screening_pipeline.py`, `post_apply_hook.py`, `correction_capture.py`,
`pre_submit_gate.py`).

**Result:** 212 unique transitive modules. Every one is categorized
above (A / B / C / D). The categorization is honest — modules that
look like they're in the apply path but are actually only reachable
via dispatcher / shared `__init__` side imports are marked C, not A.

**Wiring honesty notes:**

- `auto_rule_generator.py` is wired into `OptimizationEngine` but
  fires only when batch thresholds are met — listed as D (latent).
- `tracked_driver.py` (ABTracker) only activates on the
  `UNIFIED_FORM_ENGINE=true` path with an application_id — D.
- `self_healing.py` runs in a background daemon thread, not per-apply
  request — D.
- `auto_rule_generator` does **not** appear directly imported by any
  apply-path module today — it's reached via
  `shared/optimization/_engine.py:16` lazy import. If the daemon
  isn't running the optimize cycle, this code path stays cold.
- `gmail_agent` is imported by `dispatcher.py` for the
  `/check_emails` Telegram command — that's command-routing, not
  apply runtime. Marked C.
- `code_graph/*` is reachable from `shared/agents.py` but only
  invoked at dev time during code review patterns. Marked B/C.

**Cross-check commands (run yourself, replicate the audit):**

```bash
# Direct level-1 imports of the 12 nerve-center files
( grep -h "^from jobpulse\|^from shared" \
    jobpulse/applicator.py jobpulse/scan_pipeline.py \
    jobpulse/application_orchestrator_pkg/*.py \
    jobpulse/native_form_filler.py jobpulse/screening_pipeline.py \
    jobpulse/post_apply_hook.py jobpulse/correction_capture.py \
    jobpulse/pre_submit_gate.py ) | awk '{print $2}' | sort -u | wc -l
# → 54

# Full transitive set, 3 levels deep
python3 <<'PY'
import re
from pathlib import Path
ROOT = Path("/Users/yashbishnoi/projects/multi_agent_patterns")
NERVE = ["jobpulse/applicator.py","jobpulse/scan_pipeline.py",
         "jobpulse/native_form_filler.py","jobpulse/screening_pipeline.py",
         "jobpulse/post_apply_hook.py","jobpulse/correction_capture.py",
         "jobpulse/pre_submit_gate.py"] + \
        [f"jobpulse/application_orchestrator_pkg/{f}" for f in
         ("__init__.py","_navigator.py","_form_filler.py","_executor.py","_auth.py")]
IMP = re.compile(r"^\s*(?:from|import)\s+(jobpulse[\w\.]*|shared[\w\.]*)", re.M)
def imports(f):
    try: t = (ROOT/f).read_text()
    except: return set()
    out = set()
    for m in IMP.finditer(t):
        for c in (Path(*m.group(1).split(".")).with_suffix(".py"),
                  Path(*m.group(1).split(".")) / "__init__.py"):
            if (ROOT/c).is_file(): out.add(str(c))
    return out
seen, frontier = set(NERVE), set(NERVE)
for _ in range(3):
    nf = set()
    for f in frontier: nf |= imports(f) - seen
    seen |= nf; frontier = nf
print(len(seen))
PY
# → 212

# Verify every transitive module appears in this doc by basename
# (any output = potential drift to investigate)
python3 -c "
import re; from pathlib import Path
all_mods = open('/tmp/apply_transitive.txt').read().split()
doc = Path('docs/job-application-pipeline.md').read_text()
for m in all_mods:
    base = Path(m).stem
    if base == '__init__': base = Path(m).parent.name
    if not re.search(r'\b' + re.escape(base) + r'\b', doc):
        print('MISSING:', m)
"
```

If any of those commands surfaces a module not categorized above,
that's a real drift — please flag.

**Closing statement:** this document and the codebase are now
synchronized at branch `pipeline-correctness-fixes` HEAD as of commit
`c42d86f`. Every one of the 212 transitive modules in the apply BFS
is accounted for under categories A (apply-runtime), B (engine
internals), C (transitive-only via dispatcher/shared imports), or D
(wired-latent).
