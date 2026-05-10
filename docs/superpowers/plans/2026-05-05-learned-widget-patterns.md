# Per-Domain Learned Widget Patterns

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a human (or `ai_assist_logger` correction) fills a field the scanner missed, capture the field's DOM signature (selectors, parent classes, accessible text). Store the signature in `GotchasDB` keyed by domain. On next visit to the same domain, the scanner consults learned signatures FIRST and returns those fields directly. This converts every manual save into a permanent agent capability.

**Architecture:** Two-sided change.

  1. **Capture side** — extend `ai_assist_logger.record_fix()` to optionally accept a Playwright element handle (or a JS-callable selector probe) and record its `id`/`name`/CSS-path/parent-classes/aria-label as a `widget_pattern` row in `GotchasDB`.
  2. **Apply side** — new scan strategy `_scan_learned_patterns(page, domain)` that queries GotchasDB for the domain, walks each stored selector, and emits a field dict. Inserted as the FIRST scan strategy in `_run_all_strategies_parallel` so domain knowledge wins.

**Tech Stack:** Python · Playwright · `jobpulse.gotchas_db` (existing SQLite) · `jobpulse.ai_assist_logger` · pytest

**Live regression context:** Today's session captured 25 user-confirmed values across JPMC Oracle HCM via `ai_assist_logger`, but those are stored as `screening_answer` (label→value) — NOT widget patterns. So next time we hit a JPMC Oracle HCM form with a different page layout, the scanner has to re-discover every Yes/No widget shape. This plan fills that gap.

---

### Task 1: Extend GotchasDB schema with widget_pattern column

**Files:**
- Modify: `jobpulse/gotchas_db.py`
- Test: `tests/jobpulse/test_gotchas_widget_pattern_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_gotchas_widget_pattern_schema.py
"""GotchasDB stores per-domain widget patterns."""
import sqlite3
import pytest


def test_widget_pattern_columns_exist(tmp_path):
    from jobpulse.gotchas_db import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    cols = [r[1] for r in sqlite3.connect(db.db_path).execute(
        "PRAGMA table_info(widget_patterns)"
    )]
    for required in ("id", "domain", "label", "selector", "widget_type",
                     "ancestor_classes", "aria_label", "captured_at",
                     "fix_count"):
        assert required in cols, f"missing column: {required}"


def test_record_widget_pattern_inserts_row(tmp_path):
    from jobpulse.gotchas_db import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com",
        label="Do you require visa sponsorship in the United Kingdom?",
        selector="div[data-q='visa-sponsorship'] button[role='button']",
        widget_type="custom_select",
        ancestor_classes="styles-module-scss-module__visa-q",
        aria_label="",
    )
    rows = list(sqlite3.connect(db.db_path).execute(
        "SELECT label, widget_type, fix_count FROM widget_patterns"
    ))
    assert len(rows) == 1
    assert rows[0][0] == "Do you require visa sponsorship in the United Kingdom?"
    assert rows[0][1] == "custom_select"
    assert rows[0][2] == 1


def test_record_widget_pattern_increments_fix_count_on_duplicate(tmp_path):
    from jobpulse.gotchas_db import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com",
        label="Visa?",
        selector="#visa",
        widget_type="select",
        ancestor_classes="",
        aria_label="",
    )
    db.record_widget_pattern(
        domain="welovealfa.com",
        label="Visa?",
        selector="#visa",
        widget_type="select",
        ancestor_classes="",
        aria_label="",
    )
    rows = list(sqlite3.connect(db.db_path).execute(
        "SELECT fix_count FROM widget_patterns WHERE domain='welovealfa.com' AND label='Visa?'"
    ))
    assert len(rows) == 1
    assert rows[0][0] == 2


def test_get_widget_patterns_for_domain_returns_list(tmp_path):
    from jobpulse.gotchas_db import GotchasDB
    db = GotchasDB(db_path=str(tmp_path / "g.db"))
    db.record_widget_pattern(
        domain="welovealfa.com", label="A", selector="#a",
        widget_type="text", ancestor_classes="", aria_label="",
    )
    db.record_widget_pattern(
        domain="welovealfa.com", label="B", selector="#b",
        widget_type="select", ancestor_classes="", aria_label="",
    )
    db.record_widget_pattern(
        domain="other.com", label="X", selector="#x",
        widget_type="text", ancestor_classes="", aria_label="",
    )
    patterns = db.get_widget_patterns("welovealfa.com")
    assert len(patterns) == 2
    assert {p["label"] for p in patterns} == {"A", "B"}
```

