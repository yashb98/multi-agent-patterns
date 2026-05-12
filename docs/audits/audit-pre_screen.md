# Subsystem 7 — `pre_screen` (line-by-line audit)

**Scope (matches audit prompt entry):**
- Entry: `prescreen_listings()` in scan-window flow + `process_single_url()` for single-URL flow.
- Files: `jobpulse/scan_pipeline.py:prescreen_listings`, `jobpulse/recruiter_screen.py`,
  `jobpulse/skill_graph_store.py`, `jobpulse/skill_extractor.py`, `jobpulse/gate4_quality.py`,
  `jobpulse/company_blocklist.py`, `jobpulse/jd_analyzer.py`, `jobpulse/skill_gap_tracker.py`,
  `jobpulse/cv_templates/scrutiny_calibrator.py`.
- Total LOC audited: ~3 718.

**NOTE on prompt's framing.** The audit prompt says “Entry: `apply_job(url, ...)` → first 5
gates.” That's only half-right: `apply_job()` itself runs no gates — it only calls
`detect_ats_platform` from `jd_analyzer`. The pre-screen stack runs in:
1. `_run_scan_window_inner` → `prescreen_listings` (Gates 1-3 + 4A) → `generate_materials`
   (Gate 4B) — the cron path used by `python -m jobpulse.runner job-scan`.
2. `process_single_url` (Gates 1-3 only — no Gate 0 / Gate 4A; Gate 4B fires inside
   `generate_materials`) — the URL-to-apply path used by `job-process-url`.

The two paths are NOT equivalent and that asymmetry hides bugs (see B-1, B-2 below).

---

## 1. Function inventory + wiring (category column legend at top of worklist doc)

