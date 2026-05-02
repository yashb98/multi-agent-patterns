# Rules: Job Autopilot (jobpulse/job_**/*)

## Live Visibility (NON-NEGOTIABLE)
- Browser always headed. Human watches live. No screenshots needed (cron-only exception).
- Logs to stdout; cron streams to Telegram. On ambiguity: STOP, tell human.

## Rate Limits
LinkedIn 15/day (session break 30min every 5 apps, `LINKEDIN_SESSION_CAP=5`) | Greenhouse/Lever 7/day headed | Indeed 8/day | Workday 5/day | Reed 7/day API | TotalJobs 4/day | Generic 5/day | Total 30/day
Session breaks: `SESSION_BREAK_EVERY=5`, `SESSION_BREAK_MINUTES=10`
Safety: `JOB_AUTOPILOT_AUTO_SUBMIT=false` default, `JOB_AUTOPILOT_MAX_DAILY=10`

## Application Engine
- `PlaywrightDriver` (`playwright_driver.py`): Core CDP driver — connects to real Chrome, human-like input, field interaction.
- `PlaywrightAdapter` (`playwright_adapter.py`): ATS adapter extending BaseATSAdapter, default for ALL platforms.
- `driver_protocol.py`: Driver interface protocol shared by both.
- Platform strategies (`ats_adapters/strategy.py`): container hints, field ranges, screening defaults
- Container-scoped CDP scan (`getPartialAXTree`). `FormExperienceDB` stores selectors/timing per domain.
- `FAST_FILL=true` skips delays (Claude Code sessions)

## Anti-Detection
- Headed + `--disable-blink-features=AutomationControlled`. LinkedIn: 50-150ms/char typing, persistent profile.
- Mutex on `apply_job()`. Pipeline lock on `run_scan_window()`. Application recorded BEFORE submission. UTC caps.

## Pre-Generation Checklist (MANDATORY)
1. `sync_verified_to_profile()` — pull latest verified skills from Notion
2. Re-run pre-screen with updated profile
3. THEN generate CV + Cover Letter
GitHub data already synced by 3am cron. Only Notion Skill Tracker needs live sync.

## CV/CL Generation
- ReportLab in `cv_templates/` (NOT xelatex). CV upfront (no LLM), CL lazy (only when form has CL field).
- 2-page max. Quantified metrics in every bullet. Professional tone, no em/en dashes.
- All text justified (TA_JUSTIFY), aligned with section headers. Skills as inline paragraphs.
- Role-adaptive via `get_role_profile()`. YOE: Data Analyst=3+, others=2+.
- 5 base categories + dynamic "Also proficient in:" from JD. `build_extra_skills()` deduplicates synonyms. No soft skills in tech section.
- Naming: `Yash_Bishnoi_{Company}.pdf`. PDF title human-readable. `set_input_files()` uses `{name, mimeType, buffer}`.
- Verify all GitHub URLs. No "JD Match" row. Headers: teal #1a5276.

## Pre-Screen Pipeline
- Route jobs via `classify_action()`, not `determine_match_tier()` — tier is display-only, not a routing signal
- Gate 0 (`recruiter_screen.py`): title + keyword filter, pre-LLM
- Gates 1-3 (`skill_graph_store.py`): kill signals, must-haves, competitiveness. Hybrid skill extraction (582 taxonomy → LLM fallback <10 skills)
- Cross-platform dedup: same company+title = one job. K1 seniority kill: ≥3yr (not ≥5)
- Thresholds: M1 ≥3 of top-5 | M2 ≥2 projects with 2+ overlap | M3 ≥92%

## Gate 4: Quality Check
- Phase A (free): A1 JD quality (<200 chars/<5 skills/boilerplate → block) | A2 Company Blocklist (Notion DB, spam detection) | A3 Company background (soft)
- Phase B (~$0.002): B1 Deterministic CV scrutiny | B2 LLM recruiter review (score ≥7 proceed, <7 → "Needs Review")

## Notion Job Tracker (MANDATORY)
ALL fields filled: Company, Role, Platform, Status, Location, ATS Score, Match Tier, Matched Projects, Applied/Follow-Up Date, Resume/CL Drive links. Never blank — use "N/A". OakNorth = reference standard.

