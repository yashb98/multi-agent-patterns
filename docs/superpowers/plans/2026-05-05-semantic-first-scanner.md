# Semantic-First Form Scanner

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace shape-driven DOM scanning as the primary mechanism with **semantic-first scanning** — start from the visible question text, then locate the nearest interactive element. This is what a human (or Claude reading the page) does naturally and is robust against custom React widgets the shape-based detectors haven't been taught.

**Architecture:**

  1. **Question extraction** — walk the visible DOM in document order, emit a `Question` for every text fragment that matches a question template (ends with `?`, starts with "Are you / Do you / What is / Have you / Will you", or is followed by interactive widgets within N pixels).
  2. **Widget proximity match** — for each `Question`, find the nearest fillable element via (a) DOM ancestor match (closest fieldset/section containing both), (b) following-sibling walk, (c) bounding-box pixel proximity ≤ 400px below.
  3. **Widget classification** — examine the matched element's tag/role/aria/click-behaviour to classify into one of: `text`, `textarea`, `select`, `combobox`, `multiselect`, `switch`, `radio_group`, `checkbox`, `file`. Same set the dispatcher already handles.
  4. **Wire as Strategy `semantic`** — runs in parallel with existing `dom_query` / `a11y_tree` / `playwright_locators`. Merge winner picks the strategy returning the most fields.

The shape-based strategies stay — they're still useful for the (common) case where DOM annotations are clean. Semantic is a peer strategy that catches everything else.

**Tech Stack:** Python · Playwright · pytest · existing `field_scanner._merge_fields`

**Live regression context:** Today's runs missed 4 widgets on Revolut welovealfa.com:
- "Do you require visa sponsorship?" (custom React select, no `<select>`)
- "What is your notice period?" (custom React select)
- "Which of the following distributed data processing engines do you have expertise in?" (custom multiselect)
- "What is your country of residence" (combobox label empty)

All 4 are visible question text on the page. Semantic-first scanning would have caught them.

---

### Task 1: Question extractor

**Files:**
- Create: `jobpulse/form_engine/semantic_scanner.py`
- Test: `tests/jobpulse/test_semantic_question_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_semantic_question_extractor.py
"""extract_visible_questions walks the page, finds question-shaped text."""
from unittest.mock import AsyncMock, MagicMock
import pytest


@pytest.mark.asyncio
async def test_extracts_questions_in_document_order():
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "Complete your profile", "y": 100},          # heading
        {"text": "First Name", "y": 150},                     # field label
        {"text": "What is your country of residence", "y": 250},
        {"text": "Do you require visa sponsorship in the UK?", "y": 320},
        {"text": "What is your notice period?", "y": 380},
        {"text": "Are you happy to work remotely all the time?", "y": 450},
        {"text": "£85,500 - £118,000 Per year", "y": 480},   # noise
        {"text": "Apply now", "y": 600},                      # button
    ])

    qs = await extract_visible_questions(page)
    texts = [q.text for q in qs]
    assert "Do you require visa sponsorship in the UK?" in texts
    assert "What is your notice period?" in texts
    assert "Are you happy to work remotely all the time?" in texts
    assert "Apply now" not in texts
    assert "£85,500 - £118,000 Per year" not in texts
    # Document order preserved
    visa_idx = texts.index("Do you require visa sponsorship in the UK?")
    notice_idx = texts.index("What is your notice period?")
    assert visa_idx < notice_idx


@pytest.mark.asyncio
async def test_recognizes_question_starters_without_question_mark():
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "Tell us about your experience", "y": 200},  # imperative — keep
        {"text": "What is your full name", "y": 300},          # no '?' — keep
        {"text": "Submit application", "y": 400},              # button verb — drop
        {"text": "Click here to upload", "y": 450},            # imperative — drop (action)
    ])
    qs = await extract_visible_questions(page)
    texts = [q.text for q in qs]
    assert "Tell us about your experience" in texts
    assert "What is your full name" in texts
    assert "Submit application" not in texts
    assert "Click here to upload" not in texts


@pytest.mark.asyncio
async def test_filters_out_pure_field_labels_without_context():
    """Bare 'First Name' is a field label, not a standalone question — let
    the existing label-based scanners handle it. Semantic scanner targets
    only the questions that DON'T have a paired <label>."""
    from jobpulse.form_engine.semantic_scanner import extract_visible_questions

    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"text": "First Name", "y": 100},
        {"text": "Email Address", "y": 150},
    ])
    qs = await extract_visible_questions(page)
    assert qs == []
```

