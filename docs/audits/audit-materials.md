# Subsystem 8 — `materials` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: post-pre-screen `generate_materials()` invoked from `_run_scan_window_inner` (cron path) and `process_single_url` (CLI path).
- Files:
  - `jobpulse/scan_pipeline.py:516-775` (`generate_materials` only — `prescreen_listings` belongs to S7)
  - `jobpulse/application_materials.py` (lazy CV + lazy CL builder)
  - `jobpulse/cv_tailor.py` (LLM tailoring + validators)
  - `jobpulse/archetype_engine.py` (role archetype detection)
  - `jobpulse/portfolio_variants.py` (per-archetype project bullets)
  - `jobpulse/project_portfolio.py` (DB-first project lookup + JD matching)
  - `jobpulse/github_matcher.py` (synonym-aware repo scoring)
  - `jobpulse/cv_templates/generate_cv.py` (ReportLab PDF)
  - `jobpulse/cv_templates/generate_cover_letter.py` (ReportLab PDF)
  - `jobpulse/ats_scorer.py` (deterministic ATS scoring)
- Total LOC audited: ~3 450 (3 149 listed by `wc -l` for the 9 module files + ~300 LOC of `generate_materials`).

**NOTE on prompt's framing.** The prompt says "Entry: post-pre-screen. Output: cv_path, optional cover_letter_path, agent_mapping." That matches the CV/CL artefact part of the contract; "agent_mapping" is set in the form-fill subsystem (S1), not here. `generate_materials` produces a `MaterialsBundle` containing `cv_path`, `cv_text`, `ats_score`, `matched_projects`, `notion_page_id`, `notion_status`, `gate4b_notes`. It does **not** produce a cover letter eagerly; CL generation is deferred to `route_and_apply` via `cl_generator` callback or to `build_lazy_cover_letter_generator` in the live-review path.

The materials subsystem also exposes a **lazy-CV path** (`ensure_tailored_cv_for_job`) called from `job_autopilot.py:661` (live-review pre-flight) and `live_review_applicator.py:225`. This path was not in the prompt's framing but is part of the apply chain — included.

---

## 1. Function inventory + wiring

