# Pipeline Introspection System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture every pipeline action via a lightweight event bus, then LLM-verbalize into exhaustive agent-voice PDF reports sent to Telegram after each application.

**Architecture:** Thin `emit()` calls at 52 action points buffer events in memory during a run. On completion, events flush to SQLite, an LLM verbalizer produces a narrative report with hallucination guard + coverage enforcement, ReportLab renders the PDF, and `send_jobs_document()` delivers it to Telegram.

**Tech Stack:** Python dataclasses, SQLite, ReportLab, `smart_llm_call()`, existing Telegram bot infra.

**Spec:** `docs/superpowers/specs/2026-04-30-pipeline-introspection-design.md`

---

## File Structure

```
jobpulse/introspection/
├── __init__.py          # Public API: emit(), flush(), get_buffer(), CATEGORIES
├── events.py            # IntrospectionEvent dataclass, IntrospectionBuffer
├── store.py             # SQLite: events, reports, dpo_pairs tables
├── verbalizer.py        # LLM verbalization + hallucination guard + retry loop
├── validator.py         # Coverage checker (event→narrative cross-ref)
├── renderer.py          # ReportLab PDF (agent-voice layout)
├── cli.py               # CLI subcommands (last, list, show, failures, correct, stats, ood-report)
├── dpo.py               # DPO pair storage + prompt refinement

tests/jobpulse/test_introspection/
├── __init__.py
├── test_events.py       # Event + buffer tests
├── test_store.py        # SQLite CRUD tests
├── test_verbalizer.py   # Verbalization + hallucination guard tests
├── test_validator.py    # Coverage enforcement tests
├── test_renderer.py     # PDF generation tests
├── test_cli.py          # CLI subcommand tests
├── test_dpo.py          # DPO pair + prompt refinement tests
├── test_integration.py  # End-to-end: emit → flush → verbalize → render → deliver
```

**Modified files (emit instrumentation — one line each):**
- `jobpulse/application_orchestrator_pkg/__init__.py` — buffer creation in `__init__`
- `jobpulse/applicator.py` — flush trigger in `confirm_application()` and `apply_job()`
- `jobpulse/runner.py` — `introspect` subcommand routing
- ~20 existing pipeline files get one-line `emit()` calls (see Task 10)

---

### Task 1: IntrospectionEvent dataclass and IntrospectionBuffer

**Files:**
- Create: `jobpulse/introspection/__init__.py`
- Create: `jobpulse/introspection/events.py`
- Test: `tests/jobpulse/test_introspection/__init__.py`
- Test: `tests/jobpulse/test_introspection/test_events.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/__init__.py
# (empty)

# tests/jobpulse/test_introspection/test_events.py
import time
import pytest
from jobpulse.introspection.events import IntrospectionEvent, IntrospectionBuffer


class TestIntrospectionEvent:
    def test_create_event(self):
        ev = IntrospectionEvent(
            category="FormFill",
            action="fill_field",
            target="First Name",
            outcome="success",
            detail={"value": "Yash", "method": "profile"},
            duration_ms=12.5,
        )
        assert ev.category == "FormFill"
        assert ev.action == "fill_field"
        assert ev.target == "First Name"
        assert ev.outcome == "success"
        assert ev.detail == {"value": "Yash", "method": "profile"}
        assert ev.duration_ms == 12.5
        assert ev.timestamp > 0

    def test_event_auto_timestamp(self):
        before = time.time()
        ev = IntrospectionEvent(
            category="Navigation", action="page_load", target="https://example.com",
            outcome="success", detail={}, duration_ms=250.0,
        )
        after = time.time()
        assert before <= ev.timestamp <= after

    def test_event_to_dict(self):
        ev = IntrospectionEvent(
            category="PreScreen", action="gate_pass", target="gate_2",
            outcome="success", detail={"score": 4, "of": 5}, duration_ms=1.2,
        )
        d = ev.to_dict()
        assert d["category"] == "PreScreen"
        assert d["action"] == "gate_pass"
        assert d["detail"] == {"score": 4, "of": 5}
        assert "timestamp" in d
        assert "event_id" in d


class TestIntrospectionBuffer:
    def test_create_buffer(self):
        buf = IntrospectionBuffer(company="ASOS", role="Data Analyst")
        assert buf.company == "ASOS"
        assert buf.role == "Data Analyst"
        assert buf.run_id  # non-empty string
        assert len(buf.events) == 0

    def test_emit_appends_event(self):
        buf = IntrospectionBuffer(company="Test", role="Engineer")
        buf.emit("FormFill", "fill_field", target="Email",
                 outcome="success", detail={"value": "test@example.com"}, duration_ms=5.0)
        assert len(buf.events) == 1
        assert buf.events[0].category == "FormFill"
        assert buf.events[0].target == "Email"

    def test_emit_multiple_categories(self):
        buf = IntrospectionBuffer(company="Test", role="Engineer")
        buf.emit("PreScreen", "gate_pass", target="gate_0", outcome="success", detail={}, duration_ms=1.0)
        buf.emit("FormFill", "fill_field", target="Name", outcome="success", detail={}, duration_ms=5.0)
        buf.emit("Learning", "signal_emit", target="optimization", outcome="success", detail={}, duration_ms=2.0)
        assert len(buf.events) == 3
        categories = {e.category for e in buf.events}
        assert categories == {"PreScreen", "FormFill", "Learning"}

    def test_events_by_category(self):
        buf = IntrospectionBuffer(company="Test", role="Engineer")
        buf.emit("FormFill", "fill_field", target="Name", outcome="success", detail={}, duration_ms=5.0)
        buf.emit("FormFill", "fill_field", target="Email", outcome="success", detail={}, duration_ms=3.0)
        buf.emit("Navigation", "page_load", target="/apply", outcome="success", detail={}, duration_ms=200.0)
        grouped = buf.events_by_category()
        assert len(grouped["FormFill"]) == 2
        assert len(grouped["Navigation"]) == 1
        assert grouped.get("PreScreen", []) == []

    def test_clear(self):
        buf = IntrospectionBuffer(company="Test", role="Engineer")
        buf.emit("FormFill", "fill_field", target="Name", outcome="success", detail={}, duration_ms=5.0)
        buf.clear()
        assert len(buf.events) == 0

    def test_disabled_buffer_no_ops(self):
        buf = IntrospectionBuffer(company="Test", role="Engineer", enabled=False)
        buf.emit("FormFill", "fill_field", target="Name", outcome="success", detail={}, duration_ms=5.0)
        assert len(buf.events) == 0

    def test_summary(self):
        buf = IntrospectionBuffer(company="ASOS", role="Analyst")
        buf.emit("FormFill", "fill_field", target="Name", outcome="success", detail={}, duration_ms=5.0)
        buf.emit("FormFill", "fill_field", target="Email", outcome="failure", detail={}, duration_ms=3.0)
        buf.emit("Navigation", "page_load", target="/", outcome="success", detail={}, duration_ms=200.0)
        s = buf.summary()
        assert s["company"] == "ASOS"
        assert s["role"] == "Analyst"
        assert s["event_count"] == 3
        assert s["categories"]["FormFill"] == 2
        assert s["categories"]["Navigation"] == 1
        assert s["outcomes"]["success"] == 2
        assert s["outcomes"]["failure"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.introspection'`

- [ ] **Step 3: Implement events.py**

```python
# jobpulse/introspection/events.py
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field


CATEGORIES = frozenset({
    "FormFill", "Navigation", "Screening", "Hooks",
    "Learning", "PreScreen", "CVGen", "Submission",
})


@dataclass
class IntrospectionEvent:
    category: str
    action: str
    target: str
    outcome: str
    detail: dict
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "category": self.category,
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
        }


class IntrospectionBuffer:
    def __init__(self, company: str, role: str, *, enabled: bool = True):
        self.company = company
        self.role = role
        self.run_id = uuid.uuid4().hex[:12]
        self.events: list[IntrospectionEvent] = []
        self._enabled = enabled

    def emit(self, category: str, action: str, *, target: str,
             outcome: str, detail: dict, duration_ms: float) -> None:
        if not self._enabled:
            return
        self.events.append(IntrospectionEvent(
            category=category, action=action, target=target,
            outcome=outcome, detail=detail, duration_ms=duration_ms,
        ))

    def events_by_category(self) -> dict[str, list[IntrospectionEvent]]:
        grouped: dict[str, list[IntrospectionEvent]] = defaultdict(list)
        for ev in self.events:
            grouped[ev.category].append(ev)
        return dict(grouped)

    def clear(self) -> None:
        self.events.clear()

    def summary(self) -> dict:
        categories: dict[str, int] = defaultdict(int)
        outcomes: dict[str, int] = defaultdict(int)
        for ev in self.events:
            categories[ev.category] += 1
            outcomes[ev.outcome] += 1
        return {
            "run_id": self.run_id,
            "company": self.company,
            "role": self.role,
            "event_count": len(self.events),
            "categories": dict(categories),
            "outcomes": dict(outcomes),
        }
```

- [ ] **Step 4: Implement __init__.py (public API)**

```python
# jobpulse/introspection/__init__.py
"""Pipeline Introspection System — capture and verbalize every pipeline action."""
from __future__ import annotations

import os
import threading
from typing import Any

from jobpulse.introspection.events import IntrospectionBuffer, IntrospectionEvent, CATEGORIES

_thread_local = threading.local()

ENABLED = os.environ.get("INTROSPECTION_ENABLED", "true").lower() not in ("false", "0", "no")


def set_buffer(buf: IntrospectionBuffer) -> None:
    _thread_local.buffer = buf


def get_buffer() -> IntrospectionBuffer | None:
    return getattr(_thread_local, "buffer", None)


def emit(category: str, action: str, *, target: str = "",
         outcome: str = "success", detail: dict[str, Any] | None = None,
         duration_ms: float = 0.0) -> None:
    if not ENABLED:
        return
    buf = get_buffer()
    if buf is None:
        return
    buf.emit(category, action, target=target, outcome=outcome,
             detail=detail or {}, duration_ms=duration_ms)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_events.py -v`
Expected: all 9 tests PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/introspection/__init__.py jobpulse/introspection/events.py \
       tests/jobpulse/test_introspection/__init__.py tests/jobpulse/test_introspection/test_events.py
