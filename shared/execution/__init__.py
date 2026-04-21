"""Durable Execution Infrastructure -- Pillar 4.

Event-sourced state management with crash recovery, MCP production server,
and A2A agent coordination protocol.
"""

from shared.execution._event_store import EventStore, Event
from shared.execution._projectors import (
    ScanProjector, FormProjector, PatternProjector, project_stream,
)
from shared.execution._checkpointer import EventStoreCheckpointer
from shared.execution._redis import RedisClient
from shared.execution._mcp_gateway import (
    CapabilityServer, create_gateway_app, register_capability_server,
)
from shared.execution._a2a_card import AgentCard, AgentSkill, FileAgentRegistry
from shared.execution._a2a_task import A2ATask, TaskManager, TASK_TIMEOUTS
from shared.execution._awareness import (
    TaskPlan, ConfidenceTracker, TaskPreFlight, TaskPostFlight, TaskRunner, Decision,
)
from shared.execution._verifier import FormVerifier, VerifyResult
from shared.execution._rescue import RescueAgent

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
