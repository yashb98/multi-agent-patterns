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

## Testing Strategy

- All tests use in-memory/tmp_path backends: SQLite `:memory:`, Qdrant in-memory mode, Neo4j testcontainers or mock
- Never touch production `data/agent_memory/` from tests
- Test each engine independently + integration test for full pipeline
- Test graceful degradation: Qdrant down, Neo4j down, both down
- Test forgetting: decay computation, promotion, tombstoning, revival
- Test autonomous linking: rule-based classification, cross-tier discovery
- Test contradiction: conflicting facts, confidence decay, tombstoning

---

## Success Criteria

1. `get_context_for_agent()` returns richer context (graph-expanded clusters) with same API
2. Semantic search finds related memories that keyword search misses
3. Forgetting sweep keeps memory lean without deleting valuable knowledge
4. Autonomous linking creates correct edges without human intervention
5. System degrades gracefully when Qdrant or Neo4j is unavailable
6. All existing tests pass without modification
7. Production swap requires only env var changes
