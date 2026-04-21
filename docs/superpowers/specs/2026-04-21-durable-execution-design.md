# Durable Execution Infrastructure — Design Spec

**Date:** 2026-04-21
**Pillar:** 4 of 6 (Autonomous Agent Infrastructure)
**Status:** Design approved, pending implementation plan
**Depends on:** Pillar 1 (Memory) for pre-flight recall, Pillar 2 (Cognitive) for complexity assessment, Pillar 3 (Optimization) for domain stats. Soft dependencies — Phase 1 works standalone.

---

## Problem Statement

The system has 6 LangGraph patterns, 12 ATS state machines, and a multi-stage scan pipeline — all running entirely in-memory. Three structural gaps:

1. **No crash recovery.** If the process dies mid-scan (after LinkedIn, before Indeed), it rescans LinkedIn on restart. A form fill that crashes on page 3 of 4 loses all work. A 7-minute pattern run that crashes at iteration 3 loses iterations 1-2.

2. **No production MCP.** The MCP server (`shared/code_intel_mcp.py`) serves 20 code intelligence tools to Claude Code over stdio. Agent capabilities (scan, apply, brief) have no external interface. Moving to the Gigabyte server or exposing to a web dashboard requires re-inventing the exposure layer.

3. **No agent coordination protocol.** Agents communicate via direct function calls or LangGraph state dicts. There's no discovery ("what agents exist?"), no delegation ("scan-agent asks materials-agent for a CV"), no task lifecycle ("is that job application still running?"), and no inter-machine communication.

## Solution: Event-Sourced Durable Execution

Three phases, each independently valuable:

| Phase | Delivers | Core Dependency |
|-------|----------|----------------|
| **Phase 1: Event Store + Checkpointing** | Crash recovery for scan/form/pattern workflows | SQLite + optional Redis |
| **Phase 2: MCP Production Server** | External access to agent capabilities via streamable HTTP | Phase 1 (streaming uses event subscription) |
| **Phase 3: A2A Protocol** | Agent discovery, delegation, task lifecycle, error escalation | Phases 1+2; soft deps on Pillars 1-3 |

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State architecture | Event sourcing (append-only log) | Full audit trail, time-travel debugging, replay recovery. Events are the source of truth, current state is a derived projection. Worth the complexity at production scale. |
| Durable store | SQLite WAL mode | Zero new deps for Phase 1. Single-writer is fine at current scale (50 apps/day). Interface designed for Postgres swap. |
| Fast cache + pub/sub | Redis (optional) | Real-time event streaming, state projections, session caching. Degrades gracefully — system works without it, just slower. |
| MCP transport | Streamable HTTP | Works local (localhost) and remote (LAN/cloud). Replaces stdio for production. Gateway multiplexes capability servers. |
| A2A protocol | Lightweight A2A-compatible | Google A2A agent card format + task lifecycle. Direct function calls locally, HTTP remotely. Full A2A upgrade path without rewrite. |
| Form fill recovery | Honest: skip + dedup, not resume | Playwright browser state dies with process. Recovery means: relaunch, re-auth, skip already-filled pages. Events track *what* was done, not browser state. |

### Deployment Targets

| Stage | Infrastructure | Event Store | Redis | MCP Transport |
|-------|---------------|-------------|-------|---------------|
| Local (Mac) | Single process | SQLite WAL | Optional (localhost) | HTTP localhost:8090 |
| LAN (Gigabyte AI Top) | Multi-process | SQLite WAL | Redis on LAN | HTTP LAN:8090 |
| Cloud (future) | Distributed | Postgres | Redis cluster | HTTPS + mTLS |

---

## Phase 1: Event Store + Checkpointing

### Event Store

Every state change across the system becomes an immutable event in an append-only log.

#### Event Schema

```python
class Event(TypedDict):
    event_id: str           # ULID (time-sortable, globally unique)
    stream_id: str          # e.g. "scan:2026-04-21T09:00", "form:greenhouse:oaknorth:abc"
    event_type: str         # e.g. "scan.platform_started", "form.page_filled"
    payload: dict           # event-specific data
    metadata: dict          # agent_name, timestamp, causation_id, correlation_id
    schema_v: int           # payload schema version — projectors handle multiple versions
    created_at: str         # ISO 8601 with milliseconds
```

No optimistic concurrency version field — this is an append-only log. Ordering is by ULID (globally) or per-stream sequence via Redis INCR (when Redis is available) / SQLite rowid (when not).

#### SQLite Schema

