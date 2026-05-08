# JobPulse — Daily Automation Agents

10+ autonomous agents running 24/7 via macOS daemon + cron + GitHub Actions.

## Agents
- gmail_agent.py — Email classification (pre-classifier + LLM)
- calendar_agent.py — Today + tomorrow events, reminders
- github_agent.py — Yesterday's commits (Commits API), trending repos
- arxiv_agent.py — Daily top 5 papers, multi-criteria ranking
- notion_agent.py — Tasks CRUD, dedup, priorities, due dates, subtasks, weekly plan
- budget_agent.py — Spending/income/savings, 17 categories, recurring, alerts, undo
- budget_tracker.py — Weekly archival, category sub-pages, weekly comparison
- budget_salary.py — Hours at £13.99/hr, tax calc, Notion timesheet
- briefing_agent.py — Collects all agents → RLM synthesis → Telegram
- job_autopilot.py — Scan → analyze JD → tailor CV → ATS score → apply/queue
- cv_templates/generate_cv.py — ReportLab PDF CV generator
- cv_templates/generate_cover_letter.py — ReportLab PDF cover letter
- ats_scorer.py — Deterministic ATS scoring (0-100)
- skill_extractor.py — Rule-based JD skill extraction (582-entry taxonomy)
- recruiter_screen.py — Gate 0 title filter (pre-LLM)
- skill_graph_store.py — 4-gate pre-screen (Gates 1-3), MindGraph abstraction
- github_profile_sync.py — Nightly 3am sync → MindGraph graph
- skill_gap_tracker.py — Records missing skills, exports ranked CSV
- skill_tracker_notion.py — Notion Skill Tracker: pending skills for verification
- verification_detector.py — Universal CAPTCHA/verification wall detection + human interaction simulation
- scan_learning.py — Scan learning engine: 17 signals, statistical correlation
- drive_uploader.py — Google Drive auto-upload for CV/CL PDFs
- gate4_quality.py — Gate 4: JD quality, company blocklist, CV scrutiny, LLM review
- company_blocklist.py — Notion Company Blocklist: spam detection
- correction_capture.py — Reinforcement learning from user corrections: diffs agent vs user values, caches corrections, feeds back into screening answers
- job_analytics.py — Conversion funnel, platform breakdown, gate stats
- ats_adapters/smartrecruiters.py — SmartRecruiters adapter (shadow DOM, spl-* web components, Playwright CDP)
- application_orchestrator.py — Re-export shim. Actual code in `application_orchestrator_pkg/`:
  - `_navigator.py` — navigation/redirect handling
  - `_form_filler.py` — delegates to NativeFormFiller
  - `_executor.py` — action execution (fill/click/upload)
  - `_auth.py` — login, signup, email verification
- form_experience_db.py — Per-domain form experience store (SQLite): adapter, pages, fields, timing
- page_analyzer.py — 3-tier page type detection: DOM classifier → semantic reasoning (LLM, cached) → vision fallback
- page_analysis/page_reasoner.py — LLM semantic page understanding: returns action + target + reasoning, cached per domain
- post_apply_hook.py — Unified post-apply: form experience DB, Drive upload, Notion update
- cookie_dismisser.py — Pattern-based cookie banner detection and dismissal
- account_manager.py — SQLite credential store per domain, ATS_ACCOUNT_PASSWORD
- gmail_verify.py — Exponential backoff Gmail polling, HTML verification link extraction
- navigation_learner.py — Per-domain navigation sequence save/replay (SQLite)
- sso_handler.py — SSO button detection (Google > LinkedIn > Microsoft > Apple)
- screening_answers.py — Pattern-based screening question answers + LLM fallback + SQLite cache
- liveness_checker.py — Ghost job detection: 12 expired patterns (EN/DE/FR), apply-button detection
- ats_api_scanner.py — Zero-browser ATS API scanning (Greenhouse/Ashby/Lever REST APIs)
- rejection_analyzer.py — Statistical rejection pattern analysis: blocker classification, recommendations
- followup_tracker.py — Follow-up cadence tracker: urgency tiers (urgent/overdue/waiting/cold), SQLite
- interview_prep.py — STAR+Reflection interview prep: skill-to-project mapping, story templates

