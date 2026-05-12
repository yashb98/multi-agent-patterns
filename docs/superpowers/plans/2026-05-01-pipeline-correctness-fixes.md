# Pipeline Correctness Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five highest-impact correctness bugs uncovered in the 2026-05-01 pipeline audit — silently-dropped `dry_run`, broken `AgentRulesDB` consume loop, wrong-DB analytics, missing failure learning, and ghost-DB collision risk.

**Architecture:** No new modules. Five surgical fixes to existing code.
1. **`scan_pipeline.route_and_apply`** gains an explicit `dry_run` parameter, propagated from `process_single_url`. Eliminates the silent-drop bug at the routing seam.
2. **`agent_rules.AgentRulesDB`** gets a single domain normalization helper applied at both write and read time. The 7 orphan rules in production get backfilled to their normalized form via a one-shot migration.
3. **`job_analytics`** points at `applications.db` (where `applications` lives) instead of `jobpulse.db` (which only has Gmail). Adaptive Gate 3 thresholds will then actually adapt.
4. **`strategy_reflector`** fans out failures to `MemoryManager.record_episode()` so failure learning enters the 3-engine memory stack instead of silently dropping.
5. **Ghost DB cleanup** — six 0-byte files removed, `determine_match_tier` deduplicated to live in one place.

**Tech Stack:** Python 3.12, pytest + pytest-asyncio, SQLite (existing `data/applications.db`, `data/agent_rules.db`, `data/optimization.db`, `data/experience_memory.db`), no new dependencies.

---

## File Structure

**Modify:**
- `jobpulse/scan_pipeline.py` (lines 776–784, 815, 893–906, plus `run_scan_window` caller in `jobpulse/job_autopilot.py`) — add `dry_run` parameter to `route_and_apply`, propagate from `process_single_url` and the cron caller.
- `jobpulse/agent_rules.py` — add `_normalize_domain` helper, apply at write site (`auto_generate_from_correction:215,242`) and read site (`get_field_overrides:313`); one-shot migration of existing rows on `_init_db`.
- `jobpulse/job_analytics.py:17` — change `_DB_PATH` from `jobpulse.db` to `applications.db`.
- `jobpulse/strategy_reflector.py` — add `_record_failure_episode()` helper, call from `reflect_on_application` for the failure path that's currently silently dropped at line 309.
- `jobpulse/job_autopilot.py:54` — delete the duplicate `determine_match_tier`, import from `scan_pipeline`.

**Create:**
- `tests/jobpulse/test_dry_run_propagation.py` — verifies `dry_run` survives the routing seam.
- `tests/jobpulse/test_agent_rules_domain_match.py` — verifies write/read normalization round-trips.
- `tests/jobpulse/test_job_analytics_db_path.py` — verifies stats actually read application rows.
- `tests/jobpulse/test_strategy_reflector_failure_episode.py` — verifies failure path produces an episode.

**Delete:**
- `data/jobs.db`, `data/navigation_sequences.db`, `data/page_reasoner_cache.db`, `data/profile.db`, `data/gate_thresholds.db`, `data/project_selection_outcomes.db` — all 0-byte ghosts.

**Existing tests to preserve:** all of `tests/jobpulse/` (currently 46 passing on `nav-verification-hardening` for the touched test suite).

---

## Task 0: Establish baseline + branch

**Files:**
- No edits.

- [ ] **Step 1: Capture current state**

Run:
```bash
cd /Users/yashbishnoi/projects/multi_agent_patterns
python -m pytest tests/jobpulse/ -v -k "scan_pipeline or agent_rules or job_analytics or strategy_reflector or determine_match_tier or dry_run" 2>&1 | tail -10
```
Expected: record the count for regression comparison.

- [ ] **Step 2: Confirm starting branch and create feature branch**

Run:
```bash
git status --short | head -3
git rev-parse --abbrev-ref HEAD
git checkout -b pipeline-correctness-fixes
git commit --allow-empty -m "chore: start pipeline correctness fixes"
```

- [ ] **Step 3: Snapshot the production data state for the broken DBs (audit baseline)**

Run:
```bash
echo "=== agent_rules baseline ===" && sqlite3 data/agent_rules.db "SELECT rule_id, source, category, pattern, times_applied FROM agent_rules WHERE source='correction_capture';"
echo "=== applications baseline ===" && sqlite3 data/applications.db "SELECT COUNT(*) FROM applications;"
echo "=== ghost DB baseline ===" && for db in jobs navigation_sequences page_reasoner_cache profile gate_thresholds project_selection_outcomes; do echo "data/$db.db: $(stat -f%z data/$db.db 2>/dev/null || stat -c%s data/$db.db 2>/dev/null) bytes"; done
```
Save the output as a comment in the marker commit message — this is the "before" snapshot for measuring whether Task 2 actually closes the loop.

---

## Task 1 (P0): Plumb `dry_run` through `route_and_apply`