| File:line | Function | Cat | Direct callers (apply path) |
|---|---|---|---|
| scan_pipeline.py:516 | `generate_materials` | A | `_run_scan_window_inner` (cron), `process_single_url` (CLI), `enhanced_generate_materials` (wrapper) |
| application_materials.py:20 | `_application_dir` | A | `ensure_tailored_cv_for_job`, `_generate` |
| application_materials.py:51 | `_sanitize_location` | A | `ensure_tailored_cv_for_job`, `_generate` |
| application_materials.py:83 | `_parse_skill_list` | A | `ensure_tailored_cv_for_job`, `_generate` |
| application_materials.py:96 | `ensure_tailored_cv_for_job` | A | `job_autopilot.py:661`, `live_review_applicator.py:225` |
| application_materials.py:212 | `build_lazy_cover_letter_generator` | A | `live_review_applicator.py:233` |
| application_materials.py:219 | `_generate` (closure) | A | returned-and-called from `live_review_applicator.py` post-handover |
| cv_tailor.py:20 | `build_required_tagline` | A | `_build_default_tagline` (generate_cv.py:272), `tailor_summary_and_tagline` (alias `_required_tagline_format`) |
| cv_tailor.py:51 | `_parse_llm_json` | A | all four `tailor_*` helpers + `polish_points_llm` |
| cv_tailor.py:163 | `validate_summary` | A | `tailor_summary_and_tagline` |
| cv_tailor.py:176 | `validate_experience` | A | `tailor_experience_bullets` |
| cv_tailor.py:197 | `validate_projects` | A | `tailor_project_bullets` |
| cv_tailor.py:213 | `validate_cover_letter` | A | `tailor_cover_letter_prose` |
| cv_tailor.py:235 | `_record_validation_failure` | A | every `tailor_*` |
| cv_tailor.py:245 | `_call_with_correction` | A | every `tailor_*` |
| cv_tailor.py:263 | `tailor_summary_and_tagline` | A | `tailor_all_sections` |
| cv_tailor.py:339 | `tailor_experience_bullets` | A | `tailor_all_sections` |
| cv_tailor.py:412 | `tailor_project_bullets` | A | `tailor_all_sections` |
| cv_tailor.py:482 | `tailor_cover_letter_prose` | A | `tailor_all_sections`, `application_materials._generate` |
| cv_tailor.py:540 | `tailor_all_sections` | A | `generate_materials:620`, `ensure_tailored_cv_for_job:172` |
| archetype_engine.py:32 | `_build_default_tagline` | A | `_build_default_profile` |
| archetype_engine.py:61 | `_build_default_profile` | A | `_get_default_profile` (post-fix; pre-fix called at module load — B-1) |
| archetype_engine.py:81 | `_load_profiles` | A | `detect_archetype`, `get_archetype_profile` |
| archetype_engine.py:102 | `detect_archetype` | B | `pipeline_hooks.with_archetype_detection` (gated by `JOBPULSE_ARCHETYPE_ENGINE`, `=true` in `.env`) |
| archetype_engine.py:157 | `get_archetype_profile` | A | `get_archetype_framing` |
| archetype_engine.py:169 | `_build_archetype_summaries` | A | `get_archetype_framing` |
| archetype_engine.py:225 | `get_archetype_framing` | B | `generate_materials:601` (only when `listing.archetype` is set, which only happens when `JOBPULSE_ARCHETYPE_ENGINE=true`) |
| portfolio_variants.py:23 | `_load_variants_from_db` | A | `get_variant_bullets`, `get_or_generate_variant_bullets` |
| portfolio_variants.py:84 | `load_auto_portfolio` | A | `get_auto_entry`, `get_or_generate_variant_bullets` |
| portfolio_variants.py:92 | `save_auto_portfolio` | A | `get_or_generate_variant_bullets` (after generation) |
| portfolio_variants.py:104 | `get_variant_bullets` | C | only called by `github_profile_sync` + tests |
| portfolio_variants.py:121 | `get_auto_entry` | A | `get_best_projects_for_jd` |
| portfolio_variants.py:127 | `get_or_generate_variant_bullets` | B | `get_best_projects_for_jd` (only when `archetype` is set — B if env=true) |
| portfolio_variants.py:195 | `_generate_jd_aware_bullets` | B | `get_or_generate_variant_bullets` (cache miss path) |
| portfolio_variants.py:262 | `generate_portfolio_entry` | C | `github_profile_sync` (3am cron) only |
| project_portfolio.py:39 | `_load_portfolio_from_db` | A | `_portfolio_lookup` |
| project_portfolio.py:77 | `_portfolio_lookup` | A | `get_project_entry`, `get_best_projects_for_jd` |
| project_portfolio.py:103 | `get_project_entry` | C | not called from apply path; tests + JSON exports |
| project_portfolio.py:112 | `get_best_projects_for_jd` | A | `generate_materials:595`, `application_materials._generate:231`, `ensure_tailored_cv_for_job:125`, `route_and_apply:836` |
| project_portfolio.py:188 | `_portfolio_merged_view` | A | `_PortfolioProxy` accessors |
| github_matcher.py:25 | `load_skill_synonyms` | A | `score_repo` (called per-repo by `pick_top_projects`) |
| github_matcher.py:46 | `_normalize` | A | `_skill_match` |
| github_matcher.py:51 | `_skill_match` | A | `score_repo` |
| github_matcher.py:78 | `score_repo` | A | `pick_top_projects` |
| github_matcher.py:145 | `pick_top_projects` | A | `generate_materials:566` (only when `screen.best_projects` is empty) |
| github_matcher.py:164 | `fetch_and_cache_repos` | A | `generate_materials:562` (lazy on empty cache); `github_profile_sync` (cron) |
| cv_templates/generate_cv.py:65 | `_discover_font` | A | `_register_fonts` |
| cv_templates/generate_cv.py:85 | `_register_fonts` | A | `generate_cv_pdf` (first call only) |
| cv_templates/generate_cv.py:117-156 | `_load_education` / `_load_experience` / `_load_certifications` / `_load_community` / `_load_base_skills` / `_load_default_projects` | A | lazy via `__getattr__` (line 171) and direct calls inside `generate_cv_pdf` |
| cv_templates/generate_cv.py:171 | `__getattr__` | A | implicit on `BASE_SKILLS`, `EXPERIENCE`, … attribute access |
| cv_templates/generate_cv.py:187 | `_build_role_profiles` | A | `get_role_profile` |
| cv_templates/generate_cv.py:266 | `_build_default_tagline` | A | `generate_cv_pdf` fallback path |
| cv_templates/generate_cv.py:287 | `build_extra_skills` | A | `generate_materials:584`, `ensure_tailored_cv_for_job:126` |
| cv_templates/generate_cv.py:343 | `get_role_profile` | A | `generate_materials:610`, `ensure_tailored_cv_for_job:177` |
| cv_templates/generate_cv.py:391 | `generate_cv_pdf` | A | `generate_materials:667`, `ensure_tailored_cv_for_job:193` |
| cv_templates/generate_cv.py:671 | `normalize_text_for_ats` | A | `generate_cv_pdf` (every CV generation) + `pipeline_hooks.enhanced_generate_materials` (B if `JOBPULSE_ATS_NORMALIZE=true`) |
| cv_templates/generate_cover_letter.py:45 | `build_dynamic_points` | A | `generate_cover_letter_pdf` (when `points` arg is None) |
| cv_templates/generate_cover_letter.py:90 | `_default_pad_points` | A | `build_dynamic_points` |
| cv_templates/generate_cover_letter.py:123 | `polish_points_llm` | A | `generate_cover_letter_pdf` |
| cv_templates/generate_cover_letter.py:183 | `_register_fonts` | A | `generate_cover_letter_pdf` (first call only) |
| cv_templates/generate_cover_letter.py:197 | `generate_cover_letter_pdf` | A | `route_and_apply` (lazy `cl_generator`), `application_materials._generate` |
| ats_scorer.py:66 | `score_ats` | A | `generate_materials:656` |
| ats_scorer.py:124 | `_load_synonyms` | A | `score_ats` |
| ats_scorer.py:143 | `_normalize` | A | `_keyword_in_text`, `_word_present` |
| ats_scorer.py:148 | `_keyword_in_text` | A | `score_ats` |
| ats_scorer.py:184 | `_word_present` | A | `_keyword_in_text` |
| ats_scorer.py:192 | `_detect_sections` | A | `score_ats` |
| ats_scorer.py:210 | `_score_format` | A | `score_ats` |

