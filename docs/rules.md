# Rules

All constraints, operational rules, convergence logic, and pattern selection.

## Operational Rules

These override all defaults. Violating any is a mistake — log it to `.claude/mistakes.md`.

### 1. Memory Before Action

```
BEFORE any task:
  1. Search memory for matching patterns/solutions
  2. Score relevance (0.0 - 1.0)
  3. If score > 0.7 → reuse the pattern
  4. If score 0.4-0.7 → use as starting point
  5. If score < 0.4 → build from scratch
```

### 2. Orchestrator, Not Executor

Claude coordinates. Agents do the work. If you're writing/researching/reviewing directly, stop and dispatch.

### 3. Enhanced Swarm for Production

Task analyzer → dynamic routing → GRPO sampling → persona evolution → RLM synthesis.

```
Supervisor (1)
  ├── Researcher (1-2)
  ├── Writer (1)
  ├── Reviewer (1)
  ├── Fact Checker (0-1)
  ├── SEO Optimizer (0-1)
  ├── Code Expert (0-1)
  └── Audience Adapter (0-1)
```

Min 3, max 8 agents. Other patterns are for experimentation only.

### 4. Learn After Success

When `review_score >= 8.0`: extract pattern (agents, routing, strengths) → store in `patterns` namespace.

### 5. 3-Tier Routing

```
Tier 1: CACHED       → identical/similar task solved before
  ↓ (miss)
Tier 2: LIGHTWEIGHT  → check [AGENT_BOOSTER_AVAILABLE]
  ↓ (miss)
Tier 3: FULL AGENT   → complete LLM call
```

### 6. Commands Return Instantly

Commands create records only. Never wait. Supervisor monitors completion.

## Convergence Rules

| Pattern | Pass Condition | Max Iterations | Fallback |
|---------|---------------|----------------|----------|
| Hierarchical | `review_score >= 8.0 AND accuracy >= 9.5` | 3 | Accept best draft |
| Peer Debate | Score improvement < threshold AND accuracy >= 9.5 | patience counter | Accept current draft |
| Dynamic Swarm | Task queue empty AND accuracy >= 9.5 | 3 re-analysis rounds | Accept current state |
| Enhanced Swarm | Adaptive threshold AND accuracy >= 9.5 | Experience-aware | Rollback to best |

## Constraints

- Never instantiate `ChatOpenAI` directly — always use `get_llm()` from `shared/agents.py`
- Never return full `AgentState` from an agent — only the fields that changed
- Never mutate `topic` after initialization — it's the immutable input
- `shared/` modules must not import from `patterns/` — dependency flows one way
- Agents must be stateless functions — no instance variables, no side effects
- Review scores are floats 0-10. Passing threshold is 8.0. Max iterations is 3
- Output files go to `outputs/` as markdown

## Pattern Selection

### Use Hierarchical When
- Task workflow is known upfront
- You need a single point of control and auditability
- Speed matters more than output quality

### Use Peer Debate When
- Output quality is the top priority
- Task is subjective (writing, design, strategy)
- You can afford higher token costs and latency

### Use Dynamic Swarm When
- Task complexity is unknown upfront
- Requirements may emerge during execution

### Use Enhanced Swarm When
- Production workload with high quality requirements
- You want agents that improve over time

## Performance

| Metric | Hierarchical | Peer Debate | Dynamic Swarm | Enhanced Swarm |
|--------|-------------|-------------|---------------|----------------|
| LLM calls/run | 4-10 | 10-25 | 6-15 | 15-40 |
| Token usage | Low | High | Medium | Very High |
| Output quality | Good | Excellent | Good | Excellent |

## Intent Routing

All intents recognized by `command_router.py`:

| Intent | Example Triggers |
|--------|-----------------|
| `CREATE_TASKS` | Multi-line list, `add task`, `!! urgent`, `! high` |
| `SHOW_TASKS` | "show tasks", "my todo", "what do I have today" |
| `COMPLETE_TASK` | "done: X", "mark X done", "complete: X" |
| `REMOVE_TASK` | "remove: X", "delete: X" |
| `WEEKLY_PLAN` | "plan", "weekly plan", "carry forward" |
| `CALENDAR` | "calendar", "what's today", "events" |
| `CREATE_EVENT` | "remind me at 3pm", "set event", "schedule" |
| `GMAIL` | "check emails", "inbox", "any recruiter emails" |
| `GITHUB` | "commits", "what did I push" |
| `TRENDING` | "trending", "hot repos" |
| `BRIEFING` | "briefing", "morning update" |
| `ARXIV` | "arxiv", "ai papers", "latest papers" |
| `LOG_SPEND` | "spent 15 on lunch", "$20 for groceries" |
| `LOG_INCOME` | "earned 500 freelance", "got paid 2000" |
| `LOG_SAVINGS` | "saved 100", "invest 50" |
| `SET_BUDGET` | "set budget groceries 50", "limit transport to 30" |
| `SHOW_BUDGET` | "budget", "weekly spending", "summary" |
| `UNDO_BUDGET` | "undo", "undo last transaction" |
| `RECURRING_BUDGET` | "recurring: 10 on spotify monthly", "show recurring" |
| `WEEKLY_REPORT` | "weekly report", "this week summary" |
| `EXPORT` | "export", "backup" |
| `CONVERSATION` | Any unmatched text → free-form LLM chat |
| `REMOTE_SHELL` | "run: ls", "$ git status" |
| `GIT_OPS` | "git status", "commit: msg", "push" |
| `FILE_OPS` | "show: file.py", "logs", "errors", "more" |
| `SYSTEM_STATUS` | "status", "daemon check" |
| `CLEAR_CHAT` | "clear chat", "new conversation" |
| `SCAN_JOBS` | "scan jobs", "find jobs", "run autopilot" |
| `SHOW_JOBS` | "jobs", "show jobs", "pending jobs" |
| `APPROVE_JOBS` | "apply 1,3,5", "apply all" |
| `REJECT_JOB` | "reject 2", "skip 3" |
| `JOB_DETAIL` | "job 3", "details 5" |
| `JOB_STATS` | "job stats", "application stats" |
| `SEARCH_CONFIG` | "search: add title X" |
| `PAUSE_JOBS` | "pause jobs", "stop autopilot" |
| `RESUME_JOBS` | "resume jobs", "start autopilot" |

## Job Autopilot Rules

### Rate Limits (March 2026 — research-backed)
- **Total daily cap**: 25 applications across all platforms
- **LinkedIn**: 15/day, session break of 30min every 5 apps (ML detection risk)
- **Indeed**: 8/day (aggressive IP banning, permanent account suspension)
- **Workday**: 5/day (behavioral analysis + 3rd-party bot detection)
- **All platforms**: 20-45s random delay between submissions, 10min break every 5 apps

### Anti-Detection
- All adapters use headed mode (not headless) with `--disable-blink-features=AutomationControlled`
- LinkedIn uses persistent browser profile with human-like typing (50-150ms/char)
- Thread mutex prevents concurrent `apply_job()` calls (TOCTOU race protection)
- Pipeline lock prevents concurrent `run_scan_window()` runs (cron vs Telegram)
- Application recorded BEFORE submission (prevents silent limit bypass on error)
- UTC timezone for daily cap tracking (prevents midnight drift)
- **Verification Wall Learning**: detect + record + correlate + adapt. 17 signals per session. Statistical engine (free) + LLM (every 5th block). 2hr→4hr→48hr cooldown. Human-like interaction on all Playwright scanners.

## Input Modes

| Mode | Source | Processing |
|------|--------|------------|
| Text | Telegram, Slack, Discord | Rule-based → LLM fallback classification |
| Voice | Telegram voice messages | Whisper transcription → text classification |
| Webhook | External HTTP POST | Payload extraction → dispatcher |

## Migration Path

```
Hierarchical → Dynamic Swarm → Enhanced Swarm
     │
     └──→ Peer Debate (if quality > speed)
```
