# Screening Feedback Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken screening answers feedback loop so that success/correction tracking is accurate and both manual + cron application paths feed learning.

**Architecture:** Single `ScreeningOutcomeRecorder` owns all writes to V2 semantic cache counters. Form filler emits structured dicts (not colon strings). V1 cache retired from the screening path; V2 is the single source of truth.

**Tech Stack:** Python, SQLite, sentence-transformers embeddings, existing `ScreeningSemanticCache` / `ScreeningFeedbackLoop`.

**Spec:** `docs/superpowers/specs/2026-04-28-screening-feedback-overhaul-design.md`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/screening_outcome_recorder.py` | **New.** Single writer for all feedback signals to V2 semantic cache. |
| `jobpulse/screening_semantic_cache.py` | **Modify.** Fix counter semantics: remove auto-increment from `_touch_sqlite()` and `cache()`, add `increment_usage()`. |
| `jobpulse/screening_answers.py` | **Modify.** Remove V1 cache calls, simplify resolution tiers (remove Tier 2 V1 lookup, promote V2). |
| `jobpulse/applicator.py` | **Modify.** Replace colon-format screening parsing in `confirm_application()` with structured dict consumption via recorder. |
| `jobpulse/native_form_filler.py` | **Modify.** Change 5 `seen_screening.append()` sites from colon strings to structured dicts. Add `record_fill()` calls. |
| `jobpulse/job_db.py` | **Modify.** Add `ALTER TABLE` migration for missing V1 columns. |
| `scripts/migrate_v1_screening_cache.py` | **New.** One-time migration of V1 `ats_answer_cache` entries to V2. |
| `tests/jobpulse/test_screening_outcome_recorder.py` | **New.** Tests for the recorder. |

---

## Task 1: Fix `times_used` Counter Semantics in ScreeningSemanticCache

**Files:**
- Modify: `jobpulse/screening_semantic_cache.py:486-492` (`_touch_sqlite`), `:216-236` (`cache` upsert)
- Test: `tests/jobpulse/test_screening_v2.py` (add new tests)

- [ ] **Step 1: Write failing test — `_touch_sqlite` should NOT increment `times_used`**

Add to `tests/jobpulse/test_screening_v2.py`:

```python
def test_touch_sqlite_does_not_increment_times_used(tmp_path):
    """Lookups must not inflate times_used — only record_fill does that."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Do you have the right to work?", intent="work_auth", answer="Yes", confidence=0.9)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    # Simulate 3 lookups (internally calls _touch_sqlite)
    for _ in range(3):
        cache._touch_sqlite(cache._qid_for("Do you have the right to work?"))

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial, "Lookup should not increment times_used"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_touch_sqlite_does_not_increment_times_used -v`
Expected: FAIL — `_touch_sqlite` currently does `times_used = times_used + 1`

- [ ] **Step 3: Fix `_touch_sqlite` — only update `last_used_at`**

In `jobpulse/screening_semantic_cache.py`, replace the `_touch_sqlite` method (lines ~486-492):

```python
def _touch_sqlite(self, qdrant_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    with self._sqlite_conn() as conn:
        conn.execute(
            "UPDATE screening_semantic_cache SET last_used_at = ? WHERE qdrant_id = ?",
            (now, qdrant_id),
        )
```

Also add a helper method that tests and the recorder will use to compute the qdrant_id:

```python
def _qid_for(self, question: str) -> str:
    return str(_to_qdrant_id(question.strip().lower()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_touch_sqlite_does_not_increment_times_used -v`
Expected: PASS

- [ ] **Step 5: Write failing test — `cache()` upsert should NOT increment `times_used`**

Add to `tests/jobpulse/test_screening_v2.py`:

```python
def test_cache_upsert_does_not_increment_times_used(tmp_path):
    """Re-caching the same question must not inflate times_used."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Salary expectations?", intent="salary", answer="35000", confidence=0.8)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    # Re-cache same question 3 times (e.g. from confirm_application)
    for _ in range(3):
        cache.cache(question="Salary expectations?", intent="salary", answer="35000", confidence=0.85)

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial, "Re-caching should not increment times_used"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_cache_upsert_does_not_increment_times_used -v`
Expected: FAIL — `ON CONFLICT` currently sets `times_used = times_used + 1`

- [ ] **Step 7: Fix `cache()` upsert — remove `times_used` increment**

In `jobpulse/screening_semantic_cache.py`, in the `cache()` method (lines ~216-236), change the `ON CONFLICT` clause. Replace:

```python
                ON CONFLICT(qdrant_id) DO UPDATE SET
                    times_used = times_used + 1,
                    last_used_at = excluded.last_used_at,
