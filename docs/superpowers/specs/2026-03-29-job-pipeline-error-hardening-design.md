# Job Pipeline Error Hardening — Design Spec

> Fix 60+ edge cases and error handling gaps across the job application pipeline using shared utilities + surgical fixes.

## Approach

**Approach B: Shared Utilities + Surgical Fixes.** Create 4 small utilities, then apply targeted fixes across 10 files in 4 phases.

## Phase 1 — Shared Utilities (Foundation)

New file: `jobpulse/utils/safe_io.py` (~120 lines total)

### 1.1 `safe_openai_call(client, **kwargs) -> str | None`
- Wraps `client.chat.completions.create()` with timeout (default 60s)
- Checks `response.choices[0].message.content` is not None
- Returns content string or None (never crashes)
- Logs with agent name + attempted action on failure

### 1.2 `managed_browser(headless=True, **launch_args)` context manager
- Yields `(browser, context, page)` tuple
- `try/finally` guarantees `browser.close()` even on exception
- Wraps Playwright `sync_playwright()` startup
- Logs browser lifecycle events

### 1.3 `locked_json_file(path)` context manager
- Acquires file lock (fcntl.flock on macOS) before read
- Yields parsed JSON data
- On `__exit__`, writes back atomically (write to .tmp, rename)
- Prevents race conditions on shared JSON files like `pending_review_jobs.json`

### 1.4 `atomic_sqlite(db_path)` context manager
- Wraps `sqlite3.connect()` with `BEGIN EXCLUSIVE` transaction
- Auto-commits on success, rolls back on exception
- Prevents race conditions in rate_limiter and job_autopilot

## Phase 2 — P0 Crash/Data-Loss Fixes

### 2.1 Browser resource leaks
**Files:** `job_scanner.py`, `jobpulse/ats_adapters/*.py` (5 adapters)
- Replace raw Playwright open/close with `managed_browser()` context manager
- Every browser session guaranteed cleanup

### 2.2 OpenAI None responses
**Files:** `cv_tailor.py`, `cover_letter_agent.py`
- Replace direct `client.chat.completions.create()` with `safe_openai_call()`
- Handle None return (log + return early with error)

### 2.3 Assert to proper error
**File:** `cv_tailor.py:248`
- Replace `assert best_score is not None` with `if best_score is None: return None`
- Log refinement failure with context

### 2.4 Pending review race condition
**File:** `job_autopilot.py`
- Replace raw `json.load/dump` with `locked_json_file()` context manager

## Phase 3 — P1 Silent Failure Fixes

### 3.1 Distinguish "no results" from "API failed"
**Files:** `job_scanner.py`, `jd_analyzer.py`, `github_agent.py`
- Functions currently return `[]` on both success-empty and failure
- Add optional `error` field to return values OR return `(results, error)` tuple
- Callers can check: `if error: log warning` vs `if not results: log "none found"`

### 3.2 Rate limit detection + backoff
**File:** `job_scanner.py`
- Check response status for 429
- On 429: log, sleep with exponential backoff (2s, 4s, 8s), retry up to 3x
- After 3 failures, return error instead of empty list

### 3.3 Notion failure visibility
**File:** `job_autopilot.py`
- Accumulate Notion sync failures in a list
- At end of pipeline run, if any Notion failures, send single Telegram alert:
  "N Notion syncs failed — check logs"
- Don't change the continue-on-error behavior (correct for resilience)

### 3.4 Template file missing fallback
**Files:** `cv_tailor.py`, `cover_letter_agent.py`
- Wrap `_TEMPLATE_PATH.read_text()` in try/except FileNotFoundError
- Return clear error message: "Template not found at {path}. Run setup first."

## Phase 4 — P2 Concurrency + Validation Fixes

### 4.1 Atomic rate limiter
**File:** `rate_limiter.py`
- Replace `sqlite3.connect()` calls with `atomic_sqlite()` for `record_application()`
- Ensures check-and-increment is atomic

### 4.2 Input validation
**File:** `job_scanner.py` — validate config JSON schema before use
**File:** `jd_analyzer.py` — reject empty/whitespace-only JD text early
**File:** `cover_letter_agent.py` — truncate JD at sentence boundary, not char count

### 4.3 Stub platform warnings
**File:** `job_scanner.py`
- When Indeed/TotalJobs/Glassdoor requested, log warning: "Platform {name} not yet implemented, skipping"
- Return `([], "not_implemented")` instead of silent `[]`

### 4.4 Profile consistency
**File:** `cover_letter_agent.py`
- Import PROFILE from `applicator.py` instead of hardcoding
- Single source of truth for user profile data

### 4.5 ATS scorer robustness
**File:** `ats_scorer.py`
- Warn when synonym file missing (not just silent empty dict)
- Use word-boundary regex for keyword matching to avoid false positives

## Files Modified

| File | Phase | Changes |
|------|-------|---------|
| `jobpulse/utils/safe_io.py` | 1 | NEW — 4 utilities |
| `jobpulse/job_scanner.py` | 2,3,4 | managed_browser, rate limit backoff, validation, stub warnings |
| `jobpulse/cv_tailor.py` | 2,3 | safe_openai_call, assert fix, template fallback |
| `jobpulse/cover_letter_agent.py` | 2,3,4 | safe_openai_call, template fallback, profile import, truncation |
| `jobpulse/job_autopilot.py` | 2,3 | locked_json_file, Notion failure alerts |
| `jobpulse/applicator.py` | 2 | managed_browser passed to adapters |
| `jobpulse/ats_adapters/*.py` | 2 | managed_browser in all 5 adapters |
| `jobpulse/github_agent.py` | 3 | error vs empty distinction |
| `jobpulse/jd_analyzer.py` | 3,4 | error vs empty, empty JD validation |
| `jobpulse/rate_limiter.py` | 4 | atomic_sqlite |
| `jobpulse/ats_scorer.py` | 4 | synonym warning, word-boundary regex |

## What We Are NOT Doing

- No retry middleware framework (just `tenacity.retry` decorator where needed)
- No circuit breakers (volume too low)
- No new dashboard (existing `/health.html` suffices)
- No Result[T] generic type (too much ceremony for this codebase)
- No changes to working code paths — only fixing broken/missing error handling

## Testing

Each phase should be verified by:
1. Running existing tests: `pytest tests/ -v`
2. Checking imports resolve: `python -c "from jobpulse.utils.safe_io import safe_openai_call"`
3. Spot-checking modified functions don't break callers