**Why:** Today `process_single_url(dry_run=True)` calls `route_and_apply(listing, bundle, db, review_batch, remaining_cap, auto_applied)` (no dry_run param). The function then calls `apply_job(...)` without `dry_run`, which defaults to `False`. Result: `python -m jobpulse.runner job-process-url <URL>` advertised as dry-run actually submits real applications.

**Files:**
- Modify: `jobpulse/scan_pipeline.py:776-784, 815, 893-906`
- Modify: `jobpulse/job_autopilot.py` — find any call to `route_and_apply` and add explicit `dry_run` argument.
- Test: `tests/jobpulse/test_dry_run_propagation.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_dry_run_propagation.py`:
```python
"""dry_run must survive the route_and_apply seam."""
from unittest.mock import patch, MagicMock
import pytest
from jobpulse.scan_pipeline import route_and_apply


def _make_listing():
    listing = MagicMock()
    listing.url = "https://example.com/job/1"
    listing.job_id = "job-1234"
    listing.title = "Software Engineer"
    listing.company = "Example Co"
    listing.ats_platform = "greenhouse"
    listing.easy_apply = True
    return listing


def _make_bundle():
    from pathlib import Path
    bundle = MagicMock()
    bundle.ats_score = 96.0
    bundle.cv_path = Path("/tmp/cv.pdf")
    bundle.cover_letter_path = Path("/tmp/cl.pdf")
    bundle.notion_page_id = "page-1"
    bundle.matched_project_names = []
    return bundle


class TestDryRunPropagation:
    def test_route_and_apply_passes_dry_run_to_apply_job(self):
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": False}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
                dry_run=True,
            )
        assert mock_apply.called
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is True, (
            f"apply_job was called without dry_run=True. kwargs were: {call_kwargs}"
        )

    def test_route_and_apply_dry_run_default_is_true(self):
        """Safer-by-default: callers that forget to pass dry_run should NOT submit."""
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": False}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
            )
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is True

    def test_route_and_apply_explicit_false_is_passed_through(self):
        """The cron auto-submit path explicitly opts out."""
        with patch("jobpulse.scan_pipeline.apply_job") as mock_apply:
            mock_apply.return_value = {"success": True, "submitted": True}
            route_and_apply(
                listing=_make_listing(),
                bundle=_make_bundle(),
                db=MagicMock(),
                review_batch=[],
                remaining_cap=10,
                auto_applied=0,
                dry_run=False,
            )
        call_kwargs = mock_apply.call_args.kwargs
        assert call_kwargs.get("dry_run") is False
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_dry_run_propagation.py -v
```
Expected: `TypeError: route_and_apply() got an unexpected keyword argument 'dry_run'`.

- [ ] **Step 3: Add `dry_run` parameter to `route_and_apply`**

In `jobpulse/scan_pipeline.py` modify the signature at line 776 from:
```python
def route_and_apply(
    listing: Any,
    bundle: MaterialsBundle,
    db: Any,
    review_batch: list[dict],
    remaining_cap: int,
    auto_applied: int,
) -> RouteResult:
```
to:
```python
def route_and_apply(
    listing: Any,
    bundle: MaterialsBundle,
    db: Any,
    review_batch: list[dict],
    remaining_cap: int,
    auto_applied: int,
    *,
    dry_run: bool = True,
) -> RouteResult:
```
Note the `*,` makes `dry_run` keyword-only. Default `True` is safer-by-default — explicit opt-in to submit.

- [ ] **Step 4: Pass `dry_run` to `apply_job` inside `route_and_apply`**

At line 815 inside `route_and_apply`, the existing call:
```python
            result = apply_job(
                url=listing.url,
                ats_platform=listing.ats_platform,
                cv_path=bundle.cv_path,
                cover_letter_path=bundle.cover_letter_path,
                cl_generator=None,
                custom_answers={
                    "_job_context": _build_screening_context(listing),
                },
                job_context={
                    ...
                },
            )
```
Add `dry_run=dry_run,` as the last keyword argument before the closing paren:
```python
            result = apply_job(
                url=listing.url,
                ats_platform=listing.ats_platform,
                cv_path=bundle.cv_path,
                cover_letter_path=bundle.cover_letter_path,
                cl_generator=None,
                custom_answers={
                    "_job_context": _build_screening_context(listing),
                },
                job_context={
                    "job_id": listing.job_id,
                    "company": listing.company,
                    "title": listing.title,
                    "notion_page_id": notion_page_id,
                    "cv_path": str(bundle.cv_path),
                    "cover_letter_path": str(bundle.cover_letter_path) if bundle.cover_letter_path else None,
                    "match_tier": tier,
                    "ats_score": ats_score,
                    "matched_projects": bundle.matched_project_names,
                },
                dry_run=dry_run,
            )
```

- [ ] **Step 5: Propagate from `process_single_url`**

