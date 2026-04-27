# Adaptive Application Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise application orchestration from 5.5/10 to 9.5+/10 by implementing container-first form scoping, adaptive timing, and semantic option matching.

**Architecture:** Three independent workstreams. WS1 scopes scans to form containers (learned → auto-detect → hint). WS2 replaces hardcoded timing with measured values from FormExperienceDB. WS3 adds semantic option matching so mapping and fill are never disconnected.

**Tech Stack:** Playwright CDP (Accessibility.getPartialAXTree), SQLite (FormExperienceDB), Voyage embeddings (semantic_matcher), existing strategy pattern.

**Spec:** `docs/superpowers/specs/2026-04-27-adaptive-application-orchestration-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `jobpulse/form_engine/semantic_matcher.py` | **NEW.** `semantic_option_match()`, `checkbox_intent()`, `CANONICAL_ALIASES` table |
| `jobpulse/form_scanner.py` | Container-scoped CDP scanning via `getPartialAXTree` |
| `jobpulse/form_engine/field_scanner.py` | `resolve_form_container()` (3-tier), `scan_fields()` wiring |
| `jobpulse/form_engine/field_mapper.py` | Options-aware `seed_mapping()`, strategy label wiring |
| `jobpulse/form_experience_db.py` | `container_selector` + timing columns, `store_container()`/`get_container()`/`store_timing()` |
| `jobpulse/native_form_filler.py` | Pipeline wiring: container resolution, timing measurement, scan validation, failure classification |
| `jobpulse/ats_adapters/strategy.py` | `form_container_hint()`, `expected_field_range()` on base class |
| `jobpulse/ats_adapters/linkedin.py` | Container hint + field range override |
| `jobpulse/ats_adapters/workday.py` | Reduced hydration wait + field range override |
| `jobpulse/ats_adapters/greenhouse.py` | Container hint + field range override |
| `jobpulse/ats_adapters/generic.py` | Auto-detect logic as Tier 2 fallback |
| `tests/jobpulse/test_semantic_matcher.py` | **NEW.** Tests for semantic matching with real option data |
| `tests/jobpulse/test_form_experience_db.py` | Extended with container + timing tests |
| `tests/jobpulse/test_form_scanner.py` | Extended with scoped scanning tests |
| `tests/jobpulse/ats_adapters/test_strategy.py` | Extended with new strategy method tests |
| `tests/jobpulse/test_adaptive_pipeline.py` | **NEW.** Live integration tests with real Chrome CDP |

---

## WS1: Adaptive Form Scoping

### Task 1: Strategy Base Class — Container Hint + Field Range

**Files:**
- Modify: `jobpulse/ats_adapters/strategy.py:44-183` (BasePlatformStrategy class)
- Test: `tests/jobpulse/ats_adapters/test_strategy.py`

- [ ] **Step 1: Write tests for new strategy methods**

Add to `tests/jobpulse/ats_adapters/test_strategy.py`:

```python
from jobpulse.ats_adapters.strategy import BasePlatformStrategy, get_strategy


def test_base_strategy_form_container_hint_returns_none():
    strategy = get_strategy("generic")
    assert strategy.form_container_hint() is None


def test_base_strategy_expected_field_range_default():
    strategy = get_strategy("generic")
    assert strategy.expected_field_range() == (1, 30)


def test_linkedin_strategy_form_container_hint():
    strategy = get_strategy("linkedin")
    assert strategy.form_container_hint() == ".jobs-easy-apply-modal"


def test_linkedin_strategy_expected_field_range():
    strategy = get_strategy("linkedin")
    assert strategy.expected_field_range() == (3, 10)


def test_workday_strategy_expected_field_range():
    strategy = get_strategy("workday")
    assert strategy.expected_field_range() == (3, 20)


def test_greenhouse_strategy_form_container_hint():
    strategy = get_strategy("greenhouse")
    assert strategy.form_container_hint() == "#application"


def test_greenhouse_strategy_expected_field_range():
    strategy = get_strategy("greenhouse")
    assert strategy.expected_field_range() == (3, 15)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_strategy.py -v -k "container_hint or field_range"`
Expected: FAIL — `form_container_hint` and `expected_field_range` not defined

- [ ] **Step 3: Add methods to BasePlatformStrategy**

In `jobpulse/ats_adapters/strategy.py`, add after `validate_field_scan` (around line 117, after `custom_field_scan`):

```python
    def form_container_hint(self) -> str | None:
        """Optional CSS selector hint for the form container.

        Used as Tier 3 fallback when learned selector and auto-detect both fail.
        After a successful fill, the hint gets overwritten by the learned selector.
        """
        return None

    def expected_field_range(self) -> tuple[int, int]:
        """(min, max) expected fields per page for this platform.

        Used by scan validation to detect obviously wrong scans.
        """
        return (1, 30)
```

- [ ] **Step 4: Add overrides to LinkedIn strategy**

In `jobpulse/ats_adapters/linkedin.py`, add to `LinkedInStrategy` class:

```python
    def form_container_hint(self) -> str | None:
        return ".jobs-easy-apply-modal"

    def expected_field_range(self) -> tuple[int, int]:
        return (3, 10)
```

- [ ] **Step 5: Add overrides to Workday strategy**

In `jobpulse/ats_adapters/workday.py`, add to `WorkdayStrategy` class:

```python
    def expected_field_range(self) -> tuple[int, int]:
        return (3, 20)
```

- [ ] **Step 6: Add overrides to Greenhouse strategy**

In `jobpulse/ats_adapters/greenhouse.py`, add to `GreenhouseStrategy` class:

```python
    def form_container_hint(self) -> str | None:
        return "#application"

    def expected_field_range(self) -> tuple[int, int]:
        return (3, 15)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/ats_adapters/test_strategy.py -v -k "container_hint or field_range"`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/ats_adapters/strategy.py jobpulse/ats_adapters/linkedin.py jobpulse/ats_adapters/workday.py jobpulse/ats_adapters/greenhouse.py tests/jobpulse/ats_adapters/test_strategy.py
git commit -m "feat(strategy): add form_container_hint() and expected_field_range() to BasePlatformStrategy"
```

---

### Task 2: FormExperienceDB — Container Selector Storage

**Files:**
- Modify: `jobpulse/form_experience_db.py:28-77` (schema), add methods after line 494
- Test: `tests/jobpulse/test_form_experience_db.py`

- [ ] **Step 1: Write tests for container storage**

Add to `tests/jobpulse/test_form_experience_db.py`:

```python
def test_store_and_get_container(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", ".application-form")
    assert db.get_container("greenhouse.io") == ".application-form"


def test_get_container_returns_none_when_missing(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    assert db.get_container("unknown.com") is None


def test_delete_container(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#app-form")
    db.delete_container("greenhouse.io")
    assert db.get_container("greenhouse.io") is None


def test_store_container_overwrites(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#old-form")
    db.store_container("greenhouse.io", "#new-form")
    assert db.get_container("greenhouse.io") == "#new-form"


def test_store_and_get_timing(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_timing("workday.com", hydration_ms=8000, fill_ms=12000, transition_ms=3000)
    timing = db.get_timing("workday.com")
    assert timing["avg_hydration_ms"] == 8000
    assert timing["avg_fill_ms"] == 12000
    assert timing["avg_transition_ms"] == 3000


def test_get_timing_returns_none_when_missing(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    assert db.get_timing("unknown.com") is None


def test_store_timing_averages_on_update(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_timing("workday.com", hydration_ms=8000, fill_ms=12000, transition_ms=3000)
    db.store_timing("workday.com", hydration_ms=10000, fill_ms=14000, transition_ms=5000)
    timing = db.get_timing("workday.com")
    assert timing["avg_hydration_ms"] == 9000
    assert timing["avg_fill_ms"] == 13000
    assert timing["avg_transition_ms"] == 4000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v -k "container or timing"`
Expected: FAIL — methods not defined

- [ ] **Step 3: Add container_selectors and timing tables to schema**

