# Form Learning Read Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up `form_experience_db`, `form_interaction_log`, and `navigation_learner` so `applicator.py` consults them before every application — enabling agents to skip LLM page detection, pre-load expected fields, and replay navigation for known domains.

**Architecture:** New module `jobpulse/form_prefetch.py` aggregates all three DBs into a single `FormPrefetch` dataclass. `applicator.apply_job()` calls it before `_call_fill_and_submit()` and injects the result into `merged_answers["_form_hints"]`. Adapters read `_form_hints` from `custom_answers` and use it to skip/optimize detection. No changes to DB schemas.

**Tech Stack:** Python, SQLite, dataclasses, pytest

---

### Task 1: Create `FormPrefetch` dataclass and aggregation function

**Files:**
- Create: `jobpulse/form_prefetch.py`
- Test: `tests/jobpulse/test_form_prefetch.py`

- [ ] **Step 1: Write the failing test — unknown domain returns empty hints**

```python
# tests/jobpulse/test_form_prefetch.py
"""Tests for form_prefetch — pre-apply knowledge aggregation."""
import pytest

from jobpulse.form_prefetch import prefetch_form_hints


@pytest.fixture
def db_paths(tmp_path):
    return {
        "form_exp_db": str(tmp_path / "form_exp.db"),
        "interaction_db": str(tmp_path / "interactions.db"),
        "nav_db": str(tmp_path / "nav.db"),
    }


def test_unknown_domain_returns_empty_hints(db_paths):
    hints = prefetch_form_hints("https://unknown-domain.com/apply", **db_paths)
    assert hints is not None
    assert hints.known_domain is False
    assert hints.expected_pages == 0
    assert hints.field_types == []
    assert hints.screening_questions == []
    assert hints.page_structures == []
    assert hints.nav_steps is None
    assert hints.apply_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_unknown_domain_returns_empty_hints -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'jobpulse.form_prefetch'"

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/form_prefetch.py
"""Pre-apply form knowledge aggregation.

Queries form_experience_db, form_interaction_log, and navigation_learner
to build a FormHints object for a URL. Injected into merged_answers so
adapters can skip LLM page detection and pre-load expected fields.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class FormHints:
    known_domain: bool = False
    platform: str = ""
    expected_pages: int = 0
    field_types: list[str] = field(default_factory=list)
    screening_questions: list[str] = field(default_factory=list)
    page_structures: list[dict] = field(default_factory=list)
    nav_steps: list[dict] | None = None
    apply_count: int = 0
    avg_time_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def prefetch_form_hints(
    url: str,
    form_exp_db: str | None = None,
    interaction_db: str | None = None,
    nav_db: str | None = None,
) -> FormHints:
    """Aggregate all known form intelligence for a URL before applying.

    Queries three DBs:
    1. form_experience — domain-level summary (pages, field types, timing)
    2. form_interaction_log — per-page field structure
    3. navigation_learner — replay steps to reach the form

    Returns FormHints with whatever data is available. Never raises.
    """
    hints = FormHints()

    # 1. Form experience (domain-level)
    try:
        from jobpulse.form_experience_db import FormExperienceDB
        exp_db = FormExperienceDB(db_path=form_exp_db)
        exp = exp_db.lookup(url)
        if exp and exp.get("success"):
            hints.known_domain = True
            hints.platform = exp.get("platform", "")
            hints.expected_pages = exp.get("pages_filled", 0)
            hints.field_types = json.loads(exp["field_types"]) if isinstance(exp["field_types"], str) else exp["field_types"]
            hints.screening_questions = json.loads(exp["screening_questions"]) if isinstance(exp["screening_questions"], str) else exp["screening_questions"]
            hints.apply_count = exp.get("apply_count", 0)
            hints.avg_time_seconds = exp.get("time_seconds", 0.0)
    except Exception as exc:
        logger.debug("form_prefetch: experience lookup failed: %s", exc)

    # 2. Page structures (per-page detail)
    try:
        from jobpulse.form_interaction_log import FormInteractionLog
        int_log = FormInteractionLog(db_path=interaction_db)
        pages = int_log.get_page_structure(url)
        if pages:
            hints.page_structures = pages
            if not hints.known_domain and pages:
                hints.known_domain = True
                hints.expected_pages = len(pages)
    except Exception as exc:
        logger.debug("form_prefetch: interaction log lookup failed: %s", exc)

    # 3. Navigation sequence
    try:
        from jobpulse.navigation_learner import NavigationLearner
        nav = NavigationLearner(db_path=nav_db)
        steps = nav.get_sequence(url)
        if steps:
            hints.nav_steps = steps
    except Exception as exc:
        logger.debug("form_prefetch: navigation lookup failed: %s", exc)

    if hints.known_domain:
        logger.info(
            "form_prefetch: %s — %d pages, %d field types, %d screening Qs, nav=%s, applied %dx",
            url[:60], hints.expected_pages, len(hints.field_types),
            len(hints.screening_questions), "yes" if hints.nav_steps else "no",
            hints.apply_count,
        )

    return hints
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_unknown_domain_returns_empty_hints -v`
Expected: PASS

