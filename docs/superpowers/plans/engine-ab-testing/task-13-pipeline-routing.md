# Task 13: Pipeline Engine Routing — applicator → ralph_loop → autopilot

**Files:**
- Modify: `jobpulse/applicator.py`
- Modify: `jobpulse/ralph_loop/loop.py`
- Modify: `jobpulse/job_autopilot.py`

**Why:** Thread the `engine` parameter from Telegram approval all the way down to the orchestrator. This is the plumbing that makes `approve 3 pw` work.

**Dependencies:** Task 12 (orchestrator must accept engine param)

---

- [ ] **Step 1: Update `ralph_apply_sync` to accept engine param**

In `jobpulse/ralph_loop/loop.py`, add `engine: str = "extension"` to the `ralph_apply_sync` signature (line ~150):

```python
def ralph_apply_sync(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    cover_letter_path: Path | None = None,
    cl_generator: Any | None = None,
    custom_answers: dict | None = None,
    db_path: str | None = None,
    dry_run: bool = False,
    iteration_callback: Any | None = None,
    engine: str = "extension",
) -> dict:
```

Pass `engine` to `PatternStore` queries:
```python
    fixes = store.get_fixes(platform, step_name, error_sig, engine=engine)
```

And to `apply_job`:
```python
    result = apply_job(..., engine=engine)
```

- [ ] **Step 2: Update `apply_job` to accept engine param**

In `jobpulse/applicator.py`, add `engine: str = "extension"` to `apply_job` signature (line 103):

```python
def apply_job(
    url: str,
    ats_platform: str | None,
    cv_path: Path,
    ...
    dry_run: bool = False,
    engine: str = "extension",
) -> dict:
```

When constructing the orchestrator (currently done inside adapters), pass `engine` through via `merged_answers`:

```python
    merged_answers["_engine"] = engine
```

The adapter reads `_engine` and passes it to `ApplicationOrchestrator(engine=engine)`.

- [ ] **Step 3: Update `approve_jobs` to parse engine from args**

In `jobpulse/job_autopilot.py`, find `approve_jobs` (line ~389). Update to parse engine from the args string:

```python
def approve_jobs(args: str, engine: str = "extension") -> str:
```

At the top of `approve_jobs`, detect engine shorthand:

```python
    # Parse engine override: "approve 3 pw" or "approve 3 playwright"
    parts = args.strip().split()
    engine_override = engine
    if len(parts) >= 2 and parts[-1].lower() in ("pw", "playwright"):
        engine_override = "playwright"
        args = " ".join(parts[:-1])
    elif len(parts) >= 2 and parts[-1].lower() in ("ext", "extension"):
        engine_override = "extension"
        args = " ".join(parts[:-1])
```

Then pass `engine=engine_override` to `ralph_apply_sync(...)`.

- [ ] **Step 4: Update `_run_scan_window_inner` to pass engine**

In the auto-apply path (line ~650), pass `engine` to `ralph_apply_sync`:

```python
    result = ralph_apply_sync(
        url=listing.url,
        ats_platform=listing.ats_platform,
        cv_path=cv_path,
        cover_letter_path=cover_letter_path,
        cl_generator=cl_generator,
        custom_answers=None,
        engine=engine,  # from approve_jobs or default
    )
```

- [ ] **Step 5: Commit**

```bash
git add jobpulse/applicator.py jobpulse/ralph_loop/loop.py jobpulse/job_autopilot.py
git commit -m "feat: thread engine param through pipeline — applicator → ralph_loop → autopilot

'approve 3 pw' now routes through the entire pipeline to create a
PlaywrightDriver instead of using ExtensionBridge."
```
