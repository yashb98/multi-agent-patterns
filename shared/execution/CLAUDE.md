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

## MCP Gateway (`_mcp_gateway.py`)
FastAPI-based router multiplexing capability servers. Auth middleware, audit logging, health check.
- `create_gateway_app()` returns FastAPI app
- `register_capability_server()` adds a capability server
- `GET /health`, `GET /mcp/tools`, `POST /mcp/call`

## JobPulse Capability Server (`_mcp_jobpulse.py`)
Wraps existing jobpulse functions as MCP tools. No new business logic.
- `create_jobpulse_server()` returns CapabilityServer with 4 tools

## MCP Resources (`_mcp_resources.py`)
Read-only data endpoints: health, events, job queue, history, gate stats.
- `get_resource(uri)` returns dict

## A2A Agent Cards (`_a2a_card.py`)
Google A2A-compatible agent card format. FileAgentRegistry for local deployment.
- `AgentCard.to_dict()` → JSON-serializable card
- `FileAgentRegistry` persists to `data/agent_registry.json`

## A2A Tasks (`_a2a_task.py`)
Task lifecycle: pending → running → verifying → completed/failed/escalated/timed_out.
- `TaskManager.create_task()` → A2ATask
- `TaskManager.transition()` validates state machine

## A2A HTTP Endpoints (`_a2a_protocol.py`)
FastAPI router for agent card discovery and task CRUD.
- `GET /a2a/{agent_name}/card`, `POST /a2a/{agent_name}/task`, `GET /a2a/{agent_name}/task/{task_id}`

## Awareness Loop (`_awareness.py`)
Cross-pillar wiring: pre-flight (memory + events + cognitive + optimization) →
execute (confidence tracking) → post-flight (learning).
- `TaskRunner` wraps any agent function with the full loop
- `ConfidenceTracker` escalates when confidence < 0.4

## FormVerifier (`_verifier.py`)
Heuristic field checks: name-in-phone, duplicate uploads, empty required fields.

## Rescue Agent (`_rescue.py`)
LLM vision analysis for unknown ATS platforms. Budget: 3 rescues/domain/day.

## Rules
- All event access goes through EventStore — never query data/events.db directly
- Same principle as MemoryManager and CognitiveEngine: single facade
- Events are immutable — never update or delete (except compaction)
- Projectors must be idempotent — replaying twice = same state
- Redis is optional — system MUST work without it
- Tests MUST use tmp_path fixture — never touch data/events.db
