# Browser Intelligence — Real-Time Form Signal Capture & Interpretation

**Date:** 2026-04-30
**Status:** Approved
**Scope:** New subsystem wired into existing form-filling pipeline

## Problem

When a form field rejects our input, we don't know why. The current pipeline:
1. Fills a field
2. Checks if the value stuck (readback verification)
3. If it didn't: classifies failure by error string pattern matching
4. Escalates: LLM recovery ($0.002, 1-3s) → vision ($0.003, 2-5s) → abandon

The form often tells us exactly what went wrong — via console errors, HTTP 422 response bodies, and DOM validation elements — but we're not listening. We guess instead of reading what the browser already knows.

## Solution

A passive signal capture layer that listens to browser events during form filling, interprets them into corrective actions, and applies fixes before escalating to expensive LLM/vision fallbacks.

## Architecture

### New Files

```
jobpulse/browser_intelligence.py   — Capture layer (listeners + ring buffer)
jobpulse/signal_interpreter.py     — Interpretation layer (filter + classify + associate + act)
tests/jobpulse/test_browser_intelligence.py — Unit + live tests
```

### Integration Points (existing files modified)

```
jobpulse/playwright_driver.py      — attach() on connect
jobpulse/native_form_filler.py     — _check_browser_signals() in fill loop
jobpulse/form_experience_db.py     — signal_corrections table
```

## Component 1: BrowserIntelligence (capture layer)

### API

```python
class BrowserIntelligence:
    async def attach(self, page: Page) -> None
    def get_signals(self, since_ms: float | None = None) -> list[CapturedSignal]
    def clear(self) -> None
    async def detach(self) -> None
```

### Signal Sources

| Source | Playwright API | What it captures |
|--------|---------------|-----------------|
| Console errors | `page.on("console")` | JS validation errors, React warnings |
| Network errors | `page.on("response")` | HTTP 400/422 responses with JSON error bodies |
| DOM mutations | `page.evaluate()` MutationObserver injection | `role="alert"` elements, `aria-invalid` changes, `.error` class elements |
| Browser logs | CDP `Log.entryAdded` | Browser-level errors Playwright doesn't surface |

### CapturedSignal

```python
@dataclass
class CapturedSignal:
    source: str          # "console", "network", "mutation", "browser_log"
    level: str           # "error", "warning", "info"
    text: str            # the error message
    timestamp_ms: float  # time.monotonic() * 1000 at capture
    url: str             # page URL or request URL
    metadata: dict       # source-specific: {status_code, response_body, element_selector, ...}
```

### Ring Buffer

- Max 50 signals (FIFO eviction)
- `clear()` called between form pages
- `get_signals(since_ms)` filters by timestamp

### Console Listener

```python
def _on_console(self, msg: ConsoleMessage) -> None:
    if msg.type not in ("error", "warning"):
        return
    text = msg.text
    # Drop known noise patterns
    if any(p in text for p in self._NOISE_PATTERNS):
        return
    self._buffer.append(CapturedSignal(
        source="console", level=msg.type, text=text,
        timestamp_ms=time.monotonic() * 1000,
        url=self._page.url, metadata={"location": msg.location},
    ))
```

Noise patterns (dropped):
- React dev warnings: "Each child in a list", "Warning: Failed prop type"
- HMR/webpack: "[HMR]", "[WDS]", "webpack"
- Analytics: "gtag", "analytics", "fbq", "hotjar"
- Deprecation: "deprecated", "will be removed"

### Network Listener

```python
def _on_response(self, response: Response) -> None:
    if response.request.method not in ("POST", "PUT", "PATCH"):
        return
    if response.status < 400:
        return
    try:
        body = response.text()
    except Exception:
        body = ""
    self._buffer.append(CapturedSignal(
        source="network", level="error", text=body[:2000],
        timestamp_ms=time.monotonic() * 1000,
        url=response.url,
        metadata={"status_code": response.status, "method": response.request.method},
    ))
```

### MutationObserver Injection

Injected once per page via `page.evaluate()`. Watches for:
- Elements added with `role="alert"`
- `aria-invalid` attribute changes to `"true"`
- Elements added with class containing `error`, `invalid`, `validation`

Stored in `window.__bi_errors` array, polled via `page.evaluate("() => window.__bi_errors")`.

### CDP Log Listener

```python
async def _setup_cdp_log(self) -> None:
    self._cdp = await self._page.context.new_cdp_session(self._page)
    await self._cdp.send("Log.enable")
    self._cdp.on("Log.entryAdded", self._on_log_entry)

def _on_log_entry(self, params: dict) -> None:
    entry = params["entry"]
    if entry["level"] not in ("error", "warning"):
        return
    self._buffer.append(CapturedSignal(
        source="browser_log", level=entry["level"], text=entry["text"],
        timestamp_ms=time.monotonic() * 1000,
        url=entry.get("url", ""), metadata={},
    ))
```

