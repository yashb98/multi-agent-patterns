# Semantic Search Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade CodeGraph MCP semantic search from bag-of-words to Voyage Code 3 embeddings + cross-encoder reranker + graph-boosted scoring, yielding +25-40% NDCG improvement.

**Architecture:** 4-stage pipeline: FTS5 BM25 + Voyage cosine (weighted RRF) -> graph boost multiplier -> MiniLM cross-encoder rerank. All new dependencies optional with graceful fallback.

**Tech Stack:** Voyage Code 3 (1024d embeddings), sentence-transformers (MiniLM-L-12 cross-encoder), leidenalg (community detection), SQLite (existing)

**Spec:** `docs/superpowers/specs/2026-04-05-semantic-search-upgrade-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `shared/hybrid_search.py` | Modify | Voyage vector search, weighted RRF, graph boost integration |
| `shared/reranker.py` | Create | Cross-encoder reranker with lazy model loading |
| `shared/code_intelligence.py` | Modify | Graph signal computation (PageRank, Leiden, fan-in), wire into search |
| `shared/code_graph.py` | Modify | Add pagerank, community_id, fan_in, fan_out columns + computation |
| `shared/code_intel_mcp.py` | Modify | Add context_symbol, search_context params to semantic_search tool |
| `tests/test_hybrid_search.py` | Modify | Voyage vector, weighted RRF, graph boost tests |
| `tests/test_reranker.py` | Create | Reranker unit tests |
| `tests/test_graph_signals.py` | Create | PageRank, Leiden, fan-in computation tests |

---

### Task 1: Voyage Vector Search in HybridSearch

**Files:**
- Modify: `shared/hybrid_search.py`
- Modify: `tests/test_hybrid_search.py`

Replace bag-of-words `_vector_search` with Voyage Code 3 embeddings from the `embeddings` table. The `embeddings` table stores pre-computed 1024d vectors as packed float BLOBs. At query time, embed the query via Voyage API, then compute cosine similarity against stored vectors.

- [ ] **Step 1: Write failing test for Voyage vector search**

Add to `tests/test_hybrid_search.py`:

```python
class TestVoyageVectorSearch:
    """Tests for Voyage Code 3 vector search integration."""

    def test_voyage_vector_search_uses_embeddings_table(self):
        """When embeddings table has vectors, _vector_search uses them."""
        import struct
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)

        # Add documents
        search.add("doc_auth", "JWT authentication token verification")
        search.add("doc_api", "REST API endpoint design patterns")

        # Manually insert Voyage-style packed embeddings into embeddings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL
            )
        """)
        # Create simple 4d vectors for testing (real ones are 1024d)
        vec_auth = struct.pack("4f", 0.9, 0.1, 0.0, 0.0)  # auth-like
        vec_api = struct.pack("4f", 0.0, 0.0, 0.9, 0.1)   # api-like
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_auth", vec_auth))
        conn.execute("INSERT INTO embeddings (doc_id, vector) VALUES (?, ?)", ("doc_api", vec_api))
        conn.commit()

        # Query with a vector similar to auth
        query_vec = [0.8, 0.2, 0.0, 0.0]
        results = search._voyage_vector_search(query_vec, limit=5)

        assert len(results) >= 1
        # doc_auth should rank higher (more similar to query)
        ids = [doc_id for doc_id, _ in results]
        assert ids[0] == "doc_auth"
        search.close()

    def test_voyage_vector_search_falls_back_to_bow(self):
        """When no embeddings table, falls back to bag-of-words."""
        search = HybridSearch(":memory:")
        search.add("doc1", "test document about authentication")
        # No embeddings table exists — should use bag-of-words
        results = search._vector_search("authentication", limit=5)
        assert len(results) >= 1
        search.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hybrid_search.py::TestVoyageVectorSearch -v`
Expected: FAIL with `AttributeError: 'HybridSearch' object has no attribute '_voyage_vector_search'`

- [ ] **Step 3: Implement Voyage vector search in HybridSearch**

In `shared/hybrid_search.py`, add after the `_vector_search` method:

```python
def _voyage_vector_search(self, query_vector: list[float], limit: int = 30) -> list[tuple]:
    """Cosine similarity against pre-computed Voyage embeddings.

    Args:
        query_vector: Pre-embedded query vector (1024d from Voyage API).
        limit: Max results to return.

    Returns: [(doc_id, rank), ...] sorted by similarity descending.
    """
    import struct

    rows = self.conn.execute(
        "SELECT doc_id, vector FROM embeddings"
    ).fetchall()

    if not rows:
        return []

    scored = []
    for row in rows:
        doc_id = row["doc_id"] if isinstance(row, sqlite3.Row) else row[0]
        blob = row["vector"] if isinstance(row, sqlite3.Row) else row[1]
        n_floats = len(blob) // 4
        doc_vec = list(struct.unpack(f"{n_floats}f", blob))
        sim = self._cosine_similarity(query_vector, doc_vec)
        scored.append((doc_id, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [(doc_id, i + 1) for i, (doc_id, _) in enumerate(scored[:limit])]
```

Then update `_vector_search` to try Voyage first:

```python
def _vector_search(self, query: str, limit: int = 30) -> list[tuple]:
    """Cosine similarity search. Tries Voyage embeddings first, falls back to bag-of-words."""
    # Check if embeddings table exists and has data
    try:
        count = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    except Exception:
        count = 0

    if count > 0 and self._query_embedding_fn is not None:
        # Use Voyage embeddings
        query_vec = self._query_embedding_fn(query)
        if query_vec:
            return self._voyage_vector_search(query_vec, limit)

    # Fallback: bag-of-words
    query_emb = self._compute_embedding(query)
    rows = self.conn.execute(
        "SELECT id, embedding FROM documents WHERE embedding IS NOT NULL"
    ).fetchall()

    scored = []
    for row in rows:
        doc_emb = [float(x) for x in row["embedding"].split(",")]
        sim = self._cosine_similarity(query_emb, doc_emb)
        scored.append((row["id"], sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [(doc_id, i + 1) for i, (doc_id, _) in enumerate(scored[:limit])]
```

Add `_query_embedding_fn` to `__init__`:

```python
def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None):
    if conn is not None:
        self.conn = conn
    else:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
    self._query_embedding_fn = None  # Set by CodeIntelligence to use Voyage
    self._init_schema()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_hybrid_search.py::TestVoyageVectorSearch -v`
Expected: PASS

- [ ] **Step 5: Run full hybrid search test suite**

Run: `python -m pytest tests/test_hybrid_search.py -v`
Expected: All existing tests still PASS (bag-of-words fallback preserved)

- [ ] **Step 6: Commit**

```bash
git add shared/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat(search): add Voyage vector search with bag-of-words fallback"
```

---

### Task 2: Weighted RRF

**Files:**
- Modify: `shared/hybrid_search.py`
- Modify: `tests/test_hybrid_search.py`

Add per-signal weights to RRF fusion. FTS weight=1.3 (exact identifiers matter more in code), vector weight=1.0.

- [ ] **Step 1: Write failing test for weighted RRF**

Add to `tests/test_hybrid_search.py`:

```python
class TestWeightedRRF:
    def test_weighted_rrf_fts_boost(self):
        """FTS weight > 1.0 should boost FTS-only matches over vector-only."""
        s = HybridSearch(":memory:")
        fts = [("doc_fts", 1)]       # Only in FTS
        vec = [("doc_vec", 1)]       # Only in vector
        # With fts_weight=1.3, FTS rank-1 should score higher than vec rank-1
        merged = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.3, vec_weight=1.0)
        s.close()

        scores = {doc_id: score for doc_id, score, _, _ in merged}
        assert scores["doc_fts"] > scores["doc_vec"]

    def test_weighted_rrf_equal_weights_matches_original(self):
        """With equal weights, should match original RRF behavior."""
        s = HybridSearch(":memory:")
        fts = [("doc_a", 1), ("doc_b", 2)]
        vec = [("doc_b", 1), ("doc_c", 2)]
        original = s._rrf_merge(fts, vec, top_k=10)
        weighted = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.0, vec_weight=1.0)
        s.close()

        orig_scores = {d: s for d, s, _, _ in original}
        weighted_scores = {d: s for d, s, _, _ in weighted}
        for doc_id in orig_scores:
            assert abs(orig_scores[doc_id] - weighted_scores[doc_id]) < 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hybrid_search.py::TestWeightedRRF -v`
Expected: FAIL with `TypeError: _rrf_merge() got an unexpected keyword argument 'fts_weight'`

- [ ] **Step 3: Update _rrf_merge to accept weights**

In `shared/hybrid_search.py`, update `_rrf_merge`:

```python
def _rrf_merge(
    self,
    fts_results: list[tuple],
    vec_results: list[tuple],
    top_k: int,
    fts_weight: float = 1.0,
    vec_weight: float = 1.0,
) -> list[tuple]:
    """Reciprocal Rank Fusion with per-signal weights.

    RRF_score(d) = sum(weight_i / (k + rank_i)) for each ranker i

    Returns: [(doc_id, rrf_score, fts_rank, vec_rank), ...]
    """
    fts_ranks = {doc_id: rank for doc_id, rank in fts_results}
    vec_ranks = {doc_id: rank for doc_id, rank in vec_results}

    all_ids = set(fts_ranks.keys()) | set(vec_ranks.keys())

    scored = []
    for doc_id in all_ids:
        fts_rank = fts_ranks.get(doc_id, 999)
        vec_rank = vec_ranks.get(doc_id, 999)

        rrf_score = 0.0
        if fts_rank < 999:
            rrf_score += fts_weight / (RRF_K + fts_rank)
        if vec_rank < 999:
            rrf_score += vec_weight / (RRF_K + vec_rank)

        scored.append((doc_id, rrf_score, fts_rank, vec_rank))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
```

Update `query()` to pass weights:

```python
# In query(), replace:
merged = self._rrf_merge(fts_results, vec_results, top_k)
# With:
merged = self._rrf_merge(fts_results, vec_results, top_k,
                          fts_weight=self.fts_weight, vec_weight=self.vec_weight)
```

Add weight attributes to `__init__`:

```python
self.fts_weight = 1.3  # Exact identifiers matter more in code search
self.vec_weight = 1.0
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_hybrid_search.py -v`
Expected: All PASS including new weighted RRF tests. Existing `test_rrf_score_formula` needs update since default weight is now 1.3.

- [ ] **Step 5: Fix existing RRF test for new default weight**

Update `test_rrf_score_formula` in `TestRRFMerge`:

```python
def test_rrf_score_formula(self):
    """Verify RRF score formula: weight/(k+rank) for each ranker."""
    s = HybridSearch(":memory:")
    fts = [("doc_a", 1), ("doc_b", 2)]
    vec = [("doc_b", 1), ("doc_c", 2)]
    # Use equal weights to test formula cleanly
    merged = s._rrf_merge(fts, vec, top_k=10, fts_weight=1.0, vec_weight=1.0)
    s.close()

    scores = {doc_id: score for doc_id, score, _, _ in merged}
    assert scores["doc_b"] > scores["doc_a"]
    assert scores["doc_a"] > scores["doc_c"]

    expected_b = 1.0 / (RRF_K + 2) + 1.0 / (RRF_K + 1)
    assert abs(scores["doc_b"] - expected_b) < 1e-9
```

- [ ] **Step 6: Run full test suite and commit**

Run: `python -m pytest tests/test_hybrid_search.py -v`
Expected: All PASS

```bash
git add shared/hybrid_search.py tests/test_hybrid_search.py
git commit -m "feat(search): add weighted RRF (FTS=1.3, vector=1.0)"
```

---

### Task 3: Wire Voyage Query Embedding in CodeIntelligence

**Files:**
- Modify: `shared/code_intelligence.py`
- Modify: `tests/test_code_intelligence.py`

Connect the Voyage API client in CodeIntelligence to HybridSearch's `_query_embedding_fn` so search queries are embedded via Voyage Code 3.

- [ ] **Step 1: Write failing test**

Add to `tests/test_code_intelligence.py`:

```python
class TestVoyageSearchIntegration:
    def test_query_embedding_fn_set_when_voyage_available(self, tmp_path, monkeypatch):
        """CodeIntelligence sets _query_embedding_fn on HybridSearch when Voyage key exists."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key-fake")
        db_path = str(tmp_path / "ci_test.db")
        ci = CodeIntelligence(db_path)
        # The fn should be set (even though it won't work with a fake key)
        assert ci._search._query_embedding_fn is not None
        ci.close()

    def test_query_embedding_fn_none_without_key(self, tmp_path, monkeypatch):
        """Without VOYAGE_API_KEY, _query_embedding_fn stays None."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        db_path = str(tmp_path / "ci_test.db")
        ci = CodeIntelligence(db_path)
        assert ci._search._query_embedding_fn is None
        ci.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_code_intelligence.py::TestVoyageSearchIntegration -v`
Expected: FAIL

- [ ] **Step 3: Implement query embedding wiring**

In `shared/code_intelligence.py`, in `__init__`, after creating `self._search`:

```python
# Wire Voyage query embedding into HybridSearch
self._query_embedding_cache: dict[str, list[float]] = {}
if os.environ.get(EMBEDDING_ENV_VAR):
    self._search._query_embedding_fn = self._embed_query
```

Add the `_embed_query` method:

```python
def _embed_query(self, query: str) -> list[float] | None:
    """Embed a search query via Voyage Code 3. LRU cache (max 100)."""
    if query in self._query_embedding_cache:
        return self._query_embedding_cache[query]

    client = self._get_voyage_client()
    if client is None:
        return None

    try:
        result = client.embed([query], model=EMBEDDING_MODEL, input_type="query")
        vector = result.embeddings[0]
        # LRU eviction
        if len(self._query_embedding_cache) >= 100:
            oldest_key = next(iter(self._query_embedding_cache))
            del self._query_embedding_cache[oldest_key]
        self._query_embedding_cache[query] = vector
        return vector
    except Exception as exc:
        logger.warning("Voyage query embedding failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_code_intelligence.py::TestVoyageSearchIntegration -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/test_code_intelligence.py tests/test_hybrid_search.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add shared/code_intelligence.py tests/test_code_intelligence.py
git commit -m "feat(search): wire Voyage query embedding into HybridSearch"
```

---

### Task 4: Cross-Encoder Reranker

**Files:**
- Create: `shared/reranker.py`
- Create: `tests/test_reranker.py`
- Modify: `shared/hybrid_search.py`

Add a cross-encoder reranker that runs after RRF fusion. Uses `cross-encoder/ms-marco-MiniLM-L-12-v2` via sentence-transformers. Graceful fallback if not installed.

- [ ] **Step 1: Write failing tests**

Create `tests/test_reranker.py`:

```python
"""Tests for shared/reranker.py — cross-encoder reranking."""

import pytest


class TestRerankerImport:
    def test_reranker_importable(self):
        from shared.reranker import Reranker
        assert Reranker is not None


class TestRerankerFallback:
    def test_rerank_returns_input_when_no_model(self, monkeypatch):
        """When sentence-transformers is not available, return input unchanged."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "sentence_transformers" in name:
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": "a", "text": "hello", "score": 0.5},
            {"id": "b", "text": "world", "score": 0.8},
        ]
        result = r.rerank("test query", candidates, top_k=2)
        # Should return candidates unchanged (sorted by original score)
        assert len(result) == 2


