# Navigation Self-Learning System — Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the learning gap in Brain 1 (navigation layer). Brain 2 (form filling) already has cross-domain learning via TrajectoryStore 3-tier lookup, StrategyReflector, CorrectionCapture, and screening answer cache. Brain 1 (FormNavigator, PageAnalyzer, CookieBannerDismisser) is still domain-siloed — fix that + add safety nets.

**Architecture:** The key insight: `form_experience.db` already stores a `platform` column on every record but nobody aggregates across domains. One SQL `GROUP BY platform` query gives PageAnalyzer and FormNavigator everything they need for new domains on known platforms. The rest is safety nets (redirect loops, session expiry, timeouts) that prevent crashes.

**What already works (DO NOT duplicate):**
- `screening_answers.py` — cached by question text, fully cross-domain
- `trajectory_store.py` — 3-tier: domain → platform → GRPO cross-domain
- `strategy_reflector.py` — extracts heuristics, feeds ExperienceMemory
- `correction_capture.py` — by field label, cross-domain
- `scan_learning.py` — per-platform adaptive params
- `NativeFormFiller` — fast path for known domains, skips LLM

**Tech Stack:** Python 3.12, asyncio, SQLite, Playwright, pytest

---

### Task 1: Platform-Level Aggregate in FormExperienceDB

**Files:**
- Modify: `jobpulse/form_experience_db.py:22-226`
- Test: `tests/jobpulse/test_form_prefetch.py`

**Why:** This is THE missing link. `form_experience.db` has 1500 rows with a `platform` column, but no method aggregates across domains. PageAnalyzer needs "how many fields does Greenhouse usually have?" and FormNavigator needs "what's the typical nav pattern for Lever?" — both answerable with one GROUP BY query. This single addition makes Brain 1 learn from every past application, not just revisited domains.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_form_prefetch.py — append

def test_platform_aggregate_returns_stats(tmp_path):
    """Platform aggregate computes avg fields, pages, timing across domains."""
    from jobpulse.form_experience_db import FormExperienceDB
    import json

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))

    # Simulate 3 different Greenhouse domains
    for domain, fields, pages, time_s in [
        ("acme.com", ["text", "email", "tel", "file"], 2, 30.0),
        ("beta.com", ["text", "text", "email", "tel", "file", "select"], 2, 45.0),
        ("gamma.com", ["text", "email", "file", "select", "textarea"], 1, 25.0),
    ]:
        db.record(domain, "greenhouse", "playwright", pages, fields,
                  ["visa?", "salary?"], time_s, success=True)

    agg = db.get_platform_aggregate("greenhouse")
    assert agg is not None
    assert agg["observation_count"] == 3
    assert 1.0 <= agg["avg_pages"] <= 2.0
    assert 4.0 <= agg["avg_field_count"] <= 6.0
    assert 25.0 <= agg["avg_time_seconds"] <= 45.0
    assert "text" in agg["common_field_types"]
    assert "email" in agg["common_field_types"]
    assert "file" in agg["common_field_types"]


def test_platform_aggregate_excludes_failures(tmp_path):
    """Only successful applications contribute to platform aggregates."""
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    db.record("fail.com", "greenhouse", "playwright", 1, ["text"], [], 10.0, success=False)
    db.record("ok.com", "greenhouse", "playwright", 2, ["text", "email"], ["visa?"], 30.0, success=True)

    agg = db.get_platform_aggregate("greenhouse")
    assert agg["observation_count"] == 1
    assert agg["avg_pages"] == 2.0


def test_platform_aggregate_unknown_platform(tmp_path):
    """Unknown platform returns None."""
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    assert db.get_platform_aggregate("nonexistent") is None