**Wiring categorisation summary**: 49 functions in category A, 5 in B (gated by `JOBPULSE_ARCHETYPE_ENGINE` / `JOBPULSE_ATS_NORMALIZE`; both `=true` in `.env`), 4 in C (apply path unreachable — only tests, CLI, or 3 AM `github_profile_sync` cron consume), 0 D-orphans, 0 E-overrides found.

---

## 2. Findings (severity-tagged)

### Blockers / majors (shipped this session)

- **B-1 BLOCKER** `archetype_engine.py:70` (pre-fix) — Module-level
  `_DEFAULT_PROFILE = _build_default_profile()` opens `data/user_profile.db`
  via ProfileStore at module import (`_connect()` runs `sqlite3.connect`
  + `PRAGMA journal_mode=WAL` + schema migration). Same shape as S7
  audit B-2 in `skill_gap_tracker._init_db`. Violates Principle 1.
  Anything that transitively imports `archetype_engine` (via
  `pipeline_hooks` → `job_autopilot` → tests + CLI) paid the cost on
  every invocation. **Fixed in `21e836d`** with lazy `_get_default_profile()`.

- **B-2 BLOCKER** `scan_pipeline.py:587-591` (pre-fix) — Pre-generation
  Notion Skill Tracker sync (`sync_verified_to_profile`) wrapped in
  `try: pass except Exception: pass` with **no log**. Per
  `.claude/rules/jobs.md` this is MANDATORY pre-CV-generation step #1;
  silent failure means stale Notion-verified skills propagate into every
  CV with zero observability (Notion auth gone, locked DB, network — none
  surface). Same shape as S7 audit M-A. **Fixed in `d1252a9`** with
  `logger.warning(..., exc_info=True)`.

