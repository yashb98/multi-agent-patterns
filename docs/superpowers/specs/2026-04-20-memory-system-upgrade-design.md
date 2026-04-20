# Memory System Upgrade — Hybrid 3-Engine Architecture

**Date:** 2026-04-20
**Pillar:** 1 of 6 (Autonomous Agent Infrastructure)
**Status:** Design approved, pending implementation plan

---

## Problem Statement

The current memory system (`shared/memory_layer/`) has 5 knowledge tiers (STM, Episodic, Semantic, Procedural, Pattern) with JSON file persistence and keyword-based retrieval. It works but has four structural gaps:

1. **No graph-based inter-memory links** — memories are flat rows with no relationships. "Everything I know about Greenhouse" requires querying 4 separate stores and merging in Python.
2. **No time-based forgetting** — facts validated 6 months ago stay at full confidence forever. No Ebbinghaus-style decay.
3. **No heat-based tier promotion** — entries don't migrate from STM to LTM based on access patterns. Lifecycle is static.
4. **No embedding-based retrieval** — `EpisodicMemory.recall()` uses keyword overlap, not semantic similarity. Misses related memories with different wording.

## Solution: Hybrid 3-Engine Architecture

Three engines, each handling what it does best. SQLite is the source of truth, Qdrant handles vector retrieval, Neo4j handles graph relationships.

```
Write Path:
  Agent produces memory → SQLite (sync) → Qdrant + Neo4j (async)

Read Path (query-routed):
  Exact lookup         → SQLite          (0.1ms)
  Semantic similarity  → Qdrant → SQLite (2ms)
  Graph expansion      → Neo4j → SQLite  (3ms)
  Combined             → Qdrant → Neo4j → SQLite (6ms)
```

### Why Three Engines

| Engine | Role | Why not the others |
|--------|------|--------------------|
| **SQLite** | Source of truth, structured storage, exact lookups | Can't do fast vector similarity or graph traversal |
| **Qdrant** | Filtered HNSW vector search | Can't traverse relationships between memories |
| **Neo4j** | Graph traversal, autonomous linking, path queries | Overkill for simple key-value lookups |

### Degradation

- Qdrant down → SQLite FTS fallback (slower, less semantic, functional)
- Neo4j down → no graph expansion (vector search still works)
- SQLite down → system broken (source of truth, must always be up)

---

## Storage Budget

**10 GB total**, partitioned:

| Engine | Allocation | Capacity |
|--------|-----------|----------|
| SQLite | 3 GB | All memories + cold archive |
| Qdrant | 4 GB | Hot+warm vectors (~500K memories at 1024 dims) |
| Neo4j | 3 GB | Full graph (~1M nodes, ~5M edges) |

At ~200 memories/day, 10 GB lasts ~13 years.

### Tiered Capacity Limits

| Tier | Hot (Qdrant) | Warm (Neo4j+SQLite) | Cold (SQLite only) |
|------|---|---|---|
| Episodic | 5,000 | 50,000 | Unlimited |
| Semantic | 2,000 | 20,000 | Unlimited |
| Procedural | 1,000 | 10,000 | Unlimited |
| Experience | 500 | 5,000 | Unlimited |

---

## Embedding Model

**Primary:** Voyage 3 Large (1024 dims, $0.00018/1K tokens, top-3 MTEB retrieval)
**Fallback:** all-MiniLM-L6-v2 (384 dims, local, free, already loaded in NLP classifier)

Voyage API key already available (code intelligence MCP uses Voyage Code 3).

Cost: ~$0.009 to embed full memory store. ~$0.00002 per new memory.

---

## Unified Memory Entry

All 5 tiers use a single base model. Tier-specific data goes in `payload`.

```python
@dataclass
class MemoryEntry:
    memory_id: str              # UUID hex[:12]
    tier: MemoryTier            # EPISODIC | SEMANTIC | PROCEDURAL | PATTERN | EXPERIENCE
    lifecycle: Lifecycle        # STM | MTM | LTM | COLD | ARCHIVED
    domain: str
    content: str
    embedding: list[float]      # 1024 dims (Voyage 3 Large)

    # Forgetting curve metadata
    created_at: datetime
    last_accessed: datetime
    access_count: int           # heat counter for promotion
    decay_score: float          # 0.0-1.0, multi-signal computed

    # Quality signals
    score: float                # 0-10, from reviewer/evaluator
    confidence: float           # 0-1, reinforced or decayed

    # Tier-specific data
    payload: dict               # typed per tier

    # Soft delete
    is_tombstoned: bool
```

### SQLite Schema

```sql
CREATE TABLE memories (
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

CREATE INDEX idx_mem_tier ON memories(tier);
CREATE INDEX idx_mem_domain ON memories(domain);
CREATE INDEX idx_mem_decay ON memories(decay_score DESC);
CREATE INDEX idx_mem_lifecycle ON memories(lifecycle);

CREATE VIEW episodic_memories AS SELECT * FROM memories WHERE tier='episodic' AND NOT is_tombstoned;
CREATE VIEW semantic_facts AS SELECT * FROM memories WHERE tier='semantic' AND NOT is_tombstoned;
CREATE VIEW procedures AS SELECT * FROM memories WHERE tier='procedural' AND NOT is_tombstoned;
```

### Neo4j Node Model

