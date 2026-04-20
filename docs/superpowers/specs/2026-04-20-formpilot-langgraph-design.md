# FormPilot — LangGraph Plan-and-Execute for Autonomous Form Filling

**Date:** 2026-04-20
**Status:** Design
**Goal:** Replace the procedural `NativeFormFiller` + `ApplicationOrchestrator` with a LangGraph Plan-and-Execute graph that fills forms autonomously, learns from every application, and pauses for human approval only before final submission.

---

## Architecture Overview

FormPilot is a single LangGraph `StateGraph` with 7 nodes. It replaces the inner form-filling loop (everything between `apply_job()` selecting an adapter and `post_apply_hook()`). The outer pipeline (scan → screen → gate4 → generate CV) stays untouched.

**Playwright-only.** No extension bridge. Headed mode with anti-detection flags.

```
apply_job()
  │
  ▼
FormPilot LangGraph
  │
  ├─ auth_gate ─► form_planner ─► field_executor ─► page_verifier
  │                    ▲                                   │
  │                    │              ┌────────────────┐    │
  │                    │              │                │    │
  │                    └── next page ─┤   observer     │◄───┤
  │                                   │                │    │
  │                                   └────────────────┘    │
  │                                                         │
  │                        ┌────────────────┐               │
  │                        │  rescue_node   │◄── failed ────┘
  │                        └───────┬────────┘
  │                                │
  │                    ┌───────────▼──────────┐
  │                    │   approval_gate      │◄── last page, all ok
  │                    │   (Telegram)         │
  │                    └───────┬──────────────┘
  │                       ┌────┴────┐
  │                    submit    corrections → re-fill
  │                       │
  ▼                       ▼
post_apply_hook()     END
```

---

## State Definition

```python
class FormPilotState(TypedDict):
    # Immutable inputs (set once by apply_job)
    url: str
    domain: str
    platform: str                     # greenhouse, lever, workday, etc.
    cv_path: str
    cl_path: str | None
    job_context: dict                 # title, company, location, jd_text
    merged_answers: dict              # pre-resolved screening answers

    # Auth state
    auth_status: str                  # "pending" | "logged_in" | "created" | "failed"
    auth_method: str                  # "sso_google" | "credentials" | "signup" | ""

    # Per-page planning
    current_page: int
    total_pages: int                  # estimated, updated as pages are discovered
    page_plan: list[FieldPlan]        # ordered fill plan for current page
    page_screenshot_b64: str          # screenshot after filling

    # Execution tracking
    fill_results: list[FieldResult]   # results for current page
    failed_fields: list[FieldPlan]    # fields that need rescue
    rescue_attempts: int              # per-page, max 2

    # Cross-page accumulation
    all_pages_filled: list[PageRecord]  # observer output per page
    form_complete: bool

    # Approval
    approval_status: str              # "pending" | "approved" | "corrected" | "aborted"
    corrections: dict                 # field_label → corrected_value (from user)

    # Output
    success: bool
    result: dict                      # final result dict for post_apply_hook
```

**Supporting types:**

```python
class FieldPlan(TypedDict):
    field_label: str
    field_type: str                   # text, select, file, radio, checkbox, combobox
    page_num: int
    selector: str | None              # from Field Registry or DOM scan
    expected_value: str | None        # from Field Registry typical_value
    combobox_mapping: str | None      # from Combobox Mappings
    resolution_strategy: str          # "registry" | "pattern" | "cache" | "llm" | "vision"

class FieldResult(TypedDict):
    field_label: str
    value_attempted: str
    value_set: str
    method: str                       # tier name from FormIntelligence
    tier: int
    confidence: float
    success: bool
    error: str | None
    selector: str

class PageRecord(TypedDict):
    page_num: int
    page_title: str
    fields: list[FieldResult]
    screenshot_b64: str
    has_file_upload: bool
    nav_button: str                   # "Next" | "Continue" | "Submit"
```

---

## Node 1: auth_gate

**Purpose:** Ensure the user is logged into the ATS platform before form filling begins.

