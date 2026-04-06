# Live Extension Test Harness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace dead hardcoded Ralph Loop tests with a `ralph-test --live` command that scrapes fresh job URLs via the extension and tests the full pipeline against real ATS pages.

**Architecture:** Add `ralph_live_test()` to `test_runner.py` that calls `scan_platforms()` for fresh URLs, picks N with platform diversity, runs each through `ralph_test_run(dry_run=True)`, and prints a summary table. Delete dead test files, strip fake integration tests from `test_ralph_loop.py`.

**Tech Stack:** Python, existing `job_scanner.py` scanners, existing `ralph_loop/` modules, existing `ext_bridge.py` + Chrome extension.

---

### Task 1: Add `ralph_live_test()` to test_runner.py

**Files:**
- Modify: `jobpulse/ralph_loop/test_runner.py:169` (append after `ralph_test_run`)
- Test: `tests/test_ralph_test_runner.py` (add unit test)

- [ ] **Step 1: Write the failing test**

In `tests/test_ralph_test_runner.py`, add a test that verifies `ralph_live_test` calls `scan_platforms` and feeds results to `ralph_test_run`:

```python
class TestRalphLiveTest:
    def test_scrapes_and_tests_each_url(self, tmp_path):
        """ralph_live_test scrapes fresh URLs and runs each through ralph_test_run."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        fake_jobs = [
            {"url": "https://linkedin.com/jobs/view/111", "platform": "linkedin", "title": "ML Engineer"},
            {"url": "https://uk.indeed.com/viewjob?jk=abc", "platform": "indeed", "title": "Data Scientist"},
            {"url": "https://www.reed.co.uk/jobs/analyst/222", "platform": "reed", "title": "Analyst"},
        ]

        mock_result = TestRunResult(
            run_id=1, platform="linkedin", url="https://linkedin.com/jobs/view/111",
            verdict="success", iterations=1, duration_ms=500,
        )

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=fake_jobs) as mock_scan, \
             patch("jobpulse.ralph_loop.test_runner.ralph_test_run", return_value=mock_result) as mock_run:
            results = ralph_live_test(
                platforms=["linkedin", "indeed", "reed"],
                count=3,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )

        mock_scan.assert_called_once_with(["linkedin", "indeed", "reed"])
        assert mock_run.call_count == 3
        assert len(results) == 3

    def test_round_robin_platform_diversity(self, tmp_path):
        """With count=2, picks 1 from each platform rather than 2 from the first."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        fake_jobs = [
            {"url": "https://linkedin.com/jobs/view/1", "platform": "linkedin", "title": "A"},
            {"url": "https://linkedin.com/jobs/view/2", "platform": "linkedin", "title": "B"},
            {"url": "https://uk.indeed.com/viewjob?jk=x", "platform": "indeed", "title": "C"},
        ]

        mock_result = TestRunResult(run_id=1, platform="test", url="x", verdict="success", iterations=1, duration_ms=100)

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=fake_jobs), \
             patch("jobpulse.ralph_loop.test_runner.ralph_test_run", return_value=mock_result) as mock_run:
            results = ralph_live_test(
                platforms=["linkedin", "indeed"],
                count=2,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )

        # Should pick 1 linkedin + 1 indeed, not 2 linkedin
        urls_tested = [c.kwargs["url"] for c in mock_run.call_args_list]
        platforms_tested = set()
        for u in urls_tested:
            if "linkedin" in u:
                platforms_tested.add("linkedin")
            elif "indeed" in u:
                platforms_tested.add("indeed")
        assert len(platforms_tested) == 2

    def test_no_jobs_found_returns_empty(self, tmp_path):
        """When scanners return nothing, ralph_live_test returns empty list."""
        from jobpulse.ralph_loop.test_runner import ralph_live_test

        with patch("jobpulse.ralph_loop.test_runner.scan_platforms", return_value=[]):
            results = ralph_live_test(
                platforms=["linkedin"],
                count=3,
                store_db_path=str(tmp_path / "store.db"),
                pattern_db_path=str(tmp_path / "patterns.db"),
                base_dir=tmp_path / "ralph_tests",
            )
        assert results == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ralph_test_runner.py::TestRalphLiveTest -v`