## Notion Skill Tracker
Unverified skills → Notion "Pending" → user marks "I Know"/"Don't Know" → `skill-verify` syncs to MindGraph.

## External Application Engine
`ApplicationOrchestrator`: cookie dismiss → page detect (DOM first, vision fallback <0.6) → navigate → SSO (Google>LinkedIn>Microsoft>Apple) → account → Gmail verify (exponential backoff) → fill → submit.
- Nav learning in SQLite, replays per domain. Cookie dismisser before EVERY page detect.
- `find_next_button()`: Submit > Review > Save & Continue > Continue > Next > Proceed
- Stuck: fingerprint comparison, abort after 2 identical pages. Max 10 nav steps, 20 form pages.
- Screening: ScreeningPipeline (cache + intent + alignment) → LLM fallback → SQLite cache.
- All platforms → NativeFormFiller + `get_strategy(platform)`

**Verification primitives** (post 2026-05 hardening):
- `NavigationActionExecutor.execute()` reads back every fill, retries once on mismatch, returns `ExecutorResult`.
- `FormNavigator._verify_action(pre, post, action_kind)` is the shared verifier — `_phase_act` and `AuthHandler` both call it.
- `PageAction.expected_outcome` is a contract — set it correctly when extending the reasoner prompt.
- On ghost click: cache invalidation + reflection via `reason_with_failure`. Don't bypass — these run even on auth pages now.
- Low-confidence (`< 0.7`) actions trigger a screenshot-based page-type cross-check; disagreement invalidates the cache.

## Form Scoping (`field_scanner.py`)
3-tier: Learned → Auto-detect (common ancestor JS) → Strategy hint. `validate_field_scan()` rejects noise.
Self-healing: stale selectors auto-deleted. Scoped CDP → falls back to `getFullAXTree`.

## Semantic Matching (`semantic_matcher.py`)
5-tier: exact → aliases → numeric → token overlap → substring. `CANONICAL_ALIASES` covers gender/boolean/ethnicity/visa/notice/experience.
`checkbox_intent()`: consent→True, marketing→False. `seed_mapping()` resolves options before LLM.
**No regex for matching** — all field/option matching uses the semantic matcher tiers, never regex patterns. When adding new matching logic, extend semantic_matcher.py tiers or use LLM fallback with caching.

## Adaptive Timing
`FormExperienceDB` stores running averages. Delay = max(measured×1.1, 3.0). Defaults: workday=8s, linkedin=3s, greenhouse=5s, indeed=8s. `FAST_FILL=true` → zero.

## Fill Failure Classification
`_classify_fill_failure()`: no_field→skip | blocked→scroll retry | wrong_value→LLM recovery | readonly→skip | unknown→vision fallback

## Dry Run → Approve → Learn (MANDATORY)
1. `apply_job(dry_run=True)` — fill, stop before Submit
2. Human reviews live, makes corrections
3. `confirm_application()` — MANDATORY. Records quota, fires `post_apply_hook()`, captures corrections as reinforcement signals
4. Filter `_`-prefixed keys before `json.dumps`. Document new quirks here.

## Post-Apply Hook
Fires after EVERY submission (both auto and manual paths). Three concerns:
1. Form experience → `data/form_experience.db` (success never overwritten by failure)
2. Drive upload → CV/CL shareable links
3. Notion update → Applied status, dates, links
Non-blocking. Runs before anti-detection delay.

## Real Data + Wiring + OPRAL (MANDATORY)
Every new job pipeline feature: test with real job URLs, real profile data, real ATS pages (never mocks or stale fixtures). Then verify the full chain fires — `post_apply_hook` → `CorrectionCapture` → `AgentRulesDB` → `strategy_reflector` → `OptimizationEngine` → `AgentPerformanceDB` → Notion update. Not wired = not done.
On error: **Observe → Plan → Reason → Act → Learn**. Capture error context → trace root cause → determine which DB prevents recurrence → fix + re-run real data → emit signal + verify DB persisted + confirm autonomous on next run.

## PDF Upload
Sanitized via PyMuPDF (`_sanitize_pdf()`). `set_input_files()` uses `{name, mimeType: "application/pdf", buffer}` — never bare paths.

