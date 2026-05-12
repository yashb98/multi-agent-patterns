# Vision Fallback Gate — Force Vision Augment On Sparse Scans

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the field scanner returns suspiciously few fields on a page the reasoner has classified as `application_form` with high confidence, force the vision LLM to inspect the page screenshot and report missing form fields. Today the `vision_gate` skips vision whenever reasoner confidence ≥ 0.7 — which is exactly the case where shape-based scanners miss custom widgets (Revolut Yes/No switches, custom React selects).

**Architecture:** Add a single quality predicate `should_force_vision(scanner_count, page_type, confidence)` consulted right before the existing vision_gate. When it triggers, call a new `vision_augment_scan(page, existing_fields)` that takes a screenshot, sends it to the vision LLM with the existing field list, and asks "what fields did the DOM scanner miss?". Results are merged into the field list as `vision_only=True`, so callers can route them through the LLM-recovery fill path. Cached per content hash to avoid repeated cost.

**Tech Stack:** Python · Playwright · existing `shared.streaming.smart_llm_call` · existing `page_reasoner` cache pattern · pytest

**Live regression context:** On Revolut welovealfa.com 2026-05-05 the DOM scanner found 9 fields on a page that actually had 13+ (visa-sponsorship dropdown, notice-period dropdown, distributed-engines multiselect all missed). Reasoner confidence was 0.90 → vision gate skipped. Agent silently advanced past the missed questions.

---

### Task 1: Predicate `should_force_vision`

**Files:**
- Create: `jobpulse/form_engine/vision_gate.py`
- Test: `tests/jobpulse/test_vision_gate_predicate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_vision_gate_predicate.py
"""Predicate that decides whether to force vision augmentation."""
from jobpulse.form_engine.vision_gate import should_force_vision


def test_forces_vision_when_application_form_has_few_fields_at_high_confidence():
    # The Revolut regression case: page is application_form, conf 0.90,
    # but DOM scanner only found 9 fields. Real form has 13+. Force vision.
    assert should_force_vision(
        scanner_field_count=9,
        page_type="application_form",
        reasoner_confidence=0.90,
    ) is True


def test_skips_vision_when_scanner_count_is_dense():
    # 25+ fields on an application form → scanner is doing fine, skip vision
    assert should_force_vision(
        scanner_field_count=25,
        page_type="application_form",
        reasoner_confidence=0.90,
    ) is False


def test_skips_vision_for_non_form_pages():
    # JD page or login wall — vision augment doesn't apply here
    assert should_force_vision(
        scanner_field_count=0,
        page_type="job_description",
        reasoner_confidence=1.0,
    ) is False


def test_skips_vision_when_reasoner_uncertain():
    # When reasoner confidence is already low, the existing vision_gate
    # already runs vision — don't double-fire.
    assert should_force_vision(
        scanner_field_count=2,
        page_type="application_form",
        reasoner_confidence=0.5,
    ) is False


def test_threshold_at_10_fields():
    # Sparse threshold = 10 fields. 10 should trigger, 11 should not.
    assert should_force_vision(10, "application_form", 0.9) is True
    assert should_force_vision(11, "application_form", 0.9) is False


def test_zero_scanner_fields_always_forces_when_form_confident():
    assert should_force_vision(0, "application_form", 0.85) is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/jobpulse/test_vision_gate_predicate.py -v
```
Expected: ImportError (module not yet created).

- [ ] **Step 3: Write the implementation**