- [ ] **Step 2: Run test to fail**

```bash
python -m pytest tests/jobpulse/test_semantic_question_extractor.py -v
```
Expected: ImportError (module not yet created).

- [ ] **Step 3: Implement extractor**

```python
# jobpulse/form_engine/semantic_scanner.py
"""Semantic-first form scanner.

Reads the visible page text and identifies form questions, then matches
each question to the nearest interactive widget. Complements the
shape-based detectors in field_scanner.py — catches questions whose
widget is a custom React component the shape detectors don't recognize.

Three pieces:
    1. extract_visible_questions(page) → list[Question]
    2. match_question_to_widget(question, page) → Widget | None
    3. classify_widget(element) → str  (text/select/combobox/switch/…)

This file holds 1; 2 and 3 are in following tasks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


# A question is text that:
#   • Ends with '?' (most natural form), OR
#   • Starts with one of: Are you, Do you, What is, Have you, Will you,
#     Tell us, How long, Which, Where, When, Why, Please describe
QUESTION_STARTERS = re.compile(
    r"^(are\s+you|do\s+you|did\s+you|have\s+you|will\s+you|"
    r"what\s+(is|was|are|were)|how\s+(long|many|much|often)|"
    r"which\s+|where\s+|when\s+|why\s+|please\s+(describe|provide|tell|share)|"
    r"tell\s+us)\b",
    re.IGNORECASE,
)

# Buttons / nav / field labels we never want to treat as questions
NON_QUESTION_PHRASES = re.compile(
    r"^(apply|submit|next|back|continue|review|save|go to|learn more|"
    r"click\s+here|upload|sign\s+(in|up)|log\s+(in|out)|return|cancel)\b",
    re.IGNORECASE,
)

# Field labels (without surrounding question context) — short, no verb
FIELD_LABEL_HEURISTIC = re.compile(
    r"^(first|last|full|preferred)\s*name$|"
    r"^email(\s+address)?$|"
    r"^phone(\s+number)?$|"
    r"^(post|zip)\s*code$|"
    r"^address(\s+line\s+\d)?$",
    re.IGNORECASE,
)

# Min text length for a question — below this is almost certainly a label
MIN_QUESTION_LEN = 12
# Max text length — above this is paragraph prose, not a question
MAX_QUESTION_LEN = 500


@dataclass
class Question:
    text: str
    y: int            # bounding-box top, used by widget proximity match
    dom_path: str     # CSS path of the text node (used by ancestor match)


def _is_question_shaped(text: str) -> bool:
    """Cheap pre-filter: does this string look like a form question?"""
    s = (text or "").strip()
    if len(s) < MIN_QUESTION_LEN or len(s) > MAX_QUESTION_LEN:
        return False
    if NON_QUESTION_PHRASES.match(s):
        return False
    if FIELD_LABEL_HEURISTIC.match(s):
        return False
    if s.endswith("?"):
        return True
    if QUESTION_STARTERS.match(s):
        return True
    return False


async def extract_visible_questions(page: Any) -> list[Question]:
    """Walk every visible text node, return question-shaped fragments
    in document order with bounding-box y position and CSS path.
    """
    raw = await page.evaluate("""() => {
        const out = [];
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT,
            { acceptNode: n => {
                const p = n.parentElement;
                if (!p || p.offsetParent === null) return NodeFilter.FILTER_REJECT;
                const t = (n.textContent || '').trim();
                if (t.length < 8 || t.length > 500) return NodeFilter.FILTER_REJECT;
                return NodeFilter.FILTER_ACCEPT;
            }},
        );
        function cssPath(el) {
            const parts = [];
            let n = el;
            for (let i = 0; n && n.nodeType === 1 && i < 6; i++, n = n.parentElement) {
                let p = n.tagName.toLowerCase();
                if (n.id) { p += '#' + n.id; parts.unshift(p); break; }
                parts.unshift(p);
            }
            return parts.join(' > ');
        }
        let n;
        while (n = walker.nextNode()) {
            const p = n.parentElement;
            const r = p.getBoundingClientRect();
            out.push({
                text: (n.textContent || '').trim(),
                y: Math.round(r.top + window.scrollY),
                dom_path: cssPath(p),
            });
        }
        return out;
    }""")
    seen = set()
    qs: list[Question] = []
    for item in (raw or []):
        text = (item.get("text") or "").strip()
        if text in seen:
            continue
        if not _is_question_shaped(text):
            continue
        seen.add(text)
        qs.append(Question(
            text=text,
            y=int(item.get("y") or 0),
            dom_path=item.get("dom_path") or "",
        ))
    return qs
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_semantic_question_extractor.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/semantic_scanner.py tests/jobpulse/test_semantic_question_extractor.py
git commit -m "feat(scanner): semantic question extractor

Walks the page's visible text in document order, returns question-
shaped fragments with bounding-box y + CSS path. Heuristic filter:
ends with '?' OR starts with 'Are you / Do you / What is / …'.
Excludes button text, nav verbs, and bare field labels (those have
their own <label> already captured by shape-based scanners).

This is piece 1 of the semantic-first scanner — proximity match
and widget classification land in follow-up commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: Widget proximity match

**Files:**
- Modify: `jobpulse/form_engine/semantic_scanner.py:end-of-file`
- Test: `tests/jobpulse/test_semantic_proximity_match.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_semantic_proximity_match.py
"""match_question_to_widget finds the nearest interactive element."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from jobpulse.form_engine.semantic_scanner import Question


@pytest.mark.asyncio
async def test_matches_widget_within_400px_below_question():
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Do you require visa sponsorship?",
                 y=300, dom_path="div.q > p")

    page = MagicMock()
    page.evaluate = AsyncMock(return_value={
        "matched": True,
        "y": 360,                  # 60px below question
        "tag": "BUTTON",
        "role": "button",
        "aria_pressed": None,
        "aria_checked": None,
        "selector": "div[data-q='visa'] button",
        "ancestor_classes": "visa-q",
        "options_text": ["Select option"],
    })

    widget = await match_question_to_widget(q, page)
    assert widget is not None
    assert widget["selector"] == "div[data-q='visa'] button"
    assert widget["distance_px"] == 60


@pytest.mark.asyncio
async def test_returns_none_when_no_widget_within_proximity():
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Stale question?", y=100, dom_path="div > p")
    page = MagicMock()
    page.evaluate = AsyncMock(return_value={"matched": False})
    widget = await match_question_to_widget(q, page)
    assert widget is None


@pytest.mark.asyncio
async def test_prefers_ancestor_match_over_pixel_proximity():
    """When the question and a widget share a fieldset ancestor, that
    beats a closer-but-unrelated widget below."""
    from jobpulse.form_engine.semantic_scanner import match_question_to_widget

    q = Question(text="Are you OK with on-call?", y=200, dom_path="fieldset.qa > p")
    page = MagicMock()
    # Page returns the ancestor-matched widget (closer-by-DOM, not by px)
    page.evaluate = AsyncMock(return_value={
        "matched": True,
        "y": 350,
        "tag": "BUTTON",
        "role": "switch",
        "aria_checked": "false",
        "selector": "fieldset.qa button",
        "ancestor_classes": "qa",
        "match_kind": "ancestor",
    })
    widget = await match_question_to_widget(q, page)
    assert widget["match_kind"] == "ancestor"
```

- [ ] **Step 2: Run test to fail**

```bash
python -m pytest tests/jobpulse/test_semantic_proximity_match.py -v
```
Expected: ImportError on `match_question_to_widget`.

- [ ] **Step 3: Implement matcher**

Append to `jobpulse/form_engine/semantic_scanner.py`:

```python
async def match_question_to_widget(
    question: Question, page: Any
) -> dict | None:
    """Find the nearest interactive element to a question.

    Two-tier search:
      1. **Ancestor match** — find the question's text node, walk up to
         a `<fieldset>`, `<section>`, or any element with role=group.
         If that ancestor contains an interactive element (input/select/
         textarea/[role]), use it. Strongest signal — the widget and
         question share a logical container by definition.
      2. **Pixel proximity** — find any visible interactive element
         within 400px below the question's bounding box. Tie-break by
         smallest distance.

    Returns a dict with selector + match metadata, or None.
    """
    return await page.evaluate("""(args) => {
        const { questionText, questionY } = args;

        // 1. Ancestor match — find the text node first
        const all = document.evaluate(
            `//*[contains(text(), ${JSON.stringify(questionText.slice(0, 50))})]`,
            document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null,
        ).singleNodeValue;

        function widgetIn(scope) {
            return scope.querySelector(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]),' +
                'select, textarea,' +
                '[role="combobox"], [role="switch"], [role="radio"],' +
                '[role="checkbox"], [role="listbox"], [role="button"][aria-haspopup]'
            );
        }

        function selectorOf(el) {
            if (!el) return '';
            if (el.id) return `#${el.id}`;
            if (el.name) return `${el.tagName.toLowerCase()}[name="${el.name}"]`;
            if (el.getAttribute('data-qa')) return `[data-qa="${el.getAttribute('data-qa')}"]`;
            // Path fallback
            const parts = [];
            let n = el;
            for (let i = 0; n && n.nodeType === 1 && i < 5; i++, n = n.parentElement) {
                let p = n.tagName.toLowerCase();
                if (n.className && typeof n.className === 'string') {
                    const cls = n.className.split(/\\s+/).filter(c => c).slice(0, 2).join('.');
                    if (cls) p += '.' + cls;
                }
                parts.unshift(p);
            }
            return parts.join(' > ');
        }

        if (all) {
            // Walk up looking for grouping ancestor
            let scope = all.parentElement;
            for (let i = 0; scope && i < 4; i++, scope = scope.parentElement) {
                if (['FIELDSET', 'SECTION'].includes(scope.tagName) ||
                    ['group', 'region'].includes(scope.getAttribute('role'))) {
                    const w = widgetIn(scope);
                    if (w && w.offsetParent !== null) {
                        return {
                            matched: true,
                            y: w.getBoundingClientRect().top + window.scrollY,
                            tag: w.tagName,
                            role: w.getAttribute('role') || '',
                            aria_pressed: w.getAttribute('aria-pressed'),
                            aria_checked: w.getAttribute('aria-checked'),
                            selector: selectorOf(w),
                            ancestor_classes: scope.className || '',
                            match_kind: 'ancestor',
                            distance_px: 0,
                        };
                    }
                }
            }
        }

        // 2. Pixel proximity within 400px below
        const candidates = [...document.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]),' +
            'select, textarea,' +
            '[role="combobox"], [role="switch"], [role="radio"],' +
            '[role="checkbox"], [role="listbox"]'
        )].filter(el => el.offsetParent !== null);

        let best = null;
        for (const w of candidates) {
            const r = w.getBoundingClientRect();
            const wy = r.top + window.scrollY;
            const dy = wy - questionY;
            if (dy < 0 || dy > 400) continue;
            if (!best || dy < best.distance_px) {
                best = {
                    matched: true,
                    y: wy,
                    tag: w.tagName,
                    role: w.getAttribute('role') || '',
                    aria_pressed: w.getAttribute('aria-pressed'),
                    aria_checked: w.getAttribute('aria-checked'),
                    selector: selectorOf(w),
                    ancestor_classes: w.parentElement?.className || '',
                    match_kind: 'proximity',
                    distance_px: dy,
                };
            }
        }
        return best || { matched: false };
    }""", {"questionText": question.text, "questionY": question.y})
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_semantic_proximity_match.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/semantic_scanner.py tests/jobpulse/test_semantic_proximity_match.py
git commit -m "feat(scanner): semantic widget proximity match

Two-tier search: (1) walk question's ancestors for fieldset/section/
[role=group] containing an interactive element; (2) fall back to
pixel proximity ≤400px below the question's y. Returns a dict with
selector + match metadata.

Ancestor match is strongest — widgets in the same fieldset are
intentionally grouped with their label. Proximity match handles
the common 'flat div soup' layout used by React form libraries.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 3: Widget classifier

**Files:**
- Modify: `jobpulse/form_engine/semantic_scanner.py:end-of-file`
- Test: `tests/jobpulse/test_semantic_widget_classifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_semantic_widget_classifier.py
"""classify_widget maps the matched element to a fill-handler input_type."""
import pytest


def test_classifies_role_switch():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "BUTTON", "role": "switch"}) == "switch"


def test_classifies_role_combobox():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "combobox"}) == "combobox"


def test_classifies_native_select():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "SELECT", "role": ""}) == "select"


def test_classifies_textarea():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "TEXTAREA", "role": ""}) == "textarea"


def test_classifies_role_radio_to_radio_group():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "radio"}) == "radio_group"


def test_classifies_role_checkbox():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "INPUT", "role": "checkbox"}) == "checkbox"


def test_button_with_haspopup_is_combobox():
    """Custom React selects render as <button aria-haspopup="listbox">."""
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({
        "tag": "BUTTON", "role": "",
        "aria_haspopup": "listbox",
    }) == "combobox"


def test_unknown_falls_back_to_text():
    from jobpulse.form_engine.semantic_scanner import classify_widget
    assert classify_widget({"tag": "DIV", "role": ""}) == "text"
```

- [ ] **Step 2: Implement**

Append to `jobpulse/form_engine/semantic_scanner.py`:

```python
def classify_widget(meta: dict) -> str:
    """Map the matched element's tag/role/aria to a fill-handler input_type.

    Returns one of: text, textarea, select, combobox, switch,
    radio_group, checkbox, file. The dispatcher in
    NativeFormFiller._fill_by_label has handlers for all of these.
    """
    tag = (meta.get("tag") or "").upper()
    role = (meta.get("role") or "").lower()
    haspopup = (meta.get("aria_haspopup") or "").lower()

    if role == "switch":
        return "switch"
    if role == "checkbox":
        return "checkbox"
    if role == "radio":
        return "radio_group"
    if role == "combobox":
        return "combobox"
    if role == "listbox":
        return "combobox"
    if tag == "SELECT":
        return "select"
    if tag == "TEXTAREA":
        return "textarea"
    if tag == "BUTTON" and haspopup in ("listbox", "true", "menu"):
        return "combobox"
    if tag == "INPUT":
        # The matcher already excluded hidden/submit/button via querySelector.
        return "text"
    return "text"
```

- [ ] **Step 3: Update the JS in `match_question_to_widget` to also return `aria_haspopup`**

Find the two `return { ... }` objects in the JS embedded in
`match_question_to_widget` and add to each:
```js
aria_haspopup: w.getAttribute('aria-haspopup') || '',
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_semantic_widget_classifier.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/semantic_scanner.py tests/jobpulse/test_semantic_widget_classifier.py
git commit -m "feat(scanner): semantic widget classifier

Maps a matched element's (tag, role, aria-haspopup) to one of the
input_type values the dispatcher handles: text, textarea, select,
combobox, switch, radio_group, checkbox.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: Wrap into a `_scan_semantic` strategy

**Files:**
- Modify: `jobpulse/form_engine/semantic_scanner.py:end-of-file`
- Modify: `jobpulse/form_engine/field_scanner.py:_run_all_strategies_parallel`
- Test: `tests/jobpulse/test_scan_semantic_strategy.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_scan_semantic_strategy.py
"""scan_semantic combines extract + match + classify into a strategy."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


