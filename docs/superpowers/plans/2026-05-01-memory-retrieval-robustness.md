# Memory Retrieval Robustness & Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the JobPulse memory layer return the right learned data at the right phase of an application with bounded latency and graceful degradation under failure — so downstream work (distillation, VLM-fallback agent, MBTL queue ranker) has a fast and reliable substrate.

**Architecture:** Wrap the existing `MemoryManager` facade with four orthogonal coordination layers — instrumentation, request-scope cache, async write queue, and prefetcher — and reuse the existing `CircuitBreaker` for L3/L4 protection. No replacement of existing stores. Every new module is additive and behind a feature flag so rollback is one env var.

**Tech Stack:** Python 3.11, SQLite WAL, Qdrant, Neo4j, existing `shared/memory_layer/_manager.py` API, `shared/circuit_breaker.py`, `shared/cost_tracker.py` pattern for the new tracker, pytest with `tmp_path` for tests.

---

## Pre-flight

Before starting any task:

- [ ] Confirm you are on a dedicated worktree, not `main`. If not: `git worktree add .claude/worktrees/memory-robustness -b feat/memory-robustness`
- [ ] Run baseline tests once and confirm green: `python -m pytest tests/shared/memory_layer/ tests/shared/test_circuit_breaker_apis.py tests/shared/test_db_pool.py -v`
- [ ] Note current p95 latency for a single `MemoryManager.recall()` call from a representative production-like run, so Phase 1 has a baseline to compare against.

---

## File Structure

**Create:**
- `shared/memory_layer/_retrieval_tracker.py` — per-call latency + tier + hit/miss recorder
- `shared/memory_layer/_request_scope.py` — thread-local request cache cleared per `apply_job()`
- `shared/memory_layer/_write_queue.py` — async write queue with SQLite-backed durable fallback
- `shared/memory_layer/_prefetcher.py` — page-entry parallel warm of L1 cache
- `shared/memory_layer/_bloom.py` — negative cache for "domain unseen" queries
- `shared/memory_layer/_pool.py` — singleton wrappers for Qdrant client + Neo4j driver
- `scripts/retrieval_latency_report.py` — p50/p95/p99 + hit-rate report from tracker logs
- `tests/shared/memory_layer/test_retrieval_tracker.py`
- `tests/shared/memory_layer/test_request_scope.py`
- `tests/shared/memory_layer/test_write_queue.py`
- `tests/shared/memory_layer/test_prefetcher.py`
- `tests/shared/memory_layer/test_bloom.py`
- `tests/shared/memory_layer/test_pool.py`

**Modify:**
- `shared/memory_layer/_manager.py` — wire tracker, request scope, write queue
- `shared/memory_layer/_qdrant_store.py` — use `_pool.get_qdrant_client()`
- `shared/memory_layer/_neo4j_store.py` — use `_pool.get_neo4j_driver()`
- `jobpulse/post_apply_hook.py` — route writes through `_write_queue`
- `jobpulse/application_orchestrator_pkg/_navigator.py` — emit `page_detected` event to prefetcher
- `jobpulse/form_experience_db.py` — add TTL invalidation on stale selectors
- `shared/memory_layer/CLAUDE.md` — document new layers

---

## Phase 1 — Instrumentation (measure before optimizing)

### Task 1: RetrievalTracker module

**Files:**
- Create: `shared/memory_layer/_retrieval_tracker.py`
- Test: `tests/shared/memory_layer/test_retrieval_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_retrieval_tracker.py
import sqlite3
import time

import pytest

from shared.memory_layer._retrieval_tracker import RetrievalTracker


@pytest.fixture
def tracker(tmp_path):
    return RetrievalTracker(db_path=str(tmp_path / "retrieval.db"))


def test_records_tier_latency_and_hit(tracker):
    with tracker.record(tier="L1_inproc", op="recall", key="domain:greenhouse.io") as r:
        time.sleep(0.001)
        r.hit = True
    rows = tracker.fetch_recent(limit=10)
    assert len(rows) == 1
    assert rows[0]["tier"] == "L1_inproc"
    assert rows[0]["op"] == "recall"
    assert rows[0]["hit"] is True
    assert rows[0]["latency_ms"] >= 1.0


def test_record_on_exception_marks_miss_and_logs(tracker):
    with pytest.raises(RuntimeError):
        with tracker.record(tier="L3_qdrant", op="search", key="x") as r:
            raise RuntimeError("boom")
    rows = tracker.fetch_recent(limit=10)
    assert rows[0]["hit"] is False
    assert rows[0]["error"] == "RuntimeError"


def test_summary_p95_per_tier(tracker):
    for ms in [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]:
        with tracker.record(tier="L2_sqlite", op="recall", key="k") as r:
            time.sleep(ms / 1000.0)
            r.hit = True
    summary = tracker.summary(window_seconds=60)
    l2 = next(s for s in summary if s["tier"] == "L2_sqlite")
    assert l2["count"] == 10
    assert l2["p95_ms"] >= 9.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.memory_layer._retrieval_tracker'`

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_retrieval_tracker.py
"""Per-call retrieval instrumentation.

Records (tier, op, key, latency_ms, hit, error) for every memory read so we
can answer 'where is the time going?' before optimizing.

Mirrors the cost_tracker.py pattern: SQLite-backed, write-only hot path,
read-only on demand for reports.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import statistics
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from shared.logging_config import get_logger
from shared.paths import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_PATH = DATA_DIR / "retrieval_tracker.db"


@dataclass
class _RecordContext:
    hit: bool = False
    error: str | None = None
    extra: dict = field(default_factory=dict)


class RetrievalTracker:
    """Thread-safe SQLite-backed retrieval call recorder."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(_DEFAULT_PATH)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS retrieval_log (
                    ts REAL NOT NULL,
                    tier TEXT NOT NULL,
                    op TEXT NOT NULL,
                    key TEXT,
                    latency_ms REAL NOT NULL,
                    hit INTEGER NOT NULL,
                    error TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_ts ON retrieval_log(ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_tier ON retrieval_log(tier)")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=2.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    @contextlib.contextmanager
    def record(self, tier: str, op: str, key: str | None = None) -> Iterator[_RecordContext]:
        ctx = _RecordContext()
        start = time.perf_counter()
        err: str | None = None
        try:
            yield ctx
        except Exception as e:
            err = type(e).__name__
            ctx.hit = False
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1000.0
            self._write(tier, op, key, latency_ms, ctx.hit, err or ctx.error)

    def _write(self, tier: str, op: str, key: str | None, latency_ms: float, hit: bool, err: str | None) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO retrieval_log(ts, tier, op, key, latency_ms, hit, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), tier, op, key, latency_ms, int(hit), err),
            )

    def fetch_recent(self, limit: int = 100) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT ts, tier, op, key, latency_ms, hit, error FROM retrieval_log ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) | {"hit": bool(r["hit"])} for r in rows]

    def summary(self, window_seconds: int = 3600) -> list[dict]:
        cutoff = time.time() - window_seconds
        with self._conn() as c:
            rows = c.execute(
                "SELECT tier, latency_ms, hit FROM retrieval_log WHERE ts >= ?",
                (cutoff,),
            ).fetchall()
        by_tier: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            by_tier.setdefault(r["tier"], []).append(r)
        out: list[dict] = []
        for tier, rs in by_tier.items():
            lats = sorted(r["latency_ms"] for r in rs)
            hits = sum(1 for r in rs if r["hit"])
            out.append({
                "tier": tier,
                "count": len(rs),
                "hit_rate": hits / len(rs) if rs else 0.0,
                "p50_ms": statistics.median(lats) if lats else 0.0,
                "p95_ms": lats[int(len(lats) * 0.95)] if lats else 0.0,
                "p99_ms": lats[int(len(lats) * 0.99)] if lats else 0.0,
            })
        return out