```sql
-- WAL mode for concurrent reads during writes
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

CREATE TABLE events (
    event_id    TEXT PRIMARY KEY,
    stream_id   TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     TEXT NOT NULL,     -- JSON
    metadata    TEXT NOT NULL,     -- JSON
    schema_v    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f','now'))
);
CREATE INDEX idx_events_stream ON events(stream_id, created_at);
CREATE INDEX idx_events_type ON events(event_type, created_at);
CREATE INDEX idx_events_created ON events(created_at);

CREATE TABLE stream_snapshots (
    stream_id       TEXT PRIMARY KEY,
    snapshot_state  TEXT NOT NULL,     -- JSON projected state
    last_event_id   TEXT NOT NULL,     -- snapshot covers events up to this ID
    created_at      TEXT NOT NULL
);
```

#### Write Path

```
Agent state change
  → EventEmitter.emit(type, stream_id, payload)
  → In-memory bounded queue (max 1000, blocks at capacity = backpressure)
  → Dedicated writer thread: SQLite INSERT (durable, sync)
  → If Redis available: PUBLISH channel:{stream_id} (async, best-effort)
  → If Redis available: HSET projection:{stream_id} (async, cached state)
```

The dedicated writer thread serializes all SQLite writes — no contention between agents. The bounded queue provides backpressure: if the writer falls behind (slow disk), agents slow down rather than accumulating unbounded memory. Warning logged at 80% capacity.

#### Read Paths

| Query | Path | Latency |
|-------|------|---------|
| Current state of a stream | Redis HGET (or SQLite replay if Redis down) | 0.1ms (Redis) / 5-50ms (SQLite) |
| Events in a stream | SQLite range query on stream_id + created_at | 10-100ms |
| Real-time event subscription | Redis SUBSCRIBE channel:{stream_id} | Push, real-time |
| State at time T | SQLite: find snapshot before T, replay events after | 5-50ms |

#### Redis Degradation

Redis is optional. When unavailable:
- Writes still go to SQLite (durable) — no data loss
- State projections rebuilt from SQLite on read (~50ms vs 0.1ms)
- No real-time push — polling fallback (1s interval)
- No session caching — re-auth on browser restart

The system MUST work without Redis. Redis makes it faster, not correct.

#### Stream Types

| Stream | stream_id Format | Key Events |
|--------|-----------------|------------|
| Scan pipeline | `scan:{iso_timestamp}` | window_started, platform_started, jobs_found, job_screened, materials_generated, platform_done, window_done |
| Form filling | `form:{platform}:{domain}:{job_id}` | started, auth_complete, page_detected, fields_filled, page_verified, page_advanced, approval_requested, submitted, post_apply_done |
| Pattern run | `pattern:{pattern_name}:{run_id}` | iteration_started, research_done, draft_written, review_scored, converged, finished |
| A2A task | `task:{task_id}` | created, accepted, progress, delegated, escalated, completed, failed |

#### Projectors

Each stream type has a `Projector` that folds events into current state. Projectors are pure functions — deterministic and idempotent (replaying an event twice produces the same state).

```python
class Projector(Protocol):
    def initial_state(self) -> dict: ...
    def apply(self, state: dict, event: Event) -> dict: ...

class ScanProjector(Projector):
    def initial_state(self):
        return {"platforms_done": [], "platforms_in_progress": None,
                "jobs_found": 0, "jobs_screened": 0, "job_cursor": 0}

    def apply(self, state, event):
        match event["event_type"]:
            case "scan.platform_started":
                state["platforms_in_progress"] = event["payload"]["platform"]
            case "scan.platform_done":
                state["platforms_done"].append(event["payload"]["platform"])
                state["platforms_in_progress"] = None
                state["jobs_found"] += event["payload"]["count"]
            case "scan.job_screened":
                state["jobs_screened"] += 1
                state["job_cursor"] = event["payload"]["job_index"]
        return state
```

Schema evolution: projectors handle multiple `schema_v` values. Rule: never remove fields from event payloads — only add. Old events with missing fields get defaults in the projector.

#### Retention & Compaction

| Age | Storage | Detail Level |
|-----|---------|-------------|
| < 7 days (hot) | SQLite + Redis projections | Full events |
| 7-30 days (warm) | SQLite only, Redis evicted | Full events |
| > 30 days (cold) | Compacted in SQLite | Snapshot + summary |

Compaction (nightly cron): for streams older than 30 days, write a `stream_snapshots` row containing the full projected state at compaction time + the last event ID covered. Original events archived to `data/events_archive/YYYY-MM.sqlite`. Queries spanning the boundary: load snapshot + replay events after `last_event_id`.

#### Storage Budget

| Component | Estimated Size | Retention |
|-----------|---------------|-----------|
| SQLite events (hot, 7d) | ~50 MB | Full detail |
| SQLite events (warm, 30d) | ~150 MB | Full detail |
| SQLite snapshots (cold) | ~20 MB | Compacted |
| Redis projections + sessions | ~5 MB | TTL 24h |
| Archive per month | ~30 MB | Indefinite |

