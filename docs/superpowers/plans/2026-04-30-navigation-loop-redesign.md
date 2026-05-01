# Navigation Loop 5-Phase Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blind-replay + reasoner-loop navigation in `_navigator.py` with a 5-phase sequential pipeline (OBSERVE -> ANALYZE -> MATCH -> PLAN -> ACT) that always scans, fingerprints, and scores pages before acting.

**Architecture:** Every navigation step runs 5 phases sequentially, accumulating data in a `StepContext` dataclass. Learned sequences are enriched with `PageFingerprint` data and matched via weighted scoring (threshold 0.7) instead of blind replay. Post-action verification detects ghost clicks. All existing subsystems (PageReasoner, PageTypeClassifier, NavigationActionExecutor, BrowserIntelligence, etc.) keep their current interfaces; only the orchestration in `_navigator.py` changes.

**Tech Stack:** Python 3.12, Playwright (async), SQLite (NavigationLearner), pytest

**Spec:** `docs/superpowers/specs/2026-04-30-navigation-loop-redesign-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `jobpulse/application_orchestrator_pkg/_navigator.py` | Modify | Main rewrite: add dataclasses (`TabState`, `PageFingerprint`, `StepContext`), add helpers (`build_page_fingerprint`, `score_fingerprint_match`, `_compute_content_hash`, `_detect_ghost_click`, `_make_result`), add 5 phase methods (`_phase_observe`, `_phase_analyze`, `_phase_match`, `_phase_plan`, `_phase_act`), rewrite `navigate_to_form` main loop, remove blind replay block + `_reasoner_step` + `_dom_classify` + `_handle_new_tabs` |
| `tests/jobpulse/test_navigation_phases.py` | Create | Unit tests for all 5 phases, fingerprinting, match scoring, ghost click detection, and the rewritten main loop |

No changes to: `navigation_learner.py`, `page_analysis/page_reasoner.py`, `page_analysis/classifier.py`, `navigation/action_executor.py`, `browser_intelligence.py`, `signal_interpreter.py`, `form_experience_db.py`.

---

### Task 1: Data Model — TabState, PageFingerprint, StepContext

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py:1-25` (imports + new dataclasses)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests for the data model**

Create `tests/jobpulse/test_navigation_phases.py`:

```python
"""Tests for the 5-phase navigation pipeline."""
import hashlib
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from jobpulse.application_orchestrator_pkg._navigator import (
    TabState,
    PageFingerprint,
    StepContext,
    build_page_fingerprint,
    score_fingerprint_match,
)
from jobpulse.form_models import PageType


class TestTabState:
    def test_enum_values(self):
        assert TabState.NORMAL.value == "normal"
        assert TabState.NEW_TAB.value == "new_tab"
        assert TabState.POPUP.value == "popup"
        assert TabState.CLOSED.value == "closed"
        assert TabState.REDIRECTED.value == "redirected"


class TestPageFingerprint:
    def test_creation(self):
        fp = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now", "Save"),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.92,
            url_path_pattern="/jobs/{id}",
        )
        assert fp.field_count == 5
        assert fp.button_texts == ("Apply Now", "Save")
        assert fp.url_path_pattern == "/jobs/{id}"

    def test_to_dict(self):
        fp = PageFingerprint(
            field_count=3,
            button_texts=("Next",),
            content_hash="def456",
            has_dialog=True,
            has_file_inputs=False,
            page_type="login_form",
            dom_confidence=0.85,
            url_path_pattern="/login",
        )
        d = fp.to_dict()
        assert d["field_count"] == 3
        assert d["button_texts"] == ["Next"]
        assert d["page_type"] == "login_form"

    def test_from_dict(self):
        d = {
            "field_count": 2,
            "button_texts": ["Submit"],
            "content_hash": "xyz",
            "has_dialog": False,
            "has_file_inputs": False,
            "page_type": "unknown",
            "dom_confidence": 0.5,
            "url_path_pattern": "/apply",
        }
        fp = PageFingerprint.from_dict(d)
        assert fp.field_count == 2
        assert fp.button_texts == ("Submit",)


class TestStepContext:
    def test_defaults(self):
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        assert ctx.dom_type == PageType.UNKNOWN
        assert ctx.dom_confidence == 0.0
        assert ctx.match_score == 0.0
        assert ctx.planned_action is None
        assert ctx.ghost_click is False
        assert ctx.overlays_detected == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestTabState -v`
Expected: FAIL — `TabState` not importable.

- [ ] **Step 3: Implement the data model**

At the top of `jobpulse/application_orchestrator_pkg/_navigator.py`, add these imports and dataclasses after the existing imports (before `MAX_NAVIGATION_STEPS`):

```python
import hashlib
import re
from enum import Enum
from dataclasses import dataclass, field as dc_field
from typing import Any

from jobpulse.form_models import PageType
from jobpulse.page_analysis.page_reasoner import PageAction


class TabState(Enum):
    NORMAL = "normal"
    NEW_TAB = "new_tab"
    POPUP = "popup"
    CLOSED = "closed"
    REDIRECTED = "redirected"


@dataclass
class PageFingerprint:
    field_count: int
    button_texts: tuple[str, ...]
    content_hash: str
    has_dialog: bool
    has_file_inputs: bool
    page_type: str
    dom_confidence: float
    url_path_pattern: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field_count": self.field_count,
            "button_texts": list(self.button_texts),
            "content_hash": self.content_hash,
            "has_dialog": self.has_dialog,
            "has_file_inputs": self.has_file_inputs,
            "page_type": self.page_type,
            "dom_confidence": self.dom_confidence,
            "url_path_pattern": self.url_path_pattern,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PageFingerprint:
        return cls(
            field_count=d.get("field_count", 0),
            button_texts=tuple(d.get("button_texts", ())),
            content_hash=d.get("content_hash", ""),
            has_dialog=d.get("has_dialog", False),
            has_file_inputs=d.get("has_file_inputs", False),
            page_type=d.get("page_type", "unknown"),
            dom_confidence=d.get("dom_confidence", 0.0),
            url_path_pattern=d.get("url_path_pattern", ""),
        )


@dataclass
class StepContext:
    snapshot: dict[str, Any]
    url: str
    tab_state: TabState

    tab_recovered: bool = False

    dom_type: PageType = PageType.UNKNOWN
    dom_confidence: float = 0.0
    page_features: Any = None
    browser_signals: list[dict] | None = None
    overlays_detected: list[str] = dc_field(default_factory=list)
    wall_detected: dict | None = None
    page_fingerprint: PageFingerprint | None = None

    learned_step: dict | None = None
    match_score: float = 0.0
    match_source: str = ""

    planned_action: PageAction | None = None
    plan_source: str = ""

    action_executed: bool = False
    post_snapshot: dict | None = None
    ghost_click: bool = False


TERMINAL_ACTIONS = frozenset({"fill_form", "done", "abort"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestTabState tests/jobpulse/test_navigation_phases.py::TestPageFingerprint tests/jobpulse/test_navigation_phases.py::TestStepContext -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): add TabState, PageFingerprint, StepContext data model"
```

---