**Flow:**
1. Check current page — if already on the form, skip auth (status = `logged_in`)
2. Try SSO (Google > LinkedIn > Microsoft > Apple) via `SSOHandler`
3. Try stored credentials via `AccountManager.get_credentials(domain)`
4. If no credentials exist → create new account:
   - Fill signup form with user identity from env (`USER_EMAIL`, `USER_FULL_NAME`)
   - Use `ATS_ACCOUNT_PASSWORD` for password
   - If email verification required → `GmailVerifier.wait_for_verification(domain, timeout=60)`
   - Save credentials to `AccountManager`
5. If all methods fail → `auth_status = "failed"` → END

**Existing code used:** `SSOHandler`, `AccountManager`, `GmailVerifier`, `cookie_dismisser`

**New code:** Account creation logic (signup form detection + fill). Currently SSO and credentials are handled but signup is manual.

**Route:** `auth_status == "logged_in" | "created"` → `form_planner` | `"failed"` → END

---

## Node 2: form_planner

**Purpose:** Scan current page, query stores, produce an ordered fill plan.

**Inputs:** `url`, `domain`, `platform`, `current_page`, Playwright page object

**Flow:**
1. Dismiss cookie banners (`cookie_dismisser`)
2. Screenshot current page
3. DOM scan: extract all visible form fields (label, type, selector, required flag)
4. Query **Platform Playbook**: "Greenhouse typically has 3 pages, fields: Name, Email, Phone, Resume, Cover Letter, Screening questions"
5. Query **Field Registry**: for each detected field label on this domain, get `typical_value`, `field_type`, `success_rate`
6. Query **Combobox Mappings**: for each select/combobox field, get `input_value → actual_option` mapping
7. Query **FormHints** (existing `form_prefetch`): page structures, nav steps
8. Merge all intelligence → produce `page_plan: list[FieldPlan]` ordered by DOM position (top-to-bottom)
9. Estimate `total_pages` from Platform Playbook if first page

**New code:** `FormPlanner` class that orchestrates DOM scan + store queries. DOM scanning logic extracted from `NativeFormFiller._scan_page()`.

**Route:** → `field_executor`

---

## Node 3: field_executor

**Purpose:** Fill all fields in the plan using FormIntelligence + Playwright.

**Flow:** For each `FieldPlan` in `page_plan` (top-to-bottom order):

1. **Resolution:**
   - If `combobox_mapping` exists → use it directly (deterministic, no LLM)
   - If `expected_value` from Field Registry with `success_rate > 0.8` → use it
   - Otherwise → `FormIntelligence.resolve()` (5-tier: pattern → cache → nano → LLM → vision)

2. **Fill:**
   - `text/textarea` → `page.fill(selector, value)`
   - `select` → `select_filler.fill_select()` (fuzzy match)
   - `combobox` → `select_filler.fill_custom_select()` (click → type → select)
   - `file` → `page.set_input_files(selector, cv_path)` (deduplicated)
   - `radio/checkbox` → `page.click(selector)`

3. **Record:** Append `FieldResult` to `fill_results`

**Existing code used:** `FormIntelligence`, `select_filler`, `fill_select`, `fill_custom_select`, `page.fill()`, `page.set_input_files()`

**No new code** — this is orchestration of existing modules.

**Route:** → `page_verifier`

---

## Node 4: page_verifier

**Purpose:** Verify that fields were actually filled correctly by checking DOM state.

**Flow:**
1. For each `FieldResult` where `success=True`:
   - Read the current DOM value of `selector`
   - Compare to `value_set`
   - If mismatch → mark as failed
2. Scan for error messages: elements with `class*="error"`, `role="alert"`, text matching `required|invalid|please enter`
3. Check for verification walls (`verification_detector`)
4. Screenshot the filled page

**Output:**
- `failed_fields` = fields where DOM value doesn't match or error adjacent
- `page_screenshot_b64` = screenshot for records / approval

**New code:** `PageVerifier` class — DOM value checking. Extracted from ad-hoc verification scattered across NativeFormFiller.

**Route:**
- `len(failed_fields) > 0 AND rescue_attempts < 2` → `rescue_node`
- `len(failed_fields) == 0` → `observer`
- Verification wall detected → END with status

---

## Node 5: rescue_node

**Purpose:** Handle failed fields by escalating to smarter methods.

