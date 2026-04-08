# Engine A/B Testing — Extension vs Playwright

## Goal

Compare the Chrome extension engine and Playwright engine head-to-head on real job applications. Both engines use the same ApplicationOrchestrator (shared page detection, field mapping, screening answers, navigation learning). Only the DOM interaction layer differs. Per-engine learning (Ralph Loop fixes, gotchas) is tracked independently so we can measure which engine improves faster over time.

## Motivation

The Ralph Loop testing phase has been running but the code isn't improving meaningfully. Failures span all categories — fields not found, values not sticking, validation errors, navigation failures. Without a controlled comparison, we can't tell if the problem is the fill mechanism, the orchestration logic, or the ATS sites themselves. A/B testing with equivalent engines isolates the fill mechanism as the variable.

## Architecture: Driver Swap Pattern

The ApplicationOrchestrator currently calls `self.bridge.fill()`, `self.bridge.click()`, etc. We rename `self.bridge` to `self.driver` and accept either an `ExtensionBridge` or a `PlaywrightDriver` — both implement the same `DriverProtocol`.

```
ApplicationOrchestrator
  └── self.driver  (was self.bridge)
        ├── ExtensionBridge    ← existing, no changes to internals
        └── PlaywrightDriver   ← NEW, same methods, Playwright native API
              └── connects via CDP to real Chrome (separate profile)

TrackedDriver(inner_driver)  ← wraps either driver, logs every call to SQLite
```

The orchestrator doesn't know which driver it's using. Everything above the driver layer is shared. Everything at the driver layer is independent.

Note: `ext_adapter.py` continues to use `ExtensionBridge` directly for the extension-driven pipeline (scanning, phase engine). The driver swap only affects `ApplicationOrchestrator` — the form-filling lifecycle.

## DriverProtocol

Both drivers implement:

```python
class DriverProtocol(Protocol):
    async def navigate(self, url: str) -> dict
    async def fill(self, selector: str, value: str) -> dict
    async def click(self, selector: str) -> dict
    async def select_option(self, selector: str, value: str) -> dict
    async def check_box(self, selector: str, checked: bool) -> dict
    async def fill_radio(self, selector: str, value: str) -> dict
    async def fill_date(self, selector: str, value: str) -> dict
    async def fill_autocomplete(self, selector: str, value: str) -> dict
    async def fill_contenteditable(self, selector: str, value: str) -> dict
    async def upload_file(self, selector: str, path: str) -> dict
    async def screenshot(self) -> dict
    async def get_snapshot(self) -> dict
    async def scan_validation_errors(self) -> dict
    async def close(self) -> None
```

All methods return `dict` with at minimum `{success: bool}`. Fill methods additionally return `{value_set, value_verified, retry_count}`.

## PlaywrightDriver

Connects to a real Chrome instance via CDP (`http://localhost:9222`). Uses a separate Chrome profile from the extension — has its own cookies, saved logins, and browser fingerprint. No automation flags (`navigator.webdriver` is false).

### Chrome Setup

Launch Chrome with remote debugging:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.chrome-playwright-profile" \
  --no-first-run
