# Hooks

Process trails, memory injection, tool integration, audit logging.

## 1. Process Trail System (NEW)

**File:** `jobpulse/process_logger.py`

Every agent run captures a full step-by-step audit trail:

```
ProcessTrail("gmail_agent", "scheduled_check")
  → step("api_call", "Connect to Gmail API")        → 120ms ✅
  → step("api_call", "Fetch inbox")                  → 340ms ✅
  → step("api_call", "Read email #1")                → 89ms  ✅
  → step("llm_call", "Classify email #1")            → 450ms ✅ INTERVIEW
  → step("api_call", "Send Telegram alert")          → 180ms ✅
  → step("extraction", "Extract knowledge")          → 15ms  ✅
  → finalize("Processed 4 emails, 1 interview")
```

### Step Types

| Type | Icon | Color | Meaning |
|------|------|-------|---------|
| `api_call` | 🔌 | Blue | External API interaction |
| `llm_call` | 🤖 | Purple | LLM classification/generation |
| `decision` | 💡 | Orange | Branching decision with reasoning |
| `extraction` | 🧠 | Green | Knowledge graph entities created |
| `output` | 📤 | White | Final result |
| `error` | ❌ | Red | Something failed |

### Storage

Table: `agent_process_trails` in mindgraph.db
Auto-cleanup: trails > 30 days deleted on import.

### API Endpoints

- `GET /api/process/runs` — recent runs (filter by agent/date)
- `GET /api/process/trail/{run_id}` — full step-by-step
- `GET /api/process/agents` — stats per agent type

### Frontend

`/processes.html` — agent cards, expandable run timelines, color-coded steps.

## 2. Simulation Event Logger

**File:** `jobpulse/event_logger.py`

Captures WHAT happened (not HOW — that's process trails):

- `email_classified`, `calendar_event`, `github_activity`
- `budget_transaction`, `briefing_sent`, `knowledge_extracted`
- `agent_action`, `error`

Table: `simulation_events` in mindgraph.db. Auto-cleanup: 90 days.

## 3. Memory Injection Hook

**File:** `shared/memory_layer.py`

Five-tier memory: Working → Short-Term → Episodic → Semantic → Procedural.
`MemoryManager.get_context_for_agent()` pushes relevant context into prompts.

## 4. Experience Memory (Enhanced Swarm)

**File:** `jobpulse/swarm_dispatcher.py`

SQLite-backed experience storage (`swarm_experience.db`):
- `experiences` table: learned patterns per intent with scores
- `persona_prompts` table: evolved agent prompts with generation tracking

Injected into swarm dispatch before each agent run.

## 5. Tool Integration

**File:** `shared/tool_integration.py`

Pipeline: Permission → Risk → Approval → Rate Limit → Execute → Audit Log.

## 6. Auto-Extraction Hook

**File:** `jobpulse/auto_extract.py`

Wired into gmail_agent — after classifying recruiter emails, extracts:
- Company entities (from sender domain)
- Person entities (from sender name)
- Relations (APPLYING_TO, INTERVIEWING_AT)

Feeds into Knowledge MindGraph automatically.