```cypher
(:Memory {
    memory_id, tier, domain, content_preview,
    score, confidence, decay_score, lifecycle, created_at
})

// Platform/domain anchor nodes
(:Platform {name: "greenhouse"})
(:Domain {name: "physics"})
```

### Neo4j Edge Types

| Edge | Meaning | Example |
|------|---------|---------|
| `PRODUCED` | Episode discovered this fact | episode → "Workday uses React inputs" |
| `TAUGHT` | Episode taught this procedure | episode → "Clear dropdown before typing" |
| `EXTRACTED_FROM` | GRPO experience came from this episode | experience → episode |
| `SIMILAR_TO` | Semantically similar (auto-linked) | fact ↔ fact |
| `CONTRADICTS` | Conflicting information | new fact → old fact |
| `REINFORCES` | Supporting evidence | fact → fact |
| `SUPERSEDES` | Newer version replaces older | new procedure → old procedure |
| `RELATED_TO` | Cross-tier relevance (generic) | episode → procedure |
| `APPLIES_TO` | Procedure works on this platform | procedure → platform node |

### Qdrant Collections

One collection per tier for clean filtering:

```
episodic_memories:  vector_size=1024, payload={domain, score, created_at, lifecycle}
semantic_facts:     vector_size=1024, payload={domain, confidence, times_validated}
procedures:         vector_size=1024, payload={domain, success_rate, times_used}
experiences:        vector_size=1024, payload={domain, score}
```

Note: `PatternMemory` stays in its own JSON file (`_pattern.py`, unchanged) — patterns are low-volume orchestration metadata, not suited for vector search. Embeddings column is NOT stored in SQLite — vectors live exclusively in Qdrant. SQLite stores the `memory_id` which is the Qdrant point ID.

---

## Memory Lifecycle: STM → MTM → LTM → Cold → Archive

Every memory has both a **tier** (what kind of knowledge) and a **lifecycle stage** (how proven it is). These are orthogonal axes.

### Promotion Rules

| Transition | Trigger |
|------------|---------|
| STM → MTM | `access_count >= 3` AND accessed within 24h of creation |
| MTM → LTM | `access_count >= 10` AND validated across 5+ runs (episodic: 5 similar-task runs exist; semantic: `times_validated >= 5`; procedural: `times_used >= 5`) |
| LTM → Cold | `decay_score < 0.1` (only if not Protected/Pinned) |
| Cold → Archive | `decay_score < 0.01` after 30 days in Cold |

### What Each Stage Means for Retrieval

| Stage | In Qdrant? | In Neo4j? | In SQLite? | Retrievable by |
|-------|-----------|-----------|-----------|----------------|
| STM | Yes | Yes | Yes | All query types |
| MTM | Yes | Yes | Yes | All query types |
| LTM | Yes | Yes | Yes | All query types |
| Cold | No | Node exists (`:COLD`) | Yes | Graph walk, exact lookup |
| Archive | No | Node exists (`:ARCHIVED`) | Yes | Exact lookup, explicit revival |

Demotion to Cold removes the vector from Qdrant (saves space) but keeps the Neo4j node (preserves graph structure). Archive further marks the Neo4j node as excluded from traversal.

---

## Multi-Signal Decay Score

Six signals, weighted to prevent accidental deletion of valuable memories.

```python
decay_score = (
    recency      * 0.30 +   # e^(-t/stability), stability grows with access_count
    frequency    * 0.20 +   # access_count / age_days, normalized to 0-1
    quality      * 0.15 +   # score / 10.0
    connectivity * 0.15 +   # min(1.0, neo4j_degree / 5.0)
    uniqueness   * 0.10 +   # 1.0 if last survivor, 0.3 if redundant
    impact       * 0.10     # avg downstream memory scores / 10.0
)
```

### Signal Definitions

| Signal | Formula | What it protects |
|--------|---------|-----------------|
| **Recency** | `e^(-hours_since_access / (BASE_STABILITY * (1 + 0.3 * access_count)))` where `BASE_STABILITY = 48.0` (hours) | Recently used knowledge |
| **Frequency** | `min(1.0, access_count / age_days)` | Regularly accessed core knowledge |
| **Quality** | `score / 10.0` | High-scoring validated memories |
| **Connectivity** | `min(1.0, neo4j_edge_count / 5.0)` | Hub memories that anchor clusters |
| **Uniqueness** | `1.0` if no similar memories, `0.3` if 3+ similar exist | Last-survivor memories |
| **Impact** | `avg(downstream_memory_scores) / 10.0` | Ancestors of valuable memories |

### Protection Levels

| Level | When applied | Effect |
|-------|-------------|--------|
| `NONE` | Default | Normal decay rules |
| `ELEVATED` | Hub node (5+ edges) or LTM tier | Threshold halved (2x harder to forget) |
| `PROTECTED` | Last survivor in cluster, or confidence >= 0.95 | Only contradiction or manual deletion |
| `PINNED` | User explicitly pinned | Never auto-deleted |

### Forgetting Sweep

Runs every hour (configurable via `MEMORY_FORGETTING_INTERVAL`):

1. Recompute `decay_score` for all active memories
2. Check protection level
3. Apply lifecycle-specific thresholds: STM < 0.3, MTM < 0.1, LTM only if contradicted
4. Tombstone memories below threshold
5. Promote memories that crossed heat thresholds (STM → MTM, MTM → LTM)
6. Demote LTM memories with very low decay to Cold