class TestRerankerScoring:
    def test_rerank_reorders_candidates(self):
        """Reranker should reorder candidates by cross-encoder score."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": "irrelevant", "text": "cooking recipes for pasta", "score": 0.9},
            {"id": "relevant", "text": "JWT authentication with refresh tokens", "score": 0.1},
        ]
        result = r.rerank("authentication tokens", candidates, top_k=2)
        # Cross-encoder should rank "relevant" higher despite lower original score
        assert result[0]["id"] == "relevant"

    def test_rerank_top_k_limits_output(self):
        """top_k should limit number of returned results."""
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            pytest.skip("sentence-transformers not installed")

        from shared.reranker import Reranker
        r = Reranker()
        candidates = [
            {"id": f"doc_{i}", "text": f"Document {i} about topic", "score": 0.5}
            for i in range(10)
        ]
        result = r.rerank("topic", candidates, top_k=3)
        assert len(result) <= 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reranker.py::TestRerankerImport -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared.reranker'`

- [ ] **Step 3: Implement reranker**

Create `shared/reranker.py`:

```python
"""Cross-encoder reranker for search results.

Uses sentence-transformers CrossEncoder (ms-marco-MiniLM-L-12-v2) to rerank
candidates after RRF fusion. Gracefully degrades if not installed.

