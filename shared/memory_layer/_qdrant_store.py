"""Qdrant vector store for the memory layer.

One collection per memory tier. SQLite is the source of truth; Qdrant handles
semantic similarity search. Memory IDs are 12-char hex strings from uuid4().hex[:12].

Collections:
  episodic_memories  — past run records (MemoryTier.EPISODIC)
  semantic_facts     — accumulated domain knowledge (MemoryTier.SEMANTIC)
  procedures         — learned strategies (MemoryTier.PROCEDURAL)
  experiences        — high-scoring run experiences (MemoryTier.EXPERIENCE)

Pattern tier stays in its own JSON file and is NOT indexed here.
"""

from __future__ import annotations

import hashlib
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client import models as qm

from shared.memory_layer._entries import MemoryTier
from shared.logging_config import get_logger

logger = get_logger(__name__)

# MemoryTier → Qdrant collection name
_TIER_COLLECTION: dict[MemoryTier, str] = {
    MemoryTier.EPISODIC: "episodic_memories",
    MemoryTier.SEMANTIC: "semantic_facts",
    MemoryTier.PROCEDURAL: "procedures",
    MemoryTier.EXPERIENCE: "experiences",
}

# Tiers indexed in Qdrant (PATTERN stays in JSON)
_INDEXED_TIERS: tuple[MemoryTier, ...] = (
    MemoryTier.EPISODIC,
    MemoryTier.SEMANTIC,
    MemoryTier.PROCEDURAL,
    MemoryTier.EXPERIENCE,
)


def _to_qdrant_id(memory_id: str) -> int:
    """Convert an arbitrary memory ID string to a stable unsigned 64-bit integer.

    Qdrant accepts either valid UUID strings or unsigned 64-bit integers as
    point IDs. We use MD5-derived integers so that any string (not just hex)
    works as a memory ID, and the mapping is collision-resistant for the scale
    of this system (hundreds of thousands of entries).
    """
    return int(hashlib.md5(memory_id.encode()).hexdigest(), 16) % (2 ** 63)


class QdrantStore:
    """Vector search engine backed by Qdrant.

    Parameters
    ----------
    location:
        Qdrant server URL (e.g. "http://localhost:6333") or ":memory:" for
        in-process in-memory mode (tests).
    dims:
        Embedding dimensionality. 1024 for Voyage, 384 for MiniLM.
    """

    def __init__(self, location: str = ":memory:", dims: int = 1024) -> None:
        self._dims = dims
        if location == ":memory:":
            self._client = QdrantClient(location=":memory:")
        else:
            self._client = QdrantClient(url=location)

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def ensure_collections(self) -> None:
        """Create all tier collections if they don't already exist."""
        existing = {c.name for c in self._client.get_collections().collections}
        for tier in _INDEXED_TIERS:
            name = _TIER_COLLECTION[tier]
            if name not in existing:
                self._client.create_collection(
                    collection_name=name,
                    vectors_config=qm.VectorParams(
                        size=self._dims,
                        distance=qm.Distance.COSINE,
                    ),
                )
                logger.debug("Created Qdrant collection '%s'", name)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        memory_id: str,
        tier: MemoryTier,
        vector: list[float],
        payload: dict,
    ) -> None:
        """Store or overwrite a vector with its payload metadata."""
        collection = _TIER_COLLECTION[tier]
        # Always store memory_id in payload so searches can return the original ID
        full_payload = dict(payload)
        full_payload["memory_id"] = memory_id
        self._client.upsert(
            collection_name=collection,
            points=[
                qm.PointStruct(
                    id=_to_qdrant_id(memory_id),
                    vector=vector,
                    payload=full_payload,
                )
            ],
        )

    def delete(self, memory_id: str, tier: MemoryTier) -> None:
        """Remove a point by memory ID from the given tier collection."""
        collection = _TIER_COLLECTION[tier]
        self._client.delete(
            collection_name=collection,
            points_selector=qm.PointIdsList(points=[_to_qdrant_id(memory_id)]),
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def search(
        self,
        tier: MemoryTier,
        query_vector: list[float],
        top_k: int = 10,
        filters: Optional[dict] = None,
        min_score: Optional[float] = None,
        score_threshold: Optional[float] = None,
    ) -> list[tuple[str, float]]:
        """Semantic similarity search within a single tier collection.

        Parameters
        ----------
        tier:
            Which tier collection to search.
        query_vector:
            Query embedding.
        top_k:
            Maximum number of results to return.
        filters:
            Dict of payload key→value equality filters (e.g. {"domain": "physics"}).
        min_score:
            Minimum value of the "score" payload field (Range filter).
        score_threshold:
            Minimum cosine similarity score (0-1) to include a result.

        Returns
        -------
        List of (memory_id, cosine_score) tuples, ordered by descending similarity.
        """
        collection = _TIER_COLLECTION[tier]
        must_conditions: list[qm.Condition] = []

        if filters:
            for key, value in filters.items():
                must_conditions.append(
                    qm.FieldCondition(key=key, match=qm.MatchValue(value=value))
                )

        if min_score is not None:
            must_conditions.append(
                qm.FieldCondition(key="score", range=qm.Range(gte=min_score))
            )

        query_filter: Optional[qm.Filter] = None
        if must_conditions:
            query_filter = qm.Filter(must=must_conditions)

        kwargs: dict = dict(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
        )
        if query_filter is not None:
            kwargs["query_filter"] = query_filter
        if score_threshold is not None:
            kwargs["score_threshold"] = score_threshold

        response = self._client.query_points(**kwargs)
        return [
            (point.payload["memory_id"], point.score)
            for point in response.points
        ]

    def search_all_tiers(
        self,
        query_vector: list[float],
        top_k: int = 10,
        score_threshold: Optional[float] = None,
    ) -> list[tuple[str, float]]:
        """Cross-collection search across all indexed tiers.

        Queries each collection independently and merges results, sorted by
        descending cosine score.

        Returns
        -------
        List of (memory_id, cosine_score) tuples, ordered by descending similarity.
        """
        all_results: list[tuple[str, float]] = []
        for tier in _INDEXED_TIERS:
            results = self.search(
                tier,
                query_vector,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            all_results.extend(results)
        all_results.sort(key=lambda x: x[1], reverse=True)
        return all_results[:top_k]

    def count(self, tier: MemoryTier) -> int:
        """Return the number of points in the given tier collection."""
        collection = _TIER_COLLECTION[tier]
        result = self._client.count(collection_name=collection)
        return result.count

    def has_point(self, memory_id: str, tier: MemoryTier) -> bool:
        """Check whether a point exists in the given tier collection."""
        collection = _TIER_COLLECTION[tier]
        points = self._client.retrieve(
            collection_name=collection,
            ids=[_to_qdrant_id(memory_id)],
        )
        return len(points) > 0