git commit -m "feat(introspection): add IntrospectionEvent dataclass and IntrospectionBuffer"
```

---

### Task 2: SQLite Store (events, reports, dpo_pairs)

**Files:**
- Create: `jobpulse/introspection/store.py`
- Test: `tests/jobpulse/test_introspection/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_store.py
import json
import time
import pytest
from jobpulse.introspection.events import IntrospectionEvent, IntrospectionBuffer
from jobpulse.introspection.store import IntrospectionStore


@pytest.fixture
def store(tmp_path):
    return IntrospectionStore(db_path=str(tmp_path / "introspection.db"))


@pytest.fixture
def sample_buffer():
    buf = IntrospectionBuffer(company="ASOS", role="Data Analyst")
    buf.emit("PreScreen", "gate_pass", target="gate_0", outcome="success",
             detail={"reason": "title match"}, duration_ms=1.2)
    buf.emit("FormFill", "fill_field", target="First Name", outcome="success",
             detail={"value": "Yash", "method": "profile"}, duration_ms=5.0)
    buf.emit("FormFill", "fill_field", target="Email", outcome="failure",
             detail={"error": "field not found"}, duration_ms=3.0)
    return buf


class TestEventStorage:
    def test_flush_events(self, store, sample_buffer):
        store.flush_events(sample_buffer)
        events = store.get_events(sample_buffer.run_id)
        assert len(events) == 3

    def test_get_events_by_category(self, store, sample_buffer):
        store.flush_events(sample_buffer)
        events = store.get_events(sample_buffer.run_id, category="FormFill")
        assert len(events) == 2
        assert all(e["category"] == "FormFill" for e in events)

    def test_get_events_empty_run(self, store):
        events = store.get_events("nonexistent")
        assert events == []

    def test_flush_with_ood_flag(self, store, sample_buffer):
        store.flush_events(sample_buffer, ood=True)
        events = store.get_events(sample_buffer.run_id)
        assert all(e["ood"] == 1 for e in events)


class TestReportStorage:
    def test_save_and_get_report(self, store):
        store.save_report(
            run_id="abc123", company="ASOS", role="Analyst", outcome="applied",
            event_count=42, narrative="I filled the form...",
            pdf_path="/tmp/report.pdf",
            verbalization_rates={"FormFill": 1.0, "Navigation": 0.9},
            overall_rate=0.95, anomaly_count=1, retried=False,
        )
        report = store.get_report("abc123")
        assert report["company"] == "ASOS"
        assert report["narrative"] == "I filled the form..."
        assert json.loads(report["verbalization_rates"])["FormFill"] == 1.0
        assert report["overall_rate"] == 0.95
        assert report["anomaly_count"] == 1

    def test_list_reports(self, store):
        for i in range(3):
            store.save_report(
                run_id=f"run_{i}", company=f"Co{i}", role="Dev", outcome="applied",
                event_count=10, narrative="...", pdf_path=None,
                verbalization_rates={}, overall_rate=1.0, anomaly_count=0, retried=False,
            )
        reports = store.list_reports(limit=2)
        assert len(reports) == 2

    def test_get_report_not_found(self, store):
        assert store.get_report("nonexistent") is None


class TestDPOPairStorage:
    def test_save_and_get_pairs(self, store):
        store.save_dpo_pair(
            run_id="abc123", category="FormFill",
            chosen="correct text", rejected="hallucinated text",
            source="automated",
        )
        pairs = store.get_dpo_pairs(limit=10)
        assert len(pairs) == 1
        assert pairs[0]["chosen"] == "correct text"
        assert pairs[0]["source"] == "automated"

    def test_get_pairs_by_source(self, store):
        store.save_dpo_pair(run_id="r1", category="FormFill",
                            chosen="a", rejected="b", source="automated")
        store.save_dpo_pair(run_id="r2", category="Navigation",
                            chosen="c", rejected="d", source="manual")
        auto = store.get_dpo_pairs(source="automated")
        assert len(auto) == 1
        manual = store.get_dpo_pairs(source="manual")
        assert len(manual) == 1

    def test_pair_count(self, store):
        for i in range(5):
            store.save_dpo_pair(run_id=f"r{i}", category="FormFill",
                                chosen=f"c{i}", rejected=f"r{i}", source="automated")
        assert store.dpo_pair_count() == 5
        assert store.dpo_pair_count(source="automated") == 5
        assert store.dpo_pair_count(source="manual") == 0


class TestFailureQuery:
    def test_query_failures(self, store, sample_buffer):
        store.flush_events(sample_buffer)
        failures = store.get_failures(days=7)
        assert len(failures) == 1
        assert failures[0]["target"] == "Email"
        assert failures[0]["outcome"] == "failure"

    def test_query_failures_by_category(self, store, sample_buffer):
        store.flush_events(sample_buffer)
        failures = store.get_failures(category="PreScreen", days=7)
        assert len(failures) == 0
        failures = store.get_failures(category="FormFill", days=7)
        assert len(failures) == 1


class TestOODQuery:
    def test_ood_stats(self, store):
        buf_known = IntrospectionBuffer(company="Known", role="Dev")
        buf_known.emit("FormFill", "fill", target="f1", outcome="success", detail={}, duration_ms=1.0)
        store.flush_events(buf_known, ood=False)
        store.save_report(run_id=buf_known.run_id, company="Known", role="Dev",
                          outcome="applied", event_count=1, narrative="...", pdf_path=None,
                          verbalization_rates={"FormFill": 1.0}, overall_rate=1.0,
                          anomaly_count=0, retried=False)

        buf_ood = IntrospectionBuffer(company="OOD", role="Dev")
        buf_ood.emit("FormFill", "fill", target="f2", outcome="success", detail={}, duration_ms=1.0)
        store.flush_events(buf_ood, ood=True)
        store.save_report(run_id=buf_ood.run_id, company="OOD", role="Dev",
                          outcome="applied", event_count=1, narrative="...", pdf_path=None,
                          verbalization_rates={"FormFill": 0.8}, overall_rate=0.8,
                          anomaly_count=0, retried=False)

        stats = store.ood_stats()
        assert stats["known_avg_rate"] == 1.0
        assert stats["ood_avg_rate"] == 0.8


class TestRetention:
    def test_cleanup_old_events(self, store):
        buf = IntrospectionBuffer(company="Old", role="Dev")
        buf.emit("FormFill", "fill", target="f1", outcome="success", detail={}, duration_ms=1.0)
        store.flush_events(buf)
        # Manually backdate the created_at
        with store._get_conn() as conn:
            conn.execute("UPDATE events SET created_at = ?", (time.time() - 100 * 86400,))
        deleted = store.cleanup(retention_days=90)
        assert deleted > 0
        assert store.get_events(buf.run_id) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobpulse.introspection.store'`

- [ ] **Step 3: Implement store.py**

```python
# jobpulse/introspection/store.py
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "introspection.db",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT,
    outcome TEXT NOT NULL,
    detail TEXT,
    duration_ms REAL,
    ood INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(run_id, category);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    outcome TEXT NOT NULL,
    event_count INTEGER,
    narrative TEXT NOT NULL,
    pdf_path TEXT,
    verbalization_rates TEXT,
    overall_rate REAL,
    anomaly_count INTEGER,
    retried INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS dpo_pairs (
    pair_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    category TEXT NOT NULL,
    chosen TEXT NOT NULL,
    rejected TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class IntrospectionStore:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DEFAULT_DB
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_conn() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Events ──

    def flush_events(self, buffer, *, ood: bool = False) -> int:
        rows = []
        now = time.time()
        for ev in buffer.events:
            rows.append((
                ev.event_id, buffer.run_id, ev.timestamp, ev.category,
                ev.action, ev.target, ev.outcome, json.dumps(ev.detail),
                ev.duration_ms, 1 if ood else 0, now,
            ))
        with self._get_conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO events "
                "(event_id, run_id, timestamp, category, action, target, "
                "outcome, detail, duration_ms, ood, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def get_events(self, run_id: str, *, category: str | None = None) -> list[dict]:
        sql = "SELECT * FROM events WHERE run_id = ?"
        params: list = [run_id]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY timestamp"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_failures(self, *, category: str | None = None, days: int = 7) -> list[dict]:
        cutoff = time.time() - days * 86400
        sql = "SELECT * FROM events WHERE outcome = 'failure' AND created_at >= ?"
        params: list = [cutoff]
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY created_at DESC"
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ── Reports ──

    def save_report(self, *, run_id: str, company: str, role: str, outcome: str,
                    event_count: int, narrative: str, pdf_path: str | None,
                    verbalization_rates: dict, overall_rate: float,
                    anomaly_count: int, retried: bool) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO reports "
                "(run_id, company, role, outcome, event_count, narrative, pdf_path, "
                "verbalization_rates, overall_rate, anomaly_count, retried, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, company, role, outcome, event_count, narrative, pdf_path,
                 json.dumps(verbalization_rates), overall_rate, anomaly_count,
                 1 if retried else 0, time.time()),
            )

    def get_report(self, run_id: str) -> dict | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM reports WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_reports(self, *, limit: int = 20) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT run_id, company, role, outcome, event_count, overall_rate, "
                "anomaly_count, created_at FROM reports ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── DPO Pairs ──

    def save_dpo_pair(self, *, run_id: str, category: str,
                      chosen: str, rejected: str, source: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO dpo_pairs (pair_id, run_id, category, chosen, rejected, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex[:16], run_id, category, chosen, rejected, source, time.time()),
            )

    def get_dpo_pairs(self, *, source: str | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM dpo_pairs"
        params: list = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def dpo_pair_count(self, *, source: str | None = None) -> int:
        sql = "SELECT COUNT(*) FROM dpo_pairs"
        params: list = []
        if source:
            sql += " WHERE source = ?"
            params.append(source)
        with self._get_conn() as conn:
            return conn.execute(sql, params).fetchone()[0]

    # ── OOD Stats ──

    def ood_stats(self) -> dict:
        with self._get_conn() as conn:
            known = conn.execute(
                "SELECT AVG(r.overall_rate) FROM reports r "
                "JOIN events e ON r.run_id = e.run_id WHERE e.ood = 0"
            ).fetchone()[0]
            ood = conn.execute(
                "SELECT AVG(r.overall_rate) FROM reports r "
                "JOIN events e ON r.run_id = e.run_id WHERE e.ood = 1"
            ).fetchone()[0]
        return {
            "known_avg_rate": known or 0.0,
            "ood_avg_rate": ood or 0.0,
        }

    # ── Stats (rolling averages) ──

    def category_stats(self, *, days: int = 7) -> dict[str, float]:
        cutoff = time.time() - days * 86400
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT verbalization_rates FROM reports WHERE created_at >= ?",
                (cutoff,),
            ).fetchall()
        if not rows:
            return {}
        from collections import defaultdict
        totals: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            rates = json.loads(row["verbalization_rates"])
            for cat, rate in rates.items():
                totals[cat].append(rate)
        return {cat: sum(vals) / len(vals) for cat, vals in totals.items()}

    # ── Retention ──

    def cleanup(self, *, retention_days: int = 90) -> int:
        cutoff = time.time() - retention_days * 86400
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
            events_deleted = cur.rowcount
            conn.execute("DELETE FROM reports WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM dpo_pairs WHERE created_at < ?", (cutoff,))
        return events_deleted
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_store.py -v`
Expected: all 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/store.py tests/jobpulse/test_introspection/test_store.py
git commit -m "feat(introspection): add SQLite store for events, reports, DPO pairs"
```

---

### Task 3: Validator — Coverage Checker

**Files:**
- Create: `jobpulse/introspection/validator.py`
- Test: `tests/jobpulse/test_introspection/test_validator.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_validator.py
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.introspection.events import IntrospectionEvent
from jobpulse.introspection.validator import (
    check_coverage,
    check_expected_actions,
    EXPECTED_ACTIONS,
)


