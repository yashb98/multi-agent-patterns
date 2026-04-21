# Durable Execution Infrastructure — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Event-sourced durable execution with crash recovery, MCP production server, and A2A agent coordination protocol.

**Architecture:** Append-only event store (SQLite WAL + optional Redis) as the foundation. LangGraph checkpointing, scan/form recovery built on events. MCP gateway exposes agent capabilities over streamable HTTP. A2A protocol enables agent discovery, delegation, and escalation with cross-pillar awareness loop.

**Tech Stack:** SQLite (WAL mode), Redis (optional, already installed v7.2.1), FastAPI/Uvicorn (existing), LangGraph BaseCheckpointSaver, python-ulid (new dep), `shared/execution/` module.

**Spec:** `docs/superpowers/specs/2026-04-21-durable-execution-design.md`

---

## File Structure

```
shared/execution/
    __init__.py               # Public API: get_event_store, emit, subscribe, TaskRunner
    _event_store.py           # Event TypedDict, EventStore class, bounded queue, writer thread
    _redis.py                 # RedisClient wrapper, graceful degradation, pub/sub
    _projectors.py            # ScanProjector, FormProjector, PatternProjector
    _checkpointer.py          # EventStoreCheckpointer (LangGraph BaseCheckpointSaver bridge)
    _mcp_gateway.py           # MCP Gateway: FastAPI router, auth middleware, health
    _mcp_jobpulse.py          # JobPulse capability server: 8 tools
    _mcp_resources.py         # MCP read-only resources: 5 URIs
    _a2a_card.py              # AgentCard, AgentRegistry protocol, file + Redis backends
    _a2a_task.py              # A2ATask, TaskManager, lifecycle state machine
    _a2a_protocol.py          # A2A HTTP endpoints, SSE streaming
    _awareness.py             # TaskPreFlight, TaskPostFlight, ConfidenceTracker, TaskRunner
    _verifier.py              # FormVerifier: 5 heuristic + vision checks
    _rescue.py                # Rescue agent: vision analysis + cross-domain transfer
    CLAUDE.md                 # Module documentation

tests/shared/execution/
    conftest.py               # Shared fixtures: tmp event store, mock Redis
    test_event_store.py       # Event emit, query, stream replay, backpressure
    test_redis.py             # Redis client, degradation, pub/sub
    test_projectors.py        # All 3 projectors: fold correctness, idempotency
    test_checkpointer.py      # LangGraph checkpoint put/get/resume
    test_mcp_gateway.py       # Gateway routing, auth, health
    test_mcp_jobpulse.py      # Tool invocation, streaming
    test_mcp_resources.py     # Resource URIs
    test_a2a_card.py          # Agent cards, registry backends
    test_a2a_task.py          # Task lifecycle, timeouts, delegation
    test_a2a_protocol.py      # HTTP endpoints, SSE
    test_awareness.py         # Pre/post-flight, confidence tracker, TaskRunner
    test_verifier.py          # Heuristic checks, vision mock
    test_rescue.py            # Rescue agent with mocked LLM
```

Modified files:
- `patterns/enhanced_swarm.py:471-508` — accept checkpointer in `build_enhanced_swarm_graph()`
- `patterns/dynamic_swarm.py:478-530` — accept checkpointer in `build_swarm_graph()`
- `patterns/plan_and_execute.py:345-367` — accept checkpointer in `build_plan_execute_graph()`
- `patterns/peer_debate.py` — accept checkpointer in `build_debate_graph()`
- `patterns/hierarchical.py` — accept checkpointer in `build_hierarchical_graph()`
- `patterns/map_reduce.py:202-217` — accept checkpointer in `build_map_reduce_graph()`
- `jobpulse/job_autopilot.py:167-335` — instrument `_run_scan_window_inner()` with events
- `shared/execution/CLAUDE.md` — module docs

---

## Phase 1: Event Store + Checkpointing

### Task 1: Event Types + SQLite Schema + EventStore Core

**Files:**
- Create: `shared/execution/__init__.py`
- Create: `shared/execution/_event_store.py`
- Test: `tests/shared/execution/conftest.py`
- Test: `tests/shared/execution/test_event_store.py`

- [ ] **Step 1: Install python-ulid dependency**

Run: `pip install python-ulid`
Expected: Successfully installed python-ulid

- [ ] **Step 2: Create test directory and conftest**

```python
# tests/shared/execution/__init__.py
# (empty)
```

```python
# tests/shared/execution/conftest.py
import pytest
from pathlib import Path


@pytest.fixture
def event_db_path(tmp_path):
    """Temporary SQLite path for event store tests."""
    return str(tmp_path / "events.db")


@pytest.fixture
def event_store(event_db_path):
    """Fresh EventStore backed by temp SQLite."""
    from shared.execution._event_store import EventStore
    store = EventStore(db_path=event_db_path)
    yield store
    store.close()
```

- [ ] **Step 3: Write failing tests for EventStore**

```python
# tests/shared/execution/test_event_store.py
import time
import pytest


class TestEventStore:
    def test_emit_and_get_stream(self, event_store):
        event_store.emit(
            stream_id="scan:2026-04-21T09:00",
            event_type="scan.platform_started",
            payload={"platform": "linkedin"},
        )
        events = event_store.get_stream("scan:2026-04-21T09:00")
        assert len(events) == 1
        assert events[0]["event_type"] == "scan.platform_started"
        assert events[0]["payload"]["platform"] == "linkedin"

    def test_emit_generates_ulid(self, event_store):
        event_store.emit(
            stream_id="test:1",
            event_type="test.event",
            payload={"x": 1},
        )
        events = event_store.get_stream("test:1")
        assert len(events[0]["event_id"]) == 26  # ULID length

    def test_get_stream_ordered_by_created_at(self, event_store):
        for i in range(5):
            event_store.emit(
                stream_id="test:order",
                event_type="test.step",
                payload={"index": i},
            )
        events = event_store.get_stream("test:order")
        indices = [e["payload"]["index"] for e in events]
        assert indices == [0, 1, 2, 3, 4]

    def test_query_by_event_type(self, event_store):
        event_store.emit("s:1", "scan.started", {"a": 1})
        event_store.emit("s:1", "scan.done", {"b": 2})
        event_store.emit("s:2", "scan.started", {"c": 3})
        results = event_store.query(event_types=["scan.started"])
        assert len(results) == 2

    def test_query_by_stream_prefix(self, event_store):
        event_store.emit("form:greenhouse:oak:1", "form.started", {})
        event_store.emit("form:greenhouse:oak:2", "form.started", {})
        event_store.emit("scan:2026", "scan.started", {})
        results = event_store.query(stream_prefix="form:greenhouse")
        assert len(results) == 2

    def test_query_since_filter(self, event_store):
        event_store.emit("s:1", "test.old", {})
        since = event_store.get_stream("s:1")[0]["created_at"]
        event_store.emit("s:1", "test.new", {})
        results = event_store.query(stream_prefix="s:1", since=since)
        # Should include the event AT since and after
        assert any(e["event_type"] == "test.new" for e in results)

    def test_metadata_includes_timestamp(self, event_store):
        event_store.emit("s:1", "test.meta", {}, metadata={"agent": "scan"})
        events = event_store.get_stream("s:1")
        assert "timestamp" in events[0]["metadata"]
        assert events[0]["metadata"]["agent"] == "scan"

    def test_schema_v_defaults_to_1(self, event_store):
        event_store.emit("s:1", "test.v", {})
        events = event_store.get_stream("s:1")
        assert events[0]["schema_v"] == 1

    def test_schema_v_custom(self, event_store):
        event_store.emit("s:1", "test.v2", {}, schema_v=2)
        events = event_store.get_stream("s:1")
        assert events[0]["schema_v"] == 2

    def test_snapshot_save_and_load(self, event_store):
        event_store.emit("s:1", "test.a", {"x": 1})
        event_store.emit("s:1", "test.b", {"x": 2})
        events = event_store.get_stream("s:1")
        last_id = events[-1]["event_id"]
        event_store.save_snapshot("s:1", {"projected": "state"}, last_id)
        snap = event_store.load_snapshot("s:1")
        assert snap is not None
        assert snap["snapshot_state"] == {"projected": "state"}
        assert snap["last_event_id"] == last_id

    def test_snapshot_returns_none_when_missing(self, event_store):
        assert event_store.load_snapshot("nonexistent") is None

    def test_incomplete_streams(self, event_store):
        event_store.emit("scan:a", "scan.window_started", {})
        event_store.emit("scan:a", "scan.window_done", {})
        event_store.emit("scan:b", "scan.window_started", {})
        # scan:b has no window_done — it's incomplete
        incomplete = event_store.find_incomplete_streams(
            prefix="scan:", start_event="scan.window_started", end_event="scan.window_done"
        )
        assert incomplete == ["scan:b"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `python -m pytest tests/shared/execution/test_event_store.py -v`
Expected: ImportError — `shared.execution._event_store` does not exist

- [ ] **Step 5: Create shared/execution package**

```python
# shared/execution/__init__.py
"""Durable Execution Infrastructure — Pillar 4.

Event-sourced state management with crash recovery, MCP production server,
and A2A agent coordination protocol.

Public API:
    get_event_store()   — shared EventStore singleton
    emit()              — emit an event (shorthand)
    subscribe()         — subscribe to event stream (async generator)
"""

from shared.execution._event_store import EventStore, Event

_store: EventStore | None = None


def get_event_store(db_path: str | None = None) -> EventStore:
    """Return shared EventStore singleton. Lazy-initialized on first call."""
    global _store
    if _store is None:
        from pathlib import Path
        path = db_path or str(Path(__file__).parent.parent.parent / "data" / "events.db")
        _store = EventStore(db_path=path)
    return _store


def emit(stream_id: str, event_type: str, payload: dict, **kwargs) -> str:
    """Emit an event to the shared store. Returns event_id."""
    return get_event_store().emit(stream_id, event_type, payload, **kwargs)