Expected: FAIL — `ImportError: cannot import name 'ralph_live_test'`

- [ ] **Step 3: Implement `ralph_live_test()`**

Append to `jobpulse/ralph_loop/test_runner.py` after the `_notify_telegram` function:

```python
def ralph_live_test(
    platforms: list[str] | None = None,
    count: int = 3,
    max_iterations: int = 5,
    store_db_path: str | None = None,
    pattern_db_path: str | None = None,
    base_dir: Path | None = None,
) -> list[TestRunResult]:
    """Scrape fresh job URLs and test each through Ralph Loop (dry_run=True).

    1. Calls scan_platforms() for fresh URLs
    2. Picks `count` jobs with round-robin platform diversity
    3. Runs each through ralph_test_run(dry_run=True)
    4. Returns list of TestRunResult
    """
    from jobpulse.ext_adapter import _detect_ats_platform

    jobs = scan_platforms(platforms)
    if not jobs:
        logger.warning("ralph_live_test: no jobs found from scanners")
        return []

    selected = _select_diverse_jobs(jobs, count)
    logger.info("ralph_live_test: selected %d jobs from %d scraped", len(selected), len(jobs))

    results: list[TestRunResult] = []
    for job in selected:
        url = job["url"]
        platform = job.get("platform") or _detect_ats_platform(url)
        logger.info("ralph_live_test: testing %s — %s", platform, url[:60])

        result = ralph_test_run(
            platform=platform,
            url=url,
            max_iterations=max_iterations,
            store_db_path=store_db_path,
            pattern_db_path=pattern_db_path,
            base_dir=base_dir,
        )
        results.append(result)

    return results


def _select_diverse_jobs(jobs: list[dict], count: int) -> list[dict]:
    """Pick up to `count` jobs with round-robin platform diversity."""
    from collections import defaultdict

    by_platform: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        by_platform[job.get("platform", "generic")].append(job)

    selected: list[dict] = []
    seen_urls: set[str] = set()
    platforms = list(by_platform.keys())
    idx = 0

    while len(selected) < count and platforms:
        platform = platforms[idx % len(platforms)]
        bucket = by_platform[platform]
        if bucket:
            job = bucket.pop(0)
            if job["url"] not in seen_urls:
                seen_urls.add(job["url"])
                selected.append(job)
        else:
            platforms.remove(platform)
            if not platforms:
                break
            continue
        idx += 1

    return selected
```

Also add the import at the top of `test_runner.py`:

```python
from jobpulse.job_scanner import scan_platforms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ralph_test_runner.py::TestRalphLiveTest -v`
Expected: All 3 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ralph_loop/test_runner.py tests/test_ralph_test_runner.py
git commit -m "feat(ralph): add ralph_live_test() — scrape fresh URLs and test via extension"
```

---

### Task 2: Add `format_live_summary()` to cli_output.py

**Files:**
- Modify: `jobpulse/ralph_loop/cli_output.py:107` (append)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ralph_test_runner.py`:

```python
class TestFormatLiveSummary:
    def test_formats_multiple_results(self):
        from jobpulse.ralph_loop.cli_output import format_live_summary

        results = [
            TestRunResult(platform="linkedin", url="https://linkedin.com/jobs/view/1", verdict="success", iterations=1, fields_filled=12, duration_ms=5000),
            TestRunResult(platform="indeed", url="https://indeed.com/viewjob?jk=x", verdict="blocked", iterations=2, error_summary="Cloudflare", duration_ms=3000),
        ]
        output = format_live_summary(results)
        assert "linkedin" in output.lower()
        assert "indeed" in output.lower()
        assert "SUCCESS" in output
        assert "BLOCKED" in output
        assert "2 jobs tested" in output.lower()

    def test_empty_results(self):
        from jobpulse.ralph_loop.cli_output import format_live_summary

        output = format_live_summary([])
        assert "no jobs" in output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ralph_test_runner.py::TestFormatLiveSummary -v`