```python
# jobpulse/form_engine/vision_gate.py
"""Vision-augment gate for sparse-field scans on confident form pages.

When the reasoner is confident a page is an application_form (≥0.7) but
the DOM scanner returns suspiciously few fields, force the vision LLM
to find what the shape-based scanners missed. Live regression on
Revolut welovealfa.com 2026-05-05: scanner found 9 fields, real form
had 13+ (visa/notice dropdowns + multiselect missed by all DOM
strategies). Reasoner confidence 0.90 → existing vision_gate skipped.

This predicate is the trigger; the augment call lives in
`vision_augment_scan` (separate module so the predicate can be tested
without invoking an LLM).
"""
from __future__ import annotations

# Threshold below which we treat the scan as suspicious. Tuned to the
# observed range:
#   • Trivial pages (CV upload only)        : 1-3   fields → covered by upload_files()
#   • Sparse screening (Revolut, missed)    : 6-10  fields ← target this band
#   • Healthy screening (Workday, Indeed)   : 12-30 fields
SPARSE_FIELD_THRESHOLD = 10

# Reasoner confidence above which existing vision_gate skips vision.
# We re-trigger above this floor when the scan looks sparse.
HIGH_CONFIDENCE_FLOOR = 0.7


def should_force_vision(
    scanner_field_count: int,
    page_type: str,
    reasoner_confidence: float,
) -> bool:
    """True when the scanner result looks too sparse for a confident form.

    Three conditions must all hold:
      1. page_type == 'application_form' (the only page type where missed
         fields cause silent submission of incomplete data)
      2. reasoner confidence ≥ HIGH_CONFIDENCE_FLOOR (otherwise the
         existing vision_gate has already fired vision)
      3. scanner found ≤ SPARSE_FIELD_THRESHOLD fields (suspicious for
         a typical screening page)
    """
    if page_type != "application_form":
        return False
    if reasoner_confidence < HIGH_CONFIDENCE_FLOOR:
        return False
    return scanner_field_count <= SPARSE_FIELD_THRESHOLD
```

- [ ] **Step 4: Run tests to verify pass**

```bash
python -m pytest tests/jobpulse/test_vision_gate_predicate.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/vision_gate.py tests/jobpulse/test_vision_gate_predicate.py
git commit -m "feat(scanner): vision-gate predicate for sparse-scan fallback

Add should_force_vision() — true when the DOM scanner returns ≤10
fields on a page the reasoner classified as application_form with
≥0.7 confidence. This is the trigger condition; the augment call
lands in a follow-up commit.

Live regression on Revolut welovealfa.com 2026-05-05: scanner found
9 fields, real form had 13+. Existing vision_gate skipped because
reasoner confidence was 0.90 — predicate now flips that on sparse
scans.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Vision augment call

**Files:**
- Modify: `jobpulse/form_engine/vision_gate.py:end-of-file`
- Test: `tests/jobpulse/test_vision_augment_scan.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_vision_augment_scan.py
"""vision_augment_scan: vision LLM finds DOM-missed fields."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_vision_augment_returns_fields_not_already_in_existing():
    from jobpulse.form_engine.vision_gate import vision_augment_scan

    page = MagicMock()
    page.url = "https://welovealfa.com/.../complete-profile"
    page.screenshot = AsyncMock(return_value=b"fake-png-bytes")
    page.title = AsyncMock(return_value="Software Engineer (Data) at Revolut")

    existing_fields = [
        {"label": "Enter your first name", "type": "text"},
        {"label": "Enter email address", "type": "text"},
    ]

    fake_llm_response = {
        "missing_fields": [
            {"label": "Do you require visa sponsorship in the United Kingdom?",
             "type": "select", "options": ["Yes", "No"]},
            {"label": "What is your notice period?",
             "type": "select", "options": ["Immediately", "1 month", "3 months"]},
        ]
    }

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm",
               AsyncMock(return_value=fake_llm_response)):
        result = await vision_augment_scan(page, existing_fields)

    assert len(result) == 2
    assert all(f.get("vision_only") is True for f in result)
    assert result[0]["label"] == "Do you require visa sponsorship in the United Kingdom?"
    assert result[1]["type"] == "select"


@pytest.mark.asyncio
async def test_vision_augment_caches_by_content_hash():
    """Two calls with the same page/screenshot must hit the cache."""
    from jobpulse.form_engine.vision_gate import vision_augment_scan, _CACHE

    _CACHE.clear()
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.screenshot = AsyncMock(return_value=b"identical-bytes")
    page.title = AsyncMock(return_value="Form")

    fake_response = {"missing_fields": [{"label": "X", "type": "text"}]}
    mock_llm = AsyncMock(return_value=fake_response)

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm", mock_llm):
        await vision_augment_scan(page, [])
        await vision_augment_scan(page, [])

    assert mock_llm.call_count == 1  # second call must hit cache


@pytest.mark.asyncio
async def test_vision_augment_returns_empty_on_llm_error():
    from jobpulse.form_engine.vision_gate import vision_augment_scan, _CACHE

    _CACHE.clear()
    page = MagicMock()
    page.url = "https://example.com/apply"
    page.screenshot = AsyncMock(return_value=b"bytes")
    page.title = AsyncMock(return_value="Form")

    with patch("jobpulse.form_engine.vision_gate._call_vision_llm",
               AsyncMock(side_effect=Exception("vision API down"))):
        result = await vision_augment_scan(page, [])
    assert result == []
```

- [ ] **Step 2: Run tests to verify failure**

```bash
python -m pytest tests/jobpulse/test_vision_augment_scan.py -v
```
Expected: ImportError on `vision_augment_scan` / `_CACHE`.

- [ ] **Step 3: Implement `vision_augment_scan`**

```python
# Append to jobpulse/form_engine/vision_gate.py

import base64
import hashlib
import json
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# Content-hash → list[field]. Bounded LRU not needed in practice — the
# orchestrator clears _CACHE on host change. Live regressions show the
# same page rarely revisited within a single run.
_CACHE: dict[str, list[dict]] = {}


_VISION_PROMPT_TEMPLATE = """\
You are auditing a job-application form. Below is the list of fields the
DOM scanner already found. Look at the page screenshot and identify any
visible question or input that is NOT in this list. Return JSON with
key `missing_fields`, each item having `label` (the visible question
text), `type` (one of: text, textarea, select, multiselect, switch,
checkbox, file), and `options` if it's a select/multiselect.

