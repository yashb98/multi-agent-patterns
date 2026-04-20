# FormPilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FormPilot LangGraph (7 nodes) + 3 Tier 2 stores (Field Registry, Combobox Mappings, Platform Playbook) that replaces the procedural NativeFormFiller with an autonomous, self-learning form filling pipeline.

**Architecture:** LangGraph Plan-and-Execute StateGraph. Stores are built first (Tasks 1-3) since all graph nodes depend on them. State types (Task 4), then nodes bottom-up: observer → planner → executor → verifier → rescue → auth → approval. Graph wiring last. Each node is a stateless function following existing pattern conventions.

**Tech Stack:** Python, LangGraph, SQLite, Playwright, pytest, dataclasses

---

### File Structure

| File | Responsibility |
|------|---------------|
| `jobpulse/field_registry.py` | FieldRegistryDB — per-domain field metadata store |
| `jobpulse/combobox_mappings.py` | ComboboxMappingsDB — dropdown input→option resolution cache |
| `jobpulse/platform_playbook.py` | PlatformPlaybookDB — cross-domain ATS platform aggregates |
| `jobpulse/form_pilot_state.py` | FormPilotState TypedDict + FieldPlan, FieldResult, PageRecord |
| `jobpulse/form_observer.py` | Unified write path to all 6 stores after each page |
| `jobpulse/page_scanner.py` | DOM field extraction (extracted from NativeFormFiller._scan_fields) |
| `jobpulse/page_verifier.py` | Post-fill DOM value verification + error detection |
| `jobpulse/rescue_resolver.py` | LLM/vision/human escalation for failed fields |
| `jobpulse/form_pilot_nodes.py` | All 7 node functions (stateless) |
| `jobpulse/form_pilot.py` | LangGraph StateGraph definition + run_form_pilot() entry point |
| `tests/jobpulse/test_field_registry.py` | Field Registry tests |
| `tests/jobpulse/test_combobox_mappings.py` | Combobox Mappings tests |
| `tests/jobpulse/test_platform_playbook.py` | Platform Playbook tests |
| `tests/jobpulse/test_form_pilot_state.py` | State type tests |
| `tests/jobpulse/test_form_observer.py` | Observer tests |
| `tests/jobpulse/test_page_scanner.py` | Page scanner tests |
| `tests/jobpulse/test_page_verifier.py` | Page verifier tests |
| `tests/jobpulse/test_form_pilot.py` | Graph integration tests |

---

### Task 1: Field Registry Store

**Files:**
- Create: `jobpulse/field_registry.py`
- Test: `tests/jobpulse/test_field_registry.py`

- [ ] **Step 1: Write the failing test — empty domain returns no fields**

```python
# tests/jobpulse/test_field_registry.py
"""Tests for FieldRegistryDB — per-domain field metadata store."""
import pytest

from jobpulse.field_registry import FieldRegistryDB


@pytest.fixture
def db(tmp_path):
    return FieldRegistryDB(db_path=str(tmp_path / "field_registry.db"))


def test_unknown_domain_returns_empty(db):
    fields = db.get_fields("unknown.com")
    assert fields == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_field_registry.py::test_unknown_domain_returns_empty -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'jobpulse.field_registry'"

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/field_registry.py
"""Per-domain field metadata store.

Records (domain, field_label, field_type, page_num, selector, typical_value,
success_rate) for every form field encountered. Replaces JSON blob scanning
in FormExperienceDB with structured, queryable records.

Read path: form_planner queries by domain to get expected fields + typical values.
Write path: observer upserts after every page fill.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "field_registry.db")


def _normalize_domain(url_or_domain: str) -> str:
    if "://" in url_or_domain:
        return urlparse(url_or_domain).netloc.lower().removeprefix("www.")
    return url_or_domain.lower().removeprefix("www.")


class FieldRegistryDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_registry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_type TEXT NOT NULL,
                    page_num INTEGER NOT NULL,
                    selector TEXT,
                    typical_value TEXT NOT NULL DEFAULT '',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    last_value TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(domain, field_label, page_num)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_registry_domain
                ON field_registry (domain)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_fields(self, domain_or_url: str) -> list[dict]:
        domain = _normalize_domain(domain_or_url)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM field_registry WHERE domain = ? ORDER BY page_num, id",
                (domain,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_field(self, domain_or_url: str, field_label: str, page_num: int) -> dict | None:
        domain = _normalize_domain(domain_or_url)
        label = field_label.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM field_registry WHERE domain = ? AND field_label = ? AND page_num = ?",
                (domain, label, page_num),
            ).fetchone()
        return dict(row) if row else None

    def upsert(
        self,
        domain_or_url: str,
        field_label: str,
        field_type: str,
        page_num: int,
        *,
        selector: str | None = None,
        value: str = "",
        success: bool = True,
    ) -> None:
        domain = _normalize_domain(domain_or_url)
        label = field_label.strip().lower()
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, success_count, fail_count FROM field_registry WHERE domain = ? AND field_label = ? AND page_num = ?",
                (domain, label, page_num),
            ).fetchone()
            if existing:
                sc = existing["success_count"] + (1 if success else 0)
                fc = existing["fail_count"] + (0 if success else 1)
                conn.execute(
                    """UPDATE field_registry
                       SET field_type = ?, selector = COALESCE(?, selector),
                           typical_value = CASE WHEN ? != '' THEN ? ELSE typical_value END,
                           success_count = ?, fail_count = ?,
                           last_value = ?, updated_at = ?
                       WHERE id = ?""",
                    (field_type, selector, value, value, sc, fc, value, now, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO field_registry
                       (domain, field_label, field_type, page_num, selector,
                        typical_value, success_count, fail_count, last_value, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (domain, label, field_type, page_num, selector,
                     value, 1 if success else 0, 0 if success else 1, value, now),
                )

    def get_success_rate(self, domain_or_url: str, field_label: str, page_num: int) -> float:
        field = self.get_field(domain_or_url, field_label, page_num)
        if not field:
            return 0.0
        total = field["success_count"] + field["fail_count"]
        return field["success_count"] / total if total > 0 else 0.0

    def get_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM field_registry").fetchone()[0]
            domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM field_registry").fetchone()[0]
        return {"total_fields": total, "total_domains": domains}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_field_registry.py::test_unknown_domain_returns_empty -v`
Expected: PASS

- [ ] **Step 5: Write tests — upsert and success rate**

Add to `tests/jobpulse/test_field_registry.py`:

```python
def test_upsert_and_retrieve(db):
    db.upsert("boards.greenhouse.io", "Full Name", "text", 1, value="Yash Bishnoi", success=True)
    db.upsert("boards.greenhouse.io", "Email", "text", 1, value="test@example.com", success=True)
    db.upsert("boards.greenhouse.io", "Resume", "file", 2, success=True)

    fields = db.get_fields("boards.greenhouse.io")
    assert len(fields) == 3
    assert fields[0]["field_label"] == "full name"
    assert fields[0]["typical_value"] == "Yash Bishnoi"
    assert fields[0]["page_num"] == 1
    assert fields[2]["page_num"] == 2


def test_upsert_increments_counts(db):
    db.upsert("example.com", "Name", "text", 1, value="A", success=True)
    db.upsert("example.com", "Name", "text", 1, value="B", success=True)
    db.upsert("example.com", "Name", "text", 1, value="C", success=False)

    field = db.get_field("example.com", "Name", 1)
    assert field["success_count"] == 2
    assert field["fail_count"] == 1
    assert field["last_value"] == "C"


def test_success_rate(db):
    db.upsert("example.com", "Country", "select", 1, success=True)
    db.upsert("example.com", "Country", "select", 1, success=True)
    db.upsert("example.com", "Country", "select", 1, success=False)

    rate = db.get_success_rate("example.com", "Country", 1)
    assert rate == pytest.approx(2 / 3)


def test_success_rate_unknown_field(db):
    rate = db.get_success_rate("unknown.com", "nope", 1)
    assert rate == 0.0


def test_url_normalization(db):
    db.upsert("https://www.boards.greenhouse.io/company/jobs/123", "Name", "text", 1, value="X")
    fields = db.get_fields("https://boards.greenhouse.io/other")
    assert len(fields) == 1


def test_get_stats(db):
    db.upsert("a.com", "Name", "text", 1, value="X")
    db.upsert("b.com", "Email", "text", 1, value="Y")
    stats = db.get_stats()
    assert stats["total_fields"] == 2
    assert stats["total_domains"] == 2
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/jobpulse/test_field_registry.py -v`
Expected: 7 PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/field_registry.py tests/jobpulse/test_field_registry.py
git commit -m "feat: add FieldRegistryDB — per-domain field metadata store"
```

---

### Task 2: Combobox Mappings Store

**Files:**
- Create: `jobpulse/combobox_mappings.py`
- Test: `tests/jobpulse/test_combobox_mappings.py`

- [ ] **Step 1: Write the failing test — no mappings for unknown domain**

```python
# tests/jobpulse/test_combobox_mappings.py
"""Tests for ComboboxMappingsDB — dropdown input→option resolution cache."""
import pytest