In the same file, find `process_single_url` (line 893). Locate where it calls `route_and_apply(...)`. Pass its own `dry_run` parameter through:
```python
    return route_and_apply(
        listing=listing,
        bundle=bundle,
        db=db,
        review_batch=review_batch,
        remaining_cap=remaining_cap,
        auto_applied=auto_applied,
        dry_run=dry_run,
    )
```
(If the existing call signature is positional, convert to keyword form to avoid argument-order mistakes.)

- [ ] **Step 6: Update the cron caller in `job_autopilot.py`**

Run:
```bash
grep -n "route_and_apply" jobpulse/job_autopilot.py
```
For each call site found, add an explicit `dry_run=` argument. The cron auto-submit caller (`run_scan_window`) should pass `dry_run=False` because that's the auto-submit semantics today; making it explicit eliminates the silent default. Document the choice with a one-line comment:
```python
        result = route_and_apply(
            listing=listing,
            bundle=bundle,
            db=db,
            review_batch=review_batch,
            remaining_cap=remaining_cap,
            auto_applied=auto_applied,
            dry_run=False,  # cron auto-submit — see followups for proper dry-run-first refactor
        )
```

- [ ] **Step 7: Run the new tests**

```bash
python -m pytest tests/jobpulse/test_dry_run_propagation.py -v
```
Expected: 3 tests pass.

- [ ] **Step 8: Run the full jobpulse suite to confirm no regressions**

```bash
python -m pytest tests/jobpulse/ 2>&1 | tail -5
```
Expected: same pass count as Task 0 baseline plus the 3 new tests, no new failures.

- [ ] **Step 9: Commit**

```bash
git add jobpulse/scan_pipeline.py jobpulse/job_autopilot.py tests/jobpulse/test_dry_run_propagation.py
git commit -m "fix(scan): plumb dry_run through route_and_apply

process_single_url(dry_run=True) was being silently dropped at the
route_and_apply seam, causing job-process-url to submit live applications
despite advertising dry-run mode.

route_and_apply now takes dry_run as a keyword-only argument with a
safer-by-default value of True. process_single_url propagates its own
dry_run; the cron auto-submit caller in job_autopilot opts out
explicitly with dry_run=False."
```

---

## Task 2 (P0): Fix AgentRulesDB domain normalization

**Why:** 7 rules exist in `agent_rules.db.agent_rules`, all with `source=correction_capture`. `times_applied=0` on every single one. The write site (`auto_generate_from_correction:215,242`) stores `pattern=domain` from the correction, the read site (`get_field_overrides:313`) filters on `r["pattern"] != domain`, but the two domain strings differ in casing/`www.`/path. A single normalization helper applied at both sides closes the loop.

**Files:**
- Modify: `jobpulse/agent_rules.py` — add helper, apply at write+read, migrate existing rows.
- Test: `tests/jobpulse/test_agent_rules_domain_match.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_agent_rules_domain_match.py`:
```python
"""Domain normalization must round-trip between write and read."""
import pytest
from jobpulse.agent_rules import AgentRulesDB, _normalize_domain


class TestNormalizeDomain:
    def test_strips_www(self):
        assert _normalize_domain("www.example.com") == "example.com"

    def test_lowercases(self):
        assert _normalize_domain("WWW.EXAMPLE.COM") == "example.com"

    def test_strips_scheme(self):
        assert _normalize_domain("https://example.com") == "example.com"
        assert _normalize_domain("http://www.example.com") == "example.com"

    def test_strips_path(self):
        assert _normalize_domain("https://example.com/jobs/123") == "example.com"

    def test_handles_empty(self):
        assert _normalize_domain("") == ""
        assert _normalize_domain(None) == ""

    def test_idempotent(self):
        once = _normalize_domain("https://www.Example.com/path")
        twice = _normalize_domain(once)
        assert once == twice == "example.com"


class TestRoundTrip:
    def test_write_then_read_matches(self, tmp_path):
        db = AgentRulesDB(db_path=str(tmp_path / "ar.db"))
        # Write with one canonical-looking form
        db.auto_generate_from_correction(
            field_label="Email",
            agent_value="old@x.com",
            user_value="new@x.com",
            domain="https://www.Greenhouse.io/job/1",
            platform="greenhouse",
        )
        # Read with a different canonical-looking form for the same domain
        overrides = db.get_field_overrides(domain="greenhouse.io")
        assert "Email" in overrides
        assert overrides["Email"]["value"] == "new@x.com"

    def test_times_applied_increments_on_read(self, tmp_path):
        db = AgentRulesDB(db_path=str(tmp_path / "ar.db"))
        db.auto_generate_from_correction(
            field_label="Phone",
            agent_value="",
            user_value="555-1234",
            domain="example.com",
            platform="generic",
        )
        # First read
        first = db.get_field_overrides(domain="EXAMPLE.com")
        assert "Phone" in first
        # Second read
        second = db.get_field_overrides(domain="https://example.com/path")
        assert "Phone" in second
        # Inspect times_applied — should be 2 after two reads
        import sqlite3
        with sqlite3.connect(db._db_path) as conn:
            row = conn.execute(
                "SELECT times_applied FROM agent_rules WHERE category = ?",
                ("Phone",),
            ).fetchone()
        assert row[0] == 2
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_agent_rules_domain_match.py -v
```
Expected: ImportError on `_normalize_domain` and AssertionError on round-trip.

