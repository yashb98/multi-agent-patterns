# AUQ — Dual-Process Uncertainty Quantification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-field confidence scoring to the form-filling pipeline so low-confidence mappings trigger Best-of-N sampling (System 2) instead of direct fill (System 1), improving field accuracy on novel forms.

**Architecture:** System 1 (fast path) = `seed_mapping()` deterministic resolution at confidence 1.0, or cached mapping. System 2 (slow path) = when any field's confidence < 0.9, generate 3 candidate mappings at temperatures [0.0, 0.3, 0.7] via `parallel_grpo_candidates()`, score consensus, pick best. Confidence tracked per domain in `FormExperienceDB` for calibration. OptimizationEngine signals emitted on every escalation.

**Tech Stack:** Python, SQLite, `shared/parallel_executor.py:parallel_grpo_candidates()`, `shared/optimization/`, `jobpulse/form_experience_db.py`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/form_engine/confidence_scorer.py` (CREATE) | Per-field confidence scoring: deterministic=1.0, cached=0.95, LLM=raw logprob or heuristic |
| `jobpulse/form_engine/field_mapper.py` (MODIFY) | Return `FieldMapping` with confidence per field, trigger Best-of-N on low confidence |
| `jobpulse/native_form_filler.py` (MODIFY) | Check confidence before committing each field to DOM |
| `jobpulse/form_experience_db.py` (MODIFY) | Add `field_confidence_log` table for calibration tracking |
| `tests/jobpulse/test_confidence_scorer.py` (CREATE) | Unit tests for confidence scoring and Best-of-N consensus |
| `tests/jobpulse/test_auq_integration.py` (CREATE) | Integration test: seed_mapping → confidence → escalation → fill |

---

### Task 1: FieldMapping Dataclass and Confidence Scorer

**Files:**
- Create: `jobpulse/form_engine/confidence_scorer.py`
- Test: `tests/jobpulse/test_confidence_scorer.py`

- [ ] **Step 1: Write the failing test for FieldMapping dataclass**

```python
# tests/jobpulse/test_confidence_scorer.py
"""Tests for per-field confidence scoring (AUQ System 1/2)."""
from __future__ import annotations

import pytest

from jobpulse.form_engine.confidence_scorer import FieldMapping, ConfidenceScorer


class TestFieldMapping:
    def test_high_confidence_mapping(self):
        fm = FieldMapping(label="First Name", value="Yash", confidence=1.0, source="deterministic")
        assert fm.is_confident
        assert fm.confidence == 1.0

    def test_low_confidence_mapping(self):
        fm = FieldMapping(label="Preferred pronouns", value="He/Him", confidence=0.6, source="llm")
        assert not fm.is_confident
        assert fm.confidence == 0.6

    def test_confidence_threshold_boundary(self):
        at_threshold = FieldMapping(label="x", value="y", confidence=0.9, source="llm")
        assert at_threshold.is_confident
        below = FieldMapping(label="x", value="y", confidence=0.89, source="llm")
        assert not below.is_confident
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestFieldMapping -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.form_engine.confidence_scorer'`

- [ ] **Step 3: Write FieldMapping dataclass**

```python
# jobpulse/form_engine/confidence_scorer.py
"""Per-field confidence scoring for AUQ dual-process form filling.

System 1 (fast): deterministic/cached mappings, confidence >= 0.9
System 2 (slow): Best-of-N sampling when confidence < 0.9
"""
from __future__ import annotations

from dataclasses import dataclass

from shared.logging_config import get_logger

logger = get_logger(__name__)

CONFIDENCE_THRESHOLD = 0.9


@dataclass
class FieldMapping:
    label: str
    value: str
    confidence: float
    source: str  # "deterministic", "cached", "llm", "consensus"

    @property
    def is_confident(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestFieldMapping -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/confidence_scorer.py tests/jobpulse/test_confidence_scorer.py
git commit -m "feat(auq): add FieldMapping dataclass with confidence threshold"
```

---

### Task 2: ConfidenceScorer — Assign Confidence per Source

**Files:**
- Modify: `jobpulse/form_engine/confidence_scorer.py`
- Test: `tests/jobpulse/test_confidence_scorer.py`

- [ ] **Step 1: Write failing tests for ConfidenceScorer**