### Memory Revival

Tombstoned memories can be revived within 30 days:

- When a new memory is created, check for tombstoned memories with >0.85 cosine similarity
- If found: revive the old memory instead of creating a duplicate
- Revived memories get 2x stability boost (harder to forget again — spaced repetition effect)

---

## Autonomous Graph Linking (A-MEM / Zettelkasten)

When a new memory is created, the system automatically discovers relationships to existing memories.

### Linking Pipeline

```
New memory written to SQLite
      |
      v
Step 1: Qdrant — find top-10 similar memories (cosine > 0.75)
                  + cross-tier search (cosine > 0.80)
      |
      v
Step 2: Neo4j — find domain neighbors within 2 hops
      |
      v
Step 3: Classify relationships (rule-based 80%, LLM fallback 20%)
      |
      v
Step 4: Neo4j — batch-create edges
```

### Relationship Classification

**Rule-based (free, instant) — handles ~80% of cases:**

| New tier | Existing tier | Condition | Edge type |
|----------|--------------|-----------|-----------|
| Same | Same | similarity > 0.85 | `SIMILAR_TO` |
| Episodic | Semantic | same domain | `PRODUCED` |
| Episodic | Procedural | same domain | `TAUGHT` |
| Experience | Episodic | any | `EXTRACTED_FROM` |
| Semantic | Semantic | similarity > 0.7, content conflicts | `CONTRADICTS` |
| Procedural | Procedural | similarity > 0.8, new scores higher | `SUPERSEDES` |
| Any | Any | similarity > 0.75, no specific rule | `RELATED_TO` |

**LLM fallback (~$0.001/call) — for ambiguous pairs where rules return None but similarity > 0.70:**

Batches up to 10 pairs per call. Uses gpt-4.1-nano. Expected: ~5% of new memories trigger this.

### Contradiction Handler

When a `CONTRADICTS` edge is created:
- Compare strength: `confidence * score * recency_boost`
- Stronger memory wins; loser's confidence drops by 0.2
- If loser's confidence < 0.2: tombstone it
- Always log the contradiction for audit

---

## Query Router

Picks engine(s) based on query type:

| Query type | Engines | Steps |
|-----------|---------|-------|
| Exact lookup by ID | SQLite | Direct fetch |
| Semantic similarity | Qdrant → SQLite | Vector search → hydrate |
| Domain cluster | Neo4j → SQLite | Graph walk → hydrate |
| Combined (semantic + related) | Qdrant → Neo4j → SQLite | Vector search → graph expand → deduplicate → hydrate |
| Filtered + semantic | Qdrant (payload filter) → SQLite | Filtered HNSW → hydrate |

### MemoryQuery Model

```python
@dataclass
class MemoryQuery:
    memory_id: str = None           # exact lookup
    semantic_query: str = None      # text to embed and search
    domain: str = None              # filter by domain
    tiers: list[MemoryTier] = None  # filter by tier
    graph_depth: int = 0            # Neo4j expansion hops (0 = no graph)
    top_k: int = 10                 # max results
    min_decay_score: float = 0.1    # exclude cold/archived
    min_confidence: float = 0.0     # quality floor
```

---

## Upgraded MemoryManager Interface

### Public API (backwards compatible)

Existing methods still work. New methods added for richer queries.

```python
class MemoryManager:
    # ── Existing (still works, delegates to new pipeline) ──
    def get_context_for_agent(self, agent_name, topic, domain="") -> str
    def record_step(self, agent, summary, score=None)
    def record_episode(self, topic, score, iterations, ...)
    def learn_fact(self, domain, fact, run_id="manual")
    def learn_procedure(self, domain, strategy, ...)
    def search_patterns(self, topic, domain="") -> tuple
    def learn_from_success(self, topic, domain, ...)

    # ── New ──
    async def store_memory(self, tier, domain, content, score, payload) -> str
    async def query(self, query: MemoryQuery) -> list[MemoryEntry]
    async def pin_memory(self, memory_id: str)
    async def startup(self)                    # verify engines, reconcile
    def health(self) -> dict                   # engine status + counts
    def get_memory_report(self) -> str         # upgraded with lifecycle stats
```

### `get_context_for_agent()` Upgrade

Same signature. Internals now use Qdrant for semantic retrieval and Neo4j for graph expansion:

- **Researcher/Reviewer:** Qdrant semantic search on episodic+procedural tiers → Neo4j expand 2 hops → format
- **Writer:** Qdrant semantic search on procedural tier → format strategies
- **All agents:** GRPO experience injection (unchanged)
- **Fallback:** If Qdrant/Neo4j unavailable, falls back to SQLite FTS (same as current behavior)

---

## File Structure

```
shared/memory_layer/
  __init__.py              # public API (unchanged exports)
  _entries.py              # MemoryEntry, MemoryTier, Lifecycle, EdgeType    [UPGRADED]
  _manager.py              # MemoryManager                                   [UPGRADED]
  _stores.py               # ShortTermMemory (unchanged, in-memory deque)
  _query.py                # MemoryQuery, RetrievalPlan, QueryRouter         [NEW]
  _sqlite_store.py         # SQLiteStore (replaces JSON persistence)         [NEW]
  _qdrant_store.py         # QdrantStore (vector search)                     [NEW]
  _neo4j_store.py          # Neo4jStore (graph traversal + linking)          [NEW]
  _embedder.py             # VoyageEmbedder + MiniLM fallback                [NEW]
  _linker.py               # AutonomousLinker (A-MEM graph linking)          [NEW]
  _forgetting.py           # ForgettingEngine (decay + sweep + revival)      [NEW]
  _sync.py                 # SyncService (3-engine reconciliation)           [NEW]
  _pattern.py              # PatternMemory (unchanged)
  _router.py               # TieredRouter (upgraded to use QueryRouter)
```