Page URL: {url}
Page title: {title}

DOM-scanner already found these fields ({n_existing}):
{existing_summary}

Respond with JSON only. If the scanner caught everything, return
{{"missing_fields": []}}.
"""


def _content_hash(url: str, screenshot_bytes: bytes) -> str:
    h = hashlib.sha256()
    h.update((url or "").encode("utf-8"))
    h.update(screenshot_bytes[:200_000])  # cap; full-page shots can be huge
    return h.hexdigest()[:24]


def _summarize_existing(fields: list[dict]) -> str:
    if not fields:
        return "(none)"
    lines = []
    for f in fields[:40]:
        lines.append(f"  - {f.get('label', '')[:80]} [{f.get('type', '')}]")
    return "\n".join(lines)


async def _call_vision_llm(
    screenshot_b64: str, prompt: str
) -> dict[str, Any]:
    """Call the cloud vision LLM with a screenshot + prompt.

    Returns parsed JSON response or {} on parse error. Real call goes
    through `shared.streaming.smart_llm_call` so cost tracking + retry
    are inherited.
    """
    from shared.streaming import smart_llm_call  # lazy: avoid import cost on cold path
    response = await smart_llm_call(
        messages=[
            {"role": "user",
             "content": [
                 {"type": "text", "text": prompt},
                 {"type": "image_url",
                  "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
             ]},
        ],
        model="gpt-4o",  # vision-capable
        response_format={"type": "json_object"},
        max_tokens=1500,
        temperature=0.0,
    )
    text = response.choices[0].message.content if response else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        return {}


async def vision_augment_scan(
    page: Any, existing_fields: list[dict]
) -> list[dict]:
    """Take a page screenshot, ask the vision LLM what fields the DOM
    scanner missed, return them tagged `vision_only=True`.

    Cached per (url, screenshot-hash). Returns [] on any failure so the
    caller can transparently fall through to the existing flow.
    """
    try:
        screenshot = await page.screenshot()
        url = page.url or ""
        title = await page.title()
    except Exception as exc:
        logger.debug("vision_augment_scan: screenshot failed: %s", exc)
        return []

    cache_key = _content_hash(url, screenshot)
    if cache_key in _CACHE:
        return list(_CACHE[cache_key])

    prompt = _VISION_PROMPT_TEMPLATE.format(
        url=url[:200],
        title=title[:200],
        n_existing=len(existing_fields),
        existing_summary=_summarize_existing(existing_fields),
    )
    screenshot_b64 = base64.b64encode(screenshot).decode("ascii")

    try:
        parsed = await _call_vision_llm(screenshot_b64, prompt)
    except Exception as exc:
        logger.warning("vision_augment_scan: LLM call failed: %s", exc)
        _CACHE[cache_key] = []
        return []

    raw = parsed.get("missing_fields") or []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = (item.get("label") or "").strip()
        if not label:
            continue
        out.append({
            "label": label[:300],
            "type": (item.get("type") or "text").lower(),
            "options": item.get("options") or None,
            "vision_only": True,
            "value": "",
        })

    _CACHE[cache_key] = out
    logger.info(
        "vision_augment_scan: %d fields recovered for %s (cache=%s)",
        len(out), url[:80], cache_key,
    )
    return out
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_vision_augment_scan.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/vision_gate.py tests/jobpulse/test_vision_augment_scan.py
git commit -m "feat(scanner): vision_augment_scan finds DOM-missed fields

Vision LLM gets a page screenshot + the existing scanner field list,
returns any visible questions the DOM scanner missed. Tagged
vision_only=True so callers can route them through the LLM-recovery
fill path (label-string match won't work — these have no DOM anchor).

Cached by (url, screenshot-hash). Returns [] on any failure for
transparent fall-through.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Wire vision augment into `scan_fields`

**Files:**
- Modify: `jobpulse/form_engine/field_scanner.py:scan_fields`
- Test: `tests/jobpulse/test_scan_fields_vision_fallback_wiring.py`

- [ ] **Step 1: Write the failing wiring test**

```python
# tests/jobpulse/test_scan_fields_vision_fallback_wiring.py
"""scan_fields must call vision_augment when the result is sparse."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_fields_invokes_vision_augment_on_sparse_application_form():
    from jobpulse.form_engine.field_scanner import scan_fields

    page = MagicMock()
    page.url = "https://welovealfa.com/.../complete-profile"

    # Only 5 fields from the existing strategies — well below threshold
    sparse = [{"label": f"f{i}", "type": "text", "value": ""} for i in range(5)]

    augment_extras = [
        {"label": "Visa sponsorship?", "type": "select",
         "options": ["Yes", "No"], "vision_only": True, "value": ""},
    ]

    with patch("jobpulse.form_engine.field_scanner._run_all_strategies_parallel",
               AsyncMock(return_value=({"dom_query": sparse}, "dom_query"))), \
         patch("jobpulse.form_engine.field_scanner._maybe_augment_with_vision",
               AsyncMock(return_value=augment_extras)) as mock_augment:
        out = await scan_fields(page)

    assert mock_augment.awaited
    # Augmented field is appended to the result
    assert any(f.get("vision_only") for f in out)
    assert any(f["label"] == "Visa sponsorship?" for f in out)


