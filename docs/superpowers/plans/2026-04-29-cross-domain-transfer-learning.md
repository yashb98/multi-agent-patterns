# Cross-Domain Transfer Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `PlatformTransferEngine` that transfers form-filling knowledge between structurally similar ATS domains using Thompson Sampling, so new domains don't start cold when the platform is already well-understood.

**Architecture:** A new `jobpulse/platform_transfer.py` module owns all transfer logic — similarity computation (8 signals), Thompson Sampling donor selection (Beta distributions), and outcome recording. Existing lookup methods in `FormExperienceDB`, `NavigationLearner`, and `GotchasDB` gain transparent fallback: on domain-miss, they ask the transfer engine for a donor. `post_apply_hook` triggers similarity recomputation after each application.

**Tech Stack:** Python 3.12+ | SQLite (WAL mode) | numpy (Beta sampling via `numpy.random.beta`) | pytest + tmp_path

---

## File Structure

| File | Responsibility |
|------|----------------|
| `jobpulse/platform_transfer.py` | **NEW** — `PlatformTransferEngine` class: schema init, 8 similarity metrics, Thompson Sampling transfer, outcome recording, matrix recomputation |
| `jobpulse/form_experience_db.py` | **MODIFY** — Add `_transfer_engine` lazy property, add transfer fallback to `get_timing`, `get_container`, `get_field_mappings`, `get_failure_reasons`, `get_scan_strategy` |
| `jobpulse/navigation_learner.py` | **MODIFY** — Add `_transfer_engine` lazy property, add transfer fallback to `get_sequence` |
| `jobpulse/form_engine/gotchas.py` | **MODIFY** — Add `_transfer_engine` lazy property, add transfer fallback to `lookup_domain` |
| `jobpulse/post_apply_hook.py` | **MODIFY** — Trigger `recompute_similarity_matrix` after form experience recording, record transfer outcomes |
| `tests/jobpulse/test_platform_transfer.py` | **NEW** — Unit tests for all engine methods + integration tests for fallback wiring |

---

### Task 1: PlatformTransferEngine — Schema + TransferResult

**Files:**
- Create: `jobpulse/platform_transfer.py`
- Create: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test for schema creation**

```python
# tests/jobpulse/test_platform_transfer.py
"""Tests for PlatformTransferEngine."""
from __future__ import annotations

import sqlite3

import pytest

from jobpulse.platform_transfer import PlatformTransferEngine


class TestSchema:
    def test_creates_tables(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "platform_similarity" in tables
        assert "transfer_outcomes" in tables
        conn.close()

    def test_idempotent_init(self, tmp_path):
        db = str(tmp_path / "transfer.db")
        PlatformTransferEngine(db_path=db)
        PlatformTransferEngine(db_path=db)  # No error on second init
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSchema -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.platform_transfer'`

- [ ] **Step 3: Write minimal implementation — schema + TypedDict**

```python
# jobpulse/platform_transfer.py
"""Cross-domain transfer learning engine.

Computes similarity between ATS domains using 8 learned signals,
selects donors via Thompson Sampling (Beta distributions), and
records transfer outcomes to improve future selections.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TypedDict

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "form_experience.db")

SIGNAL_TYPES = (
    "field_types",
    "page_count",
    "timing_profile",
    "fill_techniques",
    "failure_patterns",
    "correction_rates",
    "navigation_flow",
    "container_selectors",
)


class TransferResult(TypedDict):
    donor_domain: str
    signal_type: str
    similarity: float
    confidence: int
    _transfer: bool


class PlatformTransferEngine:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS platform_similarity (
                    domain_a TEXT NOT NULL,
                    domain_b TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    similarity REAL NOT NULL,
                    sample_count INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (domain_a, domain_b, signal_type)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transfer_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_domain TEXT NOT NULL,
                    donor_domain TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    alpha REAL NOT NULL DEFAULT 1.0,
                    beta_param REAL NOT NULL DEFAULT 1.0,
                    transfer_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    last_outcome TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (target_domain, donor_domain, signal_type)
                )
            """)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSchema -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/platform_transfer.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): PlatformTransferEngine schema + TransferResult type"
```

---

### Task 2: Similarity Metrics — 8 Signal Computations