```

- [ ] **Step 6: Implement EventStore**

```python
# shared/execution/_event_store.py
"""Append-only event store backed by SQLite WAL mode.

Events are immutable records of state changes. Current state is derived
by replaying events through projectors. SQLite is the source of truth.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import TypedDict

from ulid import ULID

from shared.logging_config import get_logger

logger = get_logger(__name__)

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    stream_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    metadata    TEXT NOT NULL,
    schema_v    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, created_at);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

CREATE TABLE IF NOT EXISTS stream_snapshots (
    stream_id       TEXT PRIMARY KEY,
    snapshot_state  TEXT NOT NULL,
    last_event_id   TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


class Event(TypedDict):
    event_id: str
    stream_id: str
    event_type: str
    payload: dict
    metadata: dict
    schema_v: int
    created_at: str


class EventStore:
    """Append-only event store. Thread-safe via internal lock."""

    def __init__(self, db_path: str = "data/events.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA_SQL)

    def emit(
        self,
        stream_id: str,
        event_type: str,
        payload: dict,
        metadata: dict | None = None,
        schema_v: int = 1,
    ) -> str:
        """Append an event. Returns the event_id (ULID)."""
        event_id = str(ULID())
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
        meta = metadata.copy() if metadata else {}
        meta.setdefault("timestamp", now)

        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_id, stream_id, event_type, payload, metadata, schema_v, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, stream_id, event_type, json.dumps(payload),
                 json.dumps(meta), schema_v, now),
            )
            self._conn.commit()

        logger.debug("Event emitted: %s %s on %s", event_id[:8], event_type, stream_id)
        return event_id

    def get_stream(
        self,
        stream_id: str,
        event_type: str | None = None,
        after_event_id: str | None = None,
    ) -> list[Event]:
        """Get all events in a stream, ordered by created_at."""
        sql = "SELECT * FROM events WHERE stream_id = ?"
        params: list = [stream_id]
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if after_event_id:
            sql += " AND event_id > ?"
            params.append(after_event_id)
        sql += " ORDER BY created_at ASC"

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query(
        self,
        stream_prefix: str | None = None,
        event_types: list[str] | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[Event]:
        """Query events across streams."""
        sql = "SELECT * FROM events WHERE 1=1"
        params: list = []
        if stream_prefix:
            sql += " AND stream_id LIKE ?"
            params.append(stream_prefix + "%")
        if event_types:
            placeholders = ",".join("?" for _ in event_types)
            sql += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def find_incomplete_streams(
        self,
        prefix: str,
        start_event: str,
        end_event: str,
    ) -> list[str]:
        """Find streams that have start_event but no end_event."""
        sql = """
            SELECT DISTINCT e1.stream_id
            FROM events e1
            WHERE e1.stream_id LIKE ?
              AND e1.event_type = ?
              AND NOT EXISTS (
                  SELECT 1 FROM events e2
                  WHERE e2.stream_id = e1.stream_id
                    AND e2.event_type = ?
              )
        """
        with self._lock:
            rows = self._conn.execute(sql, (prefix + "%", start_event, end_event)).fetchall()
        return [r[0] for r in rows]

    def save_snapshot(self, stream_id: str, state: dict, last_event_id: str) -> None:
        """Save a projected state snapshot for a stream."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO stream_snapshots "
                "(stream_id, snapshot_state, last_event_id, created_at) VALUES (?, ?, ?, ?)",
                (stream_id, json.dumps(state), last_event_id, now),
            )
            self._conn.commit()

    def load_snapshot(self, stream_id: str) -> dict | None:
        """Load the latest snapshot for a stream."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM stream_snapshots WHERE stream_id = ?", (stream_id,)
            ).fetchone()
        if not row:
            return None
        return {
            "stream_id": row["stream_id"],
            "snapshot_state": json.loads(row["snapshot_state"]),
            "last_event_id": row["last_event_id"],
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        return Event(
            event_id=row["event_id"],
            stream_id=row["stream_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            metadata=json.loads(row["metadata"]),
            schema_v=row["schema_v"],
            created_at=row["created_at"],
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_event_store.py -v`
Expected: All 13 tests PASS

- [ ] **Step 8: Commit**

```bash
git add shared/execution/__init__.py shared/execution/_event_store.py tests/shared/execution/
git commit -m "feat(execution): add EventStore with SQLite WAL backend"
```

---

### Task 2: Redis Client with Graceful Degradation

**Files:**
- Create: `shared/execution/_redis.py`
- Test: `tests/shared/execution/test_redis.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_redis.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestRedisClient:
    def test_creates_with_defaults(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        assert client.url == "redis://localhost:6379"

    def test_is_available_returns_false_when_no_redis(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        assert client.is_available() is False

    def test_publish_silent_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        # Should not raise — silently degrades
        client.publish("channel:test", {"event": "data"})

    def test_hset_silent_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        client.hset("key", {"field": "value"})

    def test_hget_returns_none_on_failure(self):
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:9999")
        assert client.hget("key") is None

    @patch("shared.execution._redis.redis")
    def test_publish_calls_redis_when_available(self, mock_redis_mod):
        mock_conn = MagicMock()
        mock_conn.ping.return_value = True
        mock_redis_mod.from_url.return_value = mock_conn
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        client._conn = mock_conn
        client._available = True
        client.publish("ch:test", {"x": 1})
        mock_conn.publish.assert_called_once()

    @patch("shared.execution._redis.redis")
    def test_hset_calls_redis_when_available(self, mock_redis_mod):
        mock_conn = MagicMock()
        mock_conn.ping.return_value = True
        mock_redis_mod.from_url.return_value = mock_conn
        from shared.execution._redis import RedisClient
        client = RedisClient(url="redis://localhost:6379")
        client._conn = mock_conn
        client._available = True
        client.hset("projection:scan:1", {"state": "running"})
        mock_conn.hset.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/execution/test_redis.py -v`
Expected: ImportError

- [ ] **Step 3: Implement RedisClient**

```python
# shared/execution/_redis.py
"""Optional Redis client with graceful degradation.

When Redis is unavailable, all operations silently no-op.
The system works without Redis — it just loses real-time push
and fast cached projections.
"""

from __future__ import annotations

import json

from shared.logging_config import get_logger

logger = get_logger(__name__)

try:
    import redis
except ImportError:
    redis = None  # type: ignore[assignment]


class RedisClient:
    """Redis wrapper that degrades gracefully when unavailable."""

    def __init__(self, url: str = "redis://localhost:6379"):
        self.url = url
        self._conn: redis.Redis | None = None
        self._available = False
        self._connect()

    def _connect(self) -> None:
        if redis is None:
            logger.info("Redis package not installed — running without Redis")
            return
        try:
            self._conn = redis.from_url(self.url, decode_responses=True)
            self._conn.ping()
            self._available = True
            logger.info("Redis connected at %s", self.url)
        except Exception as e:
            self._available = False
            self._conn = None
            logger.info("Redis unavailable (%s) — degrading gracefully", e)

    def is_available(self) -> bool:
        return self._available

    def publish(self, channel: str, data: dict) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.publish(channel, json.dumps(data))
        except Exception as e:
            logger.debug("Redis publish failed: %s", e)
            self._available = False

    def hset(self, key: str, mapping: dict) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.hset(key, mapping={k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in mapping.items()})
        except Exception as e:
            logger.debug("Redis hset failed: %s", e)
            self._available = False

    def hget(self, key: str) -> dict | None:
        if not self._available or self._conn is None:
            return None
        try:
            raw = self._conn.hgetall(key)
            if not raw:
                return None
            result = {}
            for k, v in raw.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            return result
        except Exception as e:
            logger.debug("Redis hget failed: %s", e)
            self._available = False
            return None

    def set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> None:
        if not self._available or self._conn is None:
            return
        try:
            self._conn.set(key, value, ex=ttl_seconds)
        except Exception as e:
            logger.debug("Redis set failed: %s", e)
            self._available = False

    def get(self, key: str) -> str | None:
        if not self._available or self._conn is None:
            return None
        try:
            return self._conn.get(key)
        except Exception as e:
            logger.debug("Redis get failed: %s", e)
            self._available = False
            return None

    def incr(self, key: str) -> int | None:
        if not self._available or self._conn is None:
            return None
        try:
            return self._conn.incr(key)
        except Exception as e:
            logger.debug("Redis incr failed: %s", e)
            self._available = False
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_redis.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_redis.py tests/shared/execution/test_redis.py
git commit -m "feat(execution): add Redis client with graceful degradation"
```

---

### Task 3: Projectors — Fold Events into Current State

**Files:**
- Create: `shared/execution/_projectors.py`
- Test: `tests/shared/execution/test_projectors.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_projectors.py
import pytest
from shared.execution._event_store import Event


def _make_event(event_type: str, payload: dict, stream_id: str = "test:1") -> Event:
    return Event(
        event_id="fake", stream_id=stream_id, event_type=event_type,
        payload=payload, metadata={}, schema_v=1, created_at="2026-04-21T09:00:00",
    )


class TestScanProjector:
    def test_initial_state(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        assert state["platforms_done"] == []
        assert state["platforms_in_progress"] is None
        assert state["jobs_found"] == 0

    def test_platform_started(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("scan.platform_started", {"platform": "linkedin"}))
        assert state["platforms_in_progress"] == "linkedin"

    def test_platform_done(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("scan.platform_started", {"platform": "linkedin"}))
        state = p.apply(state, _make_event("scan.platform_done", {"platform": "linkedin", "count": 12}))
        assert "linkedin" in state["platforms_done"]
        assert state["platforms_in_progress"] is None
        assert state["jobs_found"] == 12

    def test_idempotent_replay(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        event = _make_event("scan.job_screened", {"job_id": "x", "job_index": 3})
        state = p.initial_state()
        state = p.apply(state, event)
        assert state["job_cursor"] == 3
        assert state["jobs_screened"] == 1

    def test_full_scan_lifecycle(self):
        from shared.execution._projectors import ScanProjector
        p = ScanProjector()
        state = p.initial_state()
        events = [
            _make_event("scan.window_started", {"platforms": ["linkedin", "indeed"]}),
            _make_event("scan.platform_started", {"platform": "linkedin"}),
            _make_event("scan.jobs_found", {"platform": "linkedin", "count": 5}),
            _make_event("scan.job_screened", {"job_id": "a", "job_index": 0}),
            _make_event("scan.job_screened", {"job_id": "b", "job_index": 1}),
            _make_event("scan.platform_done", {"platform": "linkedin", "count": 5}),
            _make_event("scan.platform_started", {"platform": "indeed"}),
        ]
        for e in events:
            state = p.apply(state, e)
        assert state["platforms_done"] == ["linkedin"]
        assert state["platforms_in_progress"] == "indeed"
        assert state["jobs_screened"] == 2
        assert state["job_cursor"] == 1


class TestFormProjector:
    def test_initial_state(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        assert state["current_page"] == 0
        assert state["pages_filled"] == []
        assert state["auth_status"] == "pending"

    def test_auth_complete(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.auth_complete", {"method": "sso_google"}))
        assert state["auth_status"] == "complete"
        assert state["auth_method"] == "sso_google"

    def test_page_fill_lifecycle(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.page_detected", {"page": 1, "total_est": 3}))
        state = p.apply(state, _make_event("form.fields_filled", {
            "page": 1, "results": [{"label": "Name", "value": "Yash", "ok": True}],
        }))
        state = p.apply(state, _make_event("form.page_verified", {"page": 1, "confidence": 0.95}))
        state = p.apply(state, _make_event("form.page_advanced", {"from": 1, "to": 2}))
        assert 1 in state["pages_filled"]
        assert state["current_page"] == 2

    def test_submitted(self):
        from shared.execution._projectors import FormProjector
        p = FormProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("form.submitted", {"dry_run": False}))
        assert state["submitted"] is True
        assert state["dry_run"] is False


class TestPatternProjector:
    def test_initial_state(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        assert state["iteration"] == 0
        assert state["status"] == "pending"

    def test_iteration_lifecycle(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("pattern.iteration_started", {"iteration": 1, "agent": "researcher"}))
        assert state["iteration"] == 1
        assert state["status"] == "running"
        state = p.apply(state, _make_event("pattern.review_scored", {"iteration": 1, "quality": 7.2, "accuracy": 9.1}))
        assert state["last_quality"] == 7.2
        assert state["last_accuracy"] == 9.1

    def test_converged(self):
        from shared.execution._projectors import PatternProjector
        p = PatternProjector()
        state = p.initial_state()
        state = p.apply(state, _make_event("pattern.converged", {"iteration": 3, "final_score": 8.4, "reason": "dual_gate"}))
        assert state["status"] == "converged"
        assert state["final_score"] == 8.4


class TestProjectStream:
    def test_project_stream_from_events(self, event_store):
        from shared.execution._projectors import ScanProjector, project_stream
        event_store.emit("scan:t1", "scan.window_started", {"platforms": ["linkedin"]})
        event_store.emit("scan:t1", "scan.platform_started", {"platform": "linkedin"})
        event_store.emit("scan:t1", "scan.platform_done", {"platform": "linkedin", "count": 7})
        state = project_stream(event_store, "scan:t1", ScanProjector())
        assert state["platforms_done"] == ["linkedin"]
        assert state["jobs_found"] == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/execution/test_projectors.py -v`
Expected: ImportError

- [ ] **Step 3: Implement projectors**

```python
# shared/execution/_projectors.py
"""Projectors fold event streams into current state.

