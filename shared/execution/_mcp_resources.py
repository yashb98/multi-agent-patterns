"""MCP Resources -- read-only data endpoints.

Resources are projections from the event store and existing SQLite DBs.
"""

from __future__ import annotations

from urllib.parse import urlparse

from shared.logging_config import get_logger

logger = get_logger(__name__)


def _get_event_store():
    try:
        from shared.execution import get_event_store
        return get_event_store()
    except Exception:
        return None


def get_resource(uri: str) -> dict:
    """Resolve a jobpulse:// URI to a read-only data dict."""
    parsed = urlparse(uri)
    # urlparse treats the first segment after :// as netloc, not path.
    # Combine netloc + path to get the full resource path.
    path = (parsed.netloc + parsed.path).lstrip("/")

    if path == "health":
        return _health_resource()
    elif path.startswith("events/"):
        stream_id = path[len("events/"):]
        return _events_resource(stream_id)
    elif path == "jobs/queue":
        return _jobs_queue_resource()
    elif path.startswith("jobs/history"):
        return _jobs_history_resource()
    elif path == "gates/stats":
        return _gates_stats_resource()
    else:
        return {"error": f"Unknown resource: {uri}"}


def _health_resource() -> dict:
    return {"status": "healthy", "event_store": _get_event_store() is not None}


def _events_resource(stream_id: str) -> dict:
    store = _get_event_store()
    if not store:
        return {"events": [], "error": "Event store unavailable"}
    events = store.get_stream(stream_id)
    return {"stream_id": stream_id, "events": events, "count": len(events)}


def _jobs_queue_resource() -> dict:
    try:
        from jobpulse.job_autopilot import _load_pending
        pending = _load_pending()
        return {"queue": pending, "count": len(pending)}
    except Exception as e:
        return {"queue": [], "error": str(e)}


def _jobs_history_resource() -> dict:
    try:
        from jobpulse.db import JobDB
        db = JobDB()
        recent = db.get_recent_applications(days=7)
        return {"applications": recent, "count": len(recent)}
    except Exception as e:
        return {"applications": [], "error": str(e)}


def _gates_stats_resource() -> dict:
    try:
        from jobpulse.job_analytics import get_gate_stats
        return get_gate_stats()
    except Exception as e:
        return {"error": str(e)}