- **M-B** `scan_pipeline.py:629` (pre-fix) — CV tailoring failure logged at
  `logger.debug`. Tailoring is the differentiating value-add of the
  pipeline; silent debug means generic template CVs ship indefinitely if
  the LLM chain breaks (quota, key, model rename) with no operator signal.
  **Fixed in `d1252a9`**.

- **M-C** `scan_pipeline.py:719` (pre-fix) — Scrutiny calibration record
  failure logged at `logger.debug`. Calibration is a learning system;
  silent debug means the calibrator never learns if a schema migration
  breaks writes. **Fixed in `d1252a9`**.

- **M-D** `cv_templates/generate_cover_letter.py:234` (pre-fix) —
  `polish_points_llm` bare `except Exception: pass` with **no log**. Same
  shape as M-B (silent quality drop in CL polish). **Fixed in `d1252a9`**.

- **M-E** `application_materials.py:155, 243` (pre-fix) — correction skill
  boost and lazy-CL tailoring failures logged at `logger.debug`. Silent
  debug on the OPRAL learning chain's last mile lets a broken
  CorrectionCapture path drift skills across every tailored CV.
  **Fixed in `d1252a9`**.

### Deferred majors (followup worklist)

- **🔴 M-F** `github_matcher.py:89` — `synonyms = load_skill_synonyms()`
  called inside `score_repo`, which is called per-repo by `pick_top_projects`.
  With ~22 GitHub repos × 36K-entry `data/skill_synonyms.json` × multiple
  `_skill_match` invocations per repo, the JSON is parsed N×3 times per
  scan-window invocation. Already noted as REMAINING in
  `.claude/rules/seven-principles.md` §3 ("`skill_graph_store.py:191` —
  N+1 queries (REMAINING)" + nearby). Not surfaced first by this audit;
  cross-referenced for the followup batch. Fix: hoist
  `load_skill_synonyms()` to module-level lazy cache.

- **🔴 M-G** `ats_scorer.py:165-178` — `_keyword_in_text` falls through to
  an O(N) scan over the entire synonyms dict (~36K entries) for every
  keyword that misses the direct match — per call, per skill. The
  reverse-lookup map should be precomputed once per process.

- **🔴 M-H** `portfolio_variants.py:23-57` — `_load_variants_from_db`
  has THREE bare `except Exception: pass` (the inner JSON parse, the
  outer SQLite open, plus `if not row: return None`). Multiple silent
  failure modes on the lookup hot path; CV ships with non-archetype
  bullets when DB is locked / schema changed / JSON malformed.
  Defer because a redesign (move to `MemoryManager`-style facade or at
  minimum log) interacts with the regex-to-dynamic migration plan.

- **🔴 M-I** `cv_tailor.tailor_summary_and_tagline:336` — validates only
  the **summary**, not the tagline. The prompt at line 286 says
  `"Tagline EXACTLY: {required_format}"` but `validate_summary` ignores
  the tagline field entirely. If the LLM drifts on tagline format
  (drops degree, wrong YOE, wrong role title), the wrong header line
  ships on the CV with no signal. Add a `validate_tagline` mirror.

### Minors

| ID | Location | Description |
|---|---|---|
| 🟡 m-1 | `scan_pipeline.py:664-678` | CV PDF generation runs **before** Gate 4B (line 689-723). If Gate 4B's LLM scrutiny says `needs_review=True`, the PDF is already on disk and `notion_status` flips to "Needs Review". Wasteful disk + ~100ms but not a correctness bug. |
| 🟡 m-2 | `archetype_engine.py:122` | `pattern = re.compile(re.escape(keyword), re.IGNORECASE)` recompiled per keyword per archetype per call. ~30 keywords × 6 archetypes per `detect_archetype` call. Cache once. |
| 🟡 m-3 | `ats_scorer.py:188` | `pattern = r"\b" + re.escape(keyword) + r"\b"` recompiled per keyword inside `_word_present`. Same pattern as m-2. |
| 🟡 m-4 | `generate_cv.py:601` | Renderer strips `^\d+\.\s*` from project title (`_re.sub`) then re-adds `f"{i+1}. "` at line 605, so `project_portfolio.get_best_projects_for_jd:169` can prefix `"1. "` and the renderer doesn't double-number. Two-place duplication; prefer one canonical numbering site. |
| 🟡 m-5 | `generate_cv.py:525-526` + `application_materials.py:39` | Company sanitisation `(company or "Company").replace(" ", "_").replace("/", "_")` does **not** strip domain suffixes (`.com`, `.co.uk`). Per user memory, expected output is `Yash_Bishnoi_ASOS.pdf`, not `Yash_Bishnoi_ASOS.com.pdf`. If `listing.company` carries a domain suffix from the JD scraper, every artefact (PDF filename, dir name, Notion link) bleeds it. |
| 🟡 m-6 | `cv_tailor.py:160` + `generate_cover_letter.py:42` | `_METRIC_RE` regex pattern duplicated. Move to a shared module-level constant. |
| 🟡 m-7 | `archetype_engine.py:18` + `portfolio_variants.py:61` + `project_portfolio.py` (legacy comments only) | `Path(__file__).parent.parent / "data" / ...` instead of centralised `DATA_DIR` from `jobpulse.config`. Same nit as S7 n-3. |
| 🟡 m-8 | `application_materials.py:110` | `db = db or JobDB()` opens a fresh JobDB connection (per-invocation cost) when the caller didn't pass one. Combined with `ensure_tailored_cv_for_job` being called from the pre-flight hot path (job_autopilot.py:661), this is a connection-per-call. Fits Principle §3 "no connection-per-call". |
| 🟡 m-9 | `generate_cv.py:155-156` | `_load_default_projects` returns `get_profile_store().cv_projects()`. If the DB is empty (fresh install), `proj_list = projects or _load_default_projects()` returns `[]` and the `Projects` section in the CV is empty — no fallback. The `get_best_projects_for_jd` upstream falls back to `DEFAULT_PROJECTS` (which is also `_load_default_projects()` after the dirty-branch refactor) so a fresh install is now silent-empty in two places. Add a non-empty assertion or a hardcoded "see GitHub" fallback. |

### Nits

| ID | Location | Description |
|---|---|---|
| ⚪ n-1 | `cv_tailor.py:48` | `_required_tagline_format = build_required_tagline` back-compat alias still referenced inside the same module — collapse callers and remove. |
| ⚪ n-2 | `archetype_engine.py:90-99` | `_TITLE_ARCHETYPE_MAP` is 9 entries; "ai engineer" → `agentic` but `ml engineer` / `mlops` → `data_platform`. Inconsistent grouping — "agentic" is the AI/agent archetype and "ml engineer" should map to the same family for consistency with `_build_archetype_summaries`. Soft naming nit. |
| ⚪ n-3 | `portfolio_variants.py:38-39` | `import sqlite3` and `import json` inside the function — both are imported at module top in many sister files. Lazy import inside a hot lookup buys nothing here (stdlib imports are essentially free post-bootstrap). |
| ⚪ n-4 | `cv_tailor.py:392` | `tailored = _parse(_call_with_correction(_build_prompt()))` — when the LLM returns `None` and `_parse(None)` returns `None`, the validator path is skipped silently. Validator-failure path correctly retries on `error`, but the parse-fail path doesn't even attempt a retry. Borderline — could either retry on parse fail or document why it doesn't. |

### Wiring / doc deltas

| ID | Location | Description |
|---|---|---|
| 🔌 W-1 | `archetype_engine.detect_archetype` and `get_archetype_framing` | Both gated behind `JOBPULSE_ARCHETYPE_ENGINE` (line 73 of pipeline_hooks.py). Default in `pipeline_hooks.feature_enabled` is `false`. `.env` has `JOBPULSE_ARCHETYPE_ENGINE=true`. So in production the archetype pipeline IS active, but anyone running tests / `python -m jobpulse.runner job-process-url` without sourcing `.env` silently gets the static-template branch. Document explicitly. |
| 🔌 W-2 | `route_and_apply.cl_generator` (`scan_pipeline.py:830-851`) vs `application_materials.build_lazy_cover_letter_generator` | Two completely separate lazy-CL generators co-exist — one inline closure created per `route_and_apply` call, one module-level builder. Both call `generate_cover_letter_pdf` but with different argument shapes (the inline generator skips the `tailor_cover_letter_prose` step, the builder includes it). Inline path produces a less-tailored CL than the live-review path. Drift risk. |
| 🔌 W-3 | `pipeline_hooks.enhanced_generate_materials` (`pipeline_hooks.py:96-123`) | Wraps `generate_materials` and applies `normalize_text_for_ats` to `bundle.cv_text` ONLY (not the PDF on disk). Since the CV PDF is already generated by the time `enhanced_generate_materials` post-processes the bundle, the normalised `cv_text` exists only in memory and is fed to nothing downstream — it's used purely by `score_ats` which already ran. Effectively a no-op observability path. |
| 📝 D-1 | `docs/job-application-pipeline.md` | Does not document the lazy-CV path (`ensure_tailored_cv_for_job`) used by live-review and `job_autopilot.handle_apply_review`. Add a §"Lazy CV generation" subsection. |
| 📝 D-2 | `docs/job-application-pipeline.md` | Does not document the two-path split for cover letter generation (eager `cl_generator` closure in `route_and_apply` vs lazy `build_lazy_cover_letter_generator` in live-review). |
| 📝 D-3 | `docs/job-application-pipeline.md` | Mentions ATS normalisation in passing but does not flag that the eager `generate_materials` PDF is generated **before** Gate 4B, so a `Needs Review` Gate 4B verdict still leaves the PDF on disk. |

---

## 3. Cross-module wiring map

```
  scan_window / process_single_url
        │
        ├── pipeline_hooks.with_archetype_detection(listing)  [B if env=true]
        │     └── archetype_engine.detect_archetype()
        │           └── archetype_engine._load_profiles()  (data/archetype_profiles.json)
        │
        ├── prescreen_listings  (Gates 0-3 + 4A — covered in S7)
        │
        └── enhanced_generate_materials
              └── generate_materials
                    ├── notion: create_application_page → page_id
                    ├── repos: fetch_and_cache_repos | screen.best_projects
                    │     └── pick_top_projects (synonyms-aware)
                    │
                    ├── build_extra_skills(req, pref)  (BASE_SKILLS+SYNONYMS)
                    ├── sync_verified_to_profile()  [now logged on fail — B-2]
                    ├── get_best_projects_for_jd(req, pref, archetype)
                    │     ├── SkillGraphStore.get_projects_for_skills (mindgraph.db)
                    │     ├── _portfolio_lookup → _load_portfolio_from_db (user_profile.db)
                    │     └── get_or_generate_variant_bullets (data/portfolio_auto.json + LLM)
                    │
                    ├── if archetype: get_archetype_framing  [B if env=true]
                    │   else:        get_role_profile()
                    │
                    ├── tailor_all_sections [4 parallel LLM calls]
                    │     ├── tailor_summary_and_tagline  → validate_summary
                    │     ├── tailor_experience_bullets   → validate_experience
                    │     ├── tailor_project_bullets      → validate_projects
                    │     └── tailor_cover_letter_prose   → validate_cover_letter
                    │
                    ├── score_ats (cv_text + JD skills) → ATSScore
                    │
                    ├── if ats_score >= 85: generate_cv_pdf → applications/<co>/Yash_Bishnoi_<Co>.pdf
                    │
                    ├── Gate 4B: scrutinize_cv_deterministic (B1)
                    │     └─ if clean/acceptable: scrutinize_cv_llm (B2)
                    │           └─ ScrutinyCalibrator.calibrate (data/cv_scrutiny_calibration.db)
                    │
                    ├── db.save_application(status, ats_score, tier, projects, cv_path)
                    └── update_application_page (Notion)
```

Producer ↔ consumer pairs verified:
- `score_ats` (producer) → `MaterialsBundle.ats_score` (consumer = `route_and_apply` for `auto_submit/review/skip` routing). ✓
- `tailor_all_sections` (producer) → `bundle.cv_text` (consumer = `scrutinize_cv_*` Gate 4B + `generate_cv_pdf`). ✓
- `ScrutinyCalibrator.calibrate` (producer) → `data/cv_scrutiny_calibration.db` (consumer = `gate4_quality.scrutinize_cv_llm.adjusted_threshold` next call). ✓ (already verified in S7 audit §3)
- `archetype_engine.detect_archetype` (producer) → `listing.archetype` (consumer = `generate_materials:594` to pick framing). ✓ (only when env=true)
- `pipeline_hooks.enhanced_generate_materials` normalised `cv_text` (producer) → ??? (consumer = none in apply path). **Wiring gap — see W-3**.

---

## 4. Live evidence

Pre-fix baseline (materials test set):
```
$ python -m pytest tests/test_ats_scorer.py tests/test_github_matcher.py \
    tests/jobpulse/test_application_materials.py \
    tests/jobpulse/test_archetype_engine.py \
    tests/jobpulse/test_portfolio_variants.py \
    tests/jobpulse/test_cv_tailor.py \
    tests/jobpulse/test_generate_cv_wiring.py
126 passed, 1 failed in 20.88s
```
Sole failure: `test_hero_project_has_all_archetypes` — pre-existing
baseline drift on dirty branch (`MANUAL_VARIANTS` was emptied per
`portfolio_variants.py:67-77` comment "All archetype variant bullets
live in user_profile.db.cv_variants now"; the test still expects the
six legacy keys). Not caused by S8 changes.

B-1 reproducer (failing on the broken module-level import, BEFORE applying the fix):
The S7-shape regression test asserts the cache stays `None` after import.
Pre-fix code had no `_DEFAULT_PROFILE_CACHE` symbol at all and ran the
DB read at module load — the new test's reload assertion catches both
"cache is populated by import" and "no lazy accessor exists".

Post-fix, materials sweep:
```
$ python -m pytest <materials test set> tests/jobpulse/test_scan_pipeline.py tests/test_dynamic_cover_letter.py
174 passed, 1 failed in 84.37s
```
The 1 failure remains the same pre-existing `test_hero_project_has_all_archetypes`
baseline drift, NOT caused by S8 changes (verified by grep — none of
the failing assertions reference `_DEFAULT_PROFILE`, `sync_verified_to_profile`,
`logger.warning`, `polish_points_llm`).

Wider repo sweep (excluding live integration tests, after both S8 fixes):
```
$ python -m pytest tests/ --ignore=tests/integration --ignore=tests/jobpulse/integration -q
4152 passed, 8 failed, 123 skipped in 529.35s
```
The 8 failures are pre-existing baseline drift on the dirty branch — the
**same 8 failures** S7's audit reported in `0cbca4c` (note added there).
Verified by grep that none of them import or reference `archetype_engine`,
`scan_pipeline.generate_materials`, `application_materials`,
`cv_templates.generate_cover_letter`, or `cv_tailor`. Failure list:

- `test_field_mapper_real::TestFuzzyCustomAnswer::test_diversity_keyword_fallback`
- `test_cross_platform_field_transfer::TestTextOverlapRanking::test_high_overlap`
- `test_portfolio_variants::TestManualVariants::test_hero_project_has_all_archetypes` (the dirty-branch `MANUAL_VARIANTS` empty-dict test referenced in §4 baseline)
- `test_runner_real::TestKnownCommandRecognition::test_known_command_not_unknown[gmail]`
- `test_no_blocking_sleep::test_no_blocking_sleep_inside_async_functions` (lint flagging a pre-existing sleep in `test_navigation_audit.py:147`)
- `test_agent_eval::test_canonical_flow_harness_passes_all_cases`
- `test_email_preclassifier::TestConfidenceThresholds::test_high_confidence_skips_llm`
- `test_screening_collision_guard::TestPatternOrdering::test_specific_before_general`

None caused by S8 changes. Net pass delta vs S7's wider sweep
(4149 → 4152) is exactly +3 — the three new regression tests landed
this session (`test_module_import_does_not_build_default_profile`,
`test_get_default_profile_caches_after_first_call`,
`test_sync_verified_failure_is_logged_not_swallowed`).

---

## 5. Fixes (this session)

| ID | Severity | Commit | Test |
|---|---|---|---|
| B-1 | blocker | `21e836d` (`fix(materials): S8 audit B-1 — lazy DB-derived archetype default profile`) | `tests/jobpulse/test_archetype_engine.py::TestImportTimeSideEffects::test_module_import_does_not_build_default_profile` + `test_get_default_profile_caches_after_first_call` |
| B-2 | blocker | `d1252a9` (`fix(materials): S8 audit B-2 + M-B/C/D/E — log silent swallows`) | `tests/jobpulse/test_scan_pipeline.py::TestGenerateMaterials::test_sync_verified_failure_is_logged_not_swallowed` |
| M-B | major | same as B-2 | (covered indirectly — log promotion is functionally identical to B-2 fix) |
| M-C | major | same as B-2 | (covered indirectly — log promotion only) |
| M-D | major | same as B-2 | (covered indirectly — log promotion only) |
| M-E | major | same as B-2 | (covered indirectly — log promotion only) |

### Deferred to followup worklist

The following findings from §2 were not fixed this session and have been
appended to `docs/audits/audit-followup-worklist.md` (Subsystem 8 section):

- M-F (`github_matcher` synonyms N+1 — known issue from seven-principles §3, not freshly surfaced)
- M-G (`ats_scorer` O(N) reverse-synonym scan)
- M-H (`portfolio_variants._load_variants_from_db` triple silent swallow)
- M-I (`cv_tailor.tailor_summary_and_tagline` missing tagline validation)
- m-1 .. m-9 (minors)
- n-1 .. n-4 (nits)
- W-1, W-2, W-3 (wiring/doc deltas)
- D-1, D-2, D-3 (architecture-doc deltas — batch with the global doc PR)

Per the audit prompt's STOP CONDITIONS, this session shipped 2 blockers
(B-1, B-2) plus 4 majors (M-B, M-C, M-D, M-E) all sharing the same
root pattern (silent failure of an observable concern). Cross-subsystem
themes (Principle 8 regex-classification creep, Principle 3 N+1
synonym loads) carry forward to the worklist.