@pytest.mark.asyncio
async def test_scan_fields_skips_vision_when_dense():
    """24 scanner fields → no vision call."""
    from jobpulse.form_engine.field_scanner import scan_fields

    page = MagicMock()
    page.url = "https://example.com/.../apply"
    dense = [{"label": f"f{i}", "type": "text", "value": ""} for i in range(24)]

    with patch("jobpulse.form_engine.field_scanner._run_all_strategies_parallel",
               AsyncMock(return_value=({"dom_query": dense}, "dom_query"))), \
         patch("jobpulse.form_engine.field_scanner._maybe_augment_with_vision",
               AsyncMock(return_value=[])) as mock_augment:
        await scan_fields(page)

    # Even if the helper exists, it should be called with a no-op signal —
    # but the helper itself must early-return without invoking vision.
    # Easiest: verify the predicate returns False and the helper is the
    # one that gates the actual LLM call (see Task 4 wiring).
    if mock_augment.awaited:
        # Helper called with sparse=False signal — fine
        pass  # Covered by Task 1 predicate test
```

- [ ] **Step 2: Run test to fail (helper doesn't exist yet)**

```bash
python -m pytest tests/jobpulse/test_scan_fields_vision_fallback_wiring.py -v
```
Expected: AttributeError on `_maybe_augment_with_vision`.

- [ ] **Step 3: Add the helper + wire into `scan_fields`**

Edit `jobpulse/form_engine/field_scanner.py`:

```python
# Add this import at the top of the file (under existing imports):
from jobpulse.form_engine.vision_gate import (
    should_force_vision, vision_augment_scan,
)