def test_platform_common_screening_questions(tmp_path):
    """Common screening questions tracked across domains."""
    from jobpulse.form_experience_db import FormExperienceDB

    db = FormExperienceDB(db_path=str(tmp_path / "exp.db"))
    db.record("a.com", "lever", "pw", 1, ["text"], ["visa?", "salary?"], 20.0, True)
    db.record("b.com", "lever", "pw", 1, ["text"], ["visa?", "notice?"], 20.0, True)
    db.record("c.com", "lever", "pw", 1, ["text"], ["visa?"], 20.0, True)

    agg = db.get_platform_aggregate("lever")
    # "visa?" appears in all 3 = 100% frequency
    assert agg["common_screening_questions"][0][0] == "visa?"
    assert agg["common_screening_questions"][0][1] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_platform_aggregate_returns_stats tests/jobpulse/test_form_prefetch.py::test_platform_aggregate_unknown_platform -v`
Expected: FAIL — `get_platform_aggregate` doesn't exist

- [ ] **Step 3: Implement `get_platform_aggregate()`**

In `jobpulse/form_experience_db.py`, add method to `FormExperienceDB`:

```python
def get_platform_aggregate(self, platform: str) -> dict | None:
    """Aggregate form experience across ALL domains for a platform.

    Returns stats that let PageAnalyzer and FormNavigator make informed
    decisions on brand-new domains where per-domain lookup returns nothing.
    """
    with sqlite3.connect(self._db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT
                COUNT(*) as obs_count,
                AVG(pages_filled) as avg_pages,
                AVG(time_seconds) as avg_time,
                GROUP_CONCAT(field_types, '|||') as all_field_types,
                GROUP_CONCAT(screening_questions, '|||') as all_screening
            FROM form_experience
            WHERE platform = ? AND success = 1""",
            (platform,),
        ).fetchone()

    if not row or row["obs_count"] == 0:
        return None

    # Compute field type frequencies across all domains
    from collections import Counter
    field_counter: Counter[str] = Counter()
    total_fields = 0
    for chunk in (row["all_field_types"] or "").split("|||"):
        if not chunk:
            continue
        types = json.loads(chunk)
        field_counter.update(types)
        total_fields += len(types)

    avg_field_count = total_fields / row["obs_count"] if row["obs_count"] else 0

    # Common screening questions with frequency
    sq_counter: Counter[str] = Counter()
    for chunk in (row["all_screening"] or "").split("|||"):
        if not chunk:
            continue
        questions = json.loads(chunk)
        sq_counter.update(questions)

    return {
        "platform": platform,
        "observation_count": row["obs_count"],
        "avg_pages": round(row["avg_pages"], 1),
        "avg_field_count": round(avg_field_count, 1),
        "avg_time_seconds": round(row["avg_time"], 1),
        "common_field_types": [ft for ft, _ in field_counter.most_common(20)],
        "field_type_frequencies": dict(field_counter.most_common(20)),
        "common_screening_questions": sq_counter.most_common(15),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_form_prefetch.py
git commit -m "feat(form-experience): add platform-level aggregate — cross-domain field/page/timing stats"
```

---

### Task 2: Wire Platform Aggregate into PageAnalyzer (DOM Stability)

**Files:**
- Modify: `jobpulse/page_analyzer.py:208-240`
- Modify: `jobpulse/application_orchestrator_pkg/__init__.py:62`
- Test: `tests/jobpulse/test_page_analyzer.py`

**Why:** When hitting a brand-new Greenhouse domain, PageAnalyzer classifies blind. But `get_platform_aggregate("greenhouse")` now tells it "expect ~12 fields, 2 pages." If the DOM shows 3 fields, the SPA is still loading — wait for stability instead of misclassifying.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_page_analyzer.py — append

@pytest.mark.asyncio
async def test_stability_wait_uses_platform_aggregate():
    """PageAnalyzer uses platform aggregate for new domains (no per-domain data)."""
    from unittest.mock import AsyncMock, MagicMock

    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)

    # Inject form experience with platform aggregate (no per-domain data)
    mock_exp = MagicMock()
    mock_exp.lookup.return_value = None  # No per-domain data — new domain
    mock_exp.get_platform_aggregate.return_value = {
        "avg_field_count": 10.0,
        "observation_count": 15,
    }
    analyzer.form_experience = mock_exp

    # Sparse snapshot: only 2 fields (SPA loading)
    sparse = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
        ],
        url="https://boards.greenhouse.io/newcompany/jobs/123",
    )

    # After stability wait, full fields load
    full = _snapshot(
        fields=[
            {"input_type": "text", "label": "First Name", "current_value": ""},
            {"input_type": "text", "label": "Last Name", "current_value": ""},
            {"input_type": "email", "label": "Email", "current_value": ""},
            {"input_type": "tel", "label": "Phone", "current_value": ""},
            {"input_type": "file", "label": "Resume", "current_value": ""},
        ] + [{"input_type": "select", "label": f"Q{i}", "current_value": ""} for i in range(5)],
        url="https://boards.greenhouse.io/newcompany/jobs/123",
        has_file_inputs=True,
    )
    bridge.get_snapshot = AsyncMock(return_value=full)

    result = await analyzer.detect(sparse)
    assert result == PageType.APPLICATION_FORM
    mock_exp.get_platform_aggregate.assert_called()


@pytest.mark.asyncio
async def test_no_stability_wait_without_form_experience():
    """Without form experience, classify immediately."""
    bridge = AsyncMock()
    analyzer = PageAnalyzer(bridge)
    analyzer.form_experience = None

    s = _snapshot(
        fields=[{"input_type": "text", "label": "First Name", "current_value": ""}],
        url="https://unknown-ats.com/apply",
    )
    result = await analyzer.detect(s)
    bridge.get_snapshot.assert_not_called()
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/jobpulse/test_page_analyzer.py::test_stability_wait_uses_platform_aggregate -v`
Expected: FAIL — `form_experience` not on PageAnalyzer

- [ ] **Step 3: Add platform-aware stability wait to PageAnalyzer**

In `jobpulse/page_analyzer.py`:

```python
import json as _json

class PageAnalyzer:
    """Hybrid page type detector: DOM first, vision LLM fallback, platform-aware stability."""

    def __init__(self, bridge: Any, form_experience=None):
        self.bridge = bridge
        self.form_experience = form_experience

    async def detect(self, snapshot: dict) -> PageType:
        """Detect page type with platform-aware DOM stability wait."""
        page_type, confidence = _dom_detect(snapshot)

        # Stability wait for APPLICATION_FORM/UNKNOWN when we know the platform
        # expects more fields than currently visible (SPA still loading)
        if self.form_experience is not None and page_type in (PageType.APPLICATION_FORM, PageType.UNKNOWN):
            url = snapshot.get("url", "")
            snapshot = await self._stability_wait(snapshot, url)
            page_type, confidence = _dom_detect(snapshot)

        if confidence >= _VISION_THRESHOLD:
            logger.debug("DOM detection: %s (confidence=%.2f)", page_type, confidence)
            return page_type

        logger.info(
            "DOM detection low confidence (%.2f for %s) — trying vision",
            confidence, page_type,
        )
        try:
            screenshot_bytes = await self.bridge.screenshot()
            if not screenshot_bytes:
                logger.warning("Vision fallback skipped: screenshot() returned empty/None")
            else:
                logger.debug("Vision fallback: screenshot %d bytes", len(screenshot_bytes))
                vision_type, vision_confidence = await _vision_detect(screenshot_bytes)
                if vision_confidence > confidence:
                    return vision_type
        except Exception as exc:
            logger.warning("Vision fallback failed: %r", exc, exc_info=True)

        return page_type

    async def _stability_wait(self, snapshot: dict, url: str, max_polls: int = 6, interval: float = 0.5) -> dict:
        """Wait for DOM to stabilize when platform data predicts more fields."""
        import asyncio

        if not url:
            return snapshot

        expected_count = self._get_expected_field_count(url)
        if expected_count is None:
            return snapshot

        current_count = len(snapshot.get("fields", []))
        if current_count >= expected_count * 0.6:
            return snapshot

        logger.info(
            "DOM stability wait: %d fields found, platform avg %.0f — polling for SPA load",
            current_count, expected_count,
        )
        for _ in range(max_polls):
            await asyncio.sleep(interval)
            try:
                fresh = await self.bridge.get_snapshot(force_refresh=True)
                if hasattr(fresh, "model_dump"):
                    fresh = fresh.model_dump()
                new_count = len(fresh.get("fields", []))
                if new_count >= expected_count * 0.6:
                    logger.info("DOM stabilized: %d fields (expected %.0f)", new_count, expected_count)
                    return fresh
                if new_count == current_count:
                    return fresh  # No change — stop waiting
                current_count = new_count
                snapshot = fresh
            except Exception:
                break
        return snapshot

    def _get_expected_field_count(self, url: str) -> float | None:
        """Get expected field count: per-domain first, platform aggregate fallback."""
        # Tier 1: exact domain
        per_domain = self.form_experience.lookup(url)
        if per_domain and per_domain.get("success"):
            stored = per_domain.get("field_types", "[]")
            if isinstance(stored, str):
                stored = _json.loads(stored)
            return float(len(stored))

        # Tier 2: platform aggregate (cross-domain)
        platform = self._infer_platform(url)
        if platform:
            agg = self.form_experience.get_platform_aggregate(platform)
            if agg and agg["observation_count"] >= 3:
                return agg["avg_field_count"]

        return None

    @staticmethod
    def _infer_platform(url: str) -> str | None:
        url_lower = url.lower()
        for platform, pattern in [
            ("greenhouse", "greenhouse"),
            ("lever", "lever.co"),
            ("workday", "myworkdayjobs"),
            ("smartrecruiters", "smartrecruiters"),
            ("indeed", "indeed.com"),
            ("ashby", "ashbyhq.com"),
            ("icims", "icims.com"),
        ]:
            if pattern in url_lower:
                return platform
        return None
```

- [ ] **Step 4: Wire into ApplicationOrchestrator**

In `jobpulse/application_orchestrator_pkg/__init__.py`, change line 62:

```python
        self.analyzer = PageAnalyzer(self.driver, form_experience=self._get_form_experience())
```

Add helper:

```python
    @staticmethod
    def _get_form_experience():
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            return FormExperienceDB()
        except Exception:
            return None
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/jobpulse/test_page_analyzer.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/page_analyzer.py jobpulse/application_orchestrator_pkg/__init__.py tests/jobpulse/test_page_analyzer.py
git commit -m "feat(page-analyzer): platform-aware DOM stability wait — uses aggregate field count across domains"
```

---

### Task 3: Platform-Level Navigation Fallback in NavigationLearner

**Files:**
- Modify: `jobpulse/navigation_learner.py:49-59`
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:77-81`
- Test: `tests/jobpulse/test_navigation_learner.py`

**Why:** When NavigationLearner has no per-domain sequence, it gives up. But `form_experience.db` tracks `platform` — if 90% of Greenhouse domains needed just `[click_apply]`, the navigator should try that pattern on new Greenhouse domains instead of blind detection.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_navigation_learner.py — append

def test_platform_nav_pattern_fallback(tmp_path):
    """When no domain sequence exists, return the most common platform pattern."""
    import json
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))

    # Save 3 Greenhouse domains with same nav pattern
    for domain in ["acme.com", "beta.com", "gamma.com"]:
        learner.save_sequence(domain, [
            {"page_type": "job_description", "action": "click_apply"},
        ], success=True)

    # New domain — no per-domain data
    assert learner.get_sequence("newcompany.com") is None

    # But platform-level pattern should be available
    pattern = learner.get_platform_pattern("greenhouse", exclude_domain="newcompany.com")
    assert pattern is not None
    assert len(pattern) == 1
    assert pattern[0]["action"] == "click_apply"


def test_platform_nav_pattern_needs_minimum_observations(tmp_path):
    """Platform pattern requires >=3 successful domains to be trustworthy."""
    learner = NavigationLearner(db_path=str(tmp_path / "nav.db"))

    # Only 1 domain — not enough
    learner.save_sequence("acme.com", [
        {"page_type": "login_form", "action": "fill_login"},
    ], success=True)

    pattern = learner.get_platform_pattern("greenhouse")
    assert pattern is None
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py::test_platform_nav_pattern_fallback -v`
Expected: FAIL — `get_platform_pattern` doesn't exist

- [ ] **Step 3: Add platform column + aggregate query to NavigationLearner**

In `jobpulse/navigation_learner.py`:

Add `platform` column to schema in `_init_db()`:

```python
def _init_db(self):
    with sqlite3.connect(self._db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sequences (
                domain TEXT PRIMARY KEY,
                platform TEXT DEFAULT '',
                steps TEXT NOT NULL,
                success INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                replay_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0
            )
        """)
        # Migration: add platform column if missing
        try:
            conn.execute("SELECT platform FROM sequences LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE sequences ADD COLUMN platform TEXT DEFAULT ''")
