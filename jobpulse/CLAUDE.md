# JobPulse — Daily Automation Agents

10+ autonomous agents running 24/7 via macOS daemon + cron + GitHub Actions.

## Agents
- gmail_agent.py — Email classification (pre-classifier + LLM). Pre-classifier eliminates 70-85% of LLM calls.
- calendar_agent.py — Today + tomorrow events, reminders
- github_agent.py — Yesterday's commits (Commits API, NOT Events API), trending repos
- arxiv_agent.py — Daily top 5 papers, multi-criteria ranking, 3-level fact verification
- notion_agent.py — Tasks CRUD, dedup, priorities, due dates, subtasks, weekly plan
- budget_agent.py — Spending/income/savings, 17 categories, recurring, alerts, undo, Notion sync
- budget_tracker.py — Weekly archival, category sub-pages, weekly comparison
- salary_agent.py — Hours at £13.99/hr, tax calc, Notion timesheet
- briefing_agent.py — Collects all agents → RLM synthesis → Telegram
- job_autopilot.py — Scan → analyze JD → tailor CV → ATS score → apply/queue
- cv_templates/generate_cv.py — ReportLab PDF CV generator (Arial, A4, no xelatex)
- cv_templates/generate_cover_letter.py — ReportLab PDF cover letter (Raleway/Spectral, two-column sidebar)
- cv_tailor.py — Legacy xelatex path (kept for determine_match_tier only)
- cover_letter_agent.py — Legacy LLM text cover letter (kept as fallback)
- ats_scorer.py — Deterministic ATS scoring (keyword + section + format, 0-100)
- skill_extractor.py — Rule-based JD skill extraction (582-entry taxonomy), LLM fallback when < 10 skills
- recruiter_screen.py — Gate 0 title filter (pre-LLM, instant, fuzzy matching + exclude keywords)
- skill_graph_store.py — SkillGraphStore: 4-gate pre-screen (Gates 1-3), MindGraph abstraction, Neo4j-ready
- github_profile_sync.py — Nightly 3am sync: GitHub repos + README skill extraction + resume BASE_SKILLS + past apps → MindGraph graph (347 skills, 750 DEMONSTRATES relations)
- skill_gap_tracker.py — Records missing skills from every pre-screened job, exports ranked CSV for upskilling
- skill_tracker_notion.py — Notion Skill Tracker: pending skills for user verification (I Know / Don't Know / Learning)
- verification_detector.py — Universal CAPTCHA/verification wall detection (Cloudflare, reCAPTCHA, hCaptcha, text, HTTP)
- scan_learning.py — Scan learning engine: event recording (17 signals), statistical correlation, LLM analysis, adaptive params, cooldown
- drive_uploader.py — Google Drive auto-upload for CV/CL PDFs, dedup, shareable links for Notion
- gate4_quality.py — Gate 4: JD quality (A1), company background (A3), CV scrutiny (B1), LLM FAANG review (B2)
- company_blocklist.py — Notion Company Blocklist: spam detection, user curation (Pending/Blocked/Approved), cached lookup

## Dispatch
Enhanced Swarm when JOBPULSE_SWARM=true (default). Flat dispatcher when false.
IMPORTANT: New intents MUST be added to BOTH dispatcher.py AND swarm_dispatcher.py.

## PDF Generation
CV and cover letter PDFs use **ReportLab** (pure Python, no system dependencies).
- CV: `cv_templates/generate_cv.py` → `generate_cv_pdf(company, location, extra_skills)`
- Cover letter: `cv_templates/generate_cover_letter.py` → `generate_cover_letter_pdf(company, role, location)`
- Fonts: Arial (system), Raleway/Spectral/Lato (data/fonts/)
- Output: `data/applications/{job_id}/` or `data/applications/{company}/`
- No xelatex, no LLM calls — instant PDF generation
- ATS scoring runs on BASE_SKILLS + JD-matched skills text

## Pre-Screen Pipeline (4-Gate Recruiter Model)
Gate 0: Title relevance (instant, before LLM) → Gate 1: Kill signals (seniority ≥5yr, primary lang missing, foreign domain)
→ Gate 2: Must-haves (≥3 of top-5 skills, ≥2 projects with 3+ overlap, ≥12 absolute matches, ≥65% required)
→ Gate 3: Competitiveness score (0-100: hard skill 35 + project evidence 25 + coherence 15 + domain 15 + recency 10)
Tiers: reject (<Gate 1) | skip (<55) | apply (55-74) | strong (75+)
LLM calls: ~10-11/day (96% reduction from 250/day). Cost: $0.23/month.
Nightly sync: `python -m jobpulse.runner profile-sync` (3am cron) — GitHub repos + resume + past apps → MindGraph.
Skill Tracker: `python -m jobpulse.runner skill-verify` — syncs Notion-verified skills to profile.
`python -m jobpulse.runner skill-pending` — shows pending skills.
Every scan sends Notion Skill Tracker link to Telegram with pending count.

## Pre-Generation Checklist (before every CV/Cover Letter)
1. Sync Notion Skill Tracker → `skill-verify` pulls latest verified skills from Notion
2. Re-run pre-screen with updated profile for accurate match score
3. THEN generate CV + Cover Letter
GitHub/README profile data is already synced by 3am nightly cron — only Notion needs live sync.

## Critical Rules
- All money as float with 2 decimal places
- One handler per Telegram message — never let two bots handle the same message
- 30s dedup guard on all write paths (budget, tasks)
- Never use GitHub Events API for commits — use Commits API per-repo
- Never wait for Telegram replies in Claude Code sessions
- All external API calls use HTTPS, never HTTP
- Tests MUST use tmp_path for DB paths — never touch data/*.db

## Commands
```
python -m jobpulse.runner daemon         # Start Telegram daemon
python -m jobpulse.runner multi-bot      # Start all 5 bots
python -m jobpulse.runner briefing       # Morning digest
python -m pytest tests/ -v -k "jobpulse" # Run JobPulse tests only
```