- [ ] **Step 5: Write test — known domain with all three DBs populated**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_known_domain_aggregates_all_sources(db_paths):
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.form_interaction_log import FormInteractionLog
    from jobpulse.navigation_learner import NavigationLearner

    exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
    exp_db.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="extension",
        pages_filled=3,
        field_types=["text", "select", "file"],
        screening_questions=["Require sponsorship?", "Salary?"],
        time_seconds=45.0,
        success=True,
    )

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "boards.greenhouse.io", "greenhouse", 1, "Contact",
        ["Name", "Email", "Phone"], ["text", "text", "text"],
        nav_buttons=["Next"],
    )
    int_log.log_page_structure(
        "boards.greenhouse.io", "greenhouse", 2, "Resume",
        ["Resume", "Cover Letter"], ["file", "file"],
        has_file_upload=True, nav_buttons=["Back", "Submit"],
    )

    nav = NavigationLearner(db_path=db_paths["nav_db"])
    nav.save_sequence("boards.greenhouse.io", [
        {"type": "click", "selector": "#apply-btn"},
        {"type": "wait", "selector": "#form"},
    ], success=True)

    hints = prefetch_form_hints(
        "https://boards.greenhouse.io/company/jobs/123", **db_paths
    )

    assert hints.known_domain is True
    assert hints.platform == "greenhouse"
    assert hints.expected_pages == 3
    assert hints.field_types == ["text", "select", "file"]
    assert hints.screening_questions == ["Require sponsorship?", "Salary?"]
    assert len(hints.page_structures) == 2
    assert hints.page_structures[0]["page_title"] == "Contact"
    assert hints.page_structures[1]["has_file_upload"] == 1
    assert hints.nav_steps is not None
    assert len(hints.nav_steps) == 2
    assert hints.apply_count == 1
    assert hints.avg_time_seconds == pytest.approx(45.0)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_known_domain_aggregates_all_sources -v`
Expected: PASS

- [ ] **Step 7: Write test — partial data (only form_experience, no interaction log)**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_partial_data_still_returns_hints(db_paths):
    from jobpulse.form_experience_db import FormExperienceDB

    exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
    exp_db.record(
        domain="jobs.lever.co",
        platform="lever",
        adapter="extension",
        pages_filled=2,
        field_types=["text", "file"],
        screening_questions=[],
        time_seconds=30.0,
        success=True,
    )

    hints = prefetch_form_hints("https://jobs.lever.co/company/abc", **db_paths)

    assert hints.known_domain is True
    assert hints.platform == "lever"
    assert hints.expected_pages == 2
    assert hints.page_structures == []
    assert hints.nav_steps is None
```

- [ ] **Step 8: Run all tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: 3 PASS