```

Update `save_sequence()` to accept and store platform:

```python
def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool, platform: str = ""):
    """Save a navigation sequence. Empty steps never overwrite existing data."""
    domain = self._normalize_domain(domain_or_url)
    now = datetime.now(UTC).isoformat()

    if not steps and success:
        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
            if existing:
                logger.debug("Skipping empty-steps save for %s — existing sequence preserved", domain)
                return

    steps_json = json.dumps(steps)
    with sqlite3.connect(self._db_path) as conn:
        conn.execute(
            """INSERT INTO sequences (domain, platform, steps, success, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain) DO UPDATE SET
                   platform = CASE WHEN excluded.platform != '' THEN excluded.platform ELSE sequences.platform END,
                   steps = excluded.steps,
                   success = excluded.success,
                   updated_at = excluded.updated_at""",
            (domain, platform, steps_json, int(success), now, now),
        )
    logger.info("Saved navigation sequence for %s (platform=%s, success=%s, %d steps)",
                domain, platform, success, len(steps))
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="adaptation",
            source_loop="navigation_learner",
            domain=domain,
            agent_name="navigator",
            payload={"param": "navigation_path", "old_value": "", "new_value": f"{len(steps)}_steps", "reason": "learned_navigation"},
            session_id=f"nl_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        )
    except Exception as e:
        logger.debug("Optimization signal failed: %s", e)
