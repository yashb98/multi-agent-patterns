# PRAXIS — Procedural Memory with Cross-Domain Generalization

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade FormExperienceDB and NavigationLearner from domain-only indexing to (content_hash, platform) dual-key indexing, enabling cross-domain generalization when a domain's stored sequence fails — finding structurally similar pages from different companies.

**Architecture:** Current state: both DBs are keyed by domain only. New state: every stored experience also records a `content_hash` (structural fingerprint of the page/form). On domain miss or failure, fall back to matching by content hash across ALL domains. Store state-action-result tuples (not just successes). Add negative exemplars so the system knows what NOT to do.

**Tech Stack:** Python, SQLite, hashlib (SHA-256), `jobpulse/form_experience_db.py`, `jobpulse/navigation_learner.py`

---

## File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/content_hasher.py` (CREATE) | Compute structural content hash from page a11y tree / field list |
| `jobpulse/form_experience_db.py` (MODIFY) | Add content_hash column, dual-key queries, negative exemplar storage |
| `jobpulse/navigation_learner.py` (MODIFY) | Add content_hash column, cross-domain fallback |
| `jobpulse/native_form_filler.py` (MODIFY) | Pass content_hash when storing/querying experience |
| `tests/jobpulse/test_content_hasher.py` (CREATE) | Hash computation tests |
| `tests/jobpulse/test_praxis_memory.py` (CREATE) | Cross-domain retrieval, negative exemplar tests |

---

### Task 1: Content Hasher — Structural Page Fingerprinting

**Files:**
- Create: `jobpulse/content_hasher.py`
- Test: `tests/jobpulse/test_content_hasher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_content_hasher.py
"""Tests for structural content hashing."""
from __future__ import annotations

import pytest

from jobpulse.content_hasher import compute_content_hash


class TestContentHasher:
    def test_same_fields_same_hash(self):
        fields_a = [
            {"label": "First Name", "type": "text"},
            {"label": "Email", "type": "text"},
            {"label": "Resume", "type": "file"},
        ]
        fields_b = [
            {"label": "First Name", "type": "text"},
            {"label": "Email", "type": "text"},
            {"label": "Resume", "type": "file"},
        ]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_different_fields_different_hash(self):
        fields_a = [
            {"label": "First Name", "type": "text"},
        ]
        fields_b = [
            {"label": "Salary", "type": "text"},
        ]
        assert compute_content_hash(fields_a) != compute_content_hash(fields_b)

    def test_order_independent(self):
        """Field order shouldn't change the hash — forms may reorder fields."""
        fields_a = [
            {"label": "Email", "type": "text"},
            {"label": "Name", "type": "text"},
        ]
        fields_b = [
            {"label": "Name", "type": "text"},
            {"label": "Email", "type": "text"},
        ]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_ignores_non_structural_keys(self):
        """Value, selector, options should not affect structural hash."""
        fields_a = [
            {"label": "Name", "type": "text", "value": "Yash", "selector": "#name"},
        ]
        fields_b = [
            {"label": "Name", "type": "text", "value": "", "selector": ".name-input"},
        ]
        assert compute_content_hash(fields_a) == compute_content_hash(fields_b)

    def test_includes_type_in_hash(self):
        """Same label but different type = different hash."""
        fields_a = [{"label": "Gender", "type": "text"}]
        fields_b = [{"label": "Gender", "type": "radio"}]
        assert compute_content_hash(fields_a) != compute_content_hash(fields_b)

    def test_empty_fields_returns_hash(self):
        h = compute_content_hash([])
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hash_is_hex_prefix(self):
        h = compute_content_hash([{"label": "X", "type": "text"}])
        assert len(h) == 16
        int(h, 16)  # should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_content_hasher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.content_hasher'`

- [ ] **Step 3: Implement content_hasher.py**

```python
# jobpulse/content_hasher.py
"""Structural content hashing for cross-domain form matching.

Computes a fingerprint from a page's field labels and types (structure),
ignoring values, selectors, and options (instance data). Used by PRAXIS
procedural memory for cross-domain generalization.
"""
from __future__ import annotations

import hashlib
import json


def compute_content_hash(fields: list[dict]) -> str:
    """Compute a 16-char hex hash from sorted field (label, type) pairs.

    Order-independent. Ignores values, selectors, options — only structural.
    """
    structural = sorted(
        (f.get("label", "").lower().strip(), f.get("type", "text"))
        for f in fields
    )
    raw = json.dumps(structural, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_content_hasher.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/content_hasher.py tests/jobpulse/test_content_hasher.py
git commit -m "feat(praxis): content_hasher for structural page fingerprinting"
```

