# Rules: Job Autopilot (jobpulse/job_**/*)

## Daily Rate Limits (updated 2026-03-31)
- LinkedIn: 20/day, scanning via guest API (httpx + BeautifulSoup, no browser), Playwright only for Easy Apply submission
- Greenhouse/Lever: 15/day, headed mode (not headless)
- Indeed/Workday/Generic: 15/day, conservative — aggressive detection
- Reed: 15/day, official API with 429 retry
- Total: 50/day across all platforms

## Application Engine Modes
- `APPLICATION_ENGINE=playwright` (default) — Playwright browser automation, headed mode
- `APPLICATION_ENGINE=extension` — Chrome MV3 extension via WebSocket bridge (ws://localhost:8765)
- Extension mode: start bridge first (`python -m jobpulse.runner ext-bridge`), load `extension/` in Chrome
- Extension adapter is a singleton — all platforms route through one ExtensionAdapter instance
- `_call_fill_and_submit()` in applicator.py handles sync/async bridging (extension adapter is async)
- Ralph Loop also uses `_call_fill_and_submit()` for retry iterations

## Anti-Detection
- Playwright adapters: headed mode + --disable-blink-features=AutomationControlled
- Extension mode: uses real Chrome profile — no automation flags, no stealth patches needed
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
- NEVER use em-dashes (—), en-dashes (–), or double dashes (--) in CV/CL text — use commas or periods
- ALL text justified (TA_JUSTIFY) — body, bullets, community section
- ALL section content aligned with section header — same left margin, no extra indent
- Skills as inline paragraphs, NOT two-column tables
- Role-adaptive tagline + summary: get_role_profile(role_title) matches JD to profile (data scientist, data analyst, ml engineer, ai engineer)
- YOE: Data Analyst = 3+ YOE, all other roles = 2+ YOE
- 5 base skill categories (Languages, AI/ML, DevOps, BI/Tools, Practices) + dynamic "Also proficient in:" from JD
- build_extra_skills() deduplicates via synonym matching (AWS=Amazon Web Services, ML=Machine Learning, etc.)
- Soft skills filtered from extra skills (customer focus, teamwork, etc. never appear in technical skills section)
- Skills table separator: 29mm between category label and values
- File naming: Yash_Bishnoi_{Company}.pdf (CV), Yash_Bishnoi_{Company}_CoverLetter.pdf (CL)
- PDF metadata title MUST be human-readable: "Yash Bishnoi {Company}" (CV), "Cover Letter {Company}" (CL) — NEVER random numbers, UUIDs, or hash strings. Recruiters see the title.
- When uploading via set_input_files(), always pass the descriptive filename — never a hash path. Use {name, mimeType, buffer} format with proper name.
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

## External Application Engine
- `ApplicationOrchestrator` manages: cookie dismiss → page detect → navigate → account → verify → fill
- Hybrid page detection: DOM first (free), vision LLM fallback when confidence < 0.6
- SSO priority: Google > LinkedIn > Microsoft > Apple. Prefers SSO over account creation.
- Account credentials in SQLite (`data/ats_accounts.db`), one password via `ATS_ACCOUNT_PASSWORD`
- Gmail verification: exponential backoff 1s→2s→4s→8s→16s→32s, requires `gmail.modify` scope
- Navigation learning in SQLite (`data/navigation_learning.db`), replays per domain
- Cookie dismisser runs before EVERY page detection — prevents misclassification
- Multi-page: `find_next_button()` priority: Submit > Review > Save & Continue > Continue > Next > Proceed
- Stuck detection: chars 200-700 comparison, abort after 2 identical pages
- Max 10 navigation steps, max 20 form pages
- Screening questions: pattern-based (work auth, salary, availability, experience years) → LLM fallback → SQLite cache
- All 6 ATS adapters wire screening via `answer_screening_questions()` in the form-fill loop

## Aggregator URL Handling
- Google Jobs returns aggregator URLs (bebee.com, learn4good.com, adzuna.co.uk, engineeringjobs.co.uk, uk.talent.com) — NOT direct company ATS pages
- Aggregators require their own registration/login before showing an application form
- `AGGREGATOR_DOMAINS` set in applicator.py — checked before auto-apply
- Cron path: scan_pipeline.py routes aggregator URLs to review queue instead of auto-apply
- Manual path: apply_job() logs a warning but still attempts (user may have logged in)
- Preferred flow: apply on the SOURCE platform (Reed, LinkedIn, company ATS), not through aggregators

## Platform-Specific Quirks (Dry Run Learnings)
- Reed Easy Apply: modal overlay with pre-filled CV from profile + "Submit application" button
  - System auto-detects CV mismatch and uploads tailored CV via "Update" → file chooser
  - Google SSO login required on first visit — handled by SSO handler
  - `NativeFormFiller._handle_modal_cv_upload()` handles the modal CV swap
- When a dry run succeeds and user approves, save platform-specific learnings:
  - Code: update NativeFormFiller, state machines, or form_gotchas.db
  - Docs: update this file + jobpulse/CLAUDE.md with the quirk
  - Cron: ensure scan_pipeline.py handles the same scenario

## SmartRecruiters Platform Quirks
- Web Components with Shadow DOM: `spl-*` custom elements (`spl-button`, `spl-autocomplete`, `spl-tag`)
- Standard `querySelectorAll('input')` returns NOTHING — must use Playwright `get_by_label()` / `get_by_role()` which pierce shadow DOM
- City autocomplete: type text → ArrowDown → Enter (shadow DOM element "outside viewport" won't click normally)
- Gender/Disability: `spl-autocomplete` dropdowns — use `get_by_role('combobox')`, fill text, select via `get_by_role('option')`
- Gender is multi-select with tag chips — `spl-tag` with close button in nested shadow DOM
- Resume* mandatory field is SEPARATE from the auto-parse upload at the top
- Experience + Education auto-parsed from CV upload — may need description edits
- Screening questions on page 2: radio pairs (Yes/No), autocomplete dropdowns, privacy checkbox
- Adapter: `jobpulse/ats_adapters/smartrecruiters.py` — uses Playwright CDP to existing Chrome
- Platform auto-detected in `applicator.py` when URL contains `smartrecruiters.com`

## Dry Run → Approve → Learn Workflow (MANDATORY for all applications)
- ALL applications use `apply_job(dry_run=True)` — fill form, stop before Submit
- User reviews filled form screenshot, makes corrections if needed, then approves
- After user submits: call `confirm_application()` from `applicator.py` — this is MANDATORY
- `confirm_application()` triggers the full post-apply learning pipeline:
  1. Records quota in rate limiter
  2. Runs `post_apply_hook()` which records form experience, uploads to Drive, updates Notion
- NativeFormFiller now tracks `field_types`, `screening_questions`, and `time_seconds` in ALL result dicts (including dry_run)
- The dry_run result dict is passed to `confirm_application()` — it contains all the form metadata needed for learning
- After user makes manual corrections before Submit: scan the form state to capture what changed (reinforcement signal)
- Internal keys (_stream, _gotchas, _job_context) MUST be filtered before json.dumps
- Document any new platform quirks in this file under "Platform-Specific Quirks"

## Post-Apply Hook (Automatic)
- `post_apply_hook()` in `jobpulse/post_apply_hook.py` runs after EVERY successful submission
- Two trigger paths:
  1. `apply_job(dry_run=False)` in `applicator.py` — auto-submit path (cron)
  2. `confirm_application()` in `applicator.py` — manual approval path (Claude Code sessions)
- BOTH paths fire the same hook — no application should ever skip learning
- Three concerns:
  1. Form experience: records domain, adapter, pages, field types, screening questions, time to `data/form_experience.db`
  2. Drive upload: uploads CV + CL PDFs to Google Drive, gets shareable links
  3. Notion update: sets status=Applied, applied date+time, follow-up date (+7 days), CV/CL Drive links
- `FormExperienceDB` in `jobpulse/form_experience_db.py` — per-domain form learning
  - Cron jobs query this to know form shape before applying (skip LLM page detection for known domains)
  - Success data never overwritten by failures (preserves what worked)
  - Tracks apply_count per domain for confidence scoring
- Hook is non-blocking: any failure is logged but doesn't affect the application result
- Hook runs BEFORE the anti-detection delay so Drive/Notion work happens during the wait

## PDF Upload Compatibility
- All CV/CL PDFs are sanitized via PyMuPDF after ReportLab generation (`_sanitize_pdf()`)
- Sanitization: garbage collection, deflate compression, clean xref table, proper metadata (title, author, creator)
- All `set_input_files()` calls use explicit `{name, mimeType: "application/pdf", buffer}` format — never bare path strings
- Prevents LinkedIn/ATS platforms from serving corrupted UUID-named files on download

## Manual Apply Session Workflow (Claude Code)
When user asks to apply to jobs manually via Claude Code + Playwright CDP:
1. **Write ONE comprehensive script** per form page — not 15 tiny scripts. One script should:
   - Scan all fields on the page (DOM-first analysis)
   - Fill ALL fields in top-to-bottom order
   - Handle dropdowns, radios, checkboxes, file uploads
   - Save/confirm each section
   - Take a final screenshot
2. **Stop ONLY before the Submit button** — show the filled form screenshot for review
3. **Never ask for approval mid-form** — the user has already approved the application by asking you to apply
4. **One script per page transition** — if the form has multiple pages (Next/Continue), write one script per page
5. **Include error recovery** — if a field fill fails, log it and continue to the next field, report failures at the end
6. **Send CV/CL to Telegram** at the start, before form filling begins
7. After user approves the final form screenshot → submit → **ALWAYS call `confirm_application()`** → learnings recorded → next job

## Dynamic Cover Letter
- Cover letter NOT generated upfront — lazy generation via cl_generator callback
- ATS form detection: Greenhouse/Lever adapters trigger generation when CL field found
- Dynamic points: build_dynamic_points() maps matched projects to JD skills with metrics
- LLM polish: polish_points_llm() refines points via GPT-5o-mini (~$0.002/call)
- Dynamic intro + hook generated from matched projects + required skills
- Falls back to static defaults if no matched data available