**Flow:** For each field in `failed_fields`:

1. **Vision analysis:** Screenshot the specific field region, ask vision LLM "What does this field expect? What options are available?"
2. **LLM reasoning:** Pass the field context + error message + page screenshot to Claude/GPT: "This field failed to fill. The error is X. The options visible are Y. What should the value be?"
3. **If still failing:** Send to Telegram: "Field '{label}' on {domain} failed. Error: {msg}. Options: {opts}. What should I enter?" Wait for human response (60s timeout).
4. **Record the rescue:** Whatever method succeeded gets saved to memory:
   - Combobox Mappings: if a dropdown mapping was discovered
   - Field Registry: if a new typical_value was learned
   - AgentRulesDB: if this field should be escalated in future

**Increment** `rescue_attempts`. Max 2 per page.

**Existing code used:** `smart_llm_call()`, `FormIntelligence.resolve_async()` (Tier 5 vision), Telegram client

**New code:** `RescueResolver` — orchestrates the escalation ladder.

**Route:** → `page_verifier` (re-verify after rescue fills)

---

## Node 6: observer

**Purpose:** Record everything learned from this page to all stores.

**Runs after:** Every successfully verified page (before navigating to next page).

**Writes to:**

| Store | What gets written |
|-------|-------------------|
| **Field Registry** (NEW) | For each filled field: `(domain, field_label, field_type, page_num, value, success)` |
| **Combobox Mappings** (NEW) | For each select/combobox fill: `(domain, field_label, input_value, actual_option, method, success)` |
| **Platform Playbook** (NEW) | Aggregate: `(platform, pages_seen, field_labels, screening_questions, success)` |
| **FieldAuditDB** (existing) | Per-field: `(url, domain, platform, field_label, value, method, tier, confidence, model)` |
| **FormInteractionLog** (existing) | Per-step: `(session_id, domain, page_num, step_type, target_label, value, method)` |
| **FormExperienceDB** (existing) | Domain summary: `(domain, platform, pages, field_types, screening_questions, time)` |

**New code:** `FormObserver` class — unified write path to all 6 stores.

**Route:**
- More pages remaining → click Next/Continue → `form_planner` (next page)
- Last page (Submit button detected) → `approval_gate`

---

## Node 7: approval_gate

**Purpose:** Pause for human approval before submitting. Single checkpoint regardless of page count or platform.

**Flow:**
1. Take final screenshot of the complete form
2. Send to Telegram:
   - CV PDF attachment
   - Cover Letter PDF attachment (if generated)
   - Final form screenshot
   - Summary: "Ready to submit to {company} ({platform}). {n} pages filled, {m} fields. Approve?"
3. Wait for response (5 minute timeout):
   - **"yes" / "approve" / "submit"** → `approval_status = "approved"` → submit the form → `post_apply_hook()`
   - **corrections dict** (field: value pairs) → `approval_status = "corrected"` → apply corrections via Playwright → record to `CorrectionCapture` → re-screenshot → ask again
   - **"no" / "abort"** → `approval_status = "aborted"` → END
   - **timeout** → `approval_status = "aborted"` → END (safe default: never auto-submit)

**Existing code used:** Telegram client, `CorrectionCapture.record_corrections()`

**New code:** Telegram approval flow (send attachments + wait for reply). Currently this is manual in Claude Code sessions — needs to be automated via Telegram bot polling.

**Route:**
- `approved` → click Submit → `post_apply_hook()` → END (success)
- `corrected` → re-fill corrected fields → `page_verifier` on last page → `approval_gate` again
- `aborted` → END

---

## Tier 2 Stores (Shared Memory Layer)

### Field Registry

```sql
CREATE TABLE field_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    field_label TEXT NOT NULL,
    field_type TEXT NOT NULL,          -- text, select, file, radio, checkbox, combobox
    page_num INTEGER NOT NULL,
    selector TEXT,                      -- CSS selector that worked
    typical_value TEXT NOT NULL DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(domain, field_label, page_num)
);
CREATE INDEX idx_field_registry_domain ON field_registry (domain);
```