- [ ] **Step 9: Write test — to_dict serialization**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_to_dict_serialization(db_paths):
    hints = prefetch_form_hints("https://unknown.com/apply", **db_paths)
    d = hints.to_dict()
    assert isinstance(d, dict)
    assert d["known_domain"] is False
    assert d["expected_pages"] == 0
    assert d["nav_steps"] is None
```

- [ ] **Step 10: Run all tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: 4 PASS

- [ ] **Step 11: Commit**

```bash
git add jobpulse/form_prefetch.py tests/jobpulse/test_form_prefetch.py
git commit -m "feat: add form_prefetch module — aggregates form learning before apply"
```

---

### Task 2: Wire `prefetch_form_hints` into `applicator.apply_job()`

**Files:**
- Modify: `jobpulse/applicator.py:238-276` (between gotchas loading and adapter call)
- Test: `tests/jobpulse/test_applicator_prefetch.py`

- [ ] **Step 1: Write the failing test — hints injected into merged_answers**

```python
# tests/jobpulse/test_applicator_prefetch.py
"""Tests that apply_job injects form hints before calling the adapter."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_adapter():
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.fill_and_submit.return_value = {
        "success": True,
        "pages_filled": 2,
        "field_types": ["text"],
        "screening_questions": [],
        "time_seconds": 10.0,
    }
    return adapter


def test_prefetch_hints_injected_on_dry_run(mock_adapter, tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB

    exp_db_path = str(tmp_path / "form_exp.db")
    exp_db = FormExperienceDB(db_path=exp_db_path)
    exp_db.record(
        domain="boards.greenhouse.io",
        platform="greenhouse",
        adapter="extension",
        pages_filled=3,
        field_types=["text", "select"],
        screening_questions=["Sponsorship?"],
        time_seconds=40.0,
        success=True,
    )

    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.applicator.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(
            known_domain=True, platform="greenhouse", expected_pages=3,
            field_types=["text", "select"], screening_questions=["Sponsorship?"],
            apply_count=1, avg_time_seconds=40.0,
        )

        from jobpulse.applicator import apply_job
        result = apply_job(
            url="https://boards.greenhouse.io/company/jobs/123",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        mock_prefetch.assert_called_once()
        call_kwargs = mock_adapter.fill_and_submit.call_args
        answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
        assert "_form_hints" in answers
        assert answers["_form_hints"]["known_domain"] is True
        assert answers["_form_hints"]["expected_pages"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py::test_prefetch_hints_injected_on_dry_run -v`
Expected: FAIL — `prefetch_form_hints` not imported in applicator.py

- [ ] **Step 3: Add prefetch call to applicator.py**

In `jobpulse/applicator.py`, add the import at top and inject hints between the gotchas block (line ~251) and the Telegram stream block (line ~254):

Add after the gotchas loading block (after line 251) and before the Telegram stream block:

```python
    # Load form hints from prior applications on this domain
    try:
        from jobpulse.form_prefetch import prefetch_form_hints
        _form_hints = prefetch_form_hints(url)
        if _form_hints.known_domain:
            merged_answers["_form_hints"] = _form_hints.to_dict()
    except Exception as _prefetch_exc:
        logger.debug("form_prefetch failed: %s", _prefetch_exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py::test_prefetch_hints_injected_on_dry_run -v`
Expected: PASS

- [ ] **Step 5: Write test — unknown domain does NOT inject hints**

Add to `tests/jobpulse/test_applicator_prefetch.py`:

```python
def test_unknown_domain_no_hints_injected(mock_adapter):
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.applicator.prefetch_form_hints") as mock_prefetch:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints()  # unknown domain

        from jobpulse.applicator import apply_job
        apply_job(
            url="https://never-seen-before.com/apply",
            ats_platform=None,
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        call_kwargs = mock_adapter.fill_and_submit.call_args
        answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
        assert "_form_hints" not in answers
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py -v`
Expected: 2 PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/applicator.py tests/jobpulse/test_applicator_prefetch.py
git commit -m "feat: wire form_prefetch into apply_job — inject hints before adapter call"
```

---

### Task 3: Add `_form_hints` filtering to JSON serialization guard

**Files:**
- Modify: `jobpulse/applicator.py` (wherever `_`-prefixed keys are filtered)
- Verify: no existing filter already covers `_form_hints`

The `_form_hints` key uses the `_` prefix convention already used by `_stream`, `_gotchas`, `_job_context`. These are filtered before `json.dumps` by existing guards in the codebase. This task verifies coverage.

- [ ] **Step 1: Grep for existing `_`-prefix filters**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py tests/jobpulse/test_applicator_prefetch.py -v`
Expected: All PASS (existing `_`-prefix filter already covers `_form_hints`)

- [ ] **Step 2: If `_form_hints` is NOT already filtered, add it**

Search the codebase for where `_`-prefixed keys are stripped from `custom_answers` before JSON serialization. The convention is to filter all keys starting with `_`. If the filter is a startswith check, `_form_hints` is covered automatically. If it's an explicit allowlist, add `_form_hints`.

- [ ] **Step 3: Commit (if changes needed)**

```bash
git add -A && git commit -m "fix: ensure _form_hints filtered from JSON serialization"
```

---

### Task 4: Pre-warm screening answer cache from form hints

**Files:**
- Modify: `jobpulse/applicator.py:185-203` (screening answer resolution section)
- Test: `tests/jobpulse/test_applicator_prefetch.py` (add test)

When `_form_hints` contains `screening_questions`, pre-resolve those answers into the cache BEFORE the adapter encounters them. This makes the adapter's question answering instant (cache hit) instead of requiring LLM calls.

- [ ] **Step 1: Write the failing test**

Add to `tests/jobpulse/test_applicator_prefetch.py`:

```python
def test_screening_questions_pre_resolved_from_hints(mock_adapter):
    with patch("jobpulse.applicator.select_adapter", return_value=mock_adapter), \
         patch("jobpulse.applicator.prefetch_form_hints") as mock_prefetch, \
         patch("jobpulse.applicator.get_answer", return_value="Yes") as mock_answer:
        from jobpulse.form_prefetch import FormHints
        mock_prefetch.return_value = FormHints(
            known_domain=True,
            screening_questions=["Do you require sponsorship?", "Willing to relocate?"],
        )

        from jobpulse.applicator import apply_job
        apply_job(
            url="https://boards.greenhouse.io/company/jobs/456",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

        # Verify screening questions from hints were pre-resolved
        screening_calls = [
            c for c in mock_answer.call_args_list
            if c[0][0] in ("Do you require sponsorship?", "Willing to relocate?")
        ]
        assert len(screening_calls) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py::test_screening_questions_pre_resolved_from_hints -v`
Expected: FAIL — screening questions not pre-resolved

- [ ] **Step 3: Add pre-resolution in applicator.py**

In `applicator.py`, right after injecting `_form_hints` into `merged_answers`, add screening question pre-warm:

```python
        if _form_hints.known_domain and _form_hints.screening_questions:
            for sq in _form_hints.screening_questions:
                if sq not in merged_answers:
                    answer = get_answer(sq, _screening_job_context, platform=platform_key)
                    if answer:
                        merged_answers[sq] = answer
            logger.info(
                "form_prefetch: pre-resolved %d screening questions",
                len(_form_hints.screening_questions),
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py::test_screening_questions_pre_resolved_from_hints -v`
Expected: PASS

- [ ] **Step 5: Run all applicator prefetch tests**

Run: `python -m pytest tests/jobpulse/test_applicator_prefetch.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/applicator.py tests/jobpulse/test_applicator_prefetch.py
git commit -m "feat: pre-resolve screening questions from form hints before apply"
```

---

### Task 5: Expose `FormHints.has_file_upload` for adapter optimization

**Files:**
- Modify: `jobpulse/form_prefetch.py`
- Modify: `tests/jobpulse/test_form_prefetch.py`

Adapters need to know upfront whether to prepare a file upload. Derive this from `page_structures`.

- [ ] **Step 1: Write the failing test**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_has_file_upload_derived_from_page_structures(db_paths):
    from jobpulse.form_interaction_log import FormInteractionLog

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "example.com", "generic", 1, "Contact",
        ["Name"], ["text"], has_file_upload=False,
    )
    int_log.log_page_structure(
        "example.com", "generic", 2, "Resume",
        ["Resume"], ["file"], has_file_upload=True,
    )

    hints = prefetch_form_hints("https://example.com/apply", **db_paths)
    assert hints.has_file_upload is True


def test_no_file_upload_when_not_present(db_paths):
    from jobpulse.form_interaction_log import FormInteractionLog

    int_log = FormInteractionLog(db_path=db_paths["interaction_db"])
    int_log.log_page_structure(
        "nofile.com", "generic", 1, "Info",
        ["Name", "Email"], ["text", "text"], has_file_upload=False,
    )

    hints = prefetch_form_hints("https://nofile.com/apply", **db_paths)
    assert hints.has_file_upload is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_has_file_upload_derived_from_page_structures -v`
Expected: FAIL — `FormHints` has no `has_file_upload` attribute

- [ ] **Step 3: Add `has_file_upload` to FormHints**

In `jobpulse/form_prefetch.py`, add the field to `FormHints`:

```python
    has_file_upload: bool = False
```

And in `prefetch_form_hints`, after loading page structures:

```python
        if pages:
            hints.page_structures = pages
            hints.has_file_upload = any(p.get("has_file_upload") for p in pages)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: All PASS (including previous tests + 2 new)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_prefetch.py tests/jobpulse/test_form_prefetch.py
git commit -m "feat: derive has_file_upload from page structures in FormHints"
```

---

### Task 6: Log prefetch hit rate for observability

**Files:**
- Modify: `jobpulse/form_prefetch.py`
- Test: `tests/jobpulse/test_form_prefetch.py`

Add a lightweight counter so we can track how often prefetch finds useful data.

- [ ] **Step 1: Write the failing test**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_prefetch_stats(db_paths):
    from jobpulse.form_prefetch import get_prefetch_stats, prefetch_form_hints
    from jobpulse.form_experience_db import FormExperienceDB

    # Start clean
    stats = get_prefetch_stats()
    assert stats["total_lookups"] == 0

    # Unknown domain
    prefetch_form_hints("https://unknown.com", **db_paths)

    # Known domain
    exp_db = FormExperienceDB(db_path=db_paths["form_exp_db"])
    exp_db.record("known.com", "greenhouse", "extension", 2, ["text"], [], 20.0, True)
    prefetch_form_hints("https://known.com/apply", **db_paths)

    stats = get_prefetch_stats()
    assert stats["total_lookups"] == 2
    assert stats["cache_hits"] == 1
    assert stats["cache_misses"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py::test_prefetch_stats -v`
Expected: FAIL — `get_prefetch_stats` not defined

- [ ] **Step 3: Add counters to form_prefetch.py**

Add at module level and update `prefetch_form_hints`:

```python
_stats = {"total_lookups": 0, "cache_hits": 0, "cache_misses": 0}


def get_prefetch_stats() -> dict:
    return dict(_stats)
```

In `prefetch_form_hints`, increment after lookup:

```python
    _stats["total_lookups"] += 1
    if hints.known_domain:
        _stats["cache_hits"] += 1
    else:
        _stats["cache_misses"] += 1
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_prefetch.py tests/jobpulse/test_form_prefetch.py
git commit -m "feat: add prefetch hit/miss counters for observability"
```

---

### Task 7: Integration test — full apply_job cycle with form hints

**Files:**
- Create: `tests/jobpulse/test_apply_with_hints_integration.py`

End-to-end test: populate all 3 DBs, call `apply_job(dry_run=True)`, verify hints reach the adapter.

- [ ] **Step 1: Write the integration test**

```python
# tests/jobpulse/test_apply_with_hints_integration.py
"""Integration test: apply_job with pre-populated form learning DBs."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def populated_dbs(tmp_path):
    from jobpulse.form_experience_db import FormExperienceDB
    from jobpulse.form_interaction_log import FormInteractionLog
    from jobpulse.navigation_learner import NavigationLearner

    exp_path = str(tmp_path / "form_exp.db")
    int_path = str(tmp_path / "interactions.db")
    nav_path = str(tmp_path / "nav.db")

    exp = FormExperienceDB(db_path=exp_path)
    exp.record("boards.greenhouse.io", "greenhouse", "extension",
               3, ["text", "select", "file"],
               ["Require sponsorship?", "Expected salary?"],
               45.0, True)

    log = FormInteractionLog(db_path=int_path)
    log.log_page_structure("boards.greenhouse.io", "greenhouse", 1, "Contact",
                           ["Name", "Email"], ["text", "text"],
                           nav_buttons=["Next"])
    log.log_page_structure("boards.greenhouse.io", "greenhouse", 2, "Resume",
                           ["Resume"], ["file"], has_file_upload=True,
                           nav_buttons=["Back", "Submit"])

    nav = NavigationLearner(db_path=nav_path)
    nav.save_sequence("boards.greenhouse.io",
                      [{"type": "click", "selector": "#apply"}], True)

    return {"form_exp_db": exp_path, "interaction_db": int_path, "nav_db": nav_path}


