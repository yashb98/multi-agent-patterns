"""Durable Execution Infrastructure -- Pillar 4.

Event-sourced state management with crash recovery, MCP production server,
and A2A agent coordination protocol.

Public API:
    get_event_store()   -- shared EventStore singleton
    emit()              -- emit an event (shorthand)
    EventStore, Event   -- event store and event type
    ScanProjector, FormProjector, PatternProjector, project_stream -- state projection
    EventStoreCheckpointer -- LangGraph checkpointing bridge
    RedisClient         -- optional fast cache + pub/sub
"""

from shared.execution._event_store import EventStore, Event
from shared.execution._projectors import (
    ScanProjector, FormProjector, PatternProjector, project_stream,
)
from shared.execution._checkpointer import EventStoreCheckpointer
from shared.execution._redis import RedisClient

_store: EventStore | None = None


def get_event_store(db_path: str | None = None) -> EventStore:
    """Return shared EventStore singleton. Lazy-initialized on first call."""
    global _store
    if _store is None:
        from pathlib import Path
        path = db_path or str(Path(__file__).parent.parent.parent / "data" / "events.db")
        _store = EventStore(db_path=path)
    return _store


def emit(stream_id: str, event_type: str, payload: dict, **kwargs) -> str:
    """Emit an event to the shared store. Returns event_id."""
    return get_event_store().emit(stream_id, event_type, payload, **kwargs)