| File | Function | Cat | Direct callers (apply path) |
|---|---|---|---|
| scan_pipeline.py:329 | `prescreen_listings` | A | `job_autopilot._run_scan_window_inner` (cron path) |
| scan_pipeline.py:511 | `generate_materials` | A | `_run_scan_window_inner`, `process_single_url` |
| scan_pipeline.py:963 | `process_single_url` | A | `runner.py:229` (`job-process-url`) |
| scan_pipeline.py:69  | `determine_match_tier` | A | `generate_materials`, `route_and_apply`, `job_autopilot` |
| scan_pipeline.py:101 | `_build_screening_context` | C | only `route_and_apply` (not pre-screen) |
| scan_pipeline.py:130 | `_reorder_projects` | A | `generate_materials` |
| recruiter_screen.py:21 | `gate0_title_relevance` | A | `scan_pipeline.fetch_and_filter_jobs:258` |
| recruiter_screen.py:14 | `_normalize_title` | A | `gate0_title_relevance` |
| skill_graph_store.py:104 | `SkillGraphStore.__init__` | A | `prescreen_listings:347`, `process_single_url:1079` |
| skill_graph_store.py:117 | `_load_synonyms` | A | `__init__` |
| skill_graph_store.py:130 | `_normalize` | A | many callers |
| skill_graph_store.py:139 | `upsert_skill` | C | `github_profile_sync` (3 AM cron, not apply path) |
| skill_graph_store.py:145 | `upsert_project` | C | same as above |
| skill_graph_store.py:182 | `get_skill_profile` | A | `pre_screen_jd`, `_score_competitiveness` |
| skill_graph_store.py:190 | `get_projects_for_skills` | A | `pre_screen_jd`, `cv_tailor` |
| skill_graph_store.py:275 | `get_profile_stats` | C | `runner profile-sync` only |
| skill_graph_store.py:297 | `pre_screen_jd` | A | `prescreen_listings`, `process_single_url` |
| skill_graph_store.py:357 | `_get_adaptive_thresholds` | A | `pre_screen_jd` |
| skill_graph_store.py:383 | `_check_kill_signals` | A | `pre_screen_jd` |
| skill_graph_store.py:424 | `_check_must_haves` | A | `pre_screen_jd` |
| skill_graph_store.py:457 | `_score_competitiveness` | A | `pre_screen_jd` |
| skill_graph_store.py:563 | `_skill_match` | A | many sites |
| skill_extractor.py:97  | `detect_jd_sections` | A | `extract_skills_rule_based` |
| skill_extractor.py:133 | `extract_skills_rule_based` | A | `extract_skills_hybrid` |
| skill_extractor.py:204 | `_detect_industry` | A | `extract_skills_rule_based` |
| skill_extractor.py:214 | `_init_learning_db` | A | `record_extraction`, `compute_noise_skills` |
| skill_extractor.py:238 | `record_extraction` | A | `analyze_jd:430` |
| skill_extractor.py:258 | `compute_noise_skills` | C | hourly optimize (not apply path) |
| skill_extractor.py:309 | `_load_learned_noise` | A | `extract_skills_rule_based` |
| skill_extractor.py:323 | `extract_skills_hybrid` | A | `analyze_jd:426` |
| skill_extractor.py:339 | `_extract_skills_llm` | B | only when rule-based < 10 skills |
| gate4_quality.py:57  | `check_jd_quality` | A | `prescreen_listings:471` |
| gate4_quality.py:117 | `check_company_background` | A | `prescreen_listings:490` |
| gate4_quality.py:206 | `scrutinize_cv_deterministic` | A | `generate_materials:685` |
| gate4_quality.py:254 | `scrutinize_cv_llm` | B | `generate_materials:692` (only if B1 ∈ {clean, acceptable}) |
| company_blocklist.py:47 | `detect_spam_company` | A | `prescreen_listings:434` |
| company_blocklist.py:96 | `BlocklistCache` | A | `prescreen_listings:414` |
| company_blocklist.py:125 | `fetch_blocklist_from_notion` | A | `BlocklistCache.refresh` |
| company_blocklist.py:161 | `flag_company_in_notion` | A | `prescreen_listings:442` |
| jd_analyzer.py:115 | `_canonicalize_url` | A | `generate_job_id` |
| jd_analyzer.py:164 | `generate_job_id` | A | `analyze_jd`, all platforms |
| jd_analyzer.py:177 | `extract_salary` | A | `analyze_jd` |
| jd_analyzer.py:222 | `extract_location` | A | `analyze_jd` |
| jd_analyzer.py:270 | `detect_remote` | A | `analyze_jd` |
| jd_analyzer.py:287 | `detect_seniority` | A | `analyze_jd` |
| jd_analyzer.py:304 | `detect_ats_platform` | A | `analyze_jd`, `applicator.apply_job:380`, `ext_adapter` |
| jd_analyzer.py:327 | `detect_easy_apply` | A | `analyze_jd` |
| jd_analyzer.py:358 | `extract_recruiter_email` | A | `analyze_jd` |
| jd_analyzer.py:390 | `analyze_jd` | A | `analyze_and_deduplicate`, `process_single_url` |
| skill_gap_tracker.py:33 | `_init_db` | A (import-time!) | called at module load |
| skill_gap_tracker.py:66 | `record_gap` | A | `prescreen_listings:366` |
| skill_gap_tracker.py:101 | `get_top_gaps` | C | `runner skill-gaps`, `export_gap_report` |
| skill_gap_tracker.py:156 | `export_gap_report` | C | `runner skill-gaps` (CLI) |
| skill_gap_tracker.py:203 | `get_gap_stats` | C | `runner skill-gaps`, briefing agent |
| scrutiny_calibrator.py:51 | `ScrutinyCalibrator.__init__` | A | `gate4_quality.scrutinize_cv_llm:315`, `generate_materials:707` |
| scrutiny_calibrator.py:80 | `calibrate` | A | `generate_materials:708` |
| scrutiny_calibrator.py:129 | `adjusted_threshold` | A | `scrutinize_cv_llm:316`, `get_insight`, `get_stats` |
| scrutiny_calibrator.py:182 | `get_insight` | C | not called from apply path |
| scrutiny_calibrator.py:215 | `update_outcome` | C | `rejection_analyzer` (post-apply, not pre-screen) |
| scrutiny_calibrator.py:249 | `get_stats` | C | runner CLI |