### Task 2: Fingerprint Builder + Match Scorer

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add module-level helpers)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests for fingerprint building and scoring**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestBuildPageFingerprint:
    def test_basic_snapshot(self):
        snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/12345",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [
                {"text": "Apply Now"},
                {"text": "Save"},
                {"text": "Apply Now"},  # duplicate
            ],
            "fields": [
                {"label": "First Name", "input_type": "text"},
                {"label": "Last Name", "input_type": "text"},
            ],
            "has_dialog": False,
            "has_file_inputs": True,
        }
        fp = build_page_fingerprint(snapshot, page_type="application_form", dom_confidence=0.9)
        assert fp.field_count == 2
        assert fp.button_texts == ("Apply Now", "Save")  # sorted, deduplicated
        assert fp.has_dialog is False
        assert fp.has_file_inputs is True
        assert fp.page_type == "application_form"
        assert fp.dom_confidence == 0.9
        assert fp.url_path_pattern == "/company/jobs/{id}"
        assert len(fp.content_hash) == 16  # 16-char hex

    def test_url_id_replacement(self):
        snapshot = {
            "url": "https://example.com/apply/98765/form",
            "page_text_preview": "",
            "buttons": [],
            "fields": [],
        }
        fp = build_page_fingerprint(snapshot, page_type="unknown", dom_confidence=0.5)
        assert fp.url_path_pattern == "/apply/{id}/form"

    def test_button_truncation(self):
        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "",
            "buttons": [{"text": "A" * 50}],
            "fields": [],
        }
        fp = build_page_fingerprint(snapshot, page_type="unknown", dom_confidence=0.5)
        assert len(fp.button_texts[0]) == 20

    def test_empty_snapshot(self):
        fp = build_page_fingerprint({}, page_type="unknown", dom_confidence=0.0)
        assert fp.field_count == 0
        assert fp.button_texts == ()
        assert fp.url_path_pattern == ""


