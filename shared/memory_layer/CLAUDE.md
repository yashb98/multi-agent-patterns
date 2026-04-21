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

## Modules
- `_sqlite_store.py` — Source of truth CRUD, WAL mode, thread-safe
- `_qdrant_store.py` — Filtered HNSW vector search, one collection per tier
- `_neo4j_store.py` — Graph traversal, signals (degree, downstream score, similarity count)
- `_embedder.py` — Voyage 3 Large (1024 dims) + MiniLM fallback (384 dims)
- `_linker.py` — Autonomous graph linking (A-MEM), 7-rule relationship classification
- `_forgetting.py` — 6-signal decay + lifecycle promotion/demotion
- `_query.py` — QueryRouter picks engine(s) per query type
- `_sync.py` — 3-engine reconciliation + tombstone propagation
- `_manager.py` — MemoryManager facade (single entry point)

## Rules
- ALL memory access through MemoryManager — never query engines directly
- Embeddings via Voyage 3 Large (fallback: MiniLM)
- Lifecycle: STM → MTM → LTM → Cold → Archive
- Forgetting sweep runs hourly — 6-signal decay score
- Tests use tmp_path for SQLite, MagicMock for Qdrant/Neo4j