- [ ] **Step 3: Add `_normalize_domain` helper at module level**

In `jobpulse/agent_rules.py`, after the existing imports (around line 14), add:
```python
def _normalize_domain(value: str | None) -> str:
    """Canonicalize a domain string for AgentRulesDB pattern matching.

    Accepts: bare host, host+path, full URL, with or without scheme,
    with or without `www.`, mixed case. Returns lowercase host without
    leading `www.`. Empty input returns empty string.
    """
    if not value:
        return ""
    from urllib.parse import urlparse
    s = value.strip().lower()
    if "://" in s:
        s = urlparse(s).netloc
    else:
        # Drop any path portion for bare host[+path] inputs
        s = s.split("/", 1)[0]
    if s.startswith("www."):
        s = s[4:]
    return s
```

- [ ] **Step 4: Apply normalization at the write site**

In `auto_generate_from_correction` (line 195), normalize the incoming domain. Find:
```python
    def auto_generate_from_correction(
        self,
        field_label: str,
        agent_value: str,
        user_value: str,
        domain: str,
        platform: str,
    ) -> dict:
        """Generate a correction-based override or escalation rule.

        Returns:
            Dict with rule_id, field_label, action.
        """
        now = datetime.now(UTC).isoformat()
        expires = (datetime.now(UTC) + timedelta(days=_RULE_TTL_DAYS)).isoformat()
```
Insert the normalization right after the docstring/decorator block, before `now = ...`:
```python
        domain = _normalize_domain(domain)
```

- [ ] **Step 5: Apply normalization at the read site**

In `get_field_overrides` (line 302), normalize the incoming domain:
```python
    def get_field_overrides(self, domain: str = "", platform: str = "") -> dict[str, dict]:
        """..."""
        domain = _normalize_domain(domain)
        rules = self.get_active_rules("correction_override")
```

- [ ] **Step 6: Migrate existing rows on `_init_db`**

The 7 existing production rules were written without normalization. Add a migration that runs once and updates them in place. In `AgentRulesDB._init_db()` (line 28), after the existing schema-creation and legacy-migration blocks, add a one-shot pattern normalization migration:
```python
            # 2026-05 migration — normalize all stored patterns for correction_override rules
            try:
                rows = conn.execute(
                    "SELECT rule_id, pattern FROM agent_rules WHERE source='correction_capture'"
                ).fetchall()
                for row in rows:
                    rule_id, raw = row[0], row[1]
                    normalized = _normalize_domain(raw)
                    if normalized != raw:
                        conn.execute(
                            "UPDATE agent_rules SET pattern = ? WHERE rule_id = ?",
                            (normalized, rule_id),
                        )
                        logger.info(
                            "agent_rules: normalized pattern rule_id=%d %r → %r",
                            rule_id, raw, normalized,
                        )
            except Exception as exc:
                logger.warning("agent_rules: pattern normalization migration failed: %s", exc)
```

- [ ] **Step 7: Run all related tests**

```bash
python -m pytest tests/jobpulse/test_agent_rules_domain_match.py tests/jobpulse/ -k "agent_rules" -v 2>&1 | tail -15
```
Expected: 8 new tests pass plus any existing agent_rules tests still pass.

- [ ] **Step 8: Verify the migration touches production data**

Run a one-off check after the test suite passes:
```bash
python -c "
from jobpulse.agent_rules import AgentRulesDB
db = AgentRulesDB()  # touches data/agent_rules.db, runs migration on init
import sqlite3
with sqlite3.connect(db._db_path) as conn:
    rows = conn.execute(
        \"SELECT rule_id, category, pattern FROM agent_rules WHERE source='correction_capture'\"
    ).fetchall()
    for r in rows:
        print(r)
"
```
Expected: previously-mismatched patterns now show their normalized form. Compare against the Task 0 Step 3 baseline snapshot.

- [ ] **Step 9: Commit**

```bash
git add jobpulse/agent_rules.py tests/jobpulse/test_agent_rules_domain_match.py
git commit -m "fix(agent_rules): normalize domain at write and read

The most-claimed self-improvement loop in JobPulse had 7 rules written
with times_applied=0 because the write site stored raw correction.domain
values (e.g. 'https://www.Example.com/path') while the read site filtered
against _page_domain (e.g. 'example.com'), causing zero matches.

Added _normalize_domain helper applied at both auto_generate_from_correction
and get_field_overrides. _init_db includes a one-shot migration that
normalizes existing rule patterns in place so the 7 orphan rules become
matchable."
```

---

## Task 3 (P1): Fix `job_analytics` DB path