8 new files, 2 upgraded files, 3 unchanged files.

---

## Configuration & Production Swap

### Environment Variables

```bash
# Local (default)
MEMORY_SQLITE_PATH=data/agent_memory/memories.db
MEMORY_QDRANT_MODE=local
MEMORY_QDRANT_PATH=data/agent_memory/qdrant
MEMORY_NEO4J_URI=bolt://localhost:7687
MEMORY_NEO4J_USER=neo4j
MEMORY_NEO4J_PASSWORD=jobpulse
MEMORY_EMBED_MODEL=voyage-3-large
MEMORY_EMBED_FALLBACK=minilm
MEMORY_STORAGE_BUDGET_GB=10
MEMORY_FORGETTING_INTERVAL=3600
MEMORY_LINK_BATCH_SIZE=10
```

### Production Swap

| Local | Production | Change |
|-------|-----------|--------|
| SQLite file | PostgreSQL | Change `MEMORY_SQLITE_PATH` to `postgresql://...` |
| Qdrant local dir | Qdrant Cloud | Set `MEMORY_QDRANT_MODE=cloud` + URL + API key |
| Neo4j Docker | Neo4j Aura | Change `MEMORY_NEO4J_URI` to `neo4j+s://...` |

Zero code changes required. Backend factory selects implementation based on env vars.

### Docker Compose (Local Dev)

```yaml
services:
  neo4j:
    image: neo4j:5.26-community
    ports: ["7687:7687", "7474:7474"]
    environment:
      NEO4J_AUTH: neo4j/jobpulse
      NEO4J_PLUGINS: '["apoc"]'
    volumes: [neo4j_data:/data]
    mem_limit: 512m

volumes:
  neo4j_data:
```

Qdrant runs in-process via `qdrant-client` (local file mode). SQLite is a file. Only Neo4j needs Docker.

---

## Dependencies

```
# New packages
qdrant-client>=1.12.0       # Vector search (includes local mode)
neo4j>=5.26.0               # Graph database driver
voyageai>=0.3.0             # Embedding API client
```

---

## Documentation Updates (Agent-Facing)

The memory infrastructure is useless if agents don't know it exists. These documentation files are what AI agents read to discover and use the system. All must be updated as part of implementation.

### Agent Memory Lifecycle

Every agent must follow this loop. Documentation files must make it explicit:

```
READ  → memory_manager.get_context_for_agent(agent_name, topic, domain)
        → injected into system prompt before execution
        
EXECUTE → agent runs with memory-enriched context

WRITE → memory_manager.store_memory(tier, domain, content, score)
        → records what was learned/discovered

LINK  → AutonomousLinker discovers connections (async, automatic)
        → no agent action needed
```

### Files to Update

| File | Changes |
|------|---------|
| **shared/CLAUDE.md** | Add memory layer section: 3-engine architecture (SQLite/Qdrant/Neo4j), new modules (`_sqlite_store.py`, `_qdrant_store.py`, `_neo4j_store.py`, `_embedder.py`, `_linker.py`, `_forgetting.py`, `_sync.py`, `_query.py`), public API (`store_memory`, `query`, `pin_memory`), query routing rules |
| **AGENTS.md** | Add memory briefing for subagents: "Before execution call `get_context_for_agent()`. After execution call `store_memory()` for any learned facts, procedures, or notable outcomes. Never query SQLite/Qdrant/Neo4j directly — always go through MemoryManager." |
| **CLAUDE.md** (root) | Add to Module Context table: `shared/memory_layer/CLAUDE.md — 3-engine memory: SQLite (truth) + Qdrant (vectors) + Neo4j (graph)` |
| **jobpulse/CLAUDE.md** | Add memory integration section: which agents record memories (form_experience → episodic, screening_answers cache → procedural, scan_learning → semantic), how `post_apply_hook` feeds the memory pipeline |
| **patterns/CLAUDE.md** | Add memory integration section: how patterns call `get_context_for_agent()` before each agent node, how `learn_from_success()` fires at convergence/finish for scores >= 7.0 |
| **.claude/rules/shared.md** | Add rule: "All memory access goes through MemoryManager — never query SQLite/Qdrant/Neo4j directly. Same principle as get_llm() for LLM calls." |
| **shared/memory_layer/CLAUDE.md** | New file — module-specific docs for the memory layer: architecture overview, engine roles, file map, query routing, lifecycle stages, how to add new memory tiers |

### Why This Matters

Claude Code subagents get `AGENTS.md` auto-injected. If it doesn't mention the memory system, subagents will:
- Not read from memory before execution (missing valuable context)
- Not write to memory after execution (losing learnings)
- Potentially bypass MemoryManager and query engines directly (breaking the abstraction)

The documentation IS the agent's understanding of the system. No docs = no usage.

---

## Testing Strategy

### Test Infrastructure

