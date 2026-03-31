# Rules: Job Autopilot (jobpulse/job_**/*)

## Daily Rate Limits (updated 2026-03-31)
- LinkedIn: 15/day, scanning via guest API (httpx + BeautifulSoup, no browser), Playwright only for Easy Apply submission
- Greenhouse/Lever: 7/day, headed mode (not headless)
- Indeed/Workday/Generic: 8/day, conservative — aggressive detection
- Reed: 7/day, official API with 429 retry
- Total: 30/day across all platforms

## Anti-Detection
- All adapters: headed mode + --disable-blink-features=AutomationControlled
- LinkedIn: human-like typing (50-150ms/char), persistent browser profile
- Thread mutex on apply_job() — no concurrent applications
- Pipeline lock on run_scan_window() — no cron vs Telegram races
- Application recorded BEFORE submission (prevents silent limit bypass on error)
- UTC timezone for daily cap tracking (prevents midnight drift)

## Pre-Generation Checklist (MANDATORY before every CV/Cover Letter)
1. Run `sync_verified_to_profile()` — pull latest "I Know" skills from Notion Skill Tracker
2. Re-run pre-screen with updated profile to get accurate match score
3. THEN generate CV + Cover Letter
Profile data (GitHub repos + READMEs) is already synced by the 3am nightly cron — no need to re-fetch from GitHub.
Only Notion Skill Tracker needs live sync (user may have approved new skills since last run).

## CV & Cover Letter PDF Generation
- Use ReportLab generators in cv_templates/ — NOT xelatex (cv_tailor.py is legacy)
- CV: generate_cv_pdf(company, location) → always generated upfront, instant PDF, no LLM
- Cover letter: LAZY GENERATION — only when ATS form has CL upload field (Greenhouse/Lever detect it)
  - generate_cover_letter_pdf(company, role, matched_projects, required_skills) → dynamic points + LLM polish
  - cl_generator callback passed to apply_job(), triggered mid-form-fill if CL field detected
  - If no CL field → no generation, no wasted resources