```

Add `get_platform_pattern()`:

```python
def get_platform_pattern(self, platform: str, exclude_domain: str = "", min_observations: int = 3) -> list[dict] | None:
    """Get the most common navigation pattern for a platform across all domains.

    Falls back here when per-domain lookup returns None. Requires >=min_observations
    successful domains on this platform to be trustworthy.
    """
    with sqlite3.connect(self._db_path) as conn:
        rows = conn.execute(
            "SELECT steps FROM sequences WHERE platform = ? AND success = 1 AND domain != ?",
            (platform, self._normalize_domain(exclude_domain) if exclude_domain else ""),
        ).fetchall()

    if len(rows) < min_observations:
        return None

    # Find most common pattern (by action sequence, ignoring selectors)
    from collections import Counter
    pattern_counts: Counter[str] = Counter()
    pattern_map: dict[str, list[dict]] = {}
    for row in rows:
        steps = json.loads(row[0])
        key = "|".join(s.get("action", "") for s in steps)
        pattern_counts[key] += 1
        pattern_map[key] = steps

    most_common_key, count = pattern_counts.most_common(1)[0]
    if count < min_observations:
        return None

    logger.info("Platform pattern for %s: %s (%d/%d domains)", platform, most_common_key, count, len(rows))
    return pattern_map[most_common_key]
```

- [ ] **Step 4: Wire platform fallback into FormNavigator**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, modify the learned sequence lookup (lines 77-81):

```python
        # Try learned sequence: per-domain first, platform fallback second
        domain = extract_domain(url)
        learned = self.learner.get_sequence(domain)
        if not learned and platform:
            learned = self.learner.get_platform_pattern(platform, exclude_domain=domain)
            if learned:
                logger.info("Using PLATFORM pattern for %s (%s, no domain-specific data)", domain, platform)
```

Update `save_sequence` call in `__init__.py:229` to pass platform:

```python
            self.learner.save_sequence(domain, navigation_steps, success=True, platform=platform)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/navigation_learner.py jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/application_orchestrator_pkg/__init__.py tests/jobpulse/test_navigation_learner.py
git commit -m "feat(nav-learner): platform-level nav pattern fallback — cross-domain sequence reuse"
```

---

### Task 4: Auto-Invalidate Stale Sequences + TTL + Failure Signals

**Files:**
- Modify: `jobpulse/navigation_learner.py:49-98`
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:112-123`
- Test: `tests/jobpulse/test_navigation_learner.py`

**Why:** Learned sequences never auto-invalidate. When replay finishes but lands on wrong page, `mark_failed()` is never called. Also no TTL — ATS redesigns rot old sequences.

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_navigation_learner.py — append

def test_ttl_expired_sequence_not_returned(learner):
    """Sequences older than 30 days are not returned."""
    from datetime import datetime, UTC, timedelta
    import sqlite3

    steps = [{"page_type": "job_description", "action": "click_apply"}]
    learner.save_sequence("acme.com", steps, success=True)

    old_date = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    with sqlite3.connect(learner._db_path) as conn:
        conn.execute("UPDATE sequences SET updated_at = ? WHERE domain = ?", (old_date, "acme.com"))

    assert learner.get_sequence("acme.com") is None


def test_consecutive_failures_purge_sequence(learner):
    """3 consecutive mark_failed() calls delete the sequence."""
    steps = [{"page_type": "login_form", "action": "fill_login"}]
    learner.save_sequence("acme.com", steps, success=True)

    learner.mark_failed("acme.com")
    learner.mark_failed("acme.com")
    learner.mark_failed("acme.com")

    import sqlite3
    with sqlite3.connect(learner._db_path) as conn:
        row = conn.execute("SELECT * FROM sequences WHERE domain = ?", ("acme.com",)).fetchone()
    assert row is None


def test_empty_steps_do_not_overwrite_existing(learner):
    """Empty steps must not overwrite a non-empty learned sequence."""
    good = [{"page_type": "login_form", "action": "fill_login"}, {"page_type": "job_description", "action": "click_apply"}]
    learner.save_sequence("acme.com", good, success=True)
    learner.save_sequence("acme.com", [], success=True)
    result = learner.get_sequence("acme.com")
    assert result is not None
    assert len(result) == 2
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py::test_ttl_expired_sequence_not_returned tests/jobpulse/test_navigation_learner.py::test_consecutive_failures_purge_sequence tests/jobpulse/test_navigation_learner.py::test_empty_steps_do_not_overwrite_existing -v`
Expected: FAIL

- [ ] **Step 3: Add TTL to `get_sequence()`, purge-on-3-failures to `mark_failed()`, empty guard to `save_sequence()`**

In `jobpulse/navigation_learner.py`:

```python
_SEQUENCE_TTL_DAYS = 30
_MAX_CONSECUTIVE_FAILURES = 3