_singleton: RetrievalTracker | None = None
_singleton_lock = threading.Lock()


def get_retrieval_tracker() -> RetrievalTracker:
    """Module-level singleton — lazy init, kill switch via RETRIEVAL_TRACKING=false."""
    global _singleton
    if os.getenv("RETRIEVAL_TRACKING", "true").lower() == "false":
        return _NullTracker()  # type: ignore[return-value]
    with _singleton_lock:
        if _singleton is None:
            _singleton = RetrievalTracker()
        return _singleton


class _NullTracker:
    @contextlib.contextmanager
    def record(self, tier: str, op: str, key: str | None = None) -> Iterator[_RecordContext]:
        yield _RecordContext()

    def fetch_recent(self, limit: int = 100) -> list[dict]:
        return []

    def summary(self, window_seconds: int = 3600) -> list[dict]:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_tracker.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_retrieval_tracker.py tests/shared/memory_layer/test_retrieval_tracker.py
git commit -m "feat(memory): add RetrievalTracker for per-call latency/hit instrumentation"
```

---

### Task 2: Wire tracker into the three stores

**Files:**
- Modify: `shared/memory_layer/_sqlite_store.py` (find existing read methods)
- Modify: `shared/memory_layer/_qdrant_store.py`
- Modify: `shared/memory_layer/_neo4j_store.py`
- Test: `tests/shared/memory_layer/test_retrieval_tracker_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_retrieval_tracker_wiring.py
import pytest

from shared.memory_layer._manager import MemoryManager
from shared.memory_layer._retrieval_tracker import RetrievalTracker


@pytest.fixture
def manager_with_tracker(tmp_path, monkeypatch):
    tracker = RetrievalTracker(db_path=str(tmp_path / "rt.db"))
    monkeypatch.setattr(
        "shared.memory_layer._retrieval_tracker.get_retrieval_tracker",
        lambda: tracker,
    )
    mm = MemoryManager(sqlite_path=str(tmp_path / "mem.db"), use_qdrant=False, use_neo4j=False)
    return mm, tracker


def test_sqlite_recall_emits_l2_record(manager_with_tracker):
    mm, tracker = manager_with_tracker
    mm.recall(query="anything", k=1)
    rows = tracker.fetch_recent(limit=10)
    tiers = {r["tier"] for r in rows}
    assert "L2_sqlite" in tiers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_tracker_wiring.py -v`
Expected: FAIL — no L2_sqlite record produced because stores are not yet instrumented.

- [ ] **Step 3: Write minimal implementation**

In each of `_sqlite_store.py`, `_qdrant_store.py`, `_neo4j_store.py`, locate the primary read method (typically `search`, `recall`, or `query`) and wrap the body. Concrete pattern (apply to each, adjusting tier name):

```python
# shared/memory_layer/_sqlite_store.py — add at top of file
from shared.memory_layer._retrieval_tracker import get_retrieval_tracker

# inside the existing search/recall method, wrap the body:
def search(self, query: str, k: int = 5) -> list[dict]:
    tracker = get_retrieval_tracker()
    with tracker.record(tier="L2_sqlite", op="search", key=query[:64]) as r:
        results = self._do_search_impl(query, k)  # existing body
        r.hit = bool(results)
        return results
```

For `_qdrant_store.py` use `tier="L3_qdrant"`, for `_neo4j_store.py` use `tier="L4_neo4j"`. If a store has multiple read methods (e.g., `search`, `get`, `multi_search`), wrap each with the appropriate `op` value (`search`, `get`, `multi_search`).

Do NOT change return types or signatures. Do NOT add try/except — `record()` already captures exceptions.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_tracker_wiring.py tests/shared/memory_layer/ -v`
Expected: new test PASSES, all existing memory_layer tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_sqlite_store.py shared/memory_layer/_qdrant_store.py shared/memory_layer/_neo4j_store.py tests/shared/memory_layer/test_retrieval_tracker_wiring.py
git commit -m "feat(memory): wire RetrievalTracker into all three stores"
```

---

### Task 3: Latency report script

**Files:**
- Create: `scripts/retrieval_latency_report.py`
- Test: `tests/shared/memory_layer/test_retrieval_latency_report.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_retrieval_latency_report.py
import importlib.util
import time
from pathlib import Path