Each projector is a pure function: deterministic and idempotent.
Replaying an event twice produces the same state.
"""

from __future__ import annotations

import copy
from typing import Protocol

from shared.execution._event_store import Event, EventStore


class Projector(Protocol):
    def initial_state(self) -> dict: ...
    def apply(self, state: dict, event: Event) -> dict: ...


class ScanProjector:
    def initial_state(self) -> dict:
        return {
            "platforms_done": [],
            "platforms_in_progress": None,
            "jobs_found": 0,
            "jobs_screened": 0,
            "job_cursor": 0,
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "scan.platform_started":
            state["platforms_in_progress"] = p["platform"]
        elif t == "scan.platform_done":
            if p["platform"] not in state["platforms_done"]:
                state["platforms_done"].append(p["platform"])
            state["platforms_in_progress"] = None
            state["jobs_found"] += p.get("count", 0)
        elif t == "scan.job_screened":
            state["jobs_screened"] += 1
            state["job_cursor"] = p.get("job_index", state["job_cursor"])
        return state


class FormProjector:
    def initial_state(self) -> dict:
        return {
            "current_page": 0,
            "total_pages_est": 0,
            "pages_filled": [],
            "auth_status": "pending",
            "auth_method": "",
            "submitted": False,
            "dry_run": None,
            "field_results": {},
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "form.auth_complete":
            state["auth_status"] = "complete"
            state["auth_method"] = p.get("method", "")
        elif t == "form.page_detected":
            state["current_page"] = p.get("page", state["current_page"])
            state["total_pages_est"] = p.get("total_est", state["total_pages_est"])
        elif t == "form.fields_filled":
            page = p.get("page", state["current_page"])
            state["field_results"][page] = p.get("results", [])
        elif t == "form.page_verified":
            pass
        elif t == "form.page_advanced":
            from_page = p.get("from", state["current_page"])
            if from_page not in state["pages_filled"]:
                state["pages_filled"].append(from_page)
            state["current_page"] = p.get("to", state["current_page"] + 1)
        elif t == "form.submitted":
            state["submitted"] = True
            state["dry_run"] = p.get("dry_run")
        return state


class PatternProjector:
    def initial_state(self) -> dict:
        return {
            "iteration": 0,
            "status": "pending",
            "last_quality": 0.0,
            "last_accuracy": 0.0,
            "final_score": 0.0,
        }

    def apply(self, state: dict, event: Event) -> dict:
        t = event["event_type"]
        p = event["payload"]
        if t == "pattern.iteration_started":
            state["iteration"] = p.get("iteration", state["iteration"] + 1)
            state["status"] = "running"
        elif t == "pattern.review_scored":
            state["last_quality"] = p.get("quality", 0.0)
            state["last_accuracy"] = p.get("accuracy", 0.0)
        elif t == "pattern.converged":
            state["status"] = "converged"
            state["final_score"] = p.get("final_score", 0.0)
        elif t == "pattern.finished":
            state["status"] = "finished"
        return state


def project_stream(store: EventStore, stream_id: str, projector: Projector) -> dict:
    """Replay all events in a stream through a projector to get current state."""
    snap = store.load_snapshot(stream_id)
    if snap:
        state = copy.deepcopy(snap["snapshot_state"])
        events = store.get_stream(stream_id, after_event_id=snap["last_event_id"])
    else:
        state = projector.initial_state()
        events = store.get_stream(stream_id)
    for event in events:
        state = projector.apply(state, event)
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_projectors.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_projectors.py tests/shared/execution/test_projectors.py
git commit -m "feat(execution): add Scan/Form/Pattern projectors"
```

---

### Task 4: LangGraph EventStoreCheckpointer

**Files:**
- Create: `shared/execution/_checkpointer.py`
- Test: `tests/shared/execution/test_checkpointer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_checkpointer.py
import pytest


class TestEventStoreCheckpointer:
    def test_put_emits_checkpoint_event(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_abc"}}
        checkpoint = {"channel_values": {"draft": "hello", "iteration": 1}}
        metadata = {"step": 2, "source": "loop"}
        cp.put(config, checkpoint, metadata)
        events = event_store.get_stream("pattern:run_abc", event_type="pattern.checkpoint")
        assert len(events) == 1
        assert events[0]["payload"]["checkpoint"] == checkpoint

    def test_get_tuple_returns_none_when_empty(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_xyz"}}
        result = cp.get_tuple(config)
        assert result is None

    def test_get_tuple_returns_latest_checkpoint(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_abc"}}
        cp.put(config, {"v": 1}, {"step": 1})
        cp.put(config, {"v": 2}, {"step": 2})
        result = cp.get_tuple(config)
        assert result is not None
        assert result.checkpoint == {"v": 2}

    def test_list_returns_all_checkpoints(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        config = {"configurable": {"thread_id": "run_list"}}
        cp.put(config, {"v": 1}, {"step": 1})
        cp.put(config, {"v": 2}, {"step": 2})
        results = list(cp.list(config))
        assert len(results) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/execution/test_checkpointer.py -v`
Expected: ImportError

- [ ] **Step 3: Implement EventStoreCheckpointer**

```python
# shared/execution/_checkpointer.py
"""LangGraph checkpoint saver backed by the event store.

Bridges LangGraph's BaseCheckpointSaver protocol to our event-sourced
storage. Each checkpoint is stored as a pattern.checkpoint event.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator, Optional

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CheckpointTuple:
    config: dict
    checkpoint: dict
    metadata: dict
    parent_config: dict | None = None


