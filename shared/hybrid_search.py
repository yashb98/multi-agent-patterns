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
from typing import Optional

from shared.logging_config import get_logger

logger = get_logger(__name__)

# RRF constant — controls how much lower-ranked results contribute.
# Higher k = more weight to lower-ranked items. Standard: 60.
RRF_K = 60


class HybridSearch:
    """SQLite-backed hybrid search with FTS5 + cosine similarity + RRF."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
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
        vec_results = self._vector_search(query_text, limit=top_k * 3)

        # ── Merge via Reciprocal Rank Fusion ──
        merged = self._rrf_merge(fts_results, vec_results, top_k)

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
        """Cosine similarity search. Returns [(doc_id, rank), ...]."""
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

    def _rrf_merge(
        self,
        fts_results: list[tuple],
        vec_results: list[tuple],
        top_k: int,
    ) -> list[tuple]:
        """Reciprocal Rank Fusion: merge two ranked lists.

        RRF_score(d) = sum(1 / (k + rank_i)) for each ranker i

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
                rrf_score += 1.0 / (RRF_K + fts_rank)
            if vec_rank < 999:
                rrf_score += 1.0 / (RRF_K + vec_rank)

            scored.append((doc_id, rrf_score, fts_rank, vec_rank))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

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