- [ ] **Step 2: Run tests to verify failure**

```bash
python -m pytest tests/jobpulse/test_gotchas_widget_pattern_schema.py -v
```
Expected: AttributeError on `record_widget_pattern` / `get_widget_patterns`.

- [ ] **Step 3: Add table + methods to GotchasDB**

```python
# Add to jobpulse/gotchas_db.py inside the GotchasDB class.

# Schema bootstrap — call from existing __init__ migrate path.
_WIDGET_PATTERNS_DDL = """
CREATE TABLE IF NOT EXISTS widget_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain          TEXT NOT NULL,
    label           TEXT NOT NULL,
    selector        TEXT NOT NULL,
    widget_type     TEXT NOT NULL,
    ancestor_classes TEXT NOT NULL DEFAULT '',
    aria_label      TEXT NOT NULL DEFAULT '',
    captured_at     TEXT NOT NULL DEFAULT (datetime('now')),
    fix_count       INTEGER NOT NULL DEFAULT 1,
    UNIQUE(domain, label, selector)
);
CREATE INDEX IF NOT EXISTS idx_widget_patterns_domain ON widget_patterns(domain);
"""


def _migrate_widget_patterns(self) -> None:
    """Idempotent migration — safe on existing DBs."""
    with self._connect() as c:
        c.executescript(self._WIDGET_PATTERNS_DDL)


def record_widget_pattern(
    self,
    *,
    domain: str,
    label: str,
    selector: str,
    widget_type: str,
    ancestor_classes: str = "",
    aria_label: str = "",
) -> None:
    """Insert or increment a widget pattern.

    The (domain, label, selector) triple is unique — repeat calls
    bump fix_count instead of duplicating rows. Higher fix_count =
    more confident pattern.
    """
    with self._connect() as c:
        c.execute(
            """INSERT INTO widget_patterns
                 (domain, label, selector, widget_type, ancestor_classes, aria_label)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(domain, label, selector) DO UPDATE SET
                 fix_count = fix_count + 1,
                 captured_at = datetime('now')""",
            (domain, label, selector, widget_type, ancestor_classes, aria_label),
        )


def get_widget_patterns(self, domain: str) -> list[dict]:
    """Return all stored patterns for a domain, ordered by fix_count desc."""
    with self._connect() as c:
        rows = c.execute(
            """SELECT label, selector, widget_type, ancestor_classes,
                      aria_label, fix_count
               FROM widget_patterns
               WHERE domain = ?
               ORDER BY fix_count DESC, captured_at DESC""",
            (domain,),
        ).fetchall()
    return [
        {"label": r[0], "selector": r[1], "widget_type": r[2],
         "ancestor_classes": r[3], "aria_label": r[4], "fix_count": r[5]}
        for r in rows
    ]
```

Then in the GotchasDB `__init__` after the existing schema bootstrap,
call `self._migrate_widget_patterns()`.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_gotchas_widget_pattern_schema.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/gotchas_db.py tests/jobpulse/test_gotchas_widget_pattern_schema.py
git commit -m "feat(gotchas): widget_patterns table for per-domain learned shapes

Schema for capturing DOM signatures of fields the scanner missed but
human/ai_assist filled. Keyed (domain, label, selector) with
fix_count incremented on conflict. Patterns retrieved ordered by
fix_count descending so the most-confirmed wins.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Capture DOM signature in `ai_assist_logger.record_fix`