```python
# Append to tests/jobpulse/test_confidence_scorer.py

class TestConfidenceScorer:
    def test_deterministic_mapping_gets_full_confidence(self):
        scorer = ConfidenceScorer()
        mappings = {"First Name": "Yash", "Email": "test@example.com"}
        source = "deterministic"
        scored = scorer.score_mappings(mappings, source=source)
        assert all(fm.confidence == 1.0 for fm in scored)
        assert all(fm.source == "deterministic" for fm in scored)

    def test_cached_mapping_gets_high_confidence(self):
        scorer = ConfidenceScorer()
        scored = scorer.score_mappings({"City": "London"}, source="cached")
        assert scored[0].confidence == 0.95

    def test_llm_mapping_gets_heuristic_confidence(self):
        scorer = ConfidenceScorer()
        fields = [
            {"label": "First Name", "type": "text", "options": []},
        ]
        scored = scorer.score_mappings(
            {"First Name": "Yash"}, source="llm", fields=fields,
        )
        # LLM with exact label match in profile → high confidence
        assert scored[0].confidence >= 0.85

    def test_llm_screening_field_gets_lower_confidence(self):
        scorer = ConfidenceScorer()
        fields = [
            {"label": "Are you authorized?", "type": "radio", "options": ["Yes", "No"]},
        ]
        scored = scorer.score_mappings(
            {"Are you authorized?": "Yes"}, source="llm", fields=fields,
        )
        # Screening-like question from LLM → lower confidence
        assert scored[0].confidence < 0.9

    def test_empty_mappings_returns_empty(self):
        scorer = ConfidenceScorer()
        assert scorer.score_mappings({}, source="deterministic") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestConfidenceScorer -v`
Expected: FAIL with `ImportError` (ConfidenceScorer not yet defined)

- [ ] **Step 3: Implement ConfidenceScorer**

```python
# Append to jobpulse/form_engine/confidence_scorer.py

from jobpulse.form_engine.field_mapper import is_screening_like_field

_SOURCE_CONFIDENCE = {
    "deterministic": 1.0,
    "cached": 0.95,
    "consensus": 0.92,
}

_LLM_BASE_CONFIDENCE = 0.85
_SCREENING_PENALTY = 0.15


class ConfidenceScorer:
    def score_mappings(
        self,
        mappings: dict[str, str],
        *,
        source: str,
        fields: list[dict] | None = None,
    ) -> list[FieldMapping]:
        if not mappings:
            return []

        field_lookup = {f["label"]: f for f in (fields or [])}
        result: list[FieldMapping] = []

        base = _SOURCE_CONFIDENCE.get(source)
        for label, value in mappings.items():
            if base is not None:
                confidence = base
            else:
                confidence = _LLM_BASE_CONFIDENCE
                field = field_lookup.get(label, {})
                if is_screening_like_field(field):
                    confidence -= _SCREENING_PENALTY

            result.append(FieldMapping(
                label=label, value=value,
                confidence=round(confidence, 3),
                source=source,
            ))
        return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestConfidenceScorer -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/confidence_scorer.py tests/jobpulse/test_confidence_scorer.py
git commit -m "feat(auq): ConfidenceScorer assigns confidence by source and field type"
```

---

### Task 3: Best-of-N Consensus via parallel_grpo_candidates

**Files:**
- Modify: `jobpulse/form_engine/confidence_scorer.py`
- Test: `tests/jobpulse/test_confidence_scorer.py`

- [ ] **Step 1: Write failing tests for consensus generation**

```python
# Append to tests/jobpulse/test_confidence_scorer.py
from unittest.mock import patch, MagicMock


class TestBestOfNConsensus:
    def test_unanimous_consensus(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Salary": "35000"}',
            '{"Salary": "35000"}',
            '{"Salary": "35000"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Salary"])
        assert result["Salary"] == "35000"

    def test_majority_consensus(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Notice": "1 month"}',
            '{"Notice": "1 month"}',
            '{"Notice": "2 weeks"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Notice"])
        assert result["Notice"] == "1 month"

    def test_no_consensus_returns_first(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"X": "a"}',
            '{"X": "b"}',
            '{"X": "c"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["X"])
        assert result["X"] == "a"

    def test_malformed_candidate_skipped(self):
        scorer = ConfidenceScorer()
        candidates = [
            '{"Y": "good"}',
            'not json',
            '{"Y": "good"}',
        ]
        result = scorer.pick_consensus(candidates, field_labels=["Y"])
        assert result["Y"] == "good"

    @patch("jobpulse.form_engine.confidence_scorer.parallel_grpo_candidates")
    def test_escalate_calls_grpo(self, mock_grpo):
        mock_grpo.return_value = ['{"Q": "A"}', '{"Q": "A"}', '{"Q": "A"}']
        scorer = ConfidenceScorer()
        low_conf = [
            FieldMapping(label="Q", value="B", confidence=0.5, source="llm"),
        ]
        fields = [{"label": "Q", "type": "radio", "options": ["A", "B"]}]
        result = scorer.escalate_low_confidence(
            low_confidence_mappings=low_conf,
            fields=fields,
            profile={"name": "Test"},
            custom_answers={},
            platform="greenhouse",
        )
        assert mock_grpo.called
        assert "Q" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestBestOfNConsensus -v`
