# Semantic Search Upgrade — 4-Phase Pipeline Enhancement

**Date:** 2026-04-05
**Status:** Approved (research completed, user approved)
**Scope:** `shared/hybrid_search.py`, `shared/code_intelligence.py`, `shared/code_graph.py`

## Problem

Current `semantic_search` uses **bag-of-words hashing (512d)** + FTS5 BM25 via RRF. Meanwhile, **Voyage Code 3 embeddings (1024d) are already computed and stored** in the `embeddings` table but never queried. The search pipeline is leaving 25-40% NDCG improvement on the table.

## Current Architecture

```
Query → FTS5 BM25 (top-30) ─┐
                              ├─ RRF (k=60) → top-10 results
Query → bag-of-words (512d)──┘
```

- `HybridSearch._compute_embedding()` — hash-based bag-of-words, 512 dims, no semantic understanding
- `embeddings` table — Voyage Code 3 vectors (1024d, packed BLOBs), computed but unused
- No reranking, no graph signals, unweighted RRF

## Target Architecture

```
Query
  ↓
┌──────────────────────────────────────────────┐
│ Stage 1: Parallel Retrieval (top-30 each)    │
├──────────┬────────────┬──────────────────────┤
│ FTS5     │ Voyage     │ (Graph proximity     │
│ BM25     │ Code 3     │  if context_qname)   │
│ (free)   │ cosine     │                      │
│          │ (stored)   │                      │
└────┬─────┴─────┬──────┴──────────┬───────────┘
     ▼           ▼                 ▼
┌──────────────────────────────────────��───────┐
│ Stage 2: Weighted RRF Fusion                 │
│ FTS weight=1.3, Vector=1.0                   │
│ k=60, merge → top-20 candidates              │
└─────────────────────┬────────────────────────┘
                      ▼
┌──��──────────────────────────────────���────────┐
│ Stage 3: Graph Boost (post-RRF multiplier)   │
│ • Direct caller/callee: 1.5x                 │
│ • Same file: 1.3x                            │
│ • Same community (Leiden): 1.2x              │
│ • High fan-in (top 10%): 1.25x               │
│ • PageRank: 1 + pagerank * 0.5               │
│ • Test file: 0.7x                            │
│ • Risk: contextual (review/security only)     │
└─────────────────────┬──────���─────────────────┘
                      ▼
┌─��──────────────────────────────��─────────────┐
│ Stage 4: Cross-Encoder Rerank (top-20→top-5) │
│ MiniLM-L-12-v2 via sentence-transformers     │
│ ~40ms on M-series, local, free               │
└───��──────────────────────────────────────────┘
```

## Phase 1: Activate Voyage Embeddings (+15-25% NDCG)

**What:** Replace `HybridSearch._compute_embedding()` bag-of-words with Voyage Code 3 vectors from the `embeddings` table.

**Changes:**
- `shared/hybrid_search.py` — `_compute_embedding()` queries `embeddings` table by doc_id, unpacks BLOB to float array. Falls back to bag-of-words if no embedding found.
- `shared/hybrid_search.py` — `_vector_search()` computes query embedding via Voyage API at search time (single call, ~100ms). Cache recent query embeddings in an LRU dict (max 100).
- `shared/hybrid_search.py` — cosine similarity between query vector (1024d) and stored document vectors.
- Add `weighted_rrf()` — FTS weight=1.3, vector weight=1.0 (exact identifiers matter more in code).

**Query-time Voyage call:** One API call per search query to embed the query string (~$0.00002). Alternatively, if query is short code identifier, use FTS-only fast path.

**Fallback:** If `VOYAGE_API_KEY` not set, degrade to FTS-only search (no vector signal). Never fail the search.

**Cost:** ~$0.50/month at 1000 queries/day. Index already paid for.

## Phase 2: Cross-Encoder Reranker (+10-15% NDCG)

**What:** Add `cross-encoder/ms-marco-MiniLM-L-12-v2` as a rerank step after RRF fusion.

**Changes:**
- New file: `shared/reranker.py`
  - `Reranker` class with lazy model loading (first call downloads 130MB model)
  - `rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]`
  - Uses `sentence-transformers` CrossEncoder
  - Singleton pattern — load once, reuse
- `shared/hybrid_search.py` — after RRF, pass top-20 to `Reranker.rerank()`, return top-5
- `requirements.txt` — add `sentence-transformers>=3.0`

**Latency:** ~40-70ms on M-series for 20 candidates. Total search: ~150ms (acceptable for MCP tool).

**Fallback:** If `sentence-transformers` not installed, skip reranking, return RRF results directly. Import guarded with try/except.

## Phase 3: Graph-Boosted Scoring (+3-6% NDCG)

**What:** Pre-compute graph signals at index time, apply as post-RRF boost multiplier at search time.

### 3a: Schema additions to `nodes` table

```sql
ALTER TABLE nodes ADD COLUMN pagerank REAL DEFAULT 0.0;
ALTER TABLE nodes ADD COLUMN community_id INTEGER;
ALTER TABLE nodes ADD COLUMN fan_in INTEGER DEFAULT 0;
ALTER TABLE nodes ADD COLUMN fan_out INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_nodes_community ON nodes(community_id);
CREATE INDEX IF NOT EXISTS idx_nodes_pagerank ON nodes(pagerank DESC);
```

Note: `risk_score` column already exists on nodes.

### 3b: Index-time computation (in `CodeGraph` or `CodeIntelligence`)

**PageRank:** 15 iterations in Python. For ~4300 nodes, <100ms. Store in `nodes.pagerank`.