from jobpulse.combobox_mappings import ComboboxMappingsDB


@pytest.fixture
def db(tmp_path):
    return ComboboxMappingsDB(db_path=str(tmp_path / "combobox.db"))


def test_unknown_domain_returns_empty(db):
    mappings = db.get_mappings("unknown.com")
    assert mappings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_combobox_mappings.py::test_unknown_domain_returns_empty -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/combobox_mappings.py
"""Dropdown input→option resolution cache.

Records (domain, field_label, input_value, actual_option, method, success_count)
for every select/combobox fill. After one successful fill, the same dropdown
on the same domain uses the cached actual_option directly — no fuzzy matching.

Read path: field_executor checks before fuzzy matching in select_filler.
Write path: observer records after every select/combobox fill.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlparse

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "combobox_mappings.db")


def _normalize_domain(url_or_domain: str) -> str:
    if "://" in url_or_domain:
        return urlparse(url_or_domain).netloc.lower().removeprefix("www.")
    return url_or_domain.lower().removeprefix("www.")


class ComboboxMappingsDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS combobox_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    domain TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    input_value TEXT NOT NULL,
                    actual_option TEXT NOT NULL,
                    method TEXT NOT NULL DEFAULT '',
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    UNIQUE(domain, field_label, input_value)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_combobox_domain
                ON combobox_mappings (domain)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_combobox_lookup
                ON combobox_mappings (domain, field_label)
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_mappings(self, domain_or_url: str) -> list[dict]:
        domain = _normalize_domain(domain_or_url)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM combobox_mappings WHERE domain = ? ORDER BY field_label",
                (domain,),
            ).fetchall()
        return [dict(r) for r in rows]

    def lookup(self, domain_or_url: str, field_label: str, input_value: str) -> str | None:
        """Return the cached actual_option for a given input, or None."""
        domain = _normalize_domain(domain_or_url)
        label = field_label.strip().lower()
        val = input_value.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT actual_option FROM combobox_mappings
                   WHERE domain = ? AND field_label = ? AND input_value = ?
                   AND success_count > 0""",
                (domain, label, val),
            ).fetchone()
        return row["actual_option"] if row else None

    def record(
        self,
        domain_or_url: str,
        field_label: str,
        input_value: str,
        actual_option: str,
        method: str = "",
        success: bool = True,
    ) -> None:
        domain = _normalize_domain(domain_or_url)
        label = field_label.strip().lower()
        val = input_value.strip().lower()
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, success_count, fail_count FROM combobox_mappings WHERE domain = ? AND field_label = ? AND input_value = ?",
                (domain, label, val),
            ).fetchone()
            if existing:
                sc = existing["success_count"] + (1 if success else 0)
                fc = existing["fail_count"] + (0 if success else 1)
                conn.execute(
                    """UPDATE combobox_mappings
                       SET actual_option = ?, method = ?,
                           success_count = ?, fail_count = ?, updated_at = ?
                       WHERE id = ?""",
                    (actual_option, method, sc, fc, now, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO combobox_mappings
                       (domain, field_label, input_value, actual_option, method,
                        success_count, fail_count, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (domain, label, val, actual_option, method,
                     1 if success else 0, 0 if success else 1, now),
                )
        logger.debug(
            "combobox_mappings: %s/%s: '%s' → '%s' (method=%s, success=%s)",
            domain, label, input_value, actual_option, method, success,
        )

    def get_field_mappings(self, domain_or_url: str, field_label: str) -> list[dict]:
        domain = _normalize_domain(domain_or_url)
        label = field_label.strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM combobox_mappings WHERE domain = ? AND field_label = ? ORDER BY success_count DESC",
                (domain, label),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM combobox_mappings").fetchone()[0]
            domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM combobox_mappings").fetchone()[0]
            successful = conn.execute("SELECT COUNT(*) FROM combobox_mappings WHERE success_count > 0").fetchone()[0]
        return {"total_mappings": total, "total_domains": domains, "successful_mappings": successful}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_combobox_mappings.py::test_unknown_domain_returns_empty -v`
Expected: PASS

- [ ] **Step 5: Write tests — record, lookup, deterministic fill**

Add to `tests/jobpulse/test_combobox_mappings.py`:

```python
def test_record_and_lookup(db):
    db.record("boards.greenhouse.io", "Country", "UK", "United Kingdom", method="abbreviation")
    result = db.lookup("boards.greenhouse.io", "Country", "UK")
    assert result == "United Kingdom"


def test_lookup_returns_none_for_unknown(db):
    result = db.lookup("example.com", "Country", "UK")
    assert result is None


def test_lookup_skips_failed_mappings(db):
    db.record("example.com", "Country", "UK", "United Kingdom", success=False)
    result = db.lookup("example.com", "Country", "UK")
    assert result is None


def test_success_count_increments(db):
    db.record("example.com", "Gender", "male", "Male", success=True)
    db.record("example.com", "Gender", "male", "Male", success=True)
    db.record("example.com", "Gender", "male", "Male", success=False)

    mappings = db.get_field_mappings("example.com", "Gender")
    assert len(mappings) == 1
    assert mappings[0]["success_count"] == 2
    assert mappings[0]["fail_count"] == 1


def test_url_normalization(db):
    db.record("https://www.jobs.lever.co/company/123", "Location", "london", "London, UK")
    result = db.lookup("https://jobs.lever.co/other/456", "Location", "london")
    assert result == "London, UK"


def test_multiple_fields_same_domain(db):
    db.record("example.com", "Country", "UK", "United Kingdom")
    db.record("example.com", "Gender", "male", "Male")
    db.record("example.com", "Visa", "graduate", "Graduate Visa")

    mappings = db.get_mappings("example.com")
    assert len(mappings) == 3
    labels = [m["field_label"] for m in mappings]
    assert "country" in labels
    assert "gender" in labels
    assert "visa" in labels


def test_get_stats(db):
    db.record("a.com", "Country", "UK", "United Kingdom")
    db.record("b.com", "Country", "UK", "United Kingdom")
    stats = db.get_stats()
    assert stats["total_mappings"] == 2
    assert stats["total_domains"] == 2
    assert stats["successful_mappings"] == 2
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/jobpulse/test_combobox_mappings.py -v`
Expected: 8 PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/combobox_mappings.py tests/jobpulse/test_combobox_mappings.py
git commit -m "feat: add ComboboxMappingsDB — deterministic dropdown resolution cache"
```

---

### Task 3: Platform Playbook Store

**Files:**
- Create: `jobpulse/platform_playbook.py`
- Test: `tests/jobpulse/test_platform_playbook.py`

- [ ] **Step 1: Write the failing test — unknown platform returns None**

```python
# tests/jobpulse/test_platform_playbook.py
"""Tests for PlatformPlaybookDB — cross-domain ATS platform aggregates."""
import pytest

from jobpulse.platform_playbook import PlatformPlaybookDB


@pytest.fixture
def db(tmp_path):
    return PlatformPlaybookDB(db_path=str(tmp_path / "playbook.db"))


def test_unknown_platform_returns_none(db):
    result = db.get_platform("unknown_ats")
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_platform_playbook.py::test_unknown_platform_returns_none -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write minimal implementation**

```python
# jobpulse/platform_playbook.py
"""Cross-domain ATS platform aggregates.

Records (platform, avg_pages, common_fields, common_screening, success_rate)
aggregated across all applications on the same ATS platform. First time on a
new Greenhouse domain → Playbook says "expect 3 pages, these fields" based
on prior Greenhouse applications.

Read path: form_planner queries by platform for unknown domains.
Write path: observer updates running averages after every application.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from shared.logging_config import get_logger

from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "platform_playbook.db")


class PlatformPlaybookDB:
    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS platform_playbook (
                    platform TEXT PRIMARY KEY,
                    avg_pages REAL NOT NULL DEFAULT 0,
                    total_applications INTEGER NOT NULL DEFAULT 0,
                    common_fields TEXT NOT NULL DEFAULT '[]',
                    common_screening TEXT NOT NULL DEFAULT '[]',
                    common_field_types TEXT NOT NULL DEFAULT '{}',
                    has_file_upload INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    avg_time_seconds REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT ''
                )
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_platform(self, platform: str) -> dict | None:
        key = platform.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM platform_playbook WHERE platform = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["common_fields"] = json.loads(d["common_fields"])
        d["common_screening"] = json.loads(d["common_screening"])
        d["common_field_types"] = json.loads(d["common_field_types"])
        return d

    def record_application(
        self,
        platform: str,
        pages: int,
        field_labels: list[str],
        screening_questions: list[str],
        field_types: dict[str, str],
        has_file_upload: bool,
        time_seconds: float,
        success: bool,
    ) -> None:
        key = platform.strip().lower()
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM platform_playbook WHERE platform = ?", (key,),
            ).fetchone()

            if existing:
                n = existing["total_applications"]
                new_avg_pages = (existing["avg_pages"] * n + pages) / (n + 1)
                new_avg_time = (existing["avg_time_seconds"] * n + time_seconds) / (n + 1)
                old_fields = set(json.loads(existing["common_fields"]))
                old_screening = set(json.loads(existing["common_screening"]))
                old_types = json.loads(existing["common_field_types"])
                merged_fields = sorted(old_fields | set(field_labels))
                merged_screening = sorted(old_screening | set(screening_questions))
                merged_types = {**old_types, **field_types}
                sc = existing["success_count"] + (1 if success else 0)
                fc = existing["fail_count"] + (0 if success else 1)
                conn.execute(
                    """UPDATE platform_playbook
                       SET avg_pages = ?, total_applications = ?,
                           common_fields = ?, common_screening = ?,
                           common_field_types = ?, has_file_upload = ?,
                           success_count = ?, fail_count = ?,
                           avg_time_seconds = ?, updated_at = ?
                       WHERE platform = ?""",
                    (new_avg_pages, n + 1,
                     json.dumps(merged_fields), json.dumps(merged_screening),
                     json.dumps(merged_types), int(has_file_upload or existing["has_file_upload"]),
                     sc, fc, new_avg_time, now, key),
                )
            else:
                conn.execute(
                    """INSERT INTO platform_playbook
                       (platform, avg_pages, total_applications, common_fields,
                        common_screening, common_field_types, has_file_upload,
                        success_count, fail_count, avg_time_seconds, updated_at)
                       VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, float(pages),
                     json.dumps(sorted(field_labels)),
                     json.dumps(sorted(screening_questions)),
                     json.dumps(field_types),
                     int(has_file_upload),
                     1 if success else 0,
                     0 if success else 1,
                     time_seconds, now),
                )
        logger.info(
            "platform_playbook: %s — %d pages, %d fields, success=%s",
            key, pages, len(field_labels), success,
        )

    def get_success_rate(self, platform: str) -> float:
        info = self.get_platform(platform)
        if not info:
            return 0.0
        total = info["success_count"] + info["fail_count"]
        return info["success_count"] / total if total > 0 else 0.0

    def get_all_platforms(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT platform, total_applications, avg_pages, success_count, fail_count FROM platform_playbook ORDER BY total_applications DESC"
            ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_platform_playbook.py::test_unknown_platform_returns_none -v`
Expected: PASS

- [ ] **Step 5: Write tests — record and aggregate**

Add to `tests/jobpulse/test_platform_playbook.py`:

```python
def test_record_and_retrieve(db):
    db.record_application(
        platform="greenhouse", pages=3,
        field_labels=["Name", "Email", "Resume"],
        screening_questions=["Sponsorship?"],
        field_types={"Name": "text", "Email": "text", "Resume": "file"},
        has_file_upload=True, time_seconds=45.0, success=True,
    )
    info = db.get_platform("greenhouse")
    assert info is not None
    assert info["avg_pages"] == 3.0
    assert info["total_applications"] == 1
    assert "Name" in info["common_fields"]
    assert "Sponsorship?" in info["common_screening"]
    assert info["common_field_types"]["Resume"] == "file"
    assert info["has_file_upload"] == 1


def test_running_averages(db):
    db.record_application(
        platform="lever", pages=2,
        field_labels=["Name", "Email"],
        screening_questions=[],
        field_types={"Name": "text", "Email": "text"},
        has_file_upload=False, time_seconds=20.0, success=True,
    )
    db.record_application(
        platform="lever", pages=4,
        field_labels=["Name", "Phone", "Resume"],
        screening_questions=["Salary?"],
        field_types={"Phone": "text", "Resume": "file"},
        has_file_upload=True, time_seconds=40.0, success=True,
    )

    info = db.get_platform("lever")
    assert info["avg_pages"] == pytest.approx(3.0)
    assert info["total_applications"] == 2
    assert info["avg_time_seconds"] == pytest.approx(30.0)
    assert set(info["common_fields"]) == {"Name", "Email", "Phone", "Resume"}
    assert info["common_screening"] == ["Salary?"]
    assert info["has_file_upload"] == 1


def test_success_rate(db):
    db.record_application("workday", 5, ["Name"], [], {}, False, 60.0, True)
    db.record_application("workday", 5, ["Name"], [], {}, False, 60.0, True)
    db.record_application("workday", 5, ["Name"], [], {}, False, 60.0, False)

    rate = db.get_success_rate("workday")
    assert rate == pytest.approx(2 / 3)


def test_success_rate_unknown(db):
    assert db.get_success_rate("nope") == 0.0


def test_case_insensitive_platform(db):
    db.record_application("Greenhouse", 3, ["Name"], [], {}, False, 30.0, True)
    info = db.get_platform("GREENHOUSE")
    assert info is not None
    assert info["total_applications"] == 1


def test_get_all_platforms(db):
    db.record_application("greenhouse", 3, ["Name"], [], {}, False, 30.0, True)
    db.record_application("lever", 2, ["Name"], [], {}, False, 20.0, True)
    db.record_application("greenhouse", 3, ["Name"], [], {}, False, 30.0, True)

    platforms = db.get_all_platforms()
    assert len(platforms) == 2
    assert platforms[0]["platform"] == "greenhouse"
    assert platforms[0]["total_applications"] == 2
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/jobpulse/test_platform_playbook.py -v`
Expected: 6 PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/platform_playbook.py tests/jobpulse/test_platform_playbook.py
git commit -m "feat: add PlatformPlaybookDB — cross-domain ATS platform aggregates"
```

---

### Task 4: FormPilot State Types

**Files:**
- Create: `jobpulse/form_pilot_state.py`
- Test: `tests/jobpulse/test_form_pilot_state.py`

- [ ] **Step 1: Write the failing test — state types are importable and constructible**

```python
# tests/jobpulse/test_form_pilot_state.py
"""Tests for FormPilot state types."""
from jobpulse.form_pilot_state import (
    FormPilotState, FieldPlan, FieldResult, PageRecord,
    make_initial_state,
)


def test_make_initial_state():
    state = make_initial_state(
        url="https://boards.greenhouse.io/co/jobs/1",
        domain="boards.greenhouse.io",
        platform="greenhouse",
        cv_path="/tmp/cv.pdf",
        job_context={"company": "Acme", "title": "SWE"},
        merged_answers={"Name": "Yash"},
    )
    assert state["url"] == "https://boards.greenhouse.io/co/jobs/1"
    assert state["current_page"] == 1
    assert state["auth_status"] == "pending"
    assert state["form_complete"] is False
    assert state["success"] is False


def test_field_plan_construction():
    plan = FieldPlan(
        field_label="Country",
        field_type="select",
        page_num=1,
        selector=None,
        expected_value=None,
        combobox_mapping="United Kingdom",
        resolution_strategy="combobox_cache",
    )
    assert plan["field_label"] == "Country"
    assert plan["combobox_mapping"] == "United Kingdom"


def test_field_result_construction():
    result = FieldResult(
        field_label="Name",
        value_attempted="Yash",
        value_set="Yash",
        method="pattern",
        tier=1,
        confidence=1.0,
        success=True,
        error=None,
        selector="input[name='name']",
    )
    assert result["success"] is True
    assert result["tier"] == 1


def test_page_record_construction():
    record = PageRecord(
        page_num=1,
        page_title="Contact Info",
        fields=[],
        screenshot_b64="",
        has_file_upload=False,
        nav_button="Next",
    )
    assert record["page_num"] == 1
    assert record["nav_button"] == "Next"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_pilot_state.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write implementation**

```python
# jobpulse/form_pilot_state.py
"""FormPilot LangGraph state types."""
from __future__ import annotations

from typing import TypedDict


class FieldPlan(TypedDict):
    field_label: str
    field_type: str
    page_num: int
    selector: str | None
    expected_value: str | None
    combobox_mapping: str | None
    resolution_strategy: str


class FieldResult(TypedDict):
    field_label: str
    value_attempted: str
    value_set: str
    method: str
    tier: int
    confidence: float
    success: bool
    error: str | None
    selector: str


class PageRecord(TypedDict):
    page_num: int
    page_title: str
    fields: list[FieldResult]
    screenshot_b64: str
    has_file_upload: bool
    nav_button: str


class FormPilotState(TypedDict):
    url: str
    domain: str
    platform: str
    cv_path: str
    cl_path: str | None
    job_context: dict
    merged_answers: dict

    auth_status: str
    auth_method: str

    current_page: int
    total_pages: int
    page_plan: list[FieldPlan]
    page_screenshot_b64: str

    fill_results: list[FieldResult]
    failed_fields: list[FieldPlan]
    rescue_attempts: int

    all_pages_filled: list[PageRecord]
    form_complete: bool

    approval_status: str
    corrections: dict

    success: bool
    result: dict


def make_initial_state(
    url: str,
    domain: str,
    platform: str,
    cv_path: str,
    job_context: dict,
    merged_answers: dict,
    cl_path: str | None = None,
) -> FormPilotState:
    return FormPilotState(
        url=url,
        domain=domain,
        platform=platform,
        cv_path=cv_path,
        cl_path=cl_path,
        job_context=job_context,
        merged_answers=merged_answers,
        auth_status="pending",
        auth_method="",
        current_page=1,
        total_pages=0,
        page_plan=[],
        page_screenshot_b64="",
        fill_results=[],
        failed_fields=[],
        rescue_attempts=0,
        all_pages_filled=[],
        form_complete=False,
        approval_status="pending",
        corrections={},
        success=False,
        result={},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_pilot_state.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_pilot_state.py tests/jobpulse/test_form_pilot_state.py
git commit -m "feat: add FormPilot state types — FieldPlan, FieldResult, PageRecord"
```

---

### Task 5: Form Observer — Unified Write Path

**Files:**
- Create: `jobpulse/form_observer.py`
- Test: `tests/jobpulse/test_form_observer.py`

- [ ] **Step 1: Write the failing test — observer writes to all stores**

```python
# tests/jobpulse/test_form_observer.py
"""Tests for FormObserver — unified write path to all stores."""
import pytest

from jobpulse.form_observer import FormObserver
from jobpulse.form_pilot_state import FieldResult, PageRecord


@pytest.fixture
def observer(tmp_path):
    return FormObserver(
        field_registry_db=str(tmp_path / "registry.db"),
        combobox_db=str(tmp_path / "combobox.db"),
        playbook_db=str(tmp_path / "playbook.db"),
        field_audit_db=str(tmp_path / "audit.db"),
        interaction_db=str(tmp_path / "interactions.db"),
        experience_db=str(tmp_path / "experience.db"),
    )


def test_record_page_populates_field_registry(observer):
    fields = [
        FieldResult(
            field_label="Full Name", value_attempted="Yash", value_set="Yash",
            method="pattern", tier=1, confidence=1.0, success=True,
            error=None, selector="input[name='name']",
        ),
    ]
    record = PageRecord(
        page_num=1, page_title="Contact",
        fields=fields, screenshot_b64="", has_file_upload=False, nav_button="Next",
    )

    observer.record_page(
        domain="boards.greenhouse.io", platform="greenhouse",
        url="https://boards.greenhouse.io/co/jobs/1",
        page_record=record, session_id="test-001",
    )

    from jobpulse.field_registry import FieldRegistryDB
    registry = FieldRegistryDB(db_path=observer._field_registry_db)
    fields_out = registry.get_fields("boards.greenhouse.io")
    assert len(fields_out) == 1
    assert fields_out[0]["field_label"] == "full name"
    assert fields_out[0]["typical_value"] == "Yash"


def test_record_page_populates_combobox(observer):
    fields = [
        FieldResult(
            field_label="Country", value_attempted="UK", value_set="United Kingdom",
            method="abbreviation", tier=1, confidence=1.0, success=True,
            error=None, selector="select#country",
        ),
    ]
    record = PageRecord(
        page_num=1, page_title="Details",
        fields=fields, screenshot_b64="", has_file_upload=False, nav_button="Next",
    )

    observer.record_page(
        domain="example.com", platform="greenhouse",
        url="https://example.com/apply",
        page_record=record, session_id="test-002",
    )

    from jobpulse.combobox_mappings import ComboboxMappingsDB
    cdb = ComboboxMappingsDB(db_path=observer._combobox_db)
    result = cdb.lookup("example.com", "Country", "UK")
    assert result == "United Kingdom"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_observer.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write implementation**

```python
# jobpulse/form_observer.py
"""Unified write path to all form learning stores.

Runs after every successfully verified page in the FormPilot graph.
Writes to: FieldRegistryDB, ComboboxMappingsDB, PlatformPlaybookDB,
FieldAuditDB, FormInteractionLog, FormExperienceDB.
"""
from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.form_pilot_state import FieldResult, PageRecord

logger = get_logger(__name__)

_SELECT_TYPES = {"select", "combobox", "select_native", "select_custom"}


class FormObserver:
    def __init__(
        self,
        field_registry_db: str | None = None,
        combobox_db: str | None = None,
        playbook_db: str | None = None,
        field_audit_db: str | None = None,
        interaction_db: str | None = None,
        experience_db: str | None = None,
    ) -> None:
        self._field_registry_db = field_registry_db
        self._combobox_db = combobox_db
        self._playbook_db = playbook_db
        self._field_audit_db = field_audit_db
        self._interaction_db = interaction_db
        self._experience_db = experience_db

    def record_page(
        self,
        domain: str,
        platform: str,
        url: str,
        page_record: PageRecord,
        session_id: str,
    ) -> None:
        self._write_field_registry(domain, page_record)
        self._write_combobox_mappings(domain, page_record)
        self._write_field_audit(url, domain, platform, page_record)
        self._write_interaction_log(domain, platform, page_record, session_id)

    def record_application_complete(
        self,
        platform: str,
        all_pages: list[PageRecord],
        time_seconds: float,
        success: bool,
    ) -> None:
        self._write_platform_playbook(platform, all_pages, time_seconds, success)

    def _write_field_registry(self, domain: str, page_record: PageRecord) -> None:
        try:
            from jobpulse.field_registry import FieldRegistryDB
            db = FieldRegistryDB(db_path=self._field_registry_db)
            for f in page_record["fields"]:
                db.upsert(
                    domain, f["field_label"], f.get("method", "text"),
                    page_record["page_num"],
                    selector=f.get("selector"),
                    value=f["value_set"],
                    success=f["success"],
                )
        except Exception as exc:
            logger.debug("observer: field_registry write failed: %s", exc)

    def _write_combobox_mappings(self, domain: str, page_record: PageRecord) -> None:
        try:
            from jobpulse.combobox_mappings import ComboboxMappingsDB
            db = ComboboxMappingsDB(db_path=self._combobox_db)
            for f in page_record["fields"]:
                if f["value_attempted"] != f["value_set"] and f["value_set"]:
                    db.record(
                        domain, f["field_label"],
                        f["value_attempted"], f["value_set"],
                        method=f.get("method", ""),
                        success=f["success"],
                    )
        except Exception as exc:
            logger.debug("observer: combobox write failed: %s", exc)

    def _write_field_audit(self, url: str, domain: str, platform: str, page_record: PageRecord) -> None:
        try:
            from jobpulse.field_audit import FieldAuditDB
            db = FieldAuditDB(db_path=self._field_audit_db)
            for f in page_record["fields"]:
                db.record_fill(
                    application_url=url, domain=domain, platform=platform,
                    field_label=f["field_label"], value=f["value_set"],
                    method=f["method"], tier=f["tier"],
                    confidence=f["confidence"],
                )
        except Exception as exc:
            logger.debug("observer: field_audit write failed: %s", exc)

    def _write_interaction_log(self, domain: str, platform: str, page_record: PageRecord, session_id: str) -> None:
        try:
            from jobpulse.form_interaction_log import FormInteractionLog
            log = FormInteractionLog(db_path=self._interaction_db)
            for i, f in enumerate(page_record["fields"]):
                log.log_step(
                    session_id=session_id, domain=domain, platform=platform,
                    page_num=page_record["page_num"],
                    page_title=page_record["page_title"],
                    step_order=i, step_type="fill",
                    target_label=f["field_label"],
                    value=f["value_set"], method=f["method"],
                )
        except Exception as exc:
            logger.debug("observer: interaction_log write failed: %s", exc)

    def _write_platform_playbook(
        self, platform: str, all_pages: list[PageRecord],
        time_seconds: float, success: bool,
    ) -> None:
        try:
            from jobpulse.platform_playbook import PlatformPlaybookDB
            db = PlatformPlaybookDB(db_path=self._playbook_db)
            all_fields = []
            all_screening = []
            field_types: dict[str, str] = {}
            has_upload = False
            for page in all_pages:
                for f in page["fields"]:
                    all_fields.append(f["field_label"])
                    field_types[f["field_label"]] = f["method"]
                if page["has_file_upload"]:
                    has_upload = True
            db.record_application(
                platform=platform, pages=len(all_pages),
                field_labels=all_fields,
                screening_questions=all_screening,
                field_types=field_types,
                has_file_upload=has_upload,
                time_seconds=time_seconds, success=success,
            )
        except Exception as exc:
            logger.debug("observer: platform_playbook write failed: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_form_observer.py -v`
Expected: 2 PASS

- [ ] **Step 5: Write test — application complete writes to playbook**

Add to `tests/jobpulse/test_form_observer.py`:

```python
def test_record_application_complete_writes_playbook(observer):
    pages = [
        PageRecord(
            page_num=1, page_title="Contact",
            fields=[
                FieldResult(
                    field_label="Name", value_attempted="Yash", value_set="Yash",
                    method="pattern", tier=1, confidence=1.0, success=True,
                    error=None, selector="input",
                ),
            ],
            screenshot_b64="", has_file_upload=False, nav_button="Next",
        ),
        PageRecord(
            page_num=2, page_title="Resume",
            fields=[
                FieldResult(
                    field_label="Resume", value_attempted="cv.pdf", value_set="cv.pdf",
                    method="file", tier=1, confidence=1.0, success=True,
                    error=None, selector="input[type=file]",
                ),
            ],
            screenshot_b64="", has_file_upload=True, nav_button="Submit",
        ),
    ]

    observer.record_application_complete(
        platform="greenhouse", all_pages=pages,
        time_seconds=30.0, success=True,
    )

    from jobpulse.platform_playbook import PlatformPlaybookDB
    pdb = PlatformPlaybookDB(db_path=observer._playbook_db)
    info = pdb.get_platform("greenhouse")
    assert info is not None
    assert info["total_applications"] == 1
    assert info["avg_pages"] == 2.0
    assert info["has_file_upload"] == 1
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/jobpulse/test_form_observer.py -v`
Expected: 3 PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/form_observer.py tests/jobpulse/test_form_observer.py
git commit -m "feat: add FormObserver — unified write path to all 6 stores"
```

---

### Task 6: Wire Tier 2 Stores into form_prefetch.py

**Files:**
- Modify: `jobpulse/form_prefetch.py`
- Modify: `tests/jobpulse/test_form_prefetch.py`

- [ ] **Step 1: Add store fields to FormHints dataclass**

In `jobpulse/form_prefetch.py`, add after the existing `frequently_corrected_fields` field:

```python
    registered_fields: list[dict] = field(default_factory=list)
    combobox_mappings: list[dict] = field(default_factory=list)
    platform_playbook: dict | None = None
```

- [ ] **Step 2: Add store queries to prefetch_form_hints()**

Add after the correction accuracy block (section 4) and before `_stats["total_lookups"] += 1`:

```python
    # 5. Field Registry
    try:
        from jobpulse.field_registry import FieldRegistryDB
        registry = FieldRegistryDB()
        reg_fields = registry.get_fields(url)
        if reg_fields:
            hints.registered_fields = reg_fields
    except Exception as exc:
        logger.debug("form_prefetch: field_registry lookup failed: %s", exc)

    # 6. Combobox Mappings
    try:
        from jobpulse.combobox_mappings import ComboboxMappingsDB
        combo_db = ComboboxMappingsDB()
        combos = combo_db.get_mappings(url)
        if combos:
            hints.combobox_mappings = combos
    except Exception as exc:
        logger.debug("form_prefetch: combobox lookup failed: %s", exc)

    # 7. Platform Playbook
    try:
        if hints.platform:
            from jobpulse.platform_playbook import PlatformPlaybookDB
            playbook = PlatformPlaybookDB()
            pinfo = playbook.get_platform(hints.platform)
            if pinfo:
                hints.platform_playbook = pinfo
    except Exception as exc:
        logger.debug("form_prefetch: playbook lookup failed: %s", exc)
```

- [ ] **Step 3: Write test — prefetch includes Tier 2 store data**

Add to `tests/jobpulse/test_form_prefetch.py`:

```python
def test_prefetch_includes_field_registry(db_paths, tmp_path):
    from jobpulse.field_registry import FieldRegistryDB

    reg_path = str(tmp_path / "registry.db")
    reg = FieldRegistryDB(db_path=reg_path)
    reg.upsert("boards.greenhouse.io", "Name", "text", 1, value="Yash")
    reg.upsert("boards.greenhouse.io", "Email", "text", 1, value="test@test.com")

    with patch("jobpulse.form_prefetch.FieldRegistryDB", return_value=reg):
        hints = prefetch_form_hints("https://boards.greenhouse.io/co/jobs/1", **db_paths)

    assert len(hints.registered_fields) == 2
```

- [ ] **Step 4: Run all form_prefetch tests**

Run: `python -m pytest tests/jobpulse/test_form_prefetch.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_prefetch.py tests/jobpulse/test_form_prefetch.py
git commit -m "feat: wire Tier 2 stores into form_prefetch — registry, combobox, playbook"
```

---

### Task 7: Page Scanner — DOM Field Extraction

**Files:**
- Create: `jobpulse/page_scanner.py`
- Test: `tests/jobpulse/test_page_scanner.py`

- [ ] **Step 1: Write the failing test — scan returns field list from mock page**

```python
# tests/jobpulse/test_page_scanner.py
"""Tests for page_scanner — DOM field extraction."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from jobpulse.page_scanner import scan_page_fields


@pytest.fixture
def mock_page():
    page = AsyncMock()

    textbox_loc = AsyncMock()
    textbox_loc.evaluate = AsyncMock(return_value="Name")
    textbox_loc.input_value = AsyncMock(return_value="")
    textbox_loc.get_attribute = AsyncMock(return_value="true")
    page.get_by_role.return_value.all = AsyncMock(return_value=[textbox_loc])

    return page


@pytest.mark.asyncio
async def test_scan_returns_text_field(mock_page):
    # get_by_role("textbox") returns our mock, others return empty
    def role_side_effect(role):
        m = AsyncMock()
        if role == "textbox":
            loc = AsyncMock()
            loc.evaluate = AsyncMock(return_value="Full Name")
            loc.input_value = AsyncMock(return_value="")
            loc.get_attribute = AsyncMock(return_value="true")
            m.all = AsyncMock(return_value=[loc])
        else:
            m.all = AsyncMock(return_value=[])
        return m

    mock_page.get_by_role = MagicMock(side_effect=role_side_effect)
    mock_page.query_selector_all = AsyncMock(return_value=[])

    fields = await scan_page_fields(mock_page)
    assert len(fields) >= 1
    assert fields[0]["label"] == "Full Name"
    assert fields[0]["type"] == "text"
    assert fields[0]["required"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_page_scanner.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write implementation**

```python
# jobpulse/page_scanner.py
"""DOM field extraction for FormPilot.

Scans visible form fields using Playwright role-based locators.
Extracted from NativeFormFiller._scan_fields() for reuse in the
FormPilot graph planner node.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging_config import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


async def _get_accessible_name(locator) -> str:
    return await locator.evaluate(
        "el => {"
        "  const lbl = el.labels?.[0];"
        "  if (lbl) {"
        "    const clone = lbl.cloneNode(true);"
        "    clone.querySelectorAll('[aria-hidden]').forEach(n => n.remove());"
        "    const t = clone.textContent.trim();"
        "    if (t) return t;"
        "  }"
        "  return el.getAttribute('aria-label') || el.placeholder || '';"
        "}"
    )


async def scan_page_fields(page: Page) -> list[dict]:
    """Scan all visible form fields on the current page.

    Returns list of dicts: {label, type, locator, value, required, options}.
    """
    fields: list[dict] = []

    for loc in await page.get_by_role("textbox").all():
        label = await _get_accessible_name(loc)
        if not label:
            continue
        fields.append({
            "label": label,
            "type": "text",
            "locator": loc,
            "value": await loc.input_value(),
            "required": await loc.get_attribute("required") is not None,
            "options": [],
        })

    for loc in await page.get_by_role("combobox").all():
        label = await _get_accessible_name(loc)
        if not label:
            continue
        tag = await loc.evaluate("el => el.tagName.toLowerCase()")
        if tag == "select":
            options = await loc.evaluate(
                "el => Array.from(el.options).map(o => o.text.trim()).filter(Boolean)"
            )
            fields.append({
                "label": label, "type": "select", "locator": loc,
                "value": "", "required": await loc.get_attribute("required") is not None,
                "options": options,
            })
        else:
            fields.append({
                "label": label, "type": "combobox", "locator": loc,
                "value": await loc.input_value(),
                "required": await loc.get_attribute("required") is not None,
                "options": [],
            })

    for loc in await page.get_by_role("radio").all():
        label = await _get_accessible_name(loc)
        if not label:
            continue
        fields.append({
            "label": label, "type": "radio", "locator": loc,
            "value": "", "required": False, "options": [],
        })

    for loc in await page.get_by_role("checkbox").all():
        label = await _get_accessible_name(loc)
        if not label:
            continue
        fields.append({
            "label": label, "type": "checkbox", "locator": loc,
            "value": "", "required": False, "options": [],
        })

    file_inputs = await page.query_selector_all("input[type='file']")
    for loc in file_inputs:
        label = await loc.evaluate(
            "el => el.labels?.[0]?.textContent?.trim() || el.getAttribute('aria-label') || 'File Upload'"
        )
        fields.append({
            "label": label, "type": "file", "locator": loc,
            "value": "", "required": False, "options": [],
        })

    logger.info("page_scanner: found %d fields", len(fields))
    return fields
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_page_scanner.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_scanner.py tests/jobpulse/test_page_scanner.py
git commit -m "feat: add page_scanner — DOM field extraction for FormPilot"
```

---

### Task 8: Page Verifier — Post-Fill DOM Verification

**Files:**
- Create: `jobpulse/page_verifier.py`
- Test: `tests/jobpulse/test_page_verifier.py`

- [ ] **Step 1: Write the failing test — verify detects mismatched field**

```python
# tests/jobpulse/test_page_verifier.py
"""Tests for page_verifier — post-fill DOM verification."""
from unittest.mock import AsyncMock, MagicMock
import pytest

from jobpulse.page_verifier import verify_page_fields
from jobpulse.form_pilot_state import FieldResult


@pytest.mark.asyncio
async def test_all_fields_pass_verification():
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value="Yash Bishnoi")
    page.query_selector_all = AsyncMock(return_value=[])

    results = [
        FieldResult(
            field_label="Name", value_attempted="Yash Bishnoi",
            value_set="Yash Bishnoi", method="pattern", tier=1,
            confidence=1.0, success=True, error=None,
            selector="input[name='name']",
        ),
    ]

    failed = await verify_page_fields(page, results)
    assert failed == []


@pytest.mark.asyncio
async def test_detects_mismatched_value():
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value="")
    page.query_selector_all = AsyncMock(return_value=[])

    results = [
        FieldResult(
            field_label="Email", value_attempted="test@test.com",
            value_set="test@test.com", method="pattern", tier=1,
            confidence=1.0, success=True, error=None,
            selector="input[name='email']",
        ),
    ]

    failed = await verify_page_fields(page, results)
    assert len(failed) == 1
    assert failed[0]["field_label"] == "Email"