## Component 2: SignalInterpreter (interpretation layer)

### API

```python
class SignalInterpreter:
    async def check_after_fill(
        self, intelligence: BrowserIntelligence,
        field_label: str, field_locator: Locator,
        fill_timestamp_ms: float, page: Page,
    ) -> CorrectionAction | None

    async def check_after_submit(
        self, intelligence: BrowserIntelligence,
        page: Page,
    ) -> list[SubmissionError]

    async def verify_correction(
        self, field_locator: Locator, page: Page,
    ) -> bool
```

### CorrectionAction

```python
@dataclass
class CorrectionAction:
    signal_type: str        # FORMAT_ERROR, REQUIRED_FIELD, etc.
    field_label: str        # associated field
    error_message: str      # raw error text
    suggested_value: str | None  # corrected value if deterministic
    transform: str          # "prepend_country_code", "strip_non_numeric", etc.
    confidence: float       # 0.0-1.0
```

### Signal Types

```python
class SignalType(str, Enum):
    FORMAT_ERROR = "format_error"
    REQUIRED_FIELD = "required_field"
    DUPLICATE = "duplicate"
    RANGE_ERROR = "range_error"
    TYPE_MISMATCH = "type_mismatch"
    OPTION_INVALID = "option_invalid"
    SUBMISSION_BLOCKED = "submission_blocked"
    UNKNOWN = "unknown"
```

### Three-Gate Verification Pipeline

**Gate 1 — Temporal correlation:**
- Signal must fire within 2000ms of the fill timestamp
- Signals older than fill = stale page-load noise → discard

**Gate 2 — DOM cross-check:**
- `aria-invalid="true"` on the field? → confirmed
- Visible error element near the field (sibling/child with error class or `role="alert"`)? → confirmed
- Field value cleared/reverted? → confirmed
- 0 of 3 = discard signal as noise

**Gate 3 — Field association:**
- Strategy 1 (temporal): we just filled field X, error fired 50ms later → field X
- Strategy 2 (DOM proximity): error element is inside same `.form-group`/`.field-wrapper` as the input
- Strategy 3 (text matching): error message contains field label → use semantic_matcher
- Strategy 4 (network field mapping): JSON key → normalize → match form label
- Can't associate → discard

### Classification (keyword tiers, no LLM)

**Tier 1 — Exact phrases (instant):**
```python
EXACT_RULES = {
    "is required": SignalType.REQUIRED_FIELD,
    "cannot be blank": SignalType.REQUIRED_FIELD,
    "already registered": SignalType.DUPLICATE,
    "already exists": SignalType.DUPLICATE,
    "please select": SignalType.OPTION_INVALID,
    "select a valid": SignalType.OPTION_INVALID,
}
```

**Tier 2 — Keyword clusters (instant):**
```python
KEYWORD_RULES = [
    ({"format", "must be", "invalid"}, {"phone", "email", "date", "url"}, SignalType.FORMAT_ERROR),
    ({"minimum", "maximum", "between", "at least", "no more"}, set(), SignalType.RANGE_ERROR),
    ({"number", "numeric", "integer", "decimal"}, set(), SignalType.TYPE_MISMATCH),
    ({"fix errors", "complete required", "review your"}, set(), SignalType.SUBMISSION_BLOCKED),
]
```

**Tier 3 — LLM fallback (~5% of cases):**
Only if tiers 1-2 return UNKNOWN. Via `cognitive_llm_call(domain="signal_classification", stakes="low")`.

### Deterministic Correction Transforms

```python
TRANSFORMS = {
    "prepend_country_code": lambda v: "+44" + v.lstrip("0") if v.startswith("0") else v,
    "strip_non_numeric": lambda v: re.sub(r"[^\d]", "", v),
    "strip_currency": lambda v: re.sub(r"[£$€,]", "", v),
    "to_international_date": lambda v: _parse_date_to_iso(v),
    "lowercase_email": lambda v: v.lower().strip(),
    "strip_whitespace": lambda v: v.strip(),
}
```

Applied based on signal_type + field context. No LLM needed.

### Post-Correction Verification

After applying a correction:
1. Check `aria-invalid` gone or `"false"`
2. Check error element removed or hidden
3. Check field value matches what we set
4. If all pass → store correction in learning DB
5. If any fail → don't store, fall through to existing LLM/vision path

## Component 3: FormExperienceDB Extension

### New Table: signal_corrections