- Fonts: Arial 9.5pt (system), Raleway/Spectral/Lato (data/fonts/) for cover letter
- MANDATORY 2-page limit for CV — never exceed
- Every project bullet MUST have a quantified metric (%, count, time saved)
- No conversational bullets — always professional tone
- Tagline: no niche tools (Claude Code removed), use universally understood terms
- All project URLs must link to CORRECT GitHub repo — verify before generating
- No "JD Match" row in skills section
- Section headers: teal (#1a5276) with thin line

## Pre-Screen Pipeline
- Gate 0 (recruiter_screen.py) runs BEFORE any LLM call — title + exclude keyword filter
- Gates 1-3 (skill_graph_store.py) run AFTER skill extraction — kill signals, must-haves, competitiveness
- Skill extraction uses hybrid approach: rule-based first (582-entry taxonomy), LLM fallback when < 10 skills
- Nightly profile sync (3am): github_profile_sync.py → ALL GitHub repos (no limit) + README + CLAUDE.md + docs/*.md + resume + past apps → MindGraph
- Profile sync scans all .md files per repo, not just README — catches architecture docs, rules, skill references
- Cross-platform dedup: same company+title on Reed AND LinkedIn = one job. Normalizes titles (strips suffixes)
- K1 seniority kill: ≥3 years (not ≥5). Graduate/junior roles typically require 0-2 years
- SkillGraphStore is Neo4j-ready — only swap internals when going multi-user
- Pre-screen thresholds are BRUTAL (7-day experiment 2026-03-31→04-06):
  M1: ≥3 of top-5 required skills | M2: ≥2 projects with 2+ overlap | M3: ≥92% of required skills (percentage-based)
- Experiment tracker: @docs/experiments/2026-03-30-brutal-prescreen-7day.md

## Notion Job Tracker (MANDATORY)
- Every application MUST have ALL fields filled in the Notion Job Tracker database
- Resume PDF: upload to Google Drive → paste link in Notion "Resume" field
- Cover Letter PDF: upload to Google Drive → paste link in Notion "Cover Letter" field
- Required fields: Company, Role, Platform, Status, Location, ATS Score, Match Tier, Matched Projects, Applied Date, Follow Up Date, Resume link, Cover Letter link
- Reference: OakNorth application is the standard for how Notion entries should look
- NEVER leave blank fields — if data unavailable, write "N/A" not empty

## Notion Skill Tracker
- Unverified skills from JDs → Notion "Pending" → user marks "I Know" / "Don't Know"
- Run `skill-verify` to sync verified skills to MindGraph profile
- Telegram scan summary includes Notion Skill Tracker link + pending count
- Running for 10-15 days to build complete verified skill profile

## Safety
- JOB_AUTOPILOT_AUTO_SUBMIT=false by default — requires explicit approval
- JOB_AUTOPILOT_MAX_DAILY=10 default (conservative, below platform limits)

## Gate 4: Application Quality Check
- Phase A (pre-generation, deterministic, free):
  - A1: JD quality — block <200 chars, <5 skills, boilerplate (3+ generic phrases with <8 skills)
  - A2: Company Blocklist — Notion "🚫 Company Blocklist" DB with Pending/Blocked/Approved status. Auto-detect spam keywords (training, bootcamp, recruitment agency) + 10+ listings/7d. Refresh cache before every scan
  - A3: Company background — generic name detection, past application flag (soft, non-blocking)
- Phase B (post-generation):
  - B1: Deterministic CV scrutiny — metrics in bullets, no conversational text, no informal words, 2-page limit
  - B2: LLM FAANG recruiter review — GPT-5o-mini scores CV 0-10 (relevance 3 + evidence 3 + presentation 2 + standout 2). Score ≥7 → proceed. Score <7 → Notion "Needs Review" with weaknesses in Notes
- Files: gate4_quality.py, company_blocklist.py
- Cost: Phase A free, Phase B ~$0.002/call (only for jobs passing Gates 0-3 + Phase A)

## Verification Wall Learning
- Universal detector: Cloudflare Turnstile, reCAPTCHA, hCaptcha, text challenges, HTTP 403/429, empty anomaly
- 17 signals tracked per scan session: time of day, requests, delay, session age, UA, cookies, VPN, mouse, referrer, query, pages, fingerprint, page load
- Statistical correlation engine: zero LLM cost, computes block rate per signal bucket, identifies risk factors (>50% block rate, ≥3 samples)
- LLM pattern analyzer: GPT-5o-mini every 5th block event, ~$0.002/call, stores human-readable rules
- Cooldown: 2hr → 4hr → 48hr (exponential backoff). Reset on successful scan. Telegram alert on 3rd consecutive block
- Adaptive params: risk level (low/medium/high) adjusts delays, max requests, human simulation, session length
- Human interaction: wait for networkidle, scroll 300-600px, random mouse movement, 1-3s reading delay
- Database: data/scan_learning.db (scan_events, learned_rules, cooldowns)

## Application Analytics
- `job stats` Telegram command → conversion funnel + platform breakdown + gate stats
- Weekly report includes funnel (Found→Applied→Interview with conversion rates)
- Per-platform breakdown (LinkedIn, Indeed, Reed)
- Gate 4 block stats (spam, JD quality, blocklist)
- Module: jobpulse/job_analytics.py

## Recruiter Email Extraction
- Extracted from JD text during jd_analyzer.py analysis
- 3-tier classification: discard (noreply/info/support), store generic_hr (careers/hiring), store recruiter (personal)
- Stored in Notion Job Tracker "Recruiter Email" column (email type)
- Field on JobListing model: recruiter_email

## Dynamic Cover Letter
- Cover letter NOT generated upfront — lazy generation via cl_generator callback
- ATS form detection: Greenhouse/Lever adapters trigger generation when CL field found
- Dynamic points: build_dynamic_points() maps matched projects to JD skills with metrics
- LLM polish: polish_points_llm() refines points via GPT-5o-mini (~$0.002/call)
- Dynamic intro + hook generated from matched projects + required skills
- Falls back to static defaults if no matched data available
