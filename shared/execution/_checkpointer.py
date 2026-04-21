"""LangGraph checkpoint saver backed by the event store.

Bridges LangGraph's BaseCheckpointSaver protocol to our event-sourced
storage. Each checkpoint is stored as a pattern.checkpoint event.
"""

from __future__ import annotations

from typing import Iterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.base import CheckpointTuple

from shared.execution._event_store import EventStore
from shared.logging_config import get_logger

logger = get_logger(__name__)


class EventStoreCheckpointer(BaseCheckpointSaver):
    """LangGraph-compatible checkpoint saver using EventStore.

    Inherits from BaseCheckpointSaver so LangGraph's compile()
    validation accepts it as a valid checkpointer.
    """

    def __init__(self, event_store: EventStore):
        super().__init__()
        self._store = event_store

    def put(
        self,
        config: dict,
        checkpoint: dict,
        metadata: dict,
        new_versions: dict | None = None,
    ) -> dict:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        self._store.emit(
            stream_id=stream_id,
            event_type="pattern.checkpoint",
            payload={"checkpoint": checkpoint, "metadata": metadata},
        )
        logger.debug("Checkpoint saved for thread %s", thread_id)
        return config

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        events = self._store.get_stream(stream_id, event_type="pattern.checkpoint")
        if not events:
            return None
        latest = events[-1]
        return CheckpointTuple(
            config=config,
            checkpoint=latest["payload"]["checkpoint"],
            metadata=latest["payload"].get("metadata", {}),
        )

    def list(
        self,
        config: dict,
        *,
        filter: dict | None = None,
        before: dict | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        thread_id = config["configurable"]["thread_id"]
        stream_id = f"pattern:{thread_id}"
        events = self._store.get_stream(stream_id, event_type="pattern.checkpoint")
        for event in reversed(events):
            yield CheckpointTuple(
                config=config,
                checkpoint=event["payload"]["checkpoint"],
                metadata=event["payload"].get("metadata", {}),
            )

    def put_writes(self, config: dict, writes: list, task_id: str) -> None:
        pass