---

### 1a. Scan Pipeline Checkpointing

Currently `run_scan_window()` in `jobpulse/job_autopilot.py` iterates platforms sequentially with no progress tracking. Platform results accumulate in a local list, returned as a batch.

**Instrumented flow:**

```
run_scan_window() starts
  → emit scan.window_started {platforms: [...], daily_caps: {...}}

  For each platform:
    → emit scan.platform_started {platform: "linkedin"}
    → emit scan.jobs_found {platform: "linkedin", count: 12, job_ids: [...]}

    For each job passing gates:
      → emit scan.job_screened {job_id: "x", job_index: 3, gates: [0,1,2,3], tier: "A"}
      → emit scan.materials_generated {job_id: "x", cv_path: "...", cl_path: "..."}

    → emit scan.platform_done {platform: "linkedin", applied: 3, skipped: 9}

  → emit scan.window_done {total_applied: 8, total_skipped: 22, cost: 0.14}
```

**Recovery:** On restart, check for incomplete `scan:*` streams (has `window_started` but no `window_done`). Replay via `ScanProjector` → know which platforms are done, which job index to resume from. Skip completed platforms entirely. Resume in-progress platform from `job_cursor`.

**Integration point:** `run_scan_window()` gets an optional `stream_id` parameter. If provided, checks for existing stream and resumes. If not, starts fresh.

### 1b. Form Filling Checkpointing

**Honest constraint:** Playwright browser state does not survive process death. "Checkpointing" for forms means:
- Know which pages were already filled (skip on retry)
- Know which fields had what values (avoid re-computation)
- Preserve auth cookies in Redis (avoid full re-login)
- Prevent duplicate submissions (dedup on stream_id)

It does NOT mean seamless browser session resume.

**Instrumented flow:**

```
apply_job() starts
  → emit form.started {url, domain, platform, job_id}
  → emit form.auth_complete {method: "sso_google", cookies_cached: true}

  Per page:
    → emit form.page_detected {page: 1, total_est: 3, field_labels: [...]}
    → emit form.fields_filled {page: 1, results: [{label, value, ok}, ...]}
    → emit form.page_verified {page: 1, confidence: 0.9, screenshot_ref: "..."}
    → emit form.page_advanced {from: 1, to: 2}

  → emit form.approval_requested {screenshot_ref: "...", summary: {...}}
  → emit form.submitted {dry_run: false}
  → emit form.post_apply_done {notion: true, drive: true}
```

**Recovery flow:**
1. Find incomplete `form:{platform}:{domain}:{job_id}` stream
2. Replay via `FormProjector` → pages 1-2 filled, page 3 not started
3. Launch new browser, navigate to URL
4. Restore cookies from Redis (if available) → skip re-auth
5. Navigate through pages 1-2 (Next/Continue buttons) without re-filling — verify each page matches expected state via quick DOM check
6. Fill page 3 from scratch using values from event history where applicable
7. If cookies expired or pages don't match → restart from auth, but still skip already-screened-and-generated materials

**Redis session cache:**
```
SET form:{platform}:{domain}:{job_id}:cookies '<encrypted blob>' EX 3600
```

### 1c. LangGraph Pattern Checkpointing

LangGraph has a `BaseCheckpointSaver` protocol. We implement `EventStoreCheckpointer` that bridges to the event store:

```python
class EventStoreCheckpointer(BaseCheckpointSaver):
    def __init__(self, event_store: EventStore):
        self._store = event_store

    def put(self, config, checkpoint, metadata):
        stream_id = f"pattern:{config['configurable']['thread_id']}"
        self._store.emit(
            stream_id=stream_id,
            event_type="pattern.checkpoint",
            payload={"checkpoint": checkpoint, "metadata": metadata},
        )

    def get_tuple(self, config):
        stream_id = f"pattern:{config['configurable']['thread_id']}"
        events = self._store.get_stream(stream_id, event_type="pattern.checkpoint")
        if not events:
            return None
        latest = events[-1]
        return CheckpointTuple(
            config=config,
            checkpoint=latest["payload"]["checkpoint"],
            metadata=latest["payload"]["metadata"],
        )
```

**Integration:** Each `build_*_graph()` function accepts an optional `checkpointer` parameter:
```python
graph = builder.compile(checkpointer=event_store_checkpointer)
```

**Additional observability events** emitted by node wrappers (not the checkpointer):
```
pattern.iteration_started  {iteration: 2, agent: "researcher"}
pattern.research_done      {iteration: 2, notes_count: 3, tokens: 1240}
pattern.draft_written      {iteration: 2, word_count: 850}
pattern.review_scored      {iteration: 2, quality: 7.2, accuracy: 9.1}
pattern.converged          {iteration: 3, final_score: 8.4, reason: "dual_gate_passed"}
```

