# LinkedIn Live Apply — Design Spec
**Date:** 2026-04-01  
**Status:** Approved  
**Scope:** Single Gousto job, dry-run, learn-while-doing

---

## Overview

Apply for the Gousto job on LinkedIn (`https://www.linkedin.com/jobs/view/4395143521/`) using the existing `LinkedInAdapter`, running in dry-run mode (`AUTO_SUBMIT=false`). The session is interactive — verbose logs stream to terminal, screenshots land at every step, and any issue is fixed in-place with the fix saved to Ralph Loop's SQLite so cron jobs benefit automatically.

---

## Architecture

### Session Script
**File:** `scripts/live_apply_linkedin.py`

A standalone test harness (not wired into the pipeline) that:
- Bypasses the rate limiter (manual test run, not cron)
- Calls `LinkedInAdapter.fill_and_submit()` directly with `AUTO_SUBMIT=false` hardcoded
- Streams structlog output to terminal with `[STEP]`, `[PAGE]`, `[ERROR]` prefixes
- Saves all screenshots to `data/applications/gousto_test/` with step-numbered names
- Detects "Continue as Yash" / login wall before navigating to the job URL — clicks through if found
- On failure: prints error + screenshot path, pauses with `input(...)` for human intervention
- On success: prints all screenshots in order for review

### LinkedIn Adapter (existing, patched live)
**File:** `jobpulse/ats_adapters/linkedin.py`

Multi-step wizard flow:
```
Navigate → Login wall check → Click Easy Apply
  → Page 1: Contact (phone, email, location typeahead)
  → Page 2: Resume upload + optional CL upload
  → Page 3: Work experience (confirm/navigate)
  → Page 4+: Screening questions
  → Review page (stop here — AUTO_SUBMIT=false)
```

### Ralph Loop (learning persistence)
**File:** `jobpulse/ralph_loop/pattern_store.py`

Every fix discovered during this session is saved to `data/ralph_patterns.db` keyed by `(platform, step_name, error_signature)`. Future cron runs load these fixes proactively before the first attempt.

---

## Known Gaps and Fix Strategy

### Gap 1 — Location typeahead
**Problem:** Logged-in LinkedIn layout uses different ARIA selectors than guest layout. Current selectors may not match.  
**Fix strategy:** Screenshot the page, inspect aria-labels in DOM, update `_fill_location_typeahead()` with correct selectors, save `selector_override` to Ralph Loop.

### Gap 2 — Work experience page
**Problem:** `_fill_experience_page()` currently just waits 1-2s. Logged-in flow may show pre-filled work history needing "Confirm" clicks, employment type dropdowns, or date fields.  
**Fix strategy:** Screenshot the page, user describes what's visible, add correct interaction code to `_fill_experience_page()`.

### Gap 3 — Login wall ("Continue as Yash")
**Problem:** Chrome profile session may expire or LinkedIn may show a sign-in overlay.  
**Fix strategy:** Pre-navigation check in the script — looks for `button:has-text('Continue as')` or sign-in redirect, clicks through before proceeding to the job URL.

### Gap 4 — Guest vs logged-in layout divergence
**Problem:** LinkedIn serves different HTML depending on login state. The Easy Apply button selector, modal structure, and field IDs differ between layouts.  
**Fix strategy:** All fixes discovered in logged-in mode are tagged `platform=linkedin` in Ralph Loop so they don't pollute the guest-mode scanner.

---

## Data Flow

```
scripts/live_apply_linkedin.py
  → LinkedInAdapter.fill_and_submit(url, cv_path, profile, AUTO_SUBMIT=false)
      → managed_persistent_browser (real Chrome, saved cookies)
      → navigate to job URL → login wall check
      → click Easy Apply → wizard pages (1-4+)
      → screenshot every page → stream logs
      → reach Review page → stop
  → on any failure:
      → print error + screenshot path
      → human reviews → tells Claude what to fix
      → Claude patches linkedin.py + saves fix to ralph_patterns.db
      → re-run
```

---

## Success Criteria

- Browser opens, navigates to Gousto job in logged-in layout
- Easy Apply modal opens cleanly
- All pages filled: contact, resume, experience, questions
- Reaches Review page with all fields populated
- Screenshots show a clean, complete application
- `AUTO_SUBMIT=false` — no submission happens

---

## What Gets Committed After Session

1. Fixes to `jobpulse/ats_adapters/linkedin.py` (all discovered issues)
2. Ralph Loop patterns in `data/ralph_patterns.db` (selector fixes, wait adjustments)
3. `scripts/live_apply_linkedin.py` (kept for future platform testing — Indeed, Reed, etc.)
4. Memory update recording what was learned

---

## Out of Scope

- Actual submission (next session after dry-run validates)
- Indeed / Reed / Greenhouse / Workday / Google Jobs testing (separate sessions)
- Google Jobs scraper (not yet implemented)
- LinkedIn scanner changes (guest-mode scanner is separate from the adapter)