Usage:
    from shared.reranker import rerank_results
    reranked = rerank_results("auth tokens", candidates, top_k=5)
"""

from shared.logging_config import get_logger

logger = get_logger(__name__)

_cross_encoder = None
_import_failed = False


def _get_cross_encoder():
    """Lazy-load the cross-encoder model. Returns None if unavailable."""
    global _cross_encoder, _import_failed

    if _cross_encoder is not None:
        return _cross_encoder
    if _import_failed:
        return None

    try:
        from sentence_transformers import CrossEncoder

        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-12-v2")
        logger.info("Cross-encoder reranker loaded (ms-marco-MiniLM-L-12-v2)")
        return _cross_encoder
    except ImportError:
        _import_failed = True
        logger.info("sentence-transformers not installed — reranking disabled")
        return None
    except Exception as exc:
        _import_failed = True
        logger.warning("Cross-encoder load failed: %s", exc)
        return None


class Reranker:
    """Cross-encoder reranker with graceful fallback."""

    def rerank(
        self,
        query: str,
        candidates: list[dict],
        top_k: int = 5,
    ) -> list[dict]:
        """Rerank candidates using cross-encoder.

        Args:
            query: Search query string.
            candidates: List of dicts with at least "text" and "score" keys.
            top_k: Number of results to return.

        Returns:
            Reranked list of candidate dicts, with "rerank_score" added.
        """
        if not candidates:
            return []

        model = _get_cross_encoder()
        if model is None:
            # Fallback: return top_k by original score
            sorted_cands = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
            return sorted_cands[:top_k]

        pairs = [(query, c.get("text", "")) for c in candidates]
        try:
            scores = model.predict(pairs)
        except Exception as exc:
            logger.warning("Cross-encoder predict failed: %s", exc)
            sorted_cands = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
            return sorted_cands[:top_k]

        for cand, score in zip(candidates, scores):
            cand["rerank_score"] = float(score)

        reranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return reranked[:top_k]


def rerank_results(
    query: str,
    candidates: list[dict],
    top_k: int = 5,
) -> list[dict]:
    """Module-level convenience function."""
    return Reranker().rerank(query, candidates, top_k)
```

- [ ] **Step 4: Run reranker tests**

Run: `python -m pytest tests/test_reranker.py -v`
Expected: Import and fallback tests PASS. Scoring tests PASS if sentence-transformers installed, SKIP otherwise.

- [ ] **Step 5: Integrate reranker into HybridSearch.query()**

In `shared/hybrid_search.py`, update the `query` method to accept and use a reranker:

Add to `__init__`:

```python
self._reranker = None  # Set externally to enable reranking
```

Update `query()` — after building the `results` list, add before `return results`:

```python
# Rerank if reranker is configured
if self._reranker is not None and len(results) > 1:
    try:
        results = self._reranker.rerank(query_text, results, top_k=top_k)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("Reranking failed: %s", exc)
```

- [ ] **Step 6: Run full test suite and commit**

Run: `python -m pytest tests/test_hybrid_search.py tests/test_reranker.py -v`
Expected: All PASS

```bash
git add shared/reranker.py tests/test_reranker.py shared/hybrid_search.py
git commit -m "feat(search): add cross-encoder reranker with graceful fallback"
```

---

### Task 5: Graph Signal Schema + Computation

**Files:**
- Modify: `shared/code_graph.py`
- Modify: `shared/code_intelligence.py`
- Create: `tests/test_graph_signals.py`

Add `pagerank`, `community_id`, `fan_in`, `fan_out` columns to the `nodes` table. Compute PageRank (15 iterations) and Leiden communities at index time.

- [ ] **Step 1: Write failing tests for graph signals**

Create `tests/test_graph_signals.py`:

```python
"""Tests for graph signal computation — PageRank, Leiden communities, fan-in/fan-out."""

import sqlite3
import pytest

from shared.code_graph import CodeGraph


@pytest.fixture
def graph():
    g = CodeGraph(":memory:")
    # Build a small call graph:
    # A -> B -> C
    # A -> C
    # D -> B
    for name in ["A", "B", "C", "D"]:
        g.conn.execute(
            "INSERT INTO nodes (kind, name, qualified_name, file_path) VALUES (?, ?, ?, ?)",
            ("function", name, f"test.py::{name}", "test.py"),
        )
    for src, tgt in [("A", "B"), ("A", "C"), ("B", "C"), ("D", "B")]:
        g.conn.execute(
            "INSERT INTO edges (kind, source_qname, target_qname, file_path) VALUES (?, ?, ?, ?)",
            ("calls", f"test.py::{src}", f"test.py::{tgt}", "test.py"),
        )
    g.conn.commit()
    yield g


class TestFanInFanOut:
    def test_compute_fan_in_out(self, graph):
        graph.compute_fan_in_out()
        rows = {
            r[0]: (r[1], r[2])
            for r in graph.conn.execute(
                "SELECT name, fan_in, fan_out FROM nodes"
            ).fetchall()
        }
        # B is called by A and D → fan_in=2
        assert rows["B"][0] == 2
        # C is called by A and B → fan_in=2
        assert rows["C"][0] == 2
        # A calls B and C → fan_out=2
        assert rows["A"][1] == 2
        # D calls B → fan_out=1
        assert rows["D"][1] == 1


class TestPageRank:
    def test_compute_pagerank_runs(self, graph):
        graph.compute_pagerank()
        rows = graph.conn.execute(
            "SELECT name, pagerank FROM nodes ORDER BY pagerank DESC"
        ).fetchall()
        # All nodes should have pagerank > 0
        for name, pr in rows:
            assert pr > 0, f"{name} has pagerank=0"
        # B and C (most called) should have highest pagerank
        top_names = [r[0] for r in rows[:2]]
        assert "B" in top_names or "C" in top_names

    def test_pagerank_sums_to_approximately_one(self, graph):
        graph.compute_pagerank()
        total = graph.conn.execute("SELECT SUM(pagerank) FROM nodes").fetchone()[0]
        assert abs(total - 1.0) < 0.01


class TestCommunityDetection:
    def test_compute_communities_assigns_ids(self, graph):
        graph.compute_communities()
        rows = graph.conn.execute(
            "SELECT name, community_id FROM nodes"
        ).fetchall()
        for name, cid in rows:
            assert cid is not None, f"{name} has no community_id"

    def test_compute_communities_fallback_without_leidenalg(self, graph, monkeypatch):
        """Without leidenalg, falls back to file-based communities."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "leidenalg" in name or "igraph" in name:
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        graph.compute_communities()
        rows = graph.conn.execute(
            "SELECT name, community_id FROM nodes"
        ).fetchall()
        # Fallback assigns community by file_path hash
        for name, cid in rows:
            assert cid is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_graph_signals.py -v`
Expected: FAIL — `fan_in`, `fan_out`, `pagerank`, `community_id` columns don't exist, methods don't exist.

- [ ] **Step 3: Add schema columns to CodeGraph**

In `shared/code_graph.py`, update `_init_schema` to add new columns:

```python
# After the main schema creation, add:
existing = {r[1] for r in self.conn.execute("PRAGMA table_info(nodes)").fetchall()}
if "pagerank" not in existing:
    self.conn.execute("ALTER TABLE nodes ADD COLUMN pagerank REAL DEFAULT 0.0")