**Recovery:** `graph.invoke(initial_state, config={"configurable": {"thread_id": run_id}})` — LangGraph natively checks the checkpointer first, resumes from latest checkpoint if one exists.

### 1d. Event Emitter API

```python
# Module-level access
from shared.execution import get_event_store, emit, subscribe

# Decorator for workflow functions
@event_sourced(stream_prefix="scan")
def run_scan_window(...):
    emit("scan.platform_started", platform="linkedin")
    ...

# Context manager for scoped operations
with event_scope("form", stream_id=f"form:{domain}:{job_id}"):
    emit("form.page_detected", page_num=1, fields=[...])

# Subscription (async generator)
async for event in subscribe("scan:2026-04-21T09:00"):
    handle(event)
```

`emit()` enqueues to the bounded in-memory queue (non-blocking unless full). The dedicated writer thread handles the dual-write: SQLite INSERT (sync, durable) + Redis PUBLISH (async, best-effort). If Redis is down, SQLite still captures the event. Subscribers catch up from SQLite on reconnect.

---

## Phase 2: MCP Production Server

### Architecture

```
┌──────────────────────────────────────┐
│          MCP Gateway                 │
│    (streamable HTTP, port 8090)      │
│    Auth + rate limit + audit         │
├──────────────────────────────────────┤
│                                      │
│  ┌────────────┐    ┌─────────────┐   │
│  │  JobPulse  │    │  Code Intel │   │
│  │ Capability │    │  Capability │   │
│  │  Server    │    │   Server    │   │
│  └─────┬──────┘    └──────┬──────┘   │
│        │                  │          │
└────────┼──────────────────┼──────────┘
         │                  │
    Event Store +      SQLite CodeGraph
    Redis Pub/Sub      (existing)
```

### Why a Gateway

Instead of N MCP servers on N ports, one gateway multiplexes capability servers:
- One port for firewall/LAN exposure
- Shared auth middleware
- Shared audit logging (all calls → event store)
- Tool namespacing: `jobpulse.scan_jobs`, `codeintel.find_symbol`

The gateway is a thin router — zero business logic.

### JobPulse Capability Server — Tools

| Tool | Wraps | Streaming | Cost |
|------|-------|-----------|------|
| `jobpulse.scan_jobs` | `run_scan_window()` | Yes — event stream per platform | $0.05-0.15 |
| `jobpulse.apply_job` | `apply_job()` | Yes — page-by-page progress | $0.01-0.05 |
| `jobpulse.confirm_application` | `confirm_application()` | No | Free |
| `jobpulse.morning_briefing` | `morning_briefing()` | Yes — section by section | $0.01 |
| `jobpulse.job_stats` | `job_analytics` | No | Free |
| `jobpulse.pre_screen` | `run_pre_screen()` | No | $0.001-0.01 |
| `jobpulse.budget` | `budget_agent` | No | Free |
| `jobpulse.run_pattern` | `run_enhanced_swarm()` etc. | Yes — iteration events | $0.10-0.50 |

### Streaming via Event Store

Streaming tools subscribe to event streams — no custom WebSocket logic:

```python
@mcp_tool("jobpulse.scan_jobs")
async def scan_jobs(params, context):
    stream_id = f"scan:{now_iso()}"

    # Start scan in background
    task = asyncio.create_task(run_scan_window_evented(stream_id, params))

    # Stream events to MCP client as they arrive
    async for event in subscribe(stream_id):
        yield mcp_progress(event)
        if event["event_type"] == "scan.window_done":
            break

    return await task
```

### MCP Resources (Read-Only)

| Resource | URI | Source |
|----------|-----|--------|
| Job queue | `jobpulse://jobs/queue` | Scan results awaiting review |
| Application history | `jobpulse://jobs/history?days=7` | Recent applications + status |
| Gate stats | `jobpulse://gates/stats` | Pre-screen pass/fail rates |
| Event stream | `jobpulse://events/{stream_id}` | Raw event history |
| Agent health | `jobpulse://health` | Bot status, DB sizes, limits |

Resources are read-only projections from event store + existing SQLite DBs.

### Code Intelligence Capability Server

Existing `code_intel_mcp.py` tools move behind the gateway (namespace: `codeintel.*`). Adds streamable HTTP transport alongside existing stdio. No functional changes.

### Auth & Rate Limits

| Deployment | Auth | Rate Limit |
|-----------|------|-----------|
| Local (Mac) | None (localhost) | Unlimited |
| LAN (Gigabyte) | Optional API key (audit trail) | Unlimited |
| Cloud (future) | mTLS + JWT | Per-client configurable |

