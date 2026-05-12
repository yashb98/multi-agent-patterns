# Memory Layer — 5-Tier / 3-Engine Architecture

## Architecture
Two perspectives on the memory system:

**5 Memory Tiers** (what gets stored):
- Working/Short-Term (`_stores.py`) — transient conversation context
- Episodic (`_stores.py`) — past interaction episodes
- Semantic (`_stores.py`) — factual knowledge
- Procedural (`_stores.py`) — learned procedures/workflows
- Pattern (`_pattern.py`) — reusable solution patterns (hybrid search)

**3 Storage Engines** (where it's stored):
- SQLite (`_sqlite_store.py`) — source of truth, structured data
- Qdrant (`_qdrant_store.py`) — vector similarity search
- Neo4j (`_neo4j_store.py`) — graph relationships

**Routing**: `_router.py` (TieredRouter) — 3-tier: cached → lightweight → full agent
**Data Models**: `_entries.py` — ShortTermEntry, EpisodicEntry, SemanticEntry, ProceduralEntry, PatternEntry

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

## Modules
- `_sqlite_store.py` — Source of truth CRUD, WAL mode, thread-safe
- `_qdrant_store.py` — Filtered HNSW vector search, one collection per tier
- `_neo4j_store.py` — Graph traversal, signals (degree, downstream score, similarity count)
- `_embedder.py` — BGE-M3 via local Ollama (1024 dims) + MiniLM fallback (384 dims)
- `_linker.py` — Autonomous graph linking (A-MEM), 7-rule relationship classification
- `_forgetting.py` — 6-signal decay + lifecycle promotion/demotion
- `_query.py` — QueryRouter picks engine(s) per query type
- `_sync.py` — 3-engine reconciliation + tombstone propagation
- `_entries.py` — Dataclasses: ShortTermEntry, EpisodicEntry, SemanticEntry, ProceduralEntry, PatternEntry
- `_stores.py` — Tier stores: ShortTermMemory, EpisodicMemory, SemanticMemory, ProceduralMemory
- `_pattern.py` — PatternMemory (hybrid search for reusable solutions)
- `_router.py` — TieredRouter (3-tier routing: cached → lightweight → full agent)
- `_manager.py` — MemoryManager facade (single entry point)

## Rules
- ALL memory access through MemoryManager — never query engines directly. Cognitive consumers use `get_procedural_entries` / `get_episodic_entries` / `get_semantic_entries`; pre-S7 these read JSON-capped stores while writes went to SQLite, but as of S7 reads are SQLite-first with JSON fallback (`pipeline-bugs.md` M-11.C / W-11.5).
- Embeddings via BGE-M3 over local Ollama (fallback: MiniLM)
- Lifecycle: STM → MTM → LTM → Cold → Archive
- Forgetting sweep runs hourly — 6-signal decay score. The 3 graph signals (`connectivity` / `impact` / `uniqueness`) require Neo4j edges; `SyncService._sync_entry` now invokes `AutonomousLinker.link_with_neighbors` after every secondary-sync write to populate them (pipeline-bugs.md S6).
- Tests use tmp_path for SQLite, MagicMock for Qdrant/Neo4j