All tests use isolated backends — **never** touch production `data/agent_memory/`:
- SQLite: `:memory:` or `tmp_path / "test.db"`
- Qdrant: `qdrant_client.QdrantClient(location=":memory:")`
- Neo4j: `testcontainers-neo4j` (spins up disposable Docker container per test session) OR mock via `unittest.mock` for unit tests that don't need real graph traversal
- Embeddings: mock `VoyageEmbedder` returns deterministic 1024-dim vectors (hash-based) so similarity tests are reproducible

### Test Files

```
tests/shared/memory_layer/
  test_sqlite_store.py        # SQLite backend CRUD + schema
  test_qdrant_store.py        # Qdrant vector search + filtering
  test_neo4j_store.py         # Neo4j graph ops + traversal
  test_embedder.py            # Voyage + MiniLM fallback
  test_linker.py              # Autonomous linking pipeline
  test_forgetting.py          # Decay, promotion, tombstoning, revival
  test_query_router.py        # Route selection per query type
  test_sync.py                # 3-engine reconciliation
  test_manager.py             # MemoryManager public API
  test_integration.py         # Full pipeline end-to-end
  test_degradation.py         # Engine failure graceful fallback
  test_backwards_compat.py    # Old API still works
  conftest.py                 # Shared fixtures
```

### Fixtures (conftest.py)

```python
@pytest.fixture
def sqlite_store(tmp_path):
    """Fresh SQLite backend per test."""
    return SQLiteStore(str(tmp_path / "test_memories.db"))

@pytest.fixture
def qdrant_store():
    """In-memory Qdrant per test."""
    return QdrantStore(location=":memory:")

@pytest.fixture
def neo4j_store():
    """Testcontainer Neo4j per test session (shared, cleaned between tests)."""
    # Uses testcontainers-neo4j — real Neo4j, disposable
    ...

@pytest.fixture
def mock_embedder():
    """Deterministic embedder: same text → same vector (hash-based)."""
    return MockEmbedder(dims=1024)

@pytest.fixture
def memory_manager(tmp_path, qdrant_store, neo4j_store, mock_embedder):
    """Fully wired MemoryManager with all 3 engines."""
    return MemoryManager(
        storage_dir=str(tmp_path),
        qdrant=qdrant_store,
        neo4j=neo4j_store,
        embedder=mock_embedder,
    )
```

---

### 1. SQLite Store Tests (test_sqlite_store.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_insert_and_retrieve` | Basic CRUD works | Insert entry, retrieve by ID, fields match |
| `test_insert_creates_all_indexes` | Schema is complete | Query `sqlite_master` for all 4 indexes |
| `test_tier_views_filter_correctly` | Views exclude tombstoned | Insert 3 entries (1 tombstoned), view returns 2 |
| `test_domain_filter` | Domain queries use index | Insert 10 entries across 3 domains, query 1 domain, get correct subset |
| `test_lifecycle_filter` | Lifecycle stages queryable | Insert STM + MTM + LTM entries, filter by lifecycle |
| `test_decay_score_ordering` | Decay-ordered retrieval | Insert 5 entries with different decay scores, retrieve ordered desc |
| `test_tombstone_soft_delete` | Tombstoned entries invisible to views | Tombstone entry, verify absent from views, present in raw table |
| `test_payload_json_roundtrip` | Tier-specific payload survives storage | Insert with nested dict payload, retrieve, verify identical |
| `test_update_access_metadata` | Access tracking works | Retrieve entry, verify `last_accessed` updated and `access_count` incremented |
| `test_concurrent_writes` | Thread safety | 10 threads each insert 10 entries simultaneously, verify all 100 present |

### 2. Qdrant Store Tests (test_qdrant_store.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_upsert_and_search` | Basic vector search works | Upsert 5 vectors, search with query vector, top result is most similar |
| `test_collection_per_tier` | Tier isolation | Insert into episodic collection, search procedural collection, get 0 results |
| `test_filtered_search_by_domain` | Payload filtering | Insert 10 vectors (5 domain A, 5 domain B), search with domain=A filter, get only A results |
| `test_filtered_search_by_score` | Score floor filtering | Insert vectors with scores 3.0-9.0, search with min_score=7.0, only high-scorers returned |
| `test_filtered_search_by_lifecycle` | Lifecycle filtering | Insert STM + Cold vectors, search with lifecycle=STM filter, Cold excluded |
| `test_similarity_ordering` | Results ranked by similarity | Insert 3 vectors at known distances, verify order matches expected similarity |
| `test_cross_tier_search` | Cross-collection search | Insert related content in episodic + procedural, cross-tier search finds both |
| `test_delete_by_id` | Tombstone propagation | Upsert vector, delete it, search returns 0 |
| `test_cosine_threshold` | Low-similarity excluded | Insert distant vector, search with threshold=0.75, not returned |
| `test_10k_vectors_under_5ms` | Performance at scale | Insert 10,000 vectors, search completes in <5ms |