```

With:

```python
                ON CONFLICT(qdrant_id) DO UPDATE SET
                    last_used_at = excluded.last_used_at,
```

- [ ] **Step 8: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_cache_upsert_does_not_increment_times_used -v`
Expected: PASS

- [ ] **Step 9: Write failing test — `increment_usage()` method**

Add to `tests/jobpulse/test_screening_v2.py`:

```python
def test_increment_usage_increments_times_used(tmp_path):
    """increment_usage is the only way to bump times_used."""
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "test.db"), qdrant_location="")
    cache.cache(question="Notice period?", intent="notice", answer="1 month", confidence=0.9)

    import sqlite3
    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        initial = row["times_used"]

    cache.increment_usage("Notice period?")
    cache.increment_usage("Notice period?")

    with sqlite3.connect(str(tmp_path / "test.db")) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT times_used FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["times_used"] == initial + 2
```

- [ ] **Step 10: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_increment_usage_increments_times_used -v`
Expected: FAIL — `increment_usage` does not exist yet

- [ ] **Step 11: Add `increment_usage()` method**

Add to `ScreeningSemanticCache` in `jobpulse/screening_semantic_cache.py`, after the `record_outcome` method:

```python
def increment_usage(self, question: str) -> None:
    """Increment times_used for a question. Called only by ScreeningOutcomeRecorder."""
    qid = str(_to_qdrant_id(question.strip().lower()))
    with self._sqlite_conn() as conn:
        conn.execute(
            "UPDATE screening_semantic_cache SET times_used = times_used + 1 WHERE qdrant_id = ?",
            (qid,),
        )
```

- [ ] **Step 12: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py::test_increment_usage_increments_times_used -v`
Expected: PASS

- [ ] **Step 13: Run all existing screening V2 tests**

Run: `python -m pytest tests/jobpulse/test_screening_v2.py -v`
Expected: All pass (no regressions)

- [ ] **Step 14: Commit**

```bash
git add jobpulse/screening_semantic_cache.py tests/jobpulse/test_screening_v2.py
git commit -m "fix(screening): times_used only increments on actual field fill, not lookup/cache"
```

---

## Task 2: Create ScreeningOutcomeRecorder

**Files:**
- Create: `jobpulse/screening_outcome_recorder.py`
- Create: `tests/jobpulse/test_screening_outcome_recorder.py`

- [ ] **Step 1: Write failing test — `record_fill` increments usage and caches**

Create `tests/jobpulse/test_screening_outcome_recorder.py`:

```python
"""Tests for ScreeningOutcomeRecorder — single writer for screening feedback."""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def recorder(tmp_path):
    from jobpulse.screening_semantic_cache import ScreeningSemanticCache
    cache = ScreeningSemanticCache(sqlite_path=str(tmp_path / "cache.db"), qdrant_location="")
    from jobpulse.screening_outcome_recorder import ScreeningOutcomeRecorder
    return ScreeningOutcomeRecorder(cache=cache)


@pytest.fixture
def cache_db(tmp_path):
    return str(tmp_path / "cache.db")


def test_record_fill_increments_usage(recorder, cache_db):
    recorder.record_fill(
        question="Do you have the right to work in the UK?",
        answer="Yes",
        field_options=None,
        field_type="radio",
        intent="work_auth_yes_no",
    )

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT times_used, answer FROM screening_semantic_cache").fetchall()
        assert len(rows) == 1
        assert rows[0]["times_used"] == 1
        assert rows[0]["answer"] == "Yes"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screening_outcome_recorder.py::test_record_fill_increments_usage -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Create `ScreeningOutcomeRecorder`**

Create `jobpulse/screening_outcome_recorder.py`:

```python
"""Single writer for all screening answer feedback signals.

Owns all writes to ScreeningSemanticCache counters (times_used, success_count,
correction_count). No other code should call increment_usage() or record_outcome()
directly — route through this recorder instead.
"""