from shared.memory_layer._retrieval_tracker import RetrievalTracker


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location("retrieval_latency_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_report_renders_table_with_p95(tmp_path, monkeypatch):
    tracker = RetrievalTracker(db_path=str(tmp_path / "rt.db"))
    for _ in range(5):
        with tracker.record(tier="L2_sqlite", op="search") as r:
            time.sleep(0.001)
            r.hit = True
    monkeypatch.setattr(
        "shared.memory_layer._retrieval_tracker.get_retrieval_tracker",
        lambda: tracker,
    )
    mod = _load_module(Path("scripts/retrieval_latency_report.py"))
    report = mod.render_report(window_seconds=60)
    assert "L2_sqlite" in report
    assert "p95" in report.lower()
    assert "5" in report  # count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_latency_report.py -v`
Expected: FAIL — script does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/retrieval_latency_report.py
"""Print p50/p95/p99 + hit-rate per memory tier over a time window.

Usage:
    python scripts/retrieval_latency_report.py [--window-hours 24]
"""
from __future__ import annotations

import argparse

from shared.memory_layer._retrieval_tracker import get_retrieval_tracker


def render_report(window_seconds: int) -> str:
    tracker = get_retrieval_tracker()
    summary = tracker.summary(window_seconds=window_seconds)
    if not summary:
        return "No retrieval activity in window."
    summary.sort(key=lambda s: s["p95_ms"], reverse=True)
    header = f"{'tier':<14}{'count':>8}{'hit%':>8}{'p50_ms':>10}{'p95_ms':>10}{'p99_ms':>10}"
    sep = "-" * len(header)
    lines = [header, sep]
    for s in summary:
        lines.append(
            f"{s['tier']:<14}{s['count']:>8}{s['hit_rate']*100:>7.1f}%"
            f"{s['p50_ms']:>10.2f}{s['p95_ms']:>10.2f}{s['p99_ms']:>10.2f}"
        )
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--window-hours", type=float, default=24.0)
    args = p.parse_args()
    print(render_report(int(args.window_hours * 3600)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_retrieval_latency_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/retrieval_latency_report.py tests/shared/memory_layer/test_retrieval_latency_report.py
git commit -m "feat(memory): add retrieval_latency_report script (p50/p95/p99 per tier)"
```

---

### Task 4: 24-hour baseline collection (manual checkpoint)

**Files:** none — operational step.

- [ ] **Step 1: Deploy tracker to a real apply session**

Run a representative apply: `JOBPULSE_AUTO_SUBMIT=false python -m jobpulse.runner job-apply-next` against a real queued job. Confirm `data/retrieval_tracker.db` accumulates rows.

- [ ] **Step 2: Let it run for at least 4 hours of activity** (sufficient sample; no need to wait 24h for first signal)

- [ ] **Step 3: Generate the report**

Run: `python scripts/retrieval_latency_report.py --window-hours 4`
Expected: ranked table per tier with p50/p95/p99 + hit-rate.

- [ ] **Step 4: Record the baseline in the plan as a comment**

Append to this file in a "Baseline (Phase 1 result)" section: which tier dominates p95, which tier has surprising miss rate, and any tier that exceeds its target SLO (L1≤1ms, L2≤10ms, L3≤50ms, L4≤200ms). This gates which Phase 2/3/4 tasks are actually worth doing first vs deferring.

- [ ] **Step 5: Commit the baseline note**

```bash
git add docs/superpowers/plans/2026-05-01-memory-retrieval-robustness.md
git commit -m "docs(memory): record baseline retrieval latency for Phase 1"
```

---

## Phase 2 — Async write queue

### Task 5: WriteQueue with durable fallback

**Files:**
- Create: `shared/memory_layer/_write_queue.py`
- Test: `tests/shared/memory_layer/test_write_queue.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_write_queue.py
import threading
import time

import pytest

from shared.memory_layer._write_queue import WriteQueue


def test_enqueue_runs_callable_async(tmp_path):
    q = WriteQueue(db_path=str(tmp_path / "wq.db"), workers=1)
    q.start()
    seen: list[int] = []
    q.enqueue("test_op", lambda: seen.append(1), idempotency_key="k1")
    for _ in range(50):
        if seen:
            break
        time.sleep(0.01)
    q.stop(timeout=2.0)
    assert seen == [1]


def test_idempotency_key_dedupes(tmp_path):
    q = WriteQueue(db_path=str(tmp_path / "wq.db"), workers=1)
    q.start()
    seen: list[int] = []
    for _ in range(5):
        q.enqueue("test_op", lambda: seen.append(1), idempotency_key="same-key")
    time.sleep(0.2)
    q.stop(timeout=2.0)
    assert sum(seen) == 1


def test_failed_job_persists_for_replay(tmp_path):
    q = WriteQueue(db_path=str(tmp_path / "wq.db"), workers=1)
    q.start()

    def boom():
        raise RuntimeError("nope")

    q.enqueue("test_op", boom, idempotency_key="k-fail")
    time.sleep(0.2)
    q.stop(timeout=2.0)
    pending = q.list_failed()
    assert len(pending) == 1
    assert pending[0]["op"] == "test_op"
    assert "RuntimeError" in pending[0]["last_error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_write_queue.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_write_queue.py
"""Async write queue with durable fallback.

Background worker thread drains a Python queue of (op, callable, idem_key) jobs.
Failures are persisted to SQLite so they can be replayed after a Qdrant/Neo4j
outage. Idempotency keys deduplicate within the in-memory queue.

Caller path is non-blocking: enqueue() returns immediately. Use stop() at
process shutdown to flush remaining work.
"""
from __future__ import annotations

import queue
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class _Job:
    op: str
    fn: Callable[[], None]
    idem_key: str | None
    enqueued_at: float


class WriteQueue:
    def __init__(self, db_path: str, workers: int = 1, max_in_flight: int = 1024):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._q: queue.Queue[_Job | None] = queue.Queue(maxsize=max_in_flight)
        self._workers = workers
        self._threads: list[threading.Thread] = []
        self._seen_keys: set[str] = set()
        self._seen_lock = threading.Lock()
        self._stopped = threading.Event()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=2.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS failed_writes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    op TEXT NOT NULL,
                    idem_key TEXT,
                    last_error TEXT,
                    retry_count INTEGER DEFAULT 0
                )
            """)

    def start(self) -> None:
        for i in range(self._workers):
            t = threading.Thread(target=self._run, name=f"WriteQueue-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stopped.set()
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout=timeout)

    def enqueue(self, op: str, fn: Callable[[], None], idempotency_key: str | None = None) -> bool:
        if idempotency_key is not None:
            with self._seen_lock:
                if idempotency_key in self._seen_keys:
                    return False
                self._seen_keys.add(idempotency_key)
        job = _Job(op=op, fn=fn, idem_key=idempotency_key, enqueued_at=time.time())
        try:
            self._q.put_nowait(job)
            return True
        except queue.Full:
            logger.warning("write_queue_full", extra={"op": op})
            self._record_failure(job, "queue_full")
            return False

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if job is None:
                return
            try:
                job.fn()
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                logger.error("write_queue_job_failed", extra={"op": job.op, "error": err})
                self._record_failure(job, err)

    def _record_failure(self, job: _Job, error: str) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT INTO failed_writes(ts, op, idem_key, last_error) VALUES (?, ?, ?, ?)",
                    (time.time(), job.op, job.idem_key, error),
                )
        except sqlite3.Error:
            logger.exception("write_queue_persistence_failed")

    def list_failed(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, ts, op, idem_key, last_error, retry_count FROM failed_writes ORDER BY ts DESC"
            ).fetchall()
        return [dict(r) for r in rows]


_singleton: WriteQueue | None = None
_singleton_lock = threading.Lock()


def get_write_queue() -> WriteQueue:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            from shared.paths import DATA_DIR
            _singleton = WriteQueue(db_path=str(DATA_DIR / "write_queue.db"))
            _singleton.start()
        return _singleton
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_write_queue.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_write_queue.py tests/shared/memory_layer/test_write_queue.py
git commit -m "feat(memory): add WriteQueue with idempotency keys and durable failure log"
```

---

### Task 6: Route post_apply_hook through WriteQueue

**Files:**
- Modify: `jobpulse/post_apply_hook.py`
- Test: `tests/jobpulse/test_post_apply_hook_async.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_post_apply_hook_async.py
import time

import pytest

from jobpulse import post_apply_hook as pah
from shared.memory_layer._write_queue import WriteQueue


def test_post_apply_hook_returns_immediately(tmp_path, monkeypatch):
    q = WriteQueue(db_path=str(tmp_path / "wq.db"), workers=1)
    q.start()
    monkeypatch.setattr(pah, "get_write_queue", lambda: q)

    slow_call_done: list[bool] = []

    def slow_form_record(*a, **kw):
        time.sleep(0.5)
        slow_call_done.append(True)

    monkeypatch.setattr(pah, "_record_form_experience", slow_form_record)
    monkeypatch.setattr(pah, "_upload_to_drive", lambda *a, **kw: None)
    monkeypatch.setattr(pah, "_update_notion", lambda *a, **kw: None)

    start = time.perf_counter()
    pah.post_apply_hook(
        result={"success": True, "pages_filled": 1, "field_types": [], "screening_questions": []},
        job_context={"company": "X", "url": "https://x.com", "platform": "generic"},
        form_exp_db_path=str(tmp_path / "fed.db"),
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 0.1, f"hook should return fast, took {elapsed}s"

    time.sleep(0.7)
    q.stop(timeout=2.0)
    assert slow_call_done == [True], "queued work must still execute"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_post_apply_hook_async.py -v`
Expected: FAIL — current hook is synchronous; elapsed will be ≥0.5s.

- [ ] **Step 3: Write minimal implementation**

Refactor `jobpulse/post_apply_hook.py` so the three concerns (form experience, drive upload, notion update) are extracted into private functions and dispatched through the write queue. Replace the existing inline calls with:

```python
# jobpulse/post_apply_hook.py — at top
from shared.memory_layer._write_queue import get_write_queue

# extract three private helpers from existing inline code:
def _record_form_experience(result: dict, job_context: dict, db_path: str | None) -> None:
    """Body of the existing FormExperienceDB call from current hook."""
    # ... (move existing FormExperienceDB write block here unchanged)

def _upload_to_drive(job_context: dict) -> tuple[str | None, str | None]:
    # ... (move existing drive upload block here)

def _update_notion(job_context: dict, cv_link: str | None, cl_link: str | None) -> None:
    # ... (move existing notion update block here)


def post_apply_hook(result: dict, job_context: dict, form_exp_db_path: str | None = None) -> None:
    """Async fan-out — caller is unblocked immediately."""
    q = get_write_queue()
    job_id = job_context.get("job_id") or job_context.get("url", "unknown")

    q.enqueue(
        "form_experience",
        lambda: _record_form_experience(result, job_context, form_exp_db_path),
        idempotency_key=f"form_exp:{job_id}",
    )
    q.enqueue(
        "drive_upload_and_notion",
        lambda: _update_notion(job_context, *_upload_to_drive(job_context)),
        idempotency_key=f"drive_notion:{job_id}",
    )
```

Preserve the existing failure-path behaviour (the `if not result.get("success")` block) — wrap that in its own queued job too. Do not change the function signature or its callers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_post_apply_hook_async.py tests/jobpulse/ -v -k post_apply`
Expected: new test PASSES, existing post_apply tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/post_apply_hook.py tests/jobpulse/test_post_apply_hook_async.py
git commit -m "refactor(post-apply): route hook writes through async WriteQueue"
```

---

## Phase 3 — Connection pools

### Task 7: Qdrant + Neo4j singleton pool

**Files:**
- Create: `shared/memory_layer/_pool.py`
- Modify: `shared/memory_layer/_qdrant_store.py`
- Modify: `shared/memory_layer/_neo4j_store.py`
- Test: `tests/shared/memory_layer/test_pool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_pool.py
from shared.memory_layer._pool import get_qdrant_client, get_neo4j_driver, reset_pools


def test_qdrant_client_is_singleton(monkeypatch):
    reset_pools()
    created: list[int] = []

    class FakeQdrant:
        def __init__(self, url: str | None = None, **kw):
            created.append(1)

    monkeypatch.setattr("shared.memory_layer._pool._QdrantClient", FakeQdrant)
    a = get_qdrant_client()
    b = get_qdrant_client()
    assert a is b
    assert sum(created) == 1


def test_neo4j_driver_is_singleton(monkeypatch):
    reset_pools()
    created: list[int] = []

    class FakeDriver:
        def __init__(self, uri: str, auth=None):
            created.append(1)

        def close(self):
            pass

    monkeypatch.setattr("shared.memory_layer._pool._neo4j_GraphDatabase_driver", lambda *a, **kw: FakeDriver(*a, **kw))
    a = get_neo4j_driver()
    b = get_neo4j_driver()
    assert a is b
    assert sum(created) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_pool.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_pool.py
"""Process-wide singleton clients for Qdrant + Neo4j.

Each external store opens TCP connections per client; creating a new client
per memory call is the dominant fixed cost (10-80 ms). One client per process,
reused for the lifetime of the daemon.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from shared.logging_config import get_logger

logger = get_logger(__name__)

try:
    from qdrant_client import QdrantClient as _QdrantClient
except ImportError:
    _QdrantClient = None  # type: ignore[assignment]

try:
    from neo4j import GraphDatabase as _Neo4j
    _neo4j_GraphDatabase_driver = _Neo4j.driver
except ImportError:
    _Neo4j = None
    _neo4j_GraphDatabase_driver = None  # type: ignore[assignment]


_qdrant_lock = threading.Lock()
_neo4j_lock = threading.Lock()
_qdrant_client: Any | None = None
_neo4j_driver: Any | None = None


def get_qdrant_client() -> Any:
    global _qdrant_client
    if _QdrantClient is None:
        raise RuntimeError("qdrant_client not installed")
    with _qdrant_lock:
        if _qdrant_client is None:
            url = os.getenv("QDRANT_URL", "http://localhost:6333")
            _qdrant_client = _QdrantClient(url=url)
            logger.info("qdrant_client_initialized", extra={"url": url})
        return _qdrant_client


def get_neo4j_driver() -> Any:
    global _neo4j_driver
    if _neo4j_GraphDatabase_driver is None:
        raise RuntimeError("neo4j not installed")
    with _neo4j_lock:
        if _neo4j_driver is None:
            uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
            user = os.getenv("NEO4J_USER", "neo4j")
            pw = os.getenv("NEO4J_PASSWORD", "neo4j")
            _neo4j_driver = _neo4j_GraphDatabase_driver(uri, auth=(user, pw))
            logger.info("neo4j_driver_initialized", extra={"uri": uri})
        return _neo4j_driver


def reset_pools() -> None:
    """Test-only — discard singletons so monkeypatch can swap implementations."""
    global _qdrant_client, _neo4j_driver
    with _qdrant_lock:
        if _qdrant_client is not None:
            try:
                _qdrant_client.close()  # type: ignore[union-attr]
            except Exception:
                pass
            _qdrant_client = None
    with _neo4j_lock:
        if _neo4j_driver is not None:
            try:
                _neo4j_driver.close()
            except Exception:
                pass
            _neo4j_driver = None
```

In `_qdrant_store.py`, find the place where `QdrantClient(...)` is currently instantiated (likely in `__init__`). Replace with `from shared.memory_layer._pool import get_qdrant_client; self._client = get_qdrant_client()`.

In `_neo4j_store.py`, find where `GraphDatabase.driver(...)` is called and replace with `from shared.memory_layer._pool import get_neo4j_driver; self._driver = get_neo4j_driver()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_pool.py tests/shared/memory_layer/ -v`
Expected: pool tests PASS, all existing memory_layer tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_pool.py shared/memory_layer/_qdrant_store.py shared/memory_layer/_neo4j_store.py tests/shared/memory_layer/test_pool.py
git commit -m "feat(memory): singleton pool for Qdrant client + Neo4j driver"
```

---

## Phase 4 — Speed wins

### Task 8: Request-scope cache

**Files:**
- Create: `shared/memory_layer/_request_scope.py`
- Test: `tests/shared/memory_layer/test_request_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_request_scope.py
from shared.memory_layer._request_scope import RequestScope, request_scope


def test_get_or_set_caches_within_scope():
    calls: list[int] = []

    def expensive():
        calls.append(1)
        return "value"

    with request_scope():
        v1 = RequestScope.get_or_set("k", expensive)
        v2 = RequestScope.get_or_set("k", expensive)
    assert v1 == v2 == "value"
    assert sum(calls) == 1


def test_scope_is_cleared_between_requests():
    calls: list[int] = []

    def expensive():
        calls.append(1)
        return "v"

    with request_scope():
        RequestScope.get_or_set("k", expensive)
    with request_scope():
        RequestScope.get_or_set("k", expensive)
    assert sum(calls) == 2


def test_scope_is_thread_local():
    import threading

    results: dict[int, int] = {}

    def worker(tid: int):
        with request_scope():
            results[tid] = id(RequestScope._store())

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results.values())) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_request_scope.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_request_scope.py
"""Thread-local request-scoped cache.

Lifetime is one apply_job() invocation. Avoids re-querying the same memory
key when multiple agents within one application ask for the same data.
"""
from __future__ import annotations

import contextlib
import threading
from typing import Any, Callable, Iterator

_local = threading.local()


class RequestScope:
    @staticmethod
    def _store() -> dict[str, Any]:
        store = getattr(_local, "store", None)
        if store is None:
            raise RuntimeError("No active request scope. Wrap with `with request_scope():`")
        return store

    @staticmethod
    def get_or_set(key: str, factory: Callable[[], Any]) -> Any:
        store = RequestScope._store()
        if key not in store:
            store[key] = factory()
        return store[key]

    @staticmethod
    def clear() -> None:
        if hasattr(_local, "store"):
            _local.store = {}


@contextlib.contextmanager
def request_scope() -> Iterator[None]:
    prev = getattr(_local, "store", None)
    _local.store = {}
    try:
        yield
    finally:
        _local.store = prev if prev is not None else {}
        if prev is None:
            del _local.store
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_request_scope.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_request_scope.py tests/shared/memory_layer/test_request_scope.py
git commit -m "feat(memory): add thread-local RequestScope cache"
```

---

### Task 9: Wire request_scope() into apply_job

**Files:**
- Modify: `jobpulse/applicator.py` (find `apply_job` function)
- Test: `tests/jobpulse/test_applicator_request_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_applicator_request_scope.py
from unittest.mock import patch

from shared.memory_layer._request_scope import RequestScope


def test_apply_job_opens_request_scope():
    captured: list[bool] = []

    def fake_inner(*a, **kw):
        try:
            RequestScope._store()
            captured.append(True)
        except RuntimeError:
            captured.append(False)
        return {"success": False, "pages_filled": 0, "field_types": [], "screening_questions": []}

    with patch("jobpulse.applicator._apply_job_inner", fake_inner):
        from jobpulse.applicator import apply_job
        apply_job(url="https://example.com/job", dry_run=True)
    assert captured == [True], "apply_job must open a request scope before running pipeline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_applicator_request_scope.py -v`
Expected: FAIL — `_apply_job_inner` does not exist OR scope is not opened.

- [ ] **Step 3: Write minimal implementation**

In `jobpulse/applicator.py`, locate the existing `apply_job` function. Extract its body into a new private `_apply_job_inner(url, dry_run, ...)` and wrap the original `apply_job` with `request_scope()`:

```python
# jobpulse/applicator.py — at top
from shared.memory_layer._request_scope import request_scope

# the current apply_job body becomes _apply_job_inner unchanged.
# the new apply_job wrapper:
def apply_job(url: str, dry_run: bool = True, **kwargs) -> dict:
    with request_scope():
        return _apply_job_inner(url=url, dry_run=dry_run, **kwargs)
```

Do not change any caller. Do not change return shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_applicator_request_scope.py tests/jobpulse/ -v -k applicator`
Expected: new test PASSES, existing applicator tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/applicator.py tests/jobpulse/test_applicator_request_scope.py
git commit -m "feat(applicator): open RequestScope around apply_job for per-application caching"
```

---

### Task 10: Bloom negative cache

**Files:**
- Create: `shared/memory_layer/_bloom.py`
- Test: `tests/shared/memory_layer/test_bloom.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_bloom.py
from shared.memory_layer._bloom import BloomNegativeCache


def test_unseen_returns_false():
    b = BloomNegativeCache(capacity=1000, error_rate=0.01)
    assert b.maybe_seen("never-added") is False


def test_added_returns_true():
    b = BloomNegativeCache(capacity=1000, error_rate=0.01)
    b.add("seen.com")
    assert b.maybe_seen("seen.com") is True


def test_no_false_negatives_for_added_items():
    b = BloomNegativeCache(capacity=10_000, error_rate=0.001)
    items = [f"domain-{i}.com" for i in range(5_000)]
    for it in items:
        b.add(it)
    for it in items:
        assert b.maybe_seen(it) is True


def test_persist_and_load(tmp_path):
    path = tmp_path / "bloom.bin"
    b = BloomNegativeCache(capacity=1000, error_rate=0.01)
    b.add("greenhouse.io")
    b.save(str(path))
    b2 = BloomNegativeCache.load(str(path))
    assert b2.maybe_seen("greenhouse.io") is True
    assert b2.maybe_seen("nope.com") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_bloom.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_bloom.py
"""Bloom filter as a negative cache.

Use case: 'have we ever seen this domain in FormExperienceDB?' — a hit means
maybe (consult real DB), a miss means definitely no (skip the lookup).
Saves the 10–50ms Qdrant/SQLite round-trip on cold domains.

No false negatives. False-positive rate is the configured error_rate.
"""
from __future__ import annotations

import hashlib
import math
import struct
from pathlib import Path


class BloomNegativeCache:
    def __init__(self, capacity: int, error_rate: float, _bits: bytearray | None = None, _hashes: int | None = None, _size: int | None = None):
        if _bits is not None and _hashes is not None and _size is not None:
            self._bits = _bits
            self._k = _hashes
            self._m = _size
            return
        self._m = max(8, int(-(capacity * math.log(error_rate)) / (math.log(2) ** 2)))
        self._k = max(1, int((self._m / capacity) * math.log(2)))
        self._bits = bytearray((self._m + 7) // 8)

    def _indices(self, item: str) -> list[int]:
        h = hashlib.blake2b(item.encode("utf-8"), digest_size=16).digest()
        h1 = int.from_bytes(h[:8], "big")
        h2 = int.from_bytes(h[8:], "big")
        return [(h1 + i * h2) % self._m for i in range(self._k)]

    def add(self, item: str) -> None:
        for idx in self._indices(item):
            self._bits[idx // 8] |= 1 << (idx % 8)

    def maybe_seen(self, item: str) -> bool:
        for idx in self._indices(item):
            if not (self._bits[idx // 8] & (1 << (idx % 8))):
                return False
        return True

    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.write(struct.pack(">II", self._m, self._k))
            f.write(self._bits)

    @classmethod
    def load(cls, path: str) -> "BloomNegativeCache":
        with open(path, "rb") as f:
            m, k = struct.unpack(">II", f.read(8))
            bits = bytearray(f.read())
        return cls(capacity=0, error_rate=0.0, _bits=bits, _hashes=k, _size=m)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_bloom.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_bloom.py tests/shared/memory_layer/test_bloom.py
git commit -m "feat(memory): BloomNegativeCache for fast 'domain-unseen' early return"
```

---

### Task 11: Page-entry Prefetcher

**Files:**
- Create: `shared/memory_layer/_prefetcher.py`
- Test: `tests/shared/memory_layer/test_prefetcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_prefetcher.py
import threading
import time

from shared.memory_layer._prefetcher import Prefetcher
from shared.memory_layer._request_scope import RequestScope, request_scope


def test_warm_runs_jobs_in_parallel():
    p = Prefetcher(max_workers=4)
    timings: list[float] = []

    def slow(label: str):
        start = time.perf_counter()
        time.sleep(0.05)
        timings.append(time.perf_counter() - start)
        return f"{label}-result"

    with request_scope():
        p.warm({
            "selectors": lambda: slow("sel"),
            "screening": lambda: slow("scr"),
            "nav": lambda: slow("nav"),
        })
        elapsed = sum(timings)
        wall = max(timings)
        assert elapsed > 0.12
        assert wall < 0.15  # parallel, not serial
        assert RequestScope.get_or_set("selectors", lambda: "MISS") == "sel-result"
        assert RequestScope.get_or_set("screening", lambda: "MISS") == "scr-result"
        assert RequestScope.get_or_set("nav", lambda: "MISS") == "nav-result"


def test_warm_failure_does_not_break_others():
    p = Prefetcher(max_workers=2)

    def boom():
        raise RuntimeError("fetch failed")

    with request_scope():
        p.warm({
            "good": lambda: "ok",
            "bad": boom,
        })
        assert RequestScope.get_or_set("good", lambda: "MISS") == "ok"
        assert RequestScope.get_or_set("bad", lambda: "DEFAULT") == "DEFAULT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_prefetcher.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
# shared/memory_layer/_prefetcher.py
"""Parallel prefetch into the active RequestScope.

Triggered when the navigator detects a page so memory the form filler will
need is warm by the time field discovery runs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from shared.logging_config import get_logger
from shared.memory_layer._request_scope import RequestScope

logger = get_logger(__name__)


class Prefetcher:
    def __init__(self, max_workers: int = 4):
        self._max_workers = max_workers

    def warm(self, jobs: dict[str, Callable[[], object]]) -> None:
        """Run jobs in parallel; store each result under its key in RequestScope.

        Failures are logged and skipped — never raised. RequestScope.get_or_set
        will fall through to its factory if a key was not warmed.
        """
        if not jobs:
            return
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(jobs))) as pool:
            futs = {pool.submit(fn): key for key, fn in jobs.items()}
            for f in as_completed(futs):
                key = futs[f]
                try:
                    result = f.result()
                    RequestScope.get_or_set(key, lambda r=result: r)
                except Exception as e:
                    logger.warning("prefetch_failed", extra={"key": key, "error": f"{type(e).__name__}: {e}"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_prefetcher.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_prefetcher.py tests/shared/memory_layer/test_prefetcher.py
git commit -m "feat(memory): Prefetcher warms RequestScope keys in parallel"
```

---

### Task 12: Wire Prefetcher into navigator page-detected event

**Files:**
- Modify: `jobpulse/application_orchestrator_pkg/_navigator.py`
- Test: `tests/jobpulse/test_navigator_prefetch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_navigator_prefetch.py
from unittest.mock import MagicMock

from shared.memory_layer._request_scope import RequestScope, request_scope


def test_navigator_prefetches_on_page_detected(monkeypatch):
    from jobpulse.application_orchestrator_pkg import _navigator

    warmed: list[str] = []

    class FakePrefetcher:
        def warm(self, jobs):
            warmed.extend(jobs.keys())

    monkeypatch.setattr(_navigator, "_prefetcher", FakePrefetcher())

    fake_page = MagicMock()
    fake_page.url = "https://greenhouse.io/jobs/123"

    with request_scope():
        _navigator.on_page_detected(page=fake_page, page_type="application_form", platform="greenhouse")
    assert {"selectors", "screening_cache", "nav_replay", "corrections"}.issubset(set(warmed))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_navigator_prefetch.py -v`
Expected: FAIL — `on_page_detected` does not exist OR does not emit prefetch.

- [ ] **Step 3: Write minimal implementation**

In `jobpulse/application_orchestrator_pkg/_navigator.py`, add a module-level prefetcher and an `on_page_detected` hook called from the existing page-detection code path. Locate where the navigator currently classifies a page (search for `page_type` assignment) and insert the call after the type is known but before form scanning starts:

```python
# jobpulse/application_orchestrator_pkg/_navigator.py — at top
from urllib.parse import urlparse

from shared.memory_layer._prefetcher import Prefetcher
from shared.memory_layer._request_scope import RequestScope

_prefetcher = Prefetcher(max_workers=4)


def on_page_detected(page, page_type: str, platform: str) -> None:
    """Warm memory the form filler will likely need.

    Best-effort and non-blocking-ish: each warm job is short and runs in parallel.
    Failures are logged inside Prefetcher and never propagate.
    """
    domain = urlparse(page.url).netloc

    def _selectors():
        from jobpulse.form_experience_db import FormExperienceDB
        return FormExperienceDB().get_container(domain)

    def _screening_cache():
        from jobpulse.screening_semantic_cache import get_common_questions
        return get_common_questions(platform=platform, limit=20)

    def _nav_replay():
        from jobpulse.navigation_learner import NavigationLearner
        return NavigationLearner().get_replay(domain=domain)

    def _corrections():
        from jobpulse.agent_rules import AgentRulesDB
        return AgentRulesDB().recent(domain=domain, limit=10)

    _prefetcher.warm({
        "selectors": _selectors,
        "screening_cache": _screening_cache,
        "nav_replay": _nav_replay,
        "corrections": _corrections,
    })
```

Then find the existing page-classification call site in `_navigator.py` and add the invocation:

```python
# inside existing navigate_to_form (or equivalent) after page_type is determined:
on_page_detected(page=current_page, page_type=page_type, platform=platform)
```

If any of the listed helper functions (`get_common_questions`, `NavigationLearner().get_replay`, `AgentRulesDB().recent`) does not yet exist with that exact name, replace with the closest existing read API in that module. The test only asserts the four keys are warmed — pick whatever real read makes sense for each.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_navigator_prefetch.py tests/jobpulse/ -v -k navigator`
Expected: new test PASSES, existing navigator tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/application_orchestrator_pkg/_navigator.py tests/jobpulse/test_navigator_prefetch.py
git commit -m "feat(nav): prefetch selectors/screening/nav-replay/corrections on page-detect"
```

---

## Phase 5 — Robustness

### Task 13: Wrap L3/L4 reads with existing CircuitBreaker

**Files:**
- Modify: `shared/memory_layer/_qdrant_store.py`
- Modify: `shared/memory_layer/_neo4j_store.py`
- Test: `tests/shared/memory_layer/test_store_circuit_breakers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/shared/memory_layer/test_store_circuit_breakers.py
import pytest

from shared.circuit_breaker import CircuitState
from shared.memory_layer import _qdrant_store


def test_qdrant_repeated_failures_open_breaker(monkeypatch):
    breaker = _qdrant_store._breaker
    breaker._state = CircuitState.CLOSED
    breaker._failure_count = 0

    def boom(*a, **kw):
        raise ConnectionError("qdrant down")

    monkeypatch.setattr(_qdrant_store, "_call_qdrant_search", boom)
    store = _qdrant_store.QdrantStore.__new__(_qdrant_store.QdrantStore)
    store._fallback = []

    for _ in range(5):
        result = store.search(query="x", k=1)
        assert result == []
    assert breaker.state == CircuitState.OPEN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/memory_layer/test_store_circuit_breakers.py -v`
Expected: FAIL — `_breaker` attribute does not exist on the module yet.

- [ ] **Step 3: Write minimal implementation**

In `_qdrant_store.py` add a module-level breaker and route the existing search through `breaker.call`:

```python
# shared/memory_layer/_qdrant_store.py — near top, beside imports
from shared.circuit_breaker import CircuitBreaker

_breaker = CircuitBreaker(name="qdrant_store", failure_threshold=5, cooldown_seconds=30.0)


def _call_qdrant_search(client, query: str, k: int):
    """Pure delegate so the test can monkeypatch this single function."""
    # move the existing client.search(...) call here unchanged
    ...


# inside QdrantStore.search method, replace the direct call:
def search(self, query: str, k: int = 5) -> list[dict]:
    fallback = getattr(self, "_fallback", [])
    return _breaker.call(
        fn=lambda: _call_qdrant_search(self._client, query, k),
        fallback=fallback,
    )
```

Apply the same pattern to `_neo4j_store.py` with `name="neo4j_store"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/shared/memory_layer/test_store_circuit_breakers.py tests/shared/memory_layer/ -v`
Expected: new test PASSES, all existing memory_layer tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_qdrant_store.py shared/memory_layer/_neo4j_store.py tests/shared/memory_layer/test_store_circuit_breakers.py
git commit -m "feat(memory): wrap Qdrant + Neo4j search with CircuitBreaker"
```

---

### Task 14: TTL invalidation on FormExperienceDB selectors

**Files:**
- Modify: `jobpulse/form_experience_db.py`
- Test: `tests/jobpulse/test_form_experience_ttl.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/jobpulse/test_form_experience_ttl.py
import time

from jobpulse.form_experience_db import FormExperienceDB


def test_get_container_returns_none_for_stale_selector(tmp_path, monkeypatch):
    monkeypatch.setenv("FORM_EXP_SELECTOR_TTL_DAYS", "0.00001")  # ~1 second
    db = FormExperienceDB(db_path=str(tmp_path / "fed.db"))
    db.store_container(domain="example.com", platform="generic", selector="#app")
    assert db.get_container("example.com") == "#app"
    time.sleep(1.5)
    assert db.get_container("example.com") is None


def test_get_container_returns_fresh_selector(tmp_path):
    db = FormExperienceDB(db_path=str(tmp_path / "fed.db"))
    db.store_container(domain="example.com", platform="generic", selector="#app")
    assert db.get_container("example.com") == "#app"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobpulse/test_form_experience_ttl.py -v`
Expected: FAIL — TTL is not enforced.

- [ ] **Step 3: Write minimal implementation**

In `jobpulse/form_experience_db.py`, find `get_container` and add a TTL filter on the read. The container row already has a timestamp column (verify column name; typical name is `last_used` or `updated_at`). If the column does not exist, add it via migration:

```python
# jobpulse/form_experience_db.py — at top
import os

_DEFAULT_TTL_DAYS = 30.0


def _ttl_days() -> float:
    return float(os.getenv("FORM_EXP_SELECTOR_TTL_DAYS", _DEFAULT_TTL_DAYS))


# in __init__ migration block:
def _migrate(self) -> None:
    with self._conn() as c:
        c.execute("CREATE TABLE IF NOT EXISTS containers (...existing schema...)")
        # idempotent column addition
        cols = {row[1] for row in c.execute("PRAGMA table_info(containers)").fetchall()}
        if "stored_at" not in cols:
            c.execute("ALTER TABLE containers ADD COLUMN stored_at REAL DEFAULT 0")


# in store_container, set stored_at:
def store_container(self, domain: str, platform: str, selector: str) -> None:
    with self._conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO containers(domain, platform, selector, stored_at) VALUES (?, ?, ?, ?)",
            (domain, platform, selector, time.time()),
        )


# in get_container, filter by TTL:
def get_container(self, domain: str) -> str | None:
    cutoff = time.time() - (_ttl_days() * 86400.0)
    with self._conn() as c:
        row = c.execute(
            "SELECT selector FROM containers WHERE domain = ? AND stored_at >= ?",
            (domain, cutoff),
        ).fetchone()
    return row["selector"] if row else None
```

If the existing schema and accessor signatures differ from the names above, match the real ones — the contract is: store records `time.time()`; reads filter by `cutoff = now - ttl`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobpulse/test_form_experience_ttl.py tests/jobpulse/ -v -k form_experience`
Expected: new tests PASS, existing form_experience tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add jobpulse/form_experience_db.py tests/jobpulse/test_form_experience_ttl.py
git commit -m "feat(form-exp): TTL invalidation for stale container selectors (default 30d)"
```

---

## Phase 6 — Documentation + smoke

### Task 15: Update memory_layer CLAUDE.md

**Files:**
- Modify: `shared/memory_layer/CLAUDE.md`

- [ ] **Step 1: Add a "Coordination layers" section**

Append the following to `shared/memory_layer/CLAUDE.md`:

```markdown
## Coordination layers (added 2026-05-01)

Added on top of the 3 storage engines — additive, never replacements.

| Module | Purpose | Env kill switch |
|--------|---------|-----------------|
| `_retrieval_tracker.py` | Per-call latency + tier + hit/miss recorder. Backed by `data/retrieval_tracker.db`. | `RETRIEVAL_TRACKING=false` |
| `_request_scope.py` | Thread-local cache cleared per `apply_job()`. Wrap with `with request_scope():`. | n/a (caller-controlled) |
| `_write_queue.py` | Async write queue for `post_apply_hook` and other learning writes. Failures persist to `data/write_queue.db`. | n/a |
| `_pool.py` | Singleton Qdrant client + Neo4j driver. Re-used for the daemon lifetime. | n/a |
| `_prefetcher.py` | Parallel warm of RequestScope keys on navigator page-detect events. | n/a |
| `_bloom.py` | Bloom-filter negative cache for "domain unseen". | n/a |

**Latency report:** `python scripts/retrieval_latency_report.py --window-hours 24` prints p50/p95/p99 + hit-rate per tier.

**Failed write replay:** query `data/write_queue.db` `failed_writes` table; manually retry once root cause is fixed.

**Circuit breakers:** Qdrant + Neo4j stores wrap reads in `shared/circuit_breaker.CircuitBreaker`. Threshold 5 failures, 30 s cooldown. Falls back to empty result on OPEN.
```

- [ ] **Step 2: Commit**

```bash
git add shared/memory_layer/CLAUDE.md
git commit -m "docs(memory): document coordination layers (tracker, scope, queue, pool, prefetcher, bloom)"
```

---

### Task 16: End-to-end smoke

**Files:** none — operational.

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest tests/shared/memory_layer/ tests/jobpulse/ -v`
Expected: 100% green.

- [ ] **Step 2: Run a real apply with all flags on**

Run: `python -m jobpulse.runner job-apply-next` against one queued job. Confirm in the logs:
- `qdrant_client_initialized` / `neo4j_driver_initialized` fires once at most
- `prefetch_failed` does NOT appear
- `write_queue_job_failed` does NOT appear (or appears with a known reason)
- Application completes and `data/retrieval_tracker.db` has rows for L1/L2/L3 (and L4 if Neo4j is in the path)

- [ ] **Step 3: Generate the post-change latency report**

Run: `python scripts/retrieval_latency_report.py --window-hours 1`
Expected: p95 for L3_qdrant + L4_neo4j drops vs Phase 1 baseline (most of the win is the singleton client). L1 hit rate during page-detect should be visibly higher than baseline due to prefetch.

- [ ] **Step 4: Verify failed-write replay works**

Manually break Notion (set `NOTION_API_KEY=invalid`) and run one apply. Confirm `data/write_queue.db` `failed_writes` has the row, the apply completes successfully (the hook didn't block on Notion), and restoring the API key + re-running the queued job recovers state.

- [ ] **Step 5: Commit any operational notes captured**

```bash
git commit --allow-empty -m "chore(memory): end-to-end smoke complete; baseline + post measurements recorded"
```

---

## Self-Review

**Spec coverage:**
- Per-tier instrumentation → Task 1, 2, 3
- Async writes for post_apply_hook → Task 5, 6
- Connection pooling for Qdrant + Neo4j → Task 7
- Request-scope dedup cache → Task 8, 9
- Predictive prefetch on page-detect → Task 11, 12
- Negative cache for domain-unseen → Task 10
- Circuit breakers on L3/L4 → Task 13
- TTL invalidation for stale selectors → Task 14
- Documentation → Task 15
- Smoke + report → Task 16

**Deferred (not in this plan, by design):**
- Schema-drift guards on memory reads — defer until Phase 1 baseline shows it's a real failure mode
- Speculative parallel cache+LLM — defer until distillation work makes the LLM call cheaper anyway
- L1 (in-memory) tier coordination — current `hybrid_search` already loads 17K embeddings into numpy at startup; that IS the L1 tier and works; do not refactor

**Type consistency check:** All new modules use the `_underscore` private convention matching existing memory_layer files. All public entry points (`get_retrieval_tracker`, `get_write_queue`, `get_qdrant_client`, `get_neo4j_driver`, `request_scope`, `RequestScope`, `Prefetcher`, `BloomNegativeCache`) are stable across the tasks that reference them. `RetrievalTracker.record()` returns the same `_RecordContext` type used in tests.

**Sequencing rationale:**
- Phase 1 first because measurement informs whether Phases 2/3/4 are worth doing in the proposed order. If the baseline shows L4 (Neo4j) is fine but `post_apply_hook` is the actual hot path, do Phase 2 before Phase 3.
- Phase 5 last because circuit breakers + TTL only matter once the rest of the system is stable enough to fail in interesting ways.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-01-memory-retrieval-robustness.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