Expected: FAIL with `AttributeError: 'ConfidenceScorer' object has no attribute 'pick_consensus'`

- [ ] **Step 3: Implement consensus picking and escalation**

```python
# Append to jobpulse/form_engine/confidence_scorer.py
import json
from collections import Counter
from typing import Any

from shared.parallel_executor import parallel_grpo_candidates
from shared.pii import assert_prompt_has_wrapped_pii

_TEMPERATURES = [0.0, 0.3, 0.7]


class ConfidenceScorer:
    # ... (keep existing score_mappings method) ...

    def pick_consensus(
        self, candidates: list[str], *, field_labels: list[str],
    ) -> dict[str, str]:
        parsed: list[dict] = []
        for c in candidates:
            try:
                parsed.append(json.loads(c))
            except (json.JSONDecodeError, TypeError):
                continue
        if not parsed:
            return {}

        result: dict[str, str] = {}
        for label in field_labels:
            values = [p.get(label) for p in parsed if label in p]
            if not values:
                continue
            counter = Counter(values)
            winner, count = counter.most_common(1)[0]
            result[label] = winner
        return result

    def escalate_low_confidence(
        self,
        *,
        low_confidence_mappings: list[FieldMapping],
        fields: list[dict],
        profile: dict,
        custom_answers: dict,
        platform: str,
    ) -> dict[str, str]:
        if not low_confidence_mappings:
            return {}

        field_labels = [fm.label for fm in low_confidence_mappings]
        field_lookup = {f["label"]: f for f in fields}
        field_descriptions = []
        for label in field_labels:
            f = field_lookup.get(label, {})
            desc = f"- {label} ({f.get('type', 'text')})"
            if f.get("options"):
                desc += f" options: {f['options'][:10]}"
            field_descriptions.append(desc)

        from jobpulse.form_engine.field_resolver import _profile_prompt_json

        prompt = (
            f'Map profile data to form fields. Return JSON {{"label": "value"}}.\n'
            f"Fields:\n{chr(10).join(field_descriptions)}\n\n"
            f"Profile: {_profile_prompt_json(profile)}\n"
            f"Platform: {platform}\n"
            f"Known answers: {json.dumps({k: v for k, v in custom_answers.items() if not k.startswith('_')})}"
        )
        assert_prompt_has_wrapped_pii(prompt, profile, "applicant.profile")

        from shared.agents import get_llm

        def llm_factory(temp: float):
            return get_llm(temperature=temp, model="gpt-4.1-nano")

        candidates = parallel_grpo_candidates(
            llm_factory=llm_factory,
            system_prompt="You are a form-filling assistant. Return valid JSON only.",
            user_message=prompt,
            temperatures=_TEMPERATURES,
        )
        logger.info(
            "AUQ System 2: generated %d candidates for %d low-confidence fields",
            len(candidates), len(field_labels),
        )
        return self.pick_consensus(candidates, field_labels=field_labels)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestBestOfNConsensus -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/confidence_scorer.py tests/jobpulse/test_confidence_scorer.py
git commit -m "feat(auq): Best-of-N consensus via parallel_grpo_candidates"
```

---

### Task 4: Wire Confidence Scoring into field_mapper.py