@pytest.mark.asyncio
async def test_scan_semantic_returns_field_dicts_for_each_question():
    from jobpulse.form_engine.semantic_scanner import scan_semantic, Question

    page = MagicMock()
    page.url = "https://welovealfa.com/.../apply"

    fake_questions = [
        Question(text="Do you require visa sponsorship?", y=300, dom_path=""),
        Question(text="What is your notice period?", y=400, dom_path=""),
    ]
    fake_widgets = [
        {"matched": True, "selector": "#visa", "tag": "BUTTON",
         "role": "button", "aria_haspopup": "listbox",
         "ancestor_classes": "", "y": 360, "distance_px": 60,
         "match_kind": "proximity"},
        {"matched": True, "selector": "#notice", "tag": "SELECT",
         "role": "", "ancestor_classes": "", "y": 460, "distance_px": 60,
         "match_kind": "proximity"},
    ]

    with patch("jobpulse.form_engine.semantic_scanner.extract_visible_questions",
               AsyncMock(return_value=fake_questions)), \
         patch("jobpulse.form_engine.semantic_scanner.match_question_to_widget",
               AsyncMock(side_effect=fake_widgets)):
        out = await scan_semantic(page)

    assert len(out) == 2
    assert out[0]["label"] == "Do you require visa sponsorship?"
    assert out[0]["type"] == "combobox"  # button + haspopup=listbox
    assert out[0]["selector"] == "#visa"
    assert out[0]["semantic_match"] is True

    assert out[1]["label"] == "What is your notice period?"
    assert out[1]["type"] == "select"