**Why:** `_DB_PATH = str(DATA_DIR / "jobpulse.db")` at `jobpulse/job_analytics.py:17`. The `applications` table lives in `data/applications.db`, not `data/jobpulse.db` (which only has Gmail tables). `get_conversion_funnel()` and `get_gate_stats()` always return zeros. Adaptive Gate 3 thresholds in `_get_adaptive_thresholds()` consume this and are permanently stuck at baseline 75/55. Adaptive gating doesn't actually adapt.

**Files:**
- Modify: `jobpulse/job_analytics.py:17`
- Test: `tests/jobpulse/test_job_analytics_db_path.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_job_analytics_db_path.py`:
```python
"""job_analytics must read from applications.db where the applications table actually lives."""
import sqlite3
import pytest
from jobpulse.job_analytics import _DB_PATH, get_conversion_funnel


def test_db_path_points_at_applications_db():
    assert _DB_PATH.endswith("applications.db"), (
        f"job_analytics._DB_PATH should target applications.db (where the applications "
        f"table lives), not {_DB_PATH}"
    )


def test_conversion_funnel_returns_nonzero_for_real_db(tmp_path):
    """With a populated applications table, the funnel should not be all zeros."""
    db_path = tmp_path / "applications.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE applications (
                job_id TEXT PRIMARY KEY,
                status TEXT,
                ats_score REAL,
                applied_at TEXT
            )
        """)
        from datetime import datetime, UTC
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
        conn.executemany(
            "INSERT INTO applications (job_id, status, ats_score, applied_at) VALUES (?, ?, ?, ?)",
            [
                ("j1", "Applied", 90.0, ts),
                ("j2", "Applied", 85.0, ts),
                ("j3", "Rejected", 60.0, ts),
                ("j4", "Skipped", 50.0, ts),
            ],
        )
    funnel = get_conversion_funnel(days=30, db_path=str(db_path))
    # We just want to confirm the function CAN read non-zero data when pointed
    # at a real applications table. Any non-zero count proves the path resolves.
    total = sum(v for v in funnel.values() if isinstance(v, (int, float)))
    assert total > 0, f"funnel returned all zeros even with 4 rows: {funnel}"
```

- [ ] **Step 2: Run, expect failure**

```bash
python -m pytest tests/jobpulse/test_job_analytics_db_path.py -v
```
Expected: `test_db_path_points_at_applications_db` FAILS with `_DB_PATH` ending in `jobpulse.db`.

- [ ] **Step 3: Update `_DB_PATH`**

In `jobpulse/job_analytics.py` line 17, change:
```python
_DB_PATH = str(DATA_DIR / "jobpulse.db")
```
to:
```python
_DB_PATH = str(DATA_DIR / "applications.db")
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/jobpulse/test_job_analytics_db_path.py -v
```
Expected: 2 tests pass.

- [ ] **Step 5: Smoke-check the production path**

```bash
python -c "
from jobpulse.job_analytics import get_conversion_funnel, get_gate_stats
print('funnel:', get_conversion_funnel(days=30))
print('gate_stats:', get_gate_stats(days=30))
"
```
Expected: now returns non-zero counts (652 applications in production per audit baseline). If it crashes with a missing-column or missing-table error, the funnel/stats SQL is referencing schema that exists in `applications.db` but the column names need confirming — read the `applications` schema with `sqlite3 data/applications.db ".schema applications"` and compare to what the SQL expects.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/job_analytics.py tests/jobpulse/test_job_analytics_db_path.py
git commit -m "fix(analytics): point job_analytics at applications.db

_DB_PATH was data/jobpulse.db (Gmail-only). The applications table
lives in data/applications.db. Result: get_conversion_funnel and
get_gate_stats always returned zeros, freezing adaptive Gate 3
thresholds at baseline 75/55 since the day they were written."
```

---

## Task 4 (P1): Wire failure learning into ExperienceMemory via `record_episode`

**Why:** `strategy_reflector._feed_experience_memory` at line 309 short-circuits on `not strategy.success` — failed applications produce zero entries in ExperienceMemory. 278 fields of failure data sit in TrajectoryStore with no path into the prompt-injected learning store. `MemoryManager.record_episode()` exists for episodic learning (it accepts strengths AND weaknesses), but `strategy_reflector` never calls it for failures.

**Files:**
- Modify: `jobpulse/strategy_reflector.py` — add `_record_failure_episode()` helper; call from `reflect_on_application` for the failure path.
- Test: `tests/jobpulse/test_strategy_reflector_failure_episode.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/jobpulse/test_strategy_reflector_failure_episode.py`:
```python
"""Failed applications must record an episode so failure learning enters the memory stack."""
from unittest.mock import patch, MagicMock
import pytest
from jobpulse.strategy_reflector import _record_failure_episode


def _make_failed_strategy():
    strategy = MagicMock()
    strategy.success = False
    strategy.domain = "greenhouse.io"
    strategy.platform = "greenhouse"
    strategy.fields_total = 12
    strategy.fields_pattern = 3
    strategy.fields_llm = 6
    strategy.fields_corrected = 8
    strategy.failure_reason = "captcha_blocked_after_3_attempts"
    return strategy


