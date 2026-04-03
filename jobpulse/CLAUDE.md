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
- job_analytics.py — Conversion funnel, platform breakdown, gate stats

## Dispatch
Enhanced Swarm when JOBPULSE_SWARM=true (default). Flat dispatcher when false.
IMPORTANT: New intents MUST be added to BOTH dispatcher.py AND swarm_dispatcher.py.

## Rules
All jobpulse rules in `.claude/rules/jobpulse.md`. Job autopilot rules in `.claude/rules/jobs.md`.

## Commands
```
python -m jobpulse.runner daemon         # Start Telegram daemon
python -m jobpulse.runner multi-bot      # Start all 5 bots
python -m jobpulse.runner briefing       # Morning digest
python -m pytest tests/ -v -k "jobpulse" # Run JobPulse tests only
```