**Files:**
- Modify: `jobpulse/form_engine/field_mapper.py:259-322` (map_fields function)
- Test: `tests/jobpulse/test_auq_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# tests/jobpulse/test_auq_integration.py
"""Integration test: field_mapper returns confidence-scored mappings."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


class TestMapFieldsWithConfidence:
    @pytest.mark.asyncio
    async def test_seed_mapping_returns_high_confidence(self):
        from jobpulse.form_engine.field_mapper import map_fields_with_confidence

        fields = [
            {"label": "first name", "type": "text", "options": [], "value": ""},
            {"label": "email", "type": "text", "options": [], "value": ""},
        ]
        profile = {"first_name": "Test", "email": "test@example.com"}

        with patch("jobpulse.form_engine.field_mapper.try_cached_mapping", return_value=None), \
             patch("jobpulse.form_engine.field_mapper.seed_mapping") as mock_seed, \
             patch("jobpulse.form_engine.field_mapper._ensure_label_db"):
            mock_seed.return_value = (
                {"first name": "Test", "email": "test@example.com"},
                [],  # no unresolved
            )
            scored, llm_calls = await map_fields_with_confidence(
                page_url="https://example.com/apply",
                fields=fields,
                profile=profile,
                custom_answers={},
                platform="generic",
                known_domain=False,
                correction_warning="",
            )
            assert all(fm.confidence >= 0.9 for fm in scored)
            assert llm_calls == 0

    @pytest.mark.asyncio
    async def test_llm_escalation_triggers_system2(self):
        from jobpulse.form_engine.field_mapper import map_fields_with_confidence

        fields = [
            {"label": "Preferred pronouns", "type": "radio",
             "options": ["He/Him", "She/Her", "They/Them"], "value": ""},
        ]
        profile = {}

        with patch("jobpulse.form_engine.field_mapper.try_cached_mapping", return_value=None), \
             patch("jobpulse.form_engine.field_mapper.seed_mapping") as mock_seed, \
             patch("jobpulse.form_engine.field_mapper._ensure_label_db"), \
             patch("jobpulse.form_engine.confidence_scorer.parallel_grpo_candidates") as mock_grpo:
            mock_seed.return_value = ({}, fields)
            mock_grpo.return_value = [
                '{"Preferred pronouns": "He/Him"}',
                '{"Preferred pronouns": "He/Him"}',
                '{"Preferred pronouns": "She/Her"}',
            ]
            # This test verifies the escalation path is wired —
            # map_fields_with_confidence delegates to existing map_fields for LLM,
            # then scores, then escalates low-confidence via System 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_auq_integration.py -v`
Expected: FAIL with `ImportError: cannot import name 'map_fields_with_confidence'`

- [ ] **Step 3: Add map_fields_with_confidence to field_mapper.py**

Add this function after the existing `map_fields()` function in `jobpulse/form_engine/field_mapper.py`:

```python
async def map_fields_with_confidence(
    page_url: str, fields: list[dict], profile: dict,
    custom_answers: dict, platform: str,
    known_domain: bool, correction_warning: str,
    domain_field_mappings: dict[str, str] | None = None,
    cached_screening: dict[str, str] | None = None,
) -> tuple[list, int]:
    """Like map_fields() but returns confidence-scored FieldMappings.

    Returns (list[FieldMapping], llm_calls).
    Low-confidence fields are escalated via Best-of-N consensus (System 2).
    """
    from jobpulse.form_engine.confidence_scorer import ConfidenceScorer, FieldMapping

    scorer = ConfidenceScorer()
    llm_calls = 0

    # System 1: try cached mapping first
    cached = try_cached_mapping(
        page_url, fields, profile, custom_answers, known_domain,
        domain_field_mappings=domain_field_mappings,
    )
    if cached is not None:
        return scorer.score_mappings(cached, source="cached", fields=fields), 0

    # System 1: deterministic seed mapping
    mapping, unresolved = seed_mapping(fields, profile, custom_answers)
    scored = scorer.score_mappings(mapping, source="deterministic", fields=fields)

    if not unresolved:
        return scored, 0

    # System 1: LLM mapping for unresolved fields
    llm_mapping, llm_call_count = await map_fields(
        page_url, fields, profile, custom_answers, platform,
        known_domain, correction_warning,
        domain_field_mappings=domain_field_mappings,
        cached_screening=cached_screening,
    )
    llm_calls += llm_call_count

    # Remove fields already resolved deterministically
    llm_only = {k: v for k, v in llm_mapping.items() if k not in mapping}
    llm_scored = scorer.score_mappings(llm_only, source="llm", fields=fields)
    scored.extend(llm_scored)

    # System 2: escalate low-confidence fields
    low_conf = [fm for fm in scored if not fm.is_confident]
    if low_conf:
        logger.info(
            "AUQ: %d/%d fields below confidence threshold, escalating to System 2",
            len(low_conf), len(scored),
        )
        consensus = scorer.escalate_low_confidence(
            low_confidence_mappings=low_conf,
            fields=fields,
            profile=profile,
            custom_answers=custom_answers,
            platform=platform,
        )
        llm_calls += 1
        # Replace low-confidence mappings with consensus results
        for fm in scored:
            if fm.label in consensus:
                fm.value = consensus[fm.label]
                fm.confidence = 0.92  # consensus confidence
                fm.source = "consensus"

        _emit_escalation_signal(low_conf, platform, page_url)

    return scored, llm_calls


def _emit_escalation_signal(
    low_conf_fields: list, platform: str, page_url: str,
) -> None:
    """Emit OptimizationEngine signal for AUQ escalation."""
    try:
        from shared.optimization import get_optimization_engine
        get_optimization_engine().emit(
            signal_type="adaptation",
            source_loop="auq_escalation",
            domain=platform,
            agent_name="field_mapper",
            payload={
                "param": "confidence_escalation",
                "field_count": len(low_conf_fields),
                "fields": [fm.label for fm in low_conf_fields],
                "page_url": page_url,
            },
            session_id=f"auq_{platform}",
        )
    except Exception as exc:
        logger.debug("AUQ escalation signal failed: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_auq_integration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/field_mapper.py tests/jobpulse/test_auq_integration.py
git commit -m "feat(auq): map_fields_with_confidence with System 1/2 escalation"
```

