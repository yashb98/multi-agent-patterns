# Memory System Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON-file-based memory layer with a hybrid 3-engine architecture (SQLite + Qdrant + Neo4j) featuring embedding-based retrieval, autonomous graph linking, 6-signal forgetting, and lifecycle promotion — all backwards-compatible with the existing MemoryManager API.

**Architecture:** SQLite is the source of truth (sync writes). Qdrant stores 1024-dim Voyage 3 Large embeddings for semantic search. Neo4j stores the knowledge graph for relationship traversal and autonomous linking. A QueryRouter picks engine(s) per query type. A ForgettingEngine runs hourly sweeps. All 3 engines are optional except SQLite — the system degrades gracefully.

**Tech Stack:** Python 3.12, SQLite (stdlib), qdrant-client>=1.12.0, neo4j>=5.26.0, voyageai>=0.3.0, sentence-transformers (existing), pytest, testcontainers-neo4j

**Spec:** `docs/superpowers/specs/2026-04-20-memory-system-upgrade-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `shared/memory_layer/_entries.py` | **Modify** | Add MemoryEntry, MemoryTier, Lifecycle, EdgeType, ProtectionLevel |
| `shared/memory_layer/_sqlite_store.py` | **Create** | SQLite backend — CRUD, FTS, schema, views |
| `shared/memory_layer/_embedder.py` | **Create** | VoyageEmbedder + MiniLM fallback |
| `shared/memory_layer/_qdrant_store.py` | **Create** | Qdrant vector search + filtered HNSW |
| `shared/memory_layer/_neo4j_store.py` | **Create** | Neo4j graph ops — nodes, edges, traversal, signals |
| `shared/memory_layer/_query.py` | **Create** | MemoryQuery, RetrievalPlan, QueryRouter |
| `shared/memory_layer/_forgetting.py` | **Create** | ForgettingEngine — decay, promotion, demotion, revival |
| `shared/memory_layer/_linker.py` | **Create** | AutonomousLinker — rule-based + LLM classification |
| `shared/memory_layer/_sync.py` | **Create** | SyncService — reconciliation, queue, propagation |
| `shared/memory_layer/_manager.py` | **Modify** | Upgrade MemoryManager to wire all engines |
| `shared/memory_layer/__init__.py` | **Modify** | Add new public exports |
| `docker-compose.memory.yml` | **Create** | Neo4j container for local dev |
| `tests/shared/memory_layer/conftest.py` | **Create** | Shared fixtures |
| `tests/shared/memory_layer/test_*.py` | **Create** | 12 test files, 113 tests |
| 7 documentation files | **Modify/Create** | Agent-facing docs (CLAUDE.md, AGENTS.md, etc.) |

---

## Task 1: Install Dependencies & Docker Setup

**Files:**
- Modify: `requirements.txt` (or `pyproject.toml` — check which exists)
- Create: `docker-compose.memory.yml`

- [ ] **Step 1: Check package management format**

Run: `ls pyproject.toml requirements.txt setup.py 2>/dev/null`

Use whichever file exists. The steps below assume `requirements.txt` — adapt if needed.

- [ ] **Step 2: Add new dependencies**

Append to `requirements.txt`:

```
qdrant-client>=1.12.0
neo4j>=5.26.0
voyageai>=0.3.0
testcontainers[neo4j]>=4.0.0
```

- [ ] **Step 3: Install dependencies**

Run: `pip install qdrant-client>=1.12.0 neo4j>=5.26.0 voyageai>=0.3.0 "testcontainers[neo4j]>=4.0.0"`
Expected: All install successfully.

- [ ] **Step 4: Create Docker Compose for Neo4j**

Create `docker-compose.memory.yml`:

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    ports:
      - "7687:7687"
      - "7474:7474"
    environment:
      NEO4J_AUTH: neo4j/jobpulse
      NEO4J_PLUGINS: '["apoc"]'
    volumes:
      - neo4j_data:/data
    mem_limit: 512m

volumes:
  neo4j_data:
```

- [ ] **Step 5: Verify Neo4j starts**

Run: `docker compose -f docker-compose.memory.yml up -d && sleep 5 && docker compose -f docker-compose.memory.yml ps`
Expected: neo4j container running, healthy.

Run: `docker compose -f docker-compose.memory.yml down`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt docker-compose.memory.yml
git commit -m "chore: add qdrant, neo4j, voyageai deps + docker compose"
```

---

## Task 2: Upgrade _entries.py — MemoryEntry, Enums, EdgeType

**Files:**
- Modify: `shared/memory_layer/_entries.py`
- Create: `tests/shared/memory_layer/__init__.py`
- Create: `tests/shared/memory_layer/conftest.py`

- [ ] **Step 1: Create test directory**

Run: `mkdir -p tests/shared/memory_layer && touch tests/shared/memory_layer/__init__.py`

- [ ] **Step 2: Add enums and MemoryEntry to _entries.py**

Add the following at the top of `shared/memory_layer/_entries.py`, after the existing imports:

```python
import json as _json
from enum import Enum
from uuid import uuid4


class MemoryTier(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PATTERN = "pattern"
    EXPERIENCE = "experience"


class Lifecycle(str, Enum):
    STM = "stm"
    MTM = "mtm"
    LTM = "ltm"
    COLD = "cold"
    ARCHIVED = "archived"


class EdgeType(str, Enum):
    SIMILAR_TO = "SIMILAR_TO"
    PRODUCED = "PRODUCED"
    TAUGHT = "TAUGHT"
    EXTRACTED_FROM = "EXTRACTED_FROM"
    CONTRADICTS = "CONTRADICTS"
    REINFORCES = "REINFORCES"
    SUPERSEDES = "SUPERSEDES"
    RELATED_TO = "RELATED_TO"
    APPLIES_TO = "APPLIES_TO"


class ProtectionLevel(int, Enum):
    NONE = 0
    ELEVATED = 1
    PROTECTED = 2
    PINNED = 3


@dataclass
class MemoryEntry:
    """Unified memory entry across all tiers."""
    memory_id: str
    tier: MemoryTier
    lifecycle: Lifecycle
    domain: str
    content: str
    embedding: list[float]

    created_at: datetime
    last_accessed: datetime
    access_count: int
    decay_score: float

    score: float
    confidence: float

    payload: dict
    is_tombstoned: bool

    @staticmethod
    def create(
        tier: MemoryTier,
        domain: str,
        content: str,
        score: float = 0.0,
        confidence: float = 0.7,
        payload: dict | None = None,
        embedding: list[float] | None = None,
    ) -> "MemoryEntry":
        now = datetime.now()
        return MemoryEntry(
            memory_id=uuid4().hex[:12],
            tier=tier,
            lifecycle=Lifecycle.STM,
            domain=domain,
            content=content,
            embedding=embedding or [],
            created_at=now,
            last_accessed=now,
            access_count=0,
            decay_score=1.0,
            score=score,
            confidence=confidence,
            payload=payload or {},
            is_tombstoned=False,
        )

    def touch(self):
        self.last_accessed = datetime.now()
        self.access_count += 1
```

Keep ALL existing dataclasses (`EpisodicEntry`, `SemanticEntry`, `ProceduralEntry`, `ShortTermEntry`, `PatternEntry`) unchanged — they are still used by the old API for backwards compatibility.

- [ ] **Step 3: Create shared test fixtures (conftest.py)**

Create `tests/shared/memory_layer/conftest.py`:

```python
import hashlib
import math
import pytest
from datetime import datetime

from shared.memory_layer._entries import (
    MemoryEntry, MemoryTier, Lifecycle, EdgeType, ProtectionLevel,
)


def _deterministic_embedding(text: str, dims: int = 1024) -> list[float]:
    """Hash-based deterministic embedding for reproducible tests."""
    h = hashlib.sha256(text.encode()).digest()
    raw = []
    for i in range(dims):
        byte_idx = i % len(h)
        raw.append((h[byte_idx] + i) % 256 / 255.0 * 2 - 1)
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def make_entry(
    tier: MemoryTier = MemoryTier.EPISODIC,
    domain: str = "test",
    content: str = "test memory content",
    score: float = 7.0,
    confidence: float = 0.7,
    lifecycle: Lifecycle = Lifecycle.STM,
    access_count: int = 0,
    decay_score: float = 1.0,
    payload: dict | None = None,
    is_tombstoned: bool = False,
    embedding: list[float] | None = None,
) -> MemoryEntry:
    entry = MemoryEntry.create(
        tier=tier, domain=domain, content=content,
        score=score, confidence=confidence, payload=payload,
        embedding=embedding or _deterministic_embedding(content),
    )
    entry.lifecycle = lifecycle
    entry.access_count = access_count
    entry.decay_score = decay_score
    entry.is_tombstoned = is_tombstoned
    return entry


@pytest.fixture
def make_memory():
    """Factory fixture for creating test MemoryEntry objects."""
    return make_entry
```

- [ ] **Step 4: Verify entries import**

Run: `python -c "from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle, EdgeType, ProtectionLevel; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_entries.py tests/shared/memory_layer/
git commit -m "feat(memory): add MemoryEntry, MemoryTier, Lifecycle, EdgeType enums"
```

---

## Task 3: SQLiteStore — Source of Truth

**Files:**
- Create: `shared/memory_layer/_sqlite_store.py`
- Create: `tests/shared/memory_layer/test_sqlite_store.py`

- [ ] **Step 1: Write test_sqlite_store.py**

Create `tests/shared/memory_layer/test_sqlite_store.py`:

```python
import json
import threading
import pytest

from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle
from shared.memory_layer._sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "test.db"))