**Read path:** `form_planner` queries by `(domain)` to get expected fields + typical values.
**Write path:** `observer` upserts after every page fill.
**Success rate:** `success_count / (success_count + fail_count)`. Fields with rate > 0.8 get auto-filled without LLM.

### Combobox Mappings

```sql
CREATE TABLE combobox_mappings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    field_label TEXT NOT NULL,
    input_value TEXT NOT NULL,          -- what we wanted to fill ("UK")
    actual_option TEXT NOT NULL,        -- what the dropdown accepted ("United Kingdom")
    method TEXT NOT NULL DEFAULT '',    -- exact, abbreviation, startswith, contains, typed
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(domain, field_label, input_value)
);
CREATE INDEX idx_combobox_domain ON combobox_mappings (domain);
CREATE INDEX idx_combobox_lookup ON combobox_mappings (domain, field_label);
```

**Read path:** `field_executor` checks before fuzzy matching — if a mapping exists with `success_count > 0`, use `actual_option` directly.
**Write path:** `observer` records after every select/combobox fill. Also wired into `select_filler.py` return values.
**Deterministic fills:** After one successful fill, the same dropdown on the same domain never needs fuzzy matching again.

### Platform Playbook

```sql
CREATE TABLE platform_playbook (
    platform TEXT PRIMARY KEY,         -- greenhouse, lever, workday, smartrecruiters, etc.
    avg_pages REAL NOT NULL DEFAULT 0,
    total_applications INTEGER NOT NULL DEFAULT 0,
    common_fields TEXT NOT NULL DEFAULT '[]',        -- JSON: ["Name", "Email", "Phone", ...]
    common_screening TEXT NOT NULL DEFAULT '[]',     -- JSON: ["Sponsorship?", "Salary?", ...]
    common_field_types TEXT NOT NULL DEFAULT '{}',   -- JSON: {"Name": "text", "Resume": "file"}
    has_file_upload INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    avg_time_seconds REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
```

**Read path:** `form_planner` queries by `platform` to know what to expect on unknown domains.
**Write path:** `observer` updates running averages after every application.
**Cross-domain learning:** First time on a new Greenhouse domain → Playbook says "expect 3 pages, these fields" based on 50+ prior Greenhouse applications.

---

## Integration Points

### Into apply_job() (applicator.py)

Replace the adapter call block with FormPilot:

```python
# Current:
result = _call_fill_and_submit(adapter, page, url, merged_answers, ...)

# New:
from jobpulse.form_pilot import run_form_pilot
result = run_form_pilot(
    page=page,
    url=url,
    domain=domain,
    platform=platform_key,
    cv_path=cv_path,
    cl_path=cl_path,
    job_context=job_context,
    merged_answers=merged_answers,
    dry_run=dry_run,
)
```

The `dry_run` flag controls whether the approval gate sends to Telegram (True) or auto-submits (False, future).

### Into post_apply_hook()

No changes. FormPilot's `result` dict matches the existing contract: `{success, pages_filled, field_types, screening_questions, time_seconds}`.

### Into form_prefetch.py

Add queries to the 3 new stores alongside existing FormExperienceDB/InteractionLog/NavigationLearner:

```python
# 4. Field Registry
from jobpulse.field_registry import FieldRegistryDB
registry = FieldRegistryDB()
fields = registry.get_fields(domain)
if fields:
    hints.registered_fields = fields

# 5. Combobox Mappings
from jobpulse.combobox_mappings import ComboboxMappingsDB
mappings = ComboboxMappingsDB()
combos = mappings.get_mappings(domain)
if combos:
    hints.combobox_mappings = combos

# 6. Platform Playbook
from jobpulse.platform_playbook import PlatformPlaybookDB
playbook = PlatformPlaybookDB()
platform_info = playbook.get_platform(platform)
if platform_info:
    hints.platform_playbook = platform_info
```

---

## What Stays, What Changes, What's New