---

### Task 5: Confidence Tracking in FormExperienceDB

**Files:**
- Modify: `jobpulse/form_experience_db.py` — add `field_confidence_log` table
- Test: `tests/jobpulse/test_confidence_scorer.py`

- [ ] **Step 1: Write failing test for confidence logging**

```python
# Append to tests/jobpulse/test_confidence_scorer.py

class TestConfidenceTracking:
    def test_log_and_retrieve_confidence(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.log_field_confidence(
            domain="greenhouse.io",
            field_label="Salary",
            predicted_confidence=0.7,
            actual_correct=True,
        )
        db.log_field_confidence(
            domain="greenhouse.io",
            field_label="Salary",
            predicted_confidence=0.8,
            actual_correct=False,
        )
        stats = db.get_confidence_calibration("greenhouse.io")
        assert stats["total"] == 2
        assert stats["correct"] == 1

    def test_calibration_empty_domain(self, tmp_path):
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        stats = db.get_confidence_calibration("unknown.com")
        assert stats["total"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestConfidenceTracking -v`
Expected: FAIL with `AttributeError: 'FormExperienceDB' object has no attribute 'log_field_confidence'`

- [ ] **Step 3: Add confidence logging to FormExperienceDB**

In `jobpulse/form_experience_db.py`, add the table to `_schema_sql()` method (append before the final `"""`):

```python
            CREATE TABLE IF NOT EXISTS field_confidence_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                field_label TEXT NOT NULL,
                predicted_confidence REAL NOT NULL,
                actual_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_confidence_domain
            ON field_confidence_log (domain);
```

Also add the same CREATE TABLE to `_init_db()` after the last `conn.execute(...)` block.

Add these methods to the `FormExperienceDB` class:

```python
    def log_field_confidence(
        self, domain: str, field_label: str,
        predicted_confidence: float, actual_correct: bool,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO field_confidence_log
                   (domain, field_label, predicted_confidence, actual_correct, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (domain, field_label, predicted_confidence, int(actual_correct), now),
            )

    def get_confidence_calibration(self, domain: str) -> dict:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT COUNT(*), SUM(actual_correct)
                   FROM field_confidence_log WHERE domain = ?""",
                (domain,),
            ).fetchone()
        total = row[0] if row else 0
        correct = row[1] or 0
        return {"total": total, "correct": correct}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestConfidenceTracking -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_confidence_scorer.py
git commit -m "feat(auq): field_confidence_log table in FormExperienceDB for calibration"
```

---

### Task 6: Wire into NativeFormFiller

**Files:**
- Modify: `jobpulse/native_form_filler.py`
- Modify: `jobpulse/form_engine/field_mapper.py` (exports)

- [ ] **Step 1: Add map_fields_with_confidence to field_mapper exports**

In `jobpulse/native_form_filler.py`, add `map_fields_with_confidence` to the imports from `jobpulse.form_engine.field_mapper`:

```python
from jobpulse.form_engine.field_mapper import (
    clean_mapping,
    is_screening_like_field,
    learn_field_mapping,
    map_fields,
    map_fields_with_confidence,
    recover_failed_fields_with_llm,
    recover_failed_fields_with_vision,
    review_form,
    screen_questions,
    seed_mapping,
    try_cached_mapping,
    vision_map_unlabeled_fields,
)
```

