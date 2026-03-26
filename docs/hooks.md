# Hooks

Process trails, memory injection, tool integration, audit logging, budget tracking pipeline.

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

## 7. Unified Logging Framework

**File:** `shared/logging_config.py`

Structured logging used across all modules:

```python
from shared.logging_config import get_logger
logger = get_logger(__name__)
```

- Per-module loggers with consistent format
- Log files written to `logs/` directory
- All agents, dispatchers, and API handlers use this framework

## 8. API Rate Limit Monitoring

**File:** `shared/rate_monitor.py`

Tracks rate limit headers from external API responses:

- Records `X-RateLimit-Remaining` / `X-RateLimit-Limit` per API
- Stored in SQLite for historical tracking
- Exposed via `GET /api/health/rate-limits` and health dashboard gauges
- `get_current_limits()` returns latest snapshot per API

## 9. Export/Backup System

**File:** `jobpulse/export.py`

One-click export of all system data:

- Copies all SQLite databases (mindgraph, jobpulse, budget, swarm_experience)
- Exports persona prompts and experiences as JSON
- Exports A/B test results and rate limit history
- Generates manifest with checksums
- Creates timestamped `.tar.gz` archive in `exports/` directory

Triggers: Telegram ("export"), CLI (`python -m jobpulse.runner export`), API (`POST /api/health/export`), health dashboard button.

## 10. Claude Code Telegram Approval Hook

**File:** `scripts/telegram_approve.py`

Claude Code PreToolUse hook that forwards bash command approvals to Telegram:

```
Claude Code runs `npm install express`
  → Hook intercepts via CLAUDE_TOOL_INPUT env var
  → Checks auto-approve list (ls, cat, git status, grep, echo, python -c, pytest)
  → Checks always-block list (rm -rf, sudo, shutdown, reboot)
  → Everything else → sends to Telegram, polls for yes/no reply
  → 1 hour timeout, blocks by default on expiry
```

### Configuration

In `.claude/settings.json`:
```json
{
  "PreToolUse": [{
    "matcher": "Bash",
    "hooks": [{
      "type": "command",
      "command": "python scripts/telegram_approve.py",
      "timeout": 120
    }]
  }]
}
```

### Auto-approve (safe)
`ls`, `cat`, `head`, `tail`, `wc`, `pwd`, `date`, `which`, `echo`, `python -c`, `python -m pytest`, `grep`, `git status`, `git log`, `git diff`, `git branch`

### Auto-block (dangerous)
`rm -rf`, `sudo`, `shutdown`, `reboot`, `> /dev`

## 11. NLP 3-Tier Intent Classification Pipeline

**Files:** `jobpulse/nlp_classifier.py`, `data/intent_examples.json`

Sits at the front of the dispatch pipeline — every incoming message passes through before reaching any agent:

```
Message In
  → Tier 1: Regex match (exact commands)              → instant, free
  → Tier 2: Semantic embedding similarity (MiniLM)    → ~5ms, free
  → Tier 3: LLM classification (gpt-4o-mini)          → ~500ms, $0.001
  → Intent + confidence → dispatcher
```

- 250+ examples, 31 intents
- Continuous learning hook: Tier 3 results automatically written back as Tier 2 training data
- Embedding model loaded once at startup, reused across all requests

## 12. Budget Tracker Pipeline

**File:** `jobpulse/budget_tracker.py`

Hooks into the budget agent to manage category sub-pages, weekly archival, and comparison:

```
Transaction logged (budget_agent)
  → Create/find category sub-page in Notion
  → Append transaction row (Amount, Date, Items, Store, Running Total)
  → Update parent row Notes with link to sub-page
  → If salary → link to timesheet page
```

### Weekly Archival (Sunday 7am cron via `install_cron.py`)

```
archive-week
  → Snapshot current weekly budget sheet
  → Create new week's sheet
  → Carry over all planned amounts
  → Reset actuals to zero
```

### Weekly Comparison

```
budget-compare / morning briefing
  → Load current week totals per category
  → Load previous week totals per category
  → Compute deltas + historical pace alerts
  → "Groceries £35 so far (was £20 by this day last week)"
```

### Dataset Export

```
budget-export
  → Query all transactions from SQLite
  → Generate CSV with 12 columns (date, amount, category, items, store, etc.)
  → Suitable for ML training / analysis
```

### Cron Setup

`install_cron.py` registers Sunday 7am cron: `python -m jobpulse.runner archive-week`

## 13. A/B Testing for Prompts

**File:** `jobpulse/ab_testing.py`

Runs controlled experiments on prompt variants:

- Define variant A and B prompts for any agent
- System alternates between variants, scores outputs
- After N trials, declares a winner based on average score
- Results stored in SQLite, exported with backup system
- Used by budget classification and briefing synthesis agents