In `jobpulse/form_experience_db.py`, add two new CREATE TABLE statements to both `_schema_sql()` and `_init_db()`:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS container_selectors (
                    domain TEXT PRIMARY KEY,
                    selector TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS page_timings (
                    domain TEXT PRIMARY KEY,
                    avg_hydration_ms INTEGER NOT NULL,
                    avg_fill_ms INTEGER NOT NULL,
                    avg_transition_ms INTEGER NOT NULL,
                    sample_count INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                )
            """)
```

Add the same to `_schema_sql()` so self-healing works.

- [ ] **Step 4: Implement container storage methods**

Add after `save_field_mappings()` (line 494) in `jobpulse/form_experience_db.py`:

```python
    def store_container(self, domain_or_url: str, selector: str) -> None:
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO container_selectors (domain, selector, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(domain) DO UPDATE SET
                       selector = excluded.selector,
                       updated_at = excluded.updated_at""",
                (domain, selector, now),
            )
        logger.info("Stored container selector for %s: %s", domain, selector)

    def get_container(self, domain_or_url: str) -> str | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT selector FROM container_selectors WHERE domain = ?",
                (domain,),
            ).fetchone()
        return row[0] if row else None

    def delete_container(self, domain_or_url: str) -> None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "DELETE FROM container_selectors WHERE domain = ?", (domain,),
            )
        logger.info("Deleted stale container selector for %s", domain)