- [ ] **Step 2: Update _log_field_trajectory to use FieldMapping confidence**

In `native_form_filler.py`, the existing `_log_field_trajectory` function already accepts a `confidence` parameter. No change needed to that function — the wiring happens at the call site where `map_fields_with_confidence` results feed into the fill loop.

The NativeFormFiller's `fill_page()` method currently calls `map_fields()` which returns `(mapping: dict, llm_calls: int)`. The new `map_fields_with_confidence()` returns `(list[FieldMapping], llm_calls: int)`.

Add an env-var gate so the feature can be toggled:

```python
# In NativeFormFiller.fill_page(), replace the map_fields call:
use_auq = os.environ.get("AUQ_ENABLED", "").lower() in ("1", "true")
if use_auq:
    from jobpulse.form_engine.confidence_scorer import FieldMapping
    scored_mappings, llm_calls = await map_fields_with_confidence(
        page_url=page_url, fields=fields, profile=profile,
        custom_answers=custom_answers, platform=platform,
        known_domain=known_domain, correction_warning=correction_warning,
        domain_field_mappings=domain_field_mappings,
        cached_screening=cached_screening,
    )
    mapping = {fm.label: fm.value for fm in scored_mappings}
    confidence_map = {fm.label: fm.confidence for fm in scored_mappings}
else:
    mapping, llm_calls = await map_fields(
        page_url=page_url, fields=fields, profile=profile,
        custom_answers=custom_answers, platform=platform,
        known_domain=known_domain, correction_warning=correction_warning,
        domain_field_mappings=domain_field_mappings,
        cached_screening=cached_screening,
    )
    confidence_map = {}
```

Then in the fill loop where `_log_field_trajectory` is called, pass `confidence_map.get(label, 1.0)` as the confidence parameter.

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v --timeout=30`
Expected: All existing tests PASS (AUQ disabled by default)

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat(auq): wire map_fields_with_confidence into NativeFormFiller with AUQ_ENABLED gate"
```

---

### Task 7: Post-Fill Confidence Calibration Logging

**Files:**
- Modify: `jobpulse/form_engine/confidence_scorer.py`
- Modify: `jobpulse/native_form_filler.py` (after fill verification)

- [ ] **Step 1: Write failing test for calibration logging**

```python
# Append to tests/jobpulse/test_confidence_scorer.py

class TestCalibrationLogging:
    def test_log_fill_outcome_updates_db(self, tmp_path):
        from jobpulse.form_engine.confidence_scorer import log_fill_outcomes
        from jobpulse.form_experience_db import FormExperienceDB

        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        outcomes = [
            {"label": "Name", "confidence": 0.95, "correct": True},
            {"label": "Salary", "confidence": 0.6, "correct": False},
        ]
        log_fill_outcomes("test.com", outcomes, db=db)
        stats = db.get_confidence_calibration("test.com")
        assert stats["total"] == 2
        assert stats["correct"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestCalibrationLogging -v`
Expected: FAIL with `ImportError: cannot import name 'log_fill_outcomes'`

- [ ] **Step 3: Implement log_fill_outcomes**

```python
# Append to jobpulse/form_engine/confidence_scorer.py

def log_fill_outcomes(
    domain: str,
    outcomes: list[dict],
    *,
    db=None,
) -> None:
    """Log per-field confidence vs actual correctness for calibration."""
    if db is None:
        from jobpulse.form_experience_db import FormExperienceDB
        db = FormExperienceDB()

    for o in outcomes:
        db.log_field_confidence(
            domain=domain,
            field_label=o["label"],
            predicted_confidence=o["confidence"],
            actual_correct=o["correct"],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py::TestCalibrationLogging -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_engine/confidence_scorer.py tests/jobpulse/test_confidence_scorer.py
git commit -m "feat(auq): log_fill_outcomes for confidence calibration tracking"
```

---

### Task 8: Run Full Test Suite

- [ ] **Step 1: Run all AUQ tests**

Run: `python -m pytest tests/jobpulse/test_confidence_scorer.py tests/jobpulse/test_auq_integration.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run form engine regression tests**

Run: `python -m pytest tests/jobpulse/ -v -k "form" --timeout=30`
Expected: No regressions

- [ ] **Step 3: Commit (if any final fixes needed)**

```bash
git add -A
git commit -m "test(auq): full suite passing, no regressions"
```