**Leiden communities:** Via `leidenalg` package (wraps igraph). Build igraph from edges table, run Leiden, store `community_id` on each node. <50ms. Add `leidenalg` and `igraph` to requirements.txt. Fallback: if not installed, skip community detection (community_id stays NULL, boost is 1.0x).

**Fan-in/fan-out:** Already partially computed in `compute_risk_score`. Extract into dedicated columns for search-time lookup without recomputation.

### 3c: Search-time boost function

```python
def compute_graph_boost(node, context_qname=None, search_context="general"):
    boost = 1.0

    # Graph proximity (if searching from a specific function)
    if context_qname:
        distance = call_distance(context_qname, node.qname)  # BFS recursive CTE
        if distance == 1: boost *= 1.5
        elif distance == 2: boost *= 1.2
        elif distance == 3: boost *= 1.1

        if same_community(context_qname, node.qname):
            boost *= 1.2

    # Static signals
    if node.is_test: boost *= 0.7
    if node.file_path and '/test' not in node.file_path:
        boost *= 1.0 + (node.pagerank * 0.5)  # PageRank continuous boost
    if node.fan_in > p90_fan_in: boost *= 1.25

    # Risk (contextual only)
    if search_context in ("review", "security", "impact"):
        boost *= 1.0 + (node.risk_score * 0.5)

    return boost
```

**Call distance:** BFS via recursive CTE, max depth 3. Efficient with existing indexes.

### 3d: MCP tool changes

`semantic_search` MCP tool gains optional parameters:
- `context_symbol` (str) — qualified name of the function/class being worked on (enables proximity boost)
- `search_context` (str) — "general" | "review" | "security" | "impact" (controls risk boost)

Default behavior unchanged (no context = no graph proximity boost, general context = no risk boost).

### 3e: Incremental reindex — graph signals stay fresh

Graph-global signals (PageRank, Leiden communities, fan-in/fan-out) must recompute after any file change because one file's edge changes ripple through the graph.

**Trigger:** `reindex-file.py` PostToolUse hook already re-indexes AST nodes/edges/Voyage embeddings for the changed file. After that single-file reindex, also recompute:
1. Fan-in/fan-out counts (SQL aggregation, <10ms)
2. PageRank (15 iterations, <100ms for ~4300 nodes)
3. Leiden communities (<50ms)

**Total overhead:** ~150ms per file edit — cheap enough to run on every Edit/Write hook.

**Full reindex:** `CodeIntelligence.index_directory()` already handles full reindex. Phase 3 adds graph signal computation as a final step after full indexing.

**File deletion:** When a file is re-indexed and its functions no longer exist in AST, their nodes/edges are removed. Next graph-global recomputation picks up the change automatically.

## Phase 4: Weighted RRF Tuning (+1-3% NDCG)

Already included in Phase 1. FTS weight=1.3, vector weight=1.0. k=60 (default, well-supported for code search).

## Dependencies

| Package | Version | Phase | Required? |
|---------|---------|-------|-----------|
| `voyageai` | >=0.3 | 1 | Already installed |
| `sentence-transformers` | >=3.0 | 2 | Optional (graceful fallback) |
| `leidenalg` | >=0.10 | 3 | Optional (graceful fallback) |
| `python-igraph` | >=0.11 | 3 | Optional (required by leidenalg) |

All new dependencies are optional with graceful degradation.

## Testing Strategy

- Unit tests for each phase in `tests/shared/test_hybrid_search.py` and `tests/shared/test_reranker.py`
- All tests use `:memory:` SQLite or `tmp_path` — never touch production DBs
- Test fallback paths (no Voyage key, no sentence-transformers, no leidenalg)
- Benchmark: run same 10 queries before/after each phase, log NDCG@5 and latency

## Performance Budget

| Stage | Latency | Notes |
|-------|---------|-------|
| FTS5 BM25 | <5ms | Already fast |
| Voyage cosine similarity | <10ms | Pre-computed vectors, just dot products |
| RRF fusion | <1ms | Simple arithmetic |
| Graph boost | <5ms | Indexed column lookups + optional BFS CTE |
| Cross-encoder rerank | 40-70ms | MiniLM-L-12 on M-series, 20 candidates |
| **Total** | **~60-90ms** | Acceptable for MCP tool call |

## Files Modified

| File | Change |
|------|--------|
| `shared/hybrid_search.py` | Voyage vectors, weighted RRF, graph boost integration |
| `shared/reranker.py` | **New** — CrossEncoder reranker with lazy loading |
| `shared/code_graph.py` | PageRank, Leiden community, fan_in/fan_out columns |
| `shared/code_intelligence.py` | Wire graph boost into semantic_search, new MCP params |
| `shared/code_intel_mcp.py` | Add context_symbol, search_context params to semantic_search tool |
| `requirements.txt` | sentence-transformers, leidenalg, python-igraph (optional) |
| `tests/shared/test_hybrid_search.py` | Voyage vector search, weighted RRF, graph boost tests |
| `tests/shared/test_reranker.py` | **New** — reranker unit tests |

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Voyage API down at query time | Cache query embeddings (LRU 100). FTS-only fallback. |
| MiniLM model download fails | Skip reranking, return RRF results. Import guarded. |
| leidenalg install fails (C extension) | Skip community detection. community_id stays NULL, boost=1.0x. |
| Graph boost over-promotes high-fan-in utility functions | Test file dampening (0.7x) + PageRank uses undirected edges (Sourcegraph finding). |
| Latency exceeds 100ms | All stages have fast paths. Reranker is the bottleneck; can reduce N from 20 to 10. |

## Success Criteria

1. Voyage embeddings actively used in search (not bag-of-words)
2. Search returns more relevant results for natural language queries (qualitative)
3. Total latency < 100ms for typical queries
4. All existing tests pass
5. Graceful degradation when optional dependencies missing