if "community_id" not in existing:
    self.conn.execute("ALTER TABLE nodes ADD COLUMN community_id INTEGER")
if "fan_in" not in existing:
    self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_in INTEGER DEFAULT 0")
if "fan_out" not in existing:
    self.conn.execute("ALTER TABLE nodes ADD COLUMN fan_out INTEGER DEFAULT 0")

self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_community ON nodes(community_id)")
self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_pagerank ON nodes(pagerank DESC)")
self.conn.commit()
```

- [ ] **Step 4: Implement compute_fan_in_out**

In `shared/code_graph.py`, add to `CodeGraph`:

```python
def compute_fan_in_out(self) -> None:
    """Compute and cache fan-in/fan-out counts for all nodes."""
    # Fan-in: how many callers per target
    self.conn.execute("UPDATE nodes SET fan_in = 0, fan_out = 0")
    self.conn.execute("""
        UPDATE nodes SET fan_in = (
            SELECT COUNT(*) FROM edges
            WHERE edges.target_qname = nodes.qualified_name AND edges.kind = 'calls'
        )
    """)
    self.conn.execute("""
        UPDATE nodes SET fan_out = (
            SELECT COUNT(*) FROM edges
            WHERE edges.source_qname = nodes.qualified_name AND edges.kind = 'calls'
        )
    """)
    self.conn.commit()