```sql
CREATE TABLE IF NOT EXISTS signal_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    field_label TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    error_message TEXT NOT NULL,
    original_value TEXT NOT NULL,
    corrected_value TEXT NOT NULL,
    transform TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sc_domain_field ON signal_corrections(domain, field_label);
```

### New Methods

```python
def store_signal_correction(self, domain, field_label, signal_type, error_message,
                            original_value, corrected_value, transform) -> None

def get_signal_corrections(self, domain, field_label=None) -> list[dict]
```

### Pre-fill Transform Lookup

Before filling a field, NativeFormFiller checks:
```python
corrections = self._fe_db.get_signal_corrections(domain, field_label)
if corrections:
    # Apply the most recent successful transform
    value = TRANSFORMS[corrections[0]["transform"]](value)
```

## Integration: PlaywrightDriver

In `connect()`, after page is resolved:
```python
self._intelligence = BrowserIntelligence()
await self._intelligence.attach(self._page)
```

Expose as property: `driver.intelligence -> BrowserIntelligence`

## Integration: NativeFormFiller

### New method: _check_browser_signals()

```python
async def _check_browser_signals(self, field_label, field_locator, fill_timestamp_ms):
    if not self._intelligence:
        return None
    return await self._interpreter.check_after_fill(
        self._intelligence, field_label, field_locator, fill_timestamp_ms, self._page,
    )
```

### Modified fill loop (in fill(), after _fill_by_label)

```python
# After each _fill_by_label that fails:
if not result.get("success") or result.get("value_mismatch"):
    action = await self._check_browser_signals(label, locator, fill_ts)
    if action and action.suggested_value:
        # Apply correction
        retry = await self._fill_by_label(label, action.suggested_value)
        if retry.get("success"):
            verified = await self._interpreter.verify_correction(locator, self._page)
            if verified:
                self._fe_db.store_signal_correction(...)
                total_fields_filled += 1
                continue  # skip adding to pending_retries
    # Fall through to existing pending_retries + LLM/vision path
```

### Pre-fill transform (before _fill_by_label)

```python
# Before filling, check for known corrections on this domain
if self._fe_db:
    prior = self._fe_db.get_signal_corrections(domain, label)
    if prior:
        value_text = TRANSFORMS.get(prior[0]["transform"], lambda v: v)(value_text)
```

### Signal buffer cleared between pages

```python
# At top of page loop:
if self._intelligence:
    self._intelligence.clear()
```

## Integration: CorrectionCapture Chain

Successful signal corrections emit to existing learning systems:
1. `CorrectionCapture.record_corrections()` — with source="browser_signal"
2. `OptimizationEngine` — "correction" signal with signal_type metadata
3. `AgentRulesDB` — stored as auto-correction rule for field_label on domain

## Testing

### Unit Tests (test_browser_intelligence.py)

1. **Ring buffer**: signals evicted at capacity, clear() empties buffer
2. **Console filter**: noise patterns dropped, validation errors kept
3. **Network filter**: GET/200 dropped, POST/422 kept with body
4. **Temporal gating**: signals before fill timestamp rejected
5. **Classification tiers**: exact phrases → keyword clusters → unknown
6. **Field association**: temporal > DOM proximity > text matching
7. **Correction transforms**: phone format, numeric strip, date conversion
8. **Post-correction verification**: aria-invalid check, error element check
9. **DB storage**: signal_corrections table CRUD
10. **Pre-fill lookup**: prior corrections applied before fill

### Live Integration Tests (@pytest.mark.live)

1. **Console capture on real page**: Load a form page, trigger validation, verify console error captured
2. **Network monitoring**: Submit invalid data, verify 422 response captured
3. **MutationObserver**: Fill invalid value, verify error element detected
4. **Full pipeline**: Fill → signal captured → correction applied → verified → stored in DB

## Latency Impact

| Component | Per-field | Per-page | Session |
|-----------|-----------|----------|---------|
| Console listener setup | — | — | ~1ms |
| Network listener setup | — | — | ~1ms |
| MutationObserver inject | — | ~5ms | — |
| CDP Log.enable | — | — | ~2ms |
| get_signals() check | <1ms | — | — |
| Signal interpretation | <1ms | — | — |
| DOM cross-check | ~3ms | — | — |
| **Total overhead** | **~4ms** | **~5ms** | **~4ms** |
| **Savings (avoided LLM/vision)** | **-1 to -5s** | — | — |

## Non-Goals

- No per-domain configuration
- No changes to a11y tree scanning
- No changes to semantic matching
- No changes to screening pipeline
- No `page.addLocatorHandler()` migration (separate follow-up)
- No Patchright migration (separate follow-up)
