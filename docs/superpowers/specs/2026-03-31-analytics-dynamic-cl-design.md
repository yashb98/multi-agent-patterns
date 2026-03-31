# Application Analytics + Dynamic Cover Letter — Design Spec

**Goal:** (A) Weekly conversion funnel + on-demand `job stats` command. (B) Dynamic cover letter generated only when ATS form has a CL upload field, with matched projects + LLM polish. Recruiter email extraction from JDs stored in Notion.

---

## Feature A: Application Analytics

### A1: Enhanced Weekly Report

Add to existing `weekly_report.py`:

**Conversion Funnel:**
```
Found (120) → Applied (30) → Interview (5) → Offer (1) / Rejected (8)
Conversion: Found→Applied 25% | Applied→Interview 17% | Interview→Offer 20%
```

**Per-Platform Breakdown:**
```
LinkedIn: 45 found, 12 applied, 2 interviews
Indeed: 35 found, 8 applied, 1 interview
Reed: 40 found, 10 applied, 2 interviews
```

**Gate Stats:**
```
Gate 0: 200 scanned → 150 passed (75%)
Gates 1-3: 150 → 80 passed (53%)
Gate 4A: 80 → 65 passed (81%) [15 blocked: 8 spam, 4 JD quality, 3 blocklist]
Gate 4B: 65 CVs → 50 clean, 15 needs review
```

**Data source:** All from `job_db.py` SQLite queries — no external API calls.

**Fix:** Replace broken `job_db._conn()` call with proper `JobDB()` usage.

### A2: On-Demand `job stats` Command

New Telegram command: `job stats` or `application stats`

Returns real-time snapshot:
- Today's stats (found, applied, blocked, remaining quota)
- This week's funnel (Found→Applied→Interview)
- Top 3 platforms by application count
- Gate 4 block reasons breakdown
- Average ATS score for applied jobs

**Integration:** Add to both `dispatcher.py` and `swarm_dispatcher.py` (dual dispatcher rule).

### Files

| File | Change |
|------|--------|
| **Create:** `jobpulse/job_analytics.py` | Funnel queries, platform breakdown, gate stats |
| **Modify:** `jobpulse/weekly_report.py` | Add funnel + platform + gate sections, fix DB bug |
| **Modify:** `jobpulse/dispatcher.py` | Add JOB_STATS handler |
| **Modify:** `jobpulse/swarm_dispatcher.py` | Add JOB_STATS handler |

---

## Feature B: Dynamic Cover Letter

### B1: Cover Letter Detection (During Form Filling)

**Change the flow from:**
```
Generate CV + CL upfront → Apply (attach both)
```

**To:**
```
Generate CV only → Start form fill → detect CL field? → Generate CL on demand → Attach
```

**Detection:** Use existing `find_file_inputs(page)` from `form_engine/file_filler.py`. If `"cover_letter"` key exists in the returned dict, CL is needed.

**Implementation in adapters:** Add a `needs_cover_letter(page) -> bool` method to `BaseATSAdapter`. Each adapter calls `find_file_inputs()` or checks its own hardcoded selectors.

**Callback pattern:** `apply_job()` receives a `cl_generator` callback instead of `cover_letter_path`. If CL field detected mid-form, call the generator to produce the CL, upload to Drive, then attach.

```python
def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cl_generator: Callable[[], Path | None] | None = None,  # NEW
    cover_letter_path: Path | None = None,  # kept for backward compat
    custom_answers: dict | None = None,
) -> dict:
```

### B2: Dynamic Points Generation

Current cover letter has 4 hardcoded points. New flow:

1. Take `matched_projects` (from `project_portfolio.py`) — each has title, url, bullets with metrics
2. Take `required_skills` from JD
3. Map top 3-4 matched projects to numbered points, highlighting how each project demonstrates JD requirements
4. LLM polish: GPT-5o-mini refines the 4 points for tone and relevance to specific role (~$0.002/call)

**Function signature change:**
```python
def generate_cover_letter_pdf(
    company: str,
    role: str,
    location: str = "London, UK",
    matched_projects: list[dict] | None = None,  # NEW — from project_portfolio
    required_skills: list[str] | None = None,     # NEW — from JD
    ...
) -> Path
```

**Point generation logic:**
1. If `matched_projects` provided → build points from project data (deterministic)
2. If `required_skills` provided → LLM polish pass maps skills to project evidence
3. If neither → fall back to current static defaults

### B3: Recruiter Email Extraction

**During JD analysis** (`jd_analyzer.py`), extract emails from JD text:

```python
emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', jd_text)
```

**Classification (deterministic, no LLM):**

| Pattern | Classification | Action |
|---------|---------------|--------|
| `noreply@`, `no-reply@`, `donotreply@` | noreply | Discard |
| `jobs@`, `careers@`, `hr@`, `recruitment@`, `hiring@` | generic_hr | Store |
| `info@`, `admin@`, `support@`, `hello@` | generic_company | Discard |
| Everything else (e.g., `john.smith@company.com`) | recruiter | Store |

**Storage:** New "Recruiter Email" column in Notion Job Tracker (type: `email`).

**Note:** Need to add this column to the Notion DB. The `job_notion_sync.py` `build_create_payload()` and `build_update_payload()` need the new field.

### B4: Skip CL When Not Needed

In `job_autopilot.py`, remove the upfront CL generation. Instead:

1. Pass a `cl_generator` lambda to `apply_job()` that generates CL on demand
2. The generator captures `listing`, `matched_projects`, `required_skills` in closure
3. If the adapter detects no CL field → generator never called → zero wasted resources

### Files

| File | Change |
|------|--------|
| **Modify:** `jobpulse/cv_templates/generate_cover_letter.py` | Accept `matched_projects` + `required_skills`, build dynamic points, LLM polish |
| **Modify:** `jobpulse/applicator.py` | Add `cl_generator` callback, detect CL field mid-form |
| **Modify:** `jobpulse/ats_adapters/base.py` | Add `needs_cover_letter(page)` method |
| **Modify:** `jobpulse/job_autopilot.py` | Remove upfront CL gen, pass cl_generator lambda |
| **Modify:** `jobpulse/jd_analyzer.py` | Extract + classify recruiter emails |
| **Modify:** `jobpulse/job_notion_sync.py` | Add "Recruiter Email" field |
| **Create:** `tests/test_job_analytics.py` | Analytics query tests |
| **Create:** `tests/test_dynamic_cover_letter.py` | CL detection, dynamic points, email extraction tests |

---

## Testing Strategy

**Analytics:**
- Funnel calculation with mock DB data
- Platform breakdown correctness
- Gate stats aggregation
- Weekly report format

**Cover Letter:**
- CL field detection (mock pages with/without CL input)
- Dynamic point generation from matched projects
- LLM polish (mocked)
- Email extraction + classification (noreply, generic, recruiter)
- Skip CL when no field detected
- All tests use tmp_path / mocks — no real API calls