## Cognitive Engine Integration
Agents using CognitiveEngine:
- `gmail_agent.py` — email classification (domain: `email_classification`)
- `screening_answers.py` — screening question answers (domain: `screening_answers`)
- `form_engine/field_mapper.py` — recovery fallback for failed field fills (domain: `form_recovery`)
- `native_form_filler.py` — stuck-page navigation recovery (domain: `form_navigation`)
- `shared/optimization/_policy.py` — low-confidence optimization decisions (domain: `optimization`)

Cron runs create engine → think per sub-task → flush() at end. Templates persist across runs.
Kill switch: `COGNITIVE_ENABLED=false`

## Dispatch
Enhanced Swarm when JOBPULSE_SWARM=true (default). Flat dispatcher when false.
IMPORTANT: New intents MUST be added via handler_registry.py + intent_registry.py + command_router.py. Both dispatcher.py AND swarm_dispatcher.py consume from get_handler_map().

## Code Exploration — Use MCP Tools First
Use CodeGraph MCP tools for ALL code exploration. Never use raw Grep/Glob.
- `find_symbol` — locate any function/class definition
- `callers_of` / `callees_of` — trace call chains
- `impact_analysis` — blast radius of a change
- `semantic_search` — find code by meaning
- `module_summary` — overview of a module's structure
- `grep_search` — ripgrep + code graph enrichment for literal/regex/TODO search with risk ranking
One MCP call replaces 5-15 Grep/Glob/Read calls. Brief subagents to do the same.

## Rules
All jobpulse rules in `.claude/rules/jobpulse.md`. Job autopilot rules in `.claude/rules/jobs.md`.
Use `semantic_search` to retrieve detailed rules on demand — all .md files are indexed with embeddings.

## 5 Telegram Bots
Main (tasks, calendar, briefing, remote) | Budget | Research | Jobs | Alert (send-only)
All fall back to `TELEGRAM_BOT_TOKEN` if dedicated token not set.

## Env Vars
**Required:** `OPENAI_API_KEY` `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID`
**Notion:** `NOTION_API_KEY` `NOTION_TASKS_DB_ID` `NOTION_RESEARCH_DB_ID` `NOTION_PARENT_PAGE_ID` `NOTION_APPLICATIONS_DB_ID`
**Jobs:** `REED_API_KEY` `GITHUB_TOKEN` `JOB_AUTOPILOT_AUTO_SUBMIT=false` `JOB_AUTOPILOT_MAX_DAILY=10`
**Playwright:** `ATS_ACCOUNT_PASSWORD` (for Greenhouse/Lever/Workday logins)

## Application Orchestrator (Playwright)
Cookie dismiss → site prompt dismiss → security wall bypass → hybrid page detect → semantic reasoning → SSO → account create → Gmail verify → multi-page fill → submit
Navigation learning replays per domain (SQLite). Max 10 nav steps, 20 form pages.
**Security wall bypass**: 6 stages (auto-wait → human simulation → Turnstile click → reload × 2 → human fallback via Telegram). Human fallback is MANDATORY — never skip.
**Platform bypass**: When aggregators (Indeed/LinkedIn/TotalJobs/Reed/Glassdoor) block persistently, `platform_bypass.py` resolves the direct ATS URL via: cached mapping → FormExperienceDB → known ATS board patterns (httpx HEAD) → Playwright web search. Stores results across all learning systems.
**Semantic reasoning**: When DOM classifier is uncertain, LLM analyzes page text/buttons/fields and returns action (dismiss_dialog, click_apply, fill_form, etc.). Cached per domain+content hash.
**Per-action verification**: Every `NavigationActionExecutor.execute()` returns an `ExecutorResult` with per-fill verified/failed counts. Failures emit `failure` signals via `emit_fill_failures`. Both `_phase_act` and `AuthHandler.handle_login/handle_signup` route through `FormNavigator._verify_action`, which produces an `ActionVerification` (pre/post URL + content hash + ghost-click flag + `expected_outcome_met`).
**Reasoner contract**: `PageAction` includes `expected_outcome` (`url_changes|fields_filled|dialog_dismissed|page_unchanged|unknown`). The reasoner applies a field-count guard that lowers `confidence` when required snapshot fields are dropped from `field_fills`.
**Failure recovery**: On confirmed ghost click → `PageReasoner.invalidate(snapshot)` + `reason_with_failure(snapshot, failure_context)` for re-grounding. When `PageAction.confidence < 0.7`, the navigator runs `classify_page_type_from_screenshot` and escalates on disagreement.