class TestScoreFingerprintMatch:
    def test_identical_fingerprints(self):
        fp = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now", "Save"),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        score = score_fingerprint_match(fp, fp.to_dict())
        assert score == 1.0

    def test_completely_different(self):
        current = PageFingerprint(
            field_count=10,
            button_texts=("Submit",),
            content_hash="aaa",
            has_dialog=True,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.9,
            url_path_pattern="/apply",
        )
        learned = {
            "field_count": 0,
            "button_texts": ["Save"],
            "content_hash": "zzz",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        score = score_fingerprint_match(current, learned)
        assert score < 0.3

    def test_same_page_type_different_content(self):
        current = PageFingerprint(
            field_count=5,
            button_texts=("Next", "Back"),
            content_hash="aaa",
            has_dialog=False,
            has_file_inputs=False,
            page_type="application_form",
            dom_confidence=0.8,
            url_path_pattern="/apply/{id}",
        )
        learned = {
            "field_count": 7,
            "button_texts": ["Next", "Back", "Save"],
            "content_hash": "bbb",
            "page_type": "application_form",
            "url_path_pattern": "/apply/{id}",
        }
        score = score_fingerprint_match(current, learned)
        # page_type matches (0.30) + url matches (0.15) + field close (0.15*0.8) + button overlap (0.15*0.67) = ~0.67
        assert 0.5 < score < 0.8

    def test_old_format_no_fingerprint(self):
        current = PageFingerprint(
            field_count=5,
            button_texts=("Apply Now",),
            content_hash="abc",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        # Old format: no fingerprint key, just page_type
        learned_step = {"page_type": "job_description", "action": "click_apply"}
        score = score_fingerprint_match(current, learned_step.get("fingerprint"))
        assert score == 0.0  # None fingerprint returns 0.0

    def test_threshold_boundary(self):
        current = PageFingerprint(
            field_count=3,
            button_texts=("Apply Now",),
            content_hash="same_hash",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        learned = {
            "field_count": 3,
            "button_texts": ["Apply Now"],
            "content_hash": "same_hash",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        score = score_fingerprint_match(current, learned)
        assert score >= 0.7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestBuildPageFingerprint -v`
Expected: FAIL — `build_page_fingerprint` not importable.

- [ ] **Step 3: Implement fingerprint builder and match scorer**

Add these module-level functions to `_navigator.py`, after the `TERMINAL_ACTIONS` constant and before the `FormNavigator` class:

```python
_NUMERIC_ID_RE = re.compile(r"/\d{3,}")


def _normalize_url_path(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") if parsed.path else ""
    return _NUMERIC_ID_RE.sub("/{id}", path)


def _compute_content_hash(url_path: str, page_text: str, field_labels: list[str], button_texts: list[str]) -> str:
    raw = "|".join([url_path, page_text[:500], ",".join(sorted(field_labels)), ",".join(sorted(button_texts))])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_page_fingerprint(snapshot: dict[str, Any], page_type: str, dom_confidence: float) -> PageFingerprint:
    url = snapshot.get("url", "")
    buttons = snapshot.get("buttons", [])
    fields = snapshot.get("fields", [])
    page_text = snapshot.get("page_text_preview", "")

    btn_texts = sorted({b.get("text", "")[:20] for b in buttons if b.get("text", "").strip()})
    field_labels = [f.get("label", "") for f in fields if f.get("label")]
    url_path = _normalize_url_path(url)

    return PageFingerprint(
        field_count=len(fields),
        button_texts=tuple(btn_texts),
        content_hash=_compute_content_hash(url_path, page_text, field_labels, btn_texts),
        has_dialog=bool(snapshot.get("has_dialog") or snapshot.get("modal_detected")),
        has_file_inputs=bool(snapshot.get("has_file_inputs")),
        page_type=page_type,
        dom_confidence=dom_confidence,
        url_path_pattern=url_path,
    )


def score_fingerprint_match(current: PageFingerprint, learned_fp: dict[str, Any] | None) -> float:
    if not learned_fp:
        return 0.0

    score = 0.0

    if current.page_type == learned_fp.get("page_type"):
        score += 0.30
    if current.content_hash == learned_fp.get("content_hash"):
        score += 0.25

    learned_fc = learned_fp.get("field_count", 0)
    diff = abs(current.field_count - learned_fc)
    score += 0.15 * (1.0 - min(diff / 10.0, 1.0))

    learned_btns = set(learned_fp.get("button_texts", []))
    current_btns = set(current.button_texts)
    if learned_btns or current_btns:
        union = learned_btns | current_btns
        intersection = learned_btns & current_btns
        score += 0.15 * (len(intersection) / len(union))
    else:
        score += 0.15

    if current.url_path_pattern == learned_fp.get("url_path_pattern"):
        score += 0.15

    return round(score, 4)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestBuildPageFingerprint tests/jobpulse/test_navigation_phases.py::TestScoreFingerprintMatch -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): add page fingerprint builder and match scorer"
```

---

### Task 3: Ghost Click Detection + Content Hash + _make_result

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add static helpers to `FormNavigator`)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
from jobpulse.application_orchestrator_pkg._navigator import FormNavigator


class TestGhostClickDetection:
    def test_nothing_changed_is_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="aaa",
            post_dialog=False,
        ) is True

    def test_url_changed_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/apply/1",
            post_content_hash="aaa",
            post_dialog=False,
        ) is False

    def test_content_changed_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="bbb",
            post_dialog=False,
        ) is False

    def test_dialog_appeared_not_ghost(self):
        assert FormNavigator._detect_ghost_click(
            pre_url="https://example.com/jobs/1",
            pre_content_hash="aaa",
            pre_dialog=False,
            post_url="https://example.com/jobs/1",
            post_content_hash="aaa",
            post_dialog=True,
        ) is False


class TestSnapshotContentHash:
    def test_basic(self):
        snapshot = {
            "page_text_preview": "Hello world",
            "fields": [{"label": "Name"}],
            "buttons": [{"text": "Submit"}],
        }
        h = FormNavigator._snapshot_content_hash(snapshot)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_different_content_different_hash(self):
        s1 = {"page_text_preview": "Page A", "fields": [], "buttons": []}
        s2 = {"page_text_preview": "Page B", "fields": [], "buttons": []}
        assert FormNavigator._snapshot_content_hash(s1) != FormNavigator._snapshot_content_hash(s2)

    def test_same_content_same_hash(self):
        s = {"page_text_preview": "Same", "fields": [{"x": 1}], "buttons": []}
        assert FormNavigator._snapshot_content_hash(s) == FormNavigator._snapshot_content_hash(s)


class TestMakeResult:
    def test_fill_form_returns_application_form(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Form ready",
            action="fill_form",
            target_text="",
            reasoning="ready",
            confidence=0.9,
            page_type="application_form",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.APPLICATION_FORM
        assert result["snapshot"] == ctx.snapshot

    def test_done_returns_confirmation(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Submitted",
            action="done",
            target_text="",
            reasoning="confirmed",
            confidence=0.95,
            page_type="confirmation",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.CONFIRMATION

    def test_abort_returns_unknown(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Can't proceed",
            action="abort",
            target_text="",
            reasoning="blocked",
            confidence=0.8,
            page_type="unknown",
        )
        result = FormNavigator._make_result(ctx)
        assert result["page_type"] == PageType.UNKNOWN

    def test_expired_job_sets_expired_flag(self):
        from jobpulse.page_analysis.page_reasoner import PageAction
        ctx = StepContext(
            snapshot={"url": "https://example.com/job/closed"},
            url="https://example.com/job/closed",
            tab_state=TabState.NORMAL,
        )
        ctx.planned_action = PageAction(
            page_understanding="Job no longer available",
            action="abort",
            target_text="",
            reasoning="expired",
            confidence=0.9,
            page_type="expired_job",
        )
        result = FormNavigator._make_result(ctx)
        assert result["expired"] is True
        assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestGhostClickDetection -v`
Expected: FAIL — `_detect_ghost_click` not found.

- [ ] **Step 3: Implement the helpers**

Add these static methods to the `FormNavigator` class (after `_as_dict`, before `_dismiss_linkedin_discard`):

```python
@staticmethod
def _detect_ghost_click(
    pre_url: str, pre_content_hash: str, pre_dialog: bool,
    post_url: str, post_content_hash: str, post_dialog: bool,
) -> bool:
    return (pre_url == post_url
            and pre_content_hash == post_content_hash
            and pre_dialog == post_dialog)

@staticmethod
def _snapshot_content_hash(snapshot: dict[str, Any]) -> str:
    text = snapshot.get("page_text_preview", "")[:300]
    fc = str(len(snapshot.get("fields", [])))
    bc = str(len(snapshot.get("buttons", [])))
    return hashlib.sha256(f"{text}|{fc}|{bc}".encode()).hexdigest()[:16]

@staticmethod
def _make_result(ctx: StepContext) -> dict[str, Any]:
    action = ctx.planned_action
    act = action.action if action else "abort"
    pt = action.page_type if action else "unknown"

    if act == "fill_form":
        result: dict[str, Any] = {"page_type": PageType.APPLICATION_FORM, "snapshot": ctx.snapshot}
    elif act == "done":
        result = {"page_type": PageType.CONFIRMATION, "snapshot": ctx.snapshot}
    else:
        result = {"page_type": PageType.UNKNOWN, "snapshot": ctx.snapshot}

    if pt == "expired_job":
        result["expired"] = True
        result["error"] = (action.page_understanding if action else "") or "Job is no longer available"

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestGhostClickDetection tests/jobpulse/test_navigation_phases.py::TestSnapshotContentHash tests/jobpulse/test_navigation_phases.py::TestMakeResult -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): add ghost click detection, content hash, and result builder"
```

---

### Task 4: Phase OBSERVE

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add `_phase_observe` method)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
@pytest.fixture
def mock_navigator():
    """Build a FormNavigator with fully mocked orchestrator."""
    orch = MagicMock()
    page = AsyncMock()
    page.url = "https://example.com/jobs/123"
    page.is_closed = MagicMock(return_value=False)
    context = MagicMock()
    context.pages = [page]
    page.context = context

    driver = MagicMock()
    driver.page = page
    driver._page = page
    driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/jobs/123", "buttons": [], "fields": []})
    driver.intelligence = None
    orch.driver = driver
    orch.analyzer = MagicMock()
    orch.cookie_dismisser = MagicMock()
    orch.cookie_dismisser.dismiss = AsyncMock()
    orch.sso = MagicMock()
    orch.learner = MagicMock()

    auth = MagicMock()
    nav = FormNavigator(orch, auth)
    return nav, driver, page, context


class TestPhaseObserve:
    @pytest.mark.asyncio
    async def test_normal_state_single_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.NORMAL
        assert result.tab_recovered is False

    @pytest.mark.asyncio
    async def test_detects_new_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        new_page = AsyncMock()
        new_page.url = "https://ats.example.com/apply"
        new_page.is_closed = MagicMock(return_value=False)
        new_page.wait_for_load_state = AsyncMock()
        context.pages = [page, new_page]
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.example.com/apply", "buttons": [], "fields": []})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.NEW_TAB
        assert result.tab_recovered is True
        assert driver._page == new_page

    @pytest.mark.asyncio
    async def test_detects_redirect(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        page.url = "https://example.com/redirected"
        driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/redirected", "buttons": [], "fields": []})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.REDIRECTED
        assert result.snapshot["url"] == "https://example.com/redirected"

    @pytest.mark.asyncio
    async def test_detects_closed_page(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        page.is_closed = MagicMock(return_value=True)

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        result = await nav._phase_observe(ctx)
        assert result.tab_state == TabState.CLOSED

    @pytest.mark.asyncio
    async def test_reinjects_browser_intelligence_on_new_tab(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        intelligence = AsyncMock()
        driver.intelligence = intelligence
        new_page = AsyncMock()
        new_page.url = "https://ats.example.com/apply"
        new_page.is_closed = MagicMock(return_value=False)
        new_page.wait_for_load_state = AsyncMock()
        context.pages = [page, new_page]
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.example.com/apply"})

        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
        )
        await nav._phase_observe(ctx)
        intelligence.clear.assert_called_once()
        intelligence.inject_on_new_page.assert_awaited_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseObserve::test_normal_state_single_tab -v`
Expected: FAIL — `_phase_observe` not found.

- [ ] **Step 3: Implement `_phase_observe`**

Add this method to `FormNavigator`, after the static helpers:

```python
async def _phase_observe(self, ctx: StepContext) -> StepContext:
    page = getattr(self.driver, "page", None)
    if page is None:
        return ctx

    if hasattr(page, "is_closed") and page.is_closed():
        ctx.tab_state = TabState.CLOSED
        return ctx

    browser_ctx = getattr(page, "context", None)
    if browser_ctx is not None:
        pages = browser_ctx.pages
        if len(pages) > 1:
            newest = pages[-1]
            if newest != page and not (hasattr(newest, "is_closed") and newest.is_closed()):
                try:
                    await newest.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                logger.info("OBSERVE: new tab detected — switching to %s", newest.url[:80])
                self.driver._page = newest
                ctx.tab_state = TabState.NEW_TAB
                ctx.tab_recovered = True
                intelligence = getattr(self.driver, "intelligence", None)
                if intelligence:
                    intelligence.clear()
                    await intelligence.inject_on_new_page()
                ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                ctx.url = ctx.snapshot.get("url", "")
                return ctx

    current_url = page.url or ""
    if current_url and current_url != ctx.url:
        logger.info("OBSERVE: redirect detected — %s → %s", ctx.url[:50], current_url[:50])
        ctx.tab_state = TabState.REDIRECTED
        ctx.tab_recovered = True
        ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
        ctx.url = ctx.snapshot.get("url", "")
        intelligence = getattr(self.driver, "intelligence", None)
        if intelligence:
            intelligence.clear()
            await intelligence.inject_on_new_page()
        return ctx

    ctx.snapshot = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
    ctx.url = ctx.snapshot.get("url", "")
    return ctx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseObserve -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): implement OBSERVE phase — proactive tab/redirect detection"
```

---

### Task 5: Phase ANALYZE

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add `_phase_analyze` method)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestPhaseAnalyze:
    @pytest.mark.asyncio
    async def test_classifies_page_and_builds_fingerprint(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123",
            "page_text_preview": "Apply for Software Engineer",
            "buttons": [{"text": "Apply Now"}],
            "fields": [{"label": "Name", "input_type": "text"}],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.JOB_DESCRIPTION, 0.85)
            result = await nav._phase_analyze(ctx)

        assert result.dom_type == PageType.JOB_DESCRIPTION
        assert result.dom_confidence == 0.85
        assert result.page_fingerprint is not None
        assert result.page_fingerprint.page_type == "job_description"
        assert result.page_fingerprint.field_count == 1

    @pytest.mark.asyncio
    async def test_detects_verification_wall(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "Checking your browser",
            "buttons": [],
            "fields": [],
            "verification_wall": {"type": "cloudflare"},
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.VERIFICATION_WALL, 0.95)
            result = await nav._phase_analyze(ctx)

        assert result.wall_detected == {"type": "cloudflare"}

    @pytest.mark.asyncio
    async def test_dismisses_cookies_and_resnapshots(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        snapshot_before = {
            "url": "https://example.com",
            "page_text_preview": "Cookie consent dialog here",
            "buttons": [{"text": "Accept Cookies"}],
            "fields": [],
            "has_dialog": True,
            "dialog_text": "We use cookies. Accept?",
        }
        snapshot_after = {
            "url": "https://example.com",
            "page_text_preview": "Welcome to our site",
            "buttons": [{"text": "Apply"}],
            "fields": [],
            "has_dialog": False,
        }
        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            return snapshot_after if call_count[0] > 1 else snapshot_before
        driver.get_snapshot = _get_snap

        ctx = StepContext(snapshot=snapshot_before, url=snapshot_before["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf, \
             patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock) as mock_cookie:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.JOB_DESCRIPTION, 0.7)
            result = await nav._phase_analyze(ctx)

        # Cookies should have been dismissed
        nav.cookie_dismisser.dismiss.assert_awaited()

    @pytest.mark.asyncio
    async def test_reads_browser_signals(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        mock_signal = MagicMock()
        mock_signal.source = "console"
        mock_signal.level = "error"
        mock_signal.text = "validation failed"
        mock_signal.timestamp_ms = 1000.0
        mock_signal.url = "https://example.com"
        mock_signal.metadata = {}
        intelligence = MagicMock()
        intelligence.get_signals.return_value = [mock_signal]
        driver.intelligence = intelligence

        snapshot = {
            "url": "https://example.com",
            "page_text_preview": "Form",
            "buttons": [],
            "fields": [],
        }
        ctx = StepContext(snapshot=snapshot, url=snapshot["url"], tab_state=TabState.NORMAL)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf:
            clf_instance = MockClf.return_value
            clf_instance.classify.return_value = (PageType.APPLICATION_FORM, 0.9)
            result = await nav._phase_analyze(ctx)

        assert result.browser_signals is not None
        assert len(result.browser_signals) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseAnalyze::test_classifies_page_and_builds_fingerprint -v`
Expected: FAIL — `_phase_analyze` not found.

- [ ] **Step 3: Implement `_phase_analyze`**

Add this method to `FormNavigator`:

```python
async def _phase_analyze(self, ctx: StepContext) -> StepContext:
    from jobpulse.page_analysis.classifier import PageTypeClassifier
    clf = PageTypeClassifier()
    dom_type, dom_confidence = clf.classify(ctx.snapshot)
    ctx.dom_type = dom_type
    ctx.dom_confidence = dom_confidence

    ctx.page_fingerprint = build_page_fingerprint(
        ctx.snapshot,
        page_type=dom_type.value if hasattr(dom_type, "value") else str(dom_type),
        dom_confidence=dom_confidence,
    )

    intelligence = getattr(self.driver, "intelligence", None)
    if intelligence:
        try:
            signals = intelligence.get_signals()
            ctx.browser_signals = [
                {"source": s.source, "level": s.level, "text": s.text,
                 "timestamp_ms": s.timestamp_ms, "url": s.url}
                for s in signals
            ]
        except Exception:
            pass

    wall = ctx.snapshot.get("verification_wall")
    if wall:
        ctx.wall_detected = wall

    await self.cookie_dismisser.dismiss(ctx.snapshot)
    page = getattr(self.driver, "page", None)
    if page is not None:
        await dismiss_cookie_banner_playwright(page)

    ctx.snapshot = await self._dismiss_site_prompt_if_present(ctx.snapshot)

    return ctx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseAnalyze -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): implement ANALYZE phase — classify, fingerprint, signal capture, overlay dismissal"
```

---

### Task 6: Phase MATCH

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add `_phase_match` method)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestPhaseMatch:
    def test_matches_learned_sequence_above_threshold(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        fp = PageFingerprint(
            field_count=0,
            button_texts=("Apply Now",),
            content_hash="abc123",
            has_dialog=False,
            has_file_inputs=False,
            page_type="job_description",
            dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        learned_steps = [
            {
                "page_type": "job_description",
                "action": "click_apply",
                "fingerprint": fp.to_dict(),
            }
        ]
        nav.learner.get_sequence.return_value = learned_steps
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score >= 0.7
        assert result.learned_step is not None
        assert result.learned_step["action"] == "click_apply"
        assert result.match_source == "domain"

    def test_no_match_below_threshold(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        current_fp = PageFingerprint(
            field_count=10,
            button_texts=("Submit",),
            content_hash="xyz",
            has_dialog=True,
            has_file_inputs=True,
            page_type="application_form",
            dom_confidence=0.8,
            url_path_pattern="/apply",
        )
        learned_steps = [
            {
                "page_type": "job_description",
                "action": "click_apply",
                "fingerprint": {
                    "field_count": 0,
                    "button_texts": ["Apply Now"],
                    "content_hash": "other",
                    "page_type": "job_description",
                    "url_path_pattern": "/jobs/{id}",
                },
            }
        ]
        nav.learner.get_sequence.return_value = learned_steps
        ctx = StepContext(
            snapshot={"url": "https://example.com/apply"},
            url="https://example.com/apply",
            tab_state=TabState.NORMAL,
            page_fingerprint=current_fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score < 0.7
        assert result.learned_step is None
        assert result.match_source == "none"

    def test_no_learned_sequence(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.learner.get_sequence.return_value = None
        nav.learner.get_platform_pattern.return_value = None
        fp = PageFingerprint(
            field_count=0, button_texts=(), content_hash="x",
            has_dialog=False, has_file_inputs=False,
            page_type="unknown", dom_confidence=0.5,
            url_path_pattern="/",
        )
        ctx = StepContext(
            snapshot={"url": "https://new-site.com"},
            url="https://new-site.com",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "new-site.com", "", step_index=0)
        assert result.match_source == "none"
        assert result.learned_step is None

    def test_step_index_exceeds_sequence(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        learned_steps = [{"page_type": "job_description", "action": "click_apply", "fingerprint": {}}]
        nav.learner.get_sequence.return_value = learned_steps
        fp = PageFingerprint(
            field_count=5, button_texts=("Next",), content_hash="abc",
            has_dialog=False, has_file_inputs=False,
            page_type="application_form", dom_confidence=0.9,
            url_path_pattern="/apply",
        )
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=5)
        assert result.match_source == "none"

    def test_old_format_caps_at_04(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        learned_steps = [{"page_type": "job_description", "action": "click_apply"}]
        nav.learner.get_sequence.return_value = learned_steps
        fp = PageFingerprint(
            field_count=0, button_texts=("Apply Now",), content_hash="abc",
            has_dialog=False, has_file_inputs=False,
            page_type="job_description", dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/123"},
            url="https://example.com/jobs/123",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "example.com", "greenhouse", step_index=0)
        assert result.match_score <= 0.4
        assert result.learned_step is None

    def test_falls_back_to_platform_pattern(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.learner.get_sequence.return_value = None
        fp_dict = {
            "field_count": 0,
            "button_texts": ["Apply Now"],
            "content_hash": "abc123",
            "page_type": "job_description",
            "url_path_pattern": "/jobs/{id}",
        }
        nav.learner.get_platform_pattern.return_value = [
            {"page_type": "job_description", "action": "click_apply", "fingerprint": fp_dict}
        ]
        fp = PageFingerprint(
            field_count=0, button_texts=("Apply Now",), content_hash="abc123",
            has_dialog=False, has_file_inputs=False,
            page_type="job_description", dom_confidence=0.9,
            url_path_pattern="/jobs/{id}",
        )
        ctx = StepContext(
            snapshot={"url": "https://new-greenhouse.io/jobs/456"},
            url="https://new-greenhouse.io/jobs/456",
            tab_state=TabState.NORMAL,
            page_fingerprint=fp,
        )
        result = nav._phase_match(ctx, "new-greenhouse.io", "greenhouse", step_index=0)
        assert result.match_score >= 0.7
        assert result.match_source == "platform"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseMatch::test_matches_learned_sequence_above_threshold -v`
Expected: FAIL — `_phase_match` not found.

- [ ] **Step 3: Implement `_phase_match`**

Add this method to `FormNavigator`:

```python
def _phase_match(self, ctx: StepContext, domain: str, platform: str, step_index: int) -> StepContext:
    if ctx.page_fingerprint is None:
        ctx.match_source = "none"
        return ctx

    sequence = self.learner.get_sequence(domain)
    source = "domain"
    if not sequence and platform:
        sequence = self.learner.get_platform_pattern(platform, exclude_domain=domain)
        source = "platform"
    if not sequence:
        content_hash = ctx.page_fingerprint.content_hash if ctx.page_fingerprint else ""
        sequence = self.learner.get_sequence_by_content_hash(content_hash, exclude_domain=domain) if content_hash else None
        source = "content_hash"

    if not sequence:
        ctx.match_source = "none"
        return ctx

    if step_index >= len(sequence):
        ctx.match_source = "none"
        return ctx

    learned_step = sequence[step_index]
    learned_fp = learned_step.get("fingerprint")

    if not learned_fp:
        page_type_match = (ctx.page_fingerprint.page_type == learned_step.get("page_type", ""))
        ctx.match_score = 0.3 if page_type_match else 0.0
        ctx.match_source = "none"
        return ctx

    ctx.match_score = score_fingerprint_match(ctx.page_fingerprint, learned_fp)

    if ctx.match_score >= 0.7:
        ctx.learned_step = learned_step
        ctx.match_source = source
        logger.info("MATCH: score=%.2f from %s — using learned step: %s",
                     ctx.match_score, source, learned_step.get("action"))
    else:
        ctx.match_source = "none"
        logger.info("MATCH: score=%.2f (below 0.7) — falling through to reasoner", ctx.match_score)

    return ctx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseMatch -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): implement MATCH phase — score-based learned sequence matching"
```

---

### Task 7: Phase PLAN

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add `_phase_plan` method)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestPhasePlan:
    def test_wall_detected_returns_wait_human(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com"},
            url="https://example.com",
            tab_state=TabState.NORMAL,
            wall_detected={"type": "cloudflare"},
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action is not None
        assert result.planned_action.action == "wait_human"
        assert result.plan_source == "fast_path"

    def test_confirmation_with_high_confidence_returns_done(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
            dom_type=PageType.CONFIRMATION,
            dom_confidence=0.85,
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action.action == "done"
        assert result.plan_source == "fast_path"

    def test_confirmation_low_confidence_falls_to_reasoner(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/thanks"},
            url="https://example.com/thanks",
            tab_state=TabState.NORMAL,
            dom_type=PageType.CONFIRMATION,
            dom_confidence=0.5,
        )
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Confirmation page", action="done",
                target_text="", reasoning="confirmed", confidence=0.9,
                page_type="confirmation",
            )
            result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "reasoner"

    def test_learned_step_verified_click_apply(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "buttons": [{"text": "Apply Now", "enabled": True}],
                "fields": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            learned_step={"page_type": "job_description", "action": "click_apply"},
            match_score=0.85,
            match_source="domain",
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "learned_verified"
        assert result.planned_action.action == "click_apply"

    def test_learned_step_verification_fails_falls_to_reasoner(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={
                "url": "https://example.com/jobs/1",
                "buttons": [],  # No apply button
                "fields": [],
            },
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            learned_step={"page_type": "job_description", "action": "click_apply"},
            match_score=0.85,
            match_source="domain",
        )
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Job page", action="click_element",
                target_text="Apply", reasoning="found apply link", confidence=0.7,
                page_type="job_description",
            )
            result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.plan_source == "reasoner"

    def test_loop_detection_aborts(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com", "buttons": [], "fields": []},
            url="https://example.com",
            tab_state=TabState.NORMAL,
        )
        visited = {"unknown:click_element": 2}
        with patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as mock_reasoner:
            mock_reasoner.return_value.reason_sync.return_value = PageAction(
                page_understanding="Stuck", action="click_element",
                target_text="Something", reasoning="trying", confidence=0.5,
                page_type="unknown",
            )
            result = nav._phase_plan(ctx, visited_states=visited, wall_bypass_attempts=0)
        assert result.planned_action.action == "abort"

    def test_application_form_high_confidence_returns_fill_form(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        ctx = StepContext(
            snapshot={"url": "https://example.com/apply", "buttons": [], "fields": [{"label": "Name"}]},
            url="https://example.com/apply",
            tab_state=TabState.NORMAL,
            dom_type=PageType.APPLICATION_FORM,
            dom_confidence=0.9,
        )
        result = nav._phase_plan(ctx, visited_states={}, wall_bypass_attempts=0)
        assert result.planned_action.action == "fill_form"
        assert result.plan_source == "fast_path"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhasePlan::test_wall_detected_returns_wait_human -v`
Expected: FAIL — `_phase_plan` not found.

- [ ] **Step 3: Implement `_phase_plan`**

Add this method to `FormNavigator`:

```python
def _phase_plan(self, ctx: StepContext, visited_states: dict[str, int], wall_bypass_attempts: int) -> StepContext:
    if ctx.wall_detected:
        ctx.planned_action = PageAction(
            page_understanding="Verification wall detected",
            action="wait_human",
            target_text="",
            reasoning=f"Wall type: {ctx.wall_detected.get('type', 'unknown')}",
            confidence=1.0,
            page_type="verification_wall",
        )
        ctx.plan_source = "fast_path"
        return ctx

    if ctx.dom_confidence >= 0.8 and ctx.dom_type == PageType.CONFIRMATION:
        ctx.planned_action = PageAction(
            page_understanding="Confirmation page detected",
            action="done",
            target_text="",
            reasoning=f"DOM confidence {ctx.dom_confidence:.2f}",
            confidence=ctx.dom_confidence,
            page_type="confirmation",
        )
        ctx.plan_source = "fast_path"
        return ctx

    if ctx.dom_confidence >= 0.8 and ctx.dom_type == PageType.APPLICATION_FORM:
        ctx.planned_action = PageAction(
            page_understanding="Application form detected",
            action="fill_form",
            target_text="",
            reasoning=f"DOM confidence {ctx.dom_confidence:.2f}",
            confidence=ctx.dom_confidence,
            page_type="application_form",
        )
        ctx.plan_source = "fast_path"
        return ctx

    if ctx.learned_step and ctx.match_score >= 0.7:
        learned_action = ctx.learned_step.get("action", "")
        if self._verify_learned_action(learned_action, ctx.snapshot):
            ctx.planned_action = PageAction(
                page_understanding=f"Learned step (score={ctx.match_score:.2f})",
                action=learned_action,
                target_text="",
                reasoning=f"Matched from {ctx.match_source}",
                confidence=ctx.match_score,
                page_type=ctx.learned_step.get("page_type", "unknown"),
            )
            ctx.plan_source = "learned_verified"
            logger.info("PLAN: using verified learned action '%s' (score=%.2f)", learned_action, ctx.match_score)
            return ctx
        logger.info("PLAN: learned action '%s' failed verification — falling to reasoner", learned_action)

    from jobpulse.page_analysis.page_reasoner import get_page_reasoner
    reasoner = get_page_reasoner()
    action = reasoner.reason_sync(ctx.snapshot)

    state_key = f"{action.page_type}:{action.action}"
    visited_states[state_key] = visited_states.get(state_key, 0) + 1
    if visited_states[state_key] >= 3:
        logger.warning("PLAN: loop detected — %s x%d — aborting", state_key, visited_states[state_key])
        ctx.planned_action = PageAction(
            page_understanding="Navigation loop detected",
            action="abort",
            target_text="",
            reasoning=f"State {state_key} repeated {visited_states[state_key]} times",
            confidence=0.0,
            page_type="unknown",
        )
        ctx.plan_source = "fast_path"
        return ctx

    if action.page_type == "expired_job":
        action = PageAction(
            page_understanding=action.page_understanding,
            action="abort",
            target_text="",
            reasoning=action.reasoning,
            confidence=action.confidence,
            page_type="expired_job",
        )

    if action.confidence < 0.3 and sum(1 for v in visited_states.values() if v >= 2) >= 2:
        try:
            from shared.cognitive import get_cognitive_engine
            engine = get_cognitive_engine()
            cog_result = engine.think(
                f"Navigation stuck: page_type={action.page_type}, action={action.action}, "
                f"confidence={action.confidence:.2f}, visited={visited_states}",
                domain="form_navigation",
            )
            if cog_result and cog_result.get("action"):
                logger.info("PLAN: CognitiveEngine escalation → %s", cog_result["action"])
        except Exception as exc:
            logger.debug("CognitiveEngine escalation failed: %s", exc)

    ctx.planned_action = action
    ctx.plan_source = "reasoner"
    logger.info("PLAN: reasoner → %s (type=%s, conf=%.2f)",
                action.action, action.page_type, action.confidence)
    return ctx

def _verify_learned_action(self, action: str, snapshot: dict) -> bool:
    if action in ("click_apply", "click_apply_guess", "linkedin_direct_apply"):
        return find_apply_button(snapshot) is not None
    if action.startswith("sso_"):
        provider = action[len("sso_"):]
        sso = self.sso.detect_sso(snapshot)
        return sso is not None and sso.get("provider") == provider
    if action in ("fill_login", "fill_signup"):
        fields = snapshot.get("fields", [])
        has_password = any(f.get("input_type") == "password" for f in fields)
        has_email = any(
            f.get("input_type") == "email" or "email" in f.get("label", "").lower()
            for f in fields
        )
        return has_password and has_email
    if action == "verify_email":
        text = (snapshot.get("page_text_preview") or "").lower()
        return "verify" in text or "check your email" in text
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhasePlan -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): implement PLAN phase — fast-path terminals, learned verification, reasoner fallback"
```

---

### Task 8: Phase ACT

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (add `_phase_act` method)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestPhaseAct:
    @pytest.mark.asyncio
    async def test_click_apply_dispatches(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.click_apply_button = AsyncMock(return_value={"url": "https://ats.com/apply", "buttons": [], "fields": []})
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.com/apply", "buttons": [], "fields": []})
        ctx = StepContext(
            snapshot={"url": "https://example.com/jobs/1", "buttons": [], "fields": []},
            url="https://example.com/jobs/1",
            tab_state=TabState.NORMAL,
            planned_action=PageAction(
                page_understanding="JD page", action="click_apply",
                target_text="", reasoning="apply", confidence=0.9,
                page_type="job_description",
            ),
            plan_source="learned_verified",
            page_fingerprint=PageFingerprint(
                field_count=0, button_texts=("Apply Now",), content_hash="abc",
                has_dialog=False, has_file_inputs=False,
                page_type="job_description", dom_confidence=0.9,
                url_path_pattern="/jobs/{id}",
            ),
        )
        result = await nav._phase_act(ctx, "greenhouse", [], 0)
        nav.click_apply_button.assert_awaited_once()
        assert result.action_executed is True
        assert result.post_snapshot is not None

    @pytest.mark.asyncio
    async def test_sso_action_dispatches(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        nav.sso.detect_sso.return_value = {"provider": "google", "selector": "#google-sso"}
        nav.sso.click_sso = AsyncMock()
        driver.get_snapshot = AsyncMock(return_value={"url": "https://example.com/sso-done", "buttons": [], "fields": []})
        ctx = StepContext(
            snapshot={"url": "https://example.com/login", "buttons": [], "fields": []},
            url="https://example.com/login",
            tab_state=TabState.NORMAL,
            planned_action=PageAction(
                page_understanding="Login", action="sso_google",
                target_text="", reasoning="sso", confidence=0.9,
                page_type="login_form",
            ),
            plan_source="learned_verified",
            page_fingerprint=PageFingerprint(
                field_count=2, button_texts=("Sign In",), content_hash="xyz",
                has_dialog=False, has_file_inputs=False,
                page_type="login_form", dom_confidence=0.8,
                url_path_pattern="/login",
            ),
        )
        result = await nav._phase_act(ctx, "greenhouse", [], 0)
        nav.sso.click_sso.assert_awaited_once()
        assert result.action_executed is True

    @pytest.mark.asyncio
    async def test_ghost_click_detected_and_retried(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        # Pre-action and post-action snapshots are identical → ghost click
        same_snapshot = {"url": "https://example.com/jobs/1", "page_text_preview": "Same content", "buttons": [{"text": "Apply Now"}], "fields": [], "has_dialog": False}
        driver.get_snapshot = AsyncMock(return_value=same_snapshot)

        with patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:
            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            ctx = StepContext(
                snapshot=same_snapshot,
                url="https://example.com/jobs/1",
                tab_state=TabState.NORMAL,
                planned_action=PageAction(
                    page_understanding="Click element", action="click_element",
                    target_text="Apply Now", reasoning="click it", confidence=0.8,
                    page_type="job_description",
                ),
                plan_source="reasoner",
                page_fingerprint=PageFingerprint(
                    field_count=0, button_texts=("Apply Now",), content_hash="abc",
                    has_dialog=False, has_file_inputs=False,
                    page_type="job_description", dom_confidence=0.8,
                    url_path_pattern="/jobs/{id}",
                ),
            )
            result = await nav._phase_act(ctx, "greenhouse", [], 0)

        assert result.ghost_click is True

    @pytest.mark.asyncio
    async def test_step_appended_with_fingerprint(self, mock_navigator):
        nav, driver, page, context = mock_navigator
        driver.get_snapshot = AsyncMock(return_value={"url": "https://ats.com/apply", "page_text_preview": "New page", "buttons": [], "fields": [{"label": "Name"}], "has_dialog": False})

        with patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:
            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            steps_list: list[dict] = []
            fp = PageFingerprint(
                field_count=0, button_texts=("Apply Now",), content_hash="abc",
                has_dialog=False, has_file_inputs=False,
                page_type="job_description", dom_confidence=0.9,
                url_path_pattern="/jobs/{id}",
            )
            ctx = StepContext(
                snapshot={"url": "https://example.com/jobs/1", "page_text_preview": "Old page", "buttons": [{"text": "Apply Now"}], "fields": [], "has_dialog": False},
                url="https://example.com/jobs/1",
                tab_state=TabState.NORMAL,
                planned_action=PageAction(
                    page_understanding="JD", action="click_element",
                    target_text="Apply Now", reasoning="click", confidence=0.8,
                    page_type="job_description",
                ),
                plan_source="reasoner",
                page_fingerprint=fp,
            )
            result = await nav._phase_act(ctx, "greenhouse", steps_list, 0)

        assert len(steps_list) == 1
        assert "fingerprint" in steps_list[0]
        assert steps_list[0]["fingerprint"]["page_type"] == "job_description"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseAct::test_click_apply_dispatches -v`
Expected: FAIL — `_phase_act` not found.

- [ ] **Step 3: Implement `_phase_act`**

Add this method to `FormNavigator`:

```python
async def _phase_act(
    self, ctx: StepContext, platform: str, steps: list[dict],
    wall_bypass_attempts: int, job: dict | None = None,
) -> StepContext:
    action = ctx.planned_action
    if not action:
        return ctx

    pre_url = ctx.snapshot.get("url", "")
    pre_hash = self._snapshot_content_hash(ctx.snapshot)
    pre_dialog = bool(ctx.snapshot.get("has_dialog"))
    post_snap: dict[str, Any] | None = None

    act = action.action

    if act in ("click_apply", "click_apply_guess", "linkedin_direct_apply"):
        post_snap = await self.click_apply_button(ctx.snapshot)
        ctx.action_executed = True
    elif act.startswith("sso_"):
        provider = act[len("sso_"):]
        sso = self.sso.detect_sso(ctx.snapshot)
        if sso and sso.get("provider") == provider:
            await self.sso.click_sso(sso)
        ctx.action_executed = True
        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
    elif act == "verify_email":
        post_snap = await self.auth.handle_email_verification(
            ctx.snapshot, platform, pre_url,
        )
        ctx.action_executed = True
    elif act == "wait_human":
        wall_info = ctx.wall_detected or {"type": "unknown"}

        if wall_bypass_attempts > 2:
            try:
                from jobpulse.page_analysis.page_reasoner import get_page_reasoner
                import sqlite3
                pr = get_page_reasoner()
                cache_key = pr._cache_key(
                    ctx.snapshot.get("url", ""),
                    ctx.snapshot.get("page_text_preview", "")[:800],
                    ctx.snapshot.get("dialog_text", "")[:500],
                    ctx.snapshot.get("fields", []),
                    ctx.snapshot.get("buttons", []),
                )
                with sqlite3.connect(pr._db_path) as conn:
                    conn.execute("DELETE FROM reasoning_cache WHERE cache_key = ?", (cache_key,))
            except Exception:
                pass
            if job:
                pb_result = await self._try_platform_bypass(ctx.snapshot, job, steps)
                if pb_result is not None:
                    ctx.post_snapshot = pb_result
                    ctx.action_executed = True
                    return ctx

        bypass_result = await self._bypass_verification_wall(ctx.snapshot, wall_info)
        ctx.action_executed = True
        if bypass_result["solved"]:
            post_snap = bypass_result["snapshot"]
        else:
            if job:
                pb_result = await self._try_platform_bypass(ctx.snapshot, job, steps)
                if pb_result is not None:
                    ctx.post_snapshot = pb_result
                    return ctx
            ctx.post_snapshot = bypass_result["snapshot"]
            return ctx
    elif act == "go_back":
        page = getattr(self.driver, "page", None)
        if page:
            await page.go_back(wait_until="domcontentloaded")
            await wait_for_page_stable(page, timeout_ms=5000)
        ctx.action_executed = True
        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
    else:
        page = getattr(self.driver, "page", None)
        if page is not None:
            from jobpulse.applicator import PROFILE
            from jobpulse.navigation.action_executor import NavigationActionExecutor
            nav_executor = NavigationActionExecutor(page)
            await nav_executor.execute(action, profile=PROFILE)
        ctx.action_executed = True
        await asyncio.sleep(1.0)
        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    if post_snap is None:
        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    post_url = post_snap.get("url", "")
    post_hash = self._snapshot_content_hash(post_snap)
    post_dialog = bool(post_snap.get("has_dialog"))

    is_click = act in ("click_apply", "click_apply_guess", "click_element",
                        "linkedin_direct_apply", "dismiss_overlay", "dismiss_dialog",
                        "accept_consent")
    if is_click and self._detect_ghost_click(pre_url, pre_hash, pre_dialog,
                                              post_url, post_hash, post_dialog):
        logger.warning("ACT: ghost click detected for action '%s'", act)
        page = getattr(self.driver, "page", None)
        if page is not None and action.target_text:
            for role in ("button", "link"):
                try:
                    loc = page.get_by_role(role, name=action.target_text, exact=False)
                    if await loc.count() and await loc.first.is_visible():
                        await loc.first.click(force=True)
                        logger.info("ACT: force-click retry on '%s'", action.target_text[:40])
                        await asyncio.sleep(1.0)
                        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))
                        retry_hash = self._snapshot_content_hash(post_snap)
                        if not self._detect_ghost_click(pre_url, pre_hash, pre_dialog,
                                                         post_snap.get("url", ""), retry_hash,
                                                         bool(post_snap.get("has_dialog"))):
                            break
                except Exception:
                    continue
            else:
                ctx.ghost_click = True
                try:
                    from shared.optimization import get_optimization_engine
                    from datetime import UTC, datetime
                    get_optimization_engine().emit(
                        signal_type="failure",
                        source_loop="navigator",
                        domain=extract_domain(pre_url),
                        agent_name="navigator",
                        payload={"param": "ghost_click", "action": act, "target": action.target_text[:40]},
                        session_id=f"gc_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}",
                    )
                except Exception:
                    pass

    intelligence = getattr(self.driver, "intelligence", None)
    if intelligence and post_url != pre_url:
        intelligence.clear()
        await intelligence.inject_on_new_page()

    step_record: dict[str, Any] = {
        "page_type": action.page_type,
        "action": act,
    }
    if ctx.page_fingerprint:
        step_record["fingerprint"] = ctx.page_fingerprint.to_dict()
    steps.append(step_record)

    await self.cookie_dismisser.dismiss(post_snap)
    page = getattr(self.driver, "page", None)
    if page is not None:
        await dismiss_cookie_banner_playwright(page)
        post_snap = self._as_dict(await self.driver.get_snapshot(force_refresh=True))

    ctx.post_snapshot = post_snap
    return ctx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestPhaseAct -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): implement ACT phase — action dispatch, ghost click detection, step recording"
```

---

### Task 9: Rewrite navigate_to_form Main Loop

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (rewrite `navigate_to_form`, remove `_reasoner_step`, `_dom_classify`, `_handle_new_tabs`)
- Test: `tests/jobpulse/test_navigation_phases.py`

- [ ] **Step 1: Write failing integration test**

Append to `tests/jobpulse/test_navigation_phases.py`:

```python
class TestNavigateToFormIntegration:
    @pytest.mark.asyncio
    async def test_simple_job_description_to_form(self, mock_navigator):
        """JD page → click apply → application form. 2 steps."""
        nav, driver, page, context = mock_navigator
        jd_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
            "fields": [],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        form_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/123/apply",
            "page_text_preview": "Application Form - First Name Last Name",
            "buttons": [{"text": "Submit"}],
            "fields": [
                {"label": "First Name", "input_type": "text"},
                {"label": "Last Name", "input_type": "text"},
                {"label": "Resume", "input_type": "file"},
            ],
            "has_dialog": False,
            "has_file_inputs": True,
            "verification_wall": None,
        }

        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            return jd_snapshot if call_count[0] <= 2 else form_snapshot
        driver.get_snapshot = _get_snap
        driver.navigate = AsyncMock()
        nav.learner.get_sequence.return_value = None
        nav.learner.get_platform_pattern.return_value = None

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf, \
             patch("jobpulse.application_orchestrator_pkg._navigator.get_page_reasoner") as MockReasoner, \
             patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock), \
             patch("jobpulse.application_orchestrator_pkg._navigator.NavigationActionExecutor") as MockExec:

            clf_instance = MockClf.return_value
            clf_returns = iter([
                (PageType.JOB_DESCRIPTION, 0.9),
                (PageType.APPLICATION_FORM, 0.92),
            ])
            clf_instance.classify.side_effect = lambda s: next(clf_returns, (PageType.APPLICATION_FORM, 0.92))

            reasoner_instance = MockReasoner.return_value
            reasoner_instance.reason_sync.return_value = PageAction(
                page_understanding="JD with apply button",
                action="click_element",
                target_text="Apply Now",
                reasoning="click to apply",
                confidence=0.9,
                page_type="job_description",
            )

            mock_exec = MockExec.return_value
            mock_exec.execute = AsyncMock()

            steps: list[dict] = []
            result = await nav.navigate_to_form(
                url="https://boards.greenhouse.io/company/jobs/123",
                platform="greenhouse",
                steps=steps,
            )

        assert result["page_type"] == PageType.APPLICATION_FORM
        assert len(steps) >= 1
        assert "fingerprint" in steps[0]

    @pytest.mark.asyncio
    async def test_learned_replay_with_verification(self, mock_navigator):
        """Learned sequence matches → verified → executed without LLM."""
        nav, driver, page, context = mock_navigator
        fp_dict = {
            "field_count": 0,
            "button_texts": ["Apply Now"],
            "content_hash": "abc123",
            "page_type": "job_description",
            "dom_confidence": 0.9,
            "url_path_pattern": "/company/jobs/{id}",
            "has_dialog": False,
            "has_file_inputs": False,
        }
        nav.learner.get_sequence.return_value = [
            {"page_type": "job_description", "action": "click_apply", "fingerprint": fp_dict}
        ]
        nav.learner.increment_replay = MagicMock()

        jd_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/456",
            "page_text_preview": "Software Engineer at Acme Corp",
            "buttons": [{"text": "Apply Now", "enabled": True, "selector": "#apply"}],
            "fields": [],
            "has_dialog": False,
            "has_file_inputs": False,
            "verification_wall": None,
        }
        form_snapshot = {
            "url": "https://boards.greenhouse.io/company/jobs/456/apply",
            "page_text_preview": "Application Form - First Name",
            "buttons": [{"text": "Submit"}],
            "fields": [{"label": "First Name", "input_type": "text"}],
            "has_dialog": False,
            "has_file_inputs": True,
            "verification_wall": None,
        }
        call_count = [0]
        async def _get_snap(force_refresh=False):
            call_count[0] += 1
            return jd_snapshot if call_count[0] <= 2 else form_snapshot
        driver.get_snapshot = _get_snap
        driver.navigate = AsyncMock()
        nav.click_apply_button = AsyncMock(return_value=form_snapshot)

        with patch("jobpulse.application_orchestrator_pkg._navigator.PageTypeClassifier") as MockClf, \
             patch("jobpulse.application_orchestrator_pkg._navigator.dismiss_cookie_banner_playwright", new_callable=AsyncMock):

            clf_instance = MockClf.return_value
            clf_returns = iter([
                (PageType.JOB_DESCRIPTION, 0.9),
                (PageType.APPLICATION_FORM, 0.92),
            ])
            clf_instance.classify.side_effect = lambda s: next(clf_returns, (PageType.APPLICATION_FORM, 0.92))

            steps: list[dict] = []
            result = await nav.navigate_to_form(
                url="https://boards.greenhouse.io/company/jobs/456",
                platform="greenhouse",
                steps=steps,
            )

        assert result["page_type"] == PageType.APPLICATION_FORM
        # Should have used learned path (no reasoner call)
        assert any(s.get("action") == "click_apply" for s in steps)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py::TestNavigateToFormIntegration::test_simple_job_description_to_form -v`
Expected: FAIL — old `navigate_to_form` doesn't use phases.

- [ ] **Step 3: Rewrite `navigate_to_form` and remove obsolete methods**

Replace the `navigate_to_form` method body (lines 107-346 of the current code) with the 5-phase loop. Keep the LinkedIn Early Apply modal check and the initial navigation preamble unchanged. Remove `_reasoner_step`, `_dom_classify`, and `_handle_new_tabs` methods entirely.

Replace the method body starting from `# Try learned sequence first` (line 146) through end of `navigate_to_form` (line 346):

```python
        # ── 5-Phase Navigation Loop ──
        domain = extract_domain(url)
        visited_states: dict[str, int] = {}
        wall_bypass_attempts = 0
        prev_url = snapshot.get("url", "")

        for step_idx in range(MAX_NAVIGATION_STEPS):
            ctx = StepContext(snapshot=snapshot, url=prev_url, tab_state=TabState.NORMAL)

            ctx = await self._phase_observe(ctx)
            if ctx.tab_state == TabState.CLOSED:
                logger.warning("Page closed during navigation — aborting")
                return {"page_type": PageType.UNKNOWN, "snapshot": ctx.snapshot}

            ctx = await self._phase_analyze(ctx)

            ctx = self._phase_match(ctx, domain, platform, len(steps))

            ctx = self._phase_plan(ctx, visited_states, wall_bypass_attempts)

            if ctx.planned_action and ctx.planned_action.action in TERMINAL_ACTIONS:
                return self._make_result(ctx)

            ctx = await self._phase_act(ctx, platform, steps, wall_bypass_attempts, job=job)

            if ctx.planned_action and ctx.planned_action.action == "wait_human":
                wall_bypass_attempts += 1
            else:
                wall_bypass_attempts = 0

            snapshot = ctx.post_snapshot or ctx.snapshot
            prev_url = snapshot.get("url", "")

        return {"page_type": PageType.UNKNOWN, "snapshot": snapshot}
```

Then delete `_reasoner_step` (old lines 701-714), `_dom_classify` (old lines 716-720), and `_handle_new_tabs` (old lines 722-737).

- [ ] **Step 4: Run the full test suite for navigation**

Run: `python -m pytest tests/jobpulse/test_navigation_phases.py -v`
Expected: all PASS.

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `python -m pytest tests/jobpulse/test_reasoner_navigation.py tests/jobpulse/test_nav_action_executor.py tests/jobpulse/test_navigation_learner.py -v`
Expected: all PASS (these test PageReasoner, NavigationActionExecutor, and NavigationLearner directly — their interfaces are unchanged).

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigation_phases.py
git commit -m "feat(nav): rewrite navigate_to_form with 5-phase pipeline, remove blind replay"
```

---

### Task 10: Cleanup and Full Test Run

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (clean up unused imports)
- Test: full suite

- [ ] **Step 1: Remove unused imports**

Check for any imports that are no longer needed after removing `_reasoner_step`, `_dom_classify`, and `_handle_new_tabs`. The `from jobpulse.page_analysis.page_reasoner import PageAction` import should now be at the top level (used by `StepContext` and `_phase_plan`). Ensure `get_page_reasoner` is imported lazily inside `_phase_plan` (it already is).

Verify the import block at the top of `_navigator.py` includes:

```python
from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field as dc_field
from enum import Enum
from typing import Any

from shared.logging_config import get_logger

from jobpulse.form_models import PageType
from jobpulse.cookie_dismisser import dismiss_cookie_banner_playwright
from jobpulse.navigation.overlay_dismisser import OverlayDismisser
from jobpulse.navigation.wait_conditions import wait_for_modal_open, wait_for_page_stable
from jobpulse.page_analysis.page_reasoner import PageAction
```

Remove the now-unused `from dataclasses import dataclass` line (replaced by the `dc_field` import pattern).

- [ ] **Step 2: Run the full jobpulse test suite**

Run: `python -m pytest tests/jobpulse/ -v --timeout=120 -x 2>&1 | tail -30`
Expected: no NEW failures. Any pre-existing failures from the 21 listed in the summary should remain unchanged.

- [ ] **Step 3: Run a type check**

Run: `python -c "from jobpulse.application_orchestrator_pkg._navigator import FormNavigator, TabState, PageFingerprint, StepContext, build_page_fingerprint, score_fingerprint_match; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 4: Commit final cleanup**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py
git commit -m "refactor(nav): clean up imports after 5-phase rewrite"
```