class TestFailureEpisodeRecording:
    def test_failure_calls_record_episode(self):
        captured = {}
        fake_mm = MagicMock()
        def capture_record_episode(**kwargs):
            captured.update(kwargs)
        fake_mm.record_episode = MagicMock(side_effect=capture_record_episode)

        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            _record_failure_episode(_make_failed_strategy(), [
                {"trigger": "captcha", "action": "wait_human", "confidence": 0.6},
            ])

        assert fake_mm.record_episode.called
        assert captured["domain"] == "job_application"
        assert captured["final_score"] < 5.0  # failures must score below mid
        assert "greenhouse" in captured["topic"].lower() or "greenhouse" in str(captured.get("output_summary", "")).lower()

    def test_success_does_not_call_record_episode(self):
        fake_mm = MagicMock()
        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            strategy = _make_failed_strategy()
            strategy.success = True
            _record_failure_episode(strategy, [])
        assert not fake_mm.record_episode.called

    def test_failure_with_no_heuristics_still_records(self):
        """Even without heuristics, a failure should produce an episode so we learn from it."""
        fake_mm = MagicMock()
        with patch("jobpulse.strategy_reflector.get_memory_manager", return_value=fake_mm):
            _record_failure_episode(_make_failed_strategy(), [])
        assert fake_mm.record_episode.called
```

- [ ] **Step 2: Run, expect ImportError**

```bash
python -m pytest tests/jobpulse/test_strategy_reflector_failure_episode.py -v
```
Expected: ImportError on `_record_failure_episode`.

- [ ] **Step 3: Add `_record_failure_episode` and a memory-manager accessor**

In `jobpulse/strategy_reflector.py`, near the other lazy-import helpers at the top of the file, add:
```python
def get_memory_manager():
    """Lazy accessor — patchable in tests via jobpulse.strategy_reflector.get_memory_manager."""
    from shared.memory_layer import MemoryManager
    return MemoryManager()
```

Then, after the existing `_feed_experience_memory` function (which ends around line 360), add the new function:
```python
def _record_failure_episode(
    strategy: "ApplicationStrategy",
    heuristics: list[dict],
) -> None:
    """Record failure as an episode so the memory stack learns from what didn't work.

    Successful runs go through _feed_experience_memory + ExperienceMemory.
    Failures are higher signal but were previously dropped. This routes them
    through MemoryManager.record_episode where the 3-engine memory stack
    captures the weaknesses for future avoidance.
    """
    if strategy.success:
        return

    try:
        mm = get_memory_manager()
        score = _compute_strategy_score(strategy)  # already returns 2.0 for failures
        weaknesses = []
        if hasattr(strategy, "failure_reason") and strategy.failure_reason:
            weaknesses.append(str(strategy.failure_reason))
        if strategy.fields_total > 0 and strategy.fields_corrected > 0:
            corr_pct = strategy.fields_corrected / strategy.fields_total * 100
            weaknesses.append(f"required {corr_pct:.0f}% corrections")

        strengths = [f"{h['trigger']} → {h['action']}" for h in heuristics[:5]]

        summary = (
            f"FAILED job_application on {strategy.domain} "
            f"({strategy.platform}): "
            f"{strategy.fields_total} fields, {strategy.fields_corrected} corrected. "
            + (strategy.failure_reason or "no specific reason recorded")
        )

        mm.record_episode(
            topic=f"job_application_failure:{strategy.domain}:{strategy.platform}",
            final_score=score,
            iterations=1,
            pattern_used="form_fill",
            agents_used=["NativeFormFiller"],
            strengths=strengths,
            weaknesses=weaknesses,
            output_summary=summary,
            domain="job_application",
        )
        logger.info(
            "strategy_reflector: recorded failure episode for %s (score=%.1f)",
            strategy.domain, score,
        )
    except Exception as exc:
        logger.debug("strategy_reflector: failure episode record failed: %s", exc)
```

- [ ] **Step 4: Call the new function from `reflect_on_application`**

Find the existing `_feed_experience_memory(strategy, all_heuristics)` call inside `reflect_on_application` (around line 299). Add the failure-path call right after it:
```python
    _feed_experience_memory(strategy, all_heuristics)
    _record_failure_episode(strategy, all_heuristics)
    return strategy
```
The two are mutually exclusive — `_feed_experience_memory` short-circuits on success-only, `_record_failure_episode` short-circuits on failure-only. Together they cover the full strategy outcome space.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/jobpulse/test_strategy_reflector_failure_episode.py tests/jobpulse/ -k "strategy_reflector" -v 2>&1 | tail -10
```
Expected: 3 new tests pass plus any pre-existing strategy_reflector tests still pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/strategy_reflector.py tests/jobpulse/test_strategy_reflector_failure_episode.py
git commit -m "feat(reflector): wire failure learning into MemoryManager.record_episode