def _make_event(category: str, action: str, target: str = "", outcome: str = "success") -> IntrospectionEvent:
    return IntrospectionEvent(
        category=category, action=action, target=target,
        outcome=outcome, detail={}, duration_ms=1.0,
    )


class TestCoverageChecker:
    def test_full_coverage(self):
        events = [
            _make_event("FormFill", "fill_field", "Name"),
            _make_event("FormFill", "fill_field", "Email"),
        ]
        narrative = (
            "I filled the Name field with 'Yash' using profile data. "
            "Then I filled the Email field with the address from profile."
        )
        with patch("jobpulse.introspection.validator._llm_check_coverage") as mock:
            mock.return_value = {"Name": True, "Email": True}
            result = check_coverage(events, narrative)
        assert result["overall_rate"] == 1.0
        assert result["missed_events"] == []

    def test_partial_coverage(self):
        events = [
            _make_event("FormFill", "fill_field", "Name"),
            _make_event("FormFill", "fill_field", "Email"),
            _make_event("FormFill", "fill_field", "Phone"),
        ]
        narrative = "I filled Name and Email fields."
        with patch("jobpulse.introspection.validator._llm_check_coverage") as mock:
            mock.return_value = {"Name": True, "Email": True, "Phone": False}
            result = check_coverage(events, narrative)
        assert result["overall_rate"] == pytest.approx(2 / 3, abs=0.01)
        assert len(result["missed_events"]) == 1
        assert result["missed_events"][0].target == "Phone"

    def test_per_category_rates(self):
        events = [
            _make_event("FormFill", "fill_field", "Name"),
            _make_event("FormFill", "fill_field", "Email"),
            _make_event("Navigation", "page_load", "/apply"),
        ]
        with patch("jobpulse.introspection.validator._llm_check_coverage") as mock:
            mock.return_value = {"Name": True, "Email": False, "/apply": True}
            result = check_coverage(events, "narrative text")
        assert result["category_rates"]["FormFill"] == 0.5
        assert result["category_rates"]["Navigation"] == 1.0


class TestExpectedActions:
    def test_successful_submit_missing_hook(self):
        events = [
            _make_event("Submission", "submit_attempt", outcome="success"),
            _make_event("Learning", "signal_emit", "optimization"),
            _make_event("Learning", "experience_store"),
        ]
        missing = check_expected_actions(events, outcome="applied")
        action_names = [m["action"] for m in missing]
        assert "post_apply_hook" in action_names

    def test_successful_submit_all_present(self):
        events = [
            _make_event("Submission", "submit_attempt", outcome="success"),
            _make_event("Hooks", "hook_fire", "post_apply_hook"),
            _make_event("Hooks", "correction_capture"),
            _make_event("Learning", "signal_emit", "strategy_reflect"),
            _make_event("Learning", "signal_emit", "optimization"),
            _make_event("Learning", "experience_store"),
        ]
        missing = check_expected_actions(events, outcome="applied")
        assert missing == []

    def test_gate_kill_expects_learning(self):
        events = [
            _make_event("PreScreen", "gate_kill", "gate_2"),
        ]
        missing = check_expected_actions(events, outcome="gate_killed")
        action_names = [m["action"] for m in missing]
        assert "gate_effectiveness" in action_names

    def test_dry_run_expects_nothing(self):
        events = [
            _make_event("Submission", "dry_run_review", outcome="success"),
        ]
        missing = check_expected_actions(events, outcome="dry_run")
        assert missing == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_validator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement validator.py**

```python
# jobpulse/introspection/validator.py
from __future__ import annotations

import json
from collections import defaultdict

from shared.agents import get_llm, smart_llm_call
from jobpulse.introspection.events import IntrospectionEvent

EXPECTED_ACTIONS: dict[str, list[dict]] = {
    "applied": [
        {"category": "Hooks", "action": "hook_fire", "target": "post_apply_hook"},
        {"category": "Hooks", "action": "correction_capture"},
        {"category": "Learning", "action": "signal_emit", "target_contains": "strategy_reflect"},
        {"category": "Learning", "action": "signal_emit", "target_contains": "optimization"},
        {"category": "Learning", "action": "experience_store"},
    ],
    "dry_run": [],
    "gate_killed": [
        {"category": "Learning", "action": "gate_effectiveness"},
    ],
    "failed": [
        {"category": "Hooks", "action": "correction_capture"},
    ],
    "nav_stuck": [
        {"category": "Learning", "action": "nav_learner_update"},
    ],
}


def check_coverage(events: list[IntrospectionEvent], narrative: str) -> dict:
    if not events:
        return {"overall_rate": 1.0, "missed_events": [], "category_rates": {}}

    coverage_map = _llm_check_coverage(events, narrative)

    missed = []
    cat_hits: dict[str, list[bool]] = defaultdict(list)
    for ev in events:
        key = ev.target or f"{ev.action}"
        covered = coverage_map.get(key, False)
        cat_hits[ev.category].append(covered)
        if not covered:
            missed.append(ev)

    total = len(events)
    covered_count = total - len(missed)
    category_rates = {
        cat: sum(hits) / len(hits) if hits else 1.0
        for cat, hits in cat_hits.items()
    }

    return {
        "overall_rate": covered_count / total if total else 1.0,
        "missed_events": missed,
        "category_rates": category_rates,
    }


def _llm_check_coverage(events: list[IntrospectionEvent], narrative: str) -> dict[str, bool]:
    event_keys = []
    for ev in events:
        key = ev.target or f"{ev.action}"
        event_keys.append(key)

    prompt = (
        "Given this narrative report and list of pipeline events, determine which events "
        "are mentioned (covered) in the narrative. Return a JSON object mapping each event "
        "key to true (covered) or false (not covered).\n\n"
        f"Events: {json.dumps(event_keys)}\n\n"
        f"Narrative:\n{narrative}\n\n"
        "Return ONLY valid JSON, no markdown."
    )

    llm = get_llm(model="gpt-4o-mini", temperature=0)
    result = smart_llm_call(llm, prompt, timeout=30)
    try:
        return json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return {k: True for k in event_keys}


def check_expected_actions(events: list[IntrospectionEvent], *, outcome: str) -> list[dict]:
    expected = EXPECTED_ACTIONS.get(outcome, [])
    if not expected:
        return []

    missing = []
    for exp in expected:
        found = False
        for ev in events:
            if ev.category != exp["category"]:
                continue
            if ev.action != exp["action"]:
                continue
            if "target" in exp and ev.target != exp["target"]:
                continue
            if "target_contains" in exp and exp["target_contains"] not in ev.target:
                continue
            found = True
            break
        if not found:
            missing.append(exp)
    return missing
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_validator.py -v`
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/validator.py tests/jobpulse/test_introspection/test_validator.py
git commit -m "feat(introspection): add coverage validator with expected action checklist"
```

---

### Task 4: Verbalizer — LLM Narrative Generation

**Files:**
- Create: `jobpulse/introspection/verbalizer.py`
- Test: `tests/jobpulse/test_introspection/test_verbalizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_verbalizer.py
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.introspection.events import IntrospectionEvent, IntrospectionBuffer
from jobpulse.introspection.verbalizer import verbalize, _build_prompt, VERBALIZER_SYSTEM_PROMPT


def _make_buffer():
    buf = IntrospectionBuffer(company="ASOS", role="Data Analyst")
    buf.emit("PreScreen", "gate_pass", target="gate_0", outcome="success",
             detail={"reason": "title match"}, duration_ms=1.0)
    buf.emit("PreScreen", "gate_pass", target="gate_2", outcome="success",
             detail={"matched": 4, "of": 5}, duration_ms=2.0)
    buf.emit("FormFill", "fill_field", target="First Name", outcome="success",
             detail={"value": "Yash", "method": "profile"}, duration_ms=5.0)
    buf.emit("FormFill", "fill_field", target="Email", outcome="success",
             detail={"value": "test@example.com", "method": "profile"}, duration_ms=3.0)
    return buf


class TestBuildPrompt:
    def test_prompt_contains_all_events(self):
        buf = _make_buffer()
        prompt = _build_prompt(buf.events, outcome="applied", negative_examples=[])
        assert "gate_0" in prompt
        assert "gate_2" in prompt
        assert "First Name" in prompt
        assert "Email" in prompt

    def test_prompt_includes_negative_examples(self):
        buf = _make_buffer()
        negatives = ["Do not say CorrectionCapture fired when only post_apply_hook is in the log."]
        prompt = _build_prompt(buf.events, outcome="applied", negative_examples=negatives)
        assert "CorrectionCapture" in prompt

    def test_system_prompt_exhaustive_rule(self):
        assert "EVERY action" in VERBALIZER_SYSTEM_PROMPT
        assert "No summarizing" in VERBALIZER_SYSTEM_PROMPT


