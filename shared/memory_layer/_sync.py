"""SyncService — 3-engine reconciliation and propagation."""

import queue
import threading
import time

from shared.logging_config import get_logger
from shared.memory_layer._entries import MemoryEntry, MemoryTier

logger = get_logger(__name__)


class SyncService:
    def __init__(
        self,
        sqlite,
        qdrant=None,
        neo4j=None,
        embedder=None,
        start_background: bool = True,
        queue_size: int = 1000,
    ):
        self._sqlite = sqlite
        self._qdrant = qdrant
        self._neo4j = neo4j
        self._embedder = embedder
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
                logger.warning("Secondary sync worker failed for %s: %s", entry.memory_id, exc)
            finally:
                self._queue.task_done()

    def _sync_entry(self, entry: MemoryEntry) -> None:
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
                logger.warning("Secondary sync queue full — falling back to inline sync")

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