```

- [ ] **Step 5: Implement compute_pagerank**

In `shared/code_graph.py`, add to `CodeGraph`:

```python
def compute_pagerank(self, iterations: int = 15, damping: float = 0.85) -> None:
    """Compute PageRank over the call graph. Undirected edges (Sourcegraph finding)."""
    nodes = self.conn.execute("SELECT qualified_name FROM nodes").fetchall()
    if not nodes:
        return

    qnames = [r[0] for r in nodes]
    n = len(qnames)
    rank = {q: 1.0 / n for q in qnames}

    # Build adjacency (undirected — both directions count)
    neighbors: dict[str, list[str]] = {q: [] for q in qnames}
    edges = self.conn.execute(
        "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
    ).fetchall()
    for src, tgt in edges:
        if src in neighbors:
            neighbors[src].append(tgt)
        if tgt in neighbors:
            neighbors[tgt].append(src)

    degree = {q: len(neighbors[q]) for q in qnames}

    for _ in range(iterations):
        new_rank = {}
        for q in qnames:
            s = sum(rank.get(nb, 0) / max(degree.get(nb, 1), 1) for nb in neighbors[q])
            new_rank[q] = (1 - damping) / n + damping * s
        rank = new_rank

    updates = [(rank[q], q) for q in qnames]
    self.conn.executemany("UPDATE nodes SET pagerank = ? WHERE qualified_name = ?", updates)
    self.conn.commit()