Auth is gateway middleware — capability servers never see auth concerns.

### Health Check

`GET /health` returns gateway + all capability server status. Heartbeat events emitted to event store every 60s. If a capability server misses 3 heartbeats → marked degraded, requests return 503.

---

## Phase 3: A2A Protocol

### What A2A Adds Over MCP

MCP = tool calling (invoke and get result). A2A adds:
- **Discovery** — agents advertise capabilities via Agent Cards
- **Task lifecycle** — long-running work with status tracking
- **Delegation** — one agent spawns work on another, gets notified on completion
- **Escalation** — failed tasks route to rescue agents or humans

### Lightweight A2A-Compatible Protocol

Full Google A2A is designed for cross-organization agent communication. We implement a lightweight subset that:
- Uses the same Agent Card JSON format (compatible)
- Uses the same task lifecycle states (compatible)
- Uses direct function calls locally (fast, zero overhead)
- Uses HTTP + Redis pub/sub on LAN (the MCP gateway)
- Can upgrade to full A2A (webhooks, DNS discovery) on cloud without rewrite

### Agent Cards

Every agent publishes a card at `/.well-known/agent.json`:

```json
{
  "name": "scan-agent",
  "description": "Scans job platforms, screens listings, generates materials",
  "url": "http://localhost:8090/a2a/scan-agent",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "stateTransitionHistory": true
  },
  "skills": [
    {
      "id": "scan-platforms",
      "name": "Scan Job Platforms",
      "description": "Scan LinkedIn, Indeed, Reed for matching jobs",
      "inputModes": ["application/json"],
      "outputModes": ["application/json", "text/event-stream"]
    }
  ]
}
```

### Agent Registry

| Deployment | Discovery Mechanism |
|-----------|-------------------|
| Local (Mac) | Static `data/agent_registry.json` |
| LAN (Gigabyte) | Redis HSET `agents:{name}` with TTL heartbeat (60s) |
| Cloud (future) | DNS SRV / service mesh |

`AgentRegistry` protocol abstracts the backend. Swap without changing agent code.

### Agents

| Agent | Skills | Delegates To |
|-------|--------|-------------|
| **scan-agent** | scan-platforms, pre-screen-job | apply-agent, materials-agent |
| **apply-agent** | apply-job, confirm-application | materials-agent (lazy CL) |
| **materials-agent** | generate-cv, generate-cover-letter | — (leaf) |
| **briefing-agent** | morning-briefing, weekly-report | scan-agent (fresh stats) |
| **budget-agent** | budget-query, budget-add | — (leaf) |
| **research-agent** | run-pattern (6 LangGraph patterns) | — (uses internal graph) |
| **rescue-agent** | rescue-form, rescue-navigation | — (LLM-powered fallback) |
| **codeintel-agent** | find-symbol, impact-analysis, etc. | — (existing MCP) |

### Task Lifecycle

```
             ┌──────────┐
   create    │ pending  │
  ────────►  └────┬─────┘
                  │ agent accepts
             ┌────▼─────┐
             │ running  │◄─── progress events (streamed)
             └────┬─────┘
                  │
       ┌──────┬──┴──────────┐
  ┌────▼───┐  │       ┌─────▼──────┐
  │verifying│  │       │  failed    │
  └────┬───┘  │       └─────┬──────┘
       │      │             │ retryable?
  pass │ fail │       ┌─────▼──────┐
       │      │       │ escalated  │──► higher tier agent
       ▼      │       └─────┬──────┘
  completed   │             │
              │        completed (with rescue metadata)
              │
         timed_out ──► escalated
```

New vs prior design: `verifying` state runs FormVerifier checks before marking complete. `timed_out` state catches hung agents.

```python
class A2ATask(TypedDict):
    task_id: str                # ULID
    parent_task_id: str | None  # delegation chain
    source_agent: str
    target_agent: str
    skill_id: str
    input: dict
    status: str                 # pending | running | verifying | completed | failed | escalated | timed_out
    output: dict | None
    artifacts: list[dict]       # files, screenshots, PDFs
    history: list[dict]         # status transitions with timestamps
    timeout_s: int              # per-type: form=600, scan=900, pattern=420
    created_at: str
    updated_at: str
```

All task mutations emit events to the `task:{task_id}` stream. Full audit trail.

### Task Timeouts

| Task Type | Timeout | On Timeout |
|-----------|---------|-----------|
| Form fill | 600s (10 min) | Escalate to rescue-agent |
| Scan window | 900s (15 min) | Mark platform as failed, continue next |
| Pattern run | 420s (7 min) | Return best result so far |
| Materials generation | 120s (2 min) | Return error, caller retries |
| Budget query | 30s | Return error |