### 3. Neo4j Store Tests (test_neo4j_store.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_create_node_and_retrieve` | Basic node CRUD | Create Memory node, retrieve by memory_id, properties match |
| `test_create_edge` | Edge creation | Create 2 nodes + SIMILAR_TO edge, verify edge exists with properties |
| `test_graph_expand_1_hop` | 1-hop expansion | Create A→B→C chain, expand from A depth=1, get {A, B} |
| `test_graph_expand_2_hops` | 2-hop expansion | Create A→B→C chain, expand from A depth=2, get {A, B, C} |
| `test_graph_expand_excludes_forgotten` | Forgotten nodes skipped | Create A→B→C, mark B as FORGOTTEN, expand from A depth=2, get {A} (C unreachable) |
| `test_domain_neighbors` | Domain-scoped traversal | Create 5 nodes (3 domain=greenhouse, 2 domain=workday), domain query for greenhouse returns 3 |
| `test_degree_count` | Connectivity signal | Create hub node with 7 edges, verify degree() returns 7 |
| `test_downstream_scores` | Impact signal | Create A→(TAUGHT)→B (score 8.0), A→(TAUGHT)→C (score 6.0), avg_downstream_score(A) = 7.0 |
| `test_count_similar` | Uniqueness signal | Create 4 nodes with SIMILAR_TO edges to target, count_similar returns 4 |
| `test_platform_node_linking` | APPLIES_TO edges | Create procedure + Platform node, link with APPLIES_TO, traversal from platform finds procedure |
| `test_contradicts_edge` | Contradiction edges | Create 2 semantic facts + CONTRADICTS edge, verify edge has similarity property |
| `test_batch_edge_creation` | Bulk edge insert | Create 20 nodes, batch-create 15 edges in one call, all edges exist |

### 4. Embedder Tests (test_embedder.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_voyage_embed_returns_1024_dims` | Correct vector size | Embed "test text", verify len == 1024 |
| `test_same_text_same_vector` | Deterministic embeddings | Embed same text twice, vectors are identical |
| `test_similar_text_high_cosine` | Semantic similarity | Embed "greenhouse form filling" and "filling greenhouse application forms", cosine > 0.8 |
| `test_different_text_low_cosine` | Semantic dissimilarity | Embed "greenhouse form" and "budget categorization", cosine < 0.5 |
| `test_fallback_to_minilm_on_api_failure` | Graceful degradation | Mock Voyage API to raise ConnectionError, verify MiniLM fallback produces 384-dim vector |
| `test_fallback_logs_warning` | Degradation is observable | Trigger fallback, verify warning logged |
| `test_batch_embed` | Multiple texts in one call | Embed 10 texts, get 10 vectors, all 1024 dims |

### 5. Autonomous Linker Tests (test_linker.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_similar_same_tier_creates_similar_to` | Rule: same tier + high similarity → SIMILAR_TO | Insert 2 similar episodic memories, verify SIMILAR_TO edge created |
| `test_episode_to_fact_creates_produced` | Rule: episodic → semantic = PRODUCED | Insert episode + semantic fact (same domain), verify PRODUCED edge |
| `test_episode_to_procedure_creates_taught` | Rule: episodic → procedural = TAUGHT | Insert episode + procedure (same domain), verify TAUGHT edge |
| `test_experience_to_episode_creates_extracted_from` | Rule: experience → episodic = EXTRACTED_FROM | Insert GRPO experience + episode, verify EXTRACTED_FROM edge |
| `test_contradicting_facts_creates_contradicts` | Rule: conflicting semantic facts → CONTRADICTS | Insert "Workday has 5 pages" then "Workday has 3 pages", verify CONTRADICTS edge |
| `test_higher_score_procedure_creates_supersedes` | Rule: better procedure → SUPERSEDES | Insert old procedure (score 6), new procedure (score 9, similar), verify SUPERSEDES |
| `test_cross_tier_creates_related_to` | Rule: cross-tier relevance → RELATED_TO | Insert episodic + experience with high similarity, different domains, verify RELATED_TO |
| `test_low_similarity_no_edge` | No edge for distant memories | Insert 2 unrelated memories (cosine < 0.5), verify 0 edges |
| `test_llm_fallback_for_ambiguous_pairs` | LLM classification fires | Insert 2 memories where rules return None but similarity > 0.70, mock LLM, verify it was called |
| `test_linking_is_idempotent` | No duplicate edges | Run linker twice on same memory, verify edge count is 1 not 2 |
| `test_contradiction_handler_decays_confidence` | Contradiction resolution | Create contradicting fact, verify loser's confidence dropped by 0.2 |
| `test_contradiction_tombstones_weak_fact` | Contradiction kills weak facts | Create fact with confidence 0.15, contradict it, verify tombstoned |

