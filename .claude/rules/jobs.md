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
- Nightly profile sync (3am): github_profile_sync.py → MindGraph SKILL/PROJECT entities
- SkillGraphStore is Neo4j-ready — only swap internals when going multi-user
- Pre-screen thresholds are HARD: ≥12 absolute matches, ≥65% required, ≥3 of top-5, ≥2 projects with 3+ overlap

## Safety
- JOB_AUTOPILOT_AUTO_SUBMIT=false by default — requires explicit approval
- JOB_AUTOPILOT_MAX_DAILY=10 default (conservative, below platform limits)