`TaskRunner` enforces timeouts via `asyncio.wait_for()`. Emits `task.timed_out` event.

### Delegation Example

```
1. User triggers scan via Telegram

2. scan-agent: task created → status: running
   → emits scan events (platform_started, jobs_found...)

3. Job passes all gates → scan-agent delegates to materials-agent:
   → child task: generate-cv {company: "OakNorth"}
   → emits task.delegated {parent: scan_task, child: cv_task}
   → materials-agent completes → emits task.completed {artifacts: [cv.pdf]}

4. CV ready → scan-agent delegates to apply-agent:
   → child task: apply-job {job_id, cv_path, dry_run: true}
   → apply-agent fills form, emits page events
   → emits task.completed {status: "awaiting_approval"}

5. User approves → confirm-application
   → emits task.completed with Notion/Drive artifacts
```

Full chain traceable via `parent_task_id`.

### Push Notifications

Agents subscribe to task updates via Redis pub/sub:

```python
async for event in subscribe("task:*", filter={"event_type": "task.completed"}):
    if event["payload"]["parent_task_id"] == my_task:
        # Delegated work complete, proceed
```

On LAN: Redis pub/sub. On cloud: webhook callbacks per A2A spec.

---

## Error Recovery & LLM Escalation

### Escalation Chain

Every task follows a 4-tier escalation:

```
Tier 1: Specialized Agent
         Adapter-specific, deterministic, zero LLM cost
         │ failed or confidence < 0.6
         ▼
Tier 2: LLM Rescue Agent
         Vision + DOM analysis, ~$0.01/call
         │ failed after 2 attempts
         ▼
Tier 3: CognitiveEngine L3 (Tree of Thought)
         Explores multiple strategies, ~$0.05
         │ best branch scored < 5.0
         ▼
Tier 4: Human Escalation
         Telegram notification with full context
```

### Rescue Agent

A dedicated A2A agent (`rescue-agent`) with two capabilities:

**1. Vision analysis** — receives screenshot + DOM snapshot + event history:
```
Page screenshot: [image]
DOM structure: [simplified tree]
Event history: [what was tried, what failed]
Task: identify form fields, types, correct fill order.
```

**2. Cross-domain transfer** — queries event store for structurally similar forms:
```python
similar = event_store.query(
    event_type="form.page_filled",
    payload_similarity=current_dom_signature,
    limit=5,
)
# "Never seen this ATS, but 47 Greenhouse fills have similar field patterns"
```

**Budget cap:** Max 3 rescue attempts per domain per day. After that → Tier 4 (human queue).

### Mistake Detection (Pre-Submit)

After every `form.fields_filled` event, `FormVerifier` runs checks:

| Check | Method | Cost | When |
|-------|--------|------|------|
| Field/value type mismatch | Heuristic (name in phone field) | Free | Every page |
| Duplicate uploads | Event history scan | Free | Every page |
| Empty required fields | DOM required attr check | Free | Every page |
| Cross-page consistency | Event history comparison | Free | Pages 2+ |
| Screenshot consistency | LLM vision — does screen match fill data? | ~$0.005 | Pages where confidence < 0.7 |

LLM vision runs on ANY page where confidence drops below 0.7, not just the final page. This catches mistakes early before they're committed via Next/Continue.

### Unknown ATS Handling

When `detect_ats_platform()` returns `"generic"`:

1. Emit `form.platform_unknown` event
2. Activate rescue-agent with: URL + screenshot + DOM + all known ATS signatures + FormExperienceDB history
3. Rescue-agent produces: field map with per-field confidence + navigation plan + risk assessment
4. If risk "high" (confidence < 0.5 on > 30% of fields) → Tier 4 (Telegram: "New ATS, need help")
5. If risk "medium"/"low" → proceed with LLM field map, extra verification every page
6. On success → emit `form.new_platform_learned`, record in FormExperienceDB
7. Next encounter with same domain → Tier 1 (cached experience, no LLM)

### Learning From Mistakes

Every mistake event feeds back into the system:

```
form.mistake_detected {
    field_label: "Phone",
    filled_value: "John Smith",
    correct_value: "+447123456789",
    cause: "field_mismatch",
    platform: "greenhouse"
}
```

This event triggers:
1. **Pillar 3** (OptimizationEngine): `correction` signal → tracks trend
2. **CorrectionCapture**: 3+ similar → auto-generates override rule
3. **Pillar 1** (Memory): stored as episodic anti-pattern for Reflexion
4. **FormExperienceDB**: field-level correction for same domain/platform

---

## Agent Awareness Loop — Cross-Pillar Wiring