### 6. Forgetting Engine Tests (test_forgetting.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_decay_score_fresh_memory_is_1` | New memory starts at max | Create memory, compute decay, score ~= 1.0 |
| `test_decay_score_drops_over_time` | Time decay works | Create memory, advance clock 48h (mock), decay < 0.5 |
| `test_access_count_increases_stability` | Frequent access resists decay | Create memory accessed 10 times, advance clock 48h, decay > 0.7 (vs 0.5 for single access) |
| `test_quality_signal_boosts_decay` | High score decays slower | Create two memories (score 9.0 vs 3.0), advance clock 24h, high-scorer has higher decay |
| `test_connectivity_signal_from_neo4j` | Hub nodes resist decay | Create memory with 6 Neo4j edges, verify connectivity signal = 1.0 |
| `test_uniqueness_signal_last_survivor` | Only memory in cluster protected | Create 1 memory in domain, verify uniqueness = 1.0 |
| `test_uniqueness_signal_redundant` | Redundant memories can decay | Create 4 similar memories, verify uniqueness = 0.3 for each |
| `test_impact_signal_from_descendants` | Valuable children protect parent | Create episode → procedure (score 9.0), verify episode's impact signal > 0.8 |
| `test_stm_tombstoned_below_threshold` | STM forgotten when decay < 0.3 | Create STM memory, advance clock until decay < 0.3, run sweep, verify tombstoned |
| `test_mtm_tombstoned_below_threshold` | MTM harder to forget | Create MTM memory, verify it survives at decay 0.2 (STM would die), dies at 0.1 |
| `test_ltm_only_dies_by_contradiction` | LTM protected | Create LTM memory, set decay to 0.05, run sweep, verify NOT tombstoned |
| `test_promotion_stm_to_mtm` | Heat-based promotion works | Create STM memory, access 3 times within 24h, run sweep, verify lifecycle=MTM |
| `test_promotion_mtm_to_ltm` | Validation-based promotion | Create MTM memory, access 10 times + validate 5 times, run sweep, verify lifecycle=LTM |
| `test_demotion_ltm_to_cold` | Cold demotion works | Create unprotected LTM memory, set decay < 0.1, run sweep, verify lifecycle=COLD |
| `test_cold_removes_from_qdrant` | Qdrant cleanup on demotion | Demote to Cold, verify Qdrant collection no longer has this vector |
| `test_cold_keeps_neo4j_node` | Neo4j preserved on demotion | Demote to Cold, verify Neo4j node exists with :COLD label |
| `test_protection_pinned_never_forgotten` | PINNED immunity | Pin memory, set decay to 0.0, run sweep, verify NOT tombstoned |
| `test_protection_last_survivor` | Last-survivor immunity | Create 1 memory in domain, set decay to 0.01, run sweep, verify NOT tombstoned (PROTECTED) |
| `test_protection_hub_node` | Hub node elevated | Create memory with 6 edges, verify threshold halved (ELEVATED) |
| `test_revival_similar_tombstoned` | Revival mechanism | Tombstone memory, create similar new memory (cosine > 0.85), verify old memory revived, new not created |
| `test_revival_boosts_stability` | Revived memory harder to forget | Revive memory, verify stability multiplied by 2.0 |
| `test_revival_window_expired` | No revival after 30 days | Tombstone memory, advance clock 31 days, create similar, verify new memory created (no revival) |
| `test_sweep_runs_promotion_and_demotion` | Full sweep cycle | Create mix of memories at various states, run sweep, verify promotions + demotions + tombstones all happened |

### 7. Query Router Tests (test_query_router.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_exact_lookup_routes_to_sqlite` | ID query → SQLite only | Query with memory_id, verify plan has engines=[SQLITE] |
| `test_semantic_query_routes_to_qdrant` | Similarity → Qdrant | Query with semantic_query, verify plan starts with VECTOR_SEARCH |
| `test_graph_depth_adds_neo4j` | Graph expansion → Neo4j | Query with semantic_query + graph_depth=2, verify plan includes GRAPH_EXPAND |
| `test_domain_only_routes_to_neo4j` | Domain dump → Neo4j | Query with domain only, verify plan uses DOMAIN_CLUSTER |
| `test_fallback_to_sqlite_when_qdrant_down` | Degradation | Mark Qdrant unavailable, semantic query, verify plan falls back to SQLite FTS |
| `test_fallback_skips_graph_when_neo4j_down` | Degradation | Mark Neo4j unavailable, combined query, verify plan skips GRAPH_EXPAND |
| `test_min_decay_score_filter_applied` | Quality floor | Query with min_decay_score=0.3, verify only hot+warm memories returned |
| `test_tier_filter_applied` | Tier scoping | Query with tiers=[SEMANTIC], verify only semantic facts returned |

### 8. Sync Service Tests (test_sync.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_reconcile_backfills_qdrant` | Startup reconciliation | Insert 5 entries in SQLite (no Qdrant), run reconcile, verify all 5 in Qdrant |
| `test_reconcile_backfills_neo4j` | Startup reconciliation | Insert 5 entries in SQLite (no Neo4j nodes), run reconcile, verify all 5 nodes created |
| `test_reconcile_skips_already_synced` | No duplicate syncing | Insert entry in all 3, run reconcile, verify no duplicate vectors or nodes |
| `test_tombstone_propagation` | Tombstone syncs to all engines | Tombstone in SQLite, run propagation, verify deleted from Qdrant + marked FORGOTTEN in Neo4j |
| `test_queue_processes_async` | Non-blocking writes | Queue 10 vector upserts, verify they complete without blocking caller |

### 9. MemoryManager Integration Tests (test_manager.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_store_memory_writes_to_all_engines` | Full write pipeline | Store memory, verify present in SQLite AND Qdrant AND Neo4j |
| `test_get_context_for_agent_returns_rich_context` | End-to-end retrieval | Store 10 memories, call get_context_for_agent, verify output contains graph-expanded cluster |
| `test_get_context_different_agents_get_different_slices` | Role-based memory | Store episodic + procedural + semantic, verify researcher gets episodic+semantic, writer gets procedural |
| `test_pin_memory_prevents_forgetting` | Pin → protected | Pin memory, run forgetting sweep with zero decay, verify memory survives |
| `test_health_reports_all_engines` | Health monitoring | Call health(), verify sqlite/qdrant/neo4j status fields present |
| `test_startup_reconciles_engines` | Startup flow | Pre-populate SQLite with unsynced entries, call startup(), verify Qdrant+Neo4j backfilled |