ExperienceMemory only stores successful strategies (success=True gate at
line 309). Failed applications — the highest-signal events for learning —
were silently dropped at this seam.

Add _record_failure_episode that calls MemoryManager.record_episode with
the failure reason, correction ratio, and any extracted heuristics. Routes
failures into the 3-engine memory stack (SQLite + Qdrant + Neo4j) so the
forgetting sweep + lifecycle promotion can preserve the most informative
ones."
```

---

## Task 5 (P2): Cleanup — ghost DBs and `determine_match_tier` duplication

**Why:** Six 0-byte ghost DBs in `data/` shadow real ones with similar names (`page_reasoner_cache.db` shadows `page_reasoning_cache.db`, `navigation_sequences.db` shadows `navigation_learning.db`). One filename typo away from a silent bug. `determine_match_tier` is defined twice (`job_autopilot.py:54` and `scan_pipeline.py:69`) — same logic, latent divergence.

**Files:**
- Delete: 6 zero-byte DB files in `data/`
- Modify: `jobpulse/job_autopilot.py:54` — delete the duplicate, import from `scan_pipeline`.
- Test: existing tests should keep passing.

- [ ] **Step 1: Confirm all six target files are 0 bytes**

```bash
for db in data/jobs.db data/navigation_sequences.db data/page_reasoner_cache.db data/profile.db data/gate_thresholds.db data/project_selection_outcomes.db; do
    if [ -e "$db" ]; then
        size=$(stat -f%z "$db" 2>/dev/null || stat -c%s "$db" 2>/dev/null)
        if [ "$size" != "0" ]; then
            echo "ABORT: $db is $size bytes, not 0. Investigate before deleting."
            exit 1
        fi
        echo "OK to delete: $db (0 bytes)"
    fi
done
```
If any file is non-zero, STOP and report BLOCKED. Investigate which code wrote it before proceeding.

- [ ] **Step 2: Confirm no Python file references the ghost paths**

```bash
for db in jobs navigation_sequences page_reasoner_cache profile gate_thresholds project_selection_outcomes; do
    echo "=== $db.db references ==="
    grep -rn "${db}\.db" jobpulse/ shared/ --include="*.py" | grep -v "test_" | head -5