```

One-time: log into ATS platforms manually. Sessions persist in the profile directory.

Runner integration: `python -m jobpulse.runner chrome-pw` launches this Chrome in the background.

### Connection Lifecycle

1. `PlaywrightDriver.__init__()` calls `playwright.chromium.connect_over_cdp("http://localhost:9222")`
2. If connection fails, raises clear error: "Start Chrome with: python -m jobpulse.runner chrome-pw"
3. Uses existing browser context (`browser.contexts[0]`) — the logged-in profile
4. Opens a new tab per application, closes after completion

### Fill Implementation (Playwright Native API + Enhancements)

| Method | Implementation |
|---|---|
| `fill()` | `el.fill()` + read-back verification via `el.evaluate("el => el.value")` + retry wrapper (max 2) |
| `click()` | Bezier curve computed in Python, intermediate points fed to `page.mouse.move()` with ease-in-out, then `page.mouse.click()` |
| `select_option()` | `page.select_option()` + verify `selectedIndex` matches intended value |
| `fill_autocomplete()` | `el.type(text, delay=80)` char-by-char + `page.wait_for_timeout(1500)` for suggestions + click matching option |
| `fill_contenteditable()` | `page.evaluate("document.execCommand('insertText', false, char)")` per character |
| `fill_date()` | `el.fill()` for native `input[type=date]`, `el.type()` for text-based + format auto-detection from placeholder |
| `scan_validation_errors()` | `page.evaluate()` with same 5-strategy JS: aria-invalid, role=alert, error CSS classes, aria-errormessage, ATS-specific selectors |
| `screenshot()` | `page.screenshot()` — native Playwright |
| `get_snapshot()` | `page.evaluate()` scanning DOM for form fields — same snapshot shape as extension |

### Human-Like Enhancements (built into every fill)

- **Smart field gap**: `asyncio.sleep(get_field_gap(label_text))` — 300-1700ms based on label length
- **Scroll-aware timing**: Measure element position before/after `scroll_into_view_if_needed()`, wait proportionally (50-800ms)
- **Bezier mouse curves**: Randomized cubic Bezier with perpendicular curvature (30-80px), slight overshoot, ease-in-out timing
- **Post-fill verification**: Read back `el.value` after every fill, set `value_verified` in result
- **Retry wrapper**: Max 2 retries on transient errors (element not found after scroll, no options loaded), 500ms delay between retries

## Per-Engine Learning

### Problem

With a shared orchestrator, both engines would share the same Ralph Loop fixes and gotchas. A fix learned by Playwright might not work for the extension and vice versa. We can't measure which engine is learning faster if learning is pooled.

### Solution: Engine-Tagged Learning

**Ralph Loop PatternStore** (`data/ralph_patterns.db`): Add `engine TEXT NOT NULL DEFAULT 'extension'` column. When storing and loading fixes, filter by engine:

```python
fixes = store.get_fixes(platform, step_name, error_sig, engine="playwright")
store.store_fix(fix, engine="playwright")
```

**GotchasDB** (`data/form_gotchas.db`): Add `engine TEXT NOT NULL DEFAULT 'extension'` column. Same filter pattern.

**Navigation learning**: Shared (engine-agnostic). Both engines navigate the same URL sequences.

**Screening answers**: Shared (engine-agnostic). Same questions, same answers.

### What stays shared vs engine-specific:

| Component | Scope |
|---|---|
| Page detection | Shared |
| Field mapping | Shared |
| Screening answers | Shared |
| Navigation sequences | Shared |
| CV/CL generation | Shared |
| Ralph Loop fixes | Per-engine |
| Gotchas | Per-engine |
| TrackedDriver metrics | Per-engine |

## TrackedDriver + A/B Tracking

### TrackedDriver Wrapper

Wraps any `DriverProtocol` implementation. Logs every call to SQLite before and after execution.

```python
class TrackedDriver:
    def __init__(self, inner: DriverProtocol, engine: str, application_id: str):
        self._inner = inner
        self._engine = engine
        self._app_id = application_id
        self._tracker = ABTracker()

    async def fill(self, selector, value):
        start = time.monotonic()
        result = await self._inner.fill(selector, value)
        self._tracker.log_field(
            application_id=self._app_id, engine=self._engine,
            action="fill", selector=selector, value_attempted=value,
            success=result.get("success"), value_verified=result.get("value_verified"),
            duration_ms=int((time.monotonic() - start) * 1000),
            error=result.get("error"), retry_count=result.get("retry_count", 0),
        )
        return result
```

### SQLite Schema — `data/ab_engine_tracking.db`

**`field_events`** — one row per fill/click/select attempt:

```sql
CREATE TABLE field_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id          TEXT NOT NULL,
    engine          TEXT NOT NULL,
    platform        TEXT,
    action          TEXT NOT NULL,
    selector        TEXT,
    success         BOOLEAN NOT NULL,
    value_verified  BOOLEAN,
    retry_count     INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    error           TEXT,
    created_at      TEXT NOT NULL
);
```

**`application_outcomes`** — one row per application attempt:

```sql
CREATE TABLE application_outcomes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id              TEXT NOT NULL UNIQUE,
    engine              TEXT NOT NULL,
    platform            TEXT,
    domain              TEXT,
    total_fields        INTEGER DEFAULT 0,
    fields_filled       INTEGER DEFAULT 0,
    fields_verified     INTEGER DEFAULT 0,
    validation_errors   INTEGER DEFAULT 0,
    outcome             TEXT,
    total_duration_s    REAL,
    pages_navigated     INTEGER DEFAULT 0,
    fixes_applied       INTEGER DEFAULT 0,
    fixes_learned       INTEGER DEFAULT 0,
    created_at          TEXT NOT NULL
);
```

**`engine_learning`** — daily snapshot of cumulative learning:

```sql
CREATE TABLE engine_learning (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    engine              TEXT NOT NULL,
    date                TEXT NOT NULL,
    applications        INTEGER DEFAULT 0,
    first_try_success   INTEGER DEFAULT 0,
    total_fixes         INTEGER DEFAULT 0,
    fix_success_rate    REAL,
    gotcha_count        INTEGER DEFAULT 0,
    UNIQUE(engine, date)
);
```

## Telegram Manual Toggle

### Approve with engine selection

```
approve 3                  → default engine (APPLICATION_ENGINE env var)
approve 3 playwright       → Playwright for this job
approve 3 pw               → shorthand
approve 3 ext              → explicitly extension
```

### Dashboard commands

| Command | Output |
|---|---|
| `job engine stats` | Side-by-side: fill rate, verify rate, submit rate, avg time, top failures per engine |
| `job engine compare <platform>` | Per-platform field-type breakdown for both engines |
| `job engine learning` | Learning curve: first-try rate over time, fix accumulation, gotcha coverage |
| `job engine reset` | Clear tracking data for fresh experiment |

### Stats output format

```
Engine A/B Results (last 7 days)