### 10. Full Pipeline End-to-End Tests (test_integration.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_full_lifecycle_stm_to_ltm` | Complete promotion chain | Store memory → access 3x (promotes to MTM) → access 7 more (promotes to LTM) → verify lifecycle=LTM in all engines |
| `test_full_lifecycle_ltm_to_cold_to_archive` | Complete demotion chain | Create LTM memory → advance clock (decay drops) → sweep (demotes to Cold) → verify removed from Qdrant, still in Neo4j → advance more → sweep (Archive) |
| `test_autonomous_linking_on_write` | Write triggers linking | Store episodic memory, then store related semantic fact, verify PRODUCED edge auto-created in Neo4j |
| `test_contradiction_resolves_across_engines` | Contradiction propagates | Store fact A, store contradicting fact B, verify A's confidence dropped in SQLite, CONTRADICTS edge in Neo4j |
| `test_graph_expanded_retrieval_finds_hidden_connections` | Graph adds value over flat search | Store 5 memories where only 2 are semantically similar to query, but 3 others are connected via graph edges → combined query returns all 5, plain vector search returns only 2 |
| `test_revival_after_forgetting` | Forget-and-relearn cycle | Store memory → advance clock → sweep tombstones it → store similar memory → verify old revived with 2x stability |
| `test_agent_context_enriched_by_graph` | Real agent workflow | Store 20 diverse memories about "Greenhouse", call get_context_for_agent("researcher", "Greenhouse application"), verify context contains episodic + semantic + procedural from graph cluster |
| `test_10_agents_concurrent_memory_access` | Concurrency | 10 threads simultaneously store + retrieve memories, verify no data corruption, no duplicate edges |

### 11. Graceful Degradation Tests (test_degradation.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_qdrant_down_semantic_search_falls_back` | Vector search degrades | Kill Qdrant, semantic query still returns results (from SQLite FTS) |
| `test_neo4j_down_graph_expansion_skipped` | Graph expansion degrades | Kill Neo4j, combined query returns vector results only (no expansion) |
| `test_both_down_sqlite_only` | Double failure | Kill Qdrant + Neo4j, queries still work via SQLite |
| `test_voyage_api_down_minilm_fallback` | Embedding degrades | Mock Voyage failure, verify MiniLM produces embeddings, write pipeline completes |
| `test_degraded_mode_logged` | Observability | Trigger each degradation, verify warning logged with engine name |
| `test_recovery_after_engine_restart` | Self-healing | Kill Qdrant, store 5 memories (SQLite only), restart Qdrant, run reconcile, verify all 5 backfilled |

### 12. Backwards Compatibility Tests (test_backwards_compat.py)

| Test | What it verifies | Assert |
|------|-----------------|--------|
| `test_old_record_episode_still_works` | Legacy API compat | Call `memory_manager.record_episode(...)`, verify entry in all 3 engines |
| `test_old_learn_fact_still_works` | Legacy API compat | Call `memory_manager.learn_fact(...)`, verify semantic entry stored |
| `test_old_learn_procedure_still_works` | Legacy API compat | Call `memory_manager.learn_procedure(...)`, verify procedural entry stored |
| `test_old_search_patterns_still_works` | Legacy API compat | Call `memory_manager.search_patterns(...)`, verify returns tuple |
| `test_old_get_context_for_agent_same_signature` | API signature unchanged | Call with (agent_name, topic, domain), no TypeError |
| `test_old_get_memory_report_includes_lifecycle` | Report upgraded | Call `get_memory_report()`, verify output includes lifecycle stage counts |
| `test_migration_from_json_to_sqlite` | Data migration | Create old-format JSON files, start MemoryManager, verify all data migrated to SQLite |

---

### Test Counts

| File | Tests | Type |
|------|-------|------|
| test_sqlite_store.py | 10 | Unit |
| test_qdrant_store.py | 10 | Unit |
| test_neo4j_store.py | 12 | Unit (mock) or Integration (testcontainer) |
| test_embedder.py | 7 | Unit |
| test_linker.py | 12 | Integration |
| test_forgetting.py | 22 | Unit + Integration |
| test_query_router.py | 8 | Unit |
| test_sync.py | 5 | Integration |
| test_manager.py | 6 | Integration |
| test_integration.py | 8 | End-to-end |
| test_degradation.py | 6 | Integration |
| test_backwards_compat.py | 7 | Integration |
| **Total** | **113** | |

All tests must pass before the implementation is considered complete. Tests are part of the implementation plan, not an afterthought.

---

## Success Criteria

1. `get_context_for_agent()` returns richer context (graph-expanded clusters) with same API
2. Semantic search finds related memories that keyword search misses
3. Forgetting sweep keeps memory lean without deleting valuable knowledge
4. Autonomous linking creates correct edges without human intervention
5. System degrades gracefully when Qdrant or Neo4j is unavailable
6. All existing tests pass without modification
7. Production swap requires only env var changes
8. All agent-facing docs updated — a new subagent spawned after this change naturally reads from and writes to memory without special instructions
9. `AGENTS.md` memory briefing causes subagents to follow the read→execute→write→link lifecycle