| Component | Status | Notes |
|-----------|--------|-------|
| `applicator.py:apply_job()` | **Modified** | Calls `run_form_pilot()` instead of `_call_fill_and_submit()` |
| `NativeFormFiller` | **Replaced** | Its logic splits across form_planner + field_executor + page_verifier |
| `ApplicationOrchestrator` | **Replaced** | auth_gate subsumes its auth/navigate/verify logic |
| `FormIntelligence` | **Unchanged** | Called by field_executor as-is |
| `select_filler.py` | **Unchanged** | Called by field_executor for dropdown fills |
| `form_prefetch.py` | **Extended** | Queries 3 new stores |
| `post_apply_hook.py` | **Unchanged** | FormPilot returns compatible result dict |
| `SSOHandler` | **Unchanged** | Called by auth_gate |
| `AccountManager` | **Unchanged** | Called by auth_gate |
| `GmailVerifier` | **Unchanged** | Called by auth_gate for email verification |
| `cookie_dismisser` | **Unchanged** | Called by form_planner before DOM scan |
| `FieldAuditDB` | **Unchanged** | Written by observer |
| `CorrectionCapture` | **Unchanged** | Written by approval_gate on corrections |
| `AgentRulesDB` | **Unchanged** | Written by rescue_node |

---

## New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `jobpulse/form_pilot.py` | LangGraph StateGraph definition + `run_form_pilot()` entry point | ~150 |
| `jobpulse/form_pilot_nodes.py` | All 7 node functions (stateless) | ~400 |
| `jobpulse/form_pilot_state.py` | State TypedDict + supporting types | ~60 |
| `jobpulse/field_registry.py` | FieldRegistryDB (SQLite store) | ~100 |
| `jobpulse/combobox_mappings.py` | ComboboxMappingsDB (SQLite store) | ~100 |
| `jobpulse/platform_playbook.py` | PlatformPlaybookDB (SQLite store) | ~100 |
| `jobpulse/page_scanner.py` | DOM field extraction (from NativeFormFiller) | ~80 |
| `jobpulse/page_verifier.py` | Post-fill DOM verification | ~80 |
| `jobpulse/rescue_resolver.py` | LLM/vision/human escalation for failed fields | ~100 |
| `jobpulse/form_observer.py` | Unified write path to all 6 stores | ~80 |
| `tests/jobpulse/test_field_registry.py` | Field Registry tests | ~80 |
| `tests/jobpulse/test_combobox_mappings.py` | Combobox Mappings tests | ~80 |
| `tests/jobpulse/test_platform_playbook.py` | Platform Playbook tests | ~80 |
| `tests/jobpulse/test_form_pilot.py` | FormPilot graph integration tests | ~120 |

**Total new code:** ~1,700 lines across 14 files.

---

## Testing Strategy

1. **Unit tests** for each store (FieldRegistryDB, ComboboxMappingsDB, PlatformPlaybookDB) — CRUD, upsert, success rate calculation. All use `tmp_path`.
2. **Unit tests** for each node — mock Playwright page, verify state transitions.
3. **Integration test** — mock Playwright, run full graph with pre-populated stores, verify observer writes.
4. **No production DB access** — all tests use `tmp_path` fixtures.

---

## Confidence Threshold (Future)

During testing phase: approval_gate ALWAYS pauses for human approval.

Future autonomous mode (when memory is strong enough):

```python
CONFIDENCE_AUTO_SUBMIT = 0.9  # all fields resolved with confidence >= 0.9
MIN_DOMAIN_APPLICATIONS = 3    # applied to this domain 3+ times before

if all(f.confidence >= CONFIDENCE_AUTO_SUBMIT for f in all_fill_results) \
   and platform_playbook.total_applications >= MIN_DOMAIN_APPLICATIONS:
    # Auto-submit without approval
```

This is NOT implemented now. Just a note for the future flag.

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| LangGraph overhead adds latency | Nodes are thin orchestrators calling existing code. No agent dispatch overhead — it's a state machine, not multi-agent. |
| Auth gate creates unwanted accounts | Only creates accounts when SSO and stored credentials both fail. Uses existing `ATS_ACCOUNT_PASSWORD` env var. |
| Rescue node LLM costs | Max 2 rescue attempts per page. Vision calls only when text methods fail. Capped at ~$0.01/rescue. |
| Observer write failures | Non-blocking: any store write failure is logged but doesn't affect the application. Same pattern as `post_apply_hook`. |
| Telegram approval timeout | 5 minute timeout → abort. Never auto-submits. Safe default. |
