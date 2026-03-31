# Rules: Job Autopilot (jobpulse/job_**/*)

## Daily Rate Limits (March 2026, research-backed)
- LinkedIn: 10/day, 30min session break every 5 apps, persistent browser profile
- Greenhouse/Lever: 7/day, headed mode (not headless)
- Indeed/Workday/Generic: 5/day, conservative — aggressive detection
- Reed: 4/day, official API with 429 retry
- Total: 25/day across all platforms

## Anti-Detection
- All adapters: headed mode + --disable-blink-features=AutomationControlled
- LinkedIn: human-like typing (50-150ms/char), persistent browser profile
- Thread mutex on apply_job() — no concurrent applications
- Pipeline lock on run_scan_window() — no cron vs Telegram races
- Application recorded BEFORE submission (prevents silent limit bypass on error)
- UTC timezone for daily cap tracking (prevents midnight drift)

## Pre-Generation Checklist (MANDATORY before every CV/Cover Letter)
1. Run `sync_verified_to_profile()` — pull latest "I Know" skills from Notion Skill Tracker
2. Run `sync_profile()` or check profile stats — ensure README skills are synced
3. Re-run pre-screen with updated profile to get accurate match score
4. THEN generate CV + Cover Letter with the latest skill data
NEVER generate CV/CL with stale profile data. Always sync first.

## CV & Cover Letter PDF Generation
- Use ReportLab generators in cv_templates/ — NOT xelatex (cv_tailor.py is legacy)
- CV: generate_cv_pdf(company, location, extra_skills) → instant PDF, no LLM
- Cover letter: generate_cover_letter_pdf(company, role, location) → instant PDF, no LLM
- Fonts: Arial (system), Raleway/Spectral/Lato (data/fonts/)
- ATS scoring runs on BASE_SKILLS text + JD-matched skills, not PDF extraction

## Pre-Screen Pipeline
- Gate 0 (recruiter_screen.py) runs BEFORE any LLM call — title + exclude keyword filter
- Gates 1-3 (skill_graph_store.py) run AFTER skill extraction — kill signals, must-haves, competitiveness
- Skill extraction uses hybrid approach: rule-based first (582-entry taxonomy), LLM fallback when < 10 skills
- Nightly profile sync (3am): github_profile_sync.py → GitHub API + README skill extraction + resume + past apps → MindGraph (347 skills, 750 DEMONSTRATES relations)
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