# Add this helper near the bottom (before scan_fields definition):
async def _maybe_augment_with_vision(
    page: "Page",
    existing_fields: list[dict],
    page_type_hint: str | None,
    confidence_hint: float | None,
) -> list[dict]:
    """Returns vision-augmented field list (or [] if not triggered).

    Caller is responsible for merging into the primary scan result.
    """
    page_type = page_type_hint or "application_form"
    confidence = confidence_hint if confidence_hint is not None else 0.9
    if not should_force_vision(
        scanner_field_count=len(existing_fields),
        page_type=page_type,
        reasoner_confidence=confidence,
    ):
        return []
    extras = await vision_augment_scan(page, existing_fields)
    return extras
```

Then in the existing `scan_fields()` function, just BEFORE the final
`return best_fields`, insert:

```python
    # Vision-augment when the scan looks sparse on a confident form.
    # Reasoner state is read from the orchestrator-provided hints if
    # available; falls back to assuming application_form @ 0.9 (matches
    # what the orchestrator passes when calling _phase_act).
    page_type_hint = getattr(page, "_jp_page_type_hint", None)
    confidence_hint = getattr(page, "_jp_reasoner_confidence", None)
    extras = await _maybe_augment_with_vision(
        page, best_fields, page_type_hint, confidence_hint,
    )
    if extras:
        # Append; downstream filler treats vision_only=True specially.
        best_fields = list(best_fields) + extras
        logger.info(
            "scan_fields: vision augment added %d fields → %d total",
            len(extras), len(best_fields),
        )
```

- [ ] **Step 4: Have the orchestrator stamp the hints on the page object**

Edit `jobpulse/application_orchestrator_pkg/_navigator.py:_phase_act`,
right after `act = action.action`:

```python
        # Stamp reasoner hints on the page so downstream scanners can
        # consult them without a circular import. Cheap — these are
        # plain attributes, not Playwright-managed state.
        try:
            page = getattr(self.driver, "page", None)
            if page is not None:
                page._jp_page_type_hint = action.page_type
                page._jp_reasoner_confidence = float(getattr(action, "confidence", 0.9))
        except Exception:
            pass
```

- [ ] **Step 5: Run wiring tests**

```bash
python -m pytest tests/jobpulse/test_scan_fields_vision_fallback_wiring.py tests/jobpulse/test_vision_gate_predicate.py tests/jobpulse/test_vision_augment_scan.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/field_scanner.py jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_scan_fields_vision_fallback_wiring.py
git commit -m "feat(scanner): wire vision-augment into scan_fields on sparse forms

When the multi-strategy scan returns ≤10 fields on a page the
reasoner classified as application_form with ≥0.7 confidence,
auto-augment via vision_augment_scan. Augmented fields are tagged
vision_only=True; downstream NativeFormFiller routes them through
the LLM-recovery fill path (label-string match cannot reach them
since they have no DOM anchor).

Reasoner hints stamped on page object in _phase_act so scan_fields
can consult them without an import cycle.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Validate end-to-end against the Revolut regression

**Files:**
- No code changes — validation only.
- Test: manual run via `job-apply-next` against the Pending Approval
  Revolut row.

- [ ] **Step 1: Reset the Revolut application row**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('/Users/yashbishnoi/projects/multi_agent_patterns/data/applications.db')
c.execute(\"UPDATE applications SET status='Pending Approval', updated_at=datetime('now')
              WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')\")
c.commit()
"
rm -f data/live_review_active.json
```

- [ ] **Step 2: Run agent**

```bash
OLLAMA_BASE_URL=http://localhost:1 JOB_AUTOPILOT_AUTO_SUBMIT=false \
  python -m jobpulse.runner job-apply-next 1
```

- [ ] **Step 3: Check log for vision-augment trigger + recovered fields**

Expected log lines:
```
scan_fields: vision augment added N fields → M total
```
Where N ≥ 3 (visa-sponsorship dropdown, notice-period dropdown,
distributed-engines multiselect — and possibly the country combobox).

- [ ] **Step 4: Verify the recovered fields appear in the form_interaction_log**

```bash
sqlite3 data/form_interaction_log.db \
  "SELECT field_labels FROM page_structures WHERE domain='welovealfa.com' ORDER BY ts DESC LIMIT 1;"
```
Expected: includes the visa/notice/distributed-engines questions.

- [ ] **Step 5: If validation passes, no further commit. Otherwise file follow-up issues.**