### The Problem

The escalation chain and event store exist, but nothing tells agents to consult them. Without explicit wiring, agents are amnesiac.

### Pre-Flight → Execute → Post-Flight

Every A2A task goes through a 3-phase awareness cycle, implemented as `TaskRunner` middleware that wraps any agent function:

```
PRE-FLIGHT (before first action)
  ├─► Pillar 1 (Memory): recall(domain, platform)
  │   → past strategies, anti-patterns
  ├─► Pillar 4 (Events): recent failures on this stream
  │   → what failed, how many times
  ├─► Pillar 2 (Cognitive): assess(task, memories, failures)
  │   → complexity, recommended cognitive level
  └─► Pillar 3 (Optimization): domain_stats(domain)
      → success rate, common failure modes
  
  Output: TaskPlan {strategy, anti_patterns, start_tier, confidence, escalation_hints}

EXECUTE (per action, real-time)
  ├─► Act → emit event (Pillar 4)
  ├─► Verify → FormVerifier checks (Pillar 4)
  ├─► Compare → expected vs actual (from pre-flight memory)
  └─► Decide → continue | retry | escalate (confidence tracker)

POST-FLIGHT (after task completes or fails)
  ├─► Pillar 1: learn_procedure() or store_episodic(failure)
  ├─► Pillar 3: emit_signal(success/failure)
  └─► Pillar 4: experience_recorded event
```

### Pre-Flight Detail

```python
class TaskPreFlight:
    def prepare(self, task: A2ATask) -> TaskPlan:
        domain = task["input"].get("domain", "")
        platform = task["input"].get("platform", "")

        # 1. Memory (Pillar 1) — what do I know?
        memories = self.memory.recall(
            query=f"{platform} {domain} {task['skill_id']}",
            tiers=["procedural", "episodic"],
            limit=5,
        )

        # 2. Event history (Pillar 4) — what happened recently?
        recent_failures = self.event_store.query(
            stream_prefix=f"form:{platform}:{domain}",
            event_types=["form.mistake_detected", "form.rescue_used"],
            since=days_ago(30),
        )

        # 3. Complexity (Pillar 2) — how hard is this?
        assessment = self.cognitive.assess(
            task=f"{task['skill_id']} on {platform}:{domain}",
            domain=task["skill_id"],
            memories=memories,
            recent_failure_count=len(recent_failures),
        )

        # 4. Domain stats (Pillar 3) — what's the trend?
        stats = self.optimization.get_domain_stats(task["skill_id"], platform)

        # Determine starting tier
        if assessment.confidence > 0.7 and len(memories) > 0:
            start_tier = 1  # known territory
        elif assessment.confidence > 0.4:
            start_tier = 2  # rescue on standby
        else:
            start_tier = 2  # start with rescue

        return TaskPlan(
            strategy=memories,
            anti_patterns=[e for e in recent_failures if "mistake" in e["event_type"]],
            cognitive_level=assessment.recommended_level,
            confidence=assessment.confidence,
            start_tier=start_tier,
            escalation_hints=self._build_hints(recent_failures, stats),
        )
```

### Cold-Start Fast Path

For the first few weeks, all stores are empty. Pre-flight must be zero-cost in this case:

```python
def prepare(self, task):
    memories = self.memory.recall(...)
    if not memories:
        recent_failures = self.event_store.query(...)
        if not recent_failures:
            # Fast path: no data anywhere, skip cognitive + optimization
            return TaskPlan(
                strategy=[], anti_patterns=[],
                cognitive_level="L1", confidence=0.5,
                start_tier=1, escalation_hints=[],
            )
    # Full path with all 4 pillar queries
    ...
```

No wasted latency querying 4 systems to get empty results.

### Confidence Tracker (Execute Phase)

```python
class ConfidenceTracker:
    def __init__(self, plan: TaskPlan):
        self.confidence = plan.confidence
        self.hints = plan.escalation_hints
        self.events: list[Event] = []

    def after_action(self, event: Event, verify: VerifyResult) -> Decision:
        self.events.append(event)

        if verify.field_mismatch:
            self.confidence -= 0.2
        if verify.unexpected_element:
            self.confidence -= 0.3
        if verify.all_ok:
            self.confidence += 0.05

        for hint in self.hints:
            if hint.matches(event, self.confidence):
                return Decision(ESCALATE, hint.action, hint.reason)

        if self.confidence < 0.4:
            return Decision(ESCALATE, "rescue", f"confidence {self.confidence:.2f}")

        return Decision(CONTINUE)
```

### TaskRunner Middleware