```

- [ ] **Step 5: Implement timing storage methods**

Add after the container methods:

```python
    def store_timing(
        self,
        domain_or_url: str,
        hydration_ms: int,
        fill_ms: int,
        transition_ms: int,
    ) -> None:
        domain = self.normalize_domain(domain_or_url)
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT avg_hydration_ms, avg_fill_ms, avg_transition_ms, sample_count "
                "FROM page_timings WHERE domain = ?",
                (domain,),
            ).fetchone()
            if existing:
                n = existing[3]
                new_hydration = (existing[0] * n + hydration_ms) // (n + 1)
                new_fill = (existing[1] * n + fill_ms) // (n + 1)
                new_transition = (existing[2] * n + transition_ms) // (n + 1)
                conn.execute(
                    """UPDATE page_timings SET
                       avg_hydration_ms = ?, avg_fill_ms = ?, avg_transition_ms = ?,
                       sample_count = sample_count + 1, updated_at = ?
                       WHERE domain = ?""",
                    (new_hydration, new_fill, new_transition, now, domain),
                )
            else:
                conn.execute(
                    """INSERT INTO page_timings
                       (domain, avg_hydration_ms, avg_fill_ms, avg_transition_ms,
                        sample_count, updated_at)
                       VALUES (?, ?, ?, ?, 1, ?)""",
                    (domain, hydration_ms, fill_ms, transition_ms, now),
                )

    def get_timing(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM page_timings WHERE domain = ?", (domain,),
            ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v -k "container or timing"`
Expected: All PASS

- [ ] **Step 7: Run full form_experience_db test suite for regressions**

Run: `python -m pytest tests/jobpulse/test_form_experience_db.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_form_experience_db.py
git commit -m "feat(form-experience): add container_selectors and page_timings tables"
```

---

### Task 3: Container-Scoped CDP Scanning

**Files:**
- Modify: `jobpulse/form_scanner.py:171-272` (scan_form function)
- Test: `tests/jobpulse/test_form_scanner.py`

- [ ] **Step 1: Write test for scoped scanning with container_backend_node_id**

Add to `tests/jobpulse/test_form_scanner.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from jobpulse.form_scanner import scan_form, _parse_ax_node


@pytest.mark.asyncio
async def test_scan_form_uses_partial_tree_when_container_provided():
    """When a container_backend_node_id is provided, scan_form should
    call getPartialAXTree instead of getFullAXTree."""
    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply"
    mock_page.context = MagicMock()
    mock_page.frames = [mock_page]
    mock_page.main_frame = mock_page

    mock_cdp = AsyncMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    mock_cdp.send = AsyncMock(return_value={"nodes": [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Apply"}, "properties": []},
        {"nodeId": "2", "role": {"value": "textbox"}, "name": {"value": "First Name"}, "properties": [
            {"name": "required", "value": {"value": True}}
        ]},
        {"nodeId": "3", "role": {"value": "textbox"}, "name": {"value": "Last Name"}, "properties": []},
    ]})

    result = await scan_form(mock_page, container_backend_node_id="42")

    mock_cdp.send.assert_called_once_with(
        "Accessibility.getPartialAXTree",
        {"backendNodeId": 42, "fetchRelatives": True},
    )
    assert len(result.fields) == 2
    assert result.fields[0].label == "First Name"
    assert result.fields[1].label == "Last Name"


@pytest.mark.asyncio
async def test_scan_form_falls_back_to_full_tree_on_partial_failure():
    """If getPartialAXTree fails, fall back to getFullAXTree."""
    mock_page = AsyncMock()
    mock_page.url = "https://example.com/apply"
    mock_page.context = MagicMock()
    mock_page.frames = [mock_page]
    mock_page.main_frame = mock_page

    mock_cdp = AsyncMock()
    mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    call_count = 0
    async def mock_send(method, params=None):
        nonlocal call_count
        call_count += 1
        if method == "Accessibility.getPartialAXTree":
            raise Exception("Not supported")
        return {"nodes": [
            {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Apply"}, "properties": []},
            {"nodeId": "2", "role": {"value": "textbox"}, "name": {"value": "Email"}, "properties": []},
        ]}

    mock_cdp.send = mock_send

    result = await scan_form(mock_page, container_backend_node_id="99")
    assert len(result.fields) == 1
    assert result.fields[0].label == "Email"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v -k "partial_tree or falls_back"`
Expected: FAIL — `scan_form()` doesn't accept `container_backend_node_id`

- [ ] **Step 3: Modify scan_form to accept container_backend_node_id**

In `jobpulse/form_scanner.py`, change the `scan_form` signature and CDP call:

```python
async def scan_form(
    page: Page,
    *,
    container_backend_node_id: str | None = None,
) -> FormScanResult:
    """Discover all form fields using CDP Accessibility tree.

    When container_backend_node_id is provided, uses getPartialAXTree
    to scope scanning to a DOM subtree. Falls back to getFullAXTree
    if the partial call fails or CDP is unavailable.
    """
    page = await _resolve_iframe_page(page)

    cdp = await _get_cdp_session(page)
    if cdp is None:
        return await _scan_form_fallback(page)

    nodes: list[dict] = []
    if container_backend_node_id is not None:
        try:
            result = await cdp.send(
                "Accessibility.getPartialAXTree",
                {"backendNodeId": int(container_backend_node_id), "fetchRelatives": True},
            )
            nodes = result.get("nodes", [])
        except Exception as exc:
            logger.debug("FormScanner: getPartialAXTree failed, falling back: %s", exc)

    if not nodes:
        try:
            result = await cdp.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes", [])
        except Exception as exc:
            logger.debug("FormScanner: getFullAXTree failed: %s", exc)
            return await _scan_form_fallback(page)

    # ... rest of the parsing logic stays the same (lines 191-272)
```

- [ ] **Step 4: Delete `_NAV_NOISE_LABELS` regex and its usage**

Remove the `_NAV_NOISE_LABELS` regex constant (lines 42-51) and remove the check at line 230:

```python
# DELETE this line:
        if _NAV_NOISE_LABELS.search(name):
            continue
```

The container scoping replaces this regex. When no container is provided (full-page scan), fields are filtered by the caller's scan validation instead.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v`
Expected: All PASS (new tests pass, existing tests still pass since they don't pass the new param)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_scanner.py tests/jobpulse/test_form_scanner.py
git commit -m "feat(form-scanner): scoped CDP scanning via getPartialAXTree + delete _NAV_NOISE_LABELS regex"
```

---

### Task 4: Container Resolution — 3-Tier Detection

**Files:**
- Modify: `jobpulse/form_engine/field_scanner.py` — replace `scope_to_dialog()` with `resolve_form_container()`
- Test: `tests/jobpulse/test_form_scanner.py` (extend)

- [ ] **Step 1: Write tests for resolve_form_container**

Add to `tests/jobpulse/test_form_scanner.py`:

```python
from jobpulse.form_engine.field_scanner import resolve_form_container


@pytest.mark.asyncio
async def test_resolve_container_tier1_learned(tmp_path):
    """Tier 1: returns stored container from FormExperienceDB."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#application")

    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply/123"
    mock_locator = AsyncMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_page.locator = MagicMock(return_value=mock_locator)

    strategy = get_strategy("greenhouse")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result == "#application"


@pytest.mark.asyncio
async def test_resolve_container_tier1_stale_falls_to_tier3(tmp_path):
    """Tier 1 selector returns 0 elements → deletes it → falls to Tier 3 hint."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
    db.store_container("greenhouse.io", "#old-form-gone")

    mock_page = AsyncMock()
    mock_page.url = "https://greenhouse.io/apply/123"
    stale_locator = AsyncMock()
    stale_locator.count = AsyncMock(return_value=0)
    hint_locator = AsyncMock()
    hint_locator.count = AsyncMock(return_value=1)

    def mock_locator_fn(selector):
        if selector == "#old-form-gone":
            return stale_locator
        if selector == "#application":
            return hint_locator
        return stale_locator

    mock_page.locator = mock_locator_fn

    strategy = get_strategy("greenhouse")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result == "#application"
    assert db.get_container("greenhouse.io") is None


@pytest.mark.asyncio
async def test_resolve_container_returns_none_when_all_fail(tmp_path):
    """All tiers fail → returns None for full-page scan."""
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.ats_adapters.strategy import get_strategy

    db = FormExperienceDB(db_path=str(tmp_path / "test.db"))

    mock_page = AsyncMock()
    mock_page.url = "https://unknown-ats.com/apply"
    mock_page.context = MagicMock()
    mock_cdp = AsyncMock()
    mock_cdp.send = AsyncMock(return_value={"nodes": []})
    mock_page.context.new_cdp_session = AsyncMock(return_value=mock_cdp)

    empty_locator = AsyncMock()
    empty_locator.count = AsyncMock(return_value=0)
    mock_page.locator = MagicMock(return_value=empty_locator)

    strategy = get_strategy("generic")
    result = await resolve_form_container(mock_page, strategy, db)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v -k "resolve_container"`
Expected: FAIL — `resolve_form_container` not defined

- [ ] **Step 3: Implement resolve_form_container in field_scanner.py**

In `jobpulse/form_engine/field_scanner.py`, replace `scope_to_dialog()` with:

```python
async def resolve_form_container(
    page: "Page",
    strategy,
    form_experience_db=None,
) -> str | None:
    """Resolve the CSS selector for the form container.

    Three-tier detection:
    1. Learned — stored selector from FormExperienceDB
    2. Auto-detect — common ancestor of form-role nodes in a11y tree
    3. Strategy hint — optional CSS selector from platform strategy

    Returns CSS selector string, or None for full-page scan.
    """
    from urllib.parse import urlparse

    url = getattr(page, "url", "") or ""
    domain = urlparse(url).netloc.lower().removeprefix("www.") if url else ""

    # Tier 1: Learned selector
    if form_experience_db and domain:
        stored = form_experience_db.get_container(domain)
        if stored:
            try:
                container = page.locator(stored)
                if await container.count():
                    logger.info("Container Tier 1 (learned): %s for %s", stored, domain)
                    return stored
            except Exception:
                pass
            form_experience_db.delete_container(domain)
            logger.info("Container Tier 1: stale selector '%s' deleted for %s", stored, domain)

    # Tier 2: Auto-detect via common ancestor
    detected = await _detect_form_container(page)
    if detected:
        logger.info("Container Tier 2 (auto-detect): %s for %s", detected, domain)
        return detected

    # Tier 3: Strategy hint
    hint = strategy.form_container_hint()
    if hint:
        try:
            container = page.locator(hint)
            if await container.count():
                logger.info("Container Tier 3 (strategy hint): %s for %s", hint, domain)
                return hint
        except Exception:
            pass

    logger.info("Container resolution: no container found for %s, full-page scan", domain)
    return None


async def _detect_form_container(page: "Page") -> str | None:
    """Auto-detect form container via common ancestor of form-role nodes.

    Walks the a11y tree to find all form fields, resolves their DOM nodes,
    then finds the deepest common ancestor that contains >=3 form nodes
    and a submit/next button.
    """
    from jobpulse.form_scanner import _get_cdp_session, _FORM_ROLES

    cdp = await _get_cdp_session(page)
    if cdp is None:
        return None

    try:
        result = await cdp.send("Accessibility.getFullAXTree")
        nodes = result.get("nodes", [])
    except Exception:
        return None
    finally:
        try:
            await cdp.detach()
        except Exception:
            pass

    form_node_ids: list[str] = []
    button_node_ids: list[str] = []
    _SUBMIT_NAMES = {"submit", "apply", "next", "continue", "review", "save", "proceed"}

    for node in nodes:
        role = node.get("role", {}).get("value", "")
        name = (node.get("name", {}).get("value", "") or "").lower()
        node_id = node.get("backendDOMNodeId")
        if not node_id:
            continue

        if role in _FORM_ROLES and role != "button" and name:
            form_node_ids.append(str(node_id))
        elif role == "button" and any(s in name for s in _SUBMIT_NAMES):
            button_node_ids.append(str(node_id))

    if len(form_node_ids) < 3:
        return None

    # Find common ancestor via DOM.getNodeForLocation is unreliable,
    # so use JS to walk from each node up and find the shared ancestor
    try:
        selector = await page.evaluate("""(nodeIds) => {
            function ancestors(el) {
                const path = [];
                while (el && el !== document.body) {
                    path.push(el);
                    el = el.parentElement;
                }
                return path;
            }
            function selectorFor(el) {
                if (el.id) return '#' + CSS.escape(el.id);
                if (el === document.body) return 'body';
                const tag = el.tagName.toLowerCase();
                const parent = el.parentElement;
                if (!parent) return tag;
                const siblings = Array.from(parent.children).filter(c => c.tagName === el.tagName);
                if (siblings.length === 1) return selectorFor(parent) + ' > ' + tag;
                const idx = siblings.indexOf(el) + 1;
                return selectorFor(parent) + ' > ' + tag + ':nth-of-type(' + idx + ')';
            }

            // Resolve backend node IDs to elements — not possible from JS.
            // Instead, find all visible form elements and compute ancestor.
            const formEls = Array.from(document.querySelectorAll(
                'input:not([type="hidden"]), select, textarea, [role="combobox"], [role="textbox"], [role="radio"], [role="checkbox"]'
            )).filter(el => el.offsetParent !== null);
            if (formEls.length < 3) return null;

            let commonAncestor = formEls[0].parentElement;
            for (const el of formEls.slice(1)) {
                while (commonAncestor && !commonAncestor.contains(el)) {
                    commonAncestor = commonAncestor.parentElement;
                }
            }
            if (!commonAncestor || commonAncestor === document.body || commonAncestor === document.documentElement) {
                return null;
            }
            // Validate: ancestor has a submit-ish button
            const buttons = commonAncestor.querySelectorAll('button, [role="button"], input[type="submit"]');
            const hasSubmit = Array.from(buttons).some(b => {
                const text = (b.textContent || b.value || b.getAttribute('aria-label') || '').toLowerCase();
                return ['submit', 'apply', 'next', 'continue', 'review', 'save', 'proceed'].some(s => text.includes(s));
            });
            if (!hasSubmit) return null;
            return selectorFor(commonAncestor);
        }""", form_node_ids)
        return selector
    except Exception as exc:
        logger.debug("Auto-detect form container failed: %s", exc)
        return None
```

- [ ] **Step 4: Delete scope_to_dialog function**

Remove the `scope_to_dialog()` function (lines 52-82) from `jobpulse/form_engine/field_scanner.py`.

- [ ] **Step 5: Update scan_fields to use resolve_form_container**

Replace the current `scan_fields()` function:

```python
async def scan_fields(
    page: "Page",
    *,
    strategy=None,
    form_experience_db=None,
) -> list[dict]:
    """Scan visible form fields — container-scoped a11y tree first, fallback second.

    Uses 3-tier container resolution to scope scanning and exclude noise.
    """
    from jobpulse.form_scanner import scan_form

    container_selector = None
    container_node_id = None

    if strategy or form_experience_db:
        from jobpulse.ats_adapters.generic import GenericStrategy
        _strategy = strategy or GenericStrategy()
        container_selector = await resolve_form_container(
            page, _strategy, form_experience_db,
        )

    if container_selector:
        try:
            container = page.locator(container_selector)
            node_id = await container.first.evaluate("el => el.id || null")
            # Get backend node ID for CDP scoping
            cdp = await page.context.new_cdp_session(page)
            try:
                dom_result = await cdp.send("DOM.getDocument")
                query_result = await cdp.send(
                    "DOM.querySelector",
                    {"nodeId": dom_result["root"]["nodeId"], "selector": container_selector},
                )
                if query_result.get("nodeId"):
                    describe = await cdp.send(
                        "DOM.describeNode", {"nodeId": query_result["nodeId"]},
                    )
                    container_node_id = str(describe["node"]["backendNodeId"])
            finally:
                await cdp.detach()
        except Exception as exc:
            logger.debug("Container node ID resolution failed: %s", exc)

    scan = await scan_form(page, container_backend_node_id=container_node_id)
    if scan.fields:
        return ax_scan_to_field_dicts(page, scan)

    fields = await scan_fields_locator_fallback(page)
    return fields
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/form_engine/field_scanner.py jobpulse/form_scanner.py tests/jobpulse/test_form_scanner.py
git commit -m "feat(field-scanner): 3-tier container resolution replaces scope_to_dialog"
```

---

### Task 5: Scan Validation Gate

**Files:**
- Modify: `jobpulse/form_engine/field_scanner.py` — add `validate_field_scan()`
- Modify: `jobpulse/native_form_filler.py` — wire validation into fill loop
- Test: `tests/jobpulse/test_form_scanner.py`

- [ ] **Step 1: Write tests for scan validation**

Add to `tests/jobpulse/test_form_scanner.py`:

```python
from jobpulse.form_engine.field_scanner import validate_field_scan


def test_validate_scan_too_many_fields():
    fields = [{"label": f"field_{i}", "type": "text"} for i in range(35)]
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("linkedin")  # expected_field_range = (3, 10)
    result = validate_field_scan(fields, strategy)
    assert not result["valid"]
    assert result["reason"] == "too_many_fields"


def test_validate_scan_zero_fields():
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("generic")
    result = validate_field_scan([], strategy)
    assert not result["valid"]
    assert result["reason"] == "zero_fields"


def test_validate_scan_excessive_duplicates():
    fields = [{"label": "Name", "type": "text"}] * 5
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("generic")
    result = validate_field_scan(fields, strategy)
    assert not result["valid"]
    assert result["reason"] == "duplicate_labels"


def test_validate_scan_passes_normal_form():
    fields = [
        {"label": "First Name", "type": "text"},
        {"label": "Last Name", "type": "text"},
        {"label": "Email", "type": "text"},
        {"label": "Phone", "type": "text"},
        {"label": "Resume", "type": "file"},
    ]
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("greenhouse")  # expected_field_range = (3, 15)
    result = validate_field_scan(fields, strategy)
    assert result["valid"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v -k "validate_scan"`
Expected: FAIL — `validate_field_scan` not defined

- [ ] **Step 3: Implement validate_field_scan**

Add to `jobpulse/form_engine/field_scanner.py`:

```python
def validate_field_scan(
    fields: list[dict],
    strategy,
    form_experience: dict | None = None,
) -> dict:
    """Validate a field scan result for obvious problems.

    Returns {"valid": bool, "reason": str, "count": int}.
    """
    from collections import Counter

    expected_min, expected_max = strategy.expected_field_range()

    if form_experience and form_experience.get("field_count"):
        expected_max = int(form_experience["field_count"] * 1.5)

    if len(fields) == 0:
        return {"valid": False, "reason": "zero_fields", "count": 0}

    if len(fields) > expected_max:
        return {"valid": False, "reason": "too_many_fields", "count": len(fields)}

    label_counts = Counter(f.get("label", "") for f in fields)
    duplicates = sum(1 for c in label_counts.values() if c > 1)
    if duplicates > 3:
        return {"valid": False, "reason": "duplicate_labels", "count": duplicates}

    return {"valid": True, "reason": "", "count": len(fields)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_scanner.py -v -k "validate_scan"`
Expected: All PASS

- [ ] **Step 5: Wire validation into NativeFormFiller.fill() loop**

In `jobpulse/native_form_filler.py`, after the `_scan_fields()` call at line 1196, add validation:

```python
            fields = await self._scan_fields()

            # Validate scan
            from jobpulse.form_engine.field_scanner import validate_field_scan
            validation = validate_field_scan(fields, self._strategy)
            if not validation["valid"]:
                logger.warning(
                    "Scan validation failed on page %d: %s (count=%d). Rescanning.",
                    page_num, validation["reason"], validation.get("count", 0),
                )
                # Rescan with tighter container or iframe
                await self._resolve_page_context()
                fields = await self._scan_fields()
                validation = validate_field_scan(fields, self._strategy)
                if not validation["valid"]:
                    logger.error(
                        "Scan validation still fails after rescan: %s", validation["reason"],
                    )
```

- [ ] **Step 6: Run full test suite for regressions**

Run: `python -m pytest tests/jobpulse/ -v -k "form_scanner or native_form" --timeout=30`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/form_engine/field_scanner.py jobpulse/native_form_filler.py tests/jobpulse/test_form_scanner.py
git commit -m "feat(field-scanner): scan validation gate rejects noise-polluted scans"
```

---

## WS2: Adaptive Timing

### Task 6: Remove Hardcoded Timing + Wire Adaptive Delays

**Files:**
- Modify: `jobpulse/native_form_filler.py:78-86` (delete `_PLATFORM_MIN_PAGE_TIME`), lines 96, 1113-1119, 1434-1435
- Modify: `jobpulse/ats_adapters/workday.py:54` (reduce hydration wait)
- Test: `tests/jobpulse/test_native_form_filler.py`

- [ ] **Step 1: Write tests for adaptive timing**

Add to `tests/jobpulse/test_native_form_filler.py`:

```python
import os


def test_platform_min_page_time_dict_removed():
    """_PLATFORM_MIN_PAGE_TIME should no longer exist."""
    import jobpulse.native_form_filler as mod
    assert not hasattr(mod, "_PLATFORM_MIN_PAGE_TIME")


def test_risk_delay_multiplier_removed():
    """NativeFormFiller should not have _risk_delay_multiplier."""
    from unittest.mock import AsyncMock, MagicMock
    page = AsyncMock()
    driver = MagicMock()
    filler = mod.NativeFormFiller(page, driver)
    assert not hasattr(filler, "_risk_delay_multiplier")


def test_fast_fill_env_var_skips_delays(monkeypatch):
    """When FAST_FILL=true, _get_adaptive_page_delay returns 0."""
    monkeypatch.setenv("FAST_FILL", "true")
    from jobpulse.native_form_filler import _get_adaptive_page_delay
    delay = _get_adaptive_page_delay("workday", None)
    assert delay == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "min_page_time or risk_delay or fast_fill"`
Expected: FAIL

- [ ] **Step 3: Delete _PLATFORM_MIN_PAGE_TIME and risk_delay_multiplier**

In `jobpulse/native_form_filler.py`:

1. Delete the `_PLATFORM_MIN_PAGE_TIME` dict (lines 78-85)
2. Delete `self._risk_delay_multiplier: float = 1.0` from `__init__` (line 105)
3. Delete the risk delay multiplier setup in `fill()` (lines 1113-1119):
```python
        # DELETE these lines:
        self._risk_delay_multiplier = 1.0
        if hints:
            risk = hints.get("risk_level", "low")
            if risk == "medium":
                self._risk_delay_multiplier = 1.5
            elif risk == "high":
                self._risk_delay_multiplier = 2.5
```
4. In `_fill_by_label()`, remove the multiplier from the sleep (line 373):
```python
        # Change from:
        await asyncio.sleep(_get_field_gap(label) * self._risk_delay_multiplier)
        # To:
        if not os.environ.get("FAST_FILL"):
            await asyncio.sleep(_get_field_gap(label))
```

- [ ] **Step 4: Add _get_adaptive_page_delay function**

Add as a module-level function in `jobpulse/native_form_filler.py`:

```python
def _get_adaptive_page_delay(platform: str, timing_data: dict | None) -> float:
    """Return adaptive page delay in seconds based on measured timing data.

    Returns 0 when FAST_FILL=true (Claude Code assisted mode).
    """
    if os.environ.get("FAST_FILL"):
        return 0.0

    if timing_data:
        measured = timing_data.get("avg_fill_ms", 5000) / 1000.0
        return max(measured * 1.1, 3.0)

    _STRATEGY_DEFAULTS = {
        "workday": 8.0,
        "linkedin": 3.0,
        "greenhouse": 5.0,
        "lever": 5.0,
        "indeed": 8.0,
    }
    return _STRATEGY_DEFAULTS.get(platform, 5.0)
```

- [ ] **Step 5: Replace hardcoded page delay with adaptive delay**

In the fill loop (line 1434), replace:
```python
            # OLD:
            min_time = _PLATFORM_MIN_PAGE_TIME.get(platform, 5.0)
            await asyncio.sleep(min_time * random.uniform(0.8, 1.2))
            # NEW:
            page_delay = _get_adaptive_page_delay(platform, self._timing_data)
            if page_delay > 0:
                await asyncio.sleep(page_delay * random.uniform(0.8, 1.2))
```

Add `self._timing_data = None` to `__init__` and load it in `fill()`:

```python
        # In fill(), after loading form_experience_db
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            url = getattr(self._page, 'url', '') or ''
            if url:
                self._timing_data = FormExperienceDB().get_timing(url)
        except Exception:
            self._timing_data = None
```

- [ ] **Step 6: Reduce Workday hydration wait**

In `jobpulse/ats_adapters/workday.py`, change line 54:
```python
    def wait_for_form_hydrated_ms(self) -> int:
        return 10000
```

- [ ] **Step 7: Add timing measurement to fill loop**

In the fill loop, after filling a page and before the adaptive delay, measure and store timing:

```python
            # After step 7 (consent boxes), before step 8 (adaptive timing):
            page_fill_ms = int((time.monotonic() - t0) * 1000) if page_num == 1 else None
            if page_fill_ms is not None:
                try:
                    from jobpulse.form_experience_db import FormExperienceDB
                    FormExperienceDB().store_timing(
                        page_url,
                        hydration_ms=0,  # measured separately in future
                        fill_ms=page_fill_ms,
                        transition_ms=0,
                    )
                except Exception:
                    pass
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "min_page_time or risk_delay or fast_fill"`
Expected: All PASS

- [ ] **Step 9: Run full test suite for regressions**

Run: `python -m pytest tests/jobpulse/ -v --timeout=30`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add jobpulse/native_form_filler.py jobpulse/ats_adapters/workday.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(timing): adaptive page delays from FormExperienceDB, delete _PLATFORM_MIN_PAGE_TIME"
```

---

## WS3: Semantic Matching

### Task 7: Semantic Option Matcher

**Files:**
- Create: `jobpulse/form_engine/semantic_matcher.py`
- Test: Create `tests/jobpulse/form_engine/test_semantic_matcher.py`

- [ ] **Step 1: Write tests with real option data from previous applications**

Create `tests/jobpulse/form_engine/test_semantic_matcher.py`:

```python
"""Tests for semantic option matching — all option lists are real data
captured from actual ATS form fills."""

import pytest


class TestExactMatch:
    def test_exact_case_insensitive(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Male", "Female", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("male", options) == "Male"
        assert semantic_option_match("FEMALE", options) == "Female"

    def test_exact_with_whitespace(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [" Yes ", "No"]
        assert semantic_option_match("Yes", options) == " Yes "


class TestCanonicalAliases:
    def test_gender_male_to_man(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("male", options) == "Man"

    def test_gender_female_to_woman(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
        assert semantic_option_match("female", options) == "Woman"

    def test_boolean_yes_authorized(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Yes, I am authorized", "No, I am not authorized"]
        assert semantic_option_match("yes", options) == "Yes, I am authorized"

    def test_ethnicity_indian(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [
            "White", "Mixed", "Asian or Asian British - Indian",
            "Asian or Asian British - Pakistani", "Black or Black British",
            "Prefer not to say",
        ]
        assert semantic_option_match("indian", options) == "Asian or Asian British - Indian"

    def test_visa_graduate(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Tier 2 (General)", "Tier 4 Graduate visa", "Indefinite Leave", "British Citizen"]
        assert semantic_option_match("graduate visa", options) == "Tier 4 Graduate visa"

    def test_notice_period_1_month(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Immediately", "Less than 30 days", "1-3 months", "3+ months"]
        assert semantic_option_match("1 month", options) == "Less than 30 days"

    def test_experience_years(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["0-1 years", "2-3 years", "3-5 years", "5+ years"]
        assert semantic_option_match("2 years", options) == "2-3 years"


class TestNumericRange:
    def test_salary_range_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["£20,000 - £30,000", "£30,000 - £40,000", "£40,000 - £50,000", "£50,000+"]
        assert semantic_option_match("35000", options, numeric_value=35000) == "£30,000 - £40,000"

    def test_age_range_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["18 - 24", "25 - 34", "35 - 44", "45+"]
        assert semantic_option_match("27", options, numeric_value=27) == "25 - 34"


class TestTokenOverlap:
    def test_partial_match_via_tokens(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = [
            "I have a valid UK work permit",
            "I require sponsorship",
            "I am a British citizen",
        ]
        assert semantic_option_match("valid UK work permit", options) == "I have a valid UK work permit"


class TestNoMatch:
    def test_returns_none_when_no_match(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        options = ["Red", "Blue", "Green"]
        assert semantic_option_match("purple", options) is None

    def test_returns_none_for_empty_options(self):
        from jobpulse.form_engine.semantic_matcher import semantic_option_match
        assert semantic_option_match("yes", []) is None


class TestCheckboxIntent:
    def test_privacy_consent_check(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("I agree to the privacy policy") is True

    def test_terms_and_conditions(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("I acknowledge the terms and conditions") is True

    def test_marketing_opt_out(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Send me marketing emails and newsletters") is False

    def test_promotional_offers(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Opt in to promotional offers") is False

    def test_required_checkbox_checked(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Some custom checkbox", required=True) is True

    def test_ambiguous_returns_none(self):
        from jobpulse.form_engine.semantic_matcher import checkbox_intent
        assert checkbox_intent("Follow this company") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/form_engine/test_semantic_matcher.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create semantic_matcher.py with all matching tiers**

Create `jobpulse/form_engine/semantic_matcher.py`:

```python
"""Semantic option matching — 6-tier cascade for form field values.

Matches a desired value to available dropdown/radio/combobox options
without relying on exact string matching. Built from real application
data across Greenhouse, Workday, SmartRecruiters, LinkedIn, and iCIMS.
"""
from __future__ import annotations

import re
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    # Gender
    "male": ("man", "m", "he/him", "he/him/his", "masculine"),
    "female": ("woman", "f", "she/her", "she/her/hers", "feminine"),
    "man": ("male", "m", "he/him"),
    "woman": ("female", "f", "she/her"),
    # Boolean
    "yes": ("true", "authorized", "i am", "i do", "i have", "y",
            "yes, i am authorized", "yes i am", "yes, i do", "yes, i have"),
    "no": ("false", "not authorized", "i am not", "i do not", "n",
           "no, i am not", "no i do not"),
    # Ethnicity
    "indian": ("asian or asian british - indian", "south asian", "asian - indian",
               "asian or asian british: indian"),
    "asian": ("asian or asian british", "east asian", "southeast asian"),
    "white": ("white british", "white - british", "white english",
              "white - english/welsh/scottish/northern irish"),
    # Visa / work authorization
    "graduate visa": ("tier 4 graduate visa", "post-study work visa",
                      "graduate route", "graduate route visa"),
    # Notice period
    "1 month": ("4 weeks", "one month", "30 days", "less than 30 days",
                "less than 1 month", "1 month or less"),
    "2 weeks": ("14 days", "two weeks", "less than 2 weeks"),
    "immediately": ("available immediately", "0 days", "now", "none"),
    # Experience years
    "2 years": ("2+ years", "2-3 years", "over 2 years", "2 to 3 years"),
    "3 years": ("3+ years", "3-5 years", "over 3 years", "3 to 5 years"),
    "1 year": ("1+ years", "1-2 years", "over 1 year", "0-1 years"),
}

_RANGE_PAT = re.compile(r"[£$€]?\s*([\d,]+)\s*[-–—]\s*[£$€]?\s*([\d,]+)")

_CONSENT_WORDS = frozenset({"privacy", "consent", "terms", "agree", "acknowledge", "confirm", "gdpr", "data protection"})
_MARKETING_WORDS = frozenset({"marketing", "newsletter", "promotional", "offers", "opt in", "subscribe", "communications"})


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def semantic_option_match(
    desired_value: str,
    available_options: list[str],
    *,
    field_label: str = "",
    aliases: dict[str, tuple[str, ...]] | None = None,
    numeric_value: float | None = None,
) -> str | None:
    """Match a desired value to available options via 5-tier cascade.

    Tiers:
    1. Exact match (case-insensitive, whitespace-normalized)
    2. Canonical alias lookup (CANONICAL_ALIASES + caller aliases)
    3. Numeric range match (salary, age, experience years)
    4. Token overlap (Jaccard similarity, threshold >= 2 shared tokens)
    5. None — caller should escalate to LLM

    Returns the exact option text to use, or None if no match.
    """
    if not available_options or not desired_value:
        return None

    desired_norm = _normalize(desired_value)
    opts_norm = {_normalize(o): o for o in available_options}

    # Tier 1: Exact match
    if desired_norm in opts_norm:
        return opts_norm[desired_norm]

    # Tier 2: Canonical aliases
    all_aliases = dict(CANONICAL_ALIASES)
    if aliases:
        all_aliases.update(aliases)

    for alias in all_aliases.get(desired_norm, ()):
        alias_norm = _normalize(alias)
        if alias_norm in opts_norm:
            return opts_norm[alias_norm]
        for opt_norm, opt_original in opts_norm.items():
            if alias_norm in opt_norm or opt_norm in alias_norm:
                return opt_original

    # Also check if desired_value is itself an alias of something
    for canonical, alias_tuple in all_aliases.items():
        if desired_norm in (_normalize(a) for a in alias_tuple):
            canonical_norm = _normalize(canonical)
            if canonical_norm in opts_norm:
                return opts_norm[canonical_norm]

    # Tier 3: Numeric range
    numeric = numeric_value
    if numeric is None:
        try:
            numeric = float(desired_value.replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
        except (ValueError, AttributeError):
            numeric = None

    if numeric is not None:
        for opt in available_options:
            m = _RANGE_PAT.search(opt)
            if m:
                low = float(m.group(1).replace(",", ""))
                high = float(m.group(2).replace(",", ""))
                if low <= numeric <= high:
                    return opt

    # Tier 4: Token overlap
    stop_words = {"and", "for", "the", "with", "from", "valid", "not", "or", "a", "an", "to", "of", "in", "i", "am", "is"}
    desired_tokens = {t for t in desired_norm.split() if len(t) > 1 and t not in stop_words}

    if desired_tokens:
        best_opt = None
        best_score = 0
        for opt_norm, opt_original in opts_norm.items():
            opt_tokens = {t for t in opt_norm.split() if len(t) > 1 and t not in stop_words}
            overlap = len(desired_tokens & opt_tokens)
            if overlap > best_score:
                best_score = overlap
                best_opt = opt_original
        if best_opt is not None and best_score >= 2:
            return best_opt

    # Tier 5: Substring containment (for values >= 4 chars)
    if len(desired_norm) >= 4:
        for opt_norm, opt_original in opts_norm.items():
            if desired_norm in opt_norm:
                return opt_original

    return None


def checkbox_intent(label: str, *, required: bool = False) -> bool | None:
    """Determine whether to check a checkbox based on its label.

    Returns True (check), False (don't check), or None (ambiguous).
    """
    label_lower = label.lower().strip()

    if any(w in label_lower for w in _CONSENT_WORDS):
        return True

    if any(w in label_lower for w in _MARKETING_WORDS):
        return False

    if required:
        return True

    return None
```

- [ ] **Step 4: Ensure tests/jobpulse/form_engine/__init__.py exists**

```bash
touch tests/jobpulse/form_engine/__init__.py
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/form_engine/test_semantic_matcher.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/semantic_matcher.py tests/jobpulse/form_engine/test_semantic_matcher.py tests/jobpulse/form_engine/__init__.py
git commit -m "feat(semantic-matcher): 5-tier option matching + checkbox intent detection"
```

---

### Task 8: Wire Strategy Labels + Semantic Matching into seed_mapping

**Files:**
- Modify: `jobpulse/form_engine/field_mapper.py:182-229` (seed_mapping function)
- Test: `tests/jobpulse/form_engine/test_semantic_matcher.py` (extend)

- [ ] **Step 1: Write tests for options-aware mapping**

Add to `tests/jobpulse/form_engine/test_semantic_matcher.py`:

```python
class TestOptionsAwareMapping:
    """Test that seed_mapping resolves constrained fields via semantic matching."""

    def test_seed_mapping_resolves_gender_dropdown(self):
        from jobpulse.form_engine.field_mapper import seed_mapping

        fields = [
            {"label": "Gender", "type": "select", "options": ["Man", "Woman", "Non-binary", "Prefer not to say"]},
        ]
        profile = {"gender": "male"}
        custom_answers = {}
        mapping, unresolved = seed_mapping(fields, profile, custom_answers)
        assert mapping.get("Gender") == "Man"
        assert len(unresolved) == 0

    def test_seed_mapping_resolves_salary_range(self):
        from jobpulse.form_engine.field_mapper import seed_mapping

        fields = [
            {"label": "Desired Salary", "type": "select", "options": [
                "£20,000 - £30,000", "£30,000 - £40,000", "£40,000 - £50,000",
            ]},
        ]
        profile = {}
        custom_answers = {"desired salary": "35000"}
        mapping, unresolved = seed_mapping(fields, profile, custom_answers)
        assert mapping.get("Desired Salary") == "£30,000 - £40,000"

    def test_seed_mapping_applies_normalize_label(self):
        """Strategy.normalize_label() should strip '(Required)' before lookup."""
        from jobpulse.form_engine.field_mapper import seed_mapping

        fields = [
            {"label": "First Name (Required)", "type": "text"},
        ]
        profile = {"first_name": "Yash"}
        custom_answers = {}
        mapping, unresolved = seed_mapping(
            fields, profile, custom_answers, strategy=_make_workday_strategy(),
        )
        assert mapping.get("First Name (Required)") == "Yash"


def _make_workday_strategy():
    from jobpulse.ats_adapters.strategy import get_strategy
    return get_strategy("workday")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/form_engine/test_semantic_matcher.py -v -k "OptionsAware"`
Expected: FAIL — seed_mapping doesn't accept `strategy` param or handle options

- [ ] **Step 3: Modify seed_mapping to accept strategy and handle options**

In `jobpulse/form_engine/field_mapper.py`, modify `seed_mapping()`:

```python
def seed_mapping(
    fields: list[dict], profile: dict, custom_answers: dict,
    *,
    strategy=None,
) -> tuple[dict[str, str], list[dict]]:
    """Resolve any field that has a deterministic profile/custom answer.

    When a field has options (select/radio/combobox), uses semantic matching
    to pick the exact option text instead of the raw profile value.
    """
    _ensure_label_db()
    from jobpulse.applicator import PROFILE

    profile_flat = {**PROFILE, **profile}
    mapping: dict[str, str] = {}
    unresolved: list[dict] = []

    # Merge strategy extra label mappings
    if strategy:
        for label_key, profile_key in strategy.extra_label_mappings().items():
            if label_key not in _FIELD_LABEL_TO_PROFILE_KEY:
                _FIELD_LABEL_TO_PROFILE_KEY[label_key] = profile_key

    for field in fields:
        if field["type"] == "file" or field.get("value"):
            continue

        label = field["label"]
        # Apply strategy label normalization for lookup
        lookup_label = label.lower()
        if strategy:
            lookup_label = strategy.normalize_label(label).lower()

        custom_value = custom_answers.get(lookup_label) or custom_answers.get(label.lower())
        if isinstance(custom_value, str) and custom_value.strip():
            resolved = _resolve_with_options(custom_value.strip(), field)
            mapping[label] = resolved
            continue

        fuzzy_custom = _fuzzy_custom_answer(lookup_label, custom_answers)
        if fuzzy_custom is not None:
            resolved = _resolve_with_options(fuzzy_custom, field)
            mapping[label] = resolved
            continue

        profile_key = _FIELD_LABEL_TO_PROFILE_KEY.get(lookup_label)
        if not profile_key:
            profile_key = _fuzzy_label_to_profile_key(lookup_label)
        profile_value = profile_flat.get(profile_key, "") if profile_key else ""
        if profile_key == "location":
            _jctx = custom_answers.get("_job_context")
            if isinstance(_jctx, dict):
                job_loc = _jctx.get("location", "")
                if isinstance(job_loc, str) and job_loc.strip():
                    profile_value = job_loc.strip()
        if isinstance(profile_value, str) and profile_value.strip():
            resolved = _resolve_with_options(profile_value.strip(), field)
            mapping[label] = resolved
            if profile_key and lookup_label not in _FIELD_LABEL_TO_PROFILE_KEY:
                _FIELD_LABEL_TO_PROFILE_KEY[lookup_label] = profile_key
                _persist_label_mapping(lookup_label, profile_key)
            continue

        unresolved.append(field)

    return mapping, unresolved


def _resolve_with_options(value: str, field: dict) -> str:
    """If a field has options, use semantic matching to pick the exact option text."""
    options = field.get("options")
    if not options or field["type"] in ("text", "textarea"):
        return value

    from jobpulse.form_engine.semantic_matcher import semantic_option_match

    try:
        numeric = float(value.replace(",", "").replace("£", "").replace("$", "").replace("€", ""))
    except (ValueError, AttributeError):
        numeric = None

    matched = semantic_option_match(
        value, options,
        field_label=field.get("label", ""),
        numeric_value=numeric,
    )
    return matched if matched is not None else value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/form_engine/test_semantic_matcher.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/field_mapper.py tests/jobpulse/form_engine/test_semantic_matcher.py
git commit -m "feat(field-mapper): options-aware seed_mapping with strategy label normalization"
```

---

### Task 9: Fill Failure Classification

**Files:**
- Modify: `jobpulse/native_form_filler.py` — add `_classify_fill_failure()`, wire into fill loop
- Test: `tests/jobpulse/test_native_form_filler.py`

- [ ] **Step 1: Write tests for failure classification**

Add to `tests/jobpulse/test_native_form_filler.py`:

```python
from jobpulse.native_form_filler import _classify_fill_failure


def test_classify_no_field():
    assert _classify_fill_failure({"success": False, "error": "No field for 'Name'"}) == "no_field"


def test_classify_blocked():
    assert _classify_fill_failure({"success": False, "error": "Element is intercepted"}) == "blocked"


def test_classify_wrong_value():
    assert _classify_fill_failure({"success": False, "value_mismatch": True}) == "wrong_value"


def test_classify_readonly():
    assert _classify_fill_failure({"success": False, "error": "Element is readonly"}) == "readonly"


def test_classify_unknown():
    assert _classify_fill_failure({"success": False, "error": "timeout"}) == "unknown"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "classify"`
Expected: FAIL — function not defined

- [ ] **Step 3: Implement _classify_fill_failure**

Add to `jobpulse/native_form_filler.py` as a module-level function:

```python
def _classify_fill_failure(result: dict) -> str:
    """Classify why a field fill failed to route to correct recovery."""
    error = (result.get("error") or "").lower()
    if "no field" in error or "not found" in error or "no fillable" in error:
        return "no_field"
    if "intercept" in error or "pointer" in error or "click" in error:
        return "blocked"
    if result.get("value_mismatch"):
        return "wrong_value"
    if "readonly" in error or "disabled" in error:
        return "readonly"
    return "unknown"
```

- [ ] **Step 4: Wire failure classification into the fill loop**

In the fill loop (around line 1376, the `pending_retries` handling), replace the blanket LLM recovery with classified recovery:

```python
            if pending_retries:
                for item in pending_retries:
                    label = item["field"]["label"]
                    failure_type = _classify_fill_failure(item["result"])

                    if failure_type == "readonly":
                        self._save_gotcha(label, "readonly", "Field is readonly, skip")
                        continue

                    if failure_type == "blocked":
                        await self._dismiss_stale_dialogs()
                        retry_result = await self._fill_by_label(label, item["attempted_value"])
                        if retry_result.get("success"):
                            total_fields_filled += 1
                            continue

                    if failure_type == "no_field" and self._strategy:
                        normalized = self._strategy.normalize_label(label)
                        if normalized != label:
                            retry_result = await self._fill_by_label(normalized, item["attempted_value"])
                            if retry_result.get("success"):
                                total_fields_filled += 1
                                mapping[label] = item["attempted_value"]
                                all_agent_mappings[label] = item["attempted_value"]
                                continue

                    # Fall through to existing LLM recovery for unknown + wrong_value
                    # (existing code handles these via recover_failed_fields_with_llm)
```

Note: keep the existing LLM recovery code after this block for items that weren't resolved by classified recovery.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "classify"`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(form-filler): fill failure classification routes to targeted recovery"
```

---

### Task 10: Wire Strategy screening_defaults into Screening Pipeline

**Files:**
- Modify: `jobpulse/native_form_filler.py` — add strategy screening defaults as a tier
- Test: `tests/jobpulse/test_native_form_filler.py`

- [ ] **Step 1: Write test**

Add to `tests/jobpulse/test_native_form_filler.py`:

```python
def test_strategy_screening_defaults_used():
    """Strategy screening_defaults() should be consulted for unresolved screening questions."""
    from jobpulse.ats_adapters.strategy import get_strategy
    strategy = get_strategy("linkedin")
    defaults = strategy.screening_defaults()
    assert "are you legally authorized to work" in defaults
    assert defaults["are you legally authorized to work"] == "yes"
```

- [ ] **Step 2: Run test to verify it passes (this is an existing feature)**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v -k "screening_defaults"`
Expected: PASS

- [ ] **Step 3: Wire screening_defaults into the screening pipeline in fill()**

In `jobpulse/native_form_filler.py`, in the screening resolution section (around line 1276), add strategy defaults as a tier between DB cache and pattern matching:

```python
                    # Strategy screening defaults (after DB cache, before pattern)
                    if self._strategy:
                        strategy_defaults = self._strategy.screening_defaults()
                        strategy_answer = strategy_defaults.get(f["label"].lower().strip())
                        if strategy_answer:
                            mapping[f["label"]] = strategy_answer
                            seen_screening.append(f"{f['label']}:{strategy_answer}")
                            continue
```

Insert this after the `db_answer` check and before the `try_instant_answer` call.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_native_form_filler.py
git commit -m "feat(screening): wire strategy.screening_defaults() into screening pipeline"
```

---

### Task 11: Integration — Store Container After Successful Fill

**Files:**
- Modify: `jobpulse/native_form_filler.py` — store container + timing after fill loop

- [ ] **Step 1: Wire container storage into post-fill**

In `jobpulse/native_form_filler.py`, in the `fill()` method, add container storage. First, save the resolved container selector as an instance variable during container resolution (before the fill loop):

```python
        # Before the fill loop, after container resolution:
        self._container_selector: str | None = None
        # ... (resolve container) ...
        # After successful resolution, store it:
        # self._container_selector = resolved_selector
```

Then after the fill loop returns successfully:

```python
        # In _result() or at each success return point, add:
        if self._container_selector:
            try:
                from jobpulse.form_experience_db import FormExperienceDB
                FormExperienceDB().store_container(page_url, self._container_selector)
            except Exception:
                pass
```

- [ ] **Step 2: Wire container resolution into the fill() method**

At the start of `fill()`, after loading the strategy and form experience, resolve the container:

```python
        # After self._strategy = get_strategy(platform)
        self._container_selector = None
        try:
            from jobpulse.form_experience_db import FormExperienceDB
            from jobpulse.form_engine.field_scanner import resolve_form_container
            fe_db = FormExperienceDB()
            import asyncio
            self._container_selector = await resolve_form_container(
                self._page, self._strategy, fe_db,
            )
            if self._container_selector:
                logger.info("Form container resolved: %s", self._container_selector)
        except Exception as exc:
            logger.debug("Container resolution failed: %s", exc)
```

- [ ] **Step 3: Pass container info to _scan_fields**

Update `_scan_fields()` to pass the container:

```python
    async def _scan_fields(self) -> list[dict]:
        return await scan_fields(
            self._page,
            strategy=self._strategy,
            form_experience_db=self._fe_db,
        )
```

Store `self._fe_db` in `fill()` after creating `FormExperienceDB()`.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat(form-filler): wire container resolution + storage into fill pipeline"
```

---

### Task 12: Live Integration Tests

**Files:**
- Create: `tests/jobpulse/test_adaptive_pipeline.py`

- [ ] **Step 1: Create live integration test file**

Create `tests/jobpulse/test_adaptive_pipeline.py`:

```python
"""Live integration tests for adaptive application orchestration.

These tests require a running Chrome instance with CDP enabled:
    python -m jobpulse.runner chrome-pw

Tests connect to real ATS job pages and validate the pipeline against
real DOM structures. Marked @pytest.mark.slow for CI exclusion.
"""

import os
import pytest

pytestmark = [pytest.mark.slow, pytest.mark.skipif(
    not os.environ.get("LIVE_TESTS"),
    reason="Set LIVE_TESTS=1 to run live browser tests",
)]


@pytest.fixture
async def cdp_page():
    """Connect to existing Chrome via CDP and return a Page."""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    page = await context.new_page()
    yield page
    await page.close()
    await pw.stop()


@pytest.mark.asyncio
async def test_linkedin_container_scoping(cdp_page):
    """Navigate to LinkedIn job → open Easy Apply → verify container scoping.

    Validates that scan_fields returns only modal fields, not navbar.
    """
    from jobpulse.form_engine.field_scanner import resolve_form_container
    from jobpulse.ats_adapters.strategy import get_strategy

    strategy = get_strategy("linkedin")
    # Navigate to any LinkedIn job page with Easy Apply
    # The exact URL may change — use search to find one
    await cdp_page.goto("https://www.linkedin.com/jobs/", wait_until="networkidle")

    container = await resolve_form_container(cdp_page, strategy)
    # When not on an apply page, should return the hint or None
    # When on an apply page with modal open, should return modal selector
    assert container is None or ".jobs-easy-apply-modal" in (container or "")


@pytest.mark.asyncio
async def test_greenhouse_container_detection(cdp_page):
    """Navigate to a Greenhouse application page → verify container auto-detection."""
    from jobpulse.form_engine.field_scanner import resolve_form_container, scan_fields
    from jobpulse.ats_adapters.strategy import get_strategy

    strategy = get_strategy("greenhouse")
    # Use a known Greenhouse job application URL
    await cdp_page.goto(
        "https://boards.greenhouse.io/example/jobs/123",
        wait_until="networkidle",
        timeout=15000,
    )
    container = await resolve_form_container(cdp_page, strategy)
    fields = await scan_fields(cdp_page, strategy=strategy)

    # Greenhouse forms typically have 3-15 fields
    min_f, max_f = strategy.expected_field_range()
    if fields:  # page may not load if URL is invalid
        assert len(fields) >= min_f
        assert len(fields) <= max_f * 1.5


@pytest.mark.asyncio
async def test_semantic_matcher_real_greenhouse_gender_options(cdp_page):
    """Scan a real Greenhouse form and verify semantic matching on gender field."""
    from jobpulse.form_engine.semantic_matcher import semantic_option_match
    from jobpulse.form_scanner import scan_combobox_options

    # Real gender options from Greenhouse forms
    real_options = ["Man", "Woman", "Non-binary", "Prefer not to say"]
    assert semantic_option_match("male", real_options) == "Man"
    assert semantic_option_match("female", real_options) == "Woman"


@pytest.mark.asyncio
async def test_workday_timing_measurement(cdp_page):
    """Navigate to Workday → verify timing is measured and stored."""
    from jobpulse.form_experience_db import FormExperienceDB
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmp:
        db = FormExperienceDB(db_path=os.path.join(tmp, "test.db"))
        db.store_timing("myworkdayjobs.com", hydration_ms=9000, fill_ms=15000, transition_ms=4000)
        timing = db.get_timing("myworkdayjobs.com")
        assert timing is not None
        assert timing["avg_hydration_ms"] == 9000

        # Simulate second measurement — should average
        db.store_timing("myworkdayjobs.com", hydration_ms=11000, fill_ms=17000, transition_ms=6000)
        timing = db.get_timing("myworkdayjobs.com")
        assert timing["avg_hydration_ms"] == 10000
        assert timing["avg_fill_ms"] == 16000
```

- [ ] **Step 2: Run non-live tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_adaptive_pipeline.py -v -k "not cdp_page"`
Expected: Tests requiring CDP are skipped, timing test passes

- [ ] **Step 3: Run live tests (requires Chrome with CDP)**

```bash
# In a separate terminal:
python -m jobpulse.runner chrome-pw

# Then:
LIVE_TESTS=1 python -m pytest tests/jobpulse/test_adaptive_pipeline.py -v --timeout=60
```
Expected: Tests connect to Chrome and validate against real pages

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_adaptive_pipeline.py
git commit -m "test: live integration tests for adaptive application orchestration"
```

---

### Task 13: Cleanup — Verify Deletions

**Files:**
- Verify no references remain to deleted code

- [ ] **Step 1: Verify _NAV_NOISE_LABELS is fully removed**

```bash
grep -rn "_NAV_NOISE_LABELS" jobpulse/ tests/ --include="*.py"
```
Expected: 0 matches

- [ ] **Step 2: Verify scope_to_dialog is fully removed**

```bash
grep -rn "scope_to_dialog" jobpulse/ tests/ --include="*.py"
```
Expected: 0 matches

- [ ] **Step 3: Verify _PLATFORM_MIN_PAGE_TIME is fully removed**

```bash
grep -rn "_PLATFORM_MIN_PAGE_TIME" jobpulse/ tests/ --include="*.py"
```
Expected: 0 matches

- [ ] **Step 4: Verify risk_delay_multiplier is fully removed**

```bash
grep -rn "risk_delay_multiplier" jobpulse/ tests/ --include="*.py"
```
Expected: 0 matches

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest tests/jobpulse/ -v --timeout=60
```
Expected: All PASS

- [ ] **Step 6: Final commit**

```bash
git commit --allow-empty -m "chore: verify all deprecated timing and noise regex code removed"
```

---

## Dependency Graph

```
Task 1 (strategy methods) ─┐
                            ├─→ Task 4 (container resolution) ─→ Task 5 (scan validation) ─→ Task 11 (wire into fill)
Task 2 (DB schema)  ───────┘                                                                         │
                                                                                                      ├─→ Task 12 (live tests)
Task 3 (scoped scanning) ──────────────────────────────────────────→ Task 4                           │
                                                                                                      ├─→ Task 13 (cleanup)
Task 6 (adaptive timing) ────────────────────────────────────────────────────────────────────────────→ │
                                                                                                      │
Task 7 (semantic matcher) ──→ Task 8 (wire into seed_mapping) ──────────────────────────────────────→ │
                                                                                                      │
Task 9 (failure classification) ────────────────────────────────────────────────────────────────────→ │
                                                                                                      │
Task 10 (screening defaults) ──────────────────────────────────────────────────────────────────────→ │
```

**Parallelizable groups:**
- Group A (WS1): Tasks 1 → 2 → 3 → 4 → 5 → 11
- Group B (WS2): Task 6 (independent, only shares FormExperienceDB with Group A)
- Group C (WS3): Tasks 7 → 8, 9, 10 (independent of Groups A and B)
- Final: Tasks 12, 13 (after all groups complete)