@pytest.mark.asyncio
async def test_scan_semantic_drops_unmatched_questions():
    from jobpulse.form_engine.semantic_scanner import scan_semantic, Question

    page = MagicMock()
    page.url = "https://example.com/apply"

    with patch("jobpulse.form_engine.semantic_scanner.extract_visible_questions",
               AsyncMock(return_value=[Question(text="Q1?", y=100, dom_path="")])), \
         patch("jobpulse.form_engine.semantic_scanner.match_question_to_widget",
               AsyncMock(return_value={"matched": False})):
        out = await scan_semantic(page)

    assert out == []
```

- [ ] **Step 2: Implement**

Append to `jobpulse/form_engine/semantic_scanner.py`:

```python
async def scan_semantic(page: Any) -> list[dict]:
    """Strategy entry: extract questions → match widgets → classify.

    Returns field dicts with `semantic_match=True` so downstream code
    can treat them with appropriate care (the locator is built from a
    selector string, may need re-resolution if the SPA re-renders).
    """
    qs = await extract_visible_questions(page)
    if not qs:
        return []
    fields: list[dict] = []
    for q in qs:
        meta = await match_question_to_widget(q, page)
        if not meta or not meta.get("matched"):
            continue
        widget_type = classify_widget(meta)
        fields.append({
            "label": q.text,
            "type": widget_type,
            "value": "",
            "selector": meta.get("selector", ""),
            "semantic_match": True,
            "ancestor_classes": meta.get("ancestor_classes", ""),
            "match_kind": meta.get("match_kind", ""),
            "distance_px": meta.get("distance_px", 0),
        })
    if fields:
        logger.info(
            "scan_semantic: matched %d/%d questions to widgets on %s",
            len(fields), len(qs), page.url[:80],
        )
    return fields
