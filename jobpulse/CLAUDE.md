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
- salary_agent.py — Hours at £13.99/hr, tax calc, Notion timesheet
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
- verification_detector.py — Universal CAPTCHA/verification wall detection
- scan_learning.py — Scan learning engine: 17 signals, statistical correlation
- drive_uploader.py — Google Drive auto-upload for CV/CL PDFs
- gate4_quality.py — Gate 4: JD quality, company blocklist, CV scrutiny, LLM review
- company_blocklist.py — Notion Company Blocklist: spam detection
- correction_capture.py — Reinforcement learning from user corrections: diffs agent vs user values, caches corrections, feeds back into screening answers
- job_analytics.py — Conversion funnel, platform breakdown, gate stats
- ats_adapters/smartrecruiters.py — SmartRecruiters adapter (shadow DOM, spl-* web components, Playwright CDP)
- application_orchestrator.py — Full external application lifecycle: navigate → account → verify → fill → submit
- form_experience_db.py — Per-domain form experience store (SQLite): adapter, pages, fields, timing
- page_analyzer.py — Hybrid DOM+Vision page type detection (PageType enum: 8 types)
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
Agents using CognitiveEngine: gmail_agent (email classification), job_autopilot (form strategy).
Cron runs create engine → think per sub-task → flush() at end. Templates persist across runs.
Kill switch: `COGNITIVE_ENABLED=false`

## Dispatch
Enhanced Swarm when JOBPULSE_SWARM=true (default). Flat dispatcher when false.
IMPORTANT: New intents MUST be added to BOTH dispatcher.py AND swarm_dispatcher.py.

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
Cookie dismiss → hybrid page detect → SSO → account create → Gmail verify → multi-page fill → submit
Navigation learning replays per domain (SQLite). Max 10 nav steps, 20 form pages.

## Dry Run & Platform Learning
- Always dry-run new platforms first: `apply_job(url, dry_run=True)`
- NativeFormFiller handles modal-based CV uploads (Reed pattern: detect CV mismatch → Update → file chooser)
- Internal dict keys (_stream, _gotchas, _job_context) filtered before JSON serialization
- Platform quirks documented in `.claude/rules/jobs.md` under "Platform-Specific Quirks"

## Cognitive Reasoning Integration
Agents opt into `shared/cognitive/CognitiveEngine` for self-improving reasoning:
- `gmail_agent.py` — email classification (domain: `email_classification`, medium stakes)
- `screening_answers.py` — LLM fallback for screening questions (domain: `screening_answers`, medium stakes)
- Kill switch: `COGNITIVE_ENABLED=false` disables everywhere, falls back to direct LLM
- Both agents use lazy singleton init — zero overhead if cognitive engine isn't needed
- Engine calls `flush_sync()` is the caller's responsibility at end of batch/cron run

## Commands
```
python -m jobpulse.runner daemon         # Start Telegram daemon
python -m jobpulse.runner multi-bot      # Start all 5 bots
python -m jobpulse.runner briefing       # Morning digest
python -m jobpulse.runner chrome-pw      # Launch Chrome with CDP for Playwright
python -m pytest tests/ -v -k "jobpulse" # Run JobPulse tests only
```