---

### Task 2: Add content_hash Column to FormExperienceDB

**Files:**
- Modify: `jobpulse/form_experience_db.py`
- Test: `tests/jobpulse/test_praxis_memory.py`

- [ ] **Step 1: Write failing test for content_hash storage**

```python
# tests/jobpulse/test_praxis_memory.py
"""Tests for PRAXIS procedural memory — cross-domain generalization."""
from __future__ import annotations

import pytest

from jobpulse.form_experience_db import FormExperienceDB


class TestContentHashStorage:
    def test_store_with_content_hash(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="company-a.com",
            platform="greenhouse",
            adapter="playwright",
            pages_filled=2,
            field_types={"text": 5, "file": 1},
            screening_questions=["Are you authorized?"],
            time_seconds=45.0,
            success=True,
            content_hash="abc123def456789a",
        )
        exp = db.lookup("https://company-a.com/apply")
        assert exp is not None
        assert exp["content_hash"] == "abc123def456789a"

    def test_store_without_content_hash_defaults_empty(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="example.com",
            platform="generic",
            adapter="playwright",
            pages_filled=1,
            field_types={"text": 3},
            screening_questions=[],
            time_seconds=20.0,
            success=True,
        )
        exp = db.lookup("https://example.com/apply")
        assert exp is not None
        assert exp.get("content_hash", "") == ""

    def test_cross_domain_lookup_by_content_hash(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        # Store experience for domain A
        db.store(
            domain="alpha.com", platform="greenhouse", adapter="playwright",
            pages_filled=2, field_types={"text": 5},
            screening_questions=[], time_seconds=30.0, success=True,
            content_hash="shared_hash_1234",
        )
        # Domain B has no experience — but same content hash
        result = db.lookup_by_content_hash("shared_hash_1234", exclude_domain="beta.com")
        assert result is not None
        assert result["domain"] == "alpha.com"
        assert result["platform"] == "greenhouse"

    def test_cross_domain_excludes_self(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store(
            domain="only.com", platform="lever", adapter="playwright",
            pages_filled=1, field_types={"text": 2},
            screening_questions=[], time_seconds=15.0, success=True,
            content_hash="unique_hash",
        )
        result = db.lookup_by_content_hash("unique_hash", exclude_domain="only.com")
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestContentHashStorage -v`
Expected: FAIL with `TypeError: store() got an unexpected keyword argument 'content_hash'`

- [ ] **Step 3: Add content_hash to FormExperienceDB schema and methods**

In `jobpulse/form_experience_db.py`:

1. Add migration in `_init_db()` after existing migrations — add `content_hash` column to `form_experience` table:

```python
            # Migration: add content_hash column if missing
            try:
                conn.execute("SELECT content_hash FROM form_experience LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE form_experience ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_form_experience_content_hash
                ON form_experience (content_hash)
            """)
```

2. Update the `store()` method signature to accept `content_hash: str = ""` and include it in the INSERT/UPDATE.

3. Update `lookup()` to return `content_hash` in the result dict.

4. Add `lookup_by_content_hash()` method:

```python
    def lookup_by_content_hash(
        self, content_hash: str, exclude_domain: str = "",
    ) -> dict | None:
        """Find experience from any domain with matching content hash.

        Returns the most recently updated successful experience, excluding
        the given domain (to avoid self-matching).
        """
        if not content_hash:
            return None
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """SELECT * FROM form_experience
                   WHERE content_hash = ? AND domain != ? AND success = 1
                   ORDER BY updated_at DESC LIMIT 1""",
                (content_hash, exclude_domain),
            ).fetchone()
        if row:
            return dict(row)
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestContentHashStorage -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_praxis_memory.py
git commit -m "feat(praxis): content_hash column in FormExperienceDB with cross-domain lookup"
```

---

### Task 3: Negative Exemplars in FormExperienceDB

**Files:**
- Modify: `jobpulse/form_experience_db.py`
- Test: `tests/jobpulse/test_praxis_memory.py`

- [ ] **Step 1: Write failing test for negative exemplars**

