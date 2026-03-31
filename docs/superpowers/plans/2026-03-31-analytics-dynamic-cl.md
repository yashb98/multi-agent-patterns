# Application Analytics + Dynamic Cover Letter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (A) Application conversion funnel + enhanced `job stats` command. (B) Dynamic cover letter generated only when ATS form needs it, with matched projects + LLM polish. Recruiter email extraction stored in Notion.

**Architecture:** `job_analytics.py` for funnel queries. Dynamic CL uses existing `points` param in `generate_cover_letter_pdf()` with a new `build_dynamic_points()` function. CL detection via `find_file_inputs()` in adapters. Email extraction in `jd_analyzer.py`.

**Tech Stack:** SQLite, Notion API, GPT-5o-mini (CL polish only), ReportLab

---

## File Structure

| File | Responsibility |
|------|----------------|
| **Create:** `jobpulse/job_analytics.py` | Funnel queries, platform breakdown, gate stats |
| **Create:** `tests/test_job_analytics.py` | Analytics query tests |
| **Create:** `tests/test_dynamic_cover_letter.py` | CL detection, dynamic points, email extraction tests |
| **Modify:** `jobpulse/dispatcher.py` | Enhanced `_handle_job_stats` |
| **Modify:** `jobpulse/weekly_report.py` | Add funnel + fix DB bug |
| **Modify:** `jobpulse/cv_templates/generate_cover_letter.py` | Add `matched_projects` + `required_skills` params, `build_dynamic_points()` |
| **Modify:** `jobpulse/applicator.py` | Add `cl_generator` callback |
| **Modify:** `jobpulse/job_autopilot.py` | Remove upfront CL gen, pass cl_generator |
| **Modify:** `jobpulse/jd_analyzer.py` | Email extraction + classification |
| **Modify:** `jobpulse/job_notion_sync.py` | Add "Recruiter Email" field |

---

### Task 1: Job Analytics Module

**Files:**
- Create: `jobpulse/job_analytics.py`
- Create: `tests/test_job_analytics.py`

Tests and implementation for:
- `get_conversion_funnel(days=7) -> dict` — counts per status (Found, Applied, Interview, Offer, Rejected, Skipped, Blocked) + conversion rates
- `get_platform_breakdown(days=7) -> dict` — per-platform counts (found, applied, interviews)
- `get_gate_stats(days=7) -> dict` — Gate 4 block counts by reason (spam, JD quality, blocklist)
- `get_enhanced_job_stats() -> str` — formatted Telegram message with today + week funnel + top platforms

All queries use JobDB SQLite. Tests use tmp_path fixture with seeded test data.

Commit: `feat(jobs): add job analytics module — funnel, platform breakdown, gate stats`

---

### Task 2: Enhanced Weekly Report + Job Stats Command

**Files:**
- Modify: `jobpulse/weekly_report.py`
- Modify: `jobpulse/dispatcher.py`

Changes:
- Fix `job_db._conn()` → `sqlite3.connect(db_path)` in weekly_report.py
- Add funnel section to weekly report using `get_conversion_funnel()`
- Add platform breakdown section
- Update `_handle_job_stats` in dispatcher.py to call `get_enhanced_job_stats()`

Commit: `feat(jobs): enhanced weekly report + job stats with funnel`

---

### Task 3: Recruiter Email Extraction + Notion Field

**Files:**
- Modify: `jobpulse/jd_analyzer.py`
- Modify: `jobpulse/job_notion_sync.py`
- Test: `tests/test_dynamic_cover_letter.py`

Add `extract_recruiter_email(jd_text) -> str | None`:
- Regex extract all emails from JD
- Classify: noreply/generic → discard, recruiter/generic_hr → return
- Store in `JobListing.recruiter_email` field

Add "Recruiter Email" to Notion sync:
- `build_create_payload()` — add email property
- `build_update_payload()` — add `recruiter_email` param

Tests: email extraction (recruiter, noreply, generic, no email), classification logic.

Commit: `feat(jobs): extract recruiter emails from JDs, store in Notion`

---

### Task 4: Dynamic Cover Letter Points

**Files:**
- Modify: `jobpulse/cv_templates/generate_cover_letter.py`
- Test: `tests/test_dynamic_cover_letter.py`

Add `build_dynamic_points(matched_projects, required_skills) -> list[tuple[str, str]]`:
- Takes matched_projects (list of dicts with title, url, bullets) + required_skills
- Builds 4 points mapping projects to JD requirements
- Each point: header = project title + relevant skills, detail = metrics from bullets

Add `polish_points_llm(points, role, company) -> list[tuple[str, str]]`:
- GPT-5o-mini refines deterministic points for tone/relevance (~$0.002)
- Falls back to unpolished points on failure

Update `generate_cover_letter_pdf()`:
- Add `matched_projects` and `required_skills` params
- If provided → `build_dynamic_points()` → optional `polish_points_llm()` → use as `points`
- Also generate dynamic intro + hook referencing matched skills

Tests: deterministic point building, LLM polish (mocked), fallback to defaults.

Commit: `feat(jobs): dynamic cover letter points from matched projects + LLM polish`

---

### Task 5: CL Detection in Form Filling + Lazy Generation

**Files:**
- Modify: `jobpulse/applicator.py`
- Modify: `jobpulse/job_autopilot.py`

Changes to `applicator.py`:
- Add `cl_generator: Callable[[], Path | None] | None = None` param to `apply_job()`
- Before `adapter.fill_and_submit()`, detect CL field need via adapter
- If CL field detected AND no `cover_letter_path` → call `cl_generator()` to produce it
- Pass the generated path to adapter

Changes to `job_autopilot.py`:
- Remove upfront `generate_cover_letter_pdf()` call
- Build `cl_generator` lambda capturing listing + matched data
- Pass to `apply_job()` — CL only generated if form needs it

Commit: `feat(jobs): lazy cover letter generation — only when ATS form has CL field`

---

### Task 6: Update Documentation

**Files:**
- Modify: `CLAUDE.md`, `.claude/rules/jobs.md`, `jobpulse/CLAUDE.md`

Add analytics and dynamic CL sections. Update pipeline description.

Commit: `docs: add application analytics + dynamic cover letter to documentation`

---

## Self-Review

**Spec coverage:** A1 funnel → Task 1 ✅ | A2 job stats → Task 2 ✅ | B1 CL detection → Task 5 ✅ | B2 dynamic points → Task 4 ✅ | B3 email extraction → Task 3 ✅ | B4 skip CL → Task 5 ✅ | Weekly report fix → Task 2 ✅

**Placeholder scan:** No TBDs.

**Type consistency:** `build_dynamic_points()` returns `list[tuple[str, str]]` — matches existing `points` param type. `cl_generator` is `Callable[[], Path | None]` used consistently.