Expected: FAIL — `ImportError: cannot import name 'format_live_summary'`

- [ ] **Step 3: Implement `format_live_summary()`**

Append to `jobpulse/ralph_loop/cli_output.py`:

```python
def format_live_summary(results: list[TestRunResult]) -> str:
    """Format a batch of live test results as a summary table."""
    if not results:
        return "No jobs tested — scanners returned no results."

    try:
        return _format_live_rich(results)
    except ImportError:
        return _format_live_plain(results)


def _format_live_rich(results: list[TestRunResult]) -> str:
    from io import StringIO
    from rich.console import Console
    from rich.table import Table

    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100)

    table = Table(title=f"{len(results)} jobs tested", show_header=True, header_style="bold")
    table.add_column("Platform", width=10)
    table.add_column("URL", width=40)
    table.add_column("Verdict", justify="center", width=10)
    table.add_column("Iters", justify="center", width=5)
    table.add_column("Fields", justify="center", width=8)
    table.add_column("Time", justify="right", width=6)

    for r in results:
        verdict_label = _VERDICT_LABELS.get(r.verdict, r.verdict.upper())
        fields = f"{r.fields_filled}/{r.fields_filled + r.fields_failed}" if r.fields_filled or r.fields_failed else "--"
        table.add_row(
            r.platform.title(),
            r.url[:40],
            verdict_label,
            str(r.iterations),
            fields,
            f"{r.duration_ms / 1000:.1f}s",
        )

    console.print(table)

    verdicts = [r.verdict for r in results]
    console.print(
        f"\nPassed: {verdicts.count('success')}  "
        f"Blocked: {verdicts.count('blocked')}  "
        f"Partial: {verdicts.count('partial')}  "
        f"Error: {verdicts.count('error')}"
    )
    return buf.getvalue()


def _format_live_plain(results: list[TestRunResult]) -> str:
    lines = [f"{len(results)} jobs tested", ""]
    for r in results:
        verdict_label = _VERDICT_LABELS.get(r.verdict, r.verdict.upper())
        fields = f"{r.fields_filled}/{r.fields_filled + r.fields_failed}" if r.fields_filled or r.fields_failed else "--"
        lines.append(
            f"  {r.platform.title():10s} {r.url[:40]:40s} {verdict_label:8s} "
            f"{r.iterations} iters  {fields} fields  {r.duration_ms / 1000:.1f}s"
        )
    verdicts = [r.verdict for r in results]
    lines.append("")
    lines.append(
        f"Passed: {verdicts.count('success')}  "
        f"Blocked: {verdicts.count('blocked')}  "
        f"Partial: {verdicts.count('partial')}  "
        f"Error: {verdicts.count('error')}"
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ralph_test_runner.py::TestFormatLiveSummary -v`
Expected: All 2 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ralph_loop/cli_output.py tests/test_ralph_test_runner.py
git commit -m "feat(ralph): add format_live_summary() for batch test output"
```

---

### Task 3: Wire `--live` flag into runner.py CLI

**Files:**
- Modify: `jobpulse/runner.py:286-316` (rewrite the `ralph-test` CLI block)

- [ ] **Step 1: Update the CLI parser**

Replace the `ralph-test` block in `runner.py` (lines 286-316) with:

```python
    elif command == "ralph-test":
        from jobpulse.ralph_loop.cli_output import format_test_result
        from jobpulse.ralph_loop.test_runner import ralph_test_run

        args = sys.argv[2:]
        is_live = "--live" in args
        args = [a for a in args if a != "--live"]

        # Parse common flags
        platform = "linkedin"
        max_iters = 5
        count = 3
        platforms_csv = None

        for i, arg in enumerate(args):
            if arg == "--platform" and i + 1 < len(args):
                platform = args[i + 1]
            elif arg == "--platforms" and i + 1 < len(args):
                platforms_csv = args[i + 1]
            elif arg == "--max-iterations" and i + 1 < len(args):
                max_iters = int(args[i + 1])
            elif arg == "--count" and i + 1 < len(args):
                count = int(args[i + 1])

        if is_live:
            # Live mode: scrape fresh URLs and test each
            from jobpulse.ralph_loop.test_runner import ralph_live_test
            from jobpulse.ralph_loop.cli_output import format_live_summary

            live_platforms = platforms_csv.split(",") if platforms_csv else None
            print(f"Scraping fresh job URLs from {live_platforms or 'all platforms'}...")
            results = ralph_live_test(
                platforms=live_platforms,
                count=count,
                max_iterations=max_iters,
            )
            print(format_live_summary(results))
        else:
            # Single URL mode (existing behavior)
            url = args[0] if args and not args[0].startswith("--") else None
            if not url:
                print(
                    "Usage:\n"
                    "  python -m jobpulse.runner ralph-test <url> [--platform linkedin] [--max-iterations 5]\n"
                    "  python -m jobpulse.runner ralph-test --live [--platforms linkedin,reed] [--count 3]"
                )
                sys.exit(1)

            print(f"Running Ralph Loop dry-run test on {platform}: {url[:60]}")
            result = ralph_test_run(platform=platform, url=url, max_iterations=max_iters)

            from jobpulse.ralph_loop.test_store import TestStore

            test_store = TestStore()
            iterations = test_store.get_iterations(result.run_id) if result.run_id else []
            output = format_test_result(result, iterations)
            print(output)