## Adaptive Form Pipeline (form_engine/)
The form-filling pipeline uses 3 layers of adaptive intelligence:

**Container Scoping** — 3-tier resolution prevents scanning full-page noise:
1. Learned: `FormExperienceDB.get_container(domain)` — stored selector from prior fills
2. Auto-detect: JS common-ancestor of form elements with submit button check
3. Strategy hint: `strategy.form_container_hint()` — platform-specific CSS selector
- Scoped scan uses `Accessibility.getPartialAXTree` (CDP) — scans only the container subtree
- Self-healing: if stored selector returns 0 fields, deletes and re-detects
- `validate_field_scan()` rejects noise: zero fields, too many fields (>1.5x expected), duplicate labels

**Semantic Option Matching** — `semantic_option_match()` in `form_engine/semantic_matcher.py`:
1. Exact case-insensitive match
2. Canonical aliases (male→Man, female→Woman, yes→true, UK→United Kingdom, etc.)
3. Numeric range matching (3→"2-5 years")
4. Token overlap scoring (highest-overlap wins, threshold 0.3)
5. Substring containment
- `checkbox_intent(label)` returns True (consent→check), False (marketing→skip), or None (unknown)
- `seed_mapping()` routes dropdown/radio values through `_resolve_with_options()` automatically

**Adaptive Timing** — measured delays replace hardcoded values:
- `FormExperienceDB.store_timing()` records running averages (hydration, fill, transition ms)
- `_get_adaptive_page_delay(platform, timing_data)` derives delays from measurements
- `FAST_FILL=true` env var = zero delays for Claude Code sessions
- Strategy defaults when no data: workday=8s, linkedin=3s, greenhouse=5s
- Minimum 3s floor even with fast measured timing