from __future__ import annotations

from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)


class ScreeningOutcomeRecorder:
    """Records screening answer outcomes for learning."""

    def __init__(self, cache: Any = None) -> None:
        self._cache = cache
        if cache is None:
            self._init_cache()

    def _init_cache(self) -> None:
        try:
            from jobpulse.screening_semantic_cache import get_screening_semantic_cache
            self._cache = get_screening_semantic_cache()
        except Exception as exc:
            logger.debug("ScreeningOutcomeRecorder: cache init failed: %s", exc)

    def record_fill(
        self,
        question: str,
        answer: str,
        field_options: list[str] | None,
        field_type: str,
        intent: str = "unknown",
    ) -> None:
        """Record that a screening answer was used to fill a field.

        This is the "weak success" signal — the answer was generated and applied.
        Caches the answer (if not already cached) and increments times_used.
        """
        if not question or not answer or self._cache is None:
            return

        q = question.strip()
        a = answer.strip()

        try:
            self._cache.cache(
                question=q,
                intent=intent,
                answer=a,
                confidence=0.7,
                selected_option=a if field_options else "",
                field_type=field_type,
                field_options=field_options,
            )
            self._cache.increment_usage(q)
        except Exception as exc:
            logger.debug("record_fill failed: %s", exc)

    def record_confirmation(
        self,
        screening_results: list[dict[str, Any]],
        corrections: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Record outcomes after user confirms the application.

        Args:
            screening_results: List of dicts with keys:
                question, answer, field_options, field_type, intent, strategy
            corrections: CorrectionCapture result dict with "corrections" key
                containing list of {"field": ..., "agent": ..., "user": ...}

        Returns:
            {"confirmed": N, "corrected": M}
        """
        if self._cache is None:
            return {"confirmed": 0, "corrected": 0}

        corrected_fields: set[str] = set()
        correction_map: dict[str, str] = {}
        if corrections:
            for c in corrections.get("corrections", []):
                field = c.get("field", "").lower().strip()
                corrected_fields.add(field)
                correction_map[field] = c.get("user", "")

        confirmed = 0
        corrected = 0

        for entry in screening_results:
            q = entry.get("question", "").strip()
            a = entry.get("answer", "").strip()
            if not q or not a:
                continue

            q_lower = q.lower().strip()
            if q_lower in corrected_fields:
                try:
                    self._cache.record_outcome(q, success=False)
                    user_answer = correction_map.get(q_lower, "")
                    if user_answer:
                        self._teach_correction(
                            question=q,
                            agent_answer=a,
                            user_answer=user_answer,
                            field_options=entry.get("field_options"),
                            field_type=entry.get("field_type", ""),
                        )
                except Exception as exc:
                    logger.debug("record_confirmation correction failed for '%s': %s", q[:50], exc)
                corrected += 1
            else:
                try:
                    self._cache.record_outcome(q, success=True)
                    self._cache.cache(
                        question=q,
                        intent=entry.get("intent", "unknown"),
                        answer=a,
                        confidence=0.90,
                        selected_option=a if entry.get("field_options") else "",
                        field_type=entry.get("field_type", ""),
                        field_options=entry.get("field_options"),
                    )
                except Exception as exc:
                    logger.debug("record_confirmation success failed for '%s': %s", q[:50], exc)
                confirmed += 1

        logger.info(
            "screening_outcome: %d confirmed, %d corrected",
            confirmed, corrected,
        )
        return {"confirmed": confirmed, "corrected": corrected}

    def _teach_correction(
        self,
        question: str,
        agent_answer: str,
        user_answer: str,
        field_options: list[str] | None = None,
        field_type: str = "",
    ) -> None:
        """Forward a correction to the V2 feedback loop."""
        try:
            from jobpulse.screening_feedback_loop import ScreeningFeedbackLoop
            loop = ScreeningFeedbackLoop()
            loop.learn_from_correction(
                question=question,
                agent_answer=agent_answer,
                user_answer=user_answer,
                field_options=field_options,
                field_type=field_type,
            )
        except Exception as exc:
            logger.debug("_teach_correction failed: %s", exc)


_instance: ScreeningOutcomeRecorder | None = None


def get_screening_outcome_recorder() -> ScreeningOutcomeRecorder:
    """Return module-level singleton."""
    global _instance
    if _instance is None:
        _instance = ScreeningOutcomeRecorder()
    return _instance
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screening_outcome_recorder.py::test_record_fill_increments_usage -v`
Expected: PASS

- [ ] **Step 5: Write test — `record_confirmation` marks successes and corrections**

Add to `tests/jobpulse/test_screening_outcome_recorder.py`:

```python
def test_record_confirmation_success_and_correction(recorder, cache_db):
    # Pre-cache two entries
    recorder.record_fill(question="Right to work?", answer="Yes", field_options=None, field_type="radio", intent="work_auth")
    recorder.record_fill(question="Salary?", answer="35000", field_options=["30k", "35k", "40k"], field_type="select", intent="salary")

    screening_results = [
        {"question": "Right to work?", "answer": "Yes", "field_options": None, "field_type": "radio", "intent": "work_auth"},
        {"question": "Salary?", "answer": "35000", "field_options": ["30k", "35k", "40k"], "field_type": "select", "intent": "salary"},
    ]
    corrections = {
        "corrections": [{"field": "Salary?", "agent": "35000", "user": "40000"}],
    }

    result = recorder.record_confirmation(screening_results, corrections)
    assert result == {"confirmed": 1, "corrected": 1}

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = {r["question_text"]: dict(r) for r in conn.execute("SELECT * FROM screening_semantic_cache").fetchall()}

    assert rows["Right to work?"]["success_count"] == 1
    assert rows["Salary?"]["correction_count"] == 1
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_screening_outcome_recorder.py::test_record_confirmation_success_and_correction -v`
Expected: PASS

- [ ] **Step 7: Write test — `record_confirmation` with no corrections marks all as success**

Add to `tests/jobpulse/test_screening_outcome_recorder.py`:

```python
def test_record_confirmation_all_success(recorder, cache_db):
    recorder.record_fill(question="Notice period?", answer="1 month", field_options=None, field_type="text", intent="notice")

    screening_results = [
        {"question": "Notice period?", "answer": "1 month", "field_options": None, "field_type": "text", "intent": "notice"},
    ]

    result = recorder.record_confirmation(screening_results, corrections=None)
    assert result == {"confirmed": 1, "corrected": 0}

    with sqlite3.connect(cache_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT success_count FROM screening_semantic_cache LIMIT 1").fetchone()
        assert row["success_count"] == 1
```

- [ ] **Step 8: Run all recorder tests**

Run: `python -m pytest tests/jobpulse/test_screening_outcome_recorder.py -v`
Expected: All 3 pass

- [ ] **Step 9: Commit**

```bash
git add jobpulse/screening_outcome_recorder.py tests/jobpulse/test_screening_outcome_recorder.py
git commit -m "feat(screening): add ScreeningOutcomeRecorder — single writer for feedback signals"
```

---

## Task 3: Structured Screening Data in Native Form Filler

**Files:**
- Modify: `jobpulse/native_form_filler.py:1235,1247,1402,1412,1426,1441,1521`

- [ ] **Step 1: Change `seen_screening` type from `list[str]` to `list[dict]`**

In `jobpulse/native_form_filler.py`, at line 1235, change:

```python
        seen_screening: list[str] = []
```

To:

```python
        seen_screening: list[dict[str, Any]] = []
```

- [ ] **Step 2: Change `screening_questions` key to `screening_results` in `_result()`**

At line 1247, change:

```python
            base.setdefault("screening_questions", seen_screening)
```

To:

```python
            base.setdefault("screening_results", seen_screening)
```

- [ ] **Step 3: Update the 5 append sites to emit structured dicts**

At line ~1402 (DB cache hit), change:

```python
                        seen_screening.append(f"{f['label']}:{db_answer}")
```

To:

```python
                        seen_screening.append({
                            "question": f["label"],
                            "answer": db_answer,
                            "field_type": f.get("type", "text"),
                            "field_options": f.get("options"),
                            "intent": "unknown",
                            "strategy": "db_cache",
                        })
```

At line ~1412 (pattern/instant match), change:

```python
                            seen_screening.append(f"{f['label']}:{cached_text}")
```

To:

```python
                            seen_screening.append({
                                "question": f["label"],
                                "answer": cached_text,
                                "field_type": f.get("type", "text"),
                                "field_options": f.get("options"),
                                "intent": "unknown",
                                "strategy": "pattern_match",
                            })
```

At line ~1426 (V2 pipeline), change:

```python
                            seen_screening.append(f"{f['label']}:{v2_text}")
```

To:

```python
                            seen_screening.append({
                                "question": f["label"],
                                "answer": v2_text,
                                "field_type": f.get("type", "text"),
                                "field_options": f.get("options"),
                                "intent": "unknown",
                                "strategy": "screening_v2",
                            })
```

At line ~1441 (LLM fallback), change:

```python
                    for q, a in screening.items():
                        seen_screening.append(f"{q}:{a}")
```

To:

```python
                    for q, a in screening.items():
                        seen_screening.append({
                            "question": q,
                            "answer": str(a),
                            "field_type": "text",
                            "field_options": None,
                            "intent": "unknown",
                            "strategy": "llm_fallback",
                        })
```

At line ~1521 (recovery retry), change:

```python
                                seen_screening.append(f"{label}:{retry_value}")
```

To:

```python
                                seen_screening.append({
                                    "question": label,
                                    "answer": retry_value,
                                    "field_type": item["field"].get("type", "text"),
                                    "field_options": item["field"].get("options"),
                                    "intent": "unknown",
                                    "strategy": "llm_recovery",
                                })
```

- [ ] **Step 4: Add `record_fill()` call after each screening field is filled**

After each of the 5 append sites above, add a call to the recorder. Add this import near the top of the screening section (after the `from jobpulse.screening_answers import` line at ~1394):

```python
                from jobpulse.screening_outcome_recorder import get_screening_outcome_recorder
                _outcome_recorder = get_screening_outcome_recorder()
```

Then after each `seen_screening.append(...)` call, add:

```python
                        _outcome_recorder.record_fill(
                            question=<the question>,
                            answer=<the answer>,
                            field_options=<field options>,
                            field_type=<field type>,
                            intent="unknown",
                        )
```

Use the same values from the dict that was just appended. For the LLM fallback loop at ~1441:

```python
                    for q, a in screening.items():
                        seen_screening.append({...})
                        _outcome_recorder.record_fill(
                            question=q, answer=str(a),
                            field_options=None, field_type="text",
                        )
```

- [ ] **Step 5: Run existing tests to check for regressions**

Run: `python -m pytest tests/test_screening_answers.py tests/jobpulse/test_screening_v2.py -v`
Expected: All pass (native_form_filler changes don't affect unit tests; integration tests may need the env var `JOBPULSE_TEST_MODE=1`)

- [ ] **Step 6: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat(screening): emit structured screening_results dicts + record_fill signals"
```

---

## Task 4: Wire confirm_application() to Use Recorder

**Files:**
- Modify: `jobpulse/applicator.py:512-538`

- [ ] **Step 1: Replace colon-format parsing with recorder call**

In `jobpulse/applicator.py`, replace the entire block at lines 512-538:

```python
    # Record screening question outcomes for the semantic cache + pattern learning
    try:
        screening_qs = dry_run_result.get("screening_questions", [])
        if screening_qs:
            from jobpulse.screening_semantic_cache import get_screening_semantic_cache
            cache = get_screening_semantic_cache()
            corrected_fields = {
                c["field"].lower().strip()
                for c in (result.get("corrections", {}).get("corrections", []))
            }
            for entry in screening_qs:
                if ":" not in str(entry):
                    continue
                q, _, a = str(entry).partition(":")
                q, a = q.strip(), a.strip()
                if not q or not a:
                    continue
                was_corrected = q.lower().strip() in corrected_fields
                if not was_corrected:
                    cache.record_outcome(q, success=True)
                    cache.cache(question=q, intent="unknown", answer=a, confidence=0.85)
            logger.info(
                "confirm_application: recorded %d screening outcomes (%d corrected)",
                len(screening_qs), len(corrected_fields),
            )
    except Exception as exc:
        logger.debug("confirm_application: screening outcome recording: %s", exc)
```

With:

```python
    # Record screening outcomes via the unified recorder
    try:
        screening_results = dry_run_result.get("screening_results", [])
        if screening_results:
            from jobpulse.screening_outcome_recorder import get_screening_outcome_recorder
            recorder = get_screening_outcome_recorder()
            outcome = recorder.record_confirmation(
                screening_results=screening_results,
                corrections=result.get("corrections"),
            )
            result["screening_outcome"] = outcome
    except Exception as exc:
        logger.debug("confirm_application: screening outcome recording: %s", exc)
```

- [ ] **Step 2: Run existing screening tests**

Run: `python -m pytest tests/test_screening_answers.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add jobpulse/applicator.py
git commit -m "refactor(screening): confirm_application uses ScreeningOutcomeRecorder, no colon parsing"
```

---

## Task 5: Retire V1 Cache from Screening Path

**Files:**
- Modify: `jobpulse/screening_answers.py:549-592`

- [ ] **Step 1: Write failing test — V1 `cache_answer` no longer called**

Add to `tests/test_screening_answers.py`:

```python
def test_llm_fallback_caches_in_v2_not_v1(monkeypatch):
    """LLM-generated answers should cache in V2, not V1."""
    import os
    monkeypatch.setenv("JOBPULSE_TEST_MODE", "1")

    v1_calls = []
    original_cache = JobDB.cache_answer

    def spy_cache(self, question, answer):
        v1_calls.append((question, answer))
        return original_cache(self, question, answer)

    monkeypatch.setattr(JobDB, "cache_answer", spy_cache)

    # Force a question that hits no pattern and no cache
    with patch("jobpulse.screening_answers._generate_answer", return_value="Test answer"):
        get_answer("A completely novel unique screening question xyz123?")

    assert len(v1_calls) == 0, "V1 cache_answer should not be called from screening path"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_screening_answers.py::test_llm_fallback_caches_in_v2_not_v1 -v`
Expected: FAIL — V1 `_db.cache_answer()` is still called at lines 551 and 589

- [ ] **Step 3: Remove V1 cache calls and simplify resolution tiers**

In `jobpulse/screening_answers.py`, modify the `get_answer()` function. The key changes:

**Remove V1 caching from Tier 1 LLM path** (lines ~549-554). Change:

```python
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            _db_tier1 = db or JobDB()
            llm_answer = _generate_answer(normalised, job_context)
            _db_tier1.cache_answer(normalised, llm_answer)
            logger.info("Generated + cached Tier 1 answer for '%s'", normalised[:60])
```

To:

```python
            logger.debug("Pattern match (LLM-required) for '%s'", normalised[:60])
            llm_answer = _generate_answer(normalised, job_context)
            logger.info("Generated Tier 1 LLM answer for '%s'", normalised[:60])
```

**Remove the V1 Tier 2 cache lookup** (lines ~579-585). Delete entirely:

```python
    # --- Tier 2: cache lookup --------------------------------------------
    _db = db or JobDB()
    cached = _db.get_cached_answer(normalised)
    if cached is not None:
        logger.debug("Cache hit for '%s'", normalised[:60])
        _strategy_local.last = AnswerResult(cached, "cache_hit", 0.8)
        return with_tone_filter(cached, normalised, None)
```

**Replace V1 caching in Tier 3 LLM** (lines ~587-592). Change:

```python
    # --- Tier 3: LLM generation ------------------------------------------
    answer = _generate_answer(normalised, job_context)
    _db.cache_answer(normalised, answer)
    logger.info("Generated + cached answer for '%s'", normalised[:60])
    _strategy_local.last = AnswerResult(answer, "llm_tier3", 0.6)
    return with_tone_filter(answer, normalised, None)
```

To:

```python
    # --- Tier 4: LLM generation → cache in V2 ----------------------------
    answer = _generate_answer(normalised, job_context)
    try:
        from jobpulse.screening_semantic_cache import get_screening_semantic_cache
        get_screening_semantic_cache().cache(
            question=normalised, intent="unknown", answer=answer, confidence=0.55,
        )
    except Exception:
        pass
    logger.info("Generated + cached (V2) answer for '%s'", normalised[:60])
    _strategy_local.last = AnswerResult(answer, "llm_tier4", 0.6)
    return with_tone_filter(answer, normalised, None)
```

- [ ] **Step 4: Remove unused imports**

In `jobpulse/screening_answers.py`, the `db` parameter is now unused in `get_answer()`. Keep the parameter signature for backwards compatibility but remove internal usage. Also remove the `JobDB` import from the `cache_answer` and `get_cached_answer` functions if they are no longer used by other code (check callers first — they are public API, so keep them as-is for now).

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_screening_answers.py::test_llm_fallback_caches_in_v2_not_v1 -v`
Expected: PASS

- [ ] **Step 6: Run full screening test suite**

Run: `python -m pytest tests/test_screening_answers.py tests/jobpulse/test_screening_v2.py tests/jobpulse/test_screening_feedback_loop.py -v`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add jobpulse/screening_answers.py tests/test_screening_answers.py
git commit -m "refactor(screening): retire V1 cache from screening path, LLM caches in V2"
```

---

## Task 6: V1 Schema Migration in JobDB

**Files:**
- Modify: `jobpulse/job_db.py:176-178`

- [ ] **Step 1: Write failing test — schema migration adds missing columns**

Add a test file or add to existing `tests/test_job_db.py` (or `tests/jobpulse/test_job_db.py`):

```python
def test_ats_answer_cache_migration_adds_missing_columns(tmp_path):
    """V1 schema migration adds success_count, correction_count, last_verified_at."""
    import sqlite3
    db_path = tmp_path / "test_apps.db"

    # Create a table WITHOUT the tracking columns (simulates production state)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE ats_answer_cache (
                question_hash TEXT PRIMARY KEY,
                question_text TEXT NOT NULL,
                answer TEXT NOT NULL,
                times_used INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO ats_answer_cache VALUES ('hash1', 'Test Q?', 'Yes', 5, '2026-01-01')"
        )

    from jobpulse.job_db import JobDB
    db = JobDB(db_path=db_path)

    # Verify the columns now exist and existing data is preserved
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(ats_answer_cache)").fetchall()}
        assert "success_count" in cols
        assert "correction_count" in cols
        assert "last_verified_at" in cols

        row = conn.execute("SELECT * FROM ats_answer_cache WHERE question_hash = 'hash1'").fetchone()
        assert row["times_used"] == 5
        assert row["success_count"] == 0
        assert row["answer"] == "Yes"

    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_screening_outcome_recorder.py -k migration -v` (or wherever you placed it)
Expected: FAIL — JobDB doesn't run migration, so columns stay missing and the row read crashes

- [ ] **Step 3: Add migration to `JobDB._init_schema()`**

In `jobpulse/job_db.py`, modify `_init_schema()` at line 176:

```python
    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
            # Migration: add tracking columns if missing (production tables pre-date these)
            existing = {r[1] for r in conn.execute("PRAGMA table_info(ats_answer_cache)").fetchall()}
            for col, typ in [
                ("success_count", "INTEGER DEFAULT 0"),
                ("correction_count", "INTEGER DEFAULT 0"),
                ("last_verified_at", "TEXT"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE ats_answer_cache ADD COLUMN {col} {typ}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest <test_file>::test_ats_answer_cache_migration_adds_missing_columns -v`
Expected: PASS

- [ ] **Step 5: Run all job_db tests**

Run: `python -m pytest tests/ -k "job_db" -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add jobpulse/job_db.py tests/jobpulse/test_screening_outcome_recorder.py
git commit -m "fix(job_db): migrate ats_answer_cache schema — add missing tracking columns"
```

---

## Task 7: One-Time V1→V2 Migration Script

**Files:**
- Create: `scripts/migrate_v1_screening_cache.py`

- [ ] **Step 1: Create the migration script**

Create `scripts/migrate_v1_screening_cache.py`:

```python
"""One-time migration: V1 ats_answer_cache → V2 screening_semantic_cache.

Reads all entries from the old exact-match cache in applications.db,
embeds the question text, and inserts into the V2 semantic cache with
confidence=0.7. Skips generic/test entries.

Usage:
    python scripts/migrate_v1_screening_cache.py [--dry-run]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobpulse.config import DATA_DIR
from jobpulse.job_db import JobDB
from jobpulse.screening_semantic_cache import ScreeningSemanticCache

SKIP_QUESTIONS = {
    "question", "question 0", "question 1", "question 2",
    "email", "name", "phone", "address",
}


def migrate(dry_run: bool = False) -> None:
    db = JobDB()
    v1_entries = db.get_all_cached_answers()
    db.close()

    cache = ScreeningSemanticCache()
    migrated = 0
    skipped = 0

    for question, answer in v1_entries.items():
        q_norm = question.strip().lower()
        if q_norm in SKIP_QUESTIONS or len(q_norm) < 10:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY RUN] Would migrate: '{question[:60]}' → '{answer[:40]}'")
            migrated += 1
            continue

        cache.cache(
            question=question,
            intent="unknown",
            answer=answer,
            confidence=0.7,
        )
        migrated += 1

    print(f"\nMigration complete: {migrated} migrated, {skipped} skipped")
    if dry_run:
        print("(Dry run — no changes written)")