```

- [ ] **Step 2: Verify CLI help output**

Run: `python -m jobpulse.runner ralph-test`
Expected: Usage message showing both modes (single URL and --live)

- [ ] **Step 3: Verify --live flag parses without crashing (dry check)**

Run: `python -c "import sys; sys.argv = ['runner', 'ralph-test', '--live', '--count', '1']; print('parse OK')"`
Expected: `parse OK` (just verifies the arg parsing logic compiles)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/runner.py
git commit -m "feat(ralph): wire --live flag into ralph-test CLI"
```

---

### Task 4: Delete dead test files

**Files:**
- Delete: `tests/test_ralph_integration.py` (127 lines — 5 tests with dead URLs, all mock apply_job, verdict mapping already covered by test_runner)
- Delete: `tests/test_ralph_cli_output.py` (70 lines — 4 formatting tests, trivial value, format_live_summary tests replace them)

- [ ] **Step 1: Verify which tests exist in the files being deleted**

Run: `pytest tests/test_ralph_integration.py tests/test_ralph_cli_output.py --collect-only -q`
Expected: Lists ~9 test items

- [ ] **Step 2: Delete the files**

```bash
rm tests/test_ralph_integration.py tests/test_ralph_cli_output.py
```

- [ ] **Step 3: Verify remaining tests still pass**

Run: `pytest tests/test_ralph_loop.py tests/test_ralph_test_runner.py tests/test_ralph_test_store.py -v`
Expected: All PASS, no import errors

- [ ] **Step 4: Commit**

```bash
git add -u tests/test_ralph_integration.py tests/test_ralph_cli_output.py
git commit -m "chore(tests): delete dead ralph integration + CLI output tests (replaced by --live harness)"
```

---

### Task 5: Strip fake integration tests from test_ralph_loop.py

**Files:**
- Modify: `tests/test_ralph_loop.py:354-942` (delete `class TestRalphLoop` — 6 tests that mock `apply_job` with fake URLs)

The 38 tests above line 354 (PatternStore, error signatures, diagnoser, overrides builder) stay — they test real internal logic.

- [ ] **Step 1: Count what's being removed**

Run: `pytest tests/test_ralph_loop.py -v --collect-only -q | grep -c "TestRalphLoop"`
Expected: 6 tests in `TestRalphLoop` class

- [ ] **Step 2: Delete class TestRalphLoop (lines 354 to end of file)**

Remove everything from line 354 (`class TestRalphLoop:`) to end of file. Also remove the now-unused imports that only `TestRalphLoop` uses:

```python
# Remove these imports if they become unused after deletion:
from unittest.mock import patch, MagicMock  # keep patch, keep MagicMock if used elsewhere
```

Check which imports are still needed by the remaining 38 tests before removing any.

- [ ] **Step 3: Run remaining tests**

Run: `pytest tests/test_ralph_loop.py -v`
Expected: ~38 tests PASS (PatternStore, signatures, diagnoser, overrides)

- [ ] **Step 4: Commit**

```bash
git add tests/test_ralph_loop.py
git commit -m "chore(tests): strip fake ralph loop integration tests (mocked apply_job with dead URLs)"
```

---

### Task 6: Clean up test_ralph_test_runner.py — remove dead URLs

**Files:**
- Modify: `tests/test_ralph_test_runner.py` (replace hardcoded dead URLs with descriptive fixture URLs)

- [ ] **Step 1: Replace dead URLs**

The existing tests in `TestBasicTestRunner` use URLs like `https://linkedin.com/jobs/view/123`. These URLs are never fetched (ralph_apply_sync is mocked), but they should be descriptive, not pretending to be real:

Replace all hardcoded job URLs in existing test classes with clearly-fake fixture URLs:

```python
# Old:
url="https://linkedin.com/jobs/view/123"
# New:
url="https://example.com/jobs/test-success"

# Old:
url="https://linkedin.com/jobs/view/456"
# New:
url="https://example.com/jobs/test-exhausted"

# Old:
url="https://linkedin.com/jobs/view/789"
# New:
url="https://example.com/jobs/test-blocked"
```

This makes it clear these are test fixtures, not real links anyone should visit.

- [ ] **Step 2: Run all test_runner tests**

Run: `pytest tests/test_ralph_test_runner.py -v`
Expected: All PASS (old + new tests)

- [ ] **Step 3: Commit**

```bash
git add tests/test_ralph_test_runner.py
git commit -m "chore(tests): replace dead LinkedIn URLs with descriptive fixture URLs in test_runner"
```

---

### Task 7: Update CLAUDE.md with new CLI usage

**Files:**
- Modify: `CLAUDE.md` (update Quick Reference to include `--live` flag)

- [ ] **Step 1: Update the ralph-test line**

In the Quick Reference section, update:

```
python -m jobpulse.runner ralph-test   # Dry-run Ralph Loop self-healing test
```

To:

```
python -m jobpulse.runner ralph-test <url>   # Test single URL via Ralph Loop
python -m jobpulse.runner ralph-test --live  # Scrape fresh URLs + test via extension
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with ralph-test --live usage"
```

---

### Task 8: End-to-end smoke test (manual, requires extension)

**Prereqs:** Extension bridge running, Chrome extension loaded, `APPLICATION_ENGINE=extension`

- [ ] **Step 1: Start the bridge**

```bash
python -m jobpulse.runner ext-bridge
```

Verify: `[JobPulse] Connected to Python backend` appears in Chrome extension console

- [ ] **Step 2: Run live test with 1 job**

```bash
python -m jobpulse.runner ralph-test --live --platforms linkedin --count 1
```

Expected: Scrapes 1 LinkedIn URL, runs ralph_test_run, prints summary table with verdict

- [ ] **Step 3: Run live test across platforms**

```bash
python -m jobpulse.runner ralph-test --live --platforms linkedin,reed --count 2
```

Expected: 1 LinkedIn + 1 Reed URL tested, summary table shows both

- [ ] **Step 4: Verify TestStore has records**

```bash
python -c "
from jobpulse.ralph_loop.test_store import TestStore
store = TestStore()
runs = store.get_recent_runs(limit=5)
for r in runs:
    print(f'{r[\"platform\"]:10s} {r[\"final_verdict\"]:8s} {r[\"url\"][:50]}')
"
```

Expected: Recent live test runs visible in store

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v -k "ralph" --tb=short
```

Expected: All ralph tests pass (unit tests + new live test unit tests)
