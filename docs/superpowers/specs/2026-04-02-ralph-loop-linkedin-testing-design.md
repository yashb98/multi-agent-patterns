# Ralph Loop LinkedIn Testing — Design Spec

## Goal

Build a CLI-driven dry-run test harness for Ralph Loop that exercises the real LinkedIn Easy Apply pipeline without submitting applications, captures screenshots at every iteration, records learned fixes with source tracking to prevent pattern pollution, and stores structured results for analysis.

## Scope

- **Platform:** LinkedIn only (v1). Other adapters follow in future iterations.
- **Approach:** Hybrid — live URLs for discovery, saved fixtures for regression (fixture capture is follow-up).
- **Mode:** `dry_run=True` only from CLI. No submit, no daily cap decrement, no Notion sync.

## Architecture

### New Files

| File | Responsibility |
|------|---------------|
| `jobpulse/ralph_loop/test_runner.py` | CLI-callable test harness, orchestrates dry-run, prints results |
| `jobpulse/ralph_loop/test_store.py` | SQLite results storage + file directory management |
| `jobpulse/runner.py` (modify) | Add `ralph-test` CLI command |
| `jobpulse/ralph_loop/loop.py` (modify) | Thread `dry_run` flag + `iteration_callback` |
| `jobpulse/ralph_loop/pattern_store.py` (modify) | Add `source` column, confirmation logic, stale pruning |
| `jobpulse/ats_adapters/linkedin.py` (modify) | Skip submit click when `dry_run=True` |
| `jobpulse/applicator.py` (modify) | Thread `dry_run` through `apply_job()` |

### Flow

```
CLI: python -m jobpulse.runner ralph-test linkedin <url>
  |
  v
test_runner.ralph_test_run(platform="linkedin", url=<url>, dry_run=True)
  |
  +-- 1. Create test run record in SQLite
  +-- 2. Create screenshot directory: data/ralph_tests/linkedin/{timestamp}/
  +-- 3. Build minimal JobListing from URL
  +-- 4. Load profile + build custom_answers (reuse job_autopilot logic)
  +-- 5. Call ralph_apply_sync(job, dry_run=True, iteration_callback=record_iteration)
  |     +-- Each iteration:
  |         +-- Try fill -> screenshot -> diagnose -> fix
  |         +-- Callback: test_store.record_iteration(screenshot, fixes, diagnosis)
  +-- 6. Compute verdict from final state
  +-- 7. Update SQLite with results
  +-- 8. Print rich summary table
  +-- 9. Auto-prune old test data (>90 days)
```

## dry_run Flag Threading

### What dry_run=True Changes

- LinkedIn adapter: fills all fields, answers all screening questions, does NOT click submit button
- Screenshots taken at every page of the wizard (not just final)
- No application record written to job_db
- No daily cap decrement
- No Notion sync

### What dry_run=True Does NOT Change

- Browser launches normally (headed mode, anti-detection flags)
- All field filling runs identically
- Ralph Loop diagnosis + fix cycle runs identically
- PatternStore records learned fixes (with `source="test"`)

## PatternStore Safety — Source Tracking + Confirmation

### Schema Changes to fix_patterns

```sql
ALTER TABLE fix_patterns ADD COLUMN source TEXT DEFAULT 'production';
ALTER TABLE fix_patterns ADD COLUMN confirmed BOOLEAN DEFAULT TRUE;
ALTER TABLE fix_patterns ADD COLUMN occurrence_count INTEGER DEFAULT 1;
ALTER TABLE fix_patterns ADD COLUMN superseded BOOLEAN DEFAULT FALSE;
```

### Confirmation Rules

| Source | Confirmed? | Applied in production? |
|--------|-----------|----------------------|
| `production` | Always `True` | Yes, immediately |
| `test` (1 occurrence) | `False` | No — skipped with log warning |
| `test` (2+ occurrences) | Auto-promoted to `True` | Yes |
| `test` + same fix seen in `production` | Auto-promoted to `True` | Yes |
| `manual` | Always `True` | Yes, immediately |

### Safety Measures

1. **Test isolation flag** — `PatternStore(mode="test")` sets source automatically. No accidental test-as-production recording.