class TestVerbalize:
    def test_verbalize_returns_narrative(self):
        buf = _make_buffer()
        fake_narrative = (
            "I started by running PreScreen on the ASOS Data Analyst role. "
            "Gate 0 passed with a title match. Gate 2 passed with 4 of 5 must-haves. "
            "I then filled the First Name field with 'Yash' from profile data. "
            "I filled the Email field with 'test@example.com' from profile."
        )
        with patch("jobpulse.introspection.verbalizer.smart_llm_call") as mock_llm, \
             patch("jobpulse.introspection.verbalizer.check_coverage") as mock_cov:
            mock_llm.return_value = fake_narrative
            mock_cov.return_value = {
                "overall_rate": 1.0,
                "missed_events": [],
                "category_rates": {"PreScreen": 1.0, "FormFill": 1.0},
            }
            result = verbalize(buf.events, outcome="applied", negative_examples=[])
        assert result["narrative"] == fake_narrative
        assert result["overall_rate"] == 1.0
        assert result["retried"] is False

    def test_verbalize_retries_on_low_coverage(self):
        buf = _make_buffer()
        incomplete = "I filled the Name field."
        complete = (
            "I ran PreScreen gate_0 (title match, passed). Gate_2 passed (4/5). "
            "Filled First Name with Yash. Filled Email with test@example.com."
        )
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return incomplete if call_count == 1 else complete

        cov_results = [
            {"overall_rate": 0.25, "missed_events": [buf.events[0]], "category_rates": {}},
            {"overall_rate": 1.0, "missed_events": [], "category_rates": {}},
        ]

        with patch("jobpulse.introspection.verbalizer.smart_llm_call", side_effect=side_effect), \
             patch("jobpulse.introspection.verbalizer.check_coverage", side_effect=cov_results):
            result = verbalize(buf.events, outcome="applied", negative_examples=[])
        assert result["retried"] is True
        assert result["overall_rate"] == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_verbalizer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement verbalizer.py**

```python
# jobpulse/introspection/verbalizer.py
from __future__ import annotations

import json

from shared.agents import get_llm, smart_llm_call
from jobpulse.introspection.events import IntrospectionEvent, CATEGORIES
from jobpulse.introspection.validator import check_coverage, check_expected_actions

VERBALIZER_SYSTEM_PROMPT = (
    "You are a pipeline agent writing a debrief of a job application you just completed. "
    "Write in first person. Describe every single action you took, in the order you took it.\n\n"
    "For each action, describe:\n"
    "- What you did and why\n"
    "- What the result was\n"
    "- If something failed, what you tried as fallback\n"
    "- If you learned something, what was stored and where\n\n"
    "CRITICAL: You must mention EVERY action in the log. No summarizing, no grouping, "
    "no 'and N others.' If 14 fields were filled, describe all 14 — what the field was, "
    "what value was entered, how it was resolved (cache/LLM/semantic match/vision), "
    "and whether it succeeded.\n\n"
    "The reader should be able to reconstruct the EXACT sequence of everything that "
    "happened without looking at the raw log.\n\n"
    "Rules:\n"
    "- Only report actions present in the log. Never invent actions.\n"
    "- If a category has zero events, say 'No actions recorded for [category].'\n"
    "- Flag anomalies: unusually slow actions, repeated failures, missing downstream signals.\n"
    "- Check the expected actions checklist and report anything that should have fired but didn't.\n\n"
    "Organize the report by category in this order: "
    "PreScreen, CVGen, Navigation, FormFill, Screening, Submission, Hooks, Learning."
)

CATEGORY_ORDER = [
    "PreScreen", "CVGen", "Navigation", "FormFill",
    "Screening", "Submission", "Hooks", "Learning",
]


def _build_prompt(events: list[IntrospectionEvent], *, outcome: str,
                  negative_examples: list[str],
                  missed_events: list[IntrospectionEvent] | None = None) -> str:
    sections = []
    grouped: dict[str, list[dict]] = {}
    for ev in events:
        grouped.setdefault(ev.category, []).append(ev.to_dict())

    for cat in CATEGORY_ORDER:
        cat_events = grouped.get(cat, [])
        if cat_events:
            sections.append(f"\n## {cat} ({len(cat_events)} events)")
            for ev in cat_events:
                sections.append(json.dumps(ev, default=str))
        else:
            sections.append(f"\n## {cat} (0 events)")

    expected_missing = check_expected_actions(events, outcome=outcome)
    if expected_missing:
        sections.append("\n## EXPECTED BUT MISSING")
        for m in expected_missing:
            sections.append(json.dumps(m))

    prompt = f"Application outcome: {outcome}\n\nAction log:\n" + "\n".join(sections)

    if negative_examples:
        prompt += "\n\n## DO NOT hallucinate these patterns:\n"
        for neg in negative_examples:
            prompt += f"- {neg}\n"

    if missed_events:
        prompt += "\n\n## RETRY: The following events were NOT covered in your previous attempt. "
        prompt += "You MUST include them this time:\n"
        for ev in missed_events:
            prompt += f"- [{ev.category}] {ev.action}: {ev.target} ({ev.outcome})\n"

    return prompt


def verbalize(events: list[IntrospectionEvent], *, outcome: str,
              negative_examples: list[str]) -> dict:
    llm = get_llm(model="gpt-4o-mini", temperature=0.3)

    prompt = _build_prompt(events, outcome=outcome, negative_examples=negative_examples)
    narrative = smart_llm_call(llm, prompt, system=VERBALIZER_SYSTEM_PROMPT, timeout=60)

    coverage = check_coverage(events, narrative)
    retried = False

    if coverage["overall_rate"] < 1.0 and coverage["missed_events"]:
        retried = True
        retry_prompt = _build_prompt(
            events, outcome=outcome, negative_examples=negative_examples,
            missed_events=coverage["missed_events"],
        )
        narrative = smart_llm_call(llm, retry_prompt, system=VERBALIZER_SYSTEM_PROMPT, timeout=60)
        coverage = check_coverage(events, narrative)

        if coverage["missed_events"]:
            addendum = "\n\n---\nThe following actions were not covered in the narrative above:\n"
            for ev in coverage["missed_events"]:
                addendum += (
                    f"- [{ev.category}] {ev.action}: {ev.target} "
                    f"(outcome={ev.outcome}, detail={json.dumps(ev.detail)})\n"
                )
            narrative += addendum
            coverage["overall_rate"] = 1.0
            coverage["missed_events"] = []

    return {
        "narrative": narrative,
        "overall_rate": coverage["overall_rate"],
        "category_rates": coverage.get("category_rates", {}),
        "anomaly_count": len(check_expected_actions(events, outcome=outcome)),
        "retried": retried,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_verbalizer.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/verbalizer.py tests/jobpulse/test_introspection/test_verbalizer.py
git commit -m "feat(introspection): add LLM verbalizer with exhaustive coverage enforcement"
```

---

### Task 5: DPO Pair Manager

**Files:**
- Create: `jobpulse/introspection/dpo.py`
- Test: `tests/jobpulse/test_introspection/test_dpo.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_dpo.py
import pytest
from jobpulse.introspection.store import IntrospectionStore
from jobpulse.introspection.dpo import DPOManager


@pytest.fixture
def dpo(tmp_path):
    store = IntrospectionStore(db_path=str(tmp_path / "introspection.db"))
    return DPOManager(store)


class TestAutomatedPairs:
    def test_no_pair_when_identical(self, dpo):
        raw = "I filled the Name field."
        cleaned = "I filled the Name field."
        pair = dpo.generate_automated_pair(run_id="r1", category="FormFill",
                                           raw=raw, cleaned=cleaned)
        assert pair is None

    def test_pair_generated_when_different(self, dpo):
        raw = "I filled the Name field. CorrectionCapture fired successfully."
        cleaned = "I filled the Name field."
        pair = dpo.generate_automated_pair(run_id="r1", category="FormFill",
                                           raw=raw, cleaned=cleaned)
        assert pair is not None
        assert pair["chosen"] == cleaned
        assert pair["rejected"] == raw
        assert pair["source"] == "automated"


class TestManualCorrections:
    def test_record_manual_correction(self, dpo):
        dpo.record_manual_correction(
            run_id="r1",
            correction="FormFill section says field was skipped but it used vision fallback",
        )
        pairs = dpo.store.get_dpo_pairs(source="manual")
        assert len(pairs) == 1


class TestNegativeExamples:
    def test_extract_negatives_empty(self, dpo):
        negatives = dpo.get_negative_examples()
        assert negatives == []

    def test_extract_negatives_from_pairs(self, dpo):
        for i in range(5):
            dpo.store.save_dpo_pair(
                run_id=f"r{i}", category="FormFill",
                chosen=f"correct {i}", rejected=f"hallucinated CorrectionCapture {i}",
                source="automated",
            )
        negatives = dpo.get_negative_examples()
        assert len(negatives) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_dpo.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement dpo.py**

```python
# jobpulse/introspection/dpo.py
from __future__ import annotations

from collections import Counter

from jobpulse.introspection.store import IntrospectionStore