```

- [ ] **Step 3: Wire into `_run_all_strategies_parallel`**

Edit `jobpulse/form_engine/field_scanner.py:_run_all_strategies_parallel`,
adding `scan_semantic` to the strategy list:

```python
from jobpulse.form_engine.semantic_scanner import scan_semantic  # at top

# Inside _run_all_strategies_parallel:
strategies = [
    ("a11y_tree",         _scan_a11y_tree),
    ("dom_query",         _scan_dom_query),
    ("playwright_locators", scan_fields_locator_fallback),
    ("semantic",          lambda p: scan_semantic(p)),  # NEW
]
```

`_merge_fields` handles deduplication by label — same labels from
shape-based and semantic strategies will collapse to one entry.
Semantic-match field dicts retain `semantic_match=True` so consumers
can prefer them when label clashes resolve to multiple candidates.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/jobpulse/test_scan_semantic_strategy.py tests/jobpulse/test_native_form_filler.py -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/semantic_scanner.py jobpulse/form_engine/field_scanner.py tests/jobpulse/test_scan_semantic_strategy.py
git commit -m "feat(scanner): wire semantic strategy into parallel runner

scan_semantic now runs alongside a11y_tree, dom_query, and
playwright_locators in _run_all_strategies_parallel. _merge_fields
collapses same-label dupes; fields tagged semantic_match=True so
later consumers can prefer them in label clashes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: Dispatcher uses semantic-attached selector

**Files:**
- Modify: `jobpulse/native_form_filler.py:_fill_by_label`
- Test: `tests/jobpulse/test_dispatcher_uses_semantic_selector.py`

The dispatcher today resolves locators by `page.get_by_label(label)`
which fails when the label is a question that doesn't have a paired
`<label>` element. Semantic-match fields carry a CSS selector — use
that directly when present.

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_dispatcher_uses_semantic_selector.py
"""When a field has selector + semantic_match=True, dispatcher uses
locator(selector) instead of get_by_label."""
import inspect


def test_fill_by_label_consults_field_metadata_for_semantic_selector():
    from jobpulse.native_form_filler import NativeFormFiller
    src = inspect.getsource(NativeFormFiller)
    # Some path must reach into self._fields_by_label / similar to
    # retrieve the field's selector before falling back to get_by_label
    assert "semantic_match" in src
    assert "page.locator" in src
```