```python
class TaskRunner:
    """Wraps any agent with the awareness loop. Agents get memory,
    confidence tracking, and learning for free."""

    def __init__(self, agent_fn, memory, cognitive, optimization, event_store):
        self.agent_fn = agent_fn
        self.preflight = TaskPreFlight(memory, cognitive, optimization, event_store)
        self.postflight = TaskPostFlight(memory, optimization, event_store)
        self.event_store = event_store

    async def run(self, task: A2ATask) -> TaskResult:
        plan = self.preflight.prepare(task)
        tracker = ConfidenceTracker(plan)

        try:
            result = await asyncio.wait_for(
                self.agent_fn(task, plan, tracker),
                timeout=task["timeout_s"],
            )
        except asyncio.TimeoutError:
            self.event_store.emit("task.timed_out", task_id=task["task_id"])
            result = TaskResult(success=False, failure_reason="timeout")
        except Exception as e:
            result = TaskResult(success=False, failure_reason=str(e))

        self.postflight.complete(task, result, tracker.events)
        return result
```

Every agent — scan, apply, materials, briefing — is wrapped in `TaskRunner`. Pre-flight awareness, real-time confidence tracking, post-flight learning are automatic.

---

## File Structure

```
shared/execution/
    __init__.py               # Public API: get_event_store, emit, subscribe, TaskRunner
    _event_store.py           # Event, EventStore, emit(), bounded queue, writer thread
    _projectors.py            # ScanProjector, FormProjector, PatternProjector
    _checkpointer.py          # EventStoreCheckpointer (LangGraph BaseCheckpointSaver bridge)
    _redis.py                 # Redis pool, pub/sub, graceful degradation
    _mcp_gateway.py           # Gateway router, auth middleware, health check
    _mcp_jobpulse.py          # JobPulse capability server (tools + streaming)
    _mcp_resources.py         # MCP resource handlers
    _a2a_card.py              # AgentCard, AgentRegistry protocol + file/redis backends
    _a2a_task.py              # A2ATask, TaskManager, task lifecycle state machine
    _a2a_protocol.py          # A2A HTTP endpoints, SSE streaming
    _awareness.py             # TaskPreFlight, TaskPostFlight, ConfidenceTracker, TaskRunner
    _verifier.py              # FormVerifier: heuristic + vision checks
    _rescue.py                # Rescue agent: vision analysis + cross-domain transfer
    CLAUDE.md                 # Module documentation
```

DB files:
- `data/events.db` — event store (hot + warm events, snapshots)
- `data/events_archive/` — monthly cold archives
- Redis: ephemeral projections, session cookies, pub/sub channels

## Dependencies

### New
- `redis` (Python client) — optional, graceful degradation
- `ulid-py` — ULID generation for event IDs

### Existing (already in use)
- `langgraph` — BaseCheckpointSaver protocol
- `aiosqlite` or standard `sqlite3` — event store
- `fastapi` / `uvicorn` — MCP gateway HTTP server (already used by health_api.py)

No heavy new infrastructure. Redis is the only new external service.

## Estimated Scope

| Phase | Files | LOC (est.) | Tests (est.) |
|-------|-------|-----------|-------------|
| Phase 1: Event Store + Checkpointing | 5 modules | ~1200 | ~30 |
| Phase 2: MCP Production | 3 modules | ~800 | ~20 |
| Phase 3: A2A + Awareness Loop | 6 modules | ~1500 | ~35 |
| **Total** | **14 modules** | **~3500** | **~85** |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| SQLite write contention at scale | Low (current: 50 apps/day) | Medium | WAL mode + writer thread. Postgres swap path designed in. |
| Redis downtime | Medium | Low | Graceful degradation — system works without Redis. |
| Event store grows unbounded | Low | Medium | Compaction cron + monthly archival. Budget: ~200MB/month. |
| Rescue agent cost spike | Low | Medium | Max 3 rescues/domain/day cap. $0.60 worst case. |
| Cold-start latency from awareness loop | High (first weeks) | Low | Fast-path bypass when all stores return empty. |
| MCP gateway SPOF | Medium (LAN) | High (no agent comms) | Heartbeat monitoring. Cloud: load balancer + replicas. |
| Event schema drift over months | High | Medium | schema_v field + multi-version projectors. Never delete fields. |
| Form recovery re-auth failure | Medium | Medium | Honest design: skip + dedup, not seamless resume. |

## Non-Goals (Explicit Exclusions)

- **Weight-space RL** — this is infrastructure, not training. Pillar 3 handles learning.
- **Multi-tenant** — single user system. No tenant isolation, no per-user quotas.
- **Container orchestration** — no Docker/K8s until cloud migration.
- **Full Google A2A compliance** — compatible subset, not certified implementation.
- **Browser session serialization** — Playwright sessions can't be persisted. Recovery is skip + dedup.