def get_sequence(self, domain_or_url: str) -> list[dict] | None:
    domain = self._normalize_domain(domain_or_url)
    with sqlite3.connect(self._db_path) as conn:
        row = conn.execute(
            "SELECT steps, updated_at FROM sequences WHERE domain = ? AND success = 1",
            (domain,),
        ).fetchone()
    if not row:
        return None
    try:
        updated = datetime.fromisoformat(row[1])
        if (datetime.now(UTC) - updated).days > _SEQUENCE_TTL_DAYS:
            logger.info("Navigation sequence for %s expired (%s)", domain, row[1])
            return None
    except (ValueError, TypeError):
        pass
    return json.loads(row[0])

def mark_failed(self, domain_or_url: str):
    domain = self._normalize_domain(domain_or_url)
    with sqlite3.connect(self._db_path) as conn:
        conn.execute(
            "UPDATE sequences SET success = 0, fail_count = fail_count + 1 WHERE domain = ?",
            (domain,),
        )
        row = conn.execute("SELECT fail_count FROM sequences WHERE domain = ?", (domain,)).fetchone()
        if row and row[0] >= _MAX_CONSECUTIVE_FAILURES:
            conn.execute("DELETE FROM sequences WHERE domain = ?", (domain,))
            logger.info("Purged navigation sequence for %s after %d failures", domain, row[0])
        else:
            logger.info("Invalidated navigation for %s (fail_count=%d)", domain, row[0] if row else 0)
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="failure", source_loop="navigation_learner",
            domain=domain, agent_name="navigator",
            payload={"param": "navigation_path", "reason": "replay_failed"},
            session_id=f"nl_fail_{domain}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
        )
    except Exception as e:
        logger.debug("Optimization signal failed: %s", e)
```

- [ ] **Step 4: Wire auto-invalidation into FormNavigator**

In `_navigator.py`, after line 123 (replay completed, wrong page):

```python
                self.learner.mark_failed(domain)
```

After line 114 (replay step exception):

```python
                    self.learner.mark_failed(domain)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/navigation_learner.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_learner.py
git commit -m "feat(nav-learner): 30-day TTL, auto-invalidate on replay mismatch, purge after 3 failures"
```

---

### Task 5: Redirect Loop Detection

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:129-198`
- Test: `tests/jobpulse/test_navigation_learner.py`

**Why:** Navigation loop has no cycle detection. Session expiry can cause LOGIN→JD→LOGIN loop, burning all 10 steps.

- [ ] **Step 1: Write failing test**

```python
# tests/jobpulse/test_navigation_learner.py — append

@pytest.mark.asyncio
async def test_redirect_loop_detected():
    """Navigator aborts when same (domain, page_type) appears 3 times."""
    from unittest.mock import AsyncMock
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator
    from jobpulse.form_models import PageType

    orch = AsyncMock()
    orch.cookie_dismisser.dismiss = AsyncMock(return_value=False)
    orch.learner.get_sequence = AsyncMock(return_value=None)
    orch.learner.get_platform_pattern = AsyncMock(return_value=None)

    auth = AsyncMock()
    nav = FormNavigator(orch, auth)

    login_snap = {"url": "https://ats.example.com/login", "buttons": [{"text": "Sign in", "enabled": True}],
                  "fields": [{"input_type": "email", "label": "Email", "current_value": ""},
                             {"input_type": "password", "label": "Password", "current_value": ""}],
                  "page_text_preview": "", "has_file_inputs": False}
    jd_snap = {"url": "https://ats.example.com/jobs/123", "buttons": [{"text": "Apply Now", "enabled": True}],
               "fields": [], "page_text_preview": "", "has_file_inputs": False}

    call_count = 0
    async def mock_snapshot(force_refresh=False):
        nonlocal call_count
        call_count += 1
        return login_snap if call_count % 2 == 1 else jd_snap

    orch.driver.navigate = AsyncMock()
    orch.driver.get_snapshot = mock_snapshot
    orch.driver.click = AsyncMock()
    orch.driver.page = None
    orch.driver.wait_for_apply = AsyncMock(side_effect=AttributeError)
    auth.handle_login = AsyncMock(return_value=jd_snap)

    steps = []
    result = await nav.navigate_to_form("https://ats.example.com/jobs/123", "generic", steps)
    assert result["page_type"] == PageType.UNKNOWN
    assert len(steps) < 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py::test_redirect_loop_detected -v`
Expected: FAIL — runs all 10 steps

- [ ] **Step 3: Add cycle detection**

In `_navigator.py`, inside `navigate_to_form()` after `apply_attempts = 0`:

```python
        apply_attempts = 0
        visited_states: dict[tuple[str, str], int] = {}
        for step in range(MAX_NAVIGATION_STEPS):
            page_type = await self.analyzer.detect(snapshot)
            logger.info("Navigation step %d: %s", step + 1, page_type)

            current_url = snapshot.get("url", "") if isinstance(snapshot, dict) else ""
            _loop_key = (_extract_loop_domain(current_url), str(page_type))
            visited_states[_loop_key] = visited_states.get(_loop_key, 0) + 1
            if visited_states[_loop_key] >= 3:
                logger.warning("Redirect loop: %s × %d — aborting", _loop_key, visited_states[_loop_key])
                return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}
```

