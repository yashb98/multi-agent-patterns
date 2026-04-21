# Durable Execution (shared/execution/)

Event-sourced durable execution infrastructure — Pillar 4 of 6.

## Core Concepts
- **Event Store** (`_event_store.py`): Append-only SQLite WAL log. `emit()` writes, `get_stream()` reads.
- **Projectors** (`_projectors.py`): Fold events → current state. Pure, deterministic, idempotent.
- **Checkpointer** (`_checkpointer.py`): LangGraph bridge — stores checkpoints as events.
- **Redis** (`_redis.py`): Optional fast cache + pub/sub. System works without it.

## Usage
```python
from shared.execution import get_event_store, emit, EventStoreCheckpointer

# Emit events
emit("scan:2026-04-21", "scan.platform_started", {"platform": "linkedin"})

# Project current state
from shared.execution import ScanProjector, project_stream
state = project_stream(get_event_store(), "scan:2026-04-21", ScanProjector())

# LangGraph checkpointing
cp = EventStoreCheckpointer(get_event_store())
graph = build_enhanced_swarm_graph(checkpointer=cp)
```

## Rules
- All event access goes through EventStore — never query data/events.db directly
- Same principle as MemoryManager and CognitiveEngine: single facade
- Events are immutable — never update or delete (except compaction)
- Projectors must be idempotent — replaying twice = same state
- Redis is optional — system MUST work without it
- Tests MUST use tmp_path fixture — never touch data/events.db