if __name__ == "__main__":
    is_dry_run = "--dry-run" in sys.argv
    migrate(dry_run=is_dry_run)
```

- [ ] **Step 2: Test with dry-run**

Run: `python scripts/migrate_v1_screening_cache.py --dry-run`
Expected: Lists entries that would be migrated, skips generic ones like "Question", "Email"

- [ ] **Step 3: Run the actual migration**

Run: `python scripts/migrate_v1_screening_cache.py`
Expected: Prints migration count. Verify with:

```bash
sqlite3 -header data/screening_semantic_cache.db "SELECT substr(question_text,1,50) as q, substr(answer,1,30) as a, confidence FROM screening_semantic_cache ORDER BY created_at DESC LIMIT 10"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/migrate_v1_screening_cache.py
git commit -m "feat(screening): one-time V1→V2 cache migration script"
```

---

## Task 8: Integration Verification

- [ ] **Step 1: Run the full screening test suite**

```bash
python -m pytest tests/test_screening_answers.py tests/jobpulse/test_screening_v2.py tests/jobpulse/test_screening_feedback_loop.py tests/jobpulse/test_screening_outcome_recorder.py -v
```

Expected: All pass

- [ ] **Step 2: Run broader regression check**

```bash
python -m pytest tests/ -v -k "screening or applicator or form_filler" --timeout=60
```

Expected: All pass

- [ ] **Step 3: Verify production DB migration**

```bash
sqlite3 data/applications.db "PRAGMA table_info(ats_answer_cache)" | grep -E "success_count|correction_count|last_verified_at"
```

Expected: All 3 columns present

- [ ] **Step 4: Verify V2 cache has migrated V1 entries**

```bash
sqlite3 -header data/screening_semantic_cache.db "SELECT COUNT(*) as total, SUM(CASE WHEN confidence = 0.7 THEN 1 ELSE 0 END) as migrated_from_v1 FROM screening_semantic_cache"
```

Expected: `migrated_from_v1` > 0

- [ ] **Step 5: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore(screening): integration verification clean-up"
```