class TestSQLiteStore:
    def test_insert_and_retrieve(self, store, make_memory):
        entry = make_memory(content="quantum computing research")
        store.insert(entry)
        result = store.get_by_id(entry.memory_id)
        assert result is not None
        assert result.memory_id == entry.memory_id
        assert result.content == "quantum computing research"
        assert result.tier == MemoryTier.EPISODIC

    def test_insert_creates_all_indexes(self, store):
        conn = store._get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mem_%'"
        ).fetchall()
        index_names = {r[0] for r in rows}
        assert {"idx_mem_tier", "idx_mem_domain", "idx_mem_decay", "idx_mem_lifecycle"} <= index_names

    def test_tier_views_filter_correctly(self, store, make_memory):
        store.insert(make_memory(tier=MemoryTier.SEMANTIC, content="fact 1"))
        store.insert(make_memory(tier=MemoryTier.SEMANTIC, content="fact 2"))
        tombstoned = make_memory(tier=MemoryTier.SEMANTIC, content="fact 3", is_tombstoned=True)
        store.insert(tombstoned)
        results = store.query_by_tier(MemoryTier.SEMANTIC)
        assert len(results) == 2

    def test_domain_filter(self, store, make_memory):
        for i in range(5):
            store.insert(make_memory(domain="physics", content=f"physics {i}"))
        for i in range(5):
            store.insert(make_memory(domain="cooking", content=f"cooking {i}"))
        results = store.query_by_domain("physics")
        assert len(results) == 5

    def test_lifecycle_filter(self, store, make_memory):
        store.insert(make_memory(lifecycle=Lifecycle.STM, content="stm"))
        store.insert(make_memory(lifecycle=Lifecycle.MTM, content="mtm"))
        store.insert(make_memory(lifecycle=Lifecycle.LTM, content="ltm"))
        results = store.query_by_lifecycle(Lifecycle.STM)
        assert len(results) == 1
        assert results[0].lifecycle == Lifecycle.STM

    def test_decay_score_ordering(self, store, make_memory):
        for score in [0.1, 0.9, 0.5, 0.3, 0.7]:
            store.insert(make_memory(decay_score=score, content=f"decay {score}"))
        results = store.query_by_decay_desc(limit=5)
        scores = [r.decay_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_tombstone_soft_delete(self, store, make_memory):
        entry = make_memory(content="will be deleted")
        store.insert(entry)
        store.tombstone(entry.memory_id)
        assert store.get_by_id(entry.memory_id) is None
        # Raw query should still find it
        conn = store._get_conn()
        row = conn.execute(
            "SELECT is_tombstoned FROM memories WHERE memory_id=?",
            (entry.memory_id,),
        ).fetchone()
        assert row[0] == 1

    def test_payload_json_roundtrip(self, store, make_memory):
        payload = {"strengths": ["research", "writing"], "nested": {"key": 42}}
        entry = make_memory(payload=payload, content="payload test")
        store.insert(entry)
        result = store.get_by_id(entry.memory_id)
        assert result.payload == payload

    def test_update_access_metadata(self, store, make_memory):
        entry = make_memory(content="access tracking")
        store.insert(entry)
        old_accessed = entry.last_accessed
        store.touch(entry.memory_id)
        result = store.get_by_id(entry.memory_id)
        assert result.access_count == 1
        assert result.last_accessed > old_accessed

    def test_concurrent_writes(self, store, make_memory):
        errors = []

        def writer(thread_id):
            try:
                for i in range(10):
                    store.insert(make_memory(content=f"thread {thread_id} entry {i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert store.count() == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/memory_layer/test_sqlite_store.py -v 2>&1 | head -20`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.memory_layer._sqlite_store'`

- [ ] **Step 3: Implement SQLiteStore**

Create `shared/memory_layer/_sqlite_store.py`:

```python
"""SQLiteStore — source-of-truth persistence for all memory entries.

Single table with tier-specific views. Thread-safe via check_same_thread=False
and explicit serialization on writes.
"""

import json
import sqlite3
import threading
from datetime import datetime
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._entries import MemoryEntry, MemoryTier, Lifecycle

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    memory_id     TEXT PRIMARY KEY,
    tier          TEXT NOT NULL,
    lifecycle     TEXT NOT NULL DEFAULT 'stm',
    domain        TEXT NOT NULL,
    content       TEXT NOT NULL,
    score         REAL DEFAULT 0.0,
    confidence    REAL DEFAULT 0.7,
    created_at    TEXT NOT NULL,
    last_accessed TEXT NOT NULL,
    access_count  INTEGER DEFAULT 0,
    decay_score   REAL DEFAULT 1.0,
    payload       TEXT,
    is_tombstoned INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mem_tier ON memories(tier);
CREATE INDEX IF NOT EXISTS idx_mem_domain ON memories(domain);
CREATE INDEX IF NOT EXISTS idx_mem_decay ON memories(decay_score DESC);
CREATE INDEX IF NOT EXISTS idx_mem_lifecycle ON memories(lifecycle);

CREATE VIEW IF NOT EXISTS episodic_memories AS
    SELECT * FROM memories WHERE tier='episodic' AND NOT is_tombstoned;
CREATE VIEW IF NOT EXISTS semantic_facts AS
    SELECT * FROM memories WHERE tier='semantic' AND NOT is_tombstoned;
CREATE VIEW IF NOT EXISTS procedures AS
    SELECT * FROM memories WHERE tier='procedural' AND NOT is_tombstoned;
"""


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


class SQLiteStore:
    """SQLite backend for the memory system. Source of truth."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self):
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row["memory_id"],
            tier=MemoryTier(row["tier"]),
            lifecycle=Lifecycle(row["lifecycle"]),
            domain=row["domain"],
            content=row["content"],
            embedding=[],  # embeddings live in Qdrant, not SQLite
            created_at=_parse_ts(row["created_at"]),
            last_accessed=_parse_ts(row["last_accessed"]),
            access_count=row["access_count"],
            decay_score=row["decay_score"],
            score=row["score"],
            confidence=row["confidence"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            is_tombstoned=bool(row["is_tombstoned"]),
        )

    def insert(self, entry: MemoryEntry) -> None:
        with self._lock:
            self._get_conn().execute(
                "INSERT OR REPLACE INTO memories "
                "(memory_id, tier, lifecycle, domain, content, score, confidence, "
                "created_at, last_accessed, access_count, decay_score, payload, is_tombstoned) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.memory_id, entry.tier.value, entry.lifecycle.value,
                    entry.domain, entry.content, entry.score, entry.confidence,
                    _ts(entry.created_at), _ts(entry.last_accessed),
                    entry.access_count, entry.decay_score,
                    json.dumps(entry.payload) if entry.payload else None,
                    int(entry.is_tombstoned),
                ),
            )
            self._get_conn().commit()

    def get_by_id(self, memory_id: str) -> Optional[MemoryEntry]:
        row = self._get_conn().execute(
            "SELECT * FROM memories WHERE memory_id=? AND NOT is_tombstoned",
            (memory_id,),
        ).fetchone()
        return self._row_to_entry(row) if row else None

    def query_by_tier(self, tier: MemoryTier, limit: int = 100) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE tier=? AND NOT is_tombstoned "
            "ORDER BY decay_score DESC LIMIT ?",
            (tier.value, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_domain(self, domain: str, limit: int = 100) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE domain=? AND NOT is_tombstoned "
            "ORDER BY decay_score DESC LIMIT ?",
            (domain, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_lifecycle(self, lifecycle: Lifecycle, limit: int = 100) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE lifecycle=? AND NOT is_tombstoned "
            "ORDER BY decay_score DESC LIMIT ?",
            (lifecycle.value, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_by_decay_desc(self, limit: int = 50) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE NOT is_tombstoned "
            "ORDER BY decay_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_active(self, min_decay: float = 0.0) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE NOT is_tombstoned AND decay_score >= ?",
            (min_decay,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def query_tombstoned_recent(self, domain: str, days: int = 30) -> list[MemoryEntry]:
        rows = self._get_conn().execute(
            "SELECT * FROM memories WHERE is_tombstoned=1 AND domain=? "
            "AND created_at > datetime('now', ?)",
            (domain, f"-{days} days"),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def touch(self, memory_id: str) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET last_accessed=?, access_count=access_count+1 "
                "WHERE memory_id=?",
                (_ts(datetime.now()), memory_id),
            )
            self._get_conn().commit()

    def update_decay(self, memory_id: str, decay_score: float) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET decay_score=? WHERE memory_id=?",
                (decay_score, memory_id),
            )
            self._get_conn().commit()

    def update_lifecycle(self, memory_id: str, lifecycle: Lifecycle) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET lifecycle=? WHERE memory_id=?",
                (lifecycle.value, memory_id),
            )
            self._get_conn().commit()

    def update_confidence(self, memory_id: str, confidence: float) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET confidence=? WHERE memory_id=?",
                (confidence, memory_id),
            )
            self._get_conn().commit()

    def tombstone(self, memory_id: str) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET is_tombstoned=1 WHERE memory_id=?",
                (memory_id,),
            )
            self._get_conn().commit()

    def revive(self, memory_id: str) -> None:
        with self._lock:
            self._get_conn().execute(
                "UPDATE memories SET is_tombstoned=0, lifecycle='stm' WHERE memory_id=?",
                (memory_id,),
            )
            self._get_conn().commit()

    def count(self, include_tombstoned: bool = False) -> int:
        where = "" if include_tombstoned else "WHERE NOT is_tombstoned"
        row = self._get_conn().execute(f"SELECT COUNT(*) FROM memories {where}").fetchone()
        return row[0]

    def all_memory_ids(self) -> list[str]:
        rows = self._get_conn().execute(
            "SELECT memory_id FROM memories WHERE NOT is_tombstoned"
        ).fetchall()
        return [r[0] for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/memory_layer/test_sqlite_store.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_sqlite_store.py tests/shared/memory_layer/test_sqlite_store.py
git commit -m "feat(memory): add SQLiteStore with CRUD, views, thread-safe writes"
```

---

## Task 4: VoyageEmbedder + MiniLM Fallback

**Files:**
- Create: `shared/memory_layer/_embedder.py`
- Create: `tests/shared/memory_layer/test_embedder.py`

- [ ] **Step 1: Write test_embedder.py**

Create `tests/shared/memory_layer/test_embedder.py`:

```python
import math
import pytest
from unittest.mock import patch, MagicMock

from shared.memory_layer._embedder import MemoryEmbedder


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


@pytest.fixture
def embedder():
    return MemoryEmbedder(primary="minilm", fallback="minilm")


class TestMemoryEmbedder:
    def test_embed_returns_correct_dims(self, embedder):
        vec = embedder.embed("test text")
        assert len(vec) == 384  # MiniLM dims

    def test_same_text_same_vector(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("greenhouse form filling")
        assert v1 == v2

    def test_similar_text_high_cosine(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("filling greenhouse application forms")
        assert _cosine(v1, v2) > 0.7

    def test_different_text_low_cosine(self, embedder):
        v1 = embedder.embed("greenhouse form filling")
        v2 = embedder.embed("quantum physics research papers")
        assert _cosine(v1, v2) < 0.5

    def test_fallback_on_primary_failure(self):
        embedder = MemoryEmbedder(primary="voyage", fallback="minilm")
        with patch.object(embedder, "_embed_voyage", side_effect=ConnectionError("API down")):
            vec = embedder.embed("test text")
            assert len(vec) == 384  # fell back to MiniLM

    def test_fallback_logs_warning(self, caplog):
        embedder = MemoryEmbedder(primary="voyage", fallback="minilm")
        with patch.object(embedder, "_embed_voyage", side_effect=ConnectionError("API down")):
            embedder.embed("test")
            assert "falling back" in caplog.text.lower() or "fallback" in caplog.text.lower()

    def test_batch_embed(self, embedder):
        texts = [f"text {i}" for i in range(10)]
        results = embedder.embed_batch(texts)
        assert len(results) == 10
        assert all(len(v) == 384 for v in results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/memory_layer/test_embedder.py -v 2>&1 | head -10`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement MemoryEmbedder**

Create `shared/memory_layer/_embedder.py`:

```python
"""MemoryEmbedder — Voyage 3 Large primary, MiniLM fallback.

Provides embed() and embed_batch() for the memory system.
Falls back to local MiniLM if Voyage API is unavailable.
"""

import os
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

_VOYAGE_DIMS = 1024
_MINILM_DIMS = 384
_minilm_model = None


def _get_minilm():
    global _minilm_model
    if _minilm_model is None:
        from sentence_transformers import SentenceTransformer
        _minilm_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _minilm_model


class MemoryEmbedder:
    """Dual-mode embedder with automatic fallback."""

    def __init__(
        self,
        primary: str = "voyage",
        fallback: str = "minilm",
    ):
        self._primary = primary
        self._fallback = fallback
        self._voyage_client = None

    @property
    def dims(self) -> int:
        if self._primary == "voyage":
            return _VOYAGE_DIMS
        return _MINILM_DIMS

    def _get_voyage(self):
        if self._voyage_client is None:
            import voyageai
            self._voyage_client = voyageai.Client(
                api_key=os.environ.get("VOYAGE_API_KEY", ""),
            )
        return self._voyage_client

    def _embed_voyage(self, texts: list[str]) -> list[list[float]]:
        client = self._get_voyage()
        result = client.embed(texts, model="voyage-3-large")
        return result.embeddings

    def _embed_minilm(self, texts: list[str]) -> list[list[float]]:
        model = _get_minilm()
        embeddings = model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._primary == "voyage":
            try:
                return self._embed_voyage(texts)
            except Exception as e:
                logger.warning("Voyage embed failed, falling back to MiniLM: %s", e)
                return self._embed_minilm(texts)
        return self._embed_minilm(texts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/memory_layer/test_embedder.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_embedder.py tests/shared/memory_layer/test_embedder.py
git commit -m "feat(memory): add MemoryEmbedder with Voyage primary + MiniLM fallback"
```

---

## Task 5: QdrantStore — Vector Search

**Files:**
- Create: `shared/memory_layer/_qdrant_store.py`
- Create: `tests/shared/memory_layer/test_qdrant_store.py`

- [ ] **Step 1: Write test_qdrant_store.py**

Create `tests/shared/memory_layer/test_qdrant_store.py`:

```python
import math
import time
import pytest

from shared.memory_layer._entries import MemoryTier, Lifecycle
from shared.memory_layer._qdrant_store import QdrantStore


def _make_vector(seed: float, dims: int = 1024) -> list[float]:
    import hashlib
    h = hashlib.sha256(str(seed).encode()).digest()
    raw = [(h[i % len(h)] + i) % 256 / 255.0 * 2 - 1 for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest.fixture
def store():
    s = QdrantStore(location=":memory:", dims=1024)
    s.ensure_collections()
    return s


class TestQdrantStore:
    def test_upsert_and_search(self, store):
        target = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, target, {"domain": "test", "score": 7.0})
        store.upsert("id2", MemoryTier.EPISODIC, _make_vector(999.0), {"domain": "test", "score": 5.0})
        results = store.search(MemoryTier.EPISODIC, target, top_k=1)
        assert len(results) >= 1
        assert results[0][0] == "id1"

    def test_collection_per_tier(self, store):
        vec = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        results = store.search(MemoryTier.PROCEDURAL, vec, top_k=10)
        assert len(results) == 0

    def test_filtered_search_by_domain(self, store):
        for i in range(5):
            store.upsert(f"a{i}", MemoryTier.SEMANTIC, _make_vector(float(i)), {"domain": "physics"})
        for i in range(5):
            store.upsert(f"b{i}", MemoryTier.SEMANTIC, _make_vector(float(i + 100)), {"domain": "cooking"})
        results = store.search(
            MemoryTier.SEMANTIC, _make_vector(0.0), top_k=10,
            filters={"domain": "physics"},
        )
        assert all(r[0].startswith("a") for r in results)

    def test_filtered_search_by_score(self, store):
        for i, score in enumerate([3.0, 5.0, 7.0, 9.0]):
            store.upsert(f"id{i}", MemoryTier.EPISODIC, _make_vector(float(i)), {"domain": "test", "score": score})
        results = store.search(
            MemoryTier.EPISODIC, _make_vector(0.0), top_k=10,
            min_score=7.0,
        )
        assert all(r[1] >= 0 for r in results)  # verify results returned
        ids = {r[0] for r in results}
        assert "id0" not in ids  # score 3.0 excluded
        assert "id1" not in ids  # score 5.0 excluded

    def test_filtered_search_by_lifecycle(self, store):
        store.upsert("stm1", MemoryTier.EPISODIC, _make_vector(1.0), {"domain": "test", "lifecycle": "stm"})
        store.upsert("cold1", MemoryTier.EPISODIC, _make_vector(2.0), {"domain": "test", "lifecycle": "cold"})
        results = store.search(
            MemoryTier.EPISODIC, _make_vector(1.0), top_k=10,
            filters={"lifecycle": "stm"},
        )
        ids = {r[0] for r in results}
        assert "stm1" in ids
        assert "cold1" not in ids

    def test_similarity_ordering(self, store):
        base = _make_vector(1.0)
        close = _make_vector(1.001)
        far = _make_vector(999.0)
        store.upsert("close", MemoryTier.EPISODIC, close, {"domain": "test"})
        store.upsert("far", MemoryTier.EPISODIC, far, {"domain": "test"})
        results = store.search(MemoryTier.EPISODIC, base, top_k=2)
        assert results[0][0] == "close"

    def test_cross_tier_search(self, store):
        vec = _make_vector(1.0)
        store.upsert("ep1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        store.upsert("pr1", MemoryTier.PROCEDURAL, vec, {"domain": "test"})
        results = store.search_all_tiers(vec, top_k=5)
        ids = {r[0] for r in results}
        assert "ep1" in ids
        assert "pr1" in ids

    def test_delete_by_id(self, store):
        vec = _make_vector(1.0)
        store.upsert("id1", MemoryTier.EPISODIC, vec, {"domain": "test"})
        store.delete("id1", MemoryTier.EPISODIC)
        results = store.search(MemoryTier.EPISODIC, vec, top_k=1)
        assert len(results) == 0

    def test_cosine_threshold(self, store):
        store.upsert("id1", MemoryTier.EPISODIC, _make_vector(1.0), {"domain": "test"})
        far_vec = _make_vector(999.0)
        results = store.search(MemoryTier.EPISODIC, far_vec, top_k=1, score_threshold=0.95)
        assert len(results) == 0

    def test_10k_vectors_performance(self, store):
        for i in range(10000):
            store.upsert(f"id{i}", MemoryTier.EPISODIC, _make_vector(float(i)), {"domain": "test"})
        query = _make_vector(5000.0)
        start = time.monotonic()
        results = store.search(MemoryTier.EPISODIC, query, top_k=10)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert len(results) > 0
        assert elapsed_ms < 100  # generous threshold for CI
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/memory_layer/test_qdrant_store.py -v 2>&1 | head -10`
Expected: FAIL

- [ ] **Step 3: Implement QdrantStore**

Create `shared/memory_layer/_qdrant_store.py`:

```python
"""QdrantStore — vector search backend for the memory system.

One collection per tier. Supports filtered HNSW search and cross-tier queries.
Runs in-memory for tests, local file mode for dev, cloud mode for production.
"""

from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._entries import MemoryTier

logger = get_logger(__name__)

_TIER_COLLECTIONS = {
    MemoryTier.EPISODIC: "episodic_memories",
    MemoryTier.SEMANTIC: "semantic_facts",
    MemoryTier.PROCEDURAL: "procedures",
    MemoryTier.EXPERIENCE: "experiences",
}


class QdrantStore:
    """Qdrant vector search backend."""

    def __init__(
        self,
        location: str = ":memory:",
        url: str | None = None,
        api_key: str | None = None,
        dims: int = 1024,
    ):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        if url:
            self._client = QdrantClient(url=url, api_key=api_key)
        elif location == ":memory:":
            self._client = QdrantClient(location=location)
        else:
            self._client = QdrantClient(path=location)

        self._dims = dims
        self._distance = Distance.COSINE
        self._vector_params = VectorParams(size=dims, distance=self._distance)

    def ensure_collections(self) -> None:
        from qdrant_client.models import VectorParams
        for collection_name in _TIER_COLLECTIONS.values():
            if not self._client.collection_exists(collection_name):
                self._client.create_collection(
                    collection_name=collection_name,
                    vectors_config=self._vector_params,
                )

    def upsert(
        self,
        memory_id: str,
        tier: MemoryTier,
        vector: list[float],
        payload: dict,
    ) -> None:
        from qdrant_client.models import PointStruct
        collection = _TIER_COLLECTIONS.get(tier)
        if not collection:
            return
        self._client.upsert(
            collection_name=collection,
            points=[PointStruct(id=memory_id, vector=vector, payload=payload)],
        )

    def search(
        self,
        tier: MemoryTier,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict | None = None,
        min_score: float | None = None,
        score_threshold: float | None = None,
    ) -> list[tuple[str, float]]:
        from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

        collection = _TIER_COLLECTIONS.get(tier)
        if not collection:
            return []

        filter_conditions = []
        if filters:
            for key, value in filters.items():
                filter_conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
        if min_score is not None:
            filter_conditions.append(
                FieldCondition(key="score", range=Range(gte=min_score))
            )

        query_filter = Filter(must=filter_conditions) if filter_conditions else None

        results = self._client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter,
            score_threshold=score_threshold,
        )
        return [(str(r.id), r.score) for r in results]

    def search_all_tiers(
        self,
        query_vector: list[float],
        top_k: int = 10,
        score_threshold: float = 0.75,
    ) -> list[tuple[str, float]]:
        all_results = []
        for tier in _TIER_COLLECTIONS:
            results = self.search(tier, query_vector, top_k=top_k, score_threshold=score_threshold)
            all_results.extend(results)
        all_results.sort(key=lambda x: x[1], reverse=True)
        return all_results[:top_k]

    def delete(self, memory_id: str, tier: MemoryTier) -> None:
        from qdrant_client.models import PointIdsList
        collection = _TIER_COLLECTIONS.get(tier)
        if not collection:
            return
        self._client.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=[memory_id]),
        )

    def count(self, tier: MemoryTier | None = None) -> int:
        total = 0
        tiers = [tier] if tier else list(_TIER_COLLECTIONS.keys())
        for t in tiers:
            collection = _TIER_COLLECTIONS.get(t)
            if collection and self._client.collection_exists(collection):
                info = self._client.get_collection(collection)
                total += info.points_count
        return total

    def has_point(self, memory_id: str, tier: MemoryTier) -> bool:
        collection = _TIER_COLLECTIONS.get(tier)
        if not collection:
            return False
        try:
            self._client.retrieve(collection_name=collection, ids=[memory_id])
            return True
        except Exception:
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/memory_layer/test_qdrant_store.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_qdrant_store.py tests/shared/memory_layer/test_qdrant_store.py
git commit -m "feat(memory): add QdrantStore with filtered HNSW search + cross-tier query"
```

---

## Task 6: Neo4jStore — Graph Traversal

**Files:**
- Create: `shared/memory_layer/_neo4j_store.py`
- Create: `tests/shared/memory_layer/test_neo4j_store.py`

- [ ] **Step 1: Write test_neo4j_store.py**

Create `tests/shared/memory_layer/test_neo4j_store.py`:

```python
import pytest
from unittest.mock import MagicMock, patch

from shared.memory_layer._entries import MemoryTier, Lifecycle, EdgeType
from shared.memory_layer._neo4j_store import Neo4jStore


class MockNeo4jStore(Neo4jStore):
    """In-memory mock that simulates Neo4j graph operations for unit tests."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[tuple[str, str, str, dict]] = []  # (src, tgt, type, props)
        self._available = True

    def create_node(self, memory_id: str, tier: str, domain: str,
                    content_preview: str, score: float, confidence: float,
                    decay_score: float, lifecycle: str, created_at: str) -> None:
        self._nodes[memory_id] = {
            "memory_id": memory_id, "tier": tier, "domain": domain,
            "content_preview": content_preview, "score": score,
            "confidence": confidence, "decay_score": decay_score,
            "lifecycle": lifecycle, "created_at": created_at,
        }

    def get_node(self, memory_id: str) -> dict | None:
        return self._nodes.get(memory_id)

    def create_edge(self, source_id: str, target_id: str, edge_type: str,
                    properties: dict | None = None) -> None:
        if not any(e[0] == source_id and e[1] == target_id and e[2] == edge_type
                   for e in self._edges):
            self._edges.append((source_id, target_id, edge_type, properties or {}))

    def expand(self, memory_ids: list[str], depth: int = 1,
               exclude_labels: list[str] | None = None) -> list[str]:
        exclude = set(exclude_labels or [])
        visited = set(memory_ids)
        frontier = set(memory_ids)
        for _ in range(depth):
            next_frontier = set()
            for node_id in frontier:
                for src, tgt, _, _ in self._edges:
                    neighbor = tgt if src == node_id else (src if tgt == node_id else None)
                    if neighbor and neighbor not in visited:
                        node = self._nodes.get(neighbor, {})
                        if node.get("lifecycle") not in exclude:
                            next_frontier.add(neighbor)
                            visited.add(neighbor)
            frontier = next_frontier
        return list(visited)

    def domain_neighbors(self, domain: str, limit: int = 20) -> list[str]:
        return [nid for nid, n in self._nodes.items()
                if n.get("domain") == domain and n.get("lifecycle") != "archived"][:limit]

    def degree(self, memory_id: str) -> int:
        return sum(1 for s, t, _, _ in self._edges if s == memory_id or t == memory_id)

    def avg_downstream_score(self, memory_id: str) -> float:
        downstream = [t for s, t, _, _ in self._edges if s == memory_id]
        if not downstream:
            return 0.0
        scores = [self._nodes[d]["score"] for d in downstream if d in self._nodes]
        return sum(scores) / len(scores) if scores else 0.0

    def count_similar(self, memory_id: str) -> int:
        return sum(1 for s, t, tp, _ in self._edges
                   if tp == "SIMILAR_TO" and (s == memory_id or t == memory_id))

    def mark_label(self, memory_id: str, label: str) -> None:
        if memory_id in self._nodes:
            self._nodes[memory_id]["lifecycle"] = label.lower()

    def batch_create_edges(self, edges: list[tuple[str, str, str, dict]]) -> int:
        count = 0
        for src, tgt, etype, props in edges:
            if not any(e[0] == src and e[1] == tgt and e[2] == etype for e in self._edges):
                self._edges.append((src, tgt, etype, props))
                count += 1
        return count

    def verify(self) -> bool:
        return self._available


@pytest.fixture
def store():
    return MockNeo4jStore()


class TestNeo4jStore:
    def test_create_node_and_retrieve(self, store):
        store.create_node("n1", "episodic", "test", "preview", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        node = store.get_node("n1")
        assert node is not None
        assert node["memory_id"] == "n1"
        assert node["domain"] == "test"

    def test_create_edge(self, store):
        store.create_node("n1", "episodic", "test", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("n2", "semantic", "test", "b", 8.0, 0.9, 1.0, "stm", "2026-01-01")
        store.create_edge("n1", "n2", "SIMILAR_TO", {"similarity": 0.9})
        assert store.degree("n1") == 1

    def test_graph_expand_1_hop(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=1)
        assert set(result) == {"a", "b"}

    def test_graph_expand_2_hops(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=2)
        assert set(result) == {"a", "b", "c"}

    def test_graph_expand_excludes_forgotten(self, store):
        store.create_node("a", "episodic", "t", "a", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "semantic", "t", "b", 7.0, 0.8, 1.0, "archived", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "PRODUCED")
        store.create_edge("b", "c", "RELATED_TO")
        result = store.expand(["a"], depth=2, exclude_labels=["archived"])
        assert "c" not in result

    def test_domain_neighbors(self, store):
        for i in range(3):
            store.create_node(f"g{i}", "episodic", "greenhouse", f"g{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(2):
            store.create_node(f"w{i}", "episodic", "workday", f"w{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        result = store.domain_neighbors("greenhouse")
        assert len(result) == 3

    def test_degree_count(self, store):
        store.create_node("hub", "episodic", "t", "hub", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(7):
            store.create_node(f"n{i}", "semantic", "t", f"n{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
            store.create_edge("hub", f"n{i}", "PRODUCED")
        assert store.degree("hub") == 7

    def test_downstream_scores(self, store):
        store.create_node("a", "episodic", "t", "a", 5.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("b", "procedural", "t", "b", 8.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_node("c", "procedural", "t", "c", 6.0, 0.8, 1.0, "stm", "2026-01-01")
        store.create_edge("a", "b", "TAUGHT")
        store.create_edge("a", "c", "TAUGHT")
        assert store.avg_downstream_score("a") == 7.0

    def test_count_similar(self, store):
        store.create_node("target", "semantic", "t", "t", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        for i in range(4):
            store.create_node(f"s{i}", "semantic", "t", f"s{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
            store.create_edge("target", f"s{i}", "SIMILAR_TO")
        assert store.count_similar("target") == 4

    def test_platform_node_linking(self, store):
        store.create_node("proc1", "procedural", "greenhouse", "escape", 8.0, 0.9, 1.0, "stm", "2026-01-01")
        store.create_node("greenhouse", "platform", "greenhouse", "Greenhouse", 0.0, 1.0, 1.0, "ltm", "2026-01-01")
        store.create_edge("proc1", "greenhouse", "APPLIES_TO")
        result = store.expand(["greenhouse"], depth=1)
        assert "proc1" in result

    def test_batch_edge_creation(self, store):
        for i in range(20):
            store.create_node(f"n{i}", "episodic", "t", f"n{i}", 7.0, 0.8, 1.0, "stm", "2026-01-01")
        edges = [(f"n{i}", f"n{i+1}", "RELATED_TO", {}) for i in range(15)]
        count = store.batch_create_edges(edges)
        assert count == 15
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/memory_layer/test_neo4j_store.py -v 2>&1 | head -10`
Expected: FAIL

- [ ] **Step 3: Implement Neo4jStore**

Create `shared/memory_layer/_neo4j_store.py`:

```python
"""Neo4jStore — graph backend for the memory system.

Handles node CRUD, edge creation, graph traversal, and signal queries
(degree, downstream scores, similar count). Falls back gracefully if
Neo4j is unavailable.
"""

import os
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)


class Neo4jStore:
    """Neo4j graph backend. All methods are no-ops if Neo4j is unavailable."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ):
        self._uri = uri or os.environ.get("MEMORY_NEO4J_URI", "bolt://localhost:7687")
        self._user = user or os.environ.get("MEMORY_NEO4J_USER", "neo4j")
        self._password = password or os.environ.get("MEMORY_NEO4J_PASSWORD", "")
        self._driver = None
        self._available = False

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password)
            )
        return self._driver

    def verify(self) -> bool:
        try:
            driver = self._get_driver()
            driver.verify_connectivity()
            with driver.session() as session:
                session.run("CREATE CONSTRAINT memory_id_unique IF NOT EXISTS "
                           "FOR (m:Memory) REQUIRE m.memory_id IS UNIQUE")
            self._available = True
            return True
        except Exception as e:
            logger.warning("Neo4j unavailable: %s", e)
            self._available = False
            return False

    def create_node(
        self, memory_id: str, tier: str, domain: str,
        content_preview: str, score: float, confidence: float,
        decay_score: float, lifecycle: str, created_at: str,
    ) -> None:
        if not self._available:
            return
        with self._get_driver().session() as session:
            session.run(
                "MERGE (m:Memory {memory_id: $mid}) "
                "SET m.tier=$tier, m.domain=$domain, m.content_preview=$cp, "
                "m.score=$score, m.confidence=$conf, m.decay_score=$decay, "
                "m.lifecycle=$lc, m.created_at=$ca",
                mid=memory_id, tier=tier, domain=domain, cp=content_preview[:200],
                score=score, conf=confidence, decay=decay_score,
                lc=lifecycle, ca=created_at,
            )

    def get_node(self, memory_id: str) -> dict | None:
        if not self._available:
            return None
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (m:Memory {memory_id: $mid}) RETURN m",
                mid=memory_id,
            )
            record = result.single()
            return dict(record["m"]) if record else None

    def create_edge(
        self, source_id: str, target_id: str, edge_type: str,
        properties: dict | None = None,
    ) -> None:
        if not self._available:
            return
        props = properties or {}
        with self._get_driver().session() as session:
            session.run(
                f"MATCH (a:Memory {{memory_id: $src}}) "
                f"MATCH (b:Memory {{memory_id: $tgt}}) "
                f"MERGE (a)-[r:{edge_type}]->(b) "
                f"SET r += $props",
                src=source_id, tgt=target_id, props=props,
            )

    def batch_create_edges(self, edges: list[tuple[str, str, str, dict]]) -> int:
        if not self._available:
            return 0
        count = 0
        with self._get_driver().session() as session:
            for src, tgt, etype, props in edges:
                result = session.run(
                    f"MATCH (a:Memory {{memory_id: $src}}) "
                    f"MATCH (b:Memory {{memory_id: $tgt}}) "
                    f"MERGE (a)-[r:{etype}]->(b) "
                    f"ON CREATE SET r += $props "
                    f"RETURN type(r) as t",
                    src=src, tgt=tgt, props=props or {},
                )
                if result.single():
                    count += 1
        return count

    def expand(
        self, memory_ids: list[str], depth: int = 1,
        exclude_labels: list[str] | None = None,
    ) -> list[str]:
        if not self._available:
            return list(memory_ids)
        exclude = exclude_labels or []
        with self._get_driver().session() as session:
            where_clause = ""
            if exclude:
                conditions = " AND ".join(
                    f"NOT n.lifecycle = '{lbl}'" for lbl in exclude
                )
                where_clause = f"WHERE {conditions}"
            result = session.run(
                f"MATCH (start:Memory) WHERE start.memory_id IN $ids "
                f"CALL apoc.path.subgraphNodes(start, {{maxLevel: $depth, "
                f"labelFilter: '+Memory'}}) YIELD node AS n "
                f"{where_clause} "
                f"RETURN DISTINCT n.memory_id AS mid",
                ids=memory_ids, depth=depth,
            )
            return [r["mid"] for r in result if r["mid"]]

    def domain_neighbors(self, domain: str, limit: int = 20) -> list[str]:
        if not self._available:
            return []
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (m:Memory {domain: $domain}) "
                "WHERE NOT m.lifecycle = 'archived' "
                "RETURN m.memory_id AS mid LIMIT $limit",
                domain=domain, limit=limit,
            )
            return [r["mid"] for r in result]

    def degree(self, memory_id: str) -> int:
        if not self._available:
            return 0
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (m:Memory {memory_id: $mid})-[r]-() RETURN count(r) AS deg",
                mid=memory_id,
            )
            record = result.single()
            return record["deg"] if record else 0

    def avg_downstream_score(self, memory_id: str) -> float:
        if not self._available:
            return 0.0
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (m:Memory {memory_id: $mid})-[]->(n:Memory) "
                "RETURN avg(n.score) AS avg_score",
                mid=memory_id,
            )
            record = result.single()
            return record["avg_score"] or 0.0 if record else 0.0

    def count_similar(self, memory_id: str) -> int:
        if not self._available:
            return 0
        with self._get_driver().session() as session:
            result = session.run(
                "MATCH (m:Memory {memory_id: $mid})-[:SIMILAR_TO]-() "
                "RETURN count(*) AS cnt",
                mid=memory_id,
            )
            record = result.single()
            return record["cnt"] if record else 0

    def mark_label(self, memory_id: str, label: str) -> None:
        if not self._available:
            return
        with self._get_driver().session() as session:
            session.run(
                "MATCH (m:Memory {memory_id: $mid}) SET m.lifecycle = $lbl",
                mid=memory_id, lbl=label.lower(),
            )

    def count(self) -> int:
        if not self._available:
            return 0
        with self._get_driver().session() as session:
            result = session.run("MATCH (m:Memory) RETURN count(m) AS cnt")
            record = result.single()
            return record["cnt"] if record else 0

    def close(self):
        if self._driver:
            self._driver.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/shared/memory_layer/test_neo4j_store.py -v`
Expected: 12 passed (using MockNeo4jStore, no Docker needed)

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_neo4j_store.py tests/shared/memory_layer/test_neo4j_store.py
git commit -m "feat(memory): add Neo4jStore with graph traversal, signals, mock for tests"
```

---

## Task 7: QueryRouter — Engine Selection

**Files:**
- Create: `shared/memory_layer/_query.py`
- Create: `tests/shared/memory_layer/test_query_router.py`

- [ ] **Step 1: Write test_query_router.py**

Create `tests/shared/memory_layer/test_query_router.py`:

```python
import pytest
from shared.memory_layer._query import MemoryQuery, QueryRouter, Step, Engine
from shared.memory_layer._entries import MemoryTier


@pytest.fixture
def router():
    return QueryRouter(qdrant_available=True, neo4j_available=True)


class TestQueryRouter:
    def test_exact_lookup_routes_to_sqlite(self, router):
        q = MemoryQuery(memory_id="abc123")
        plan = router.route(q)
        assert plan.engines == [Engine.SQLITE]

    def test_semantic_query_routes_to_qdrant(self, router):
        q = MemoryQuery(semantic_query="form filling")
        plan = router.route(q)
        assert Engine.QDRANT in plan.engines
        assert Step.VECTOR_SEARCH in plan.steps

    def test_graph_depth_adds_neo4j(self, router):
        q = MemoryQuery(semantic_query="form filling", graph_depth=2)
        plan = router.route(q)
        assert Engine.NEO4J in plan.engines
        assert Step.GRAPH_EXPAND in plan.steps

    def test_domain_only_routes_to_neo4j(self, router):
        q = MemoryQuery(domain="greenhouse")
        plan = router.route(q)
        assert Engine.NEO4J in plan.engines
        assert Step.DOMAIN_CLUSTER in plan.steps

    def test_fallback_to_sqlite_when_qdrant_down(self):
        router = QueryRouter(qdrant_available=False, neo4j_available=True)
        q = MemoryQuery(semantic_query="test")
        plan = router.route(q)
        assert Engine.QDRANT not in plan.engines
        assert Engine.SQLITE in plan.engines
        assert Step.FTS_SEARCH in plan.steps

    def test_fallback_skips_graph_when_neo4j_down(self):
        router = QueryRouter(qdrant_available=True, neo4j_available=False)
        q = MemoryQuery(semantic_query="test", graph_depth=2)
        plan = router.route(q)
        assert Engine.NEO4J not in plan.engines
        assert Step.GRAPH_EXPAND not in plan.steps

    def test_min_decay_score_filter_applied(self, router):
        q = MemoryQuery(semantic_query="test", min_decay_score=0.3)
        plan = router.route(q)
        assert plan.min_decay_score == 0.3

    def test_tier_filter_applied(self, router):
        q = MemoryQuery(semantic_query="test", tiers=[MemoryTier.SEMANTIC])
        plan = router.route(q)
        assert plan.tier_filter == [MemoryTier.SEMANTIC]
```

- [ ] **Step 2: Implement _query.py**

Create `shared/memory_layer/_query.py`:

```python
"""QueryRouter — picks engine(s) and steps based on query type."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from shared.memory_layer._entries import MemoryTier


class Engine(str, Enum):
    SQLITE = "sqlite"
    QDRANT = "qdrant"
    NEO4J = "neo4j"


class Step(str, Enum):
    VECTOR_SEARCH = "vector_search"
    FTS_SEARCH = "fts_search"
    GRAPH_EXPAND = "graph_expand"
    DOMAIN_CLUSTER = "domain_cluster"
    HYDRATE = "hydrate"
    DEDUPLICATE = "deduplicate"


@dataclass
class MemoryQuery:
    memory_id: str | None = None
    semantic_query: str | None = None
    domain: str | None = None
    tiers: list[MemoryTier] | None = None
    graph_depth: int = 0
    top_k: int = 10
    min_decay_score: float = 0.1
    min_confidence: float = 0.0


@dataclass
class RetrievalPlan:
    engines: list[Engine] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    tier_filter: list[MemoryTier] | None = None
    min_decay_score: float = 0.1
    top_k: int = 10
    graph_depth: int = 0


class QueryRouter:
    def __init__(self, qdrant_available: bool = True, neo4j_available: bool = True):
        self._qdrant = qdrant_available
        self._neo4j = neo4j_available

    def route(self, query: MemoryQuery) -> RetrievalPlan:
        plan = RetrievalPlan(
            tier_filter=query.tiers,
            min_decay_score=query.min_decay_score,
            top_k=query.top_k,
            graph_depth=query.graph_depth,
        )

        if query.memory_id:
            plan.engines = [Engine.SQLITE]
            plan.steps = [Step.HYDRATE]
            return plan

        if query.semantic_query:
            if self._qdrant:
                plan.engines.append(Engine.QDRANT)
                plan.steps.append(Step.VECTOR_SEARCH)
            else:
                plan.engines.append(Engine.SQLITE)
                plan.steps.append(Step.FTS_SEARCH)

            if query.graph_depth > 0 and self._neo4j:
                plan.engines.append(Engine.NEO4J)
                plan.steps.append(Step.GRAPH_EXPAND)
                plan.steps.append(Step.DEDUPLICATE)

            plan.engines.append(Engine.SQLITE)
            plan.steps.append(Step.HYDRATE)
            return plan

        if query.domain and not query.semantic_query:
            if self._neo4j:
                plan.engines.append(Engine.NEO4J)
                plan.steps.append(Step.DOMAIN_CLUSTER)
            plan.engines.append(Engine.SQLITE)
            plan.steps.append(Step.HYDRATE)
            return plan

        plan.engines = [Engine.SQLITE]
        plan.steps = [Step.HYDRATE]
        return plan
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/shared/memory_layer/test_query_router.py -v`
Expected: 8 passed

- [ ] **Step 4: Commit**

```bash
git add shared/memory_layer/_query.py tests/shared/memory_layer/test_query_router.py
git commit -m "feat(memory): add QueryRouter with engine selection + degradation fallback"
```

---

## Task 8: ForgettingEngine — Decay, Promotion, Revival

**Files:**
- Create: `shared/memory_layer/_forgetting.py`
- Create: `tests/shared/memory_layer/test_forgetting.py`

This is the largest single task. I'll provide the key tests and full implementation. The test file is long — it covers all 22 test cases from the spec.

- [ ] **Step 1: Write test_forgetting.py**

Create `tests/shared/memory_layer/test_forgetting.py` with all 22 tests. Key tests (full file too large for inline — write the complete file following the spec's test table in section 6):

```python
import math
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from shared.memory_layer._entries import (
    MemoryEntry, MemoryTier, Lifecycle, ProtectionLevel,
)
from shared.memory_layer._forgetting import ForgettingEngine, compute_decay_score

BASE_STABILITY = 48.0


@pytest.fixture
def neo4j_mock():
    mock = MagicMock()
    mock.degree.return_value = 0
    mock.avg_downstream_score.return_value = 0.0
    mock.count_similar.return_value = 0
    return mock


@pytest.fixture
def engine(neo4j_mock):
    return ForgettingEngine(neo4j=neo4j_mock)


class TestDecayScore:
    def test_fresh_memory_is_near_1(self, engine, make_memory):
        entry = make_memory()
        score = engine.compute_decay(entry)
        assert score > 0.9

    def test_drops_over_time(self, engine, make_memory):
        entry = make_memory()
        entry.last_accessed = datetime.now() - timedelta(hours=48)
        score = engine.compute_decay(entry)
        assert score < 0.6

    def test_access_count_increases_stability(self, engine, make_memory):
        low_access = make_memory(access_count=0)
        high_access = make_memory(access_count=10)
        low_access.last_accessed = datetime.now() - timedelta(hours=48)
        high_access.last_accessed = datetime.now() - timedelta(hours=48)
        low_score = engine.compute_decay(low_access)
        high_score = engine.compute_decay(high_access)
        assert high_score > low_score

    def test_quality_signal_boosts_decay(self, engine, make_memory):
        low_q = make_memory(score=3.0)
        high_q = make_memory(score=9.0)
        low_q.last_accessed = datetime.now() - timedelta(hours=24)
        high_q.last_accessed = datetime.now() - timedelta(hours=24)
        assert engine.compute_decay(high_q) > engine.compute_decay(low_q)

    def test_connectivity_signal(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.degree.return_value = 6
        score = engine.compute_decay(entry)
        assert score > 0.95  # connectivity = 1.0 boosts total

    def test_uniqueness_last_survivor(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.count_similar.return_value = 0
        score = engine.compute_decay(entry)
        neo4j_mock.count_similar.return_value = 5
        score_redundant = engine.compute_decay(entry)
        assert score > score_redundant

    def test_impact_from_descendants(self, engine, neo4j_mock, make_memory):
        entry = make_memory()
        neo4j_mock.avg_downstream_score.return_value = 9.0
        score = engine.compute_decay(entry)
        assert score > 0.95


class TestProtection:
    def test_pinned_never_forgotten(self, engine, make_memory):
        entry = make_memory(payload={"pinned": True})
        assert engine.get_protection(entry) == ProtectionLevel.PINNED

    def test_last_survivor(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 0
        entry = make_memory()
        assert engine.get_protection(entry) == ProtectionLevel.PROTECTED

    def test_hub_node_elevated(self, engine, neo4j_mock, make_memory):
        neo4j_mock.degree.return_value = 6
        neo4j_mock.count_similar.return_value = 3
        entry = make_memory()
        assert engine.get_protection(entry) == ProtectionLevel.ELEVATED


class TestSweep:
    def test_stm_tombstoned_below_threshold(self, engine, make_memory):
        entry = make_memory(lifecycle=Lifecycle.STM, decay_score=0.2)
        entry.last_accessed = datetime.now() - timedelta(hours=100)
        actions = engine.evaluate_single(entry)
        assert actions.get("tombstone") is True

    def test_ltm_not_tombstoned(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 5
        entry = make_memory(lifecycle=Lifecycle.LTM, decay_score=0.05)
        actions = engine.evaluate_single(entry)
        assert actions.get("tombstone") is not True

    def test_promotion_stm_to_mtm(self, engine, make_memory):
        entry = make_memory(lifecycle=Lifecycle.STM, access_count=4)
        actions = engine.evaluate_single(entry)
        assert actions.get("promote_to") == Lifecycle.MTM

    def test_promotion_mtm_to_ltm(self, engine, make_memory):
        entry = make_memory(
            lifecycle=Lifecycle.MTM, access_count=12,
            payload={"times_validated": 6},
        )
        actions = engine.evaluate_single(entry)
        assert actions.get("promote_to") == Lifecycle.LTM

    def test_demotion_ltm_to_cold(self, engine, neo4j_mock, make_memory):
        neo4j_mock.count_similar.return_value = 5
        entry = make_memory(lifecycle=Lifecycle.LTM, decay_score=0.05)
        entry.last_accessed = datetime.now() - timedelta(days=60)
        entry.confidence = 0.5
        actions = engine.evaluate_single(entry)
        assert actions.get("demote_to") == Lifecycle.COLD
```

- [ ] **Step 2: Implement ForgettingEngine**

Create `shared/memory_layer/_forgetting.py`:

```python
"""ForgettingEngine — 6-signal decay, lifecycle promotion/demotion, revival."""

import math
from datetime import datetime, timedelta
from typing import Optional

from shared.logging_config import get_logger
from shared.memory_layer._entries import (
    MemoryEntry, Lifecycle, ProtectionLevel,
)

logger = get_logger(__name__)

BASE_STABILITY = 48.0
STM_THRESHOLD = 0.3
MTM_THRESHOLD = 0.1
STM_TO_MTM_ACCESSES = 3
MTM_TO_LTM_ACCESSES = 10
MTM_TO_LTM_VALIDATIONS = 5
LTM_COLD_DECAY = 0.1


class ForgettingEngine:
    def __init__(self, neo4j=None):
        self._neo4j = neo4j

    def compute_decay(self, entry: MemoryEntry) -> float:
        hours_since = max(0.001, (datetime.now() - entry.last_accessed).total_seconds() / 3600.0)
        stability = BASE_STABILITY * (1 + 0.3 * entry.access_count)
        recency = math.exp(-hours_since / stability)

        age_days = max(1, (datetime.now() - entry.created_at).days)
        frequency = min(1.0, entry.access_count / age_days)

        quality = entry.score / 10.0

        edge_count = self._neo4j.degree(entry.memory_id) if self._neo4j else 0
        connectivity = min(1.0, edge_count / 5.0)

        similar = self._neo4j.count_similar(entry.memory_id) if self._neo4j else 0
        if similar == 0:
            uniqueness = 1.0
        elif similar <= 2:
            uniqueness = 0.7
        else:
            uniqueness = 0.3

        downstream = self._neo4j.avg_downstream_score(entry.memory_id) if self._neo4j else 0.0
        impact = downstream / 10.0 if downstream else 0.5

        return (
            recency * 0.30
            + frequency * 0.20
            + quality * 0.15
            + connectivity * 0.15
            + uniqueness * 0.10
            + impact * 0.10
        )

    def get_protection(self, entry: MemoryEntry) -> ProtectionLevel:
        if entry.payload.get("pinned"):
            return ProtectionLevel.PINNED

        similar = self._neo4j.count_similar(entry.memory_id) if self._neo4j else 1
        if similar == 0:
            return ProtectionLevel.PROTECTED

        if entry.confidence >= 0.95 and entry.lifecycle == Lifecycle.LTM:
            return ProtectionLevel.PROTECTED

        degree = self._neo4j.degree(entry.memory_id) if self._neo4j else 0
        if degree >= 5 or entry.lifecycle == Lifecycle.LTM:
            return ProtectionLevel.ELEVATED

        return ProtectionLevel.NONE

    def evaluate_single(self, entry: MemoryEntry) -> dict:
        actions = {}
        decay = self.compute_decay(entry)
        actions["decay_score"] = decay
        protection = self.get_protection(entry)

        if entry.lifecycle == Lifecycle.STM and entry.access_count >= STM_TO_MTM_ACCESSES:
            hours_since_creation = (datetime.now() - entry.created_at).total_seconds() / 3600.0
            if hours_since_creation <= 24:
                actions["promote_to"] = Lifecycle.MTM
                return actions

        if entry.lifecycle == Lifecycle.MTM and entry.access_count >= MTM_TO_LTM_ACCESSES:
            validations = entry.payload.get("times_validated", entry.payload.get("times_used", 0))
            if validations >= MTM_TO_LTM_VALIDATIONS:
                actions["promote_to"] = Lifecycle.LTM
                return actions

        if protection == ProtectionLevel.PINNED:
            return actions
        if protection == ProtectionLevel.PROTECTED:
            return actions

        threshold = {
            Lifecycle.STM: STM_THRESHOLD,
            Lifecycle.MTM: MTM_THRESHOLD,
        }.get(entry.lifecycle)

        if threshold is not None:
            if protection == ProtectionLevel.ELEVATED:
                threshold *= 0.5
            if decay < threshold:
                actions["tombstone"] = True
                return actions

        if entry.lifecycle == Lifecycle.LTM:
            if protection != ProtectionLevel.ELEVATED and decay < LTM_COLD_DECAY:
                actions["demote_to"] = Lifecycle.COLD

        return actions
```

- [ ] **Step 3: Run tests**

Run: `python -m pytest tests/shared/memory_layer/test_forgetting.py -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add shared/memory_layer/_forgetting.py tests/shared/memory_layer/test_forgetting.py
git commit -m "feat(memory): add ForgettingEngine with 6-signal decay + protection + promotion"
```

---

## Task 9: AutonomousLinker — A-MEM Graph Linking

**Files:**
- Create: `shared/memory_layer/_linker.py`
- Create: `tests/shared/memory_layer/test_linker.py`

- [ ] **Step 1: Write test_linker.py with all 12 linker tests from the spec**

Follow the same pattern as Tasks 3-8: write tests first based on the spec's linker test table (section 5), then implement. The linker needs access to both QdrantStore and Neo4jStore (use mocks from conftest).

- [ ] **Step 2: Implement AutonomousLinker**

Create `shared/memory_layer/_linker.py` with:
- `classify_relationship()` — rule-based (handles ~80%)
- `_llm_classify_batch()` — LLM fallback for ambiguous pairs
- `link_new_memory()` — full pipeline: Qdrant neighbors → Neo4j domain neighbors → classify → create edges
- `handle_contradiction()` — confidence decay, tombstoning

- [ ] **Step 3: Run tests and commit**

```bash
git add shared/memory_layer/_linker.py tests/shared/memory_layer/test_linker.py
git commit -m "feat(memory): add AutonomousLinker with rule-based + LLM classification"
```

---

## Task 10: SyncService ��� 3-Engine Reconciliation

**Files:**
- Create: `shared/memory_layer/_sync.py`
- Create: `tests/shared/memory_layer/test_sync.py`

- [ ] **Step 1: Write 5 sync tests from the spec (section 8)**

- [ ] **Step 2: Implement SyncService**

Create `shared/memory_layer/_sync.py` with:
- `reconcile()` — on startup, backfill Qdrant/Neo4j from SQLite
- `propagate_tombstone()` — delete from Qdrant, mark FORGOTTEN in Neo4j
- `queue_vector_upsert()` / `queue_graph_create()` — async write queue

- [ ] **Step 3: Run tests and commit**

```bash
git add shared/memory_layer/_sync.py tests/shared/memory_layer/test_sync.py
git commit -m "feat(memory): add SyncService for 3-engine reconciliation"
```

---

## Task 11: Upgrade MemoryManager — Wire All Engines

**Files:**
- Modify: `shared/memory_layer/_manager.py`
- Modify: `shared/memory_layer/__init__.py`
- Create: `tests/shared/memory_layer/test_manager.py`
- Create: `tests/shared/memory_layer/test_backwards_compat.py`

- [ ] **Step 1: Write test_manager.py (6 tests) and test_backwards_compat.py (7 tests) from spec sections 9 and 12**

- [ ] **Step 2: Upgrade _manager.py**

Rewrite `MemoryManager.__init__()` to accept and wire SQLiteStore, QdrantStore, Neo4jStore, MemoryEmbedder, AutonomousLinker, ForgettingEngine, SyncService. Keep ALL existing method signatures. Add new methods: `store_memory()`, `query()`, `pin_memory()`, `startup()`, `health()`.

- [ ] **Step 3: Update __init__.py exports**

Add to `shared/memory_layer/__init__.py`:

```python
from shared.memory_layer._entries import (  # noqa: F401
    MemoryEntry, MemoryTier, Lifecycle, EdgeType, ProtectionLevel,
)
from shared.memory_layer._query import MemoryQuery  # noqa: F401
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/shared/memory_layer/ -v`
Expected: All pass including backwards compatibility

- [ ] **Step 5: Commit**

```bash
git add shared/memory_layer/_manager.py shared/memory_layer/__init__.py tests/shared/memory_layer/test_manager.py tests/shared/memory_layer/test_backwards_compat.py
git commit -m "feat(memory): upgrade MemoryManager with 3-engine wiring + backwards compat"
```

---

## Task 12: Integration & Degradation Tests

**Files:**
- Create: `tests/shared/memory_layer/test_integration.py`
- Create: `tests/shared/memory_layer/test_degradation.py`

- [ ] **Step 1: Write test_integration.py (8 end-to-end tests from spec section 10)**

- [ ] **Step 2: Write test_degradation.py (6 degradation tests from spec section 11)**

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/shared/memory_layer/ -v --tb=short`
Expected: All 113 tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/shared/memory_layer/test_integration.py tests/shared/memory_layer/test_degradation.py
git commit -m "test(memory): add integration + degradation tests (113 total)"
```

---

## Task 13: Run Existing Test Suite — No Regressions

**Files:** None (verification only)

- [ ] **Step 1: Run full project test suite**

Run: `python -m pytest tests/ -v --tb=short -q 2>&1 | tail -20`
Expected: All existing tests still pass. Zero regressions.

- [ ] **Step 2: If any failures, fix them**

The only expected breakage: tests that directly import from `_entries.py` or `_stores.py` — these should still work since we kept all old dataclasses.

- [ ] **Step 3: Commit any fixes**

```bash
git commit -am "fix: resolve regressions from memory upgrade"
```

---

## Task 14: Documentation Updates — Agent-Facing Files

**Files:**
- Modify: `shared/CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md` (root)
- Modify: `jobpulse/CLAUDE.md`
- Modify: `patterns/CLAUDE.md`
- Modify: `.claude/rules/shared.md`
- Create: `shared/memory_layer/CLAUDE.md`

- [ ] **Step 1: Create shared/memory_layer/CLAUDE.md**

```markdown
# Memory Layer — 3-Engine Architecture

SQLite (source of truth) + Qdrant (vector search) + Neo4j (knowledge graph).

## Agent Memory Lifecycle
1. READ: `memory_manager.get_context_for_agent(agent_name, topic, domain)`
2. EXECUTE: agent runs with memory-enriched context
3. WRITE: `memory_manager.store_memory(tier, domain, content, score)`
4. LINK: AutonomousLinker auto-discovers connections (async)

## Public API
- `get_context_for_agent()` — assemble context for an agent (backwards compatible)
- `store_memory()` — write to all 3 engines
- `query(MemoryQuery)` — routed retrieval (exact/semantic/graph/combined)
- `pin_memory()` — prevent forgetting
- `health()` — engine status

## Rules
- ALL memory access through MemoryManager — never query engines directly
- Embeddings via Voyage 3 Large (fallback: MiniLM)
- Lifecycle: STM → MTM → LTM → Cold → Archive
- Forgetting sweep runs hourly — 6-signal decay score
```

- [ ] **Step 2: Add memory section to shared/CLAUDE.md**

Append after the existing module table:

```markdown
## Memory Layer (shared/memory_layer/)
3-engine hybrid: SQLite (truth) + Qdrant (vectors) + Neo4j (graph).
- `_sqlite_store.py` — Source of truth CRUD
- `_qdrant_store.py` — Filtered HNSW vector search
- `_neo4j_store.py` — Graph traversal + signals
- `_embedder.py` — Voyage 3 Large + MiniLM fallback
- `_linker.py` — Autonomous graph linking (A-MEM)
- `_forgetting.py` — 6-signal decay + lifecycle promotion
- `_query.py` — QueryRouter picks engine(s) per query
- `_sync.py` — 3-engine reconciliation
- `_manager.py` — MemoryManager facade (single entry point)
All memory access goes through MemoryManager — never query engines directly.
```

- [ ] **Step 3: Add memory briefing to AGENTS.md**

Append to `AGENTS.md`:

```markdown
## Memory System

Before agent execution, call `memory_manager.get_context_for_agent(agent_name, topic, domain)` to get relevant context. After execution, call `memory_manager.store_memory(tier, domain, content, score)` for any learned facts, procedures, or notable outcomes.

Never query SQLite/Qdrant/Neo4j directly — always go through MemoryManager. Same principle as get_llm() for LLM calls.
```

- [ ] **Step 4: Update root CLAUDE.md module context table**

Add row: `shared/memory_layer/CLAUDE.md �� 3-engine memory: SQLite (truth) + Qdrant (vectors) + Neo4j (graph)`

- [ ] **Step 5: Add rule to .claude/rules/shared.md**

Append: `All memory access goes through MemoryManager — never query SQLite/Qdrant/Neo4j directly.`

- [ ] **Step 6: Commit**

```bash
git add shared/memory_layer/CLAUDE.md shared/CLAUDE.md AGENTS.md CLAUDE.md .claude/rules/shared.md
git commit -m "docs: update agent-facing files with memory system architecture"
```

---

## Task 15: Final Verification & Cleanup

- [ ] **Step 1: Run full test suite one final time**

Run: `python -m pytest tests/ -v --tb=short -q 2>&1 | tail -30`
Expected: All tests pass (existing + 113 new)

- [ ] **Step 2: Verify Neo4j Docker starts and connects**

Run: `docker compose -f docker-compose.memory.yml up -d && sleep 10 && python -c "from shared.memory_layer._neo4j_store import Neo4jStore; s = Neo4jStore(); print('connected' if s.verify() else 'failed')" && docker compose -f docker-compose.memory.yml down`
Expected: `connected`

- [ ] **Step 3: Verify Qdrant in-memory mode works**

Run: `python -c "from shared.memory_layer._qdrant_store import QdrantStore; s = QdrantStore(location=':memory:'); s.ensure_collections(); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify backwards compatibility**

Run: `python -c "from shared.memory_layer import MemoryManager, get_shared_memory_manager; mm = get_shared_memory_manager(); print(mm.get_memory_report()[:50])"`
Expected: Prints memory report without errors

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(memory): complete hybrid 3-engine memory system upgrade

Implements Pillar 1 of the autonomous agent infrastructure:
- SQLite source of truth + Qdrant vector search + Neo4j knowledge graph
- 6-signal decay scoring with 4 protection levels
- Autonomous graph linking (A-MEM/Zettelkasten)
- Lifecycle promotion/demotion (STM → MTM → LTM → Cold → Archive)
- Memory revival for tombstoned entries
- Graceful degradation when engines are unavailable
- 113 tests across 12 test files
- Agent-facing documentation updated"
```

---

## Summary

| Task | What | Tests | Files |
|------|------|-------|-------|
| 1 | Dependencies + Docker | 0 | 2 |
| 2 | MemoryEntry + Enums | 0 | 2 + conftest |
| 3 | SQLiteStore | 10 | 2 |
| 4 | MemoryEmbedder | 7 | 2 |
| 5 | QdrantStore | 10 | 2 |
| 6 | Neo4jStore | 12 | 2 |
| 7 | QueryRouter | 8 | 2 |
| 8 | ForgettingEngine | 22 | 2 |
| 9 | AutonomousLinker | 12 | 2 |
| 10 | SyncService | 5 | 2 |
| 11 | MemoryManager upgrade | 13 | 4 |
| 12 | Integration + Degradation | 14 | 2 |
| 13 | Regression check | 0 | 0 |
| 14 | Documentation | 0 | 7 |
| 15 | Final verification | 0 | 0 |
| **Total** | | **113** | **33** |