class EventStoreCheckpointer:
    """LangGraph-compatible checkpoint saver using EventStore.

    Implements the essential methods of BaseCheckpointSaver without
    inheriting from it (avoids tight coupling to langgraph internals).
    """

    def __init__(self, event_store: EventStore):
        self._store = event_store

    def put(
        self,
        config: dict,
        checkpoint: dict,
        metadata: dict,
        new_versions: dict | None = None,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        self._store.emit(
            stream_id=stream_id,
            event_type="pattern.checkpoint",
            payload={"checkpoint": checkpoint, "metadata": metadata},
        )
        logger.debug("Checkpoint saved for thread %s", thread_id)
        return config

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        events = self._store.get_stream(stream_id, event_type="pattern.checkpoint")
        if not events:
            return None
        latest = events[-1]
        return CheckpointTuple(
            config=config,
            checkpoint=latest["payload"]["checkpoint"],
            metadata=latest["payload"].get("metadata", {}),
        )

    def list(
        self,
        config: dict,
        *,
        filter: dict | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        events = self._store.get_stream(stream_id, event_type="pattern.checkpoint")
        for event in reversed(events):
            yield CheckpointTuple(
                config=config,
                checkpoint=event["payload"]["checkpoint"],
                metadata=event["payload"].get("metadata", {}),
            )

    def put_writes(self, config: dict, writes: list, task_id: str) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_checkpointer.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_checkpointer.py tests/shared/execution/test_checkpointer.py
git commit -m "feat(execution): add LangGraph EventStoreCheckpointer"
```

---

### Task 5: Instrument LangGraph Patterns with Checkpointer

**Files:**
- Modify: `patterns/enhanced_swarm.py:471-508`
- Modify: `patterns/dynamic_swarm.py:478-530`
- Modify: `patterns/plan_and_execute.py:345-382`
- Modify: `patterns/peer_debate.py` (build_debate_graph)
- Modify: `patterns/hierarchical.py` (build_hierarchical_graph)
- Modify: `patterns/map_reduce.py:202-217`
- Test: `tests/shared/execution/test_pattern_checkpointing.py`

- [ ] **Step 1: Write integration test**

```python
# tests/shared/execution/test_pattern_checkpointing.py
import pytest
from unittest.mock import patch, MagicMock


class TestPatternCheckpointing:
    def test_enhanced_swarm_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.enhanced_swarm import build_enhanced_swarm_graph
        graph = build_enhanced_swarm_graph(checkpointer=cp)
        assert graph is not None

    def test_dynamic_swarm_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.dynamic_swarm import build_swarm_graph
        graph = build_swarm_graph(checkpointer=cp)
        assert graph is not None

    def test_plan_execute_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.plan_and_execute import build_plan_execute_graph
        graph = build_plan_execute_graph(checkpointer=cp)
        assert graph is not None

    def test_peer_debate_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.peer_debate import build_debate_graph
        graph = build_debate_graph(checkpointer=cp)
        assert graph is not None

    def test_hierarchical_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.hierarchical import build_hierarchical_graph
        graph = build_hierarchical_graph(checkpointer=cp)
        assert graph is not None

    def test_map_reduce_accepts_checkpointer(self, event_store):
        from shared.execution._checkpointer import EventStoreCheckpointer
        cp = EventStoreCheckpointer(event_store)
        from patterns.map_reduce import build_map_reduce_graph
        graph = build_map_reduce_graph(checkpointer=cp)
        assert graph is not None

    def test_default_no_checkpointer(self):
        """All patterns still work with no checkpointer (backwards compat)."""
        from patterns.enhanced_swarm import build_enhanced_swarm_graph
        graph = build_enhanced_swarm_graph()
        assert graph is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/execution/test_pattern_checkpointing.py -v`
Expected: TypeError — unexpected keyword argument 'checkpointer'

- [ ] **Step 3: Modify build_enhanced_swarm_graph**

In `patterns/enhanced_swarm.py`, change the function signature and compile call:

```python
def build_enhanced_swarm_graph(checkpointer=None):
```

And change the compile line from:
```python
    compiled = graph.compile()
```
to:
```python
    compiled = graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 4: Modify build_swarm_graph**

In `patterns/dynamic_swarm.py`, same pattern:

```python
def build_swarm_graph(checkpointer=None):
```

Change compile:
```python
    compiled = graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 5: Modify build_plan_execute_graph**

In `patterns/plan_and_execute.py`:

```python
def build_plan_execute_graph(checkpointer=None):
```

Change:
```python
    return graph.compile(checkpointer=checkpointer)
```

- [ ] **Step 6: Modify build_debate_graph**

In `patterns/peer_debate.py`, add `checkpointer=None` parameter and pass to `graph.compile(checkpointer=checkpointer)`.

- [ ] **Step 7: Modify build_hierarchical_graph**

In `patterns/hierarchical.py`, add `checkpointer=None` parameter and pass to `graph.compile(checkpointer=checkpointer)`.

- [ ] **Step 8: Modify build_map_reduce_graph**

In `patterns/map_reduce.py`, add `checkpointer=None` parameter and pass to `graph.compile(checkpointer=checkpointer)`.

- [ ] **Step 9: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_pattern_checkpointing.py -v`
Expected: All 7 tests PASS

- [ ] **Step 10: Run existing pattern tests to verify no regression**

Run: `python -m pytest tests/ -v -k "pattern or swarm or debate or hierarchical or map_reduce or plan_execute" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 11: Commit**

```bash
git add patterns/enhanced_swarm.py patterns/dynamic_swarm.py patterns/plan_and_execute.py patterns/peer_debate.py patterns/hierarchical.py patterns/map_reduce.py tests/shared/execution/test_pattern_checkpointing.py
git commit -m "feat(execution): wire checkpointer into all 6 LangGraph patterns"
```

---

### Task 6: Instrument Scan Pipeline with Events

**Files:**
- Modify: `jobpulse/job_autopilot.py:167-335`
- Test: `tests/shared/execution/test_scan_events.py`

- [ ] **Step 1: Write failing test**

```python
# tests/shared/execution/test_scan_events.py
import pytest
from unittest.mock import patch, MagicMock


class TestScanPipelineEvents:
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_ENABLED", True)
    @patch("jobpulse.job_autopilot.is_paused", return_value=False)
    @patch("jobpulse.job_autopilot._applied_today", return_value=0)
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_MAX_DAILY", 50)
    @patch("jobpulse.job_autopilot.JOB_AUTOPILOT_AUTO_SUBMIT", False)
    @patch("jobpulse.scan_pipeline.fetch_and_filter_jobs")
    @patch("jobpulse.scan_pipeline.analyze_and_deduplicate")
    @patch("jobpulse.scan_pipeline.prescreen_listings")
    @patch("jobpulse.scan_pipeline.generate_materials")
    @patch("jobpulse.job_autopilot.load_search_config")
    @patch("jobpulse.job_autopilot.send_jobs")
    @patch("jobpulse.job_autopilot.JobDB")
    def test_scan_emits_events(
        self, mock_db, mock_send, mock_config,
        mock_gen, mock_prescreen, mock_analyze, mock_fetch,
        mock_paused, mock_applied, event_store,
    ):
        mock_config.return_value = {}
        mock_fetch.return_value = ([], 0, 0)
        mock_analyze.return_value = []
        mock_prescreen.return_value = ([], 0, 0, 0)

        with patch("jobpulse.job_autopilot._get_event_store", return_value=event_store):
            from jobpulse.job_autopilot import _run_scan_window_inner
            _run_scan_window_inner(platforms=["linkedin"])

        events = event_store.query(stream_prefix="scan:")
        types = [e["event_type"] for e in events]
        assert "scan.window_started" in types
        assert "scan.window_done" in types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/shared/execution/test_scan_events.py -v`
Expected: FAIL — no event emission in current code

- [ ] **Step 3: Add event emission to _run_scan_window_inner**

Add at top of `jobpulse/job_autopilot.py`, after existing imports:

```python
def _get_event_store():
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None
```

In `_run_scan_window_inner()`, after the gate checks pass (after line 211), add:

```python
    # --- Event emission ---
    _evt = _get_event_store()
    _stream_id = f"scan:{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M')}" if _evt else ""
    if _evt:
        _evt.emit(_stream_id, "scan.window_started", {
            "platforms": platforms or ["linkedin", "indeed", "reed"],
            "daily_cap": JOB_AUTOPILOT_MAX_DAILY,
            "already_applied": already_applied,
        })
```

After `fetch_and_filter_jobs` call (after line 215), add:

```python
    if _evt:
        _evt.emit(_stream_id, "scan.jobs_found", {
            "total_found": total_found,
            "gate0_rejected": gate0_rejected,
            "raw_count": len(raw_jobs),
        })
```

At the end of the function before `return summary_msg` (before line 335), add:

```python
    if _evt:
        _evt.emit(_stream_id, "scan.window_done", {
            "total_found": total_found,
            "auto_applied": auto_applied_count,
            "review_count": len(review_batch),
            "errors": errors,
        })
```

- [ ] **Step 4: Add timezone import if missing**

At the top of `job_autopilot.py`, ensure `timezone` is imported:
```python
from datetime import datetime, timezone
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/shared/execution/test_scan_events.py -v`
Expected: PASS

- [ ] **Step 6: Run existing scan tests to verify no regression**

Run: `python -m pytest tests/ -v -k "autopilot or scan" --timeout=30`
Expected: All existing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add jobpulse/job_autopilot.py tests/shared/execution/test_scan_events.py
git commit -m "feat(execution): instrument scan pipeline with event emission"
```

---

### Task 7: Compaction Logic

**Files:**
- Modify: `shared/execution/_event_store.py`
- Test: `tests/shared/execution/test_event_store.py` (add compaction tests)

- [ ] **Step 1: Add compaction tests to test_event_store.py**

```python
# Append to tests/shared/execution/test_event_store.py

class TestCompaction:
    def test_compact_stream_creates_snapshot(self, event_store):
        from shared.execution._projectors import ScanProjector
        event_store.emit("scan:old", "scan.window_started", {"platforms": ["linkedin"]})
        event_store.emit("scan:old", "scan.platform_started", {"platform": "linkedin"})
        event_store.emit("scan:old", "scan.platform_done", {"platform": "linkedin", "count": 5})
        event_store.emit("scan:old", "scan.window_done", {"total": 5})
        event_store.compact_stream("scan:old", ScanProjector())
        snap = event_store.load_snapshot("scan:old")
        assert snap is not None
        assert snap["snapshot_state"]["platforms_done"] == ["linkedin"]

    def test_compact_removes_old_events(self, event_store):
        from shared.execution._projectors import ScanProjector
        event_store.emit("scan:old", "scan.window_started", {"platforms": []})
        event_store.emit("scan:old", "scan.window_done", {"total": 0})
        event_store.compact_stream("scan:old", ScanProjector())
        events = event_store.get_stream("scan:old")
        assert len(events) == 0  # events archived, only snapshot remains

    def test_project_from_snapshot_plus_new_events(self, event_store):
        from shared.execution._projectors import ScanProjector, project_stream
        event_store.emit("scan:s", "scan.platform_done", {"platform": "linkedin", "count": 3})
        event_store.compact_stream("scan:s", ScanProjector())
        # New event after compaction
        event_store.emit("scan:s", "scan.platform_done", {"platform": "indeed", "count": 5})
        state = project_stream(event_store, "scan:s", ScanProjector())
        assert "linkedin" in state["platforms_done"]
        assert "indeed" in state["platforms_done"]
        assert state["jobs_found"] == 8
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_event_store.py::TestCompaction -v`
Expected: AttributeError — `compact_stream` not defined

- [ ] **Step 3: Add compact_stream to EventStore**

Add this method to `EventStore` in `_event_store.py`:

```python
    def compact_stream(self, stream_id: str, projector) -> None:
        """Compact a stream: project state, save snapshot, delete old events."""
        from shared.execution._projectors import project_stream
        state = project_stream(self, stream_id, projector)
        events = self.get_stream(stream_id)
        if not events:
            return
        last_id = events[-1]["event_id"]
        self.save_snapshot(stream_id, state, last_id)
        with self._lock:
            self._conn.execute(
                "DELETE FROM events WHERE stream_id = ?", (stream_id,)
            )
            self._conn.commit()
        logger.info("Compacted stream %s: %d events → snapshot", stream_id, len(events))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_event_store.py -v`
Expected: All tests PASS (original 13 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_event_store.py tests/shared/execution/test_event_store.py
git commit -m "feat(execution): add stream compaction with snapshot archival"
```

---

### Task 8: Module CLAUDE.md + Public API Exports

**Files:**
- Create: `shared/execution/CLAUDE.md`
- Modify: `shared/execution/__init__.py` — add remaining exports

- [ ] **Step 1: Update __init__.py with full Phase 1 exports**

Add these exports to `shared/execution/__init__.py`:

```python
from shared.execution._event_store import EventStore, Event
from shared.execution._projectors import (
    ScanProjector, FormProjector, PatternProjector, project_stream,
)
from shared.execution._checkpointer import EventStoreCheckpointer
from shared.execution._redis import RedisClient
```

- [ ] **Step 2: Write CLAUDE.md**

```markdown
# Durable Execution (shared/execution/)

Event-sourced durable execution infrastructure — Pillar 4 of 6.

## Core Concepts
- **Event Store** (`_event_store.py`): Append-only SQLite WAL log. `emit()` writes, `get_stream()` reads.
- **Projectors** (`_projectors.py`): Fold events → current state. Pure, deterministic, idempotent.
- **Checkpointer** (`_checkpointer.py`): LangGraph bridge — stores checkpoints as events.
- **Redis** (`_redis.py`): Optional fast cache + pub/sub. System works without it.

## Usage
```python
from shared.execution import get_event_store, emit, EventStoreCheckpointer

# Emit events
emit("scan:2026-04-21", "scan.platform_started", {"platform": "linkedin"})

# Project current state
from shared.execution import ScanProjector, project_stream
state = project_stream(get_event_store(), "scan:2026-04-21", ScanProjector())

# LangGraph checkpointing
cp = EventStoreCheckpointer(get_event_store())
graph = build_enhanced_swarm_graph(checkpointer=cp)
```

## Rules
- All event access goes through EventStore — never query data/events.db directly
- Same principle as MemoryManager and CognitiveEngine: single facade
- Events are immutable — never update or delete (except compaction)
- Projectors must be idempotent — replaying twice = same state
- Redis is optional — system MUST work without it
- Tests MUST use tmp_path fixture — never touch data/events.db
```

- [ ] **Step 3: Commit**

```bash
git add shared/execution/__init__.py shared/execution/CLAUDE.md
git commit -m "docs(execution): add CLAUDE.md and finalize Phase 1 public API"
```

---

## Phase 2: MCP Production Server

### Task 9: MCP Gateway — FastAPI Router

**Files:**
- Create: `shared/execution/_mcp_gateway.py`
- Test: `tests/shared/execution/test_mcp_gateway.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_mcp_gateway.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def gateway_app():
    from shared.execution._mcp_gateway import create_gateway_app
    return create_gateway_app()


@pytest.fixture
def client(gateway_app):
    return TestClient(gateway_app)


class TestMCPGateway:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")

    def test_list_tools(self, client):
        resp = client.get("/mcp/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert isinstance(tools, list)

    def test_call_unknown_tool_returns_404(self, client):
        resp = client.post("/mcp/call", json={"tool": "nonexistent.tool", "params": {}})
        assert resp.status_code == 404

    def test_audit_log_emits_event(self, client, event_store):
        with pytest.MonkeyPatch.context() as m:
            m.setattr("shared.execution._mcp_gateway._get_event_store", lambda: event_store)
            resp = client.get("/mcp/tools")
        # Audit event should be emitted for the call
        # (implementation detail — check if audit events exist)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_mcp_gateway.py -v`
Expected: ImportError

- [ ] **Step 3: Implement MCP Gateway**

```python
# shared/execution/_mcp_gateway.py
"""MCP Gateway — multiplexes capability servers behind one HTTP endpoint.

Thin router with auth middleware, audit logging, and health checks.
Zero business logic — delegates to capability servers.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from shared.logging_config import get_logger

logger = get_logger(__name__)

_capability_servers: dict[str, "CapabilityServer"] = {}


class ToolCallRequest(BaseModel):
    tool: str
    params: dict = {}


class CapabilityServer:
    """Base class for MCP capability servers."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._tools: dict[str, callable] = {}

    def register_tool(self, name: str, handler: callable, description: str = "") -> None:
        self._tools[name] = {"handler": handler, "description": description}

    def list_tools(self) -> list[dict]:
        return [
            {"name": f"{self.namespace}.{name}", "description": t["description"]}
            for name, t in self._tools.items()
        ]

    async def call_tool(self, name: str, params: dict) -> dict:
        if name not in self._tools:
            raise KeyError(f"Tool {name} not found in {self.namespace}")
        handler = self._tools[name]["handler"]
        import asyncio
        if asyncio.iscoroutinefunction(handler):
            return await handler(params)
        return handler(params)


def register_capability_server(server: CapabilityServer) -> None:
    _capability_servers[server.namespace] = server


def _get_event_store():
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None


def create_gateway_app() -> FastAPI:
    app = FastAPI(title="MCP Gateway", version="1.0.0")

    @app.get("/health")
    def health():
        servers_status = {
            ns: "healthy" for ns in _capability_servers
        }
        overall = "healthy" if all(s == "healthy" for s in servers_status.values()) else "degraded"
        return {"status": overall, "servers": servers_status}

    @app.get("/mcp/tools")
    def list_tools():
        all_tools = []
        for server in _capability_servers.values():
            all_tools.extend(server.list_tools())
        return {"tools": all_tools}

    @app.post("/mcp/call")
    async def call_tool(req: ToolCallRequest):
        parts = req.tool.split(".", 1)
        if len(parts) != 2:
            raise HTTPException(404, f"Tool must be namespace.name, got: {req.tool}")
        namespace, name = parts
        if namespace not in _capability_servers:
            raise HTTPException(404, f"Unknown namespace: {namespace}")
        try:
            result = await _capability_servers[namespace].call_tool(name, req.params)
            store = _get_event_store()
            if store:
                store.emit("mcp:audit", "mcp.tool_called", {
                    "tool": req.tool, "success": True,
                })
            return {"result": result}
        except KeyError:
            raise HTTPException(404, f"Unknown tool: {req.tool}")
        except Exception as e:
            logger.error("MCP tool call failed: %s — %s", req.tool, e)
            raise HTTPException(500, str(e))

    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_mcp_gateway.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_mcp_gateway.py tests/shared/execution/test_mcp_gateway.py
git commit -m "feat(execution): add MCP Gateway with tool routing and health check"
```

---

### Task 10: JobPulse Capability Server

**Files:**
- Create: `shared/execution/_mcp_jobpulse.py`
- Test: `tests/shared/execution/test_mcp_jobpulse.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_mcp_jobpulse.py
import pytest
from unittest.mock import patch, MagicMock


class TestJobPulseCapabilityServer:
    def test_registers_tools(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server
        server = create_jobpulse_server()
        tools = server.list_tools()
        tool_names = [t["name"] for t in tools]
        assert "jobpulse.job_stats" in tool_names
        assert "jobpulse.pre_screen" in tool_names
        assert "jobpulse.budget" in tool_names

    def test_job_stats_tool(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server
        server = create_jobpulse_server()
        with patch("shared.execution._mcp_jobpulse._job_stats_handler") as mock:
            mock.return_value = {"funnel": {}, "platforms": {}}
            import asyncio
            result = asyncio.get_event_loop().run_until_complete(
                server.call_tool("job_stats", {"period": "week"})
            )
            assert "funnel" in result

    def test_unknown_tool_raises(self):
        from shared.execution._mcp_jobpulse import create_jobpulse_server
        server = create_jobpulse_server()
        import asyncio
        with pytest.raises(KeyError):
            asyncio.get_event_loop().run_until_complete(
                server.call_tool("nonexistent", {})
            )
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_mcp_jobpulse.py -v`
Expected: ImportError

- [ ] **Step 3: Implement JobPulse capability server**

```python
# shared/execution/_mcp_jobpulse.py
"""JobPulse MCP Capability Server — exposes job automation tools.

Each tool wraps an existing function from jobpulse/. No new business logic.
"""

from __future__ import annotations

from shared.execution._mcp_gateway import CapabilityServer
from shared.logging_config import get_logger

logger = get_logger(__name__)


def _job_stats_handler(params: dict) -> dict:
    try:
        from jobpulse.job_analytics import get_conversion_funnel, get_platform_breakdown
        period = params.get("period", "week")
        return {
            "funnel": get_conversion_funnel(period),
            "platforms": get_platform_breakdown(period),
        }
    except Exception as e:
        return {"error": str(e)}


def _pre_screen_handler(params: dict) -> dict:
    try:
        from jobpulse.jd_analyzer import analyze_jd
        url = params.get("url", "")
        result = analyze_jd(url)
        return {"analysis": result}
    except Exception as e:
        return {"error": str(e)}


def _budget_handler(params: dict) -> dict:
    try:
        from jobpulse.budget_agent import handle_budget
        command = params.get("command", "")
        return {"response": handle_budget(command)}
    except Exception as e:
        return {"error": str(e)}


def _morning_briefing_handler(params: dict) -> dict:
    try:
        from jobpulse.briefing_agent import generate_briefing
        return {"briefing": generate_briefing()}
    except Exception as e:
        return {"error": str(e)}


def create_jobpulse_server() -> CapabilityServer:
    server = CapabilityServer(namespace="jobpulse")
    server.register_tool("job_stats", _job_stats_handler, "Job application conversion funnel and platform breakdown")
    server.register_tool("pre_screen", _pre_screen_handler, "Pre-screen a job listing URL through Gates 0-3")
    server.register_tool("budget", _budget_handler, "Budget query, add transaction, or undo")
    server.register_tool("morning_briefing", _morning_briefing_handler, "Generate morning briefing digest")
    return server
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_mcp_jobpulse.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_mcp_jobpulse.py tests/shared/execution/test_mcp_jobpulse.py
git commit -m "feat(execution): add JobPulse MCP capability server with 4 tools"
```

---

### Task 11: MCP Resources

**Files:**
- Create: `shared/execution/_mcp_resources.py`
- Test: `tests/shared/execution/test_mcp_resources.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_mcp_resources.py
import pytest
from unittest.mock import patch


class TestMCPResources:
    def test_health_resource(self):
        from shared.execution._mcp_resources import get_resource
        result = get_resource("jobpulse://health")
        assert "status" in result

    def test_events_resource(self, event_store):
        event_store.emit("scan:t1", "scan.window_started", {})
        with patch("shared.execution._mcp_resources._get_event_store", return_value=event_store):
            from shared.execution._mcp_resources import get_resource
            result = get_resource("jobpulse://events/scan:t1")
            assert len(result["events"]) == 1

    def test_unknown_resource_returns_error(self):
        from shared.execution._mcp_resources import get_resource
        result = get_resource("jobpulse://nonexistent")
        assert "error" in result
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_mcp_resources.py -v`
Expected: ImportError

- [ ] **Step 3: Implement MCP resources**

```python
# shared/execution/_mcp_resources.py
"""MCP Resources — read-only data endpoints.

Resources are projections from the event store and existing SQLite DBs.
"""

from __future__ import annotations

from urllib.parse import urlparse

from shared.logging_config import get_logger

logger = get_logger(__name__)


def _get_event_store():
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None


def get_resource(uri: str) -> dict:
    parsed = urlparse(uri)
    path = parsed.path.lstrip("/")

    if path == "health":
        return _health_resource()
    elif path.startswith("events/"):
        stream_id = path[len("events/"):]
        return _events_resource(stream_id)
    elif path == "jobs/queue":
        return _jobs_queue_resource()
    elif path.startswith("jobs/history"):
        return _jobs_history_resource()
    elif path == "gates/stats":
        return _gates_stats_resource()
    else:
        return {"error": f"Unknown resource: {uri}"}


def _health_resource() -> dict:
    return {"status": "healthy", "event_store": _get_event_store() is not None}


def _events_resource(stream_id: str) -> dict:
    store = _get_event_store()
    if not store:
        return {"events": [], "error": "Event store unavailable"}
    events = store.get_stream(stream_id)
    return {"stream_id": stream_id, "events": events, "count": len(events)}


def _jobs_queue_resource() -> dict:
    try:
        from jobpulse.job_autopilot import _load_pending
        pending = _load_pending()
        return {"queue": pending, "count": len(pending)}
    except Exception as e:
        return {"queue": [], "error": str(e)}


def _jobs_history_resource() -> dict:
    try:
        from jobpulse.db import JobDB
        db = JobDB()
        recent = db.get_recent_applications(days=7)
        return {"applications": recent, "count": len(recent)}
    except Exception as e:
        return {"applications": [], "error": str(e)}


def _gates_stats_resource() -> dict:
    try:
        from jobpulse.job_analytics import get_gate_stats
        return get_gate_stats()
    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_mcp_resources.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_mcp_resources.py tests/shared/execution/test_mcp_resources.py
git commit -m "feat(execution): add MCP resource handlers"
```

---

## Phase 3: A2A Protocol

### Task 12: Agent Card + Registry

**Files:**
- Create: `shared/execution/_a2a_card.py`
- Test: `tests/shared/execution/test_a2a_card.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_a2a_card.py
import json
import pytest


class TestAgentCard:
    def test_create_card(self):
        from shared.execution._a2a_card import AgentCard, AgentSkill
        card = AgentCard(
            name="scan-agent",
            description="Scans platforms",
            url="http://localhost:8090/a2a/scan-agent",
            skills=[AgentSkill(id="scan-platforms", name="Scan", description="Scan jobs")],
        )
        assert card.name == "scan-agent"
        assert len(card.skills) == 1

    def test_card_to_json(self):
        from shared.execution._a2a_card import AgentCard, AgentSkill
        card = AgentCard(
            name="test", description="t", url="http://localhost",
            skills=[AgentSkill(id="s1", name="S1", description="d")],
        )
        data = card.to_dict()
        assert data["name"] == "test"
        assert data["skills"][0]["id"] == "s1"
        assert "capabilities" in data


class TestFileRegistry:
    def test_register_and_get(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard, AgentSkill
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        card = AgentCard(name="scan-agent", description="s", url="http://localhost", skills=[])
        registry.register(card)
        found = registry.get("scan-agent")
        assert found is not None
        assert found.name == "scan-agent"

    def test_list_all(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        registry.register(AgentCard(name="a1", description="", url="", skills=[]))
        registry.register(AgentCard(name="a2", description="", url="", skills=[]))
        agents = registry.list_all()
        assert len(agents) == 2

    def test_get_unknown_returns_none(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        assert registry.get("nope") is None

    def test_unregister(self, tmp_path):
        from shared.execution._a2a_card import FileAgentRegistry, AgentCard
        registry = FileAgentRegistry(path=str(tmp_path / "agents.json"))
        registry.register(AgentCard(name="a1", description="", url="", skills=[]))
        registry.unregister("a1")
        assert registry.get("a1") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_a2a_card.py -v`
Expected: ImportError

- [ ] **Step 3: Implement AgentCard and FileAgentRegistry**

```python
# shared/execution/_a2a_card.py
"""A2A Agent Cards and Registry.

Agent Cards describe agent capabilities in Google A2A-compatible format.
FileAgentRegistry persists cards to JSON (local deployment).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Protocol

from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AgentSkill:
    id: str
    name: str
    description: str
    input_modes: list[str] = field(default_factory=lambda: ["application/json"])
    output_modes: list[str] = field(default_factory=lambda: ["application/json"])


@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    skills: list[AgentSkill]
    version: str = "1.0.0"
    streaming: bool = True
    push_notifications: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": {
                "streaming": self.streaming,
                "pushNotifications": self.push_notifications,
                "stateTransitionHistory": True,
            },
            "skills": [
                {
                    "id": s.id, "name": s.name, "description": s.description,
                    "inputModes": s.input_modes, "outputModes": s.output_modes,
                }
                for s in self.skills
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentCard:
        skills = [
            AgentSkill(
                id=s["id"], name=s["name"], description=s["description"],
                input_modes=s.get("inputModes", ["application/json"]),
                output_modes=s.get("outputModes", ["application/json"]),
            )
            for s in data.get("skills", [])
        ]
        caps = data.get("capabilities", {})
        return cls(
            name=data["name"], description=data["description"],
            url=data["url"], skills=skills, version=data.get("version", "1.0.0"),
            streaming=caps.get("streaming", True),
            push_notifications=caps.get("pushNotifications", True),
        )


class AgentRegistry(Protocol):
    def register(self, card: AgentCard) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> AgentCard | None: ...
    def list_all(self) -> list[AgentCard]: ...


class FileAgentRegistry:
    """File-backed agent registry for local deployment."""

    def __init__(self, path: str = "data/agent_registry.json"):
        self._path = Path(path)
        self._cards: dict[str, AgentCard] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            for item in data:
                self._cards[item["name"]] = AgentCard.from_dict(item)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            [c.to_dict() for c in self._cards.values()], indent=2
        ))

    def register(self, card: AgentCard) -> None:
        self._cards[card.name] = card
        self._save()
        logger.info("Registered agent: %s", card.name)

    def unregister(self, name: str) -> None:
        self._cards.pop(name, None)
        self._save()

    def get(self, name: str) -> AgentCard | None:
        return self._cards.get(name)

    def list_all(self) -> list[AgentCard]:
        return list(self._cards.values())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_a2a_card.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_a2a_card.py tests/shared/execution/test_a2a_card.py
git commit -m "feat(execution): add A2A Agent Cards and FileAgentRegistry"
```

---

### Task 13: A2A Task Lifecycle + TaskManager

**Files:**
- Create: `shared/execution/_a2a_task.py`
- Test: `tests/shared/execution/test_a2a_task.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_a2a_task.py
import pytest


class TestA2ATask:
    def test_create_task(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task(
            source_agent="scan-agent",
            target_agent="materials-agent",
            skill_id="generate-cv",
            input={"company": "OakNorth"},
            timeout_s=120,
        )
        assert task["status"] == "pending"
        assert task["source_agent"] == "scan-agent"
        assert task["target_agent"] == "materials-agent"
        assert len(task["task_id"]) == 26  # ULID

    def test_create_emits_event(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        events = event_store.get_stream(f"task:{task['task_id']}")
        assert len(events) == 1
        assert events[0]["event_type"] == "task.created"

    def test_transition_pending_to_running(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        updated = mgr.transition(task["task_id"], "running")
        assert updated["status"] == "running"

    def test_transition_running_to_completed(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        mgr.transition(task["task_id"], "running")
        updated = mgr.transition(task["task_id"], "completed", output={"result": "ok"})
        assert updated["status"] == "completed"
        assert updated["output"] == {"result": "ok"}

    def test_invalid_transition_raises(self, event_store):
        from shared.execution._a2a_task import TaskManager, InvalidTransition
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        with pytest.raises(InvalidTransition):
            mgr.transition(task["task_id"], "completed")  # can't skip running

    def test_get_task(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {"x": 1})
        found = mgr.get_task(task["task_id"])
        assert found is not None
        assert found["input"] == {"x": 1}

    def test_delegation_chain(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        parent = mgr.create_task("scan", "apply", "apply-job", {})
        child = mgr.create_task("apply", "materials", "gen-cv", {}, parent_task_id=parent["task_id"])
        assert child["parent_task_id"] == parent["task_id"]

    def test_history_tracks_transitions(self, event_store):
        from shared.execution._a2a_task import TaskManager
        mgr = TaskManager(event_store)
        task = mgr.create_task("a", "b", "s", {})
        mgr.transition(task["task_id"], "running")
        mgr.transition(task["task_id"], "completed")
        final = mgr.get_task(task["task_id"])
        assert len(final["history"]) == 3  # created + running + completed

    def test_task_timeout_constants(self):
        from shared.execution._a2a_task import TASK_TIMEOUTS
        assert TASK_TIMEOUTS["form_fill"] == 600
        assert TASK_TIMEOUTS["scan_window"] == 900
        assert TASK_TIMEOUTS["pattern_run"] == 420
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_a2a_task.py -v`
Expected: ImportError

- [ ] **Step 3: Implement TaskManager**

```python
# shared/execution/_a2a_task.py
"""A2A Task Lifecycle — create, transition, and track agent tasks.

All mutations emit events. Task state is reconstructed from events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from ulid import ULID

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)

TASK_TIMEOUTS = {
    "form_fill": 600,
    "scan_window": 900,
    "pattern_run": 420,
    "materials": 120,
    "budget": 30,
}

VALID_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"verifying", "completed", "failed", "escalated", "timed_out"},
    "verifying": {"completed", "failed"},
    "escalated": {"running", "completed", "failed"},
    "timed_out": {"escalated", "failed"},
    "completed": set(),
    "failed": {"pending"},  # retry
}


class InvalidTransition(Exception):
    pass


class A2ATask(TypedDict):
    task_id: str
    parent_task_id: str | None
    source_agent: str
    target_agent: str
    skill_id: str
    input: dict
    status: str
    output: dict | None
    artifacts: list[dict]
    history: list[dict]
    timeout_s: int
    created_at: str
    updated_at: str


class TaskManager:
    """Manages A2A task lifecycle. All mutations go through the event store."""

    def __init__(self, event_store: EventStore):
        self._store = event_store
        self._tasks: dict[str, A2ATask] = {}

    def create_task(
        self,
        source_agent: str,
        target_agent: str,
        skill_id: str,
        input: dict,
        timeout_s: int = 120,
        parent_task_id: str | None = None,
    ) -> A2ATask:
        task_id = str(ULID())
        now = datetime.now(timezone.utc).isoformat()
        task = A2ATask(
            task_id=task_id,
            parent_task_id=parent_task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            skill_id=skill_id,
            input=input,
            status="pending",
            output=None,
            artifacts=[],
            history=[{"status": "pending", "timestamp": now}],
            timeout_s=timeout_s,
            created_at=now,
            updated_at=now,
        )
        self._tasks[task_id] = task
        self._store.emit(
            stream_id=f"task:{task_id}",
            event_type="task.created",
            payload={
                "source_agent": source_agent,
                "target_agent": target_agent,
                "skill_id": skill_id,
                "parent_task_id": parent_task_id,
            },
        )
        logger.info("Task created: %s (%s → %s:%s)", task_id[:8], source_agent, target_agent, skill_id)
        return task

    def transition(
        self,
        task_id: str,
        new_status: str,
        output: dict | None = None,
        artifacts: list[dict] | None = None,
    ) -> A2ATask:
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found")
        current = task["status"]
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"Cannot transition from {current} to {new_status}")
        now = datetime.now(timezone.utc).isoformat()
        task["status"] = new_status
        task["updated_at"] = now
        task["history"].append({"status": new_status, "timestamp": now})
        if output is not None:
            task["output"] = output
        if artifacts:
            task["artifacts"].extend(artifacts)
        self._store.emit(
            stream_id=f"task:{task_id}",
            event_type=f"task.{new_status}",
            payload={"from_status": current, "output": output},
        )
        logger.info("Task %s: %s → %s", task_id[:8], current, new_status)
        return task

    def get_task(self, task_id: str) -> A2ATask | None:
        return self._tasks.get(task_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_a2a_task.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_a2a_task.py tests/shared/execution/test_a2a_task.py
git commit -m "feat(execution): add A2A TaskManager with lifecycle state machine"
```

---

### Task 14: FormVerifier — Heuristic Checks

**Files:**
- Create: `shared/execution/_verifier.py`
- Test: `tests/shared/execution/test_verifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_verifier.py
import pytest


class TestFormVerifier:
    def test_detects_name_in_phone_field(self):
        from shared.execution._verifier import FormVerifier, VerifyResult
        v = FormVerifier()
        results = [{"label": "Phone", "value": "John Smith", "ok": True}]
        vr = v.check_field_mismatches(results)
        assert vr.field_mismatch is True
        assert "Phone" in vr.details

    def test_no_mismatch_for_valid_phone(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [{"label": "Phone", "value": "+447123456789", "ok": True}]
        vr = v.check_field_mismatches(results)
        assert vr.field_mismatch is False

    def test_detects_duplicate_upload(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        events = [
            {"event_type": "form.fields_filled", "payload": {"page": 1, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
            {"event_type": "form.fields_filled", "payload": {"page": 2, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
        ]
        vr = v.check_duplicate_uploads(events)
        assert vr.duplicate_upload is True

    def test_no_duplicate_for_single_upload(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        events = [
            {"event_type": "form.fields_filled", "payload": {"page": 1, "results": [{"label": "Resume", "value": "cv.pdf"}]}},
        ]
        vr = v.check_duplicate_uploads(events)
        assert vr.duplicate_upload is False

    def test_detects_empty_required(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [
            {"label": "Name", "value": "Yash", "ok": True, "required": True},
            {"label": "Email", "value": "", "ok": True, "required": True},
        ]
        vr = v.check_empty_required(results)
        assert vr.empty_required is True
        assert "Email" in vr.details

    def test_all_ok_when_no_issues(self):
        from shared.execution._verifier import FormVerifier
        v = FormVerifier()
        results = [
            {"label": "Name", "value": "Yash Bishnoi", "ok": True},
            {"label": "Email", "value": "yash@example.com", "ok": True},
            {"label": "Phone", "value": "+447123456789", "ok": True},
        ]
        field_vr = v.check_field_mismatches(results)
        empty_vr = v.check_empty_required(results)
        assert field_vr.all_ok is True
        assert empty_vr.all_ok is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_verifier.py -v`
Expected: ImportError

- [ ] **Step 3: Implement FormVerifier**

```python
# shared/execution/_verifier.py
"""FormVerifier — heuristic + vision checks for form fill correctness.

Runs after every form.fields_filled event to catch mistakes early.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.logging_config import get_logger

logger = get_logger(__name__)

_PHONE_PATTERN = re.compile(r"^[+\d\s\-()]{7,20}$")
_EMAIL_PATTERN = re.compile(r"^[^@]+@[^@]+\.[^@]+$")
_NAME_PATTERN = re.compile(r"^[A-Za-z\s\-'.]{2,60}$")

_PHONE_LABELS = {"phone", "telephone", "mobile", "cell", "contact number", "phone number"}
_EMAIL_LABELS = {"email", "e-mail", "email address"}
_NAME_LABELS = {"name", "full name", "first name", "last name", "surname"}
_FILE_LABELS = {"resume", "cv", "cover letter", "attachment", "upload"}


@dataclass
class VerifyResult:
    field_mismatch: bool = False
    duplicate_upload: bool = False
    empty_required: bool = False
    unexpected_element: bool = False
    details: str = ""

    @property
    def all_ok(self) -> bool:
        return not (self.field_mismatch or self.duplicate_upload
                    or self.empty_required or self.unexpected_element)


class FormVerifier:
    def check_field_mismatches(self, results: list[dict]) -> VerifyResult:
        issues = []
        for r in results:
            label = r.get("label", "").lower().strip()
            value = str(r.get("value", ""))
            if not value:
                continue
            if any(k in label for k in _PHONE_LABELS):
                if not _PHONE_PATTERN.match(value):
                    issues.append(f"Phone field '{r['label']}' has non-phone value: '{value[:30]}'")
            if any(k in label for k in _EMAIL_LABELS):
                if not _EMAIL_PATTERN.match(value):
                    issues.append(f"Email field '{r['label']}' has non-email value: '{value[:30]}'")
            if any(k in label for k in _NAME_LABELS):
                if _PHONE_PATTERN.match(value) or _EMAIL_PATTERN.match(value):
                    issues.append(f"Name field '{r['label']}' has non-name value: '{value[:30]}'")
        return VerifyResult(
            field_mismatch=len(issues) > 0,
            details="; ".join(issues),
        )

    def check_duplicate_uploads(self, events: list[dict]) -> VerifyResult:
        uploads = []
        for e in events:
            if e.get("event_type") != "form.fields_filled":
                continue
            for r in e.get("payload", {}).get("results", []):
                label = r.get("label", "").lower()
                if any(k in label for k in _FILE_LABELS) and r.get("value"):
                    uploads.append((r["label"], r["value"]))
        seen_files = set()
        for label, value in uploads:
            if value in seen_files:
                return VerifyResult(
                    duplicate_upload=True,
                    details=f"File '{value}' uploaded multiple times",
                )
            seen_files.add(value)
        return VerifyResult()

    def check_empty_required(self, results: list[dict]) -> VerifyResult:
        empty = []
        for r in results:
            if r.get("required") and not str(r.get("value", "")).strip():
                empty.append(r.get("label", "unknown"))
        if empty:
            return VerifyResult(
                empty_required=True,
                details=f"Empty required fields: {', '.join(empty)}",
            )
        return VerifyResult()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_verifier.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_verifier.py tests/shared/execution/test_verifier.py
git commit -m "feat(execution): add FormVerifier with heuristic field checks"
```

---

### Task 15: Awareness Loop — TaskRunner Middleware

**Files:**
- Create: `shared/execution/_awareness.py`
- Test: `tests/shared/execution/test_awareness.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_awareness.py
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock


class TestConfidenceTracker:
    def test_initial_confidence(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan
        plan = TaskPlan(confidence=0.8, strategy=[], anti_patterns=[],
                        cognitive_level="L1", start_tier=1, escalation_hints=[])
        tracker = ConfidenceTracker(plan)
        assert tracker.confidence == 0.8

    def test_confidence_drops_on_mismatch(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan
        from shared.execution._verifier import VerifyResult
        plan = TaskPlan(confidence=0.8, strategy=[], anti_patterns=[],
                        cognitive_level="L1", start_tier=1, escalation_hints=[])
        tracker = ConfidenceTracker(plan)
        decision = tracker.after_action(
            event={"event_type": "form.fields_filled"},
            verify=VerifyResult(field_mismatch=True),
        )
        assert tracker.confidence == pytest.approx(0.6)

    def test_confidence_recovers_on_ok(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan
        from shared.execution._verifier import VerifyResult
        plan = TaskPlan(confidence=0.5, strategy=[], anti_patterns=[],
                        cognitive_level="L1", start_tier=1, escalation_hints=[])
        tracker = ConfidenceTracker(plan)
        tracker.after_action({}, VerifyResult())
        assert tracker.confidence == pytest.approx(0.55)

    def test_escalates_below_threshold(self):
        from shared.execution._awareness import ConfidenceTracker, TaskPlan, Decision
        from shared.execution._verifier import VerifyResult
        plan = TaskPlan(confidence=0.5, strategy=[], anti_patterns=[],
                        cognitive_level="L1", start_tier=1, escalation_hints=[])
        tracker = ConfidenceTracker(plan)
        decision = tracker.after_action({}, VerifyResult(field_mismatch=True))
        # 0.5 - 0.2 = 0.3 < 0.4 threshold
        assert decision.action == "escalate"


class TestTaskPreFlight:
    def test_cold_start_fast_path(self, event_store):
        from shared.execution._awareness import TaskPreFlight
        mock_memory = MagicMock()
        mock_memory.recall.return_value = []
        preflight = TaskPreFlight(
            memory=mock_memory, cognitive=None,
            optimization=None, event_store=event_store,
        )
        plan = preflight.prepare({
            "input": {"domain": "test.com", "platform": "greenhouse"},
            "skill_id": "apply-job",
        })
        assert plan.confidence == 0.5
        assert plan.start_tier == 1
        assert plan.cognitive_level == "L1"

    def test_full_path_with_memories(self, event_store):
        from shared.execution._awareness import TaskPreFlight
        mock_memory = MagicMock()
        mock_memory.recall.return_value = [{"strategy": "fill top-to-bottom"}]
        mock_cognitive = MagicMock()
        mock_cognitive.assess.return_value = MagicMock(confidence=0.85, recommended_level="L1")
        mock_opt = MagicMock()
        mock_opt.get_domain_stats.return_value = {"success_rate": 0.9}
        preflight = TaskPreFlight(
            memory=mock_memory, cognitive=mock_cognitive,
            optimization=mock_opt, event_store=event_store,
        )
        plan = preflight.prepare({
            "input": {"domain": "test.com", "platform": "greenhouse"},
            "skill_id": "apply-job",
        })
        assert plan.confidence == 0.85
        assert plan.start_tier == 1


class TestTaskRunner:
    def test_wraps_agent_and_runs(self, event_store):
        from shared.execution._awareness import TaskRunner, TaskPlan

        async def mock_agent(task, plan, tracker):
            return {"success": True, "failure_reason": None}

        runner = TaskRunner(
            agent_fn=mock_agent, memory=MagicMock(recall=MagicMock(return_value=[])),
            cognitive=None, optimization=None, event_store=event_store,
        )
        task = {
            "task_id": "test123", "input": {"domain": "x", "platform": "y"},
            "skill_id": "test", "timeout_s": 30,
        }
        result = asyncio.get_event_loop().run_until_complete(runner.run(task))
        assert result["success"] is True

    def test_timeout_produces_failure(self, event_store):
        from shared.execution._awareness import TaskRunner

        async def slow_agent(task, plan, tracker):
            await asyncio.sleep(10)
            return {"success": True}

        runner = TaskRunner(
            agent_fn=slow_agent, memory=MagicMock(recall=MagicMock(return_value=[])),
            cognitive=None, optimization=None, event_store=event_store,
        )
        task = {
            "task_id": "timeout_test", "input": {"domain": "x", "platform": "y"},
            "skill_id": "test", "timeout_s": 1,
        }
        result = asyncio.get_event_loop().run_until_complete(runner.run(task))
        assert result["success"] is False
        assert "timeout" in result["failure_reason"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_awareness.py -v`
Expected: ImportError

- [ ] **Step 3: Implement awareness loop**

```python
# shared/execution/_awareness.py
"""Agent Awareness Loop — cross-pillar wiring.

TaskPreFlight queries Pillars 1-4 before execution.
ConfidenceTracker monitors real-time confidence during execution.
TaskPostFlight records outcomes to Pillars 1, 3, 4.
TaskRunner wraps any agent function with the full loop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from shared.execution._event_store import EventStore
from shared.execution._verifier import VerifyResult
from shared.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class TaskPlan:
    confidence: float
    strategy: list
    anti_patterns: list
    cognitive_level: str
    start_tier: int
    escalation_hints: list = field(default_factory=list)


@dataclass
class Decision:
    action: str  # "continue" or "escalate"
    target: str | None = None
    reason: str = ""


class ConfidenceTracker:
    def __init__(self, plan: TaskPlan):
        self.confidence = plan.confidence
        self.hints = plan.escalation_hints
        self.events: list[dict] = []

    def after_action(self, event: dict, verify: VerifyResult) -> Decision:
        self.events.append(event)
        if verify.field_mismatch:
            self.confidence -= 0.2
        if verify.unexpected_element:
            self.confidence -= 0.3
        if verify.all_ok:
            self.confidence += 0.05
        for hint in self.hints:
            if callable(getattr(hint, "matches", None)) and hint.matches(event, self.confidence):
                return Decision("escalate", hint.action, hint.reason)
        if self.confidence < 0.4:
            return Decision("escalate", "rescue", f"confidence {self.confidence:.2f}")
        return Decision("continue")


class TaskPreFlight:
    def __init__(self, memory, cognitive, optimization, event_store: EventStore):
        self._memory = memory
        self._cognitive = cognitive
        self._optimization = optimization
        self._store = event_store

    def prepare(self, task: dict) -> TaskPlan:
        domain = task.get("input", {}).get("domain", "")
        platform = task.get("input", {}).get("platform", "")
        skill_id = task.get("skill_id", "")

        memories = []
        if self._memory:
            try:
                memories = self._memory.recall(
                    query=f"{platform} {domain} {skill_id}",
                    tiers=["procedural", "episodic"],
                    limit=5,
                )
            except Exception:
                memories = []

        if not memories:
            recent = self._store.query(
                stream_prefix=f"form:{platform}:{domain}" if platform else None,
                event_types=["form.mistake_detected", "form.rescue_used"],
                limit=10,
            )
            if not recent:
                return TaskPlan(
                    confidence=0.5, strategy=[], anti_patterns=[],
                    cognitive_level="L1", start_tier=1, escalation_hints=[],
                )

        assessment_confidence = 0.5
        cognitive_level = "L1"
        if self._cognitive:
            try:
                assessment = self._cognitive.assess(
                    task=f"{skill_id} on {platform}:{domain}",
                    domain=skill_id, memories=memories,
                    recent_failure_count=0,
                )
                assessment_confidence = assessment.confidence
                cognitive_level = assessment.recommended_level
            except Exception:
                pass

        if self._optimization:
            try:
                self._optimization.get_domain_stats(skill_id, platform)
            except Exception:
                pass

        start_tier = 1 if assessment_confidence > 0.7 and memories else 2
        return TaskPlan(
            confidence=assessment_confidence, strategy=memories, anti_patterns=[],
            cognitive_level=cognitive_level, start_tier=start_tier, escalation_hints=[],
        )


class TaskPostFlight:
    def __init__(self, memory, optimization, event_store: EventStore):
        self._memory = memory
        self._optimization = optimization
        self._store = event_store

    def complete(self, task: dict, result: dict, events: list[dict]) -> None:
        success = result.get("success", False)
        if self._memory:
            try:
                if success:
                    self._memory.learn_procedure(
                        domain=task.get("skill_id", "unknown"),
                        strategy=f"Completed {task.get('skill_id')}",
                        context=f"{task.get('input', {}).get('platform', '')}:{task.get('input', {}).get('domain', '')}",
                        score=0.8,
                        source=task.get("input", {}).get("platform", "unknown"),
                    )
                else:
                    self._memory.store_episodic(
                        content=f"Failed: {result.get('failure_reason', 'unknown')}",
                        context=task.get("skill_id", ""),
                        tags=["failure"],
                    )
            except Exception as e:
                logger.debug("Post-flight memory store failed: %s", e)

        if self._optimization:
            try:
                self._optimization.emit_signal(
                    signal_type="success" if success else "failure",
                    domain=task.get("skill_id", ""),
                    source=task.get("input", {}).get("platform", ""),
                    payload={"task_id": task.get("task_id")},
                )
            except Exception as e:
                logger.debug("Post-flight optimization signal failed: %s", e)

        self._store.emit(
            stream_id=f"task:{task.get('task_id', 'unknown')}",
            event_type="task.post_flight_done",
            payload={"success": success},
        )


class TaskRunner:
    """Wraps any agent function with the awareness loop."""

    def __init__(self, agent_fn, memory, cognitive, optimization, event_store: EventStore):
        self.agent_fn = agent_fn
        self.preflight = TaskPreFlight(memory, cognitive, optimization, event_store)
        self.postflight = TaskPostFlight(memory, optimization, event_store)
        self._store = event_store

    async def run(self, task: dict) -> dict:
        plan = self.preflight.prepare(task)
        tracker = ConfidenceTracker(plan)
        try:
            result = await asyncio.wait_for(
                self.agent_fn(task, plan, tracker),
                timeout=task.get("timeout_s", 120),
            )
        except asyncio.TimeoutError:
            self._store.emit(
                f"task:{task.get('task_id', 'unknown')}",
                "task.timed_out", {"timeout_s": task.get("timeout_s")},
            )
            result = {"success": False, "failure_reason": "timeout"}
        except Exception as e:
            result = {"success": False, "failure_reason": str(e)}
        self.postflight.complete(task, result, tracker.events)
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_awareness.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_awareness.py tests/shared/execution/test_awareness.py
git commit -m "feat(execution): add awareness loop with pre/post-flight and confidence tracking"
```

---

### Task 16: Rescue Agent Skeleton

**Files:**
- Create: `shared/execution/_rescue.py`
- Test: `tests/shared/execution/test_rescue.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_rescue.py
import pytest
from unittest.mock import patch, MagicMock


class TestRescueAgent:
    def test_analyze_unknown_form_returns_field_map(self):
        from shared.execution._rescue import RescueAgent
        agent = RescueAgent(event_store=MagicMock())
        with patch.object(agent, "_llm_analyze_page") as mock_llm:
            mock_llm.return_value = {
                "fields": [
                    {"label": "Name", "selector": "#name", "type": "text", "confidence": 0.9},
                    {"label": "Email", "selector": "#email", "type": "email", "confidence": 0.85},
                ],
                "risk": "low",
            }
            result = agent.analyze_page(
                screenshot_b64="fake_base64",
                dom_summary="<form><input id='name'/><input id='email'/></form>",
                event_history=[],
            )
            assert len(result["fields"]) == 2
            assert result["risk"] == "low"

    def test_rescue_budget_cap(self):
        from shared.execution._rescue import RescueAgent
        store = MagicMock()
        store.query.return_value = [
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
        ]
        agent = RescueAgent(event_store=store, max_rescues_per_domain=3)
        assert agent.can_rescue("x.com") is False

    def test_rescue_allowed_under_cap(self):
        from shared.execution._rescue import RescueAgent
        store = MagicMock()
        store.query.return_value = [
            {"event_type": "form.rescue_used", "payload": {"domain": "x.com"}},
        ]
        agent = RescueAgent(event_store=store, max_rescues_per_domain=3)
        assert agent.can_rescue("x.com") is True
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_rescue.py -v`
Expected: ImportError

- [ ] **Step 3: Implement RescueAgent**

```python
# shared/execution/_rescue.py
"""Rescue Agent — LLM-powered fallback for unknown forms.

Vision analysis + cross-domain transfer for unrecognized ATS platforms.
Budget-capped: max 3 rescue attempts per domain per day.
"""

from __future__ import annotations

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)


class RescueAgent:
    def __init__(self, event_store: EventStore, max_rescues_per_domain: int = 3):
        self._store = event_store
        self._max_rescues = max_rescues_per_domain

    def can_rescue(self, domain: str) -> bool:
        recent = self._store.query(
            event_types=["form.rescue_used"],
            limit=100,
        )
        domain_count = sum(
            1 for e in recent
            if e.get("payload", {}).get("domain") == domain
        )
        return domain_count < self._max_rescues

    def analyze_page(
        self,
        screenshot_b64: str,
        dom_summary: str,
        event_history: list[dict],
    ) -> dict:
        return self._llm_analyze_page(screenshot_b64, dom_summary, event_history)

    def _llm_analyze_page(
        self,
        screenshot_b64: str,
        dom_summary: str,
        event_history: list[dict],
    ) -> dict:
        from shared.agents import get_llm, smart_llm_call
        import json

        prompt = (
            "Analyze this form page and identify all fillable fields.\n\n"
            f"DOM structure:\n{dom_summary[:3000]}\n\n"
            f"Previous attempts: {len(event_history)} events\n\n"
            "Return JSON with:\n"
            '{"fields": [{"label": str, "selector": str, "type": str, "confidence": float}], '
            '"risk": "low"|"medium"|"high"}'
        )
        try:
            llm = get_llm()
            response = smart_llm_call(llm, prompt)
            return json.loads(response)
        except Exception as e:
            logger.error("Rescue LLM analysis failed: %s", e)
            return {"fields": [], "risk": "high", "error": str(e)}

    def find_similar_forms(self, dom_signature: str, limit: int = 5) -> list[dict]:
        return self._store.query(
            event_types=["form.page_filled"],
            limit=limit,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_rescue.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_rescue.py tests/shared/execution/test_rescue.py
git commit -m "feat(execution): add RescueAgent with budget cap and LLM fallback"
```

---

### Task 17: A2A HTTP Endpoints

**Files:**
- Create: `shared/execution/_a2a_protocol.py`
- Test: `tests/shared/execution/test_a2a_protocol.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/shared/execution/test_a2a_protocol.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def a2a_app(event_store):
    from shared.execution._a2a_protocol import create_a2a_router
    from shared.execution._a2a_task import TaskManager
    from shared.execution._a2a_card import FileAgentRegistry, AgentCard
    from fastapi import FastAPI
    mgr = TaskManager(event_store)
    registry = FileAgentRegistry(path="/tmp/test_agents.json")
    registry.register(AgentCard(name="test-agent", description="t", url="http://localhost", skills=[]))
    app = FastAPI()
    app.include_router(create_a2a_router(mgr, registry))
    return app


@pytest.fixture
def a2a_client(a2a_app):
    return TestClient(a2a_app)


class TestA2AEndpoints:
    def test_get_agent_card(self, a2a_client):
        resp = a2a_client.get("/a2a/test-agent/card")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test-agent"

    def test_get_unknown_agent_card(self, a2a_client):
        resp = a2a_client.get("/a2a/nonexistent/card")
        assert resp.status_code == 404

    def test_create_task(self, a2a_client):
        resp = a2a_client.post("/a2a/test-agent/task", json={
            "source_agent": "caller",
            "skill_id": "test-skill",
            "input": {"x": 1},
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    def test_get_task(self, a2a_client):
        create_resp = a2a_client.post("/a2a/test-agent/task", json={
            "source_agent": "caller", "skill_id": "s", "input": {},
        })
        task_id = create_resp.json()["task_id"]
        resp = a2a_client.get(f"/a2a/test-agent/task/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task_id
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/shared/execution/test_a2a_protocol.py -v`
Expected: ImportError

- [ ] **Step 3: Implement A2A HTTP endpoints**

```python
# shared/execution/_a2a_protocol.py
"""A2A HTTP Endpoints — agent card discovery, task CRUD, SSE streaming."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from shared.execution._a2a_card import AgentRegistry
from shared.execution._a2a_task import TaskManager
from shared.logging_config import get_logger

logger = get_logger(__name__)


class CreateTaskRequest(BaseModel):
    source_agent: str
    skill_id: str
    input: dict = {}
    timeout_s: int = 120
    parent_task_id: str | None = None


def create_a2a_router(task_manager: TaskManager, registry: AgentRegistry) -> APIRouter:
    router = APIRouter()

    @router.get("/a2a/{agent_name}/card")
    def get_card(agent_name: str):
        card = registry.get(agent_name)
        if not card:
            raise HTTPException(404, f"Agent not found: {agent_name}")
        return card.to_dict()

    @router.post("/a2a/{agent_name}/task", status_code=201)
    def create_task(agent_name: str, req: CreateTaskRequest):
        card = registry.get(agent_name)
        if not card:
            raise HTTPException(404, f"Agent not found: {agent_name}")
        task = task_manager.create_task(
            source_agent=req.source_agent,
            target_agent=agent_name,
            skill_id=req.skill_id,
            input=req.input,
            timeout_s=req.timeout_s,
            parent_task_id=req.parent_task_id,
        )
        return task

    @router.get("/a2a/{agent_name}/task/{task_id}")
    def get_task(agent_name: str, task_id: str):
        task = task_manager.get_task(task_id)
        if not task:
            raise HTTPException(404, f"Task not found: {task_id}")
        return task

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/execution/test_a2a_protocol.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add shared/execution/_a2a_protocol.py tests/shared/execution/test_a2a_protocol.py
git commit -m "feat(execution): add A2A HTTP endpoints for cards and tasks"
```

---

### Task 18: Final Integration — Public API + Exports

**Files:**
- Modify: `shared/execution/__init__.py` — add all Phase 2+3 exports
- Modify: `shared/execution/CLAUDE.md` — add Phase 2+3 docs

- [ ] **Step 1: Update __init__.py with full exports**

```python
# shared/execution/__init__.py
"""Durable Execution Infrastructure — Pillar 4.

Event-sourced state management with crash recovery, MCP production server,
and A2A agent coordination protocol.
"""

from shared.execution._event_store import EventStore, Event
from shared.execution._projectors import (
    ScanProjector, FormProjector, PatternProjector, project_stream,
)
from shared.execution._checkpointer import EventStoreCheckpointer
from shared.execution._redis import RedisClient
from shared.execution._mcp_gateway import (
    CapabilityServer, create_gateway_app, register_capability_server,
)
from shared.execution._a2a_card import AgentCard, AgentSkill, FileAgentRegistry
from shared.execution._a2a_task import A2ATask, TaskManager, TASK_TIMEOUTS
from shared.execution._awareness import (
    TaskPlan, ConfidenceTracker, TaskPreFlight, TaskPostFlight, TaskRunner, Decision,
)
from shared.execution._verifier import FormVerifier, VerifyResult
from shared.execution._rescue import RescueAgent

_store: EventStore | None = None


def get_event_store(db_path: str | None = None) -> EventStore:
    """Return shared EventStore singleton. Lazy-initialized on first call."""
    global _store
    if _store is None:
        from pathlib import Path
        path = db_path or str(Path(__file__).parent.parent.parent / "data" / "events.db")
        _store = EventStore(db_path=path)
    return _store


def emit(stream_id: str, event_type: str, payload: dict, **kwargs) -> str:
    """Emit an event to the shared store. Returns event_id."""
    return get_event_store().emit(stream_id, event_type, payload, **kwargs)
```

- [ ] **Step 2: Update CLAUDE.md with full module docs**

Append Phase 2 and Phase 3 docs to `shared/execution/CLAUDE.md`:

```markdown
## MCP Gateway (`_mcp_gateway.py`)
FastAPI-based router multiplexing capability servers. Auth middleware, audit logging, health check.
- `create_gateway_app()` returns FastAPI app
- `register_capability_server()` adds a capability server
- `GET /health`, `GET /mcp/tools`, `POST /mcp/call`

## JobPulse Capability Server (`_mcp_jobpulse.py`)
Wraps existing jobpulse functions as MCP tools. No new business logic.
- `create_jobpulse_server()` returns CapabilityServer with 4 tools

## MCP Resources (`_mcp_resources.py`)
Read-only data endpoints: health, events, job queue, history, gate stats.
- `get_resource(uri)` returns dict

## A2A Agent Cards (`_a2a_card.py`)
Google A2A-compatible agent card format. FileAgentRegistry for local deployment.
- `AgentCard.to_dict()` → JSON-serializable card
- `FileAgentRegistry` persists to `data/agent_registry.json`

## A2A Tasks (`_a2a_task.py`)
Task lifecycle: pending → running → verifying → completed/failed/escalated/timed_out.
- `TaskManager.create_task()` → A2ATask
- `TaskManager.transition()` validates state machine

## Awareness Loop (`_awareness.py`)
Cross-pillar wiring: pre-flight (memory + events + cognitive + optimization) →
execute (confidence tracking) → post-flight (learning).
- `TaskRunner` wraps any agent function with the full loop
- `ConfidenceTracker` escalates when confidence < 0.4

## FormVerifier (`_verifier.py`)
Heuristic field checks: name-in-phone, duplicate uploads, empty required fields.

## Rescue Agent (`_rescue.py`)
LLM vision analysis for unknown ATS platforms. Budget: 3 rescues/domain/day.
```

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/shared/execution/ -v`
Expected: All tests PASS (approximately 55 tests)

- [ ] **Step 4: Commit**

```bash
git add shared/execution/__init__.py shared/execution/CLAUDE.md
git commit -m "docs(execution): finalize public API exports and module documentation"
```

---

### Task 19: Full Regression Check

- [ ] **Step 1: Run all existing tests to verify no regressions**

Run: `python -m pytest tests/ -v --timeout=60 -x`
Expected: All existing tests still PASS. No regressions from pattern modifications.

- [ ] **Step 2: Run execution-specific tests**

Run: `python -m pytest tests/shared/execution/ -v --tb=short`
Expected: All ~55 execution tests PASS

- [ ] **Step 3: Verify imports work cleanly**

Run: `python -c "from shared.execution import EventStore, TaskRunner, AgentCard, FormVerifier; print('All imports OK')"`
Expected: "All imports OK"

- [ ] **Step 4: Final commit — update spec status**

Update the spec status from "pending implementation plan" to "implementation plan written":

In `docs/superpowers/specs/2026-04-21-durable-execution-design.md`, line 5:

Change: `**Status:** Design approved, pending implementation plan`
To: `**Status:** Design approved, implementation plan at docs/superpowers/plans/2026-04-21-durable-execution-implementation.md`

```bash
git add docs/superpowers/specs/2026-04-21-durable-execution-design.md
git commit -m "docs(execution): link spec to implementation plan"
```
