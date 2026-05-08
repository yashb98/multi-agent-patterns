"""SyncService — 3-engine reconciliation and propagation."""

import queue
import threading
import time

from shared.logging_config import get_logger
from shared.memory_layer._entries import MemoryEntry, MemoryTier
from shared.memory_layer._qdrant_store import _INDEXED_TIERS

logger = get_logger(__name__)


# Per-write linker tuning. score_threshold=0.5 short-circuits noisy hits before
# they ever reach `classify_relationship` (which itself rejects <0.75 anyway).
# top_k=5 per tier × 4 tiers bounds the per-write work to one Qdrant round-trip
# per tier and at most one batched Neo4j MERGE.
_LINKER_TOP_K = 5
_LINKER_SCORE_THRESHOLD = 0.5


class SyncService:
    def __init__(
        self,
        sqlite,
        qdrant=None,
        neo4j=None,
        embedder=None,
        linker=None,
        start_background: bool = True,
        queue_size: int = 1000,
    ):
        self._sqlite = sqlite
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._embedder = embedder
        self._linker = linker
        self._queue: queue.Queue[MemoryEntry] = queue.Queue(maxsize=queue_size)
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._worker_guard = threading.Lock()
        if start_background and (self._qdrant is not None or self._neo4j is not None):
            self._start_worker()

    def _start_worker(self) -> None:
        with self._worker_guard:
            if self._worker and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker,
                name="memory-secondary-sync",
                daemon=True,
            )
            self._worker.start()

    def _run_worker(self) -> None:
        while not self._stop_event.is_set() or not self._queue.empty():
            try:
                entry = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._sync_entry(entry)
            except Exception as exc:
                logger.warning(
                    "Secondary sync worker failed for %s: %s",
                    entry.memory_id,
                    exc,
                    extra={
                        "memory_id": entry.memory_id,
                        "error_type": type(exc).__name__,
                    },
                )
            finally:
                self._queue.task_done()

    def _embed_for_qdrant(self, content: str, memory_id: str) -> list[float] | None:
        """Embed and validate dimension matches the Qdrant collection.

        Returns the vector when dimensions match, ``None`` (with warning) when
        the embedder fell back to a different model whose dim doesn't fit the
        Qdrant collections. Skipping is safer than letting Qdrant reject the
        upsert silently in the background worker.
        """
        if not (self._qdrant and self._embedder):
            return None
        vector = self._embedder.embed(content)
        expected = getattr(self._qdrant, "_dims", None)
        # Only enforce when _dims is an actual int — MagicMock-based test
        # fixtures that don't set _dims should not silently skip Qdrant writes.
        if isinstance(expected, int) and len(vector) != expected:
            logger.warning(
                "Embedder produced %d-dim vector but Qdrant collection expects %d-dim — "
                "skipping Qdrant write (memory_id=%s). "
                "Likely a primary/fallback model dim mismatch.",
                len(vector), expected, memory_id,
            )
            return None
        return vector

    def _sync_entry(self, entry: MemoryEntry) -> None:
        vector: list[float] | None = None
        if self._qdrant and self._embedder:
            vector = self._embed_for_qdrant(entry.content, entry.memory_id)
            if vector is not None:
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

        # Discover and create graph edges to similar existing memories.
        # Reuses the vector already computed for the upsert — re-embedding would
        # waste a Voyage call and could even produce a different vector if the
        # fallback model fires, causing the linker's search to miss its own point.
        if self._linker is not None and self._qdrant is not None and vector is not None:
            self._link_neighbors(entry, vector)

    def _link_neighbors(
        self,
        entry: MemoryEntry,
        vector: list[float],
    ) -> None:
        """Search Qdrant top-K across all tiers, hydrate, hand to linker.

        Loops every indexed tier (not just `entry.tier`) because relationship
        rules in `classify_relationship` are cross-tier (EXPERIENCE→EPISODIC,
        EPISODIC→SEMANTIC, EPISODIC→PROCEDURAL). Filters self-match — the new
        entry was upserted to Qdrant moments ago and would be its own top hit.
        Failures here are swallowed: edge creation is a best-effort enrichment
        on top of the source-of-truth SQLite write.
        """
        hits: list[tuple[str, float]] = []
        for tier in _INDEXED_TIERS:
            try:
                tier_hits = self._qdrant.search(
                    tier, vector,
                    top_k=_LINKER_TOP_K,
                    score_threshold=_LINKER_SCORE_THRESHOLD,
                )
            except Exception as exc:
                logger.warning(
                    "Linker: Qdrant search in tier %s failed for %s: %s",
                    tier.value, entry.memory_id, exc,
                )
                continue
            hits.extend((mid, score) for mid, score in tier_hits if mid != entry.memory_id)

        if not hits:
            return

        neighbor_ids = [mid for mid, _ in hits]
        try:
            hydrated = self._sqlite.get_by_ids(neighbor_ids)
        except Exception as exc:
            logger.warning(
                "Linker: SQLite hydration failed for %s: %s",
                entry.memory_id, exc,
            )
            return
        by_id = {e.memory_id: e for e in hydrated}
        neighbors = [(by_id[mid], score) for mid, score in hits if mid in by_id]
        if not neighbors:
            return

        try:
            self._linker.link_with_neighbors(entry, neighbors)
        except Exception as exc:
            logger.warning(
                "Linker: link_with_neighbors failed for %s: %s",
                entry.memory_id, exc,
            )

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
                vector = self._embed_for_qdrant(entry.content, memory_id) if self._embedder else None
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

    def sync_to_secondary(self, entry: MemoryEntry, background: bool = True) -> None:
        """Write a single entry to both secondary engines.

        Background mode queues work to the daemon sync thread; when disabled
        (or unavailable), sync runs inline in the caller thread.
        """
        if self._qdrant is None and self._neo4j is None:
            return

        if background and self._worker is not None:
            try:
                self._queue.put_nowait(entry)
                return
            except queue.Full:
                logger.warning(
                    "Secondary sync queue full — falling back to inline sync",
                    extra={"queue_size": self._queue.qsize(), "memory_id": entry.memory_id},
                )

        self._sync_entry(entry)

    def flush(self, timeout: float | None = None) -> None:
        """Block until queued secondary-sync tasks complete."""
        start = time.monotonic()
        while self._queue.unfinished_tasks:
            if timeout is not None and (time.monotonic() - start) >= timeout:
                break
            time.sleep(0.01)

    def pending_count(self) -> int:
        """Number of queued sync items waiting for processing."""
        return self._queue.qsize()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the background worker gracefully."""
        self._stop_event.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=timeout)