```python
# Append to tests/jobpulse/test_praxis_memory.py

class TestNegativeExemplars:
    def test_store_negative_exemplar(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_negative_exemplar(
            domain="workday.com",
            field_label="Salary",
            value_tried="negotiate",
            failure_reason="validation_error",
            platform="workday",
            content_hash="wday_hash_123456",
        )
        negatives = db.get_negative_exemplars("workday.com")
        assert len(negatives) == 1
        assert negatives[0]["field_label"] == "Salary"
        assert negatives[0]["value_tried"] == "negotiate"

    def test_cross_domain_negative_exemplars(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        db.store_negative_exemplar(
            domain="alpha.com", field_label="Visa", value_tried="N/A",
            failure_reason="wrong_value", platform="greenhouse",
            content_hash="shared_hash",
        )
        # Cross-domain lookup by content hash
        negatives = db.get_negative_exemplars_by_hash("shared_hash")
        assert len(negatives) == 1
        assert negatives[0]["domain"] == "alpha.com"

    def test_negative_exemplar_deduplication(self, tmp_path):
        db = FormExperienceDB(db_path=str(tmp_path / "test.db"))
        for _ in range(3):
            db.store_negative_exemplar(
                domain="dup.com", field_label="X", value_tried="bad",
                failure_reason="wrong", platform="generic",
                content_hash="dup_hash",
            )
        negatives = db.get_negative_exemplars("dup.com")
        assert len(negatives) == 1
        assert negatives[0]["attempt_count"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestNegativeExemplars -v`
Expected: FAIL with `AttributeError: 'FormExperienceDB' object has no attribute 'store_negative_exemplar'`

- [ ] **Step 3: Add negative_exemplars table and methods**

