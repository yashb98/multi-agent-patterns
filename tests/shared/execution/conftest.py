import pytest
from pathlib import Path


@pytest.fixture
def event_db_path(tmp_path):
    """Temporary SQLite path for event store tests."""
    return str(tmp_path / "events.db")


@pytest.fixture
def event_store(event_db_path):
    """Fresh EventStore backed by temp SQLite."""
    from shared.execution._event_store import EventStore
    store = EventStore(db_path=event_db_path)
    yield store
    store.close()