## Pipeline Execution (Claude Code)
**Default: Run the agents, don't replace them.** Invoke `apply_job()` / `job-apply-next` → let `ApplicationOrchestrator` + `NativeFormFiller` execute → observe output → diagnose failures → direct corrections. Agents learn from their own runs (CorrectionCapture, AgentRulesDB, strategy_reflector fire). Ad-hoc Playwright scripts bypass the learning loop.
**Manual fallback (only when agents can't handle a specific field/page):** ONE script per form page, fill top-to-bottom, stop before Submit. Feed corrections back into agent DBs so the same issue is handled autonomously next time.

## Cover Letter
Lazy via `cl_generator` callback. `build_dynamic_points()` maps projects→skills. `polish_points_llm()` ~$0.002. Static fallback.

## Security Wall Bypass (MANDATORY)
When any security wall is detected (Cloudflare, Turnstile, reCAPTCHA, hCaptcha, 403, 429), use Playwright's FULL capabilities to bypass it automatically. Human fallback is MANDATORY if auto-bypass fails.

**6-stage bypass pipeline** (`_navigator.py:_bypass_verification_wall`):
1. **Auto-wait** (15s) — Cloudflare JS challenges auto-resolve in 3-10s. Poll every 3s.
2. **Human simulation** — Mouse movement, scrolling, random delays via Playwright `page.mouse.move()`, `page.evaluate("window.scrollBy()")`.
3. **Turnstile checkbox** — Locate Cloudflare iframe → enter content frame → click checkbox/challenge element.
4. **Page reload** — `page.reload(wait_until="domcontentloaded")` clears transient challenges.
5. **Second reload** — `page.reload(wait_until="networkidle")` with longer wait.
6. **Human fallback (MANDATORY)** — Telegram alert with URL + wall type. Poll 120s. Confirm cleared. If still blocked after 120s, skip job + Telegram notification.

**Never abort without human fallback.** Even if all 5 auto-stages fail, the human MUST be asked. The pipeline MUST wait for human response before giving up.

**Platform bypass** (`platform_bypass.py`): When aggregators (Indeed/LinkedIn/TotalJobs/Reed/Glassdoor) block persistently after all 6 stages, resolve the direct ATS URL instead. Resolution order: cached mapping → FormExperienceDB → known ATS board patterns (httpx HEAD) → Playwright web search. Stores results in NavigationLearner, GotchasDB, OptimizationEngine, ExperienceMemory, TrajectoryStore. Wired in `_navigator.py` after `_bypass_verification_wall()` returns `solved=False` on aggregator domains.

**Detection**: `playwright_driver.py:get_snapshot()` inline JS detects Cloudflare selectors, text patterns, and iframe URLs. `page_analysis/classifier.py` weights `verification_wall_present` at 6.0.

## Verification Wall Learning
Universal detector (Turnstile/reCAPTCHA/hCaptcha/403/429). 17 signals per session. Statistical correlation (zero LLM). LLM every 5th block (~$0.002).
Cooldown: 2hr→4hr→48hr exponential. Reset on success. Telegram alert on 3rd block. Adaptive params by risk level.

## Platform Quirks
- **LinkedIn**: Navigate to `/jobs/` first, then specific URL. Easy Apply badge can be `<a>` not `<button>`. Stuck detection: compare chars 300-700, not first 200 (generic wrapper text). Numeric fields: plain integers only (no currency, commas, ranges).
- **Reed**: Modal overlay, pre-filled CV → auto-detect mismatch → Update → file chooser. Google SSO first visit.
- **SmartRecruiters**: Shadow DOM `spl-*` elements. `get_by_label()`/`get_by_role()` pierce shadow. City: type→ArrowDown→Enter. Gender: multi-select `spl-tag`. Separate Resume* field. Page 2: radios + dropdowns + privacy.

## Analytics
`job stats` Telegram → funnel + platform breakdown + gate stats. Module: `job_analytics.py`

## Recruiter Email
Extracted from JD. 3-tier: discard (noreply) | generic_hr (careers) | recruiter (personal). Notion "Recruiter Email" column.