**Files:**
- Modify: `jobpulse/ai_assist_logger.py`
- Test: `tests/jobpulse/test_ai_assist_widget_capture.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_ai_assist_widget_capture.py
"""record_fix optionally captures widget DOM signature."""
from unittest.mock import MagicMock, patch
import pytest


def test_record_fix_with_dom_signature_writes_to_gotchas(tmp_path, monkeypatch):
    from jobpulse.ai_assist_logger import AiAssistLogger

    captured = {}

    def fake_record(self, **kw):
        captured.update(kw)

    monkeypatch.setattr(
        "jobpulse.gotchas_db.GotchasDB.record_widget_pattern", fake_record,
    )

    logger = AiAssistLogger(db_path=str(tmp_path / "ai.db"))
    sess = logger.start_session(
        agent_name="claude", job_id="abc",
        domain="welovealfa.com", platform="generic",
    )
    logger.record_fix(
        sess.session_id,
        field_label="Do you require visa sponsorship?",
        old_value="",
        new_value="No",
        reasoning="user fix",
        fix_category="screening_answer",
        confidence=1.0,
        dom_signature={
            "selector": "div[data-q='visa'] button",
            "widget_type": "custom_select",
            "ancestor_classes": "styles-bi1IZa-q",
            "aria_label": "",
        },
    )
    assert captured["domain"] == "welovealfa.com"
    assert captured["label"] == "Do you require visa sponsorship?"
    assert captured["widget_type"] == "custom_select"


def test_record_fix_without_dom_signature_skips_widget_record(tmp_path, monkeypatch):
    """Backwards compat: existing call sites that don't pass dom_signature
    must keep working — just no widget pattern stored."""
    from jobpulse.ai_assist_logger import AiAssistLogger

    calls = []
    monkeypatch.setattr(
        "jobpulse.gotchas_db.GotchasDB.record_widget_pattern",
        lambda *a, **kw: calls.append(kw),
    )

    logger = AiAssistLogger(db_path=str(tmp_path / "ai.db"))
    sess = logger.start_session(
        agent_name="claude", job_id="abc",
        domain="x.com", platform="generic",
    )
    logger.record_fix(
        sess.session_id,
        field_label="Q",
        old_value="", new_value="A",
        reasoning="r",
        fix_category="screening_answer",
        confidence=1.0,
    )
    assert calls == []
```

- [ ] **Step 2: Run test to fail**

```bash
python -m pytest tests/jobpulse/test_ai_assist_widget_capture.py -v
```
Expected: TypeError (`dom_signature` not in signature).

- [ ] **Step 3: Add `dom_signature` parameter to `record_fix`**