Add helper at bottom:

```python
def _extract_loop_domain(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower().removeprefix("www.") if parsed.netloc else url
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_navigation_learner.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_learner.py
git commit -m "feat(navigator): redirect loop detection — abort on 3× repeated (domain, page_type)"
```

---

### Task 6: Session Expiry + Consent Gate Detection

**Files:**
- Modify: `jobpulse/form_models.py:11-21`
- Modify: `jobpulse/page_analyzer.py:33-138`
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py`
- Modify: `jobpulse/application_orchestrator_pkg/_form_filler.py`
- Modify: `jobpulse/application_orchestrator_pkg/__init__.py`
- Test: `tests/jobpulse/test_page_analyzer.py`

**Why:** Two missing PageTypes. Session expiry mid-form silently kills applications — no re-auth path. GDPR consent gates classified as UNKNOWN, ending navigation.

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_page_analyzer.py — append

def test_dom_session_expired():
    s = _snapshot(page_text="Your session has expired. Please sign in again.")
    result, confidence = _dom_detect(s)
    assert result == PageType.SESSION_EXPIRED
    assert confidence >= 0.9


def test_dom_session_timed_out():
    s = _snapshot(page_text="Session timed out. Please log in to continue.")
    result, confidence = _dom_detect(s)
    assert result == PageType.SESSION_EXPIRED


def test_dom_consent_gate_privacy():
    s = _snapshot(
        page_text="Please agree to our privacy policy to continue your application.",
        buttons=[{"text": "I Accept", "enabled": True}, {"text": "Decline", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result == PageType.CONSENT_GATE
    assert confidence >= 0.8


def test_dom_consent_gate_not_cookie_banner():
    """Cookie text alone should NOT trigger CONSENT_GATE."""
    s = _snapshot(
        page_text="We use cookies to improve your experience",
        buttons=[{"text": "Accept All", "enabled": True}],
    )
    result, confidence = _dom_detect(s)
    assert result != PageType.CONSENT_GATE
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/jobpulse/test_page_analyzer.py::test_dom_session_expired tests/jobpulse/test_page_analyzer.py::test_dom_consent_gate_privacy -v`
Expected: FAIL — enum values don't exist

- [ ] **Step 3: Add to PageType enum**

In `jobpulse/form_models.py`:

```python
class PageType(StrEnum):
    JOB_DESCRIPTION = "job_description"
    LOGIN_FORM = "login_form"
    SIGNUP_FORM = "signup_form"
    EMAIL_VERIFICATION = "email_verification"
    APPLICATION_FORM = "application_form"
    CONFIRMATION = "confirmation"
    VERIFICATION_WALL = "verification_wall"
    SESSION_EXPIRED = "session_expired"
    CONSENT_GATE = "consent_gate"
    UNKNOWN = "unknown"
```

- [ ] **Step 4: Add detection patterns to `_dom_detect`**

In `jobpulse/page_analyzer.py`, add two pattern blocks:

```python
_SESSION_EXPIRED_PATTERNS = re.compile(
    r"(session\s*(has\s*)?(expired|timed?\s*out)"
    r"|please\s*(sign|log)\s*in\s*again"
    r"|you\s*(have\s*)?been\s*(signed|logged)\s*out"
    r"|login\s*session\s*(has\s*)?(expired|ended))",
    re.IGNORECASE,
)

_CONSENT_GATE_PATTERNS = re.compile(
    r"(agree\s*to\s*(our\s*)?(privacy|data)\s*(policy|processing)"
    r"|consent\s*to\s*(the\s*)?(processing|collection|use)\s*of\s*(your\s*)?(personal\s*)?data"
    r"|accept\s*(our\s*)?terms\s*(and|&)\s*(conditions|privacy)"
    r"|by\s*continuing.*consent\s*to)",
    re.IGNORECASE,
)
```

Insert in `_dom_detect()` between verification_wall (step 1) and confirmation (step 2):

```python
    # 1.5 Session expired
    if _SESSION_EXPIRED_PATTERNS.search(page_text):
        return PageType.SESSION_EXPIRED, 0.95

    # 1.6 Consent gate (full-page, not cookie banner)
    if _CONSENT_GATE_PATTERNS.search(page_text) and not has_application_fields:
        if any(re.search(r"(accept|agree|continue|proceed)", b.get("text", ""), re.IGNORECASE)
               for b in buttons if b.get("enabled", True)):
            return PageType.CONSENT_GATE, 0.9
```

- [ ] **Step 5: Handle both in navigator**

In `_navigator.py`, add to the navigation loop:

```python
            elif page_type == PageType.SESSION_EXPIRED:
                sso = self.sso.detect_sso(snapshot)
                if sso:
                    await self.sso.click_sso(sso)
                    snapshot = self._as_dict(await self.driver.get_snapshot())
                    steps.append({"page_type": "session_expired", "action": f"sso_{sso['provider']}"})
                else:
                    snapshot = await self.auth.handle_login(snapshot, platform)
                    steps.append({"page_type": "session_expired", "action": "fill_login"})

            elif page_type == PageType.CONSENT_GATE:
                for btn in snapshot.get("buttons", []):
                    if btn.get("enabled", True) and re.search(
                        r"(accept|agree|continue|proceed|i\s*accept)", btn.get("text", ""), re.IGNORECASE
                    ):
                        logger.info("Accepting consent gate: '%s'", btn.get("text", ""))
                        await self.driver.click(btn["selector"])
                        break
                snapshot = self._as_dict(await self.driver.get_snapshot())
                steps.append({"page_type": "consent_gate", "action": "accept_consent"})
```