done
```
Expected: no matches in production code (test files referencing similarly-named DBs are fine — they use `tmp_path`). If any production code references one of these paths, STOP — that path needs migration before deletion.

- [ ] **Step 3: Delete the 6 ghost DBs**

```bash
rm data/jobs.db data/navigation_sequences.db data/page_reasoner_cache.db data/profile.db data/gate_thresholds.db data/project_selection_outcomes.db
ls data/*.db | wc -l
```
Record the new count. The previous count was 55; expected new count: 49.

- [ ] **Step 4: De-duplicate `determine_match_tier`**

Read both definitions:
```bash
sed -n '54,70p' jobpulse/job_autopilot.py
sed -n '69,85p' jobpulse/scan_pipeline.py
```
Verify they are byte-for-byte identical (or only differ in comments/whitespace). If they differ in logic, STOP — the divergence may be intentional and needs human resolution.

If identical, delete the `job_autopilot.py:54` copy and replace with an import. In `jobpulse/job_autopilot.py`, find the existing `def determine_match_tier(ats_score: float) -> str:` block (line 54) and delete the entire function body. Add an import at the top of the file alongside the existing imports:
```python
from jobpulse.scan_pipeline import determine_match_tier
```
(Only add this import if `determine_match_tier` is actually used inside `job_autopilot.py` — check with `grep -n "determine_match_tier" jobpulse/job_autopilot.py` after deletion.)

- [ ] **Step 5: Run the full jobpulse test suite**

```bash
python -m pytest tests/jobpulse/ 2>&1 | tail -5
```
Expected: same passing count as before — deletion of dead resources should not break any test. If anything fails, STOP and investigate; one of the "ghost" DBs may have had an actual reference.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove 6 ghost DBs + dedupe determine_match_tier

Six 0-byte DBs in data/ shadowed real ones with similar names
(page_reasoner_cache.db vs page_reasoning_cache.db,
navigation_sequences.db vs navigation_learning.db). Deleted to
remove collision risk.

determine_match_tier was defined byte-for-byte identically in
job_autopilot.py:54 and scan_pipeline.py:69. Removed the
job_autopilot copy and imported from scan_pipeline."
```

---

## Task 6: Final verification + followups doc

- [ ] **Step 1: Run the full jobpulse test suite**

```bash
python -m pytest tests/jobpulse/ 2>&1 | tail -10
```
Expected: all green, count = baseline (Task 0) + 13 new tests (3 dry-run + 8 agent-rules + 2 analytics + 3 strategy-reflector failure).

- [ ] **Step 2: Verify each fix in production data**

Run, and capture the output as the "after" snapshot for the followups doc:
```bash
echo "=== Task 1: dry_run plumbing ==="
python -c "import inspect; from jobpulse.scan_pipeline import route_and_apply; print(inspect.signature(route_and_apply))"

echo "=== Task 2: agent_rules normalization ==="
python -c "
from jobpulse.agent_rules import AgentRulesDB, _normalize_domain
print('helper:', _normalize_domain('https://www.GREENHOUSE.io/path'))
db = AgentRulesDB()
import sqlite3
with sqlite3.connect(db._db_path) as conn:
    rows = conn.execute(\"SELECT rule_id, category, pattern, times_applied FROM agent_rules WHERE source='correction_capture'\").fetchall()
    for r in rows: print(r)
"

echo "=== Task 3: analytics DB path ==="
python -c "from jobpulse.job_analytics import _DB_PATH; print('_DB_PATH:', _DB_PATH)"

echo "=== Task 5: ghost DB count ==="
ls data/*.db | wc -l
```

- [ ] **Step 3: Update the existing followups doc**

Append to `docs/superpowers/plans/2026-05-01-navigator-verification-hardening-followups.md`:
```markdown

## 2026-05-01 Pipeline correctness fixes — applied

The follow-up plan `2026-05-01-pipeline-correctness-fixes.md` shipped 5 fixes:

1. ✅ `route_and_apply(dry_run=...)` — `process_single_url(dry_run=True)` no longer silently drops at the routing seam.
2. ✅ `agent_rules` domain normalization — write and read paths share `_normalize_domain`. The 7 orphan production rules were migrated in place.
3. ✅ `job_analytics._DB_PATH` — points at `applications.db` instead of `jobpulse.db`.
4. ✅ `strategy_reflector._record_failure_episode` — failure learning enters the memory stack via `MemoryManager.record_episode`.
5. ✅ 6 ghost DBs deleted; `determine_match_tier` deduplicated.

## Remaining followups (not in scope of pipeline-correctness-fixes plan)

- **Cron auto-submit dry-run-first refactor.** `run_scan_window` now passes `dry_run=False` explicitly. The proper fix is to make cron also call `apply_job(dry_run=True)` first, then call `confirm_application()` programmatically once a quality gate passes. This requires careful sequencing with the rate limiter and Notion update.
- **Pre-submit gate revival.** `PreSubmitGate` only fires when `company_research is not None` (`__init__.py:235`), and `live_review_applicator` never passes it. Either remove the dead gate or wire it.
- **Mutex coverage of fill/submit.** `_apply_lock` only protects quota recording, not the actual fill/submit. Concurrent `apply_job()` calls can interleave their browser sessions.
- **Two parallel verification systems.** `NativeFormFiller` has its own per-field verification; the new `ExecutorResult` lives only on the navigator path. Unify so corrections from one path teach the other's DBs.
- **`gate_effectiveness` table.** Schema exists; never receives a row. Trace why the writer is unreachable.
- **`draft_applicator.py` (~900 lines)** — fully dead code. Either delete or wire.
- **`GateThresholdAdapter`** — fully implemented, never instantiated. Delete or wire.
- **`ai_assist_logger.ai_fixes_count` always 0** — verify whether external AI fixes are actually being logged.
```

- [ ] **Step 4: Commit the followups update**

```bash
git add docs/superpowers/plans/2026-05-01-navigator-verification-hardening-followups.md
git commit -m "docs: record pipeline correctness fixes + remaining followups"
```

- [ ] **Step 5: Branch summary**

Run:
```bash
git log main..HEAD --oneline
```
Expected: ~7-8 commits covering Tasks 0-6.

---

## Self-Review

**Spec coverage:**
- ✅ Audit Priority 1 (cron dry-run gap) — Task 1 plumbs `dry_run`. Note: full cron dry-run-first refactor is deferred to followups; this task closes the silent-drop bug at minimum.
- ✅ Audit Priority 2 (AgentRulesDB domain mismatch) — Task 2 normalizes + migrates.
- ✅ Audit Priority 3 (`get_gate_stats` wrong DB) — Task 3 fixes `_DB_PATH`.
- ✅ Audit Priority 4 (failure learning) — Task 4 wires `record_episode`.
- ✅ Audit Priority 5 (ghost DBs + duplicate function) — Task 5.

**Placeholder scan:** No "TBD"/"TODO"/"implement later" — every step has concrete code blocks or commands.

**Type/name consistency:**
- `_normalize_domain` defined in Task 2 Step 3, used Tasks 2/4/5/6 — consistent.
- `route_and_apply(dry_run=...)` keyword-only in Task 1 Step 3, called consistently in Steps 4–6.
- `_record_failure_episode(strategy, heuristics)` defined Task 4 Step 3, called Step 4 with the same signature.
- `get_memory_manager()` defined and called in Task 4 — consistent.

**Verification path** (Task 6 Step 2) checks every new symbol resolves at runtime, not just at import time.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-01-pipeline-correctness-fixes.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