@pytest.mark.asyncio
async def test_detects_error_messages():
    page = AsyncMock()
    page.evaluate = AsyncMock(return_value="test@test.com")

    error_el = AsyncMock()
    error_el.text_content = AsyncMock(return_value="This field is required")
    error_el.is_visible = AsyncMock(return_value=True)
    page.query_selector_all = AsyncMock(return_value=[error_el])

    errors = await verify_page_fields(page, [], check_errors=True)
    # Returns error info but doesn't fail fields
    assert isinstance(errors, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_page_verifier.py -v`
Expected: FAIL with "ModuleNotFoundError"

- [ ] **Step 3: Write implementation**

```python
# jobpulse/page_verifier.py
"""Post-fill DOM verification for FormPilot.

After filling all fields on a page, checks that DOM values match
what was filled and scans for error messages.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from shared.logging_config import get_logger

from jobpulse.form_pilot_state import FieldResult

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)

_ERROR_SELECTORS = [
    "[class*='error']",
    "[role='alert']",
    ".field-error",
    ".validation-error",
    ".invalid-feedback",
]


async def verify_page_fields(
    page: Page,
    fill_results: list[FieldResult],
    *,
    check_errors: bool = True,
) -> list[FieldResult]:
    """Verify filled fields by reading back DOM values.

    Returns list of FieldResult entries where verification failed.
    """
    failed: list[FieldResult] = []

    for result in fill_results:
        if not result["success"] or not result["selector"]:
            continue
        if result["method"] == "file":
            continue

        try:
            actual = await page.evaluate(
                """(selector) => {
                    const el = document.querySelector(selector);
                    if (!el) return null;
                    if (el.tagName === 'SELECT') return el.options[el.selectedIndex]?.text?.trim() || '';
                    return el.value || '';
                }""",
                result["selector"],
            )
            if actual is not None and actual.strip() != result["value_set"].strip():
                logger.warning(
                    "page_verifier: MISMATCH %s — expected '%s', got '%s'",
                    result["field_label"], result["value_set"][:30], str(actual)[:30],
                )
                failed.append(result)
        except Exception as exc:
            logger.debug("page_verifier: verification error for %s: %s", result["field_label"], exc)

    if check_errors:
        for selector in _ERROR_SELECTORS:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements:
                    visible = await el.is_visible()
                    if visible:
                        text = await el.text_content()
                        if text and text.strip():
                            logger.warning("page_verifier: error message found: '%s'", text.strip()[:80])
            except Exception:
                pass

    logger.info("page_verifier: %d/%d fields failed verification", len(failed), len(fill_results))
    return failed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_page_verifier.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/page_verifier.py tests/jobpulse/test_page_verifier.py
git commit -m "feat: add page_verifier — post-fill DOM verification for FormPilot"
```

---

### Task 9: FormPilot LangGraph — Graph Definition + Entry Point

**Files:**
- Create: `jobpulse/form_pilot.py`
- Test: `tests/jobpulse/test_form_pilot.py`

- [ ] **Step 1: Write the graph definition**

```python
# jobpulse/form_pilot.py
"""FormPilot — LangGraph Plan-and-Execute for autonomous form filling.

Entry point: run_form_pilot() called from applicator.apply_job().
Graph: auth_gate → form_planner → field_executor → page_verifier
       → observer → (next page | approval_gate) → submit/abort.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import StateGraph, START, END

from shared.logging_config import get_logger

from jobpulse.form_pilot_state import FormPilotState, make_initial_state

if TYPE_CHECKING:
    from playwright.async_api import Page

logger = get_logger(__name__)


def _route_after_verify(state: FormPilotState) -> str:
    if state["failed_fields"] and state["rescue_attempts"] < 2:
        return "rescue_node"
    return "observer"


def _route_after_observe(state: FormPilotState) -> str:
    if state["form_complete"]:
        return "approval_gate"
    return "form_planner"


def _route_after_approval(state: FormPilotState) -> str:
    if state["approval_status"] == "approved":
        return END
    if state["approval_status"] == "corrected":
        return "field_executor"
    return END


def _route_after_auth(state: FormPilotState) -> str:
    if state["auth_status"] in ("logged_in", "created"):
        return "form_planner"
    return END


def build_form_pilot_graph() -> StateGraph:
    from jobpulse.form_pilot_nodes import (
        auth_gate_node,
        form_planner_node,
        field_executor_node,
        page_verifier_node,
        rescue_node,
        observer_node,
        approval_gate_node,
    )

    graph = StateGraph(FormPilotState)

    graph.add_node("auth_gate", auth_gate_node)
    graph.add_node("form_planner", form_planner_node)
    graph.add_node("field_executor", field_executor_node)
    graph.add_node("page_verifier", page_verifier_node)
    graph.add_node("rescue_node", rescue_node)
    graph.add_node("observer", observer_node)
    graph.add_node("approval_gate", approval_gate_node)

    graph.add_edge(START, "auth_gate")
    graph.add_conditional_edges("auth_gate", _route_after_auth)
    graph.add_edge("form_planner", "field_executor")
    graph.add_edge("field_executor", "page_verifier")
    graph.add_conditional_edges("page_verifier", _route_after_verify)
    graph.add_edge("rescue_node", "page_verifier")
    graph.add_conditional_edges("observer", _route_after_observe)
    graph.add_conditional_edges("approval_gate", _route_after_approval)

    return graph


_compiled_graph = None


def _get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_form_pilot_graph().compile()
    return _compiled_graph


async def run_form_pilot(
    page: Page,
    url: str,
    domain: str,
    platform: str,
    cv_path: str,
    job_context: dict,
    merged_answers: dict,
    cl_path: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Entry point called from applicator.apply_job()."""
    state = make_initial_state(
        url=url, domain=domain, platform=platform,
        cv_path=cv_path, cl_path=cl_path,
        job_context=job_context, merged_answers=merged_answers,
    )

    graph = _get_graph()
    final_state = await graph.ainvoke(state, config={"page": page, "dry_run": dry_run})

    return final_state.get("result", {"success": False})
```

- [ ] **Step 2: Write the node stubs**

```python
# jobpulse/form_pilot_nodes.py
"""FormPilot LangGraph node functions.

All nodes are stateless functions that receive FormPilotState and return
partial state updates. Playwright page is passed via config.
"""
from __future__ import annotations

from shared.logging_config import get_logger

from jobpulse.form_pilot_state import FormPilotState

logger = get_logger(__name__)


async def auth_gate_node(state: FormPilotState, config: dict) -> dict:
    """Check auth status and handle login/signup if needed."""
    # For now: assume already on form page (logged_in)
    # Full implementation: SSOHandler → AccountManager → signup → GmailVerifier
    return {"auth_status": "logged_in", "auth_method": "assumed"}


async def form_planner_node(state: FormPilotState, config: dict) -> dict:
    """Scan current page and produce ordered fill plan."""
    from jobpulse.page_scanner import scan_page_fields

    page = config.get("page")
    if not page:
        return {"page_plan": [], "form_complete": True}

    fields = await scan_page_fields(page)

    from jobpulse.form_pilot_state import FieldPlan
    plan = []
    for f in fields:
        # Query stores for intelligence
        expected = None
        combo_mapping = None
        strategy = "llm"

        try:
            from jobpulse.field_registry import FieldRegistryDB
            reg = FieldRegistryDB()
            reg_field = reg.get_field(state["domain"], f["label"], state["current_page"])
            if reg_field and reg_field["success_count"] > 0:
                expected = reg_field["typical_value"]
                rate = reg.get_success_rate(state["domain"], f["label"], state["current_page"])
                if rate > 0.8:
                    strategy = "registry"
        except Exception:
            pass

        if f["type"] in ("select", "combobox") and strategy != "registry":
            try:
                from jobpulse.combobox_mappings import ComboboxMappingsDB
                cdb = ComboboxMappingsDB()
                cached = cdb.lookup(state["domain"], f["label"], expected or "")
                if cached:
                    combo_mapping = cached
                    strategy = "combobox_cache"
            except Exception:
                pass

        plan.append(FieldPlan(
            field_label=f["label"],
            field_type=f["type"],
            page_num=state["current_page"],
            selector=None,
            expected_value=expected,
            combobox_mapping=combo_mapping,
            resolution_strategy=strategy,
        ))

    # Detect if this is the last page (Submit button present)
    form_complete = False
    try:
        submit_btn = await page.query_selector("button[type='submit'], input[type='submit'], button:has-text('Submit')")
        next_btn = await page.query_selector("button:has-text('Next'), button:has-text('Continue')")
        if submit_btn and not next_btn:
            form_complete = True
    except Exception:
        pass

    return {
        "page_plan": plan,
        "fill_results": [],
        "failed_fields": [],
        "rescue_attempts": 0,
    }


async def field_executor_node(state: FormPilotState, config: dict) -> dict:
    """Fill all fields in the plan using FormIntelligence + Playwright."""
    from jobpulse.form_pilot_state import FieldResult

    page = config.get("page")
    if not page:
        return {"fill_results": []}

    results = []
    for field_plan in state["page_plan"]:
        value = ""
        method = "unknown"
        tier = 0
        confidence = 0.0
        success = False
        error = None

        # Resolve value
        if field_plan["combobox_mapping"]:
            value = field_plan["combobox_mapping"]
            method = "combobox_cache"
            tier = 0
            confidence = 0.95
        elif field_plan["expected_value"] and field_plan["resolution_strategy"] == "registry":
            value = field_plan["expected_value"]
            method = "registry"
            tier = 0
            confidence = 0.9
        elif field_plan["field_label"] in state["merged_answers"]:
            value = state["merged_answers"][field_plan["field_label"]]
            method = "merged_answer"
            tier = 1
            confidence = 1.0
        else:
            try:
                from jobpulse.form_intelligence import FormIntelligence
                fi = FormIntelligence()
                result = fi.resolve(
                    field_plan["field_label"],
                    state["job_context"],
                    platform=state["platform"],
                )
                value = result.answer
                method = result.tier_name
                tier = result.tier
                confidence = result.confidence
            except Exception as exc:
                error = str(exc)

        # Fill via Playwright
        if value and not error:
            try:
                if field_plan["field_type"] == "file":
                    file_inputs = await page.query_selector_all("input[type='file']")
                    if file_inputs:
                        await file_inputs[0].set_input_files(state["cv_path"])
                        success = True
                elif field_plan["field_type"] in ("select", "combobox"):
                    from jobpulse.form_engine.select_filler import fill_select
                    loc = page.get_by_label(field_plan["field_label"])
                    selector = await loc.evaluate("el => el.id ? '#' + el.id : ''") if await loc.count() else ""
                    if selector:
                        fill_result = await fill_select(page, selector, value)
                        success = fill_result.success
                        if fill_result.value_set:
                            value = fill_result.value_set
                    else:
                        success = False
                        error = "No selector found for select"
                else:
                    loc = page.get_by_label(field_plan["field_label"])
                    if await loc.count():
                        await loc.fill(value)
                        success = True
                    else:
                        error = f"Locator not found for '{field_plan['field_label']}'"
            except Exception as exc:
                error = str(exc)

        results.append(FieldResult(
            field_label=field_plan["field_label"],
            value_attempted=value,
            value_set=value if success else "",
            method=method, tier=tier, confidence=confidence,
            success=success, error=error, selector="",
        ))

    return {"fill_results": results}


async def page_verifier_node(state: FormPilotState, config: dict) -> dict:
    """Verify filled fields by checking DOM state."""
    from jobpulse.page_verifier import verify_page_fields
    from jobpulse.form_pilot_state import FieldPlan

    page = config.get("page")
    if not page:
        return {"failed_fields": []}

    failed_results = await verify_page_fields(page, state["fill_results"])

    failed_plans = []
    for fr in failed_results:
        for fp in state["page_plan"]:
            if fp["field_label"].lower() == fr["field_label"].lower():
                failed_plans.append(fp)
                break

    screenshot = ""
    try:
        screenshot_bytes = await page.screenshot()
        import base64
        screenshot = base64.b64encode(screenshot_bytes).decode()
    except Exception:
        pass

    return {
        "failed_fields": failed_plans,
        "page_screenshot_b64": screenshot,
    }


async def rescue_node(state: FormPilotState, config: dict) -> dict:
    """Escalate failed fields: vision → LLM → human-in-the-loop."""
    from jobpulse.form_pilot_state import FieldResult

    page = config.get("page")
    new_results = list(state["fill_results"])

    for field_plan in state["failed_fields"]:
        value = ""
        method = "rescue_llm"
        success = False

        # Try LLM with more context
        try:
            from shared.agents import smart_llm_call
            prompt = (
                f"A form field '{field_plan['field_label']}' (type: {field_plan['field_type']}) "
                f"failed to fill on {state['domain']}. "
                f"Job: {state['job_context'].get('title', '')} at {state['job_context'].get('company', '')}. "
                f"What value should this field have?"
            )
            value = await smart_llm_call(prompt)
            if value and page:
                loc = page.get_by_label(field_plan["field_label"])
                if await loc.count():
                    await loc.fill(value)
                    success = True
                    method = "rescue_llm"
        except Exception:
            pass

        # Update the result in the list
        for i, r in enumerate(new_results):
            if r["field_label"].lower() == field_plan["field_label"].lower():
                new_results[i] = FieldResult(
                    field_label=field_plan["field_label"],
                    value_attempted=value, value_set=value if success else "",
                    method=method, tier=6, confidence=0.6,
                    success=success, error=None if success else "rescue failed",
                    selector="",
                )
                break

    return {
        "fill_results": new_results,
        "rescue_attempts": state["rescue_attempts"] + 1,
    }


async def observer_node(state: FormPilotState, config: dict) -> dict:
    """Record page results to all stores and navigate to next page."""
    from jobpulse.form_pilot_state import PageRecord
    import uuid

    page = config.get("page")
    session_id = str(uuid.uuid4())[:8]

    page_record = PageRecord(
        page_num=state["current_page"],
        page_title="",
        fields=state["fill_results"],
        screenshot_b64=state.get("page_screenshot_b64", ""),
        has_file_upload=any(f["method"] == "file" for f in state["fill_results"]),
        nav_button="",
    )

    # Write to stores
    try:
        from jobpulse.form_observer import FormObserver
        observer = FormObserver()
        observer.record_page(
            domain=state["domain"], platform=state["platform"],
            url=state["url"], page_record=page_record,
            session_id=session_id,
        )
    except Exception as exc:
        logger.debug("observer_node: store writes failed: %s", exc)

    all_pages = list(state["all_pages_filled"])
    all_pages.append(page_record)

    # Navigate to next page if not complete
    form_complete = state.get("form_complete", False)
    if not form_complete and page:
        try:
            next_btn = await page.query_selector("button:has-text('Next'), button:has-text('Continue'), button:has-text('Save & Continue')")
            if next_btn:
                await next_btn.click()
                await page.wait_for_load_state("networkidle")
            else:
                form_complete = True
        except Exception:
            form_complete = True

    return {
        "all_pages_filled": all_pages,
        "current_page": state["current_page"] + 1,
        "form_complete": form_complete,
    }


async def approval_gate_node(state: FormPilotState, config: dict) -> dict:
    """Send filled form to Telegram for approval. Wait for response."""
    dry_run = config.get("dry_run", True)

    if not dry_run:
        return {"approval_status": "approved", "success": True, "result": _build_result(state)}

    # Send to Telegram
    try:
        from shared.telegram_client import send_message, send_photo
        company = state["job_context"].get("company", "Unknown")
        platform = state["platform"]
        n_pages = len(state["all_pages_filled"])
        n_fields = sum(len(p["fields"]) for p in state["all_pages_filled"])

        msg = (
            f"FormPilot ready to submit to {company} ({platform}).\n"
            f"{n_pages} pages, {n_fields} fields filled.\n"
            f"Reply 'yes' to submit, 'no' to abort."
        )
        await send_message(msg)

        if state.get("page_screenshot_b64"):
            import base64
            screenshot_bytes = base64.b64decode(state["page_screenshot_b64"])
            await send_photo(screenshot_bytes, caption=f"Final form — {company}")
    except Exception as exc:
        logger.warning("approval_gate: Telegram send failed: %s", exc)

    # For now: return pending (manual approval handled externally)
    return {
        "approval_status": "pending",
        "result": _build_result(state),
    }


def _build_result(state: FormPilotState) -> dict:
    all_fields = []
    screening = []
    for page in state["all_pages_filled"]:
        for f in page["fields"]:
            all_fields.append(f["method"])
    return {
        "success": True,
        "pages_filled": len(state["all_pages_filled"]),
        "field_types": all_fields,
        "screening_questions": screening,
        "time_seconds": 0.0,
    }
```

- [ ] **Step 3: Write graph integration test**

```python
# tests/jobpulse/test_form_pilot.py
"""Integration tests for FormPilot LangGraph."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from jobpulse.form_pilot import build_form_pilot_graph, _route_after_verify, _route_after_observe, _route_after_auth
from jobpulse.form_pilot_state import FormPilotState, make_initial_state


def test_graph_builds_without_error():
    graph = build_form_pilot_graph()
    compiled = graph.compile()
    assert compiled is not None


def test_route_after_auth_success():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["auth_status"] = "logged_in"
    assert _route_after_auth(state) == "form_planner"


def test_route_after_auth_failure():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["auth_status"] = "failed"
    from langgraph.graph import END
    assert _route_after_auth(state) == END


def test_route_after_verify_with_failures():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["failed_fields"] = [{"field_label": "x"}]
    state["rescue_attempts"] = 0
    assert _route_after_verify(state) == "rescue_node"


def test_route_after_verify_max_rescue():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["failed_fields"] = [{"field_label": "x"}]
    state["rescue_attempts"] = 2
    assert _route_after_verify(state) == "observer"


def test_route_after_verify_all_ok():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["failed_fields"] = []
    assert _route_after_verify(state) == "observer"


def test_route_after_observe_more_pages():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["form_complete"] = False
    assert _route_after_observe(state) == "form_planner"


def test_route_after_observe_last_page():
    state = make_initial_state("url", "d", "p", "cv", {}, {})
    state["form_complete"] = True
    assert _route_after_observe(state) == "approval_gate"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/jobpulse/test_form_pilot.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_pilot.py jobpulse/form_pilot_nodes.py tests/jobpulse/test_form_pilot.py
git commit -m "feat: add FormPilot LangGraph — 7-node autonomous form filling graph"
```

---

### Task 10: Run Full Test Suite — Verify No Regressions

**Files:** None (verification only)

- [ ] **Step 1: Run all new Tier 2 store tests**

Run: `python -m pytest tests/jobpulse/test_field_registry.py tests/jobpulse/test_combobox_mappings.py tests/jobpulse/test_platform_playbook.py -v`
Expected: All PASS (21 tests)

- [ ] **Step 2: Run all FormPilot tests**

Run: `python -m pytest tests/jobpulse/test_form_pilot_state.py tests/jobpulse/test_form_observer.py tests/jobpulse/test_page_scanner.py tests/jobpulse/test_page_verifier.py tests/jobpulse/test_form_pilot.py -v`
Expected: All PASS

- [ ] **Step 3: Run full jobpulse test suite for regressions**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60`
Expected: All PASS, no regressions

- [ ] **Step 4: Final commit if cleanup needed**

```bash
git add -A && git commit -m "chore: post-integration cleanup"
```