```

- [ ] **Step 6: Implement compute_communities**

In `shared/code_graph.py`, add to `CodeGraph`:

```python
def compute_communities(self) -> None:
    """Compute Leiden communities. Falls back to file-based grouping."""
    nodes = self.conn.execute("SELECT qualified_name, file_path FROM nodes").fetchall()
    if not nodes:
        return

    qnames = [r[0] for r in nodes]
    file_paths = {r[0]: r[1] for r in nodes}

    try:
        import igraph as ig
        import leidenalg

        # Build igraph
        qname_to_idx = {q: i for i, q in enumerate(qnames)}
        g = ig.Graph(n=len(qnames), directed=False)

        edges_data = self.conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind = 'calls'"
        ).fetchall()
        ig_edges = []
        for src, tgt in edges_data:
            if src in qname_to_idx and tgt in qname_to_idx:
                ig_edges.append((qname_to_idx[src], qname_to_idx[tgt]))
        if ig_edges:
            g.add_edges(ig_edges)

        partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
        updates = [(partition.membership[i], q) for i, q in enumerate(qnames)]

    except ImportError:
        logger.info("leidenalg/igraph not installed — using file-based communities")
        # Fallback: group by file_path hash
        file_to_id: dict[str, int] = {}
        counter = 0
        updates = []
        for q in qnames:
            fp = file_paths.get(q, "unknown")
            if fp not in file_to_id:
                file_to_id[fp] = counter
                counter += 1
            updates.append((file_to_id[fp], q))

    self.conn.executemany("UPDATE nodes SET community_id = ? WHERE qualified_name = ?", updates)
    self.conn.commit()
```

- [ ] **Step 7: Run graph signal tests**

Run: `python -m pytest tests/test_graph_signals.py -v`
Expected: All PASS (community tests may use fallback if leidenalg not installed)

- [ ] **Step 8: Commit**

```bash
git add shared/code_graph.py tests/test_graph_signals.py
git commit -m "feat(graph): add PageRank, Leiden communities, fan-in/fan-out computation"
```

---

### Task 6: Compute Graph Signals at Index Time

**Files:**
- Modify: `shared/code_intelligence.py`

Wire graph signal computation into `index_directory()` and `reindex_file()`.

- [ ] **Step 1: Add Phase 5 to index_directory**

In `shared/code_intelligence.py`, update `index_directory()`:

```python
def index_directory(self, root: str) -> dict[str, Any]:
    t0 = time.monotonic()
    root_path = Path(root)

    # Phase 1 — Python AST
    self._index_python_files(root_path)

    # Phase 2 — risk scores
    self._cache_risk_scores()

    # Phase 3 — text files
    self._index_text_files(root_path)

    # Phase 4 — search index
    self._populate_search_index()

    # Phase 5 — graph signals (PageRank, communities, fan-in/fan-out)
    self._compute_graph_signals()

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    # ... rest unchanged
```

Add the method:

```python
def _compute_graph_signals(self) -> None:
    """Compute graph-global signals: fan-in/fan-out, PageRank, communities."""
    try:
        self._graph.compute_fan_in_out()
        self._graph.compute_pagerank()
        self._graph.compute_communities()
        logger.info("Graph signals computed (fan-in, PageRank, communities)")
    except Exception as exc:
        logger.warning("Graph signal computation failed: %s", exc)
```

- [ ] **Step 2: Add graph signal recomputation to reindex_file**

In `reindex_file()`, after step 10 (updating search index), add:

```python
# 11. Recompute graph-global signals (PageRank, communities affected by edge changes)
self._compute_graph_signals()
```

Update the step 11 comment and return to be step 12.

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/test_code_intelligence.py tests/test_graph_signals.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add shared/code_intelligence.py
git commit -m "feat(search): compute graph signals at index and reindex time"
```

---

### Task 7: Graph Boost in Search

**Files:**
- Modify: `shared/hybrid_search.py`
- Modify: `shared/code_intelligence.py`
- Modify: `tests/test_hybrid_search.py`

Apply graph boost multipliers to search results after RRF fusion.

- [ ] **Step 1: Write failing test**

Add to `tests/test_hybrid_search.py`:

```python
class TestGraphBoost:
    def test_graph_boost_promotes_high_fan_in(self):
        """Nodes with high fan-in should get boosted."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        search = HybridSearch(conn=conn)

        # Create nodes table with fan-in data
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                qualified_name TEXT PRIMARY KEY,
                fan_in INTEGER DEFAULT 0,
                fan_out INTEGER DEFAULT 0,
                pagerank REAL DEFAULT 0.0,
                community_id INTEGER,
                is_test INTEGER DEFAULT 0,
                risk_score REAL DEFAULT 0.0,
                file_path TEXT DEFAULT ''
            )
        """)
        conn.execute("INSERT INTO nodes VALUES ('high_fan', 20, 2, 0.3, 1, 0, 0.1, 'src/core.py')")
        conn.execute("INSERT INTO nodes VALUES ('low_fan', 1, 2, 0.01, 2, 0, 0.1, 'src/util.py')")
        conn.commit()

        from shared.hybrid_search import compute_graph_boost
        boost_high = compute_graph_boost(conn, "high_fan")
        boost_low = compute_graph_boost(conn, "low_fan")
        assert boost_high > boost_low

    def test_graph_boost_dampens_test_files(self):
        """Test files should get dampened (0.7x)."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                qualified_name TEXT PRIMARY KEY,
                fan_in INTEGER DEFAULT 0,
                fan_out INTEGER DEFAULT 0,
                pagerank REAL DEFAULT 0.0,
                community_id INTEGER,
                is_test INTEGER DEFAULT 0,
                risk_score REAL DEFAULT 0.0,
                file_path TEXT DEFAULT ''
            )
        """)
        conn.execute("INSERT INTO nodes VALUES ('prod_fn', 5, 2, 0.1, 1, 0, 0.1, 'src/auth.py')")
        conn.execute("INSERT INTO nodes VALUES ('test_fn', 5, 2, 0.1, 1, 1, 0.1, 'tests/test_auth.py')")
        conn.commit()

        from shared.hybrid_search import compute_graph_boost
        boost_prod = compute_graph_boost(conn, "prod_fn")
        boost_test = compute_graph_boost(conn, "test_fn")
        assert boost_test < boost_prod
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_hybrid_search.py::TestGraphBoost -v`
Expected: FAIL with `ImportError: cannot import name 'compute_graph_boost'`

- [ ] **Step 3: Implement compute_graph_boost**

In `shared/hybrid_search.py`, add module-level function:

```python
def compute_graph_boost(
    conn: sqlite3.Connection,
    qualified_name: str,
    context_qname: str | None = None,
    search_context: str = "general",
) -> float:
    """Compute graph-based boost multiplier for a search result.

    Args:
        conn: SQLite connection with nodes/edges tables.
        qualified_name: The node's qualified name.
        context_qname: Optional symbol being worked on (enables proximity boost).
        search_context: "general", "review", "security", or "impact".

    Returns:
        Multiplicative boost factor (1.0 = no change).
    """
    row = conn.execute(
        "SELECT fan_in, pagerank, is_test, risk_score, community_id "
        "FROM nodes WHERE qualified_name = ?",
        (qualified_name,),
    ).fetchone()

    if row is None:
        return 1.0

    fan_in = row[0] or 0
    pagerank = row[1] or 0.0
    is_test = row[2] or 0
    risk_score = row[3] or 0.0
    community_id = row[4]

    boost = 1.0

    # Test file dampening
    if is_test:
        boost *= 0.7

    # PageRank boost (continuous)
    boost *= 1.0 + (pagerank * 0.5)

    # Fan-in boost (top 10% heuristic: fan_in >= 5)
    if fan_in >= 5:
        boost *= 1.25

    # Context-based proximity
    if context_qname and context_qname != qualified_name:
        # Check same community
        ctx_row = conn.execute(
            "SELECT community_id FROM nodes WHERE qualified_name = ?",
            (context_qname,),
        ).fetchone()
        if ctx_row and ctx_row[0] is not None and ctx_row[0] == community_id:
            boost *= 1.2

        # Check direct caller/callee (1-hop)
        direct = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE "
            "(source_qname = ? AND target_qname = ?) OR "
            "(source_qname = ? AND target_qname = ?)",
            (context_qname, qualified_name, qualified_name, context_qname),
        ).fetchone()[0]
        if direct > 0:
            boost *= 1.5

    # Risk boost (contextual)
    if search_context in ("review", "security", "impact"):
        boost *= 1.0 + (risk_score * 0.5)

    return boost
```

- [ ] **Step 4: Run graph boost tests**

Run: `python -m pytest tests/test_hybrid_search.py::TestGraphBoost -v`
Expected: PASS

- [ ] **Step 5: Wire graph boost into CodeIntelligence.semantic_search**

In `shared/code_intelligence.py`, update `semantic_search`:

```python
def semantic_search(
    self,
    query: str,
    top_k: int = 10,
    context_symbol: str | None = None,
    search_context: str = "general",
) -> list[dict[str, Any]]:
    """Hybrid FTS5 + vector semantic search with graph boosting."""
    from shared.hybrid_search import compute_graph_boost

    raw = self._search.query(query, top_k=top_k * 2)  # Over-fetch for graph boost reranking
    results: list[dict[str, Any]] = []
    for item in raw:
        metadata = item.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        qname = item.get("id", "")
        base_score = item.get("score", 0.0)

        # Apply graph boost
        boost = compute_graph_boost(
            self.conn, qname,
            context_qname=context_symbol,
            search_context=search_context,
        )

        results.append({
            "name": qname,
            "file": metadata.get("file_path", ""),
            "score": base_score * boost,
            "snippet": (item.get("text") or "")[:200],
        })

    # Re-sort by boosted score and limit
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]
```

- [ ] **Step 6: Run full test suite and commit**

Run: `python -m pytest tests/test_hybrid_search.py tests/test_code_intelligence.py tests/test_graph_signals.py -v`
Expected: All PASS

```bash
git add shared/hybrid_search.py shared/code_intelligence.py tests/test_hybrid_search.py
git commit -m "feat(search): add graph-boosted scoring (PageRank, fan-in, community, test dampening)"
```