class DPOManager:
    def __init__(self, store: IntrospectionStore):
        self.store = store

    def generate_automated_pair(self, *, run_id: str, category: str,
                                raw: str, cleaned: str) -> dict | None:
        if raw.strip() == cleaned.strip():
            return None
        self.store.save_dpo_pair(
            run_id=run_id, category=category,
            chosen=cleaned, rejected=raw, source="automated",
        )
        return {"run_id": run_id, "category": category,
                "chosen": cleaned, "rejected": raw, "source": "automated"}

    def record_manual_correction(self, *, run_id: str, correction: str) -> None:
        report = self.store.get_report(run_id)
        original = report["narrative"] if report else ""
        self.store.save_dpo_pair(
            run_id=run_id, category="manual_correction",
            chosen=correction, rejected=original, source="manual",
        )

    def get_negative_examples(self, *, limit: int = 10) -> list[str]:
        pairs = self.store.get_dpo_pairs(limit=100)
        if not pairs:
            return []

        fragments: list[str] = []
        for pair in pairs:
            rejected = pair["rejected"]
            chosen = pair["chosen"]
            if len(rejected) > len(chosen):
                diff_fragment = rejected.replace(chosen, "").strip()
                if diff_fragment and len(diff_fragment) < 200:
                    fragments.append(diff_fragment)

        counts = Counter(fragments)
        return [
            f"Do not include: '{frag}'" for frag, _ in counts.most_common(limit) if frag
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_dpo.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/dpo.py tests/jobpulse/test_introspection/test_dpo.py
git commit -m "feat(introspection): add DPO pair manager with automated + manual correction"
```

---

### Task 6: PDF Renderer (ReportLab)

**Files:**
- Create: `jobpulse/introspection/renderer.py`
- Test: `tests/jobpulse/test_introspection/test_renderer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_renderer.py
import os
import pytest
from jobpulse.introspection.renderer import render_pdf


@pytest.fixture
def sample_report():
    return {
        "run_id": "abc123def456",
        "company": "ASOS",
        "role": "Data Analyst",
        "outcome": "applied",
        "event_count": 42,
        "narrative": (
            "## PreScreen\n\n"
            "I ran PreScreen on the ASOS Data Analyst role. Gate 0 passed with a title match. "
            "Gate 2 passed with 4 of 5 must-haves matched.\n\n"
            "## FormFill\n\n"
            "I filled the First Name field with 'Yash' from profile data. "
            "I filled the Email field with the address from profile. "
            "The Years of Experience dropdown was invisible to the a11y tree. "
            "I fell back to vision tier, which identified it and selected '2-3 years.'"
        ),
        "verbalization_rates": {"PreScreen": 1.0, "FormFill": 0.93},
        "overall_rate": 0.96,
        "anomaly_count": 1,
        "retried": False,
        "duration_seconds": 47.3,
    }


class TestPDFRenderer:
    def test_render_creates_file(self, tmp_path, sample_report):
        pdf_path = render_pdf(sample_report, output_dir=str(tmp_path))
        assert os.path.exists(pdf_path)
        assert pdf_path.endswith(".pdf")

    def test_render_filename_format(self, tmp_path, sample_report):
        pdf_path = render_pdf(sample_report, output_dir=str(tmp_path))
        filename = os.path.basename(pdf_path)
        assert "ASOS" in filename
        assert "Data_Analyst" in filename

    def test_render_pdf_readable(self, tmp_path, sample_report):
        pdf_path = render_pdf(sample_report, output_dir=str(tmp_path))
        with open(pdf_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"

    def test_render_with_no_anomalies(self, tmp_path, sample_report):
        sample_report["anomaly_count"] = 0
        pdf_path = render_pdf(sample_report, output_dir=str(tmp_path))
        assert os.path.exists(pdf_path)

    def test_render_long_narrative(self, tmp_path, sample_report):
        sample_report["narrative"] = "I filled field. " * 500
        pdf_path = render_pdf(sample_report, output_dir=str(tmp_path))
        size = os.path.getsize(pdf_path)
        assert size > 1000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_renderer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement renderer.py**

```python
# jobpulse/introspection/renderer.py
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

_TEAL = HexColor("#1a5276")
_LIGHT_GRAY = HexColor("#f2f3f4")
_GREEN = HexColor("#27ae60")
_RED = HexColor("#c0392b")
_ORANGE = HexColor("#f39c12")

_FONT = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

_TITLE_STYLE = ParagraphStyle(
    "Title", fontName=_FONT_BOLD, fontSize=16, alignment=TA_CENTER,
    textColor=_TEAL, spaceAfter=4,
)
_HEADER_STYLE = ParagraphStyle(
    "Header", fontName=_FONT, fontSize=9, alignment=TA_CENTER,
    textColor=HexColor("#555555"), spaceAfter=8,
)
_SECTION_STYLE = ParagraphStyle(
    "Section", fontName=_FONT_BOLD, fontSize=11, textColor=_TEAL,
    spaceBefore=10, spaceAfter=4,
)
_BODY_STYLE = ParagraphStyle(
    "Body", fontName=_FONT, fontSize=9, alignment=TA_JUSTIFY,
    leading=13, spaceAfter=6,
)
_FOOTER_STYLE = ParagraphStyle(
    "Footer", fontName=_FONT, fontSize=8, alignment=TA_CENTER,
    textColor=HexColor("#888888"), spaceBefore=10,
)
_RATE_STYLE = ParagraphStyle(
    "Rate", fontName=_FONT, fontSize=8, textColor=HexColor("#666666"),
    spaceAfter=2,
)


def _rate_color(rate: float) -> str:
    if rate >= 0.95:
        return _GREEN.hexval()
    if rate >= 0.80:
        return _ORANGE.hexval()
    return _RED.hexval()


def _safe_text(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text


def render_pdf(report: dict, *, output_dir: str | None = None) -> str:
    if output_dir is None:
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "introspection", "reports",
        )
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    company_safe = re.sub(r"[^\w]", "_", report["company"])
    role_safe = re.sub(r"[^\w]", "_", report["role"])
    filename = f"{date_str}_{company_safe}_{role_safe}.pdf"
    pdf_path = os.path.join(output_dir, filename)

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )

    story = []

    story.append(Paragraph("Pipeline Introspection Report", _TITLE_STYLE))
    header_text = (
        f"{_safe_text(report['company'])} | {_safe_text(report['role'])} | "
        f"Outcome: {report['outcome']} | Events: {report.get('event_count', '?')} | "
        f"Duration: {report.get('duration_seconds', 0):.1f}s"
    )
    story.append(Paragraph(header_text, _HEADER_STYLE))
    story.append(HRFlowable(width="100%", thickness=1, color=_TEAL))
    story.append(Spacer(1, 4 * mm))

    narrative = report.get("narrative", "")
    sections = re.split(r"(?m)^##\s+", narrative)

    for section in sections:
        section = section.strip()
        if not section:
            continue
        lines = section.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        story.append(Paragraph(_safe_text(title), _SECTION_STYLE))
        if body:
            for paragraph in body.split("\n\n"):
                paragraph = paragraph.strip()
                if paragraph:
                    story.append(Paragraph(_safe_text(paragraph), _BODY_STYLE))

        rates = report.get("verbalization_rates", {})
        if title in rates:
            rate = rates[title]
            color = _rate_color(rate)
            story.append(Paragraph(
                f'Verbalization rate: <font color="{color}">{rate:.0%}</font>',
                _RATE_STYLE,
            ))

    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=_TEAL))

    overall = report.get("overall_rate", 0)
    anomalies = report.get("anomaly_count", 0)
    retried = "Yes" if report.get("retried") else "No"
    color = _rate_color(overall)
    footer = (
        f'Overall Verbalization: <font color="{color}">{overall:.0%}</font> | '
        f'Anomalies: {anomalies} | Retried: {retried}'
    )
    story.append(Paragraph(footer, _FOOTER_STYLE))

    doc.build(story)
    return pdf_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_renderer.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/renderer.py tests/jobpulse/test_introspection/test_renderer.py
git commit -m "feat(introspection): add ReportLab PDF renderer with agent-voice layout"
```

---

### Task 7: CLI Subcommands

**Files:**
- Create: `jobpulse/introspection/cli.py`
- Modify: `jobpulse/runner.py:421` (add `introspect` command routing)
- Test: `tests/jobpulse/test_introspection/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_cli.py
import json
import pytest
from unittest.mock import patch
from jobpulse.introspection.store import IntrospectionStore
from jobpulse.introspection.cli import run_cli


@pytest.fixture
def store(tmp_path):
    return IntrospectionStore(db_path=str(tmp_path / "introspection.db"))


@pytest.fixture
def populated_store(store):
    store.save_report(
        run_id="run_abc", company="ASOS", role="Analyst", outcome="applied",
        event_count=42, narrative="I filled the form completely.", pdf_path="/tmp/r.pdf",
        verbalization_rates={"FormFill": 1.0, "Navigation": 0.9},
        overall_rate=0.95, anomaly_count=1, retried=False,
    )
    store.save_report(
        run_id="run_def", company="Google", role="SWE", outcome="gate_killed",
        event_count=6, narrative="Gate 2 killed this application.", pdf_path=None,
        verbalization_rates={"PreScreen": 1.0},
        overall_rate=1.0, anomaly_count=0, retried=False,
    )
    return store


class TestCLIList:
    def test_list_reports(self, populated_store, capsys):
        run_cli(["list"], store=populated_store)
        out = capsys.readouterr().out
        assert "ASOS" in out
        assert "Google" in out

    def test_list_empty(self, store, capsys):
        run_cli(["list"], store=store)
        out = capsys.readouterr().out
        assert "No reports" in out


class TestCLIShow:
    def test_show_report(self, populated_store, capsys):
        run_cli(["show", "run_abc"], store=populated_store)
        out = capsys.readouterr().out
        assert "I filled the form" in out
        assert "ASOS" in out

    def test_show_not_found(self, store, capsys):
        run_cli(["show", "nonexistent"], store=store)
        out = capsys.readouterr().out
        assert "not found" in out.lower()


class TestCLILast:
    def test_last_report(self, populated_store, capsys):
        run_cli(["last"], store=populated_store)
        out = capsys.readouterr().out
        assert "Google" in out or "ASOS" in out


class TestCLIStats:
    def test_stats(self, populated_store, capsys):
        run_cli(["stats"], store=populated_store)
        out = capsys.readouterr().out
        assert "FormFill" in out or "PreScreen" in out or "No data" in out


class TestCLICorrect:
    def test_correct_stores_pair(self, populated_store, capsys):
        run_cli(["correct", "run_abc", "FormFill was wrong about Email"],
                store=populated_store)
        pairs = populated_store.get_dpo_pairs(source="manual")
        assert len(pairs) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement cli.py**

```python
# jobpulse/introspection/cli.py
from __future__ import annotations

import json
import sys
from datetime import datetime

from jobpulse.introspection.store import IntrospectionStore
from jobpulse.introspection.dpo import DPOManager


def run_cli(args: list[str], *, store: IntrospectionStore | None = None) -> None:
    if store is None:
        store = IntrospectionStore()

    if not args:
        _print_usage()
        return

    cmd = args[0]

    if cmd == "list":
        _cmd_list(store)
    elif cmd == "last":
        _cmd_last(store)
    elif cmd == "show" and len(args) >= 2:
        _cmd_show(store, args[1])
    elif cmd == "failures":
        category = None
        days = 7
        i = 1
        while i < len(args):
            if args[i] == "--category" and i + 1 < len(args):
                category = args[i + 1]
                i += 2
            elif args[i] == "--days" and i + 1 < len(args):
                days = int(args[i + 1])
                i += 2
            else:
                i += 1
        _cmd_failures(store, category=category, days=days)
    elif cmd == "correct" and len(args) >= 3:
        _cmd_correct(store, run_id=args[1], correction=" ".join(args[2:]))
    elif cmd == "stats":
        _cmd_stats(store)
    elif cmd == "ood-report":
        _cmd_ood(store)
    else:
        _print_usage()


def _print_usage() -> None:
    print("Usage: python -m jobpulse.runner introspect <command>")
    print("Commands:")
    print("  list                              List all reports")
    print("  last                              Show most recent report")
    print("  show <run_id>                     Show full report")
    print("  failures [--category X] [--days N] Query failures")
    print("  correct <run_id> <text>           Submit DPO correction")
    print("  stats                             Rolling rate averages")
    print("  ood-report                        Known vs OOD comparison")


def _cmd_list(store: IntrospectionStore) -> None:
    reports = store.list_reports(limit=20)
    if not reports:
        print("No reports found.")
        return
    print(f"{'Run ID':<14} {'Company':<15} {'Role':<20} {'Outcome':<12} {'Rate':>6} {'Events':>7}")
    print("-" * 76)
    for r in reports:
        dt = datetime.fromtimestamp(r["created_at"]).strftime("%m-%d %H:%M")
        print(f"{r['run_id']:<14} {r['company']:<15} {r['role']:<20} "
              f"{r['outcome']:<12} {r.get('overall_rate', 0):>5.0%} {r.get('event_count', 0):>7}")


def _cmd_last(store: IntrospectionStore) -> None:
    reports = store.list_reports(limit=1)
    if not reports:
        print("No reports found.")
        return
    _cmd_show(store, reports[0]["run_id"])


def _cmd_show(store: IntrospectionStore, run_id: str) -> None:
    report = store.get_report(run_id)
    if not report:
        print(f"Report not found: {run_id}")
        return
    print(f"\n{'=' * 60}")
    print(f"  {report['company']} — {report['role']}")
    print(f"  Outcome: {report['outcome']} | Events: {report.get('event_count', '?')}")
    rates = json.loads(report.get("verbalization_rates", "{}"))
    print(f"  Overall rate: {report.get('overall_rate', 0):.0%} | Anomalies: {report.get('anomaly_count', 0)}")
    if rates:
        print(f"  Per-category: {', '.join(f'{k}={v:.0%}' for k, v in rates.items())}")
    print(f"{'=' * 60}\n")
    print(report["narrative"])


def _cmd_failures(store: IntrospectionStore, *, category: str | None, days: int) -> None:
    failures = store.get_failures(category=category, days=days)
    if not failures:
        print(f"No failures in the last {days} days" +
              (f" for {category}" if category else ""))
        return
    print(f"Failures (last {days} days):")
    for f in failures[:50]:
        detail = json.loads(f.get("detail", "{}")) if isinstance(f.get("detail"), str) else f.get("detail", {})
        print(f"  [{f['category']}] {f['action']}: {f['target']} — {detail}")


def _cmd_correct(store: IntrospectionStore, *, run_id: str, correction: str) -> None:
    dpo = DPOManager(store)
    dpo.record_manual_correction(run_id=run_id, correction=correction)
    print(f"Correction recorded for {run_id}")


def _cmd_stats(store: IntrospectionStore) -> None:
    for label, days in [("7-day", 7), ("30-day", 30)]:
        stats = store.category_stats(days=days)
        if not stats:
            print(f"{label}: No data")
            continue
        print(f"\n{label} averages:")
        for cat, rate in sorted(stats.items()):
            flag = " ⚠" if rate < 0.80 else ""
            print(f"  {cat:<15} {rate:.0%}{flag}")


def _cmd_ood(store: IntrospectionStore) -> None:
    stats = store.ood_stats()
    print(f"\nKnown platforms:  avg {stats['known_avg_rate']:.0%} verbalization")
    print(f"OOD platforms:    avg {stats['ood_avg_rate']:.0%} verbalization")
```

- [ ] **Step 4: Add `introspect` command to runner.py**

In `jobpulse/runner.py`, add before the `else: logger.error("Unknown command")` block (around line 421):

```python
    elif command == "introspect":
        from jobpulse.introspection.cli import run_cli

        run_cli(sys.argv[2:])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_cli.py -v`
Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/introspection/cli.py tests/jobpulse/test_introspection/test_cli.py jobpulse/runner.py
git commit -m "feat(introspection): add CLI subcommands and runner integration"
```

---

### Task 8: Pipeline Orchestrator — flush + verbalize + render + deliver

**Files:**
- Update: `jobpulse/introspection/__init__.py` (add `flush_and_report()`)
- Test: `tests/jobpulse/test_introspection/test_integration.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/jobpulse/test_introspection/test_integration.py
import os
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.introspection.events import IntrospectionBuffer
from jobpulse.introspection.store import IntrospectionStore
from jobpulse.introspection import flush_and_report


@pytest.fixture
def store(tmp_path):
    return IntrospectionStore(db_path=str(tmp_path / "introspection.db"))


@pytest.fixture
def sample_buffer():
    buf = IntrospectionBuffer(company="TestCo", role="Engineer")
    buf.emit("PreScreen", "gate_pass", target="gate_0", outcome="success",
             detail={"reason": "title match"}, duration_ms=1.0)
    buf.emit("FormFill", "fill_field", target="Name", outcome="success",
             detail={"value": "Yash", "method": "profile"}, duration_ms=5.0)
    buf.emit("FormFill", "fill_field", target="Email", outcome="success",
             detail={"value": "test@example.com"}, duration_ms=3.0)
    buf.emit("Submission", "submit_attempt", target="submit", outcome="success",
             detail={}, duration_ms=100.0)
    return buf


class TestFlushAndReport:
    def test_full_pipeline(self, store, sample_buffer, tmp_path):
        fake_narrative = (
            "## PreScreen\nGate 0 passed.\n\n"
            "## FormFill\nFilled Name and Email.\n\n"
            "## Submission\nSubmitted successfully."
        )
        with patch("jobpulse.introspection.verbalizer.smart_llm_call", return_value=fake_narrative), \
             patch("jobpulse.introspection.verbalizer.check_coverage") as mock_cov, \
             patch("jobpulse.introspection.send_jobs_document") as mock_send:
            mock_cov.return_value = {
                "overall_rate": 1.0,
                "missed_events": [],
                "category_rates": {"PreScreen": 1.0, "FormFill": 1.0, "Submission": 1.0},
            }
            result = flush_and_report(
                sample_buffer, outcome="applied", store=store,
                output_dir=str(tmp_path),
            )

        assert result["run_id"] == sample_buffer.run_id
        assert result["overall_rate"] == 1.0
        assert os.path.exists(result["pdf_path"])

        events = store.get_events(sample_buffer.run_id)
        assert len(events) == 4

        report = store.get_report(sample_buffer.run_id)
        assert report is not None
        assert report["company"] == "TestCo"

    def test_telegram_delivery_called(self, store, sample_buffer, tmp_path):
        with patch("jobpulse.introspection.verbalizer.smart_llm_call", return_value="report"), \
             patch("jobpulse.introspection.verbalizer.check_coverage") as mock_cov, \
             patch("jobpulse.introspection.send_jobs_document") as mock_send:
            mock_cov.return_value = {"overall_rate": 1.0, "missed_events": [], "category_rates": {}}
            flush_and_report(
                sample_buffer, outcome="applied", store=store,
                output_dir=str(tmp_path), send_telegram=True,
            )
        mock_send.assert_called_once()
        caption = mock_send.call_args[1].get("caption", "") or mock_send.call_args[0][1]
        assert "TestCo" in caption

    def test_telegram_skipped_when_disabled(self, store, sample_buffer, tmp_path):
        with patch("jobpulse.introspection.verbalizer.smart_llm_call", return_value="report"), \
             patch("jobpulse.introspection.verbalizer.check_coverage") as mock_cov, \
             patch("jobpulse.introspection.send_jobs_document") as mock_send:
            mock_cov.return_value = {"overall_rate": 1.0, "missed_events": [], "category_rates": {}}
            flush_and_report(
                sample_buffer, outcome="applied", store=store,
                output_dir=str(tmp_path), send_telegram=False,
            )
        mock_send.assert_not_called()

    def test_empty_buffer_no_report(self, store, tmp_path):
        buf = IntrospectionBuffer(company="Empty", role="Dev")
        result = flush_and_report(buf, outcome="applied", store=store,
                                  output_dir=str(tmp_path))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_integration.py -v`
Expected: FAIL with `ImportError: cannot import name 'flush_and_report'`

- [ ] **Step 3: Add flush_and_report to __init__.py**

Append to `jobpulse/introspection/__init__.py`:

```python
def flush_and_report(
    buffer: IntrospectionBuffer,
    *,
    outcome: str,
    store: "IntrospectionStore | None" = None,
    output_dir: str | None = None,
    send_telegram: bool = True,
    ood: bool = False,
) -> dict | None:
    if not buffer.events:
        return None

    from jobpulse.introspection.store import IntrospectionStore
    from jobpulse.introspection.verbalizer import verbalize
    from jobpulse.introspection.renderer import render_pdf
    from jobpulse.introspection.dpo import DPOManager

    if store is None:
        store = IntrospectionStore()

    store.flush_events(buffer, ood=ood)

    dpo = DPOManager(store)
    negative_examples = dpo.get_negative_examples()

    result = verbalize(buffer.events, outcome=outcome, negative_examples=negative_examples)

    report_data = {
        "run_id": buffer.run_id,
        "company": buffer.company,
        "role": buffer.role,
        "outcome": outcome,
        "event_count": len(buffer.events),
        "narrative": result["narrative"],
        "verbalization_rates": result.get("category_rates", {}),
        "overall_rate": result["overall_rate"],
        "anomaly_count": result.get("anomaly_count", 0),
        "retried": result["retried"],
        "duration_seconds": (
            (buffer.events[-1].timestamp - buffer.events[0].timestamp)
            if len(buffer.events) > 1 else 0.0
        ),
    }

    pdf_path = render_pdf(report_data, output_dir=output_dir)
    report_data["pdf_path"] = pdf_path

    store.save_report(
        run_id=buffer.run_id,
        company=buffer.company,
        role=buffer.role,
        outcome=outcome,
        event_count=len(buffer.events),
        narrative=result["narrative"],
        pdf_path=pdf_path,
        verbalization_rates=result.get("category_rates", {}),
        overall_rate=result["overall_rate"],
        anomaly_count=result.get("anomaly_count", 0),
        retried=result["retried"],
    )

    if send_telegram:
        try:
            from jobpulse.telegram_bots import send_jobs_document
            caption = (
                f"Introspection: {buffer.company} {buffer.role} — "
                f"{outcome} | {result['overall_rate']:.0%} verbalized | "
                f"{result.get('anomaly_count', 0)} anomalies"
            )
            send_jobs_document(pdf_path, caption=caption)
        except Exception:
            pass

    return report_data
```

Also add import at top of `__init__.py`:

```python
from jobpulse.introspection.events import IntrospectionBuffer, IntrospectionEvent, CATEGORIES  # noqa: F401
```

And update the `send_jobs_document` import to be lazy (already handled by the try/except in the function body).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_integration.py -v`
Expected: all 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add jobpulse/introspection/__init__.py tests/jobpulse/test_introspection/test_integration.py
git commit -m "feat(introspection): add flush_and_report orchestrator with Telegram delivery"
```

---

### Task 9: Wire into ApplicationOrchestrator and applicator.py

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/__init__.py:44-60` (create buffer in `__init__`)
- Modify: `jobpulse/applicator.py:445-586` (trigger flush in `confirm_application`)
- Test: `tests/jobpulse/test_introspection/test_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_introspection/test_wiring.py
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.introspection import get_buffer, set_buffer
from jobpulse.introspection.events import IntrospectionBuffer


class TestOrchestratorBufferCreation:
    def test_orchestrator_creates_buffer(self):
        with patch("jobpulse.application_orchestrator_pkg._navigator.FormNavigator"), \
             patch("jobpulse.application_orchestrator_pkg._auth.AuthHandler"), \
             patch("jobpulse.application_orchestrator_pkg._form_filler.FormFiller"), \
             patch("jobpulse.application_orchestrator_pkg._executor.ActionExecutor"):
            from jobpulse.application_orchestrator_pkg import ApplicationOrchestrator
            orch = ApplicationOrchestrator(driver=MagicMock())
            assert hasattr(orch, "_introspection_buffer")
            assert isinstance(orch._introspection_buffer, IntrospectionBuffer)


class TestConfirmApplicationFlush:
    def test_flush_called_on_confirm(self, tmp_path):
        buf = IntrospectionBuffer(company="Test", role="Dev")
        buf.emit("FormFill", "fill_field", target="Name", outcome="success",
                 detail={}, duration_ms=5.0)
        set_buffer(buf)

        with patch("jobpulse.applicator.post_apply_hook"), \
             patch("jobpulse.applicator.RateLimiter"), \
             patch("jobpulse.introspection.flush_and_report") as mock_flush:
            mock_flush.return_value = {"run_id": "test", "pdf_path": "/tmp/t.pdf"}
            from jobpulse.applicator import confirm_application
            confirm_application(
                dry_run_result={"success": True},
                url="https://example.com/apply",
                cv_path=tmp_path / "cv.pdf",
                job_context={"company": "Test", "title": "Dev"},
            )
        mock_flush.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/jobpulse/test_introspection/test_wiring.py -v`
Expected: FAIL (no `_introspection_buffer` attribute, `flush_and_report` not called)

- [ ] **Step 3: Wire buffer creation in ApplicationOrchestrator.__init__**

In `jobpulse/application_orchestrator_pkg/__init__.py`, add after `self.gmail = gmail_verifier or GmailVerifier()` (around line 60):

```python
        # Introspection buffer for capturing pipeline actions
        try:
            from jobpulse.introspection import IntrospectionBuffer, set_buffer, ENABLED
            if ENABLED:
                self._introspection_buffer = IntrospectionBuffer(
                    company="", role="",  # set later when job context available
                )
                set_buffer(self._introspection_buffer)
            else:
                self._introspection_buffer = IntrospectionBuffer(company="", role="", enabled=False)
        except ImportError:
            self._introspection_buffer = None
```

- [ ] **Step 4: Wire flush in confirm_application**

In `jobpulse/applicator.py`, add after the `_record_agent_performance` call near the end of `confirm_application()` (around line 584, before `return result`):

```python
    # Flush introspection report
    try:
        from jobpulse.introspection import get_buffer, flush_and_report
        buf = get_buffer()
        if buf and buf.events:
            buf.company = ctx.get("company", "Unknown")
            buf.role = ctx.get("title", "Unknown")
            flush_and_report(buf, outcome="applied")
    except Exception as exc:
        logger.debug("confirm_application: introspection flush: %s", exc)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/jobpulse/test_introspection/test_wiring.py -v`
Expected: all 2 tests PASS

- [ ] **Step 6: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/__init__.py jobpulse/applicator.py \
       tests/jobpulse/test_introspection/test_wiring.py
git commit -m "feat(introspection): wire buffer creation in orchestrator, flush in confirm_application"
```

---

### Task 10: Instrument Pipeline — emit() calls (Phase 1: PreScreen + FormFill + Submission)

The spec calls for 52 emit points. This task adds the first 21 (PreScreen=6, FormFill=10, Submission=5) — the most failure-prone categories. Remaining categories (Navigation, Screening, CVGen, Hooks, Learning) follow in Task 11.

**Files:**
- Modify: `jobpulse/screening_pipeline.py` (1 emit)
- Modify: `jobpulse/recruiter_screen.py` (1 emit)
- Modify: `jobpulse/skill_graph_store.py` (3 emits)
- Modify: `jobpulse/pre_submit_gate.py` (1 emit)
- Modify: `jobpulse/native_form_filler.py` (5 emits)
- Modify: `jobpulse/form_engine/field_scanner.py` (1 emit)
- Modify: `jobpulse/form_engine/field_mapper.py` (1 emit)
- Modify: `jobpulse/form_engine/semantic_matcher.py` (1 emit)
- Modify: `jobpulse/form_experience_db.py` (1 emit)
- Modify: `jobpulse/vision_tier.py` (1 emit)
- Modify: `jobpulse/applicator.py` (3 emits — apply_job, confirm, rate limiter)
- Modify: `jobpulse/job_db.py` (1 emit)

Each emit call is a single line. The pattern for every instrumentation point:

```python
from jobpulse.introspection import emit

# After the relevant action completes:
emit("Category", "action_name", target="what was acted on",
     outcome="success" if ok else "failure",
     detail={"key": "relevant context"}, duration_ms=elapsed)
```

- [ ] **Step 1: Add emit to PreScreen functions**

In each file, add `from jobpulse.introspection import emit` at the top (inside a try/except ImportError to avoid hard dependency), then add one `emit()` call after each action completes. Example for `recruiter_screen.py:screen()`:

```python
# At top of file:
try:
    from jobpulse.introspection import emit as _introspect
except ImportError:
    _introspect = lambda *a, **kw: None

# After the screen result is determined:
_introspect("PreScreen", "gate_screen", target="gate_0",
            outcome="pass" if result["pass"] else "kill",
            detail={"reason": result.get("reason", ""), "title": title},
            duration_ms=elapsed_ms)
```

Apply the same pattern to:
- `screening_pipeline.py:classify_action()` — emit after routing decision
- `skill_graph_store.py:check_kill_signals()` — emit gate_1 result
- `skill_graph_store.py:check_must_haves()` — emit gate_2 result with match count
- `skill_graph_store.py:check_competitiveness()` — emit gate_3 result with score
- `pre_submit_gate.py:run_gate4()` — emit gate_4 result with sub-scores

- [ ] **Step 2: Add emit to FormFill functions**

Apply the same import pattern, then emit in:
- `native_form_filler.py:fill_form()` — session start/end events
- `native_form_filler.py:_fill_single_field()` — per-field fill result
- `native_form_filler.py:_resolve_field_value()` — resolution method used
- `native_form_filler.py:_upload_file()` — file upload result
- `native_form_filler.py:_classify_fill_failure()` — failure classification
- `field_scanner.py:scan_fields()` — discovery method used
- `field_mapper.py:map_fields()` — mapping decisions
- `semantic_matcher.py:match_option()` — tier used for matching
- `form_experience_db.py:record_fill()` — experience write
- `vision_tier.py:analyze_field()` — vision fallback trigger

- [ ] **Step 3: Add emit to Submission functions**

- `applicator.py:apply_job()` — dry_run flag and submission decision
- `native_form_filler.py:_find_submit_button()` — button discovery
- `applicator.py:confirm_application()` — confirmation event
- `job_db.py:record_application()` — DB write
- Rate limiter check in `applicator.py` — platform + daily counts

- [ ] **Step 4: Verify existing tests still pass**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60 -x -q 2>&1 | tail -10`
Expected: no new failures introduced by emit calls (all emit calls are wrapped in try/except or use the safe import pattern)

- [ ] **Step 5: Commit**

```bash
git add jobpulse/screening_pipeline.py jobpulse/recruiter_screen.py \
       jobpulse/skill_graph_store.py jobpulse/pre_submit_gate.py \
       jobpulse/native_form_filler.py jobpulse/form_engine/field_scanner.py \
       jobpulse/form_engine/field_mapper.py jobpulse/form_engine/semantic_matcher.py \
       jobpulse/form_experience_db.py jobpulse/vision_tier.py \
       jobpulse/applicator.py jobpulse/job_db.py
git commit -m "feat(introspection): instrument PreScreen + FormFill + Submission (21 emit points)"
```

---

### Task 11: Instrument Pipeline — emit() calls (Phase 2: Navigation + Screening + CVGen + Hooks + Learning)

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py` (6 emits)
- Modify: `jobpulse/page_analysis/classifier.py` (1 emit)
- Modify: `jobpulse/page_analysis/page_reasoner.py` (1 emit)
- Modify: `jobpulse/screening_pipeline.py` (4 more emits — resolve, classify_intent, generate, cache)
- Modify: `jobpulse/screening_decomposer.py` (1 emit)
- Modify: `jobpulse/cv_templates/__init__.py` (3 emits)
- Modify: `jobpulse/cv_templates/generate_cover_letter.py` (2 emits)
- Modify: `jobpulse/job_autopilot.py` (2 emits — sync_profile, post_apply_hook)
- Modify: `jobpulse/post_apply_hook.py` (1 emit — hook entry)
- Modify: `jobpulse/job_notion_sync.py` (1 emit)
- Modify: `jobpulse/correction_capture.py` (1 emit)
- Modify: `jobpulse/agent_rules.py` (1 emit)
- Modify: `jobpulse/strategy_reflector.py` (1 emit)
- Modify: `shared/optimization/engine.py` (2 emits)
- Modify: `shared/experiential_learning.py` (1 emit)
- Modify: `jobpulse/agent_performance.py` (1 emit)
- Modify: `shared/cognitive/engine.py` (1 emit)
- Modify: `jobpulse/navigation_learner.py` (1 emit)

Same pattern as Task 10: safe import at top, one-line emit after each action.

- [ ] **Step 1: Navigation (8 points)**

Add emit calls in `_navigator.py` for: navigate_to_form, dismiss_overlays, detect_page_type, bypass_verification_wall, click_apply_button, handle_stuck. Add in `classifier.py:classify_page()` and `page_reasoner.py:reason_about_page()`.

- [ ] **Step 2: Screening (5 points)**

Add emit calls in `screening_pipeline.py` for: resolve (cache check), classify_intent, check_alignment, generate_answer, cache_answer. Add in `screening_decomposer.py:decompose()`.

- [ ] **Step 3: CVGen (5 points)**

Add emit calls in `job_autopilot.py:_sync_profile()`, `cv_templates/__init__.py:generate_cv()`, `cv_templates/__init__.py:_build_extra_skills()`, `generate_cover_letter.py:generate_cover_letter()`, `generate_cover_letter.py:polish_points_llm()`.

- [ ] **Step 4: Hooks (5 points)**

Add emit in `post_apply_hook.py:post_apply_hook()` (hook entry), `form_experience_db.py:record_experience()`, `job_notion_sync.py:update_application_page()`, `correction_capture.py:capture()`, `agent_rules.py:create_rule()`.

- [ ] **Step 5: Learning (7 points)**

Add emit in `strategy_reflector.py:reflect()`, `engine.py:emit_signal()`, `engine.py:aggregate()`, `experiential_learning.py:store_experience()`, `agent_performance.py:record_snapshot()`, `cognitive/engine.py:think()`, `navigation_learner.py:record()`.

- [ ] **Step 6: Verify existing tests still pass**

Run: `python -m pytest tests/jobpulse/ -v --timeout=60 -x -q 2>&1 | tail -10`
Expected: no new failures

- [ ] **Step 7: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py \
       jobpulse/page_analysis/classifier.py jobpulse/page_analysis/page_reasoner.py \
       jobpulse/screening_pipeline.py jobpulse/screening_decomposer.py \
       jobpulse/cv_templates/__init__.py jobpulse/cv_templates/generate_cover_letter.py \
       jobpulse/job_autopilot.py jobpulse/post_apply_hook.py \
       jobpulse/job_notion_sync.py jobpulse/correction_capture.py \
       jobpulse/agent_rules.py jobpulse/strategy_reflector.py \
       shared/optimization/engine.py shared/experiential_learning.py \
       jobpulse/agent_performance.py shared/cognitive/engine.py \
       jobpulse/navigation_learner.py
git commit -m "feat(introspection): instrument Navigation + Screening + CVGen + Hooks + Learning (31 emit points)"
```

---

### Task 12: Full Integration Test

**Files:**
- Test: `tests/jobpulse/test_introspection/test_full_pipeline.py`

- [ ] **Step 1: Write the end-to-end test**

```python
# tests/jobpulse/test_introspection/test_full_pipeline.py
import os
import json
import pytest
from unittest.mock import patch, MagicMock
from jobpulse.introspection import set_buffer, get_buffer, flush_and_report
from jobpulse.introspection.events import IntrospectionBuffer
from jobpulse.introspection.store import IntrospectionStore


@pytest.fixture
def store(tmp_path):
    return IntrospectionStore(db_path=str(tmp_path / "introspection.db"))


class TestFullPipeline:
    def test_emit_flush_verbalize_render_deliver(self, store, tmp_path):
        """Simulate a complete application run: emit events, flush, verbalize, render, deliver."""
        buf = IntrospectionBuffer(company="Acme Corp", role="Software Engineer")
        set_buffer(buf)

        # Simulate PreScreen
        buf.emit("PreScreen", "gate_screen", target="gate_0", outcome="success",
                 detail={"reason": "title match"}, duration_ms=0.5)
        buf.emit("PreScreen", "gate_pass", target="gate_1", outcome="success",
                 detail={"kill_signals": 0}, duration_ms=1.0)
        buf.emit("PreScreen", "gate_pass", target="gate_2", outcome="success",
                 detail={"matched": 4, "of": 5, "missing": ["Kubernetes"]}, duration_ms=2.0)
        buf.emit("PreScreen", "gate_pass", target="gate_3", outcome="success",
                 detail={"score": 94.2}, duration_ms=1.5)
        buf.emit("PreScreen", "gate_pass", target="gate_4", outcome="success",
                 detail={"recruiter_score": 8.5}, duration_ms=50.0)

        # Simulate CVGen
        buf.emit("CVGen", "profile_sync", target="skill_graph", outcome="success",
                 detail={"skills_added": 3, "skills_removed": 1}, duration_ms=200.0)
        buf.emit("CVGen", "pdf_render", target="cv", outcome="success",
                 detail={"role_profile": "software_engineer", "pages": 2}, duration_ms=150.0)

        # Simulate FormFill
        for field in ["First Name", "Last Name", "Email", "Phone", "Resume Upload"]:
            outcome = "success" if field != "Resume Upload" else "success"
            method = "file_upload" if field == "Resume Upload" else "profile"
            buf.emit("FormFill", "fill_field", target=field, outcome=outcome,
                     detail={"method": method}, duration_ms=5.0)

        # Simulate Submission
        buf.emit("Submission", "dry_run_review", target="submit_button", outcome="success",
                 detail={}, duration_ms=0.0)
        buf.emit("Submission", "submit_attempt", target="submit", outcome="success",
                 detail={"rate_check": "5/30"}, duration_ms=100.0)

        # Simulate Hooks
        buf.emit("Hooks", "hook_fire", target="post_apply_hook", outcome="success",
                 detail={}, duration_ms=50.0)
        buf.emit("Hooks", "correction_capture", target="corrections", outcome="success",
                 detail={"correction_count": 0}, duration_ms=10.0)

        # Simulate Learning
        buf.emit("Learning", "signal_emit", target="strategy_reflect", outcome="success",
                 detail={}, duration_ms=20.0)
        buf.emit("Learning", "signal_emit", target="optimization", outcome="success",
                 detail={"signal_type": "adaptation"}, duration_ms=5.0)
        buf.emit("Learning", "experience_store", target="experience_memory", outcome="success",
                 detail={}, duration_ms=10.0)

        assert len(buf.events) == 17

        fake_narrative = (
            "## PreScreen\n\nI screened the Acme Corp Software Engineer role through all 5 gates.\n\n"
            "## CVGen\n\nSynced profile, rendered 2-page CV.\n\n"
            "## FormFill\n\nFilled First Name, Last Name, Email, Phone. Uploaded Resume.\n\n"
            "## Submission\n\nDry run reviewed. Submitted successfully. Rate: 5/30.\n\n"
            "## Hooks\n\npost_apply_hook fired. No corrections captured.\n\n"
            "## Learning\n\nstrategy_reflect fired. Optimization signal emitted. Experience stored."
        )

        with patch("jobpulse.introspection.verbalizer.smart_llm_call", return_value=fake_narrative), \
             patch("jobpulse.introspection.verbalizer.check_coverage") as mock_cov, \
             patch("jobpulse.introspection.send_jobs_document") as mock_tg:
            mock_cov.return_value = {
                "overall_rate": 1.0,
                "missed_events": [],
                "category_rates": {
                    "PreScreen": 1.0, "CVGen": 1.0, "FormFill": 1.0,
                    "Submission": 1.0, "Hooks": 1.0, "Learning": 1.0,
                },
            }
            result = flush_and_report(
                buf, outcome="applied", store=store,
                output_dir=str(tmp_path), send_telegram=True,
            )

        assert result is not None
        assert result["overall_rate"] == 1.0
        assert result["event_count"] == 17
        assert os.path.exists(result["pdf_path"])

        # Verify DB state
        events = store.get_events(buf.run_id)
        assert len(events) == 17

        report = store.get_report(buf.run_id)
        assert report is not None
        assert report["company"] == "Acme Corp"

        rates = json.loads(report["verbalization_rates"])
        assert rates["PreScreen"] == 1.0

        # Verify Telegram was called
        mock_tg.assert_called_once()
        caption = mock_tg.call_args[0][1] if len(mock_tg.call_args[0]) > 1 else mock_tg.call_args[1].get("caption", "")
        assert "Acme Corp" in caption
        assert "100%" in caption
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/jobpulse/test_introspection/test_full_pipeline.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/jobpulse/test_introspection/test_full_pipeline.py
git commit -m "test(introspection): add full pipeline integration test (17 events, all categories)"
```

---

## Summary

| Task | Component | Files Created | Tests |
|------|-----------|---------------|-------|
| 1 | Event + Buffer | `events.py`, `__init__.py` | 9 |
| 2 | SQLite Store | `store.py` | 13 |
| 3 | Validator | `validator.py` | 7 |
| 4 | Verbalizer | `verbalizer.py` | 5 |
| 5 | DPO Manager | `dpo.py` | 5 |
| 6 | PDF Renderer | `renderer.py` | 5 |
| 7 | CLI | `cli.py` + runner.py mod | 6 |
| 8 | Orchestrator | `__init__.py` update | 4 |
| 9 | Pipeline Wiring | orchestrator + applicator mods | 2 |
| 10 | Instrumentation P1 | 12 files, 21 emit points | existing suite |
| 11 | Instrumentation P2 | 18 files, 31 emit points | existing suite |
| 12 | Full Integration | test file | 1 |

**Total: 12 tasks, 8 new files, ~30 modified files, ~57 tests, 52 emit points.**

Tasks 1-8 are independent and parallelizable. Task 9 depends on Tasks 1+8. Tasks 10-11 depend on Task 1. Task 12 depends on all.