- [ ] **Step 6: Add session expiry detection in FormFiller**

In `_form_filler.py`, after `result = await filler.fill(...)`:

```python
        if not result.get("success"):
            try:
                from jobpulse.page_analyzer import _dom_detect
                from jobpulse.form_models import PageType
                post = await self.driver.get_snapshot(force_refresh=True)
                if hasattr(post, "model_dump"):
                    post = post.model_dump()
                post_type, _ = _dom_detect(post)
                if post_type in (PageType.SESSION_EXPIRED, PageType.LOGIN_FORM):
                    result["error"] = "session_expired"
            except Exception:
                pass
```

In `__init__.py`, after `result = await self._filler.fill_application(...)`, add re-auth retry:

```python
        if result.get("error") == "session_expired" and not result.get("_reauth_attempted"):
            logger.info("Session expired during form fill — re-authenticating")
            reauth = await self._navigator.navigate_to_form(url, platform, navigation_steps)
            if reauth["page_type"] == PageType.APPLICATION_FORM:
                result = await self._filler.fill_application(
                    platform=platform, snapshot=reauth["snapshot"],
                    cv_path=cv_path, cover_letter_path=cover_letter_path,
                    profile=profile, custom_answers=custom_answers,
                    overrides=overrides, dry_run=dry_run,
                    form_intelligence=form_intelligence,
                )
                result["_reauth_attempted"] = True
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/jobpulse/test_page_analyzer.py tests/jobpulse/test_navigation_learner.py tests/jobpulse/test_application_orchestrator.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/form_models.py jobpulse/page_analyzer.py jobpulse/application_orchestrator_pkg/_navigator.py jobpulse/application_orchestrator_pkg/_form_filler.py jobpulse/application_orchestrator_pkg/__init__.py tests/jobpulse/test_page_analyzer.py
git commit -m "feat: SESSION_EXPIRED + CONSENT_GATE detection, re-auth on session expiry, auto-accept consent"
```

---

### Task 7: Per-Action Timeouts + SSO Wait + i18n Cookies + verify_submission Fix

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_executor.py`
- Modify: `jobpulse/sso_handler.py:64-67`
- Modify: `jobpulse/cookie_dismisser.py:16-35`
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:328-367`
- Test: `tests/jobpulse/test_cookie_dismisser.py`
- Test: `tests/jobpulse/test_application_orchestrator.py`

**Why:** Four small fixes bundled — each is <20 lines, same blast radius (safety net layer). Per-action timeout prevents hangs. SSO needs post-click wait. Cookie dismisser needs DE/FR/ES. verify_submission crashes on dict snapshots.

- [ ] **Step 1: Write failing tests**

```python
# tests/jobpulse/test_cookie_dismisser.py — append

@pytest.mark.asyncio
async def test_dismiss_german_akzeptieren(dismisser):
    snapshot = {"buttons": [{"text": "Alle akzeptieren", "enabled": True, "selector": "#de"}],
                "page_text_preview": "Wir verwenden Cookies"}
    assert await dismisser.dismiss(snapshot) is True

@pytest.mark.asyncio
async def test_dismiss_french_accepter(dismisser):
    snapshot = {"buttons": [{"text": "Tout accepter", "enabled": True, "selector": "#fr"}],
                "page_text_preview": "Ce site utilise des cookies"}
    assert await dismisser.dismiss(snapshot) is True

@pytest.mark.asyncio
async def test_dismiss_spanish_aceptar(dismisser):
    snapshot = {"buttons": [{"text": "Aceptar todas", "enabled": True, "selector": "#es"}],
                "page_text_preview": "Utilizamos cookies"}
    assert await dismisser.dismiss(snapshot) is True
```

```python
# tests/jobpulse/test_application_orchestrator.py — append

@pytest.mark.asyncio
async def test_verify_submission_handles_dict():
    from unittest.mock import AsyncMock, MagicMock
    from jobpulse.application_orchestrator_pkg._navigator import FormNavigator

    orch = MagicMock()
    orch.driver = AsyncMock()
    orch.driver.get_snapshot = AsyncMock(return_value={
        "url": "https://ats.example.com/thank-you",
        "page_text_preview": "Thank you for applying!",
        "buttons": [], "fields": [],
    })
    nav = FormNavigator(orch, MagicMock())
    result = await nav.verify_submission()
    assert result["verified"] is True
```

- [ ] **Step 2: Run to verify failures**

Run: `python -m pytest tests/jobpulse/test_cookie_dismisser.py::test_dismiss_german_akzeptieren tests/jobpulse/test_application_orchestrator.py::test_verify_submission_handles_dict -v`
Expected: FAIL

- [ ] **Step 3: Add i18n cookie patterns**

In `jobpulse/cookie_dismisser.py`:

```python
_ACCEPT_PATTERNS = [
    re.compile(r"accept\s*(all)?\s*(cookies?)?", re.IGNORECASE),
    re.compile(r"agree\s*(to\s*all|\s*&\s*continue)?", re.IGNORECASE),
    re.compile(r"i\s*agree", re.IGNORECASE),
    re.compile(r"(got\s*it|okay|ok)(!|\.)?$", re.IGNORECASE),
    re.compile(r"allow\s*(all\s*)?(cookies?)?", re.IGNORECASE),
    re.compile(r"consent", re.IGNORECASE),
    re.compile(r"(alle\s*)?akzeptieren", re.IGNORECASE),
    re.compile(r"zustimmen", re.IGNORECASE),
    re.compile(r"(tout\s*)?accepter", re.IGNORECASE),
    re.compile(r"j.accepte", re.IGNORECASE),
    re.compile(r"aceptar\s*(todas?)?", re.IGNORECASE),
]

_COOKIE_CONTEXT = re.compile(
    r"(cookie|gdpr|privacy|consent|tracking"
    r"|datenschutz|privacidad|confidentialit[eé]"
    r"|wir\s*verwenden|utilizamos|ce\s*site\s*utilise)", re.IGNORECASE
)

_ANTI_PATTERNS = re.compile(
    r"(reject|decline|manage|customize|preferences|settings|policy|learn\s*more"
    r"|user\s*agreement|terms|copyright"
    r"|ablehnen|verwalten|einstellungen|rechazar|gestionar|refuser|param[eè]tres)",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Fix verify_submission dict safety**

In `_navigator.py`, replace attribute access in `verify_submission()`:

```python
    async def verify_submission(self) -> dict:
        await asyncio.sleep(3.0)
        snapshot = await self.driver.get_snapshot(force_refresh=True)
        if not snapshot:
            return {"verified": False, "reason": "no_snapshot"}
        snapshot = self._as_dict(snapshot)
        text = (snapshot.get("page_text_preview") or "").lower()
        url = (snapshot.get("url") or "").lower()
        # ... rest uses dict .get() access (already shown in original)
```

- [ ] **Step 5: Add per-action timeout to executor**

In `_executor.py`, wrap action dispatch:

```python
_ACTION_TIMEOUT = 30

async def execute_action(self, action: Any, tg_stream: Any = None):
    # ... parse action fields (unchanged) ...
    try:
        await asyncio.wait_for(self._dispatch(atype, selector, value, file_path), timeout=_ACTION_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Action %s on %s timed out after %ds", atype, selector[:60], _ACTION_TIMEOUT)
        raise TimeoutError(f"Action {atype} timed out")
    # ... stream to telegram (unchanged) ...

async def _dispatch(self, atype, selector, value, file_path):
    # ... all the if/elif branches moved here (unchanged) ...
```

- [ ] **Step 6: Add SSO post-click wait**

In `sso_handler.py`:

```python
async def click_sso(self, sso: dict):
    import asyncio
    logger.info("Clicking SSO: %s at %s", sso["provider"], sso["selector"])
    await self.bridge.click(sso["selector"])
    # Wait for redirect (SSO flows take 2-10s)
    for _ in range(20):
        await asyncio.sleep(0.5)
        try:
            snap = await self.bridge.get_snapshot()
            if hasattr(snap, "model_dump"):
                snap = snap.model_dump()
            url = snap.get("url", "").lower()
            if "oauth" not in url and "accounts.google" not in url and "login" not in url:
                break
        except Exception:
            continue
    logger.info("SSO flow completed for %s", sso["provider"])
```

- [ ] **Step 7: Run all tests**

Run: `python -m pytest tests/jobpulse/test_cookie_dismisser.py tests/jobpulse/test_application_orchestrator.py tests/jobpulse/test_page_analyzer.py tests/jobpulse/test_navigation_learner.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/cookie_dismisser.py jobpulse/sso_handler.py jobpulse/application_orchestrator_pkg/_executor.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_cookie_dismisser.py tests/jobpulse/test_application_orchestrator.py
git commit -m "feat: i18n cookies (DE/FR/ES), SSO post-click wait, 30s action timeout, dict-safe verify_submission"
```

---

## Post-Implementation Data Flow

```
                    ┌───────────────────────────────────────────────┐
                    │              BRAIN 2 (already works)          │
                    │  trajectory_store → strategy_reflector        │
                    │  correction_capture → screening_answers       │
                    │  ExperienceMemory (GRPO cross-domain)         │
                    └───────────────────────────────────────────────┘
                                         ▲
                    ┌────────────────────┐│ optimization signals
                    │  optimization.db   │◄──────────────────────────┐
                    └────────────────────┘                           │
                              ▲                                      │
             success + failure signals                               │
                    ┌─────────┴──────────┐                           │
                    │                    │                            │
    ┌───────────────┴───────┐  ┌────────┴────────────┐  ┌───────────┴─────┐
    │ navigation_learning.db│  │ form_experience.db   │  │ scan_learning.db│
    │ + platform column     │  │ + get_platform_      │  │ (per-platform   │
    │ + TTL 30 days         │  │   aggregate()        │  │  already works) │
    │ + auto-invalidate     │  │ GROUP BY platform    │  └─────────────────┘
    │ + purge on 3 failures │  │ → avg fields, pages  │
    └───────────┬───────────┘  └──────────┬───────────┘
                │                         │
    per-domain ─┤── platform fallback     │ per-domain ─┤── platform aggregate
                │                         │
    ┌───────────┴───────────┐  ┌──────────┴───────────┐
    │ FormNavigator         │  │ PageAnalyzer          │
    │ - loop detection      │  │ - SESSION_EXPIRED     │
    │ - session expiry      │  │ - CONSENT_GATE        │
    │ - consent gate        │  │ - stability wait      │
    │ - platform nav replay │  │   (platform avg)      │
    └───────────────────────┘  └──────────────────────┘

    ┌───────────────────┐  ┌──────────────────────────┐
    │ CookieBannerDismis│  │ ActionExecutor            │
    │ EN/DE/FR/ES       │  │ 30s per-action timeout    │
    └───────────────────┘  └──────────────────────────┘

    ┌───────────────────┐
    │ SSOHandler        │
    │ post-click wait   │
    └───────────────────┘
```

**Every application now contributes to the platform aggregate.** Application #1 to a new Greenhouse domain already benefits from the 47 prior Greenhouse applications. The agent gets faster and more accurate with zero additional LLM cost.