---

### Task 8: Update MCP Tool Schema

**Files:**
- Modify: `shared/code_intel_mcp.py`

Add `context_symbol` and `search_context` parameters to the `semantic_search` MCP tool.

- [ ] **Step 1: Update tool definition**

In `shared/code_intel_mcp.py`, update the semantic_search entry in `_TOOL_DEFS`:

```python
{
    "name": "semantic_search",
    "description": (
        "Hybrid FTS5 + Voyage Code 3 semantic search with graph-boosted scoring. "
        "Returns matching nodes with name, file path, score, and snippet. "
        "Optionally provide context_symbol for proximity-aware ranking."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language or code query"},
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 10)",
                "default": 10,
            },
            "context_symbol": {
                "type": "string",
                "description": "Qualified name of the symbol being worked on (enables proximity boost)",
            },
            "search_context": {
                "type": "string",
                "enum": ["general", "review", "security", "impact"],
                "description": "Search context for risk boosting (default: general)",
                "default": "general",
            },
        },
        "required": ["query"],
    },
},
```

- [ ] **Step 2: Update dispatch**

In `_dispatch`, update the semantic_search case:

```python
elif name == "semantic_search":
    return ci.semantic_search(
        args["query"],
        top_k=args.get("top_k", 10),
        context_symbol=args.get("context_symbol"),
        search_context=args.get("search_context", "general"),
    )
```

- [ ] **Step 3: Run MCP tests**

Run: `python -m pytest tests/test_code_intel_mcp.py -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add shared/code_intel_mcp.py
git commit -m "feat(mcp): add context_symbol and search_context params to semantic_search"
```

---

### Task 9: Wire Reranker in CodeIntelligence

**Files:**
- Modify: `shared/code_intelligence.py`

Connect the reranker to HybridSearch so it runs after RRF + graph boost.

- [ ] **Step 1: Wire reranker in __init__**

In `shared/code_intelligence.py`, in `__init__`, after creating `self._search`:

```python
# Wire reranker (optional — graceful fallback if sentence-transformers not installed)
try:
    from shared.reranker import Reranker
    self._search._reranker = Reranker()
except Exception:
    pass  # Reranking disabled
```

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v -k "hybrid_search or code_intel or reranker or graph_signal" --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add shared/code_intelligence.py
git commit -m "feat(search): wire cross-encoder reranker into search pipeline"
```

---

### Task 10: Install Dependencies + Integration Test

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add optional dependencies to requirements.txt**

Add these lines (check they don't already exist):

```
# Semantic search upgrades (optional — graceful fallback)
sentence-transformers>=3.0
leidenalg>=0.10
python-igraph>=0.11
```

- [ ] **Step 2: Install**

Run: `pip install sentence-transformers leidenalg python-igraph`

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v -k "hybrid_search or code_intel or reranker or graph_signal" --tb=short`
Expected: All PASS including reranker scoring tests (no longer skipped)

- [ ] **Step 4: Integration smoke test**

Run: `python -c "from shared.code_intelligence import CodeIntelligence; ci = CodeIntelligence('data/code_intelligence.db'); print(ci.semantic_search('authentication token')); ci.close()"`
Expected: Returns search results with scores

- [ ] **Step 5: Commit everything**

```bash
git add requirements.txt
git commit -m "chore: add sentence-transformers, leidenalg, igraph to requirements"
```

---

### Task 11: Reindex + Verify

**Files:** None (operational task)

- [ ] **Step 1: Full reindex with new pipeline**

Run: `python -c "from shared.code_intelligence import CodeIntelligence; ci = CodeIntelligence('data/code_intelligence.db'); ci.index_directory('.'); ci.close()"`

- [ ] **Step 2: Verify graph signals populated**

Run: `python -c "
import sqlite3
conn = sqlite3.connect('data/code_intelligence.db')
print('Nodes with pagerank > 0:', conn.execute('SELECT COUNT(*) FROM nodes WHERE pagerank > 0').fetchone()[0])
print('Nodes with community_id:', conn.execute('SELECT COUNT(*) FROM nodes WHERE community_id IS NOT NULL').fetchone()[0])
print('Nodes with fan_in > 0:', conn.execute('SELECT COUNT(*) FROM nodes WHERE fan_in > 0').fetchone()[0])
print('Embeddings:', conn.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0])
print('Top PageRank:', conn.execute('SELECT name, pagerank FROM nodes ORDER BY pagerank DESC LIMIT 5').fetchall())
conn.close()
"`

Expected: All counts > 0, embeddings count matches documents count.

- [ ] **Step 3: Test semantic search with context**

Run: `python -c "
from shared.code_intelligence import CodeIntelligence
ci = CodeIntelligence('data/code_intelligence.db')
results = ci.semantic_search('authentication token verification', context_symbol='shared/code_intelligence.py::CodeIntelligence::semantic_search', search_context='review')
for r in results[:5]:
    print(f'{r[\"score\"]:.4f} {r[\"name\"][:60]}')
ci.close()
"`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat(search): complete 4-phase semantic search upgrade

Activated Voyage Code 3 embeddings (replacing bag-of-words), added
weighted RRF (FTS=1.3), cross-encoder reranker (MiniLM-L-12),
graph-boosted scoring (PageRank, Leiden communities, fan-in, test
dampening). Expected +25-40% NDCG improvement."
```