2. **Stale fix pruning** — Test fixes with `occurrence_count=1` older than 14 days auto-deleted on PatternStore init.

3. **Fix audit log** — Every fix application (used or skipped) logged to `ralph_test_iterations` with fix ID for traceability.

4. **Collision guard** — If test fix and production fix exist for same error signature, production fix always wins. Test fix marked `superseded=True`.

5. **CLI summary shows provenance** — Rich table marks each fix as `[TEST]` or `[PROD]`.

### build_overrides_from_fixes() Change

```python
def build_overrides_from_fixes(platform, error_sig):
    fixes = get_fixes(platform, error_sig)
    for fix in fixes:
        if fix.source == "test" and not fix.confirmed:
            logger.info("Skipping unconfirmed test fix: %s", fix.id)
            continue
        if fix.superseded:
            continue
        # ... apply fix as normal
```

## Test Results Storage

### SQLite Tables (in scan_learning.db)

```sql
CREATE TABLE ralph_test_runs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    url TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    iterations INTEGER DEFAULT 0,
    fixes_applied TEXT,          -- JSON array of fix IDs
    fixes_skipped TEXT,          -- JSON array of skipped unconfirmed fix IDs
    fields_filled INTEGER DEFAULT 0,
    fields_failed INTEGER DEFAULT 0,
    final_verdict TEXT,          -- "success" | "partial" | "blocked" | "error"
    error_summary TEXT,
    screenshot_dir TEXT,         -- path to file directory
    dry_run BOOLEAN DEFAULT TRUE
);

CREATE TABLE ralph_test_iterations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES ralph_test_runs(id),
    iteration INTEGER NOT NULL,
    screenshot_path TEXT,
    diagnosis TEXT,              -- vision model assessment
    fix_type TEXT,               -- selector | value | skip | retry | escalate
    fix_detail TEXT,             -- JSON of what was changed
    duration_ms INTEGER
);
```

### File Layout

```
data/ralph_tests/
  linkedin/
    2026-04-02_143022/
      iter_0.png
      iter_1.png
      iter_2.png
      summary.json
```

### Verdicts

- `success` — All visible fields filled, no errors in final screenshot
- `partial` — Some fields filled but diagnosis still shows issues after max iterations
- `blocked` — Verification wall or login wall detected
- `error` — Exception thrown (browser crash, timeout)

### Cleanup

Test runs older than 90 days auto-delete (SQLite rows + screenshot directories) on next `ralph_test_run()` call.

## CLI Command

```python
@app.command()
def ralph_test(
    platform: str = typer.Argument("linkedin"),
    url: str = typer.Argument(..., help="Job posting URL to test"),
    max_iterations: int = typer.Option(5, help="Max Ralph Loop iterations"),
):
    """Run Ralph Loop in dry-run mode against a live job URL."""
```

### Rich Output

```
+---------------------------------------------------+
|  Ralph Loop Test -- LinkedIn                       |
|  URL: linkedin.com/jobs/view/123456                |
|  Started: 2026-04-02 14:30:22                      |
+------+------------+----------+--------------------+|
| Iter | Diagnosis  | Fix Type | Detail             ||
+------+------------+----------+--------------------+|
|  0   | Location   | selector | typeahead click    ||
|  1   | Screening  | value    | salary -> 30000    ||
|  2   | Clean      | --       | No issues found    ||
+------+------------+----------+--------------------+|
|  Verdict: SUCCESS  |  Fields: 12/12  |  3 fixes    |
|  Screenshots: data/ralph_tests/linkedin/...        |
|  Fixes: 2 [PROD] 1 [TEST-unconfirmed]             |
+---------------------------------------------------+
```

## What v1 Does NOT Include

- No Telegram integration (follow-up)
- No HTML fixture capture/replay (follow-up)
- No nightly regression runner (follow-up)
- No `dry_run=False` CLI flag (must use Python directly to submit)
- No platforms other than LinkedIn (follow-up)

## Testing Strategy

- All new code tested with pytest + tmp_path for SQLite
- PatternStore source/confirmation logic: unit tests for each rule in the confirmation table
- test_runner: mock ralph_apply_sync, verify SQLite records + file creation
- Integration: one end-to-end test with mocked browser (no live LinkedIn)
