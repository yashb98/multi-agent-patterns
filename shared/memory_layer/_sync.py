"""SyncService — 3-engine reconciliation and propagation."""

from shared.logging_config import get_logger
from shared.memory_layer._entries import MemoryEntry, MemoryTier

logger = get_logger(__name__)


class SyncService:
    def __init__(self, sqlite, qdrant=None, neo4j=None, embedder=None):
        self._sqlite = sqlite
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._embedder = embedder

    def reconcile(self) -> dict:
        """Backfill Qdrant/Neo4j from SQLite for any missing entries."""
        stats = {"qdrant_backfilled": 0, "neo4j_backfilled": 0}
        all_ids = self._sqlite.all_memory_ids()

        for memory_id in all_ids:
            entry = self._sqlite.get_by_id(memory_id)
            if not entry:
                continue

            # Backfill Qdrant
            if self._qdrant and not self._qdrant.has_point(memory_id, entry.tier):
                vector = self._embedder.embed(entry.content) if self._embedder else []
                if vector:
                    self._qdrant.upsert(
                        memory_id, entry.tier, vector,
                        {"domain": entry.domain, "score": entry.score,
                         "lifecycle": entry.lifecycle.value},
                    )
                    stats["qdrant_backfilled"] += 1

            # Backfill Neo4j
            if self._neo4j and not self._neo4j.get_node(memory_id):
                self._neo4j.create_node(
                    memory_id, entry.tier.value, entry.domain,
                    entry.content[:200], entry.score, entry.confidence,
                    entry.decay_score, entry.lifecycle.value,
                    entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
                stats["neo4j_backfilled"] += 1

        logger.info("Reconciliation complete: %s", stats)
        return stats

    def propagate_tombstone(self, memory_id: str, tier: MemoryTier) -> None:
        """Delete from Qdrant, mark FORGOTTEN in Neo4j."""
        if self._qdrant:
            self._qdrant.delete(memory_id, tier)
        if self._neo4j:
            self._neo4j.mark_label(memory_id, "forgotten")

    def sync_to_secondary(self, entry: MemoryEntry) -> None:
        """Write a single entry to both secondary engines."""
        if self._qdrant and self._embedder:
            vector = self._embedder.embed(entry.content)
            self._qdrant.upsert(
                entry.memory_id, entry.tier, vector,
                {"domain": entry.domain, "score": entry.score,
                 "lifecycle": entry.lifecycle.value},
            )

        if self._neo4j:
            self._neo4j.create_node(
                entry.memory_id, entry.tier.value, entry.domain,
                entry.content[:200], entry.score, entry.confidence,
                entry.decay_score, entry.lifecycle.value,
                entry.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            )