**Fill Failure Classification** — `_classify_fill_failure()`:
- `no_field` → skip (field doesn't exist on page)
- `blocked` → retry with scroll/click workaround
- `wrong_value` → LLM recovery suggests alternate value
- `readonly` → skip (pre-filled by ATS)
- `unknown` → vision fallback

**Platform Strategies** (`ats_adapters/strategy.py`):
- `BasePlatformStrategy` ABC. Of 17 declared methods, only **6 are reachable in the default apply path**: `pre_fill`, `fill_combobox`, `form_container_hint`, `expected_field_range`, `extra_label_mappings`, `normalize_label`. (`screening_defaults` was deliberately removed — PII policy. The remaining methods — `submit_selectors`, `next_page_selectors`, `post_page`, `known_widget_libraries`, `apply_button_selectors`, `wait_for_form_hydrated_ms`, `iframe_names`, `custom_field_scan`, `field_fill_overrides` — are only consulted via `form_engine/engine.py` `FormFillEngine`, which is gated behind `UNIFIED_FORM_ENGINE=true` and **not enabled in production**. Tracked in `pipeline-bugs.md` S12 D-12.1 / D-12.2.)
- `get_strategy(platform)` returns the registered strategy or GenericStrategy fallback
- LinkedIn: container `.jobs-easy-apply-modal`, range 3-10
- Greenhouse: container `#application`, range 3-15
- Workday: range 3-20, hydration 10s
- Strategies store successful containers via `FormExperienceDB.store_container()` after fill

**Adapter Registry** (`ats_adapters/__init__.py`):
- `get_adapter()` takes **no arguments** — the universal `playwright_adapter` is returned for every platform after the 2026-04 unification. Adapter selection by `ats_platform` happens at the strategy layer (`get_strategy(platform)`), not the adapter layer.

## Dry Run & Platform Learning
- Always dry-run new platforms first: `apply_job(url, dry_run=True)`
- NativeFormFiller handles modal-based CV uploads (Reed pattern: detect CV mismatch → Update → file chooser)
- Internal dict keys (_stream, _gotchas, _job_context) filtered before JSON serialization
- Platform quirks documented in `.claude/rules/jobs.md` under "Platform-Specific Quirks"

## Cognitive Reasoning Integration
Agents opt into `shared/cognitive/CognitiveEngine` for self-improving reasoning:
- `gmail_agent.py` — email classification (domain: `email_classification`, medium stakes)
- `screening_answers.py` — LLM fallback for screening questions (domain: `screening_answers`, medium stakes)
- `form_engine/field_mapper.py` — cognitive fallback when direct LLM recovery fails (domain: `form_recovery`)
- `native_form_filler.py` — cognitive unstuck when form pages loop (domain: `form_navigation`)
- Kill switch: `COGNITIVE_ENABLED=false` disables everywhere, falls back to direct LLM
- Agents use lazy singleton init — zero overhead if cognitive engine isn't needed
- Engine calls `flush_sync()` is the caller's responsibility at end of batch/cron run

## AI Assist Learning Pipeline
When Kimi, Claude, Codex, or any external AI fixes form fields directly in the browser:

```python
from jobpulse.ai_assist_logger import get_ai_assist_logger

logger = get_ai_assist_logger()
session = logger.start_session("kimi", domain="greenhouse.io", platform="greenhouse")
logger.record_fix(session.session_id, "Salary", "", "80000", reasoning="JD midpoint")
logger.record_strategy(session.session_id, "greenhouse.io", "fill_technique",
                       description="Click label first", selector_pattern="[data-qa='salary']")
logger.finalize_session(session.session_id, push_to_learning=True)
```

Fixes automatically flow to:
- `CorrectionCapture` (`field_corrections.db`) — same table as human corrections
- `GotchasDB` (`form_gotchas.db`) — AI-discovered platform quirks
- `OptimizationEngine` — `correction` + `adaptation` signals
- `AgentPerformanceDB` — `ai_agent_name`, `ai_fixes_count`, `ai_reasoning_summary`

CLI: `python -m jobpulse.runner ai-assist-summary [agent] [days]`

## Memory Layer Integration
All old API calls (`learn_fact`, `record_episode`, `learn_procedure`) now automatically
feed the 3-engine memory stack (SQLite + Qdrant + Neo4j) on the **write path**. No caller
code changes required.

**Read-path asymmetry**: `MemoryManager.get_procedural_entries` and `get_episodic_entries`
still read from JSON-only legacy stores (capped at 100 / 200 entries) while SQLite has
~19 800 procedural / ~200 episodic rows. Cognitive consumers (`_classifier`, `_engine`,
`_strategy`, `_reflexion`) therefore see ~1/4 of the procedural memory. Tracked in
`pipeline-bugs.md` S11 M-11.C.

Forgetting sweep runs automatically every hour via the daemon optimization tick.

## Undocumented Subsystems

### Screening Pipeline (10 files)
`screening_pipeline.py` (orchestrator), `screening_decomposer.py` (compound question splitting), `screening_detector.py` (universal detector), `screening_feedback_loop.py` (corrections teach pipeline), `screening_intent.py` (embedding-based intent), `screening_option_aligner.py` (answer-to-option alignment), `screening_outcome_recorder.py` (centralized feedback), `screening_pattern_extractor.py` (auto-pattern extraction), `screening_semantic_cache.py` (Qdrant cache), `screening_validator.py` (post-generation quality checks).

### ATS Adapters (`ats_adapters/`)
`base.py` (BaseATSAdapter ABC), `discovery.py` (auto-discovery from URL/DOM).
Platform adapters: `ashby.py`, `greenhouse.py`, `icims.py`, `indeed.py`, `lever.py`, `linkedin.py`, `workday.py`, `generic.py`.

### Platform Adapters (`platforms/`)
`base.py`, `discord_adapter.py`, `slack_adapter.py`, `telegram_adapter.py`.

### A/B Testing
`ab_testing.py` (engine comparison), `ab_dashboard.py` (Telegram dashboard), `tracked_driver.py` (per-field metrics, ABTracker).

### Dispatch & Routing
- `command_router.py` — Intent enum + classification (43 intents)
- `handler_registry.py` — Shared handler map consumed by both dispatchers
- `intent_registry.py` — Canonical intent groupings (budget, jobs, research, etc.)
- `dispatcher.py` — Flat intent dispatch
- `swarm_dispatcher.py` — Enhanced Swarm dispatch (GRPO + personas)

### Application Pipeline
- `playwright_driver.py` — Core CDP driver, foundation of all form filling
- `playwright_adapter.py` — ATS adapter extending BaseATSAdapter, default for ALL platforms
- `driver_protocol.py` — Driver interface protocol shared by both drivers
- `applicator.py` — Job application executor (dry run + submit paths)
- `native_form_filler.py` — Adaptive form filler (field scan → map → fill → verify)
- `scan_pipeline.py` — Orchestrates: pre-screen → CV/CL gen → materials prep
- `draft_applicator.py` / `draft_queue.py` — Human-in-the-loop draft review flow
- `pre_submit_gate.py` — LLM pre-submit quality review before submission
- `cross_platform_field_transfer.py` / `platform_transfer.py` — Semantic field transfer (Thompson Sampling)

### Learning & Adaptation
- `strategy_reflector.py` — Post-apply strategy analysis → TrajectoryStore + ExperienceMemory
- `agent_rules.py` — AgentRulesDB: stores corrections as rules for NativeFormFiller
- `agent_performance.py` — AgentPerformanceDB: per-application metrics + success tracking
- `trajectory_store.py` — Application trajectory recording for optimization
- `ai_assist_logger.py` — Logs AI-assisted fixes (Kimi/Claude/Codex) into learning pipeline
- `gotchas_db.py` — Platform-specific gotchas learned from failures
- `platform_bypass.py` — Direct ATS URL resolution when aggregators block (cache → FormExperienceDB → ATS patterns → web search). Emits to NavigationLearner, GotchasDB, OptimizationEngine, ExperienceMemory, TrajectoryStore.

### CV & Profile
- `application_materials.py` — Material generation coordinator (CV + CL + profile)
- `cv_tailor.py` — JD-adaptive CV content selection
- `archetype_engine.py` — Role archetype detection for CV profile tuning
- `portfolio_variants.py` — Project variant selection per JD requirements
- `github_profile_sync.py` — Nightly GitHub → MindGraph sync

### Scanning & Analysis
- `gate_threshold_adapter.py` — Adaptive gate thresholds from historical data
- `ghost_detector.py` — Expired/ghost job detection (12 patterns, 3 languages)
- `jd_analyzer.py` — JD parsing, ATS platform detection, requirement extraction
- `job_dedup.py` — Cross-platform dedup (same company+title = one job)
- `ext_adapter.py` — External job board adapter

### Webhook/API Layer
- `webhook_server.py` — FastAPI server (port 8080, Swagger at /docs)
- `job_api.py` — Job CRUD API endpoints
- `analytics_api.py` — Analytics dashboard API
- `calibration_api.py` — Gate calibration endpoints
- `health_api.py` — Health check endpoint for daemon monitoring

### Infrastructure
- `config.py` — ALL env vars centralized (never os.getenv() elsewhere)
- `runner.py` — CLI entrypoint: daemon, briefing, job-scan, multi-bot, etc.
- `multi_bot_listener.py` — Concurrent polling for all 5 Telegram bots
- `voice_handler.py` — Whisper transcription for voice messages
- `healthcheck.py` — Daemon health monitoring + Telegram alerts
- `rate_limiter.py` — Per-platform daily caps + session breaks

## Commands
```
python -m jobpulse.runner daemon         # Start Telegram daemon
python -m jobpulse.runner multi-bot      # Start all 5 bots
python -m jobpulse.runner briefing       # Morning digest
python -m jobpulse.runner chrome-pw      # Launch Chrome with CDP for Playwright
python -m pytest tests/ -v -k "jobpulse" # Run JobPulse tests only
```