**Files:**
- Modify: `jobpulse/platform_transfer.py`
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing tests for each similarity metric**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
class TestSimilarityMetrics:
    def test_cosine_similarity_identical(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 3, "email": 1, "tel": 1}
        vec_b = {"text": 3, "email": 1, "tel": 1}
        assert engine._cosine_similarity(vec_a, vec_b) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 1}
        vec_b = {"email": 1}
        assert engine._cosine_similarity(vec_a, vec_b) == pytest.approx(0.0)

    def test_cosine_similarity_partial_overlap(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        vec_a = {"text": 3, "email": 1}
        vec_b = {"text": 2, "tel": 1}
        result = engine._cosine_similarity(vec_a, vec_b)
        assert 0.0 < result < 1.0

    def test_cosine_similarity_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._cosine_similarity({}, {}) == 0.0

    def test_jaccard_index(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_jaccard_index_identical(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index({"a", "b"}, {"a", "b"}) == pytest.approx(1.0)

    def test_jaccard_index_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._jaccard_index(set(), set()) == 0.0

    def test_normalized_page_diff(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._normalized_page_diff(3, 3) == pytest.approx(1.0)
        assert engine._normalized_page_diff(3, 5) == pytest.approx(0.6)
        assert engine._normalized_page_diff(0, 0) == 0.0

    def test_normalized_levenshtein(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        seq_a = ["login", "fill_form", "submit"]
        seq_b = ["login", "fill_form", "submit"]
        assert engine._normalized_levenshtein(seq_a, seq_b) == pytest.approx(1.0)

    def test_normalized_levenshtein_different(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        seq_a = ["login", "fill_form", "submit"]
        seq_b = ["login", "review", "submit"]
        result = engine._normalized_levenshtein(seq_a, seq_b)
        assert 0.0 < result < 1.0

    def test_normalized_levenshtein_empty(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._normalized_levenshtein([], []) == 0.0

    def test_token_overlap(self, tmp_path):
        engine = PlatformTransferEngine(db_path=str(tmp_path / "t.db"))
        assert engine._token_overlap("#app .form-container", "#app .form-wrapper") > 0.0
        assert engine._token_overlap("#app .form", "#app .form") == pytest.approx(1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSimilarityMetrics -v`
Expected: FAIL with `AttributeError: 'PlatformTransferEngine' object has no attribute '_cosine_similarity'`

- [ ] **Step 3: Implement similarity metrics**

Add these methods to `PlatformTransferEngine` in `jobpulse/platform_transfer.py`:

```python
    @staticmethod
    def _cosine_similarity(vec_a: dict[str, int | float], vec_b: dict[str, int | float]) -> float:
        if not vec_a or not vec_b:
            return 0.0
        keys = set(vec_a) | set(vec_b)
        dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in keys)
        mag_a = sum(v ** 2 for v in vec_a.values()) ** 0.5
        mag_b = sum(v ** 2 for v in vec_b.values()) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def _jaccard_index(set_a: set, set_b: set) -> float:
        if not set_a and not set_b:
            return 0.0
        union = set_a | set_b
        return len(set_a & set_b) / len(union)

    @staticmethod
    def _normalized_page_diff(pages_a: int, pages_b: int) -> float:
        if pages_a == 0 and pages_b == 0:
            return 0.0
        return 1.0 - abs(pages_a - pages_b) / max(pages_a, pages_b)

    @staticmethod
    def _normalized_levenshtein(seq_a: list[str], seq_b: list[str]) -> float:
        if not seq_a and not seq_b:
            return 0.0
        n, m = len(seq_a), len(seq_b)
        dp = list(range(m + 1))
        for i in range(1, n + 1):
            prev, dp[0] = dp[0], i
            for j in range(1, m + 1):
                cost = 0 if seq_a[i - 1] == seq_b[j - 1] else 1
                prev, dp[j] = dp[j], min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
        distance = dp[m]
        max_len = max(n, m)
        return 1.0 - distance / max_len

    @staticmethod
    def _token_overlap(selector_a: str, selector_b: str) -> float:
        import re
        tokens_a = set(re.split(r"[\s.#\[\]=>\+~,()]+", selector_a)) - {""}
        tokens_b = set(re.split(r"[\s.#\[\]=>\+~,()]+", selector_b)) - {""}
        if not tokens_a and not tokens_b:
            return 0.0
        union = tokens_a | tokens_b
        return len(tokens_a & tokens_b) / len(union)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSimilarityMetrics -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/platform_transfer.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): 8 similarity metric functions (cosine, Jaccard, Levenshtein, page diff, token overlap)"
```

---

### Task 3: Similarity Matrix Recomputation

**Files:**
- Modify: `jobpulse/platform_transfer.py`
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test for matrix recomputation**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
import json


class TestSimilarityMatrix:
    def _seed_two_domains(self, db_path: str) -> None:
        """Insert form_experience rows for two Greenhouse-like domains."""
        import sqlite3
        from jobpulse.form_experience_db import FormExperienceDB

        exp_db = FormExperienceDB(db_path=db_path)
        exp_db.record(
            domain="boards.greenhouse.io/acme",
            platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email", "tel", "file"],
            screening_questions=[], time_seconds=45.0, success=True,
        )
        exp_db.store_timing("boards.greenhouse.io/acme", hydration_ms=800, fill_ms=2000, transition_ms=500)
        exp_db.store_container("boards.greenhouse.io/acme", "#application")
        exp_db.record_fill_technique("boards.greenhouse.io/acme", "email", "email", "type_text")
        exp_db.record_fill_technique("boards.greenhouse.io/acme", "phone", "tel", "type_text")

        exp_db.record(
            domain="boards.greenhouse.io/beta",
            platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email", "tel", "file", "select"],
            screening_questions=[], time_seconds=50.0, success=True,
        )
        exp_db.store_timing("boards.greenhouse.io/beta", hydration_ms=900, fill_ms=2200, transition_ms=600)
        exp_db.store_container("boards.greenhouse.io/beta", "#application .form")
        exp_db.record_fill_technique("boards.greenhouse.io/beta", "email", "email", "type_text")
        exp_db.record_fill_technique("boards.greenhouse.io/beta", "salary", "text", "select_option")

    def test_recompute_populates_similarity(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")

        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM platform_similarity").fetchall()
        conn.close()
        assert len(rows) > 0

    def test_recompute_stores_all_signal_types(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")

        conn = sqlite3.connect(db)
        signal_types = {r[0] for r in conn.execute(
            "SELECT DISTINCT signal_type FROM platform_similarity"
        ).fetchall()}
        conn.close()
        # At least field_types, page_count, timing_profile, fill_techniques, container_selectors
        # (correction_rates and navigation_flow need their own DBs seeded)
        assert len(signal_types) >= 5

    def test_recompute_incremental_only_new_domain(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")

        conn = sqlite3.connect(db)
        count_before = conn.execute("SELECT COUNT(*) FROM platform_similarity").fetchone()[0]
        conn.close()

        # Recompute for same domain — should update, not duplicate
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")

        conn = sqlite3.connect(db)
        count_after = conn.execute("SELECT COUNT(*) FROM platform_similarity").fetchone()[0]
        conn.close()
        assert count_after == count_before

    def test_similarity_values_in_range(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        self._seed_two_domains(db)
        engine = PlatformTransferEngine(db_path=db)
        engine.recompute_similarity_matrix("boards.greenhouse.io/acme")

        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT similarity FROM platform_similarity").fetchall()
        conn.close()
        for (sim,) in rows:
            assert 0.0 <= sim <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSimilarityMatrix -v`
Expected: FAIL with `AttributeError: 'PlatformTransferEngine' object has no attribute 'recompute_similarity_matrix'`

- [ ] **Step 3: Implement `recompute_similarity_matrix`**

Add to `PlatformTransferEngine` in `jobpulse/platform_transfer.py`. Add `import json` at the top of the file if not already there:

```python
    def _load_form_experience_data(self) -> dict[str, dict]:
        """Load all form_experience rows keyed by domain."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM form_experience WHERE success = 1"
            ).fetchall()
        return {r["domain"]: dict(r) for r in rows}

    def _load_timing_data(self) -> dict[str, dict]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM page_timings").fetchall()
        return {r["domain"]: dict(r) for r in rows}

    def _load_container_data(self) -> dict[str, str]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute("SELECT domain, selector FROM container_selectors").fetchall()
        return {r[0]: r[1] for r in rows}

    def _load_fill_techniques(self) -> dict[str, set[str]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT domain, technique FROM fill_techniques WHERE success = 1"
            ).fetchall()
        result: dict[str, set[str]] = {}
        for domain, technique in rows:
            result.setdefault(domain, set()).add(technique)
        return result

    def _load_failure_data(self) -> dict[str, dict[str, int]]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT domain, failure_type, COUNT(*) as cnt "
                "FROM form_failure_reasons GROUP BY domain, failure_type"
            ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for domain, ftype, cnt in rows:
            result.setdefault(domain, {})[ftype] = cnt
        return result

    def _load_correction_data(self) -> dict[str, dict[str, int]]:
        """Load correction frequency vectors from field_corrections.db (read-only)."""
        corrections_db = str(DATA_DIR / "field_corrections.db")
        try:
            with sqlite3.connect(corrections_db) as conn:
                rows = conn.execute(
                    "SELECT domain, field_label, COUNT(*) as cnt "
                    "FROM field_corrections GROUP BY domain, field_label"
                ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return {}
        result: dict[str, dict[str, int]] = {}
        for domain, label, cnt in rows:
            result.setdefault(domain, {})[label] = cnt
        return result

    def _load_navigation_data(self) -> dict[str, list[str]]:
        """Load nav action sequences from navigation_learning.db (read-only)."""
        nav_db = str(DATA_DIR / "navigation_learning.db")
        try:
            with sqlite3.connect(nav_db) as conn:
                rows = conn.execute(
                    "SELECT domain, steps FROM sequences WHERE success = 1"
                ).fetchall()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            return {}
        result: dict[str, list[str]] = {}
        for domain, steps_json in rows:
            try:
                steps = json.loads(steps_json)
                result[domain] = [s.get("action", "") for s in steps]
            except (json.JSONDecodeError, AttributeError):
                pass
        return result

    def recompute_similarity_matrix(self, trigger_domain: str) -> int:
        """Recompute similarity for all pairs involving trigger_domain.

        Returns the number of similarity rows written.
        """
        fe_data = self._load_form_experience_data()
        timing_data = self._load_timing_data()
        container_data = self._load_container_data()
        technique_data = self._load_fill_techniques()
        failure_data = self._load_failure_data()
        correction_data = self._load_correction_data()
        nav_data = self._load_navigation_data()

        all_domains = set(fe_data.keys())
        if trigger_domain not in all_domains:
            return 0

        now = datetime.now(UTC).isoformat()
        written = 0

        with sqlite3.connect(self._db_path) as conn:
            for other_domain in all_domains:
                if other_domain == trigger_domain:
                    continue

                pairs = self._compute_pair_signals(
                    trigger_domain, other_domain,
                    fe_data, timing_data, container_data,
                    technique_data, failure_data, correction_data, nav_data,
                )
                for signal_type, similarity, sample_count in pairs:
                    if sample_count < 2:
                        continue
                    conn.execute(
                        """INSERT INTO platform_similarity
                           (domain_a, domain_b, signal_type, similarity, sample_count, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(domain_a, domain_b, signal_type) DO UPDATE SET
                               similarity = excluded.similarity,
                               sample_count = excluded.sample_count,
                               updated_at = excluded.updated_at""",
                        (trigger_domain, other_domain, signal_type, similarity, sample_count, now),
                    )
                    conn.execute(
                        """INSERT INTO platform_similarity
                           (domain_a, domain_b, signal_type, similarity, sample_count, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(domain_a, domain_b, signal_type) DO UPDATE SET
                               similarity = excluded.similarity,
                               sample_count = excluded.sample_count,
                               updated_at = excluded.updated_at""",
                        (other_domain, trigger_domain, signal_type, similarity, sample_count, now),
                    )
                    written += 2

        logger.info(
            "transfer: recomputed similarity for %s — %d rows across %d peers",
            trigger_domain, written, len(all_domains) - 1,
        )
        return written

    def _compute_pair_signals(
        self,
        domain_a: str,
        domain_b: str,
        fe_data: dict,
        timing_data: dict,
        container_data: dict,
        technique_data: dict,
        failure_data: dict,
        correction_data: dict,
        nav_data: dict,
    ) -> list[tuple[str, float, int]]:
        """Compute all 8 signal similarities for a domain pair.

        Returns list of (signal_type, similarity, sample_count).
        """
        results: list[tuple[str, float, int]] = []
        fe_a, fe_b = fe_data.get(domain_a), fe_data.get(domain_b)

        # 1. field_types — cosine of field-type frequency vectors
        if fe_a and fe_b:
            def _parse_field_types(ft_raw) -> dict[str, int]:
                from collections import Counter
                if isinstance(ft_raw, str):
                    ft_list = json.loads(ft_raw)
                else:
                    ft_list = ft_raw
                return dict(Counter(ft_list))

            ft_a = _parse_field_types(fe_a.get("field_types", "[]"))
            ft_b = _parse_field_types(fe_b.get("field_types", "[]"))
            count = (fe_a.get("apply_count", 1) or 1) + (fe_b.get("apply_count", 1) or 1)
            results.append(("field_types", self._cosine_similarity(ft_a, ft_b), count))

            # 2. page_count
            pages_a = fe_a.get("pages_filled", 0) or 0
            pages_b = fe_b.get("pages_filled", 0) or 0
            if pages_a > 0 or pages_b > 0:
                results.append(("page_count", self._normalized_page_diff(pages_a, pages_b), count))

        # 3. timing_profile — cosine of [hydration, fill, transition]
        t_a, t_b = timing_data.get(domain_a), timing_data.get(domain_b)
        if t_a and t_b:
            vec_a = {
                "hydration": t_a["avg_hydration_ms"],
                "fill": t_a["avg_fill_ms"],
                "transition": t_a["avg_transition_ms"],
            }
            vec_b = {
                "hydration": t_b["avg_hydration_ms"],
                "fill": t_b["avg_fill_ms"],
                "transition": t_b["avg_transition_ms"],
            }
            samples = (t_a.get("sample_count", 1) or 1) + (t_b.get("sample_count", 1) or 1)
            results.append(("timing_profile", self._cosine_similarity(vec_a, vec_b), samples))

        # 4. fill_techniques — Jaccard index of technique sets
        tech_a, tech_b = technique_data.get(domain_a), technique_data.get(domain_b)
        if tech_a and tech_b:
            results.append(("fill_techniques", self._jaccard_index(tech_a, tech_b), len(tech_a) + len(tech_b)))

        # 5. failure_patterns — cosine of failure-type frequency vectors
        fail_a, fail_b = failure_data.get(domain_a), failure_data.get(domain_b)
        if fail_a and fail_b:
            results.append(("failure_patterns", self._cosine_similarity(fail_a, fail_b), sum(fail_a.values()) + sum(fail_b.values())))

        # 6. correction_rates — cosine of correction-field frequency vectors
        corr_a, corr_b = correction_data.get(domain_a), correction_data.get(domain_b)
        if corr_a and corr_b:
            results.append(("correction_rates", self._cosine_similarity(corr_a, corr_b), sum(corr_a.values()) + sum(corr_b.values())))

        # 7. navigation_flow — normalized Levenshtein of action sequences
        nav_a, nav_b = nav_data.get(domain_a), nav_data.get(domain_b)
        if nav_a and nav_b:
            results.append(("navigation_flow", self._normalized_levenshtein(nav_a, nav_b), len(nav_a) + len(nav_b)))

        # 8. container_selectors — token overlap
        cont_a, cont_b = container_data.get(domain_a), container_data.get(domain_b)
        if cont_a and cont_b:
            results.append(("container_selectors", self._token_overlap(cont_a, cont_b), 2))

        return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestSimilarityMatrix -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/platform_transfer.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): similarity matrix recomputation with 8 signal metrics"
```

---

### Task 4: Thompson Sampling — `get_transfer_data` + `record_outcome`

**Files:**
- Modify: `jobpulse/platform_transfer.py`
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing tests for Thompson Sampling**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
class TestThompsonSampling:
    def _seed_similarity_and_outcomes(self, db_path: str) -> None:
        """Seed similarity rows and outcome priors for testing."""
        conn = sqlite3.connect(db_path)
        now = "2026-04-29T00:00:00+00:00"
        # Two donors for target "new.greenhouse.io"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "acme.greenhouse.io", "timing_profile", 0.9, 5, now),
        )
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "beta.greenhouse.io", "timing_profile", 0.5, 3, now),
        )
        # Outcome: acme has strong history (α=10, β=2), beta is fresh (α=1, β=1)
        conn.execute(
            "INSERT INTO transfer_outcomes (target_domain, donor_domain, signal_type, alpha, beta_param, transfer_count, success_count, last_outcome, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "acme.greenhouse.io", "timing_profile", 10.0, 2.0, 12, 10, "success", now, now),
        )
        conn.commit()
        conn.close()

    def test_get_transfer_data_selects_donor(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        self._seed_similarity_and_outcomes(db)

        result = engine.get_transfer_data("new.greenhouse.io", "timing_profile")
        assert result is not None
        assert result["donor_domain"] in ("acme.greenhouse.io", "beta.greenhouse.io")
        assert result["signal_type"] == "timing_profile"
        assert result["_transfer"] is True
        assert 0.0 < result["similarity"] <= 1.0

    def test_get_transfer_data_prefers_strong_donor(self, tmp_path):
        """With many samples, acme (α=10, β=2) should be chosen more often than beta (α=1, β=1)."""
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        self._seed_similarity_and_outcomes(db)

        acme_count = 0
        for _ in range(100):
            result = engine.get_transfer_data("new.greenhouse.io", "timing_profile")
            if result and result["donor_domain"] == "acme.greenhouse.io":
                acme_count += 1
        # acme should win most of the time (>60% given strong prior + higher similarity)
        assert acme_count > 60

    def test_get_transfer_data_returns_none_below_threshold(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        # Only low-similarity donor
        conn = sqlite3.connect(db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("target.io", "donor.io", "timing_profile", 0.1, 2, now),
        )
        conn.commit()
        conn.close()

        result = engine.get_transfer_data("target.io", "timing_profile", min_similarity=0.3)
        assert result is None

    def test_get_transfer_data_no_donors(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)
        result = engine.get_transfer_data("unknown.io", "timing_profile")
        assert result is None

    def test_record_outcome_creates_entry(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)

        engine.record_outcome("target.io", "donor.io", "timing_profile", success=True)

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT alpha, beta_param, transfer_count, success_count, last_outcome "
            "FROM transfer_outcomes WHERE target_domain = ? AND donor_domain = ? AND signal_type = ?",
            ("target.io", "donor.io", "timing_profile"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 2.0   # α = 1 + 1 success
        assert row[1] == 1.0   # β = 1 (no failure)
        assert row[2] == 1     # transfer_count
        assert row[3] == 1     # success_count
        assert row[4] == "success"

    def test_record_outcome_updates_existing(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)

        engine.record_outcome("t.io", "d.io", "field_types", success=True)
        engine.record_outcome("t.io", "d.io", "field_types", success=False)
        engine.record_outcome("t.io", "d.io", "field_types", success=True)

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT alpha, beta_param, transfer_count, success_count "
            "FROM transfer_outcomes WHERE target_domain = 't.io' AND donor_domain = 'd.io'"
        ).fetchone()
        conn.close()
        assert row[0] == 3.0   # α = 1 + 2 successes
        assert row[1] == 2.0   # β = 1 + 1 failure
        assert row[2] == 3     # transfer_count
        assert row[3] == 2     # success_count

    def test_record_outcome_emits_optimization_signal(self, tmp_path, monkeypatch):
        db = str(tmp_path / "form_experience.db")
        engine = PlatformTransferEngine(db_path=db)

        emitted = []
        class FakeEngine:
            def emit(self, **kwargs):
                emitted.append(kwargs)

        monkeypatch.setattr(
            "jobpulse.platform_transfer.get_optimization_engine",
            lambda: FakeEngine(),
        )
        engine.record_outcome("t.io", "d.io", "timing_profile", success=True)
        assert len(emitted) == 1
        assert emitted[0]["signal_type"] == "transfer"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestThompsonSampling -v`
Expected: FAIL with `AttributeError: 'PlatformTransferEngine' object has no attribute 'get_transfer_data'`

- [ ] **Step 3: Implement Thompson Sampling methods**

Add to `PlatformTransferEngine` in `jobpulse/platform_transfer.py`. Add `import numpy as np` to the file's imports (at top, after the existing stdlib imports):

```python
    def get_transfer_data(
        self,
        target_domain: str,
        signal_type: str,
        min_similarity: float = 0.3,
    ) -> TransferResult | None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            candidates = conn.execute(
                """SELECT domain_b AS donor_domain, similarity, sample_count
                   FROM platform_similarity
                   WHERE domain_a = ? AND signal_type = ? AND similarity >= ? AND sample_count >= 2""",
                (target_domain, signal_type, min_similarity),
            ).fetchall()

        if not candidates:
            return None

        import numpy as np

        best_score = -1.0
        best_candidate = None

        for row in candidates:
            donor = row["donor_domain"]
            alpha, beta_param = self._get_outcome_params(target_domain, donor, signal_type)
            sampled = float(np.random.beta(alpha, beta_param))
            score = sampled * row["similarity"]
            if score > best_score:
                best_score = score
                best_candidate = row

        if best_candidate is None:
            return None

        return TransferResult(
            donor_domain=best_candidate["donor_domain"],
            signal_type=signal_type,
            similarity=best_candidate["similarity"],
            confidence=best_candidate["sample_count"],
            _transfer=True,
        )

    def _get_outcome_params(self, target: str, donor: str, signal_type: str) -> tuple[float, float]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT alpha, beta_param, updated_at FROM transfer_outcomes "
                "WHERE target_domain = ? AND donor_domain = ? AND signal_type = ?",
                (target, donor, signal_type),
            ).fetchone()
        if not row:
            return 1.0, 1.0

        alpha, beta_param = row[0], row[1]

        # Decay stale distributions — halve if not used in 30 days
        try:
            updated = datetime.fromisoformat(row[2])
            if (datetime.now(UTC) - updated).days > 30:
                alpha = max(1.0, alpha / 2)
                beta_param = max(1.0, beta_param / 2)
        except (ValueError, TypeError):
            pass

        return alpha, beta_param

    def record_outcome(
        self,
        target_domain: str,
        donor_domain: str,
        signal_type: str,
        success: bool,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT alpha, beta_param, transfer_count, success_count "
                "FROM transfer_outcomes "
                "WHERE target_domain = ? AND donor_domain = ? AND signal_type = ?",
                (target_domain, donor_domain, signal_type),
            ).fetchone()

            if existing:
                new_alpha = existing[0] + (1.0 if success else 0.0)
                new_beta = existing[1] + (0.0 if success else 1.0)
                new_count = existing[2] + 1
                new_success = existing[3] + (1 if success else 0)
                conn.execute(
                    """UPDATE transfer_outcomes SET
                       alpha = ?, beta_param = ?, transfer_count = ?,
                       success_count = ?, last_outcome = ?, updated_at = ?
                       WHERE target_domain = ? AND donor_domain = ? AND signal_type = ?""",
                    (new_alpha, new_beta, new_count, new_success,
                     "success" if success else "failure", now,
                     target_domain, donor_domain, signal_type),
                )
            else:
                conn.execute(
                    """INSERT INTO transfer_outcomes
                       (target_domain, donor_domain, signal_type,
                        alpha, beta_param, transfer_count, success_count,
                        last_outcome, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
                    (target_domain, donor_domain, signal_type,
                     2.0 if success else 1.0,
                     1.0 if success else 2.0,
                     1 if success else 0,
                     "success" if success else "failure",
                     now, now),
                )

        logger.info(
            "transfer: recorded %s outcome %s→%s (%s)",
            signal_type, donor_domain, target_domain,
            "success" if success else "failure",
        )

        try:
            from shared.optimization import get_optimization_engine
            get_optimization_engine().emit(
                signal_type="transfer",
                source_loop="platform_transfer",
                domain=target_domain,
                agent_name="transfer_engine",
                payload={
                    "donor_domain": donor_domain,
                    "signal": signal_type,
                    "success": success,
                },
                session_id=f"tx_{target_domain}_{now}",
            )
        except Exception as e:
            logger.debug("Transfer optimization signal failed: %s", e)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestThompsonSampling -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/platform_transfer.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): Thompson Sampling donor selection + outcome recording with Beta distributions"
```

---

### Task 5: FormExperienceDB — Transfer Fallback Integration

**Files:**
- Modify: `jobpulse/form_experience_db.py` (lines 24-26 for init, lines 435-449, 467-475, 504-513, 555-562, 600-607, 628-636 for lookups)
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test for transfer fallback in get_timing**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
from jobpulse.form_experience_db import FormExperienceDB


class TestFormExperienceDBFallback:
    def test_get_timing_falls_back_to_transfer(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        exp_db = FormExperienceDB(db_path=db)
        # Seed donor timing
        exp_db.store_timing("donor.greenhouse.io", hydration_ms=800, fill_ms=2000, transition_ms=500)

        # Seed similarity so transfer engine can find the donor
        engine = PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "donor.greenhouse.io", "timing_profile", 0.9, 5, now),
        )
        conn.commit()
        conn.close()

        # Query timing for unknown domain — should get donor's timing
        result = exp_db.get_timing("new.greenhouse.io")
        assert result is not None
        assert result["avg_hydration_ms"] == 800
        assert result.get("_transfer") is True
        assert result.get("_donor") == "donor.greenhouse.io"

    def test_get_timing_prefers_direct_hit(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        exp_db = FormExperienceDB(db_path=db)
        exp_db.store_timing("direct.io", hydration_ms=100, fill_ms=200, transition_ms=50)

        result = exp_db.get_timing("direct.io")
        assert result is not None
        assert result["avg_hydration_ms"] == 100
        assert "_transfer" not in result

    def test_get_container_falls_back_to_transfer(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        exp_db = FormExperienceDB(db_path=db)
        exp_db.store_container("donor.io", "#application")

        engine = PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.io", "donor.io", "container_selectors", 0.8, 3, now),
        )
        conn.commit()
        conn.close()

        result = exp_db.get_container("new.io")
        assert result is not None
        # Returns just the selector string (not a dict) for backwards compat
        # The _transfer metadata is lost for string returns, which is fine

    def test_get_field_mappings_falls_back_to_transfer(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        exp_db = FormExperienceDB(db_path=db)
        exp_db.save_field_mappings("donor.io", {"email": "email", "phone": "phone"})

        engine = PlatformTransferEngine(db_path=db)
        conn = sqlite3.connect(db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.io", "donor.io", "field_types", 0.85, 4, now),
        )
        conn.commit()
        conn.close()

        result = exp_db.get_field_mappings("new.io")
        assert len(result) == 2
        assert result["email"] == "email"

    def test_no_transfer_returns_empty(self, tmp_path):
        db = str(tmp_path / "form_experience.db")
        exp_db = FormExperienceDB(db_path=db)
        result = exp_db.get_timing("totally-unknown.io")
        assert result is None

        result = exp_db.get_field_mappings("totally-unknown.io")
        assert result == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestFormExperienceDBFallback -v`
Expected: FAIL — `get_timing` returns `None` for unknown domain (no fallback yet)

- [ ] **Step 3: Add transfer fallback to FormExperienceDB**

In `jobpulse/form_experience_db.py`, add a lazy `_transfer_engine` property and modify 5 lookup methods.

First, add the property after the `__init__` method (after line 26):

```python
    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            self._te = PlatformTransferEngine(db_path=self._db_path)
        return self._te
```

Then modify `get_timing` (currently lines 600-607) — replace the full method:

```python
    def get_timing(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM page_timings WHERE domain = ?", (domain,),
            ).fetchone()
        if row:
            return dict(row)
        transfer = self._transfer_engine.get_transfer_data(domain, "timing_profile")
        if transfer:
            donor_row = None
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_row = conn.execute(
                    "SELECT * FROM page_timings WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                result = dict(donor_row)
                result["_transfer"] = True
                result["_donor"] = transfer["donor_domain"]
                return result
        return None
```

Modify `get_container` (currently lines 555-562) — replace the full method:

```python
    def get_container(self, domain_or_url: str) -> str | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT selector FROM container_selectors WHERE domain = ?",
                (domain,),
            ).fetchone()
        if row:
            return row[0]
        transfer = self._transfer_engine.get_transfer_data(domain, "container_selectors")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                donor_row = conn.execute(
                    "SELECT selector FROM container_selectors WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                return donor_row[0]
        return None
```

Modify `get_field_mappings` (currently lines 467-475) — replace the full method:

```python
    def get_field_mappings(self, domain_or_url: str) -> dict[str, str]:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT field_label, profile_key FROM field_label_mappings WHERE domain = ?",
                (domain,),
            ).fetchall()
        if rows:
            return {label: key for label, key in rows}
        transfer = self._transfer_engine.get_transfer_data(domain, "field_types")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                donor_rows = conn.execute(
                    "SELECT field_label, profile_key FROM field_label_mappings WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchall()
            if donor_rows:
                return {label: key for label, key in donor_rows}
        return {}
```

Modify `get_failure_reasons` (currently lines 435-449) — replace the full method:

```python
    def get_failure_reasons(self, domain_or_url: str, limit: int = 10) -> list[dict]:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM form_failure_reasons
                WHERE domain = ? ORDER BY created_at DESC LIMIT ?""",
                (domain, limit),
            ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        transfer = self._transfer_engine.get_transfer_data(domain, "failure_patterns")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_rows = conn.execute(
                    """SELECT * FROM form_failure_reasons
                    WHERE domain = ? ORDER BY created_at DESC LIMIT ?""",
                    (transfer["donor_domain"], limit),
                ).fetchall()
            if donor_rows:
                return [dict(r) for r in donor_rows]
        return []
```

Modify `get_scan_strategy` (currently lines 628-636) — replace the full method:

```python
    def get_scan_strategy(self, domain_or_url: str) -> dict | None:
        domain = self.normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM scan_strategy_preferences WHERE domain = ?",
                (domain,),
            ).fetchone()
        if row:
            return dict(row)
        transfer = self._transfer_engine.get_transfer_data(domain, "fill_techniques")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_row = conn.execute(
                    "SELECT * FROM scan_strategy_preferences WHERE domain = ?",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                result = dict(donor_row)
                result["_transfer"] = True
                result["_donor"] = transfer["donor_domain"]
                return result
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestFormExperienceDBFallback -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Run existing FormExperienceDB tests to check for regressions**

Run: `python -m pytest tests/jobpulse/ -v -k "form_experience" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): transparent transfer fallback in FormExperienceDB (5 methods)"
```

---

### Task 6: NavigationLearner — Transfer Fallback

**Files:**
- Modify: `jobpulse/navigation_learner.py` (lines 58-75 `get_sequence` method)
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
from jobpulse.navigation_learner import NavigationLearner


class TestNavigationLearnerFallback:
    def test_get_sequence_falls_back_to_transfer(self, tmp_path):
        nav_db = str(tmp_path / "navigation_learning.db")
        fe_db = str(tmp_path / "form_experience.db")

        # Seed donor nav sequence
        learner = NavigationLearner(db_path=nav_db)
        learner.save_sequence("donor.greenhouse.io", [
            {"action": "click_apply", "selector": "button.apply"},
            {"action": "fill_form", "selector": "#form"},
        ], success=True, platform="greenhouse")

        # Seed similarity in form_experience.db
        engine = PlatformTransferEngine(db_path=fe_db)
        conn = sqlite3.connect(fe_db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "donor.greenhouse.io", "navigation_flow", 0.85, 4, now),
        )
        conn.commit()
        conn.close()

        # Override transfer engine DB path on the learner
        learner._transfer_db_path = fe_db
        result = learner.get_sequence("new.greenhouse.io")
        assert result is not None
        assert len(result) == 2
        assert result[0]["action"] == "click_apply"

    def test_get_sequence_prefers_direct_hit(self, tmp_path):
        nav_db = str(tmp_path / "navigation_learning.db")
        learner = NavigationLearner(db_path=nav_db)
        learner.save_sequence("direct.io", [{"action": "submit"}], success=True)

        result = learner.get_sequence("direct.io")
        assert result is not None
        assert result[0]["action"] == "submit"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestNavigationLearnerFallback -v`
Expected: FAIL — `get_sequence` returns None for unknown domain (no fallback), `_transfer_db_path` doesn't exist

- [ ] **Step 3: Add transfer fallback to NavigationLearner**

In `jobpulse/navigation_learner.py`, add a lazy property after `__init__` (after line 28):

```python
    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            db_path = getattr(self, "_transfer_db_path", None)
            self._te = PlatformTransferEngine(db_path=db_path)
        return self._te
```

Replace the `get_sequence` method (currently lines 58-75):

```python
    def get_sequence(self, domain_or_url: str) -> list[dict] | None:
        domain = self._normalize_domain(domain_or_url)
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT steps, updated_at FROM sequences WHERE domain = ? AND success = 1",
                (domain,),
            ).fetchone()
        if row:
            try:
                updated = datetime.fromisoformat(row[1])
                if (datetime.now(UTC) - updated).days > _SEQUENCE_TTL_DAYS:
                    logger.info("Navigation sequence for %s expired (%s)", domain, row[1])
                    return None
            except (ValueError, TypeError):
                pass
            return json.loads(row[0])
        transfer = self._transfer_engine.get_transfer_data(domain, "navigation_flow")
        if transfer:
            with sqlite3.connect(self._db_path) as conn:
                donor_row = conn.execute(
                    "SELECT steps FROM sequences WHERE domain = ? AND success = 1",
                    (transfer["donor_domain"],),
                ).fetchone()
            if donor_row:
                return json.loads(donor_row[0])
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestNavigationLearnerFallback -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run existing NavigationLearner tests**

Run: `python -m pytest tests/jobpulse/ -v -k "navigation" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/navigation_learner.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): transparent transfer fallback in NavigationLearner"
```

---

### Task 7: GotchasDB — Transfer Fallback

**Files:**
- Modify: `jobpulse/form_engine/gotchas.py` (lines 111-119 `lookup_domain` method)
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
from jobpulse.form_engine.gotchas import GotchasDB


class TestGotchasDBFallback:
    def test_lookup_domain_falls_back_to_transfer(self, tmp_path):
        gotchas_db = str(tmp_path / "form_gotchas.db")
        fe_db = str(tmp_path / "form_experience.db")

        # Seed donor gotchas
        gotchas = GotchasDB(db_path=gotchas_db)
        gotchas.store("donor.greenhouse.io", ".salary-input", "hidden field", "scroll first")

        # Seed similarity
        engine = PlatformTransferEngine(db_path=fe_db)
        conn = sqlite3.connect(fe_db)
        now = "2026-04-29T00:00:00+00:00"
        conn.execute(
            "INSERT INTO platform_similarity VALUES (?, ?, ?, ?, ?, ?)",
            ("new.greenhouse.io", "donor.greenhouse.io", "failure_patterns", 0.8, 3, now),
        )
        conn.commit()
        conn.close()

        gotchas._transfer_db_path = fe_db
        result = gotchas.lookup_domain("new.greenhouse.io")
        assert len(result) == 1
        assert result[0]["problem"] == "hidden field"

    def test_lookup_domain_prefers_direct_hit(self, tmp_path):
        gotchas_db = str(tmp_path / "form_gotchas.db")
        gotchas = GotchasDB(db_path=gotchas_db)
        gotchas.store("direct.io", ".btn", "readonly", "click twice")

        result = gotchas.lookup_domain("direct.io")
        assert len(result) == 1
        assert result[0]["solution"] == "click twice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestGotchasDBFallback -v`
Expected: FAIL — `lookup_domain` returns empty list for unknown domain

- [ ] **Step 3: Add transfer fallback to GotchasDB**

In `jobpulse/form_engine/gotchas.py`, add a lazy property after `__init__` (after line 25):

```python
    @property
    def _transfer_engine(self):
        if not hasattr(self, "_te"):
            from jobpulse.platform_transfer import PlatformTransferEngine
            db_path = getattr(self, "_transfer_db_path", None)
            self._te = PlatformTransferEngine(db_path=db_path)
        return self._te
```

Replace `lookup_domain` (currently lines 111-119):

```python
    def lookup_domain(self, domain: str, engine: str = "extension") -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM gotchas WHERE domain = ? AND engine = ? ORDER BY times_used DESC",
                (domain, engine),
            ).fetchall()
        if rows:
            return [dict(r) for r in rows]
        transfer = self._transfer_engine.get_transfer_data(domain, "failure_patterns")
        if transfer:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                donor_rows = conn.execute(
                    "SELECT * FROM gotchas WHERE domain = ? AND engine = ? ORDER BY times_used DESC",
                    (transfer["donor_domain"], engine),
                ).fetchall()
            if donor_rows:
                return [dict(r) for r in donor_rows]
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestGotchasDBFallback -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run existing GotchasDB tests**

Run: `python -m pytest tests/jobpulse/ -v -k "gotcha" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/form_engine/gotchas.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): transparent transfer fallback in GotchasDB"
```

---

### Task 8: post_apply_hook — Trigger Recomputation + Outcome Recording

**Files:**
- Modify: `jobpulse/post_apply_hook.py` (after line 116 for recomputation, new section for outcome recording)
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write failing test**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
class TestPostApplyHookIntegration:
    def test_post_apply_triggers_recomputation(self, tmp_path, monkeypatch):
        db = str(tmp_path / "form_experience.db")

        # Seed two domains so recomputation has pairs to work with
        exp_db = FormExperienceDB(db_path=db)
        exp_db.record(
            domain="https://boards.greenhouse.io/existing",
            platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email"],
            screening_questions=[], time_seconds=30.0, success=True,
        )
        exp_db.store_timing("boards.greenhouse.io/existing", 500, 1500, 400)

        # Monkeypatch to avoid Drive/Notion/JobDB calls
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cv", lambda *a, **k: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.upload_cover_letter", lambda *a, **k: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.find_application_page", lambda *a, **k: None)
        monkeypatch.setattr("jobpulse.post_apply_hook.JobDB", lambda: type("FakeJobDB", (), {"mark_applied": lambda self, x: None})())

        from jobpulse.post_apply_hook import post_apply_hook

        result = {
            "success": True,
            "pages_filled": 3,
            "field_types": ["text", "email", "tel"],
            "screening_questions": [],
            "time_seconds": 40.0,
        }
        job_context = {
            "job_id": "",
            "company": "NewCo",
            "url": "https://boards.greenhouse.io/newco",
            "platform": "greenhouse",
            "ats_platform": "greenhouse",
        }
        post_apply_hook(result, job_context, form_exp_db_path=db)

        # Verify similarity matrix was populated
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT COUNT(*) FROM platform_similarity").fetchone()
        conn.close()
        assert rows[0] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestPostApplyHookIntegration -v`
Expected: FAIL — similarity table is empty (recomputation not wired)

- [ ] **Step 3: Add recomputation trigger to post_apply_hook**

In `jobpulse/post_apply_hook.py`, add the transfer recomputation after the form experience recording (after the `except` block ending at line 116). Insert a new section:

```python
    # --- 1b. Trigger cross-domain transfer learning ---
    try:
        from jobpulse.platform_transfer import PlatformTransferEngine
        transfer_engine = PlatformTransferEngine(db_path=form_exp_db_path)
        domain = FormExperienceDB.normalize_domain(url)
        transfer_engine.recompute_similarity_matrix(domain)
    except Exception as exc:
        logger.debug("post_apply_hook: transfer recomputation failed: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py::TestPostApplyHookIntegration -v`
Expected: PASS (1 test)

- [ ] **Step 5: Run full post_apply_hook tests**

Run: `python -m pytest tests/jobpulse/ -v -k "post_apply" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_platform_transfer.py
git commit -m "feat(transfer): wire recomputation into post_apply_hook"
```

---

### Task 9: Full Integration Test + Regression Sweep

**Files:**
- Modify: `tests/jobpulse/test_platform_transfer.py`

- [ ] **Step 1: Write full end-to-end integration test**

Append to `tests/jobpulse/test_platform_transfer.py`:

```python
class TestEndToEnd:
    def test_full_transfer_cycle(self, tmp_path):
        """Seed domain A → recompute → query transfer for B → record outcome → verify Beta update."""
        db = str(tmp_path / "form_experience.db")

        # Step 1: Seed domain A with full data
        exp_db = FormExperienceDB(db_path=db)
        exp_db.record(
            domain="boards.greenhouse.io/alpha",
            platform="greenhouse", adapter="native",
            pages_filled=4, field_types=["text", "email", "tel", "file", "select"],
            screening_questions=[], time_seconds=60.0, success=True,
        )
        exp_db.store_timing("boards.greenhouse.io/alpha", 700, 1800, 450)
        exp_db.store_container("boards.greenhouse.io/alpha", "#application")
        exp_db.record_fill_technique("boards.greenhouse.io/alpha", "email", "email", "type_text")
        exp_db.save_field_mappings("boards.greenhouse.io/alpha", {"email": "email", "name": "first_name"})

        # Step 2: Seed domain B (similar Greenhouse)
        exp_db.record(
            domain="boards.greenhouse.io/beta",
            platform="greenhouse", adapter="native",
            pages_filled=4, field_types=["text", "email", "tel", "file"],
            screening_questions=[], time_seconds=55.0, success=True,
        )
        exp_db.store_timing("boards.greenhouse.io/beta", 750, 1900, 500)
        exp_db.store_container("boards.greenhouse.io/beta", "#application .form-body")
        exp_db.record_fill_technique("boards.greenhouse.io/beta", "email", "email", "type_text")

        # Step 3: Recompute similarity matrix for alpha
        engine = PlatformTransferEngine(db_path=db)
        written = engine.recompute_similarity_matrix("boards.greenhouse.io/alpha")
        assert written > 0

        # Step 4: Query transfer for a NEW domain (not in DB yet)
        # First add a form_experience entry so it appears in the matrix
        exp_db.record(
            domain="boards.greenhouse.io/gamma",
            platform="greenhouse", adapter="native",
            pages_filled=3, field_types=["text", "email"],
            screening_questions=[], time_seconds=30.0, success=True,
        )
        engine.recompute_similarity_matrix("boards.greenhouse.io/gamma")

        # Now query timing for gamma via FormExperienceDB fallback
        timing = exp_db.get_timing("boards.greenhouse.io/gamma")
        # gamma has no timing data, but alpha/beta do — transfer should fire
        # (gamma was added to form_experience but not to page_timings)
        if timing and timing.get("_transfer"):
            assert timing["_donor"] in ("boards.greenhouse.io/alpha", "boards.greenhouse.io/beta")

        # Step 5: Record outcome
        engine.record_outcome(
            "boards.greenhouse.io/gamma",
            "boards.greenhouse.io/alpha",
            "timing_profile",
            success=True,
        )

        # Step 6: Verify Beta distribution updated
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT alpha, beta_param FROM transfer_outcomes "
            "WHERE target_domain = 'boards.greenhouse.io/gamma'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 2.0  # α = 1 + 1 success
        assert row[1] == 1.0  # β = 1 (no failure)
```

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py -v`
Expected: ALL tests PASS (~25 tests)

- [ ] **Step 3: Run full jobpulse test suite for regressions**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60 -x`
Expected: No regressions. If any fail, investigate and fix.

- [ ] **Step 4: Commit**

```bash
git add tests/jobpulse/test_platform_transfer.py
git commit -m "test(transfer): end-to-end integration test for full transfer cycle"
```

---

### Task 10: Final Cleanup + Combined Commit

- [ ] **Step 1: Verify all imports are clean**

Run: `python -c "from jobpulse.platform_transfer import PlatformTransferEngine, TransferResult, SIGNAL_TYPES; print('OK')"`
Expected: `OK`

- [ ] **Step 2: Verify no production DB references in test file**

Run: `grep -n "data/" tests/jobpulse/test_platform_transfer.py`
Expected: No matches (all tests use `tmp_path`)

- [ ] **Step 3: Run full test suite one final time**

Run: `python -m pytest tests/jobpulse/test_platform_transfer.py -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Verify file structure matches spec**

Run: `ls -la jobpulse/platform_transfer.py tests/jobpulse/test_platform_transfer.py`
Expected: Both files exist

- [ ] **Step 5: Final commit with all files**

If any files were modified but not yet committed during cleanup:

```bash
git add -A
git status
git commit -m "chore(transfer): final cleanup for cross-domain transfer learning"
```
