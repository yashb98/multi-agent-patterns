"""Cross-platform field embedding transfer.

Learns field→value mappings on one ATS platform and transfers them to others
via semantic similarity. When a field on LinkedIn is semantically close to a
field on Greenhouse that we already know how to fill, reuse the mapping.

This reduces per-platform cold-start time and improves screening answer quality
on platforms the system has seen fewer times.

Usage:
    transfer = CrossPlatformFieldTransfer()

    # After successfully filling a field
    transfer.record_mapping(
        platform="greenhouse",
        field_label="Current Base Salary",
        value="45000",
        source="intent_resolver",  # how the value was derived
    )

    # When encountering an unknown field on a new platform
    candidates = transfer.find_transfers(
        to_platform="linkedin",
        field_label="What is your current annual salary?",
        top_n=3,
    )
    # candidates = [{"platform": "greenhouse", "field": "...", "value": "45000", "score": 0.92}, ...]
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from shared.logging_config import get_logger
from jobpulse.config import DATA_DIR

logger = get_logger(__name__)

_DEFAULT_DB = str(DATA_DIR / "cross_platform_fields.db")


@dataclass
class FieldMapping:
    """A learned field→value mapping from a specific platform."""

    platform: str
    field_label: str
    value: str
    source: str
    times_used: int
    success_count: int
    last_used: str


@dataclass
class TransferCandidate:
    """A candidate mapping from another platform, ranked by similarity."""

    from_platform: str
    from_field: str
    value: str
    source: str
    semantic_score: float
    success_rate: float
    total_uses: int


class CrossPlatformFieldTransfer:
    """Semantic cross-platform field mapping transfer using SQLite + optional Qdrant."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._init_db()
        self._embedder: Optional[Any] = None
        self._qdrant: Optional[Any] = None
        self._init_vector_stores()

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS field_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    field_label TEXT NOT NULL,
                    field_embedding BLOB,
                    value TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    times_used INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_used TEXT NOT NULL,
                    UNIQUE(platform, field_label)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_platform
                ON field_mappings(platform)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_field_label
                ON field_mappings(field_label)
            """)

    def _init_vector_stores(self) -> None:
        """Lazy-init embedding model and Qdrant client.

        Audit S4 B-3: previously imported a non-existent `shared.embeddings`
        module (silent ImportError → embedder=None) and a non-existent
        `_get_qdrant_client` from screening_semantic_cache (silent
        ImportError → qdrant=None). Both paths now use the canonical
        accessors so the vector path actually wires when configured.
        """
        try:
            from shared.semantic_utils import _get_embedder
            self._embedder = _get_embedder()
        except Exception as exc:
            logger.debug("Embedder unavailable for cross-platform transfer: %s", exc)
        try:
            from jobpulse.screening_semantic_cache import _get_qdrant_client
            self._qdrant = _get_qdrant_client()
        except Exception as exc:
            logger.debug("Qdrant unavailable for cross-platform transfer: %s", exc)

    def record_mapping(
        self,
        platform: str,
        field_label: str,
        value: str,
        source: str = "unknown",
        success: bool = True,
    ) -> None:
        """Record or update a field→value mapping for a platform."""
        from datetime import UTC, datetime

        now = datetime.now(UTC).isoformat()
        embedding_bytes = self._embed_field(field_label)

        with sqlite3.connect(self._db_path) as conn:
            existing = conn.execute(
                "SELECT id, times_used, success_count FROM field_mappings "
                "WHERE platform = ? AND field_label = ?",
                (platform, field_label),
            ).fetchone()

            if existing:
                new_used = existing[1] + 1
                new_success = existing[2] + (1 if success else 0)
                conn.execute(
                    """UPDATE field_mappings
                       SET value = ?, source = ?, times_used = ?,
                           success_count = ?, last_used = ?,
                           field_embedding = COALESCE(?, field_embedding)
                       WHERE id = ?""",
                    (value, source, new_used, new_success, now,
                     embedding_bytes, existing[0]),
                )
            else:
                conn.execute(
                    """INSERT INTO field_mappings
                       (platform, field_label, field_embedding, value, source,
                        times_used, success_count, created_at, last_used)
                       VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                    (platform, field_label, embedding_bytes, value, source,
                     1 if success else 0, now, now),
                )

        # Also upsert to Qdrant for fast similarity search
        if self._qdrant and embedding_bytes:
            self._upsert_to_qdrant(platform, field_label, value, source, embedding_bytes)

    def _embed_field(self, field_label: str) -> bytes | None:
        """Embed a field label. Returns None if embedder unavailable."""
        if self._embedder is None:
            return None
        try:
            vector = self._embedder.embed(field_label)
            import numpy as np
            return np.array(vector, dtype=np.float32).tobytes()
        except Exception as exc:
            logger.debug("Embedding failed for '%s': %s", field_label[:60], exc)
            return None

    def _upsert_to_qdrant(
        self, platform: str, field_label: str,
        value: str, source: str, embedding_bytes: bytes,
    ) -> None:
        try:
            import numpy as np
            from qdrant_client.models import PointStruct

            vector = np.frombuffer(embedding_bytes, dtype=np.float32).tolist()
            point_id = f"{platform}::{field_label}"
            self._qdrant.upsert(
                collection_name="field_mappings",
                points=[PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "platform": platform,
                        "field_label": field_label,
                        "value": value,
                        "source": source,
                    },
                )],
                wait=False,
            )
        except Exception as exc:
            logger.debug("Qdrant upsert failed: %s", exc)

    def find_transfers(
        self,
        to_platform: str,
        field_label: str,
        *,
        top_n: int = 3,
        min_success_rate: float = 0.5,
        exclude_same_platform: bool = True,
    ) -> list[TransferCandidate]:
        """Find mappings from other platforms that are semantically similar.

        Tries Qdrant vector search first, falls back to SQLite brute-force
        cosine similarity if Qdrant is unavailable.
        """
        query_embedding = self._embed_field(field_label)

        # Try Qdrant first
        if self._qdrant and query_embedding:
            candidates = self._search_qdrant(
                to_platform, field_label, query_embedding,
                top_n=top_n * 2,  # fetch more for filtering
                exclude_same_platform=exclude_same_platform,
            )
        else:
            candidates = self._search_sqlite_brute_force(
                to_platform, field_label, query_embedding,
                top_n=top_n * 2,
                exclude_same_platform=exclude_same_platform,
            )

        # Filter by success rate and rank
        filtered = [
            c for c in candidates
            if c.success_rate >= min_success_rate
        ]
        filtered.sort(key=lambda c: c.semantic_score, reverse=True)
        return filtered[:top_n]

    def _search_qdrant(
        self,
        to_platform: str,
        field_label: str,
        query_embedding_bytes: bytes,
        top_n: int,
        exclude_same_platform: bool,
    ) -> list[TransferCandidate]:
        import numpy as np
        from qdrant_client.models import Filter, FieldCondition, MatchExcept

        vector = np.frombuffer(query_embedding_bytes, dtype=np.float32).tolist()

        search_filter = None
        if exclude_same_platform:
            search_filter = Filter(
                must=[FieldCondition(
                    key="platform",
                    match=MatchExcept(except_=[to_platform]),
                )]
            )

        try:
            results = self._qdrant.search(
                collection_name="field_mappings",
                query_vector=vector,
                limit=top_n,
                query_filter=search_filter,
                with_payload=True,
            )
        except Exception as exc:
            logger.debug("Qdrant search failed: %s", exc)
            return []

        candidates = []
        for r in results:
            payload = r.payload or {}
            total = payload.get("times_used", 1)
            successes = payload.get("success_count", 0)
            success_rate = successes / total if total > 0 else 0.0
            candidates.append(TransferCandidate(
                from_platform=payload.get("platform", "unknown"),
                from_field=payload.get("field_label", ""),
                value=payload.get("value", ""),
                source=payload.get("source", "unknown"),
                semantic_score=r.score,
                success_rate=success_rate,
                total_uses=total,
            ))
        return candidates

    def _search_sqlite_brute_force(
        self,
        to_platform: str,
        field_label: str,
        query_embedding_bytes: bytes | None,
        top_n: int,
        exclude_same_platform: bool,
    ) -> list[TransferCandidate]:
        """Fallback: brute-force cosine over SQLite stored embeddings."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            if exclude_same_platform:
                rows = conn.execute(
                    "SELECT * FROM field_mappings WHERE platform != ?",
                    (to_platform,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM field_mappings").fetchall()

        if not rows:
            return []

        import numpy as np

        if query_embedding_bytes:
            query_vec = np.frombuffer(query_embedding_bytes, dtype=np.float32)
        else:
            # No embedding — use simple text overlap as fallback
            return self._text_overlap_ranking(field_label, rows, top_n)

        candidates = []
        for r in rows:
            emb = r["field_embedding"]
            if not emb:
                continue
            vec = np.frombuffer(emb, dtype=np.float32)
            if len(vec) != len(query_vec):
                continue
            score = float(np.dot(query_vec, vec) / (np.linalg.norm(query_vec) * np.linalg.norm(vec)))
            total = r["times_used"]
            successes = r["success_count"]
            success_rate = successes / total if total > 0 else 0.0
            candidates.append(TransferCandidate(
                from_platform=r["platform"],
                from_field=r["field_label"],
                value=r["value"],
                source=r["source"],
                semantic_score=score,
                success_rate=success_rate,
                total_uses=total,
            ))

        candidates.sort(key=lambda c: c.semantic_score, reverse=True)
        return candidates[:top_n]

    @staticmethod
    def _text_overlap_ranking(
        field_label: str,
        rows: list[sqlite3.Row],
        top_n: int,
    ) -> list[TransferCandidate]:
        """Fallback when no embeddings available: simple word overlap score."""
        query_words = set(field_label.lower().split())
        candidates = []
        for r in rows:
            field_words = set(r["field_label"].lower().split())
            overlap = len(query_words & field_words)
            total = len(query_words | field_words)
            score = overlap / total if total > 0 else 0.0
            if score > 0.3:  # minimum overlap threshold
                total_uses = r["times_used"]
                successes = r["success_count"]
                success_rate = successes / total_uses if total_uses > 0 else 0.0
                candidates.append(TransferCandidate(
                    from_platform=r["platform"],
                    from_field=r["field_label"],
                    value=r["value"],
                    source=r["source"],
                    semantic_score=score,
                    success_rate=success_rate,
                    total_uses=total_uses,
                ))
        candidates.sort(key=lambda c: c.semantic_score, reverse=True)
        return candidates[:top_n]

    def get_stats(self) -> dict:
        """Return aggregate statistics about learned mappings."""
        with sqlite3.connect(self._db_path) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM field_mappings"
            ).fetchone()[0]
            platforms = conn.execute(
                "SELECT platform, COUNT(*) as cnt FROM field_mappings GROUP BY platform"
            ).fetchall()
            total_uses = conn.execute(
                "SELECT SUM(times_used) FROM field_mappings"
            ).fetchone()[0]

        return {
            "total_mappings": total,
            "platforms": {r[0]: r[1] for r in platforms},
            "total_uses": total_uses or 0,
        }