---

## 2. Findings (severity-tagged)

### Blockers / majors

- **B-1 BLOCKER** `scan_pipeline.py:1092` — `process_single_url` checks
  `pre_screen.tier == "rejected"`, but `SkillGraphStore.pre_screen_jd` only ever sets
  `tier ∈ {"reject", "skip", "apply", "strong"}` (skill_graph_store.py:325). Result:
  every Gate 1 kill (seniority ≥3yr, missing primary skill, foreign-domain top-3) is
  silently bypassed in the single-URL flow. The job proceeds to `generate_materials`
  → `route_and_apply` as if it had passed, then is rejected only by ATS-score
  thresholds. The cron path (`prescreen_listings:385`) uses the correct `"reject"`
  literal, so this only fires for the `job-process-url` CLI path.

- **B-2 BLOCKER** `skill_gap_tracker.py:63` — `_init_db()` runs at module-import time.
  Opens a SQLite connection to `data/skill_gaps.db` and writes schema unconditionally.
  Violates seven-principles §1 ("No module-level code that makes network calls, opens
  DB connections, or reads files — use lazy init on first use"). Side effects: tests
  importing the module touch the production DB unless they patch `DATA_DIR` first;
  CLI tools that import `scan_pipeline` (which lazy-imports skill_gap_tracker but the
  function-level imports still trigger module init) pay the cost. Fix: move DB init
  into `_get_conn` / `record_gap` lazy path.

- **B-3 BLOCKER** `company_blocklist.py:135-156` — `fetch_blocklist_from_notion`
  pagination loop has no max-iteration guard. Notion API is generally well-behaved,
  but Principle 4 ("Loops MUST have a max iteration bound") is explicit. A
  malformed/cached `next_cursor` would block scan startup forever (this is called
  early via `BlocklistCache.refresh` inside `prescreen_listings:416`).

- **M-A** `scan_pipeline.py:487-489` — `except (AttributeError, Exception): past_apps = []`
  silently swallows ALL exceptions for `db.get_applications_by_company`. Reasons to
  fail include schema drift, locked DB, legitimate AttributeError. Errors here mean
  Gate 4 misses "previously applied" duplicates and re-applies. Should at minimum
  log warning with exc_info.

- **M-B** `jd_analyzer.py:222-257 / extract_location` — fallback ordering bug. The
  `_REMOTE_LOCATION_RE` check (line 247) runs BEFORE the UK-city scan (line 253).
  Any JD that mentions "remote" anywhere — even in non-location context like
  "we support remote-friendly culture in our London office" — returns `"Remote"`
  instead of the explicit city. Repro: JD with "Remote" word + "London" city → wrong
  location.

### Minors

- **m-1** `skill_graph_store.py:215-217` — `total_projects = ... or 1` is computed
  but never used in the function. Wasted query per `pre_screen_jd` call (single
  COUNT statement, but still useless).
- **m-2** `skill_graph_store.py:432` — `[m.lower() for m in matched]` is rebuilt
  inside list comprehension `for s in top5 if s in [...]`. `matched` is already
  lowercased upstream (`pre_screen_jd:313`), so the `m.lower()` is a no-op AND
  the inner comprehension is recomputed for every iteration of `top5` (O(n×m)).
- **m-3** `skill_graph_store.py:408` — `if primary and self._normalize(primary) not in profile and not self._skill_match(primary, profile):` —
  `_skill_match` already starts with `if normalized in profile: return True`, so
  the explicit `not in profile` check is redundant.
- **m-4** `gate4_quality.py:217-221` — bullet-line heuristic flags any line >20 chars
  not all-caps as a bullet. Causes false-positive "missing metric" warnings on
  paragraph-style summary lines.
- **m-5** `recruiter_screen.py:44-49` — `try / except: pass` swallows AgentRulesDB
  errors silently. Should `logger.debug(..., exc_info=True)` minimum.
- **m-6** `jd_analyzer.py:145` — `import re` inside `_canonicalize_url`; already
  imported at module top.
- **m-7** `skill_extractor.py:392-396` — `_FakeChoice` wrapper is unnecessary; the
  string returned by `cognitive_llm_call` could be parsed directly.
- **m-8** `gate4_quality.py:130` — generic-company detection: `Cloud Solutions Ltd`
  (3 words all in `GENERIC_WORDS`) is flagged generic, even though it's plausibly
  a real company name. Soft flag only, but produces noise.
- **m-9** `scan_pipeline.py:1078-1090` — `process_single_url` skips Gate 0 (title)
  and Gate 4A (blocklist, JD quality, spam). For ad-hoc URL submissions this may
  be intentional (user vetted the URL), but the asymmetry isn't documented in the
  function docstring or `docs/job-application-pipeline.md`.

### Nits

- **n-1** `jd_analyzer.py:100-103` — `_SINGLE_SALARY_RE` defined, never referenced.
  Dead code.
- **n-2** `scrutiny_calibrator.py:184` — `import json` inside `get_insight` is unused
  (no JSON ops in the function body).
- **n-3** `skill_extractor.py:32-33` — `SYNONYMS_PATH` and `_LEARNING_DB_PATH`
  constructed via `Path(__file__).parent.parent` instead of the centralized
  `DATA_DIR` from `jobpulse.config`.

### Wiring / doc deltas

- **🔌 W-1** `scan_pipeline.process_single_url` doesn't call `record_gap` — the
  scan-pipeline path does (line 365), so single-URL applies don't contribute to the
  skill-gap tracker. Inconsistent learning signal coverage.
- **📝 W-2** `docs/job-application-pipeline.md` does not document the scan vs
  single-URL gate-coverage asymmetry.

---

## 3. Cross-module wiring map

```
fetch_and_filter_jobs ──(raw_jobs)──> analyze_and_deduplicate ──(JobListings)──>
prescreen_listings ──(SkillGraphStore.pre_screen_jd)──> {reject|skip|apply|strong}
       │
       ├─ Gate 1: kill signals (seniority/primary skill/foreign domain)
       ├─ Gate 2: must-haves (M1: 3/5 top required skills, M2: 2 strong projects, M3: 92% match)
       ├─ Gate 3: competitiveness score (hard skill + project + coherence + domain + recency)
       │
       ├─ skill_gap_tracker.record_gap   (writes data/skill_gaps.db)
       ├─ skill_tracker_notion.sync_skills_to_notion  (writes Notion Skill Tracker)
       │
       ├─ Gate 4A:
       │    ├─ BlocklistCache.is_blocked  (reads Notion blocklist)
       │    ├─ detect_spam_company       (keyword + listing-count)
       │    ├─ db.get_company_reliability (reads applications.db)
       │    ├─ check_jd_quality          (writes gate_decisions table)
       │    └─ check_company_background  (writes gate_decisions table)
       │
       └─> generate_materials
              ├─ Gate 4B: scrutinize_cv_deterministic  (B1)
              ├─ Gate 4B: scrutinize_cv_llm            (B2 — only if B1 ∈ {clean, acceptable})
              └─ ScrutinyCalibrator.calibrate          (writes data/cv_scrutiny_calibration.db)
```

Producer ↔ consumer pairs verified:
- `record_gap`(producer) → `data/skill_gaps.db`(store) → `get_top_gaps`(consumer
  via `runner skill-gaps` + briefing). ✓
- `ScrutinyCalibrator.calibrate`(producer) → `data/cv_scrutiny_calibration.db`(store)
  → `adjusted_threshold`(consumer in `gate4_quality.scrutinize_cv_llm:315`). ✓
- `flag_company_in_notion`(producer) → Notion blocklist DB(store) → `BlocklistCache.refresh`
  reads back on next scan. ✓
- `JobDB.record_gate_decision`("jd_quality"/"company_background") — table exists
  (gate_decisions), but no consumer found in pre-screen subsystem; possibly read
  by `job_analytics.get_funnel_stats`. Wiring weak but not a blocker.

---

## 4. Live evidence

Pre-fix baseline (pre-screen test files only, full pre-fix tree):
```
$ python -m pytest tests/jobpulse/test_scan_pipeline.py tests/test_jd_analyzer.py \
    tests/test_skill_gap_tracker.py tests/test_skill_graph_store.py \
    tests/test_company_blocklist.py tests/test_recruiter_screen.py \
    tests/test_skill_extractor.py tests/test_gate4_quality.py \
    tests/jobpulse/test_scrutiny_calibrator.py
136 passed in 48.05s
```

B-1 reproducer (failing on the broken comparison, BEFORE applying the fix):
```
FAILED tests/jobpulse/test_scan_pipeline.py::TestProcessSingleUrlGateRouting::test_gate1_reject_short_circuits
E   AssertionError: Gate 1 reject must short-circuit, got status=skipped
E   assert 'skipped' == 'rejected'
```
With `tier="reject"` returned by SkillGraphStore, `process_single_url` fell
through to material generation; the bug surfaced as `status=skipped` (ATS
score 0 → skip-tier route). After the one-character fix, the assertion holds.

Post-fix sweep (same test set + the 6 new regression tests added in this
session):
```
142 passed in 37.81s
```

Wider repo sweep (excluding live integration tests, after all S7 fixes):
```
4149 passed, 8 failed, 106 skipped in 594.14s
```
The 8 failures are pre-existing baseline drift on the dirty branch — verified
by grep that none of them import or reference `scan_pipeline`,
`jd_analyzer`, `skill_gap_tracker`, or `company_blocklist`. Failure list:
`test_field_mapper_real`, `test_cross_platform_field_transfer`,
`test_portfolio_variants`, `test_runner_real[gmail]`,
`test_no_blocking_sleep` (lint flagging a pre-existing sleep in
`test_navigation_audit.py:147`), `test_agent_eval`,
`test_email_preclassifier`, `test_screening_collision_guard`. None caused
by S7 changes.

---

## 5. Fixes (this session)

| ID | Severity | Commit | Test |
|---|---|---|---|
| B-1 | blocker | `7e10b10` (`fix(scan): S7 audit B-1 — process_single_url tier comparison`) | `TestProcessSingleUrlGateRouting::test_gate1_reject_short_circuits` |
| B-2 | blocker | `45749a2` (`fix(skill_gap): S7 audit B-2 — drop import-time DB init`) | `test_module_import_does_not_apply_schema_eagerly` |
| B-3 | blocker | `0de4527` (`fix(blocklist): S7 audit B-3 — bounded Notion pagination loop`) | `TestFetchBlocklistFromNotion::{test_aborts_on_repeated_cursor, test_respects_max_pages_cap}` |
| M-A | major | `4fa9fb0` (`fix(pre_screen): S7 audit M-A + M-B — log + reorder`) | `test_get_applications_by_company_failure_logs_warning` |
| M-B | major | same commit as M-A | `test_extract_location_city_beats_remote_mention` |

(Commit hashes resolved at end of session; see `git log --grep="S7 audit"`.)

### Deferred to followup worklist

The following findings from §2 were not fixed this session and have been
appended to `docs/audits/audit-followup-worklist.md` (Subsystem 7 section):

- m-1 .. m-9 (minors)
- n-1 .. n-3 (nits)
- W-1 single-URL flow doesn't call `record_gap` or run Gate 0 / Gate 4A
- W-2 `docs/job-application-pipeline.md` doesn't document the
  scan-vs-single-URL gate-coverage asymmetry

Per the audit prompt's STOP CONDITIONS, this session shipped 3 blockers + 2
majors (within the > 5 blockers limit), defer the rest. Cross-subsystem
themes (Principle 8 regex-for-classification creep, etc.) carry forward.