Extension (12 applications):
  Fill success: 94.2% (195/207 fields)
  Values stuck: 87.3% (170/195 verified)
  Submit success: 75% (9/12)
  Avg time: 4m 12s
  Top failures: autocomplete (5), custom_select (3)

Playwright (8 applications):
  Fill success: 91.8% (157/171 fields)
  Values stuck: 89.2% (140/157 verified)
  Submit success: 62.5% (5/8)
  Avg time: 3m 45s
  Top failures: contenteditable (4), date (2)

Per-platform:
  Greenhouse: ext 3/4 | pw 2/3
  Lever:      ext 2/2 | pw 1/2
  Workday:    ext 2/3 | pw 1/1
```

### Learning curve output

```
Engine Learning (last 30 days)

Extension:
  Fixes learned: 23 (14 confirmed, 9 pending)
  Fix success rate: 72%
  First-try rate: 48% -> 61% (improving)
  Gotchas: 17 domains covered

Playwright:
  Fixes learned: 18 (12 confirmed, 6 pending)
  Fix success rate: 78%
  First-try rate: 52% -> 70% (improving)
  Gotchas: 14 domains covered
```

## Engine Routing Flow

```
Telegram "approve 3 pw"
  -> job_autopilot.approve_jobs(args, engine="playwright")
    -> ralph_apply_sync(..., engine="playwright")
      -> ApplicationOrchestrator(driver=TrackedDriver(PlaywrightDriver(), "playwright", app_id))
        -> driver.fill() -> Playwright el.fill() + verify + retry + Bezier mouse
        -> TrackedDriver logs to ab_engine_tracking.db
        -> Ralph Loop fixes tagged with engine="playwright"
        -> Gotchas tagged with engine="playwright"
      -> outcome logged to application_outcomes table

Telegram "job engine stats"
  -> dispatcher -> ab_dashboard.engine_stats()
    -> ABTracker.compare() -> side-by-side stats from SQLite
```

## Env Vars

```bash
APPLICATION_ENGINE=extension       # default engine (unchanged)
APPLICATION_ENGINE=playwright      # switch default globally
PLAYWRIGHT_CDP_URL=http://localhost:9222  # CDP endpoint (default)
```

Telegram toggle always overrides the env var for that specific application.

## File Map

### New files:

| File | Purpose | Est. lines |
|---|---|---|
| `jobpulse/driver_protocol.py` | `DriverProtocol` Protocol class | ~40 |
| `jobpulse/playwright_driver.py` | `PlaywrightDriver` — CDP connect + native API + enhancements | ~350 |
| `jobpulse/tracked_driver.py` | `TrackedDriver` wrapper + `ABTracker` SQLite logger | ~200 |
| `jobpulse/ab_dashboard.py` | Telegram command handlers for engine stats/compare/learning | ~150 |

### Modified files:

| File | Change |
|---|---|
| `jobpulse/application_orchestrator.py` | `self.bridge` -> `self.driver`, accept `engine` param |
| `jobpulse/ralph_loop/pattern_store.py` | Add `engine` column, filter fixes by engine |
| `jobpulse/form_engine/gotchas.py` | Add `engine` column, filter gotchas by engine |
| `jobpulse/ralph_loop/loop.py` | Pass `engine` through to PatternStore and GotchasDB |
| `jobpulse/applicator.py` | Accept `engine` param, pass to orchestrator |
| `jobpulse/job_autopilot.py` | Parse engine from approve args, pass through pipeline |
| `jobpulse/dispatcher.py` | Route `engine stats/compare/learning` intents |
| `jobpulse/swarm_dispatcher.py` | Same routes (dual dispatcher invariant) |
| `jobpulse/runner.py` | Add `chrome-pw` subcommand |
| `jobpulse/form_engine/models.py` | Add `value_verified: bool = False` to FillResult |
| `jobpulse/form_engine/text_filler.py` | Post-fill verification, retry wrapper, smart timing |
| `jobpulse/form_engine/select_filler.py` | Verification, retry |
| `jobpulse/form_engine/checkbox_filler.py` | `value_verified` on returns |
| `jobpulse/form_engine/radio_filler.py` | `value_verified` on returns |
| `jobpulse/form_engine/date_filler.py` | `value_verified` on returns |
| `jobpulse/form_engine/multi_select_filler.py` | `value_verified` on returns |
| `jobpulse/form_engine/validation.py` | Add 3 missing strategies: error CSS classes, aria-errormessage, ATS-specific selectors |

### Unchanged:

- Extension code (`content.js`, `background.js`) — already has all features
- Page detection, field mapping, screening answers — shared, engine-agnostic
- Navigation learning — shared
- CV/CL generation — not engine-related

## Dependencies

- `playwright` Python package (async API)
- Chrome installed with a second profile for Playwright CDP
- No new LLM costs — all enhancements are deterministic
