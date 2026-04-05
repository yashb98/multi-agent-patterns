"""Hybrid Search — FTS5 full-text + vector similarity merged via Reciprocal Rank Fusion.

Combines two search signals for memory retrieval:
1. BM25 keyword matching (via SQLite FTS5) — catches exact terms
2. Vector similarity (via simple embedding cosine) — catches semantic meaning
3. Reciprocal Rank Fusion (RRF) merges both rankings into a single list

Inspired by code-review-graph's hybrid search approach.

Usage:
    search = HybridSearch(db_path=":memory:")
    search.add("doc_1", "How to implement authentication with JWT tokens", {"domain": "security"})
    search.add("doc_2", "Building REST APIs with FastAPI framework", {"domain": "backend"})

    results = search.query("JWT auth for APIs", top_k=5)
    # Returns: [{"id": "doc_1", "text": "...", "score": 0.82, ...}, ...]
"""

import sqlite3
import hashlib
import math
import re
import struct
from typing import Optional

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

from shared.logging_config import get_logger

logger = get_logger(__name__)

# RRF constant — controls how much lower-ranked results contribute.
# Higher k = more weight to lower-ranked items. Standard: 60.
RRF_K = 60


class HybridSearch:
    """SQLite-backed hybrid search with FTS5 + cosine similarity + RRF."""

    def __init__(self, db_path: str = ":memory:", conn: sqlite3.Connection | None = None):
        if conn is not None:
            self.conn = conn
        else:
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
        self._query_embedding_fn = None  # Set by CodeIntelligence to use Voyage
        self.fts_weight = 1.3  # Exact identifiers matter more in code search
        self.vec_weight = 1.0
        # In-memory embedding matrix (loaded once, used for all queries)
        self._embedding_matrix = None  # numpy ndarray (N x D) or None
        self._embedding_ids: list[str] = []  # doc_id at each row index
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                embedding TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                id, text,
                content='documents',
                content_rowid='rowid',
                tokenize='porter unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, id, text) VALUES (new.rowid, new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, id, text) VALUES('delete', old.rowid, old.id, old.text);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                INSERT INTO documents_fts(documents_fts, rowid, id, text) VALUES('delete', old.rowid, old.id, old.text);
                INSERT INTO documents_fts(rowid, id, text) VALUES (new.rowid, new.id, new.text);
            END;
        """)
        self.conn.commit()

    def add(self, doc_id: str, text: str, metadata: Optional[dict] = None):
        """Add a document to the search index."""
        import json
        meta_str = json.dumps(metadata or {})

        # Compute simple bag-of-words embedding (lightweight, no dependencies)
        embedding = self._compute_embedding(text)
        emb_str = ",".join(f"{v:.4f}" for v in embedding)

        self.conn.execute(
            "INSERT OR REPLACE INTO documents (id, text, metadata, embedding) VALUES (?,?,?,?)",
            (doc_id, text, meta_str, emb_str),
        )
        self.conn.commit()

    def remove(self, doc_id: str):
        """Remove a document from the index."""
        self.conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        self.conn.commit()

    def query(self, query_text: str, top_k: int = 10) -> list[dict]:
        """Hybrid search: FTS5 BM25 + vector cosine, merged via RRF.

        Returns list of dicts: {"id", "text", "metadata", "score", "fts_rank", "vec_rank"}
        """
        import json

        # ── Signal 1: FTS5 BM25 ranking ──
        fts_results = self._fts_search(query_text, limit=top_k * 3)

        # ── Signal 2: Vector cosine similarity ──
        # Fast path: skip Voyage API for single code identifiers (snake_case/camelCase)
        if self._is_code_identifier(query_text):
            vec_results = []  # FTS handles exact identifiers better
        else:
            vec_results = self._vector_search(query_text, limit=top_k * 3)

        # ── Merge via Reciprocal Rank Fusion ──
        merged = self._rrf_merge(fts_results, vec_results, top_k,
                                  fts_weight=self.fts_weight, vec_weight=self.vec_weight)

        # Enrich with metadata
        results = []
        for doc_id, rrf_score, fts_rank, vec_rank in merged:
            row = self.conn.execute(
                "SELECT text, metadata FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
            if row:
                results.append({
                    "id": doc_id,
                    "text": row["text"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "score": rrf_score,
                    "fts_rank": fts_rank,
                    "vec_rank": vec_rank,
                })

        return results

    def _fts_search(self, query: str, limit: int = 30) -> list[tuple]:
        """Full-text search via FTS5. Returns [(doc_id, rank), ...]."""
        # Clean query for FTS5 syntax
        clean = re.sub(r"[^\w\s]", " ", query).strip()
        if not clean:
            return []

        # Use OR matching so partial matches still return results
        terms = clean.split()
        fts_query = " OR ".join(terms)

        try:
            rows = self.conn.execute(
                "SELECT id, rank FROM documents_fts WHERE documents_fts MATCH ? "
                "ORDER BY rank LIMIT ?",
                (fts_query, limit),
            ).fetchall()
            return [(r["id"], i + 1) for i, r in enumerate(rows)]
        except Exception:
            return []

    def _vector_search(self, query: str, limit: int = 30) -> list[tuple]:
        """Cosine similarity search. Tries Voyage embeddings first, falls back to bag-of-words.

        Returns [(doc_id, rank), ...]
        """
        # Check if embeddings table exists and has data
        try:
            count = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        except Exception:
            count = 0

        if count > 0 and self._query_embedding_fn is not None:
            # Use Voyage embeddings
            try:
                query_vec = self._query_embedding_fn(query)
            except Exception:
                query_vec = None
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

    def load_embeddings_to_memory(self):
        """Pre-load all Voyage embeddings into a numpy matrix for fast cosine search.

        Call once after init or after reindexing. Converts 3000+ SQLite BLOB reads
        into a single in-memory matrix multiply (~0.1ms vs ~460ms).
        """
        try:
            rows = self.conn.execute("SELECT doc_id, vector FROM embeddings").fetchall()
        except Exception:
            return

        if not rows:
            return

        ids = []
        vectors = []
        for row in rows:
            doc_id = row["doc_id"] if isinstance(row, sqlite3.Row) else row[0]
            blob = row["vector"] if isinstance(row, sqlite3.Row) else row[1]
            n_floats = len(blob) // 4
            vec = struct.unpack(f"{n_floats}f", blob)
            ids.append(doc_id)
            vectors.append(vec)

        if _HAS_NUMPY:
            mat = np.array(vectors, dtype=np.float32)
            # L2 normalize rows for cosine similarity via dot product
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._embedding_matrix = mat / norms
        else:
            self._embedding_matrix = vectors  # fallback: list of tuples

        self._embedding_ids = ids
        logger.info("Loaded %d embeddings into memory (%s)",
                     len(ids), "numpy" if _HAS_NUMPY else "list")

    def _voyage_vector_search(self, query_vector: list[float], limit: int = 30) -> list[tuple]:
        """Cosine similarity against pre-computed Voyage embeddings.

        Uses in-memory numpy matrix if available (0.1ms), falls back to
        SQLite BLOB reads (~460ms).

        Returns: [(doc_id, rank), ...] sorted by similarity descending.
        """
        # Fast path: numpy matrix in memory
        if self._embedding_matrix is not None and _HAS_NUMPY and isinstance(self._embedding_matrix, np.ndarray):
            qvec = np.array(query_vector, dtype=np.float32)
            norm = np.linalg.norm(qvec)
            if norm > 0:
                qvec = qvec / norm
            # Single matrix multiply → all cosine similarities at once
            sims = self._embedding_matrix @ qvec
            top_indices = np.argsort(sims)[::-1][:limit]
            return [(self._embedding_ids[i], rank + 1) for rank, i in enumerate(top_indices)]

        # Fallback: in-memory list (no numpy)
        if self._embedding_matrix is not None and isinstance(self._embedding_matrix, list):
            scored = []
            for i, doc_vec in enumerate(self._embedding_matrix):
                sim = self._cosine_similarity(query_vector, list(doc_vec))
                scored.append((self._embedding_ids[i], sim))
            scored.sort(key=lambda x: x[1], reverse=True)
            return [(doc_id, rank + 1) for rank, (doc_id, _) in enumerate(scored[:limit])]

        # Last resort: read from SQLite
        rows = self.conn.execute("SELECT doc_id, vector FROM embeddings").fetchall()
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

    @staticmethod
    def _is_code_identifier(query: str) -> bool:
        """Check if query is a single code identifier (skip Voyage, use FTS only)."""
        stripped = query.strip()
        if " " in stripped:
            return False
        # snake_case, camelCase, PascalCase, UPPER_CASE — all single-token identifiers
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", stripped))

    # ─── LIGHTWEIGHT EMBEDDINGS ─────────────────────────────────────
    # Simple bag-of-words TF-IDF-like embedding. No external dependencies.
    # For production, swap with sentence-transformers or OpenAI embeddings.

    _VOCAB_SIZE = 512  # Hash-based vocabulary size

    def _compute_embedding(self, text: str) -> list[float]:
        """Compute a simple hash-based bag-of-words embedding."""
        tokens = re.findall(r"\w+", text.lower())
        vec = [0.0] * self._VOCAB_SIZE

        for token in tokens:
            idx = int(hashlib.md5(token.encode()).hexdigest(), 16) % self._VOCAB_SIZE
            vec[idx] += 1.0

        # L2 normalise
        magnitude = math.sqrt(sum(v * v for v in vec))
        if magnitude > 0:
            vec = [v / magnitude for v in vec]

        return vec

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    # ─── STATS ─────────────────────────────────────────────────────

    def count(self) -> int:
        """Return total number of indexed documents."""
        return self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

    def close(self):
        self.conn.close()


def compute_graph_boost(
    conn: sqlite3.Connection,
    qualified_name: str,
    context_qname: str | None = None,
    search_context: str = "general",
) -> float:
    """Compute graph-based boost multiplier for a single search result.

    For batch operations, prefer compute_graph_boost_batch().
    """
    results = compute_graph_boost_batch(
        conn, [qualified_name],
        context_qname=context_qname,
        search_context=search_context,
    )
    return results.get(qualified_name, 1.0)


def compute_graph_boost_batch(
    conn: sqlite3.Connection,
    qualified_names: list[str],
    context_qname: str | None = None,
    search_context: str = "general",
) -> dict[str, float]:
    """Batch compute graph-based boost multipliers — single SQL round-trip.

    Args:
        conn: SQLite connection with nodes/edges tables.
        qualified_names: List of node qualified names.
        context_qname: Optional symbol being worked on (enables proximity boost).
        search_context: "general", "review", "security", or "impact".

    Returns:
        Dict mapping qualified_name → multiplicative boost factor (1.0 = no change).
    """
    if not qualified_names:
        return {}

    placeholders = ",".join("?" * len(qualified_names))

    # Single batch fetch for all nodes
    rows = conn.execute(
        f"SELECT qualified_name, fan_in, pagerank, is_test, risk_score, community_id "
        f"FROM nodes WHERE qualified_name IN ({placeholders})",
        qualified_names,
    ).fetchall()

    node_data = {}
    for row in rows:
        node_data[row[0]] = {
            "fan_in": row[1] or 0,
            "pagerank": row[2] or 0.0,
            "is_test": row[3] or 0,
            "risk_score": row[4] or 0.0,
            "community_id": row[5],
        }

    # Single p90 query (cached for all results)
    try:
        p90 = conn.execute(
            "SELECT fan_in FROM nodes ORDER BY fan_in DESC "
            "LIMIT 1 OFFSET (SELECT COUNT(*)/10 FROM nodes)"
        ).fetchone()
        fan_in_threshold = max(p90[0] if p90 else 5, 2)
    except Exception:
        fan_in_threshold = 5

    # Context community (single lookup)
    ctx_community = None
    if context_qname:
        ctx_row = conn.execute(
            "SELECT community_id FROM nodes WHERE qualified_name = ?",
            (context_qname,),
        ).fetchone()
        if ctx_row:
            ctx_community = ctx_row[0]

    # Batch edge check for context proximity
    direct_edges: set[str] = set()
    if context_qname:
        edge_rows = conn.execute(
            f"SELECT source_qname, target_qname FROM edges WHERE "
            f"(source_qname = ? AND target_qname IN ({placeholders})) OR "
            f"(target_qname = ? AND source_qname IN ({placeholders}))",
            [context_qname] + qualified_names + [context_qname] + qualified_names,
        ).fetchall()
        for erow in edge_rows:
            direct_edges.add(erow[0])
            direct_edges.add(erow[1])

    # Compute boosts
    boosts: dict[str, float] = {}
    for qname in qualified_names:
        data = node_data.get(qname)
        if data is None:
            boosts[qname] = 1.0
            continue

        boost = 1.0

        if data["is_test"]:
            boost *= 0.7

        boost *= 1.0 + (data["pagerank"] * 0.5)

        if data["fan_in"] >= fan_in_threshold:
            boost *= 1.25

        if context_qname and context_qname != qname:
            if ctx_community is not None and data["community_id"] == ctx_community:
                boost *= 1.2
            if qname in direct_edges:
                boost *= 1.5

        if search_context in ("review", "security", "impact"):
            boost *= 1.0 + (data["risk_score"] * 0.5)

        boosts[qname] = boost

    return boosts