```python
# Edit jobpulse/ai_assist_logger.py — find record_fix signature and extend:

def record_fix(
    self,
    session_id: str,
    field_label: str,
    old_value: str,
    new_value: str,
    reasoning: str,
    fix_category: str,
    confidence: float = 1.0,
    dom_signature: dict | None = None,  # NEW
) -> None:
    # ... existing body unchanged ...

    # NEW — optional DOM signature capture
    if dom_signature:
        try:
            from jobpulse.gotchas_db import GotchasDB
            session = self._sessions.get(session_id)
            domain = session.domain if session else ""
            if domain:
                GotchasDB().record_widget_pattern(
                    domain=domain,
                    label=field_label,
                    selector=dom_signature.get("selector", ""),
                    widget_type=dom_signature.get("widget_type", "unknown"),
                    ancestor_classes=dom_signature.get("ancestor_classes", ""),
                    aria_label=dom_signature.get("aria_label", ""),
                )
        except Exception as exc:
            logger.debug("ai_assist: widget pattern capture failed: %s", exc)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_ai_assist_widget_capture.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/ai_assist_logger.py tests/jobpulse/test_ai_assist_widget_capture.py
git commit -m "feat(ai_assist): capture DOM signature in record_fix

Optional dom_signature dict on record_fix lets callers (Claude, the
correction-capture pipeline, future agent recovery) record the DOM
selector + widget type + ancestor classes whenever they fill a
field the scanner missed. Results land in GotchasDB.widget_patterns
keyed by domain so future visits learn from past fixes.

Backwards compat: existing callers that don't pass dom_signature
work unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: New scan strategy `_scan_learned_patterns`

**Files:**
- Modify: `jobpulse/form_engine/field_scanner.py`
- Test: `tests/jobpulse/test_scan_learned_patterns.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_scan_learned_patterns.py
"""Scanner consults GotchasDB.widget_patterns for the current domain."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_learned_patterns_returns_known_widgets():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"
    # Page has the expected element matching the learned selector
    page.locator = MagicMock(return_value=MagicMock(
        count=AsyncMock(return_value=1),
        first=MagicMock(
            is_visible=AsyncMock(return_value=True),
            evaluate=AsyncMock(return_value=""),
        ),
    ))

    fake_patterns = [
        {"label": "Do you require visa sponsorship?",
         "selector": "div[data-q='visa'] button",
         "widget_type": "custom_select",
         "ancestor_classes": "", "aria_label": "", "fix_count": 3},
    ]
    with patch("jobpulse.gotchas_db.GotchasDB.get_widget_patterns",
               return_value=fake_patterns):
        out = await _scan_learned_patterns(page)

    assert len(out) == 1
    assert out[0]["label"] == "Do you require visa sponsorship?"
    assert out[0]["type"] == "custom_select"
    # Must include locator so dispatcher uses it directly
    assert out[0].get("locator") is not None


@pytest.mark.asyncio
async def test_scan_learned_patterns_skips_when_selector_not_on_page():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"
    page.locator = MagicMock(return_value=MagicMock(
        count=AsyncMock(return_value=0),
    ))

    fake_patterns = [
        {"label": "Stale field", "selector": "#missing",
         "widget_type": "text", "ancestor_classes": "",
         "aria_label": "", "fix_count": 1},
    ]
    with patch("jobpulse.gotchas_db.GotchasDB.get_widget_patterns",
               return_value=fake_patterns):
        out = await _scan_learned_patterns(page)

    assert out == []


@pytest.mark.asyncio
async def test_scan_learned_patterns_returns_empty_for_unknown_domain():
    from jobpulse.form_engine.field_scanner import _scan_learned_patterns

    page = MagicMock()
    page.url = "https://brand-new-domain.test/apply"
    with patch("jobpulse.gotchas_db.GotchasDB.get_widget_patterns",
               return_value=[]):
        out = await _scan_learned_patterns(page)
    assert out == []
```

- [ ] **Step 2: Run test to fail**

```bash
python -m pytest tests/jobpulse/test_scan_learned_patterns.py -v
```
Expected: ImportError on `_scan_learned_patterns`.

- [ ] **Step 3: Implement the strategy**

Append to `jobpulse/form_engine/field_scanner.py`:

```python
async def _scan_learned_patterns(page: "Page") -> list[dict]:
    """Strategy 0: per-domain widgets learned from prior corrections.

    Queries GotchasDB.widget_patterns for the current domain, walks each
    stored selector, returns matching elements as field dicts with the
    locator pre-attached so the dispatcher uses it directly (no
    label-string re-resolution).

    Live regression context: today's commits taught the scanner several
    new widget shapes (Oracle HCM ul[role=list], ARIA switches, salary
    number inputs). That covers shapes the scanner can recognize. This
    strategy covers everything else — once a human corrects the field,
    the SAME selector becomes detectable forever after.
    """
    from urllib.parse import urlparse
    from jobpulse.gotchas_db import GotchasDB

    try:
        domain = urlparse(page.url or "").netloc.lower().removeprefix("www.")
    except Exception:
        return []
    if not domain:
        return []

    try:
        patterns = GotchasDB().get_widget_patterns(domain)
    except Exception as exc:
        logger.debug("learned_patterns: GotchasDB read failed: %s", exc)
        return []

    if not patterns:
        return []

    out: list[dict] = []
    for p in patterns:
        selector = p["selector"]
        try:
            loc = page.locator(selector).first
            if not await loc.count():
                continue
        except Exception:
            continue
        out.append({
            "label": p["label"],
            "type": p["widget_type"],
            "value": "",
            "locator": loc,
            "selector": selector,
            "learned_pattern": True,
            "fix_count": p["fix_count"],
        })
    if out:
        logger.info(
            "learned_patterns: %d/%d known widgets matched on %s",
            len(out), len(patterns), domain,
        )
    return out
```

- [ ] **Step 4: Wire into `_run_all_strategies_parallel` as Strategy 0**

Find `_run_all_strategies_parallel` in `field_scanner.py` and add
`_scan_learned_patterns` to the strategy list FIRST (before
`_scan_a11y_tree`). The merge logic already prefers higher-count
strategies but we want learned patterns to always survive:

```python
# In _run_all_strategies_parallel, before the existing strategy list:
strategies = [
    ("learned_patterns", _scan_learned_patterns),
    ("a11y_tree",      _scan_a11y_tree),
    ("dom_query",      _scan_dom_query),
    ("playwright_locators", scan_fields_locator_fallback),
]
```

And in `_merge_fields`, add a precedence rule so `learned_pattern=True`
fields are never replaced by later-strategy duplicates with the same
label:

```python
def _merge_fields(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged = list(primary)
    by_label = {f.get("label", ""): f for f in merged}
    for f in secondary:
        lbl = f.get("label", "")
        if lbl in by_label:
            existing = by_label[lbl]
            if existing.get("learned_pattern"):
                continue  # learned wins
            if f.get("learned_pattern"):
                # Replace generic with learned
                idx = merged.index(existing)
                merged[idx] = f
                by_label[lbl] = f
            continue
        merged.append(f)
        by_label[lbl] = f
    return merged
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/jobpulse/test_scan_learned_patterns.py tests/jobpulse/test_native_form_filler.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/field_scanner.py tests/jobpulse/test_scan_learned_patterns.py
git commit -m "feat(scanner): _scan_learned_patterns strategy reads GotchasDB

New strategy 0 in _run_all_strategies_parallel: query
GotchasDB.widget_patterns for the current domain, walk each stored
selector, emit fields with the locator pre-attached.

Pre-attached locators mean the dispatcher uses the learned widget
directly — no label-string re-resolution that fails on synthetic
labels. _merge_fields ensures learned patterns survive even when
generic strategies emit a same-label duplicate.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Auto-capture signatures during fill failures

**Files:**
- Modify: `jobpulse/native_form_filler.py:_fill_by_label`
- Test: `tests/jobpulse/test_auto_capture_on_fill_failure.py`

The user-correction path already records via `ai_assist_logger`. We
also want the **correction-capture path** (when the form-filler later
detects a value mismatch via the per-page snapshot) to attach a
DOM signature so future visits get the pattern.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_auto_capture_on_fill_failure.py
"""When confirm_application records a correction, the field's DOM
signature is captured into GotchasDB.widget_patterns."""
from unittest.mock import MagicMock, patch
import pytest


def test_correction_capture_records_widget_pattern(tmp_path, monkeypatch):
    from jobpulse.applicator import confirm_application
    # Stub everything except the widget capture path
    monkeypatch.setattr(
        "jobpulse.applicator.post_apply_hook", lambda **kw: None,
    )
    monkeypatch.setattr(
        "jobpulse.applicator._record_agent_performance",
        lambda *a, **kw: None,
    )

    captured = []
    monkeypatch.setattr(
        "jobpulse.gotchas_db.GotchasDB.record_widget_pattern",
        lambda self, **kw: captured.append(kw),
    )

    agent_mapping = {"City": "Chester"}
    final_mapping = {"City": "Dundee", "City__dom": {
        "selector": "input[name='city']",
        "widget_type": "text",
        "ancestor_classes": "addr-row",
        "aria_label": "City",
    }}

    confirm_application(
        dry_run_result={"success": True, "agent_mapping": agent_mapping},
        url="https://example.com/apply",
        cv_path=tmp_path / "cv.pdf",
        agent_mapping=agent_mapping,
        final_mapping=final_mapping,
    )

    assert any(c.get("label") == "City" for c in captured)
```

- [ ] **Step 2: Implement capture in confirm_application**

In `jobpulse/applicator.py:confirm_application`, find the
correction-capture loop (where `correction_result["corrections"]` is
iterated). After the existing `AgentRulesDB.auto_generate_from_correction`
call, add:

```python
            # Auto-capture widget pattern when final_mapping carries DOM
            # signature alongside the corrected value. Convention:
            # final_mapping[label + "__dom"] = {selector, widget_type,
            # ancestor_classes, aria_label}
            try:
                from jobpulse.gotchas_db import GotchasDB
                gdb = GotchasDB()
                for c in correction_result.get("corrections", []):
                    sig = (final_mapping or {}).get(c["field"] + "__dom")
                    if sig and isinstance(sig, dict):
                        gdb.record_widget_pattern(
                            domain=domain,
                            label=c["field"],
                            selector=sig.get("selector", ""),
                            widget_type=sig.get("widget_type", "unknown"),
                            ancestor_classes=sig.get("ancestor_classes", ""),
                            aria_label=sig.get("aria_label", ""),
                        )
            except Exception as exc:
                logger.debug("widget pattern capture: %s", exc)
```

- [ ] **Step 3: Have NativeFormFiller's `_capture_final_mapping_async`
populate the `__dom` keys**

Edit `jobpulse/native_form_filler.py:_snapshot_live_form_state` (added
in commit 3bf4269 today): for each captured field, also emit a
`{label}__dom` entry with selector + widget_type + ancestor_classes.

```python
async def _snapshot_live_form_state(self) -> dict[str, str]:
    # ... existing body ...
    # NEW — append DOM signatures so confirm_application can capture them
    try:
        sigs = await self._page.evaluate("""() => {
            const out = {};
            document.querySelectorAll('input, select, textarea, [role="switch"], [role="combobox"]').forEach(el => {
                if (el.offsetParent === null && el.type !== 'radio') return;
                const lblNode = el.id ? document.querySelector(`label[for="${el.id}"]`) : null;
                const label = (lblNode?.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 200);
                if (!label) return;
                let sel = '';
                if (el.id) sel = `#${el.id}`;
                else if (el.name) sel = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
                else if (el.getAttribute('data-qa')) sel = `[data-qa="${el.getAttribute('data-qa')}"]`;
                else return; // no stable selector
                out[label] = {
                    selector: sel,
                    widget_type: (el.getAttribute('role') === 'switch' ? 'switch'
                        : el.tagName.toLowerCase() === 'select' ? 'select'
                        : el.tagName.toLowerCase() === 'textarea' ? 'textarea'
                        : el.type === 'number' ? 'number' : 'text'),
                    ancestor_classes: el.parentElement?.className || '',
                    aria_label: el.getAttribute('aria-label') || '',
                };
            });
            return out;
        }""")
    except Exception:
        sigs = {}
    for label, sig in (sigs or {}).items():
        snapshot[label + "__dom"] = sig
    return snapshot
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_auto_capture_on_fill_failure.py tests/jobpulse/test_per_page_snapshot_wiring.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/applicator.py jobpulse/native_form_filler.py tests/jobpulse/test_auto_capture_on_fill_failure.py
git commit -m "feat(corrections): auto-capture DOM signatures on user fix

When confirm_application diffs agent_mapping vs final_mapping and
records a correction, also capture the field's DOM signature
(selector + widget_type + ancestor_classes) into GotchasDB. Pulled
from final_mapping[label + '__dom'] which is now populated by
NativeFormFiller._snapshot_live_form_state.

Closes the feedback loop: every human/agent correction becomes a
permanent agent capability for that domain.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: End-to-end validation

- [ ] **Step 1: Reset Revolut**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/applications.db')
c.execute(\"UPDATE applications SET status='Pending Approval' WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')\")
c.commit()
"
rm -f data/live_review_active.json
```

- [ ] **Step 2: First run — expect missed fields, then capture them via `ai_assist`**

```bash
OLLAMA_BASE_URL=http://localhost:1 JOB_AUTOPILOT_AUTO_SUBMIT=false \
  python -m jobpulse.runner job-apply-next 1
```
Expect: scanner returns ~9 fields, agent reaches approval gate.
Manually correct visa-sponsorship + notice-period dropdowns + the
distributed-engines multiselect. Per-page snapshot fires, DOM signatures
land in `GotchasDB.widget_patterns`.

- [ ] **Step 3: Verify capture**

```bash
sqlite3 data/form_gotchas.db \
  "SELECT label, widget_type, fix_count FROM widget_patterns WHERE domain='welovealfa.com';"
```
Expect rows for visa-sponsorship, notice-period, distributed-engines.

- [ ] **Step 4: Reset Revolut and re-run — learned patterns now apply**

```bash
sqlite3 data/applications.db "UPDATE applications SET status='Pending Approval' WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')"
rm -f data/live_review_active.json
OLLAMA_BASE_URL=http://localhost:1 JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next 1
```
Expect log: `learned_patterns: 3/3 known widgets matched on welovealfa.com`.
Field count goes from 9 → 12+ on the same page.

- [ ] **Step 5: If validation passes, no further commit. Otherwise file follow-ups.**