- [ ] **Step 2: Implement**

In `NativeFormFiller`, find where the field map is built (look for
`fields_by_label = {f["label"]: f for f in fields}` — added by an
existing commit). Then in `_fill_by_label`, BEFORE the existing
`page.get_by_label(base_label)` line, consult the map:

```python
async def _fill_by_label(self, label: str, value: str) -> dict:
    page = self._page
    # ... existing pre-amble ...

    # NEW: if the field came from the semantic scanner with a selector,
    # use it directly. Avoids label-string resolution that fails when
    # the label is a free-form question without a paired <label>.
    field_meta = getattr(self, "_fields_by_label", {}).get(label)
    if field_meta and field_meta.get("semantic_match") and field_meta.get("selector"):
        try:
            sem_loc = page.locator(field_meta["selector"]).first
            if await sem_loc.count():
                return await self._fill_resolved_locator(
                    sem_loc, label, value,
                    input_type=field_meta.get("type") or "text",
                )
        except Exception as exc:
            logger.debug("semantic selector resolve failed for %r: %s", label, exc)

    # Existing path:
    locator = page.get_by_label(base_label, exact=False)
    # ... rest unchanged ...
```

Also extract the existing post-resolution fill logic into
`_fill_resolved_locator(self, loc, label, value, *, input_type)` so
both paths share it. (This is a small refactor — copy the body of
`_fill_by_label` from after the locator is resolved into a new method.)

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/jobpulse/test_dispatcher_uses_semantic_selector.py tests/jobpulse/test_native_form_filler.py -v
```
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py tests/jobpulse/test_dispatcher_uses_semantic_selector.py
git commit -m "feat(filler): dispatcher uses semantic-scanner selector directly

When the matched field carries semantic_match=True + selector,
_fill_by_label uses page.locator(selector) instead of
page.get_by_label(label). Resolves the label-without-DOM-anchor
problem that left semantic-detected widgets unfillable.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: End-to-end validation against Revolut regression

- [ ] **Step 1: Reset Revolut + run agent**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('data/applications.db')
c.execute(\"UPDATE applications SET status='Pending Approval' WHERE job_id IN (SELECT job_id FROM job_listings WHERE company='Revolut')\")
c.commit()
"
rm -f data/live_review_active.json
OLLAMA_BASE_URL=http://localhost:1 JOB_AUTOPILOT_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next 1
```

- [ ] **Step 2: Inspect log for `scan_semantic` activations + recovered fields**

Expected:
```
scan_semantic: matched 4-5/12 questions to widgets on welovealfa.com
```
With visa-sponsorship, notice-period, distributed-engines, and
country-of-residence appearing in the form_interaction_log fields.

- [ ] **Step 3: Verify fill-through**

The agent should reach the approval gate with these fields actually
*filled* (not just detected). Check pre-submit screenshot in Telegram.

- [ ] **Step 4: If validation passes, no further commit. Otherwise file follow-ups.**

---

### Future work outside this plan

- Hybrid score: when both shape and semantic strategies emit a same-label field, use a confidence score to pick. Today `_merge_fields` is first-wins-by-label.
- Vision augment integration (covered by separate plan `2026-05-05-vision-fallback-gate.md`) is complementary — semantic catches what DOM-text exposes; vision catches what only the rendered pixels show.
- Per-domain learning (covered by `2026-05-05-learned-widget-patterns.md`) is also complementary — once a semantic match works, store the (selector, widget_type) so future runs hit Strategy 0 before re-running text extraction.