def test_full_cycle_hints_reach_adapter(populated_dbs):
    adapter = MagicMock()
    adapter.name = "mock"
    adapter.fill_and_submit.return_value = {
        "success": True, "pages_filled": 3,
        "field_types": ["text", "select", "file"],
        "screening_questions": ["Require sponsorship?", "Expected salary?"],
        "time_seconds": 42.0,
    }

    with patch("jobpulse.applicator.select_adapter", return_value=adapter), \
         patch("jobpulse.applicator.prefetch_form_hints") as mock_pf:
        from jobpulse.form_prefetch import FormHints, prefetch_form_hints
        real_hints = prefetch_form_hints(
            "https://boards.greenhouse.io/co/jobs/1", **populated_dbs,
        )
        mock_pf.return_value = real_hints

        from jobpulse.applicator import apply_job
        result = apply_job(
            url="https://boards.greenhouse.io/co/jobs/1",
            ats_platform="greenhouse",
            cv_path=Path("/tmp/cv.pdf"),
            dry_run=True,
        )

    assert result["success"] is True
    call_kwargs = adapter.fill_and_submit.call_args
    answers = call_kwargs.kwargs.get("custom_answers") or call_kwargs[1].get("custom_answers", {})
    hints = answers.get("_form_hints", {})
    assert hints["known_domain"] is True
    assert hints["expected_pages"] == 3
    assert hints["has_file_upload"] is True
    assert len(hints["page_structures"]) == 2
    assert hints["nav_steps"] is not None
```

- [ ] **Step 2: Run the integration test**

Run: `python -m pytest tests/jobpulse/test_apply_with_hints_integration.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_apply_with_hints_integration.py
git commit -m "test: add integration test for apply_job with form learning hints"
```

---

### Task 8: Run full test suite and verify no regressions

**Files:** None (verification only)

- [ ] **Step 1: Run all form-related tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py tests/jobpulse/test_applicator_prefetch.py tests/jobpulse/test_apply_with_hints_integration.py tests/jobpulse/test_form_experience_db.py tests/jobpulse/test_form_interaction_log.py -v`
Expected: All PASS

- [ ] **Step 2: Run full jobpulse test suite**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60`
Expected: All PASS, no regressions

- [ ] **Step 3: Final commit if any cleanup needed**

```bash
git add -A && git commit -m "chore: post-integration cleanup"
```