In `jobpulse/form_experience_db.py`, add table creation in `_init_db()`:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS negative_exemplars (
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    value_tried TEXT NOT NULL,
                    failure_reason TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain, field_label, value_tried)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_neg_content_hash
                ON negative_exemplars (content_hash)
            """)
```

Add methods to `FormExperienceDB`:

```python
    def store_negative_exemplar(
        self, domain: str, field_label: str, value_tried: str,
        failure_reason: str, platform: str = "", content_hash: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO negative_exemplars
                   (domain, field_label, value_tried, failure_reason, platform,
                    content_hash, attempt_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(domain, field_label, value_tried) DO UPDATE SET
                       attempt_count = attempt_count + 1,
                       failure_reason = excluded.failure_reason,
                       updated_at = excluded.updated_at""",
                (domain, field_label, value_tried, failure_reason, platform,
                 content_hash, now, now),
            )

    def get_negative_exemplars(self, domain: str) -> list[dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM negative_exemplars WHERE domain = ? ORDER BY updated_at DESC",
                (domain,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_negative_exemplars_by_hash(self, content_hash: str) -> list[dict]:
        if not content_hash:
            return []
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM negative_exemplars WHERE content_hash = ? ORDER BY updated_at DESC",
                (content_hash,),
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestNegativeExemplars -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_praxis_memory.py
git commit -m "feat(praxis): negative exemplars table with cross-domain content hash lookup"
```

---

### Task 4: Add content_hash to NavigationLearner

**Files:**
- Modify: `jobpulse/navigation_learner.py`
- Test: `tests/jobpulse/test_praxis_memory.py`

- [ ] **Step 1: Write failing test for nav learner content hash**

```python
# Append to tests/jobpulse/test_praxis_memory.py

from jobpulse.navigation_learner import NavigationLearner


class TestNavigationLearnerContentHash:
    def test_save_with_content_hash(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")  # avoid real transfer DB
        steps = [{"action": "click", "selector": "#apply"}]
        nl.save_sequence("company-a.com", steps, success=True,
                         platform="greenhouse", content_hash="nav_hash_1234")
        result = nl.get_sequence("company-a.com")
        assert result == steps

    def test_cross_domain_nav_fallback(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")
        steps = [{"action": "click", "selector": "#apply-btn"}]
        nl.save_sequence("alpha.com", steps, success=True,
                         platform="greenhouse", content_hash="shared_nav_hash")
        # No sequence for beta.com — but same content hash
        result = nl.get_sequence_by_content_hash(
            "shared_nav_hash", exclude_domain="beta.com",
        )
        assert result == steps

    def test_failed_sequence_stored_with_hash(self, tmp_path):
        nl = NavigationLearner(db_path=str(tmp_path / "nav.db"))
        nl._transfer_db_path = str(tmp_path / "transfer.db")
        fail_steps = [{"action": "click", "selector": "#wrong"}]
        nl.save_sequence("fail.com", fail_steps, success=False,
                         platform="lever", content_hash="fail_hash")
        # Failed sequences don't come back via get_sequence
        assert nl.get_sequence("fail.com") is None
        # But they're stored for negative exemplar retrieval
        result = nl.get_failed_sequences("fail.com")
        assert len(result) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestNavigationLearnerContentHash -v`
Expected: FAIL with `TypeError: save_sequence() got an unexpected keyword argument 'content_hash'`

- [ ] **Step 3: Add content_hash to NavigationLearner**

In `jobpulse/navigation_learner.py`:

1. Add migration in `_init_db()`:

```python
            # Migration: add content_hash column if missing
            try:
                conn.execute("SELECT content_hash FROM sequences LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE sequences ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sequences_content_hash
                ON sequences (content_hash)
            """)
```

2. Update `save_sequence()` to accept `content_hash: str = ""`:

```python
    def save_sequence(self, domain_or_url: str, steps: list[dict], success: bool,
                      platform: str = "", content_hash: str = ""):
```

Include `content_hash` in the INSERT and ON CONFLICT UPDATE.

3. Add `get_sequence_by_content_hash()`:

```python
    def get_sequence_by_content_hash(
        self, content_hash: str, exclude_domain: str = "",
    ) -> list[dict] | None:
        if not content_hash:
            return None
        exclude = self._normalize_domain(exclude_domain) if exclude_domain else ""
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                """SELECT steps FROM sequences
                   WHERE content_hash = ? AND domain != ? AND success = 1
                   ORDER BY updated_at DESC LIMIT 1""",
                (content_hash, exclude),
            ).fetchone()
        if row:
            return json.loads(row[0])
        return None
```

4. Add `get_failed_sequences()`:

```python
    def get_failed_sequences(self, domain_or_url: str) -> list[dict]:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT steps, updated_at, content_hash FROM sequences WHERE domain = ? AND success = 0",
                (domain,),
            ).fetchall()
        return [
            {"steps": json.loads(r[0]), "updated_at": r[1], "content_hash": r[2]}
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_praxis_memory.py::TestNavigationLearnerContentHash -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/navigation_learner.py tests/jobpulse/test_praxis_memory.py
git commit -m "feat(praxis): content_hash in NavigationLearner with cross-domain fallback"
```

---

### Task 5: Wire Content Hash into NativeFormFiller

**Files:**
- Modify: `jobpulse/native_form_filler.py`

- [ ] **Step 1: Import content_hasher**

Add import to `jobpulse/native_form_filler.py`:

```python
from jobpulse.content_hasher import compute_content_hash
```

- [ ] **Step 2: Compute hash after field scan, pass to experience lookups**

In `NativeFormFiller.fill_page()`, after `scan_fields()` returns the field list, compute the content hash:

```python
content_hash = compute_content_hash(fields)
```

Pass `content_hash` to `FormExperienceDB.store()` calls in the post-fill experience recording.

When `try_cached_mapping()` returns None and `FormExperienceDB.lookup()` returns None, add a cross-domain fallback:

```python
# After domain lookup fails, try content hash
cross_domain = db.lookup_by_content_hash(content_hash, exclude_domain=domain)
if cross_domain:
    logger.info("PRAXIS: cross-domain match found from %s", cross_domain["domain"])
```

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/jobpulse/test_native_form_filler.py -v --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add jobpulse/native_form_filler.py
git commit -m "feat(praxis): wire content_hash into NativeFormFiller for cross-domain generalization"
```

---

### Task 6: Wire into Navigator for Cross-Domain Nav Sequences

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py`

- [ ] **Step 1: Add cross-domain fallback in navigator**

In `_navigator.py`, where `NavigationLearner.get_sequence(domain)` is called, add a fallback:

```python
# After get_sequence returns None:
if sequence is None and content_hash:
    sequence = nav_learner.get_sequence_by_content_hash(
        content_hash, exclude_domain=domain,
    )
    if sequence:
        logger.info("PRAXIS: using cross-domain nav sequence (content_hash match)")
```

The `content_hash` would need to be computed from the initial page snapshot or passed from the caller. Since the navigator runs before field scanning, use a simplified hash from the page's a11y tree structure.

- [ ] **Step 2: Run existing navigator tests**

Run: `python -m pytest tests/jobpulse/test_application_orchestrator.py -v --timeout=30`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py
git commit -m "feat(praxis): cross-domain nav sequence fallback via content hash"
```

---

### Task 7: Run Full Test Suite

- [ ] **Step 1: Run all PRAXIS tests**

Run: `python -m pytest tests/jobpulse/test_content_hasher.py tests/jobpulse/test_praxis_memory.py -v`
Expected: All tests PASS

- [ ] **Step 2: Run full jobpulse regression**

Run: `python -m pytest tests/jobpulse/ -v --timeout=30`
Expected: No regressions

- [ ] **Step 3: Final commit if needed**

```bash
git add -A
git commit -m "test(praxis): full suite passing, no regressions"
```
